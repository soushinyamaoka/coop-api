COOP連携APIのデプロイ作業を実行してください。

> ## ⚠ VPS安全化後の現行 production contract（デプロイ前に必ず遵守）
> - **bind `127.0.0.1:8003` を維持**（`0.0.0.0`へ戻さない）。外部公開ポートは 22/80/443 のみ。
> - **runtime user/group = `coop-api`**。systemd runtime を deploy ユーザーへ戻さない。
> - **`.env` を deploy artifact に含めない・転送・上書きしない**（`root:coop-api 0640` / runtime read-only）。下記「.envもアップロード」前提の旧記述は**現行では不可**。
> - **scheduled worker は `/etc/cron.d/coop-api`（`coop-api`ユーザー・07:00/20:00 JST）**。deploy crontab へ旧 worker entry を復活させない。worker を確認目的で勝手に手動実行しない。
> - **`/opt/apps/deploy.sh` は legacy route**（新 canonical framework ではない）。未完成の Deploy v2 / helper draft を production に使わない。
> - production変更前に本 contract と既存 runbook/仕様書を確認する。

## デプロイの仕組み

デプロイバッチ: `C:\work\PRG\Sakura\deploy\recipe-api\deploy-coop-api.bat`
- 共通スクリプト `deploy.bat` を呼び出す
- `deploy-files.txt` に記載されたファイルをscpでVPSにアップロード（**`.env` は現行contractで転送対象外。上記参照**）
- VPS上の `/opt/apps/deploy.sh coop-api` でサービス再起動（**legacy route。現行 canonical 手順を確認**）
- SSH鍵: `%USERPROFILE%\.ssh\id_ed25519`、ユーザー: `deploy`
- デプロイ先: `/opt/apps/coop-api/`

## 手順

### 1. デプロイ前チェック
- `git status` で未コミットの変更がないか確認する
- デプロイ対象ファイル（deploy-files.txt記載）に `python -m py_compile` で構文エラーがないか確認する
- 直近のコミットで何が変わったかを `git log --oneline -5` と `git diff HEAD~1 --stat` で確認し、変更概要を日本語で表示する
- requirements.txt に変更がある場合はその旨を警告する（VPS上でpip installが別途必要になるため）

### 2. ユーザーに確認
変更内容の概要を表示した上で、デプロイを実行してよいか確認する。

### 3. デプロイ実行
ユーザーの許可を得たら以下を実行する:
```
C:\work\PRG\Sakura\deploy\recipe-api\deploy-coop-api.bat
```

### 4. デプロイ後の動作確認
バッチ完了後、.envからAPI_TOKENを読み取り、以下のcurlで動作確認する:
```bash
curl -s http://<VPS_IP>:8003/
curl -s -H "Authorization: Bearer <API_TOKEN>" http://<VPS_IP>:8003/api/coop/ingredients
```
※ VPS_IPとAPI_TOKENは .env および memory の reference_vps_services.md を参照すること。
レスポンスが正常に返ることを確認し、結果を報告する。

## 注意事項
- **`.env` は現行 production contract では deploy 対象外**（`root:coop-api 0640` / runtime read-only）。転送・上書き・deploy所有へ戻すことはしない。VPS上の `.env` は変更しない。
- requirements.txt に変更がある場合、バッチ実行後にVPS上で手動の `pip install -r requirements.txt` が必要になる可能性がある旨を伝えること
