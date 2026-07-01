COOP連携APIのデプロイ作業を実行してください。

## デプロイの仕組み

デプロイバッチ: `C:\work\PRG\Sakura\deploy\recipe-api\deploy-coop-api.bat`
- 共通スクリプト `deploy.bat` を呼び出す
- `deploy-files.txt` に記載されたファイルをscpでVPSにアップロード
- VPS上の `/opt/apps/deploy.sh coop-api` でサービス再起動
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
- .env はデプロイ対象に含まれているが、VPS上で直接編集されている可能性がある。.envに変更がある場合はユーザーに上書きしてよいか確認すること
- requirements.txt に変更がある場合、バッチ実行後にVPS上で手動の `pip install -r requirements.txt` が必要になる可能性がある旨を伝えること
