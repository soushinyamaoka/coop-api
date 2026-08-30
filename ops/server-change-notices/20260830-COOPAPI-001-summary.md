# Server Change Notice

notice_id: 20260830-COOPAPI-001

app: coop-api

source_branch: main

source_commit: f26c211

impact_level: L2

status: applied

created_by: Codex

## 変更概要

coop-api本体と定期メール取得処理のログを、stderrへ出力する共通ログ規約v1の1行JSON形式へ統一する。定期処理が作成していた `logs/fetch_coop.log` へのファイル出力を廃止する。

## 変更理由

ログの収集とローテーションをjournald側へ一元化し、機械的な監視に必要な必須フィールド、定期処理の開始・終了、外部依存失敗を安全に記録するため。

## server_impact判定

server_impact: notify

判定理由: ファイルログを廃止してstderrへ出力先を変更し、ログ形式・イベント・個人情報マスキング規則も変更するためL2通知対象。

## 現在と変更後

| 項目 | 現在 | 変更後 |
|---|---|---|
| 定期処理ログ出力先 | `logs/fetch_coop.log` とstderrの二重出力 | stderrのみ |
| APIログ形式 | プレーンテキスト | 共通ログ規約v1の1行JSON |
| 定期処理ログ形式 | プレーンテキスト | 共通ログ規約v1の1行JSON |
| 定期処理イベント | 開始・終了の固定イベントなし | `job_start` / `job_end` と同一 `run_id` |
| 機密・個人情報対策 | メールアドレスと件名がログに含まれ得る | 認証情報の有無、件数、例外クラス等の非識別情報のみ |
| uvicornアクセスログ | プレーンテキストで出力 | 無効化（query文字列付きURLの混入を防止） |

## 影響対象

- service/container: coop-api systemd service。**2026-08-30、本noticeとは別のVPS管理側起点の変更として、stdout/stderrをjournaldへ向けるdrop-in（`coop-api.service.d/logging.conf`）を追加済み**（VPS管理側 `docs/operations/change_notice_ledger.md` の「2026-08-30 ログ経路変更」を参照）。ベースのunit fileとapp codeは未変更。本notice自体（app code deploy）はunit fileを変更しない
- URL/port/health: 変更なし
- cron/timer/worker: `/etc/cron.d/coop-api` が起動する既存workerのログ出力のみ変更。scheduleと実行ユーザーは変更なし
- dependency: Gmail IMAP、recipe-generator、recipe-searchの接続契約は変更なし
- data/DB/volume: 変更なし
- log/monitoring: journaldで1行JSONを収集する前提へ変更。従来のファイルログ監視があれば切替確認が必要

## production変更

- 必要性: あり（本通知のreviewと別途承認後のアプリ配置・再起動）
- 想定作業: legacy route（`deploy-coop-api.bat` → `/opt/apps/deploy.sh coop-api`）。venv使用ありのため `pip install -r requirements.txt` を実行するが、VPS側venvの依存版（fastapi 0.115.0 / uvicorn 0.30.0 / httpx 0.27.0 / python-dotenv 1.0.1、2026-08-30確認）は`requirements.txt`と完全一致しており実質no-opの見込み
- downtime: `systemctl restart` によるbrief-restart。2026-08-30のrecipe-generator deploy実績では起動確認まで数秒
- maintenance window: 不要（brief-restartのため）

## 利用者への影響

- user_maintenance_impact: possible
- 対象利用者・機能: deploy時のservice再起動によるCOOP連携APIの数秒間の停止
- 通知方法: 利用規模が小さいため事前告知は行わず、deploy後検証（health・journald確認）で異常があれば都度対応

## env・secret contract

- 変更: なし
- 変数名・secret種類のみ: 既存の認証用環境変数を継続利用（値はログへ出力しない）
- provisioning/rotation: 変更なし

secret値は記載しない。

## Data・migration・backup

- schema/format変更: なし
- migration: なし
- backup対象: 変更なし
- restore確認: 本変更では不要
- backward compatibility: API・保存データ形式は変更しない

## Deploy・rollback

- deploy前提: VPS管理側reviewとproduction変更の明示承認
- deploy手順: legacy route。転送前にVPS側現物（`coop_api_server.py` / `fetch_coop_mail.py` / `coop_parser.py`）のsha256を取得・保全してから `deploy-coop-api.bat` 相当（scp転送 → ハッシュ一致確認 → `/opt/apps/deploy.sh coop-api`）を実行する（recipe-generator deployと同じ手順）
- rollback方法: 保全したVPS側現物をそのまま `/opt/apps/coop-api/` へ書き戻し、`sudo systemctl restart coop-api`
- rollback不能条件: なし（data migrationなし）

## Health・テスト

- health contract変更: なし
- 実施テスト: Python構文解析、parser単体実行、認証情報未設定経路のjobログ確認、実uvicorn起動・停止、主要endpoint応答、到達不能なローカル内部APIへの転送失敗、全実ログのJSONパース、静的なログ呼び出し検査
- 結果: task `20260830-005` で実サーバーを起動し、`startup` / `shutdown`、uvicorn lifecycleログのJSON化、アクセスログ抑止、httpx INFOログ抑止、query文字列の非出力、`dependency_failed`、停止後の8003解放を確認した。`/api/coop/ingredients` は実データなし時の404と、メモリ上の合成注文による200正常応答を確認した
- 未実施テストと理由: production接続・deploy、実Gmail接続、production内部API接続は未承認または禁止のため実施しない

## Log・監視

- log量/形式/保存先変更: stderrのみ、1行JSON、必須4フィールドと固定eventへ変更。実測では1回のサーバー起動・正常停止でuvicorn lifecycle 8行とアプリ `startup` / `shutdown` 2行の計10行。アクセスログは停止し、内部依存2件の失敗試験では `dependency_failed` が2行追加された
- 新しいalert条件: `job_start` に対応する `job_end` の欠落、`job_end.status=failure`、`external_call_failed`、`dependency_failed` を監視候補とする
- secret/個人情報対策: メールアドレス、件名、本文、例外メッセージ、外部レスポンス本文、query文字列付きURLをログへ出力しない

## 未解決事項

- 従来の `logs/fetch_coop.log` を参照する監視・runbookがあれば切替確認が必要（journald収集経路自体は2026-08-30のログ経路変更で解決済み）。
- 07:00/20:00 JSTの定期処理（`job_start`/`job_end`）を次回実行時にjournaldで確認する（deploy後まだ1サイクル経過していない）。
- 監視登録と稼働確認が済むまで受理台帳を `closed` にしない（[監視組み込みフロー](app_onboarding_flow.md) §2.1）。

## Deploy結果（2026-08-30）

- 手順: VPS側現物3ファイルのsha256保全 → scp転送（`deploy-files.txt`記載4ファイル） → ハッシュ一致確認 → `/opt/apps/deploy.sh coop-api` 実行。
- deploy中の `pip install -r requirements.txt` はVPS側venvの既存版と一致のためno-op。
- 検証: health `HTTP 200` / bind `127.0.0.1:8003` 維持 / runtime user `coop-api` 維持 / journaldで `startup` の1行JSON実出力を確認（`{"ts":"2026-08-30T22:06:20.157+09:00","app":"coop-api","level":"info","event":"startup"}`）。
- downtime: `systemctl restart` のみ、事前想定どおり数秒。
- rollback用の現物（deploy前sha256）はローカルscratchpadに保全済み。

## 希望時期

VPS管理側review後。

## Approval

- app owner: 未承認
- VPS management review: **accepted（2026-08-30）**。受入判定7項目（`application_change_notification_policy.md` §9）を照合し、`source_commit`未確定と「service/container変更なし」の2点を本notice更新で是正、他5項目は充足を確認した。**`accepted`はdeploy承認ではない。**
- production approval: **承認済み（2026-08-30、ユーザー）**。想定downtime（brief-restart数秒）・事前告知なしの方針を含めて承認された
- related task_id: 20260830-001（実装）、20260830-005（実起動検証）
