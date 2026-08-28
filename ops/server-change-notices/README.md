# サーバ変更通知（server-change-notices）

VPS の構成・運用・利用者に影響する変更を行ったときに、通知をここへ作成する。

## ファイル名

`YYYYMMDD-APP-NNN-summary.md`

- `YYYYMMDD` … 作成日
- `APP` … `COOPAPI`（このリポジトリ固定）
- `NNN` … このディレクトリ内の3桁連番（`001` から）

例: `20260828-COOPAPI-001-summary.md`

## 運用

- `server_impact` が `notify` / `approval_required` のときに作成する。影響が不明なときは `none` にせず `notify` とする。
- **通知の作成は production 変更の承認ではない。** 通知を書いてもデプロイはしない。
- 雛形と通知ポリシーの正本は VPS管理プロジェクト側にある。所在は `work/ai_handoff/AI_INSTRUCTIONS.md` を参照する（別プロジェクトなので読むだけで編集しない）。

## このAPI固有の通知対象

CLAUDE.md の「VPS安全化後の現行仕様」に触れる変更は必ず通知する。

- bind（`127.0.0.1:8003`）・公開ポート・HTTPS入口の変更
- runtime user/group（`coop-api`）と `.env` の権限（`root:coop-api 0640`）
- scheduled worker（`/etc/cron.d/coop-api`、`coop-api` ユーザー、07:00 / 20:00 JST）
- `/api/coop/*` の API 契約・認証方式（Bearer Token）
- 8001 / 8002 への転送先の変更
