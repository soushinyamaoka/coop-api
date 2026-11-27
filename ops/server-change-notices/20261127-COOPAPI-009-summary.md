# Server Change Notice

record_type: server_change
template_type: full
policy_bundle_version: 2026-09-04.1
notice_id: 20261127-COOPAPI-009
app: coop-api
source_branch: main
source_commit: de6de35958697e0cd95e48307e44670ab04ba10b
production_baseline_commit: 5c38323efabe231b075da802bba6e3479ca43549
release_commits: afe40412db64f29e03ce121709f3dfe7428ebd17, 64c3147b1a38d42cc874567cab50b3e6e08cde6a, 55034dc7c9797a9eb47063a930a508eb38a33865, 47cd81096839fde0f7c2cb767f67fa028ad4ad61, de6de35958697e0cd95e48307e44670ab04ba10b
impact_level: L3
status: ready_for_review
created_by: Claude
production_change: required
vps_management_handoff: required
deployment_status: not_started

## 再提出（2026-11-27）: 差し戻しへの対応

初回（source `55034dc`）は差し戻された。`de6de35` で修正し、通知書を直した。

| 指摘 | 初回の問題 | 対応 |
|---|---|---|
| 全権トークンの欠落・同値を起動時に拒否 | `API_TOKEN` が無いと全APIが認証なしで動いた（従来からの動き）。`API_READ_TOKEN` が `API_TOKEN` と同じ値でも起動し、参照専用のつもりのトークンで全権の操作ができた | `check_token_config` を追加。`API_TOKEN` が空、または `API_READ_TOKEN` が `API_TOKEN` と同じ値なら**起動しない**（FastAPI の lifespan と `python coop_api_server.py` の両方。ログ `startup_configuration_invalid` に理由のコード `api_token_missing`・`read_token_same_as_api_token` だけ、値は出さない。`__main__` は待ち受け前に終了コード3）。「`API_TOKEN` が未設定なら認証を飛ばす」動きは削除し、空でも401にする（`de6de35`） |
| 影響度 | L2 としていた | **L3** に訂正（認証の方式の変更、`.env` の追加、起動条件の変更で、設定を誤ると COOP API が起動しない） |
| `.env` の保全と切り戻し手順 | 書いていなかった | 下の「production変更」と「Deploy・rollback」に追記 |

## 変更概要

coop-api に、**注文の参照だけに使える参照専用トークン** `API_READ_TOKEN` を追加し、トークンの設定が誤っているときは起動しないようにしたい。

- `GET /api/coop/ingredients`・`GET /api/coop/orders` は、全権トークン（`API_TOKEN`）か参照専用トークンで使える
- それ以外のAPIは、参照専用トークンでは **403**「参照専用のトークンでは使えない操作です」
- トークンの照合は `hmac.compare_digest`。`API_READ_TOKEN` が未設定なら参照専用トークンは使えない（空のトークンは一致しない）
- **起動時**: `API_TOKEN` が空、または `API_READ_TOKEN` が `API_TOKEN` と同じ値なら起動しない。認証を飛ばす動きは無くす

## 変更理由

meal-api 通知001のレビューで、第2段階（COOPの参照）の前提として「coop-api の参照専用トークン」が求められた。meal-api に全権の `API_TOKEN` を渡すと、COOPの注文を読むだけの連携が、取り込み・分類の修正・献立作成までできてしまう。meal-api には参照専用トークンだけを渡す（meal-api への設定は、meal-api の第2段階の通知003で行う）。あわせて、トークンの設定の誤りで認証なし・権限の取り違えのまま動かないようにする。

## server_impact判定

server_impact: approval_required
判定理由: 認証の方式（受け付けるトークンの種類）と起動条件を変え、`.env` の変数を追加するため。`.env` を誤ると coop-api が起動しない（アプリの COOP タブが使えない）。bind・port・公開面・データは不変。

## 現在と変更後

| 項目 | 現在（baseline `5c38323`、通知008反映後） | 変更後 |
|---|---|---|
| `GET /api/coop/ingredients`・`/api/coop/orders` | `API_TOKEN` | `API_TOKEN` または `API_READ_TOKEN` |
| ほかのAPI | `API_TOKEN` | `API_TOKEN`（`API_READ_TOKEN` は403） |
| 合わない・無いトークン | 401 | 401（同じ） |
| `API_TOKEN` が空 | 認証なしで全APIが動く | **起動しない**（ログ `startup_configuration_invalid reason=api_token_missing`） |
| `API_READ_TOKEN` が `API_TOKEN` と同じ | （項目なし） | **起動しない**（`reason=read_token_same_as_api_token`） |
| トークンの照合 | 文字列の `!=` | `hmac.compare_digest` |
| `.env` | `API_TOKEN` | 加えて `API_READ_TOKEN`（任意） |

アプリ（献立ノート）は `API_TOKEN` のままで、見え方は変わらない。本番の `.env` には `API_TOKEN` が設定済みのため、`API_READ_TOKEN` を正しく足せば起動条件で止まることは無い。

## 影響対象

- service/container: `coop-api.service`。`.env` の追加と `coop_api_server.py` の配置の後に再起動が要る。設定を誤ると起動しない（systemd は `Restart=always` で再起動を繰り返すが、設定を直すまで待ち受けない）
- URL/port/health: 変更なし。`/` は従来どおり認証なし（起動していれば 200）
- cron/timer/worker: 変更なし（`fetch_coop_mail.py` は `API_TOKEN` を使わず、起動条件の影響を受けない）
- dependency: 変更なし（`hmac` は標準ライブラリ）
- data/DB/volume: 変更なし
- log/monitoring: 新しいイベント `startup_configuration_invalid`（level `critical`、`reason` のコードだけ）。認証の失敗は従来どおり HTTP の 401・403
- 配布対象: `coop_api_server.py` のみ（`deploy-files.txt` の5file中1file）。ほかは baseline と同一

## production変更

- 必要性: あり
- 想定作業（VPS管理側の承認と手順で）:
  1. **`.env` の控えを取る**（中身は表示しない）: `sudo install -o root -g root -m 0600 /opt/apps/coop-api/.env /root/coop-api.env.before-009`（root だけが読める。リポジトリ・チャットに出さない）
  2. 参照専用トークンの値を作る（例: `openssl rand -hex 32`。`API_TOKEN` とは別の値）。値は画面・チャット・リポジトリに残さない（シェルの履歴にも残さない方法で）
  3. **`.env` に追記し、所有者と権限を保つ**: 同じディレクトリに一時ファイルを `root:coop-api` 0640 で作り、元の内容＋`API_READ_TOKEN=<値>` を書いて `mv` で置き換える（`.env` を直接開いて編集しない。置き換えの後 `stat` で `root:coop-api` 0640 を確認）
  4. **起動前に確認する**（値は出さない）: `API_TOKEN` と `API_READ_TOKEN` の行がそれぞれ1行だけあり、値が空でなく、互いに違うこと（例: 値の SHA-256 を比べ、ハッシュも表示しない形で一致・不一致だけを出す）
  5. `coop_api_server.py` を共通deploy経路で配置し、`coop-api` を再起動
  6. 起動の確認: `systemctl is-active coop-api`、journald に `startup` があり `startup_configuration_invalid` が無いこと、`/` が 200
  7. 値は、meal-api の通知003の反映のときに、meal-api の `.env` の `COOP_API_TOKEN` に設定する（それまでは meal-api に渡さない）
- downtime: brief-restart
- maintenance window: cron（07:00・20:00 JST）の時刻を避ける
- 反映後の確認（案、VPS内の `127.0.0.1:8003` で）:
  1. `API_TOKEN` で `GET /api/coop/ingredients`・`/api/coop/orders`・`GET /api/coop/meals` が 200（アプリの動きが変わらない）
  2. `API_READ_TOKEN` で `GET /api/coop/ingredients`・`/api/coop/orders?include_items=true` が 200
  3. `API_READ_TOKEN` で `GET /api/coop/meals` が 403（書き込み系は、取り込みを動かさないよう試さない）
  4. 無いトークン・違うトークンで 401
  5. アプリで COOP タブの一覧と 🔄 が従来どおり動く（app owner）
  6. 1の控えは、通知003の反映と確認が済むまで残し、その後に消す

## 利用者への影響

- user_maintenance_impact: possible（再起動の数秒、COOP APIが使えない可能性。`.env` を誤ると起動せず、切り戻すまで COOP タブが使えない）
- 対象利用者・機能: 家族2名のアプリ。見え方の変化なし（アプリは `API_TOKEN` のまま）

## env・secret contract

- 追加: `API_READ_TOKEN`（secret、任意。未設定なら参照専用トークンは使えない。`API_TOKEN` と同じ値なら起動しない）
- 変更: `API_TOKEN` は必須（無い・空なら起動しない）。本番は設定済み
- `.env.example` と `ops/runtime-contract.yaml` に追記した（値は伏せる）
- provisioning: 上の「想定作業」1〜4
- rotation: 値を作り直し、coop-api と meal-api の `.env` を同時に（上の3の方法で）置き換え、両方を再起動

## Data・migration・backup

- schema/format変更: なし
- migration: なし
- `.env` の控え: 上の「想定作業」1（`/root/coop-api.env.before-009`、`root:root` 0600）
- backward compatibility: `API_TOKEN` の動きは同じ。`API_READ_TOKEN` を設定しなければ従来と同じ動き（ただし `API_TOKEN` が無いと起動しない点だけ変わる。本番は設定済み）

## Deploy・rollback

- deploy前提: 通知008が反映済み（baseline `5c38323`）
- deploy手順の変更: なし（共通deploy経路・`USE_VENV=yes`）
- **rollback（起動しない・アプリの COOP タブが使えない・確認で不具合があった場合）**:
  1. `.env` を控えから戻す: `sudo install -o root -g coop-api -m 0640 /root/coop-api.env.before-009 /opt/apps/coop-api/.env`（所有者・権限を元どおりにする）
  2. `coop_api_server.py` を baseline（`5c38323` の版。通知008の反映物）に戻す（共通deploy経路で旧artifactを再配置）
  3. `coop-api` を再起動し、`systemctl is-active`・journald の `startup`・`/` の 200・アプリの COOP タブを確認
  4. `.env` だけの誤り（値の空・同じ値・重複行）なら、コードは戻さず1と3だけでもよい（新しいコードは `API_READ_TOKEN` が無ければ従来どおり動く）
- 切り戻しの判断: 再起動後 1分以内に `startup` が出ない、または `startup_configuration_invalid` が出たら、すぐに上の1（必要なら2）を行う

## Health・テスト

- health contract変更: なし（起動していれば `/` は 200）
- 実施テスト（ローカル、合成データのみ）: `pytest` 175 passed・4 skipped（POSIX の権限検証）、`python:3.10-slim`（Docker）で 179 passed。`tests/test_read_token.py`（28件）:
  - 注文の参照2つは全権・参照専用のどちらでも 200。参照専用トークンで取り込み・カテゴリ修正・メニューの一覧と削除が 403（取り込みと保存は動かない）。無い・違う・空・`Bearer` の無いトークンは 401。参照専用トークンを受け付ける関数がソースの上で2つだけ
  - **再提出で追加**: `API_TOKEN` が空・同じ値で `check_token_config` が拒否、正しい設定は通る。FastAPI の起動（lifespan）が止まり `startup` を出さず、`startup_configuration_invalid` に理由のコードだけ（値を出さない）。正しい設定では起動して `/` が 200。`python coop_api_server.py` が `API_TOKEN` 無しで終了コード3（uvicorn を読み込む前）。`API_TOKEN` が空でも認証を飛ばさない（401）
  - 既存のテストのうち、認証を飛ばす動きに頼っていたもの（カテゴリの同時保存・注文の参照）は、テスト用のトークンを渡す形に直した（検証している内容は変えない）
- 未実施: 本番の `.env`・再起動後の確認（反映後の確認で行う）

## Log・監視

- 新しいイベント: `startup_configuration_invalid`（`critical`、`reason`: `api_token_missing`・`read_token_same_as_api_token`）。値は出さない
- 新しい alert 条件（案）: `startup_configuration_invalid` が出た、または `coop-api` が active にならない。登録は VPS 管理側で判断

## 提出前セルフチェック

- baseline `5c38323` から `de6de35` までの全commitを `git rev-list --reverse` で取得し `release_commits` に記載した（`afe4041`・`64c3147` は通知008の通知書、`47cd810` は本通知の初回）。ops以外の差分は `coop_api_server.py`・tests・`API_SPEC.md`・`CLAUDE.md`・`.env.example`
- コミットにトークンの値・パスワード・メールアドレス・接続情報が無いことを確認した

## 未解決事項（判断をお願いしたい）

1. `.env.example` の `API_HOST=0.0.0.0` と、`coop_api_server.py` の既定値 `0.0.0.0` が、本番の取り決め（`127.0.0.1`）と違う（本番は `.env` で `127.0.0.1`）。見本と既定値を `127.0.0.1` に直すか（直すなら別の notice）

（初回の未解決事項「`API_TOKEN` が未設定なら認証を行わない」は、今回の修正で解消した）

## 希望時期

VPS管理レビュー後に別途調整。meal-api の第2段階（通知003予定）の前に済ませたい。

## VPS管理チャットへの引き継ぎ

- 引き継ぎ要否: 必要
- VPS管理チャットへ渡すローカル絶対path: C:\work\PRG\HomeTools\meal-planner\api\coop-api\ops\server-change-notices\20261127-COOPAPI-009-summary.md

## Approval

- app owner: 方針を承認（2026-11-27「ok」: 参照専用トークン・meal-api には参照専用トークンだけを渡す）。差し戻しへの対応を承認（2026-11-27「良いです」）
- VPS management review: 初回（`55034dc`）差し戻し（全権トークンの欠落・同値の起動時拒否、L3への訂正、`.env` の保全と切り戻し手順）。2026-11-27 に `de6de35` で再提出
- production approval: 未実施
