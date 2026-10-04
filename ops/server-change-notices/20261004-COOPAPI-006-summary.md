# Server Change Notice

record_type: server_change
template_type: full
policy_bundle_version: 2026-09-04.1
notice_id: 20261004-COOPAPI-006
app: coop-api
source_branch: main
source_commit: 684d77e84b58bbcb80db95b9beb54917dfd64ad2
production_baseline_commit: c1c0505d88f904529a2e1dafe1066aad6c360430   # VPS1の稼働hashからVPS管理が確認（2026-10-04）
release_commits: 78431bbebb8fe170d6eb4f50478496fb29817bc8, 684d77e84b58bbcb80db95b9beb54917dfd64ad2
impact_level: L3
status: ready_for_review
created_by: Claude
production_change: required
vps_management_handoff: required
deployment_status: not_started

## 変更概要

COOP注文を、取込のたびに上書きする方式から、`data/coop_orders.json` へ追記する注文履歴へ変更する。あわせて次を追加する。

- `GET /api/coop/orders` を新しい順にし、`include_items=true` で注文ごとの商品一覧を返す（既定は従来どおり概要のみ）。
- 手動実行用の過去分取込 `python fetch_coop_mail.py --backfill-days N`。期間内の注文メールを全件取り込み、履歴だけに追記する。`coop_latest.json` は更新せず、Jevは呼ばずキーワード分類のみを使う。

金額は取得しない（注文メール本文に含まれるかは未確認のため対象外）。

## 変更理由

過去の注文商品を参照できず、「最後に作った日」表示や買い物メモの土台にできないため。アプリ（meal-planner-app）のCOOPタブに「過去の注文」を追加する前提でもある。

## server_impact判定

server_impact: approval_required
判定理由: schemaは不変だが、永続データ `coop_orders.json` の意味（1件で上書き → 蓄積）と保存方式を変えるためL3。rollback時に蓄積済み履歴を失う可能性があり、backup・restore条件の確認が必要。

## 現在と変更後

| 項目 | 現在（baseline想定） | 変更後 |
|---|---|---|
| `coop_orders.json` | 取込ごとに `orders` が通常1件へ上書きされる | 既存履歴に追記。注文日＋`source_fingerprint`で重複除外し、注文日の古い順に保存 |
| 既存履歴が読めない場合 | （該当なし） | 履歴の保存だけをskipし、既存fileを上書きしない。`coop_latest.json` の更新は従来どおり行う |
| `GET /api/coop/orders` | 概要のみ。file順（古い順） | 新しい順。`source_fingerprint` を追加。`include_items=true` で `items`（name、original_name、quantity、category）を追加 |
| 通常取込（cron・`POST /api/coop/fetch`） | 最新1通の注文を処理 | 変更なし（履歴への追記だけ増える） |
| 過去分取込 | なし | 手動CLIのみ（`--backfill-days N`）。cron・APIからは実行されない |
| ジョブの成否（`job_end.status`） | 最新の注文確認メールを1通取れれば成功 | 通常取込の条件は不変。加えて、**注文履歴を保存できなかった場合（既存fileが読めない・書込失敗）は `failure`**。過去分取込は、**1通でも取得・解析に失敗したら `failure`**（取得できた分は保存済みで、再実行しても重複しない） |
| 履歴の書込失敗時の最新注文 | （該当なし） | 最新注文（`coop_latest.json`）の更新は続ける。履歴の失敗でジョブが中断し、最新注文が更新されなくなることはない |

## 影響対象

- service/container: `coop-api.service`。設定変更なし。要restart
- URL/port/health: 変更なし。`/api/coop/orders` は認証必須のまま。追加するのは任意のquery parameterのみ
- cron/timer/worker: 時刻・定義は変更なし。07:00/20:00の取込は同じ `.coop_import.lock` を使う。過去分取込も同じlockを取る
- dependency: 変更なし（`requirements.txt` は不変）
- data/DB/volume: `data/coop_orders.json` の追記化。新しいfile・directoryは作らない。1注文あたり数KB想定（実測は未実施）
- log/monitoring: 過去分取込のjob名は `coop-mail-backfill`（既存の `job_start` / `job_end` 規約に従う）。通常取込の `coop-mail-import` は不変。`results_saved`（`target=coop_orders`）に `added_count` を追加。履歴を保存できない場合は `results_save_failed` をerrorで記録する（`reason=history_unreadable`＝既存fileが読めない、`reason=history_write_failed`＝書込失敗。後者は `error_class` を伴う）。同じrunの `job_end` は `status=failure`、level=error になる。商品名・本文は出力しない
- `POST /api/coop/fetch` の応答は変更しない（取得した注文件数を返す）。履歴の保存失敗はAPI応答ではなく、ログの `results_save_failed` と `job_end` で判別する
- 配布対象: `coop_api_server.py`、`fetch_coop_mail.py`（`deploy-files.txt` の5file中、変更は2file）。`.env`、`data/`、test、通知書は配布対象外

## production変更

- 必要性: あり
- 想定作業: VPS管理側の別承認後に、2fileを既存の共通deploy経路で反映し、service再起動と動作確認を行う
- downtime: brief-restart想定
- maintenance window: VPS管理側で決定
- 過去分取込の本番実行は、本noticeのdeployとは**別の操作**として、VPS管理側が実行日数と時刻を決める。実行前後で `coop_orders.json` の件数と構文を確認する

## 利用者への影響

- user_maintenance_impact: none
- 対象利用者・機能: 家族2名のアプリ利用。restart中の数秒だけCOOP APIが使えない可能性がある。未更新のmeal-planner-appは `/orders` を呼ばないため影響しない
- 通知方法: 不要想定（VPS管理側reviewで判断）

## env・secret contract

- 変更: なし
- 変数名・secret種類のみ: 変更なし
- provisioning/rotation: なし

## Data・migration・backup

- schema/format変更: なし。`orders` 配列の各要素の形は従来と同じ（`source_fingerprint` は2026-09-27以降のデータにある既存項目）
- migration: なし。既存の `coop_orders.json`（通常1件）はそのまま履歴の先頭になる
- 既存データとの照合: `source_fingerprint` を持たない旧レコードは、メール件名と日時を代用keyとして重複判定する。そのため、同一注文が旧レコードと新レコードで**1回だけ重複して並ぶ可能性がある**（同じ注文日の2件として表示。誤動作ではなく許容する）
- backup対象: `coop_orders.json` は既存のbackup対象（`data/` ごと）。履歴が失えない価値を持つため `backup_required: true` のまま、`regenerable` の扱いはruntime contractへ記載済み。新しい一時file・directoryは追加しないため、backup除外条件に変更はない
- 書込方式: 従来どおり同一pathへの直接書込（一時fileを使った原子的置換は**していない**）。書込は `.coop_import.lock` で直列化される。書込中にプロセスが異常終了すると、履歴fileが壊れる可能性がある。壊れた場合は次回以降の履歴保存をskipし（上書きしない）、復旧はbackupまたはGmailからの過去分取込で行う
- restore確認: 本noticeでは未実施。反映前の `coop_orders.json` のbackup確認を前提とする
- backward compatibility: endpointと認証は維持。`/orders` は項目追加と並び順の変更のみ。旧データ（`source_fingerprint` 無し）でも応答できる

## Deploy・rollback

- deploy前提: 通知005の反映状態（分類override保存・一時directory・backup除外）を前提とするため、005の反映完了をVPS管理側で確認する。反映前に `coop_orders.json` のbackupを確認する
- deploy手順の変更: なし（共通deploy経路・`USE_VENV=yes`）
- rollback方法（artifact）: 旧artifactの2fileを再配置しservice再起動
- **rollbackに関する注意（data）**: 旧コードは取込のたびに `coop_orders.json` を1件へ上書きする。rollback後に取込（cron・手動）が走ると、蓄積した履歴が失われる。rollbackする場合は、先に現在の `coop_orders.json` を別の場所へ退避する。履歴の復元は退避fileまたは確認済みbackupの隔離restoreで行い、実データを確認せずにdata rollbackしない
- rollback不能条件: 履歴fileの退避・backupがないままrollback後に取込が走った場合（Gmailに残る範囲は過去分取込で再取得できるが、Gmail側から消えた注文は戻らない）

## Health・テスト

- health contract変更: なし
- 実施テスト（ローカル、合成データのみ）: `pytest`（90 passed、POSIX所有検証2 skipped）。追加した主なテスト:
  - 過去分取込の一部取得失敗が `job_end=failure` になり、取得済みの分は保存され、失敗解消後の再実行は成功して重複しないこと
  - 履歴が読めない場合・書込に失敗した場合に `job_end=failure` となり、最新注文は更新されること（修正前のコードでは3件が失敗することを確認済み）
  - 通常取込は、従来どおり最新の注文確認メールが取れれば成功すること
  - 追記と重複除外（同じメールの再取込、同じ内容で別週の注文は残す）
  - 既存履歴が壊れている場合に上書きしないこと
  - 過去分取込が全件を履歴にだけ追記し、`coop_latest.json` を変えず、Jevを呼ばないこと
  - `/api/coop/orders` が新しい順で、`include_items` の有無で出力が変わること
  - 取込lockの競合（既存テスト）
- 結果: ローカル検証成功。ただしWindows実行環境のため、POSIXのfile mode・groupは未確認
- 未実施テストと理由:
  - production相当のファイル権限（`coop-api` ユーザーが既存の `coop_orders.json` へ追記できること）: production未反映のため
  - 実Gmailに対する過去分取込（IMAPの負荷・所要時間・lock保持時間）: 実メールへの接続は行っていないため
  - 異常終了時のfile破損: 発生時の挙動（上書きしないこと）は合成データで確認したが、実際の途中終了は再現していない

## Log・監視

- log量/形式/保存先変更: 構造化ログ（stdout）のまま。過去分取込時に1メールごとの既存event（`email_candidate_processing` 等）が増える
- 新しいalert条件: なし。`results_save_failed` はerrorだが、alert登録の要否はVPS管理側で判断
- secret/個人情報対策: 商品名・メール本文・件名・送信者をlogへ出さない（既存規約のまま）

## 提出前セルフチェック

### 再提出（2026-10-04 JST）の差分確認

初回提出（draft・source `78431bb`）に対するVPS管理側レビュー（受理台帳 `blocked`）の指摘を受けた再提出。前回review対象commit: `843487a`（通知書の最終commit。コードは `78431bb`）。

- 指摘: ①過去分取込の部分取得失敗、②注文履歴の保存失敗が、成功として記録される経路がある。
- 対応commit: `684d77e84b58bbcb80db95b9beb54917dfd64ad2`（`fetch_coop_mail.py` とtestのみ。`coop_api_server.py`・依存・配布一覧・認証・data形式は不変）。
  - ①: 過去分取込は1通でも取得・解析に失敗したら `job_end=failure`。取得済みの分は保存し、再実行は重複しない。
  - ②: 履歴が読めない・書込に失敗した場合は `job_end=failure`。あわせて、履歴の書込失敗でジョブが `job_failed` で中断し最新注文が更新されなくなる経路（修正前は `OSError` が伝播）を解消し、最新注文の保存は続ける。
  - 通常取込の成功条件（最新の注文確認メールが1通取れれば成功）は変更していない。
- 確認: pytest 90 passed（2 skipped）。新規4件のうち3件は修正前コードで失敗することを確認し、残る1件（通常取込の成功条件）は修正の前後とも通る。
- baseline: VPS管理側がVPS1の稼働hashから `c1c0505` と確認済み（初回の仮置きは確定）。005の分類override保存・一時directory・Jev cache保存先は反映済みとして扱う。
- 未実施: VPS管理側 `tools/review_notice_preflight.ps1`（この環境のPowerShell 5.1では実行できない。前回と同じ）。再提出commit・実remoteの一致確認は、push後にVPS管理側で行う。
- 以下は初回提出時の確認結果（コード差分は上記以外に変更なし）。

### 初回提出時の確認

- 実施日: 2026-10-04 JST。
- 確認できた項目:
  - source `78431bb`（コード変更）は実remoteの `main` へpush済み。通知書commit `f80973d` も同じ `main` に載り、local HEADと `git ls-remote` の結果が一致した。
  - 配布対象（`deploy-files.txt` の5file）のうち、baseline想定 `c1c0505` からの変更は `coop_api_server.py` と `fetch_coop_mail.py` の2fileのみ。`c1c0505..78431bb` の他の差分は `ops/`・`API_SPEC.md`・`CLAUDE.md`・testで、配布対象外。依存・認証・bind・cron・env変数名は不変。
  - 追跡対象のworking treeはcleanになる（`.gitignore` へのローカル設定file除外1行はcommit `ee1ccb4`。配布対象外）。未追跡の `AGENTS.md` は既存で配布一覧に含まれない。
  - ローカルのpytestは86 passed（2 skipped）。秘密値・IP・URLの混入なし（通知書と差分を走査）。
- **未実施**: VPS管理側の `tools/review_notice_preflight.ps1`。この環境のPowerShell 5.1では構文エラー（BOMなしUTF-8）となり、BOM付きの一時copyでも .NET Core専用のAPI（`Path.GetRelativePath`）で停止した。PowerShell 7が必要と思われる。VPS管理側での実行を依頼する。
- 実施していない確認: POSIXでのfile mode・group、実Gmailでの過去分取込（baselineの照合は、その後VPS管理側が完了）。

## 未解決事項

1. （解消）production baselineは、VPS管理側がVPS1の稼働hashから `c1c0505` と確認した。なお `production_deployments.yaml` の記載は古いままで、正本の更新はVPS管理側の作業。
2. 反映前の `coop_orders.json` のbackup確認をVPS管理側で行う。
3. 過去分取込の本番実行は別承認とし、日数・時刻・実行ユーザー・実行後の確認をVPS管理側で決める。
4. 金額は対象外。メール本文に金額があるかは未確認。
5. meal-planner-appの「過去の注文」画面は実装済み・端末配信は別途。サーバー未反映の間は、日付と件数のみ表示し「商品一覧はサーバー更新後に表示されます」と出る。

## 希望時期

VPS管理レビュー後に別途調整。急がない。

## VPS管理チャットへの引き継ぎ

- 引き継ぎ要否: 必要
- ユーザーへの案内: 再提出。VPS管理チャットへ、再レビューの依頼を送る
- VPS管理チャットへ渡すローカル絶対path: C:\work\PRG\HomeTools\meal-planner\api\coop-api\ops\server-change-notices\20261004-COOPAPI-006-summary.md

## Approval

- app owner: 初回提出後、指摘（部分取得失敗・履歴保存失敗の成功扱い）を修正して再提出
- VPS management review: 初回は `blocked`（2026-10-04）。再reviewは未実施
- production approval: 未実施
- related task_id: なし（ユーザー指示によるClaude直接実装）
