# Server Change Notice

record_type: server_change
template_type: full
policy_bundle_version: 2026-09-04.1
notice_id: 20261118-COOPAPI-008
app: coop-api
source_branch: main
source_commit: a09b441e733b58486b516f4dadbee7f6282fb73b
production_baseline_commit: c94f8a0c3a310727779610d74f01b8a80d228913
release_commits: a09b441e733b58486b516f4dadbee7f6282fb73b
impact_level: L2
status: ready_for_review
created_by: Claude
production_change: required
vps_management_handoff: required
deployment_status: not_started

## 変更概要

COOPメールの取得失敗を、「注文確認メールが無かった」と区別して返す。あわせて、通常取込の直近の成否を記録してAPIで返し、古い注文メールで最新注文が戻らないようにする。

1. `POST /api/coop/fetch`: 取得そのものに失敗した場合（Gmailにログインできない・接続できない等）は、HTTP 502 で `{"status":"error","error_kind":…,"detail":…}` を返す。`no_data` は、取得に成功して注文確認メールが無かったときだけ返す。
2. 通常取込（cronの定期取込・`POST /api/coop/fetch`）のたびに、成否を `data/coop_fetch_status.json` に記録する（新規file）。`GET /api/coop/ingredients` の応答に `fetch_status` を追加する（記録前は `null`）。
3. 保存済みの最新注文（`coop_latest.json`）より前の注文日の注文は、最新注文にしない（履歴にだけ追加する）。cronの定期取込では、そのような古い注文メールは分類（Jev）・保存せずに終える。

## 変更理由

2026-10-10 20:00 から 11-18 07:00 まで、Gmailのアプリパスワード失効でIMAP認証が78回失敗し、注文の取り込みが止まっていた（VPS管理側の確認による）。しかし `POST /api/coop/fetch` は失敗時も `no_data`（「注文確認メールが見つかりませんでした」）を返し、cronの失敗はログにしか残らないため、利用者が気づくまで約6週間かかった。アプリ（別途配信）が、`fetch_status` と502を使って、失敗をCOOPタブに表示する。

また、復旧時に古い注文メール（10/14・10/21お届け分）を受け取り直したため、現行の取込では次の定期取込で最新注文が古い注文に戻るおそれがあった（今回は過去分取込で回避した）。3はその再発防止。

## server_impact判定

server_impact: approval_required
判定理由: APIの応答に失敗時の新しいHTTPステータス（502）と項目（`fetch_status`）を追加し、`data/` に新しいfileを作るため。既存の項目・認証・bind・cron定義は不変。保存済みデータの意味・形式は変えない。

## 現在と変更後

| 項目 | 現在（baseline `c94f8a0`） | 変更後 |
|---|---|---|
| `POST /api/coop/fetch` の取得失敗 | HTTP 200 `no_data` | HTTP 502 `status=error`、`error_kind`、`detail`（利用者向けの文） |
| `POST /api/coop/fetch` のメールなし | HTTP 200 `no_data` | 同左 |
| `POST /api/coop/fetch` の成功・busy | HTTP 200 `success` / `busy` | 同左 |
| `GET /api/coop/ingredients` | 最新注文の食材 | `fetch_status`（下表）を追加。既存項目は不変 |
| 取込の成否の記録 | ログ（`job_end`）のみ | ログに加えて `data/coop_fetch_status.json` |
| 最新注文より前の注文 | 最新注文を上書きする | 履歴にだけ追加。cronでは分類せずに `import_skipped`（`reason=older_than_latest`）で終える |
| 過去分取込（`--backfill-days`） | 履歴のみ | 同左。取込状況は記録しない |

`fetch_status` の項目: `last_attempt_at`（日本時間）、`last_result`（`success`/`failure`。メールが無かっただけなら `success`）、`last_error_kind`（`auth`/`connection`/`message`/`configuration`/`save`/`internal`、成功時は `null`）、`last_success_at`、`consecutive_failures`。秘密値・メール本文・件名・商品名・金額は含めない。

## 影響対象

- service/container: `coop-api.service`。設定変更なし。要restart
- URL/port/health: 変更なし。認証・bind・公開面は不変。`POST /api/coop/fetch` が失敗時に502を返す（HTTPS入口・proxyで502を書き換えないこと）
- cron/timer/worker: 時刻・定義・lock・終了コードは変更なし。cronの取込も成否を `coop_fetch_status.json` に書く
- dependency: 変更なし（`requirements.txt` 不変。標準ライブラリの `dataclasses` を追加で使うのみ）
- data/DB/volume: **新しいfile `data/coop_fetch_status.json`**（数百バイト、取込のたびに上書き）。書き込みは同じdirectoryの一時file（`.coop_fetch_status.json.<ランダム>.tmp`）から `os.replace` で置き換える。所有者は書いたプロセス（API・cronとも `coop-api`）、modeはプロセスのumaskに従う（既存の `coop_orders.json` と同じ作り方）。既存fileの形式は不変
- log/monitoring: 新しいイベントなし。既存の `import_skipped` に `reason=older_than_latest`、`results_save_skipped` に `target=coop_latest`・`reason=older_than_latest`、`results_save_failed` に `target=coop_fetch_status` が加わる
- 配布対象: `coop_api_server.py`、`fetch_coop_mail.py`（`deploy-files.txt` の5file中、変更は2file）。`coop_parser.py`・`jev_classifier.py`・`requirements.txt`・`.env`・`data/`・test・文書・通知書は配布対象外・不変

## production変更

- 必要性: あり
- 想定作業: VPS管理側の別承認後に、2fileを既存の共通deploy経路で反映し、service再起動と動作確認を行う
- downtime: brief-restart想定
- maintenance window: VPS管理側で決定。cron（07:00・20:00 JST）の時刻を避ける
- 反映後の確認（案）:
  1. `POST /api/coop/fetch` が HTTP 200（`success` または `no_data`）を返し、`data/coop_fetch_status.json` が作られ、`last_result=success` であること。所有者・グループ・modeが他の `data/` のfileと同じ（`coop-api:coop-api`・0640）であること。異なる場合は報告をお願いしたい（`.env` の `GMAIL_APP_PASSWORD` を変えて502を確かめる試験は、本番では行わない）
  2. `GET /api/coop/ingredients` に `fetch_status` が含まれること
  3. 次のcron取込の後も `last_result=success` であること

## 利用者への影響

- user_maintenance_impact: none
- 対象利用者・機能: 家族2名のアプリ利用。restart中の数秒だけCOOP APIが使えない可能性がある
- 見え方の変化: 取得失敗時、**アプリを更新していない端末**は「メール取得に失敗しました: サーバーエラー (502)」と表示する（現在の「見つかりませんでした」より正確）。更新済みのアプリ（`367447a`、配信は本反映の後）は、失敗の理由と、定期取込が失敗している間の警告をCOOPタブに表示する
- 古い注文メールを受け取り直しても、COOPタブの最新注文は変わらない

## env・secret contract

- 変更: なし
- 変数名・secret種類のみ: 変更なし
- provisioning/rotation: なし。なお、Gmailの認証情報は起動時に1回だけ読むため、`.env` の差し替え後は service の再起動が必要（現行どおり。今回の復旧で確認された点を `CLAUDE.md` に記録した）

## Data・migration・backup

- schema/format変更: 既存fileはなし。新しいfile `coop_fetch_status.json` を追加
- migration: なし。初回の取込まで `fetch_status` は `null`
- backup対象: `coop_fetch_status.json` は、次回の取込で作り直される状況の記録で、backupは不要（`data/` ごとのbackupに含まれても問題ない）。一時file（`.coop_fetch_status.json.*.tmp`）は通常残らないが、書き込み途中の異常終了で残った場合は削除してよい。backupの除外条件に加えるかはVPS管理側で判断
- restore確認: 不要（失っても次の取込で作られる）
- backward compatibility: 既存の項目・endpoint・認証は維持。旧アプリは `fetch_status` を無視し、502は「サーバーエラー」として表示する。新アプリは、`fetch_status` が無い・502を返さない旧サーバーでも従来どおり動く

## Deploy・rollback

- deploy前提: 通知007が反映済みであること（baseline `c94f8a0`。runtime contract に反映済みと記録）
- deploy手順の変更: なし（共通deploy経路・`USE_VENV=yes`）
- rollback方法（artifact）: 旧artifactの2fileを再配置しservice再起動
- rollback時のdata: `coop_fetch_status.json` は旧コードから読まれず、残っても無害（削除してもよい）。既存の `coop_orders.json`・`coop_latest.json` の形式は変えないため、データのrollbackは不要

## Health・テスト

- health contract変更: なし
- 実施テスト（ローカル、合成データのみ）: `pytest`（113 passed、POSIX所有検証2 skipped）。追加した主なテスト:
  - Gmailのログイン失敗で、`POST /api/coop/fetch` が502・`error_kind=auth` を返し、`coop_fetch_status.json` に失敗が記録されること
  - 接続失敗・タイムアウト・その他の例外の `error_kind`（`connection`・`internal`）
  - 注文確認メールが無い場合は従来どおり200 `no_data`、記録は `success`
  - 連続失敗の回数が増え、成功で0に戻り、`last_success_at` が更新されること
  - 過去分取込とbusyでは記録しないこと。記録の書き込みに失敗しても取込の結果が変わらず、一時fileが残らないこと
  - `GET /api/coop/ingredients` の `fetch_status`（記録前は `null`）
  - 最新注文より前の注文は最新注文にせず履歴にだけ追加し、同じ週の注文の変更メールは最新注文を更新すること。cronでは古い注文メールを分類せずに終えること
  - 既存テスト（成否の判定・終了コード・lock・重複除外・過去分取込）は変更なしで成功（busyのテストは、呼び出す関数名の変更に合わせて差し替え先だけ変えた）
- 結果: ローカル検証成功。ただしWindows実行環境のため、POSIXでの権限・`os.replace` の確認は未実施
- 未実施テストと理由:
  - production相当のファイル権限・実際のcron取込: production未反映のため
  - 実Gmailへの接続: 本番の認証情報を使う試験はしていない

## Log・監視

- log量/形式/保存先変更: なし（上記のreason・targetの追加のみ）
- 新しいalert条件: なし。`coop_fetch_status.json` の `consecutive_failures` を監視に使うかは、VPS管理側で判断（今回の停止は、`external_call_failed`（`error_kind=auth`）の連続で検知できた）
- secret/個人情報対策: 状況fileとログに、秘密値・メール本文・件名・送信者・商品名・金額を出さない

## 提出前セルフチェック

通知006で全項目を実施済みのため、今回は変更範囲に絞って確認した。

- baseline `c94f8a0` から `source_commit` までのops以外の差分は、配布対象の `coop_api_server.py`・`fetch_coop_mail.py` と、文書・test（`API_SPEC.md`・`CLAUDE.md`・`tests/`）。`coop_parser.py`・`jev_classifier.py`・`requirements.txt`・`deploy-files.txt` は変更なし。依存・認証・bind・cron・env変数名・lock・終了コードは不変。
- 配布物に `.env`・`data/`・一時file・実メールの内容が入らないこと、通知書とコミットに実際の金額・番号・URL・アドレス・パスワードが無いことを確認した。
- VPS管理側の `review_notice_preflight.ps1` は、アプリ側のPowerShell 5.1では実行できない（通知006・007と同じ）。VPS管理側での実行をお願いしたい。

## 未解決事項

1. `coop_fetch_status.json` の所有者・modeが既存の `data/` のfileと揃うか（umaskに依存）の、本番での確認。
2. `consecutive_failures` を監視・通知に使うかの判断（VPS管理側）。
3. アプリ（`367447a`）の端末配信は、本noticeのproduction反映・確認の後に、app ownerが別途判断する。

## 希望時期

VPS管理レビュー後に別途調整。

## VPS管理チャットへの引き継ぎ

- 引き継ぎ要否: 必要
- VPS管理チャットへ渡すローカル絶対path: C:\work\PRG\HomeTools\meal-planner\api\coop-api\ops\server-change-notices\20261118-COOPAPI-008-summary.md

## Approval

- app owner: 方針を承認（2026-11-18「その対応、進めましょう」「良いです」）
- VPS management review: 未実施
- production approval: 未実施（本番反映・端末配信は別承認）
