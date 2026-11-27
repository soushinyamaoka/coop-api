# Server Change Notice

record_type: server_change
template_type: full
policy_bundle_version: 2026-09-04.1
notice_id: 20261127-COOPAPI-009
app: coop-api
source_branch: main
source_commit: 55034dc7c9797a9eb47063a930a508eb38a33865
production_baseline_commit: 5c38323efabe231b075da802bba6e3479ca43549
release_commits: afe40412db64f29e03ce121709f3dfe7428ebd17, 64c3147b1a38d42cc874567cab50b3e6e08cde6a, 55034dc7c9797a9eb47063a930a508eb38a33865
impact_level: L2
status: ready_for_review
created_by: Claude
production_change: required
vps_management_handoff: required
deployment_status: not_started

## 変更概要

coop-api に、**注文の参照だけに使える参照専用トークン** `API_READ_TOKEN` を追加したい。

- `GET /api/coop/ingredients`・`GET /api/coop/orders` は、従来の全権トークン（`API_TOKEN`）か参照専用トークンで使える
- それ以外のAPI（メールの取り込み・カテゴリの修正・献立作成・レシピ提案・メニューの登録と削除など）は、参照専用トークンでは **403**「参照専用のトークンでは使えない操作です」
- トークンの照合を `hmac.compare_digest`（時間差で推測されない比べ方）にする。`API_READ_TOKEN` が未設定なら、参照専用トークンは使えない（空のトークンは一致しない）

## 変更理由

meal-api 通知001のレビューで、第2段階（COOPの参照）の前提として「coop-api の参照専用トークン」が求められた。meal-api に全権の `API_TOKEN` を渡すと、COOPの注文を読むだけの連携が、取り込み・分類の修正・献立作成までできてしまう。meal-api には参照専用トークンだけを渡す（meal-api への設定は、meal-api の第2段階の通知003で行う）。

## server_impact判定

server_impact: approval_required
判定理由: 認証の方式（受け付けるトークンの種類）と `.env` の変数を追加するため。bind・port・公開面・データは不変。

## 現在と変更後

| 項目 | 現在（baseline `5c38323`、通知008反映後） | 変更後 |
|---|---|---|
| `GET /api/coop/ingredients`・`/api/coop/orders` | `API_TOKEN` | `API_TOKEN` または `API_READ_TOKEN` |
| ほかのAPI | `API_TOKEN` | `API_TOKEN`（`API_READ_TOKEN` は403） |
| 合わない・無いトークン | 401 | 401（同じ） |
| トークンの照合 | 文字列の `!=` | `hmac.compare_digest` |
| `.env` | `API_TOKEN` | 加えて `API_READ_TOKEN`（任意。未設定なら参照専用トークンは使えない） |

アプリ（献立ノート）は `API_TOKEN` のままで、見え方は変わらない。

## 影響対象

- service/container: `coop-api.service`。`.env` の追加と、`coop_api_server.py` の配置の後に再起動が要る
- URL/port/health: 変更なし。`/` は従来どおり認証なし
- cron/timer/worker: 変更なし（`fetch_coop_mail.py` は不変）
- dependency: 変更なし（`hmac` は標準ライブラリ）
- data/DB/volume: 変更なし
- log/monitoring: 変更なし（認証の失敗は従来どおり HTTP の 401・403）
- 配布対象: `coop_api_server.py` のみ（`deploy-files.txt` の5file中1file）。`fetch_coop_mail.py`・`coop_parser.py`・`jev_classifier.py`・`requirements.txt` は baseline と同一

## production変更

- 必要性: あり
- 想定作業（VPS管理側の承認と手順で）:
  1. 参照専用トークンの値を VPS 管理側で作る（例: `openssl rand -hex 32`。`API_TOKEN` とは別の値）。チャット・リポジトリには書かない
  2. `/opt/apps/coop-api/.env`（`root:coop-api` 0640 を維持）に `API_READ_TOKEN=<値>` を追加
  3. `coop_api_server.py` を共通deploy経路で配置し、`coop-api` を再起動（`.env` は起動時に読むため）
  4. 値は、meal-api の通知003の反映のときに、meal-api の `.env` の `COOP_API_TOKEN` に設定する（それまでは meal-api に渡さない）
- downtime: brief-restart
- maintenance window: cron（07:00・20:00 JST）の時刻を避ける
- 反映後の確認（案、VPS内の `127.0.0.1:8003` で）:
  1. `API_TOKEN` で `GET /api/coop/ingredients`・`/api/coop/orders`・`GET /api/coop/meals` が 200（アプリの動きが変わらない）
  2. `API_READ_TOKEN` で `GET /api/coop/ingredients`・`/api/coop/orders?include_items=true` が 200
  3. `API_READ_TOKEN` で `GET /api/coop/meals` が 403（書き込み系は、取り込みを動かさないよう試さない）
  4. 無いトークン・違うトークンで 401
  5. アプリで COOP タブの一覧と 🔄 が従来どおり動く（app owner）

## 利用者への影響

- user_maintenance_impact: possible（再起動の数秒、COOP APIが使えない可能性）
- 対象利用者・機能: 家族2名のアプリ。見え方の変化なし（アプリは `API_TOKEN` のまま）

## env・secret contract

- 追加: `API_READ_TOKEN`（secret、任意。未設定なら参照専用トークンは使えない）。`.env.example` と `ops/runtime-contract.yaml` に追記した（値は伏せる）
- provisioning: VPS 管理側で生成し `.env` に追加。meal-api へは通知003で渡す
- rotation: 値を作り直して coop-api と meal-api の `.env` を同時に置き換え、両方を再起動

## Data・migration・backup

- schema/format変更: なし
- migration: なし
- backward compatibility: `API_TOKEN` の動きは同じ。`API_READ_TOKEN` を設定しなければ従来と同じ動き

## Deploy・rollback

- deploy前提: 通知008が反映済み（baseline `5c38323`）
- deploy手順の変更: なし（共通deploy経路・`USE_VENV=yes`）
- rollback: 旧 `coop_api_server.py`（`5c38323` の版）を再配置して再起動。`.env` の `API_READ_TOKEN` は残っても旧コードは読まない（消してもよい）

## Health・テスト

- health contract変更: なし
- 実施テスト（ローカル、合成データのみ）: `pytest` 165 passed・4 skipped（POSIX の権限検証）、`python:3.10-slim`（Docker）で 169 passed。追加した `tests/test_read_token.py`（18件）:
  - 注文の参照2つ（`include_items` 付きを含む）は全権・参照専用のどちらでも 200
  - 参照専用トークンで、取り込み（`POST /api/coop/fetch`）・カテゴリ修正（`PUT /api/coop/classify`）・メニューの一覧と削除が 403。取り込みと保存は動かない
  - 無い・違う・空・`Bearer` の無いトークンは 401
  - 参照専用トークンが未設定のとき、空の `Bearer` は 401
  - 参照専用トークンを受け付ける関数が、ソースの上で `get_ingredients`・`get_orders` の2つだけ
- 未実施: 本番の `.env`・再起動後の確認（反映後の確認で行う）

## Log・監視

- 変更なし。参照専用トークンで使えない操作は 403 で返る（アクセスログは従来どおり出していない）

## 提出前セルフチェック

- baseline `5c38323` から `55034dc` までの全commitを `git rev-list --reverse` で取得し `release_commits` に記載した（`afe4041`・`64c3147` は通知008の通知書のみ）。ops以外の差分は `coop_api_server.py`・`tests/test_read_token.py`・`API_SPEC.md`・`CLAUDE.md`・`.env.example`
- コミットにトークンの値・パスワード・メールアドレス・接続情報が無いことを確認した

## 未解決事項（判断をお願いしたい）

1. `API_TOKEN` が未設定のとき、認証を行わない（従来からの動き。本番は設定済み）。未設定なら起動を止める・すべて401にする等へ変えるか（app owner・VPS管理側で判断。変えるなら別の notice）
2. `.env.example` の `API_HOST=0.0.0.0` と、`coop_api_server.py` の既定値 `0.0.0.0` が、本番の取り決め（`127.0.0.1`）と違う（本番は `.env` で `127.0.0.1`）。見本と既定値を `127.0.0.1` に直すか（直すなら別の notice）

## 希望時期

VPS管理レビュー後に別途調整。meal-api の第2段階（通知003予定）の前に済ませたい。

## VPS管理チャットへの引き継ぎ

- 引き継ぎ要否: 必要
- VPS管理チャットへ渡すローカル絶対path: C:\work\PRG\HomeTools\meal-planner\api\coop-api\ops\server-change-notices\20261127-COOPAPI-009-summary.md

## Approval

- app owner: 方針を承認（2026-11-27「ok」: 参照専用トークン・meal-api には参照専用トークンだけを渡す）
- VPS management review: 未実施
- production approval: 未実施
