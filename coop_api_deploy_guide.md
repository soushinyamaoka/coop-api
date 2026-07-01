# COOP連携API デプロイ作業手順書
# （WinSCP + TeraTerm 操作用）

---

## 環境情報

| 項目 | 値 |
|------|-----|
| VPS IP | `<VPS_IP>` |
| APIポート | `8003` |
| APIトークン | `.env` の `API_TOKEN` を参照 |
| APIベースURL | `http://<VPS_IP>:8003` |

---

## 事前準備

### 用意するもの
- [ ] 先にダウンロードした `coop_api` フォルダ（7ファイル入り）
- [ ] WinSCP（ファイル転送用）
- [ ] TeraTerm（コマンド実行用）
- [ ] Gmailアプリパスワード（まだの場合は手順0で発行）

### ファイル一覧（coop_apiフォルダの中身）
```
coop_parser.py          ... メールパーサー
fetch_coop_mail.py      ... メール取得スクリプト
coop_api_server.py      ... APIサーバー本体
requirements.txt        ... Pythonパッケージ一覧
.env                    ... 環境変数（Gmail情報のみVPS上で設定）
.env.example            ... 環境変数テンプレート（参考用）
coop-api.service        ... systemdサービス定義
```

---

## 手順0: Gmailアプリパスワードの発行（まだの場合）

1. ブラウザで https://myaccount.google.com を開いてログイン
2. 左メニューから「セキュリティ」をクリック
3. 「2段階認証プロセス」が **有効** になっていることを確認
   - 無効の場合は先に有効化する
4. 上部の検索バーで「アプリパスワード」と検索してページを開く
5. アプリ名に `COOP連携` と入力して「作成」をクリック
6. 表示された **16文字のパスワード**（`abcd efgh ijkl mnop` の形式）をメモ帳にコピー
   - ※ このパスワードは一度しか表示されないので必ず控える

---

## 手順1: WinSCPでファイルをアップロード

1. WinSCPを起動して `<VPS_IP>` に接続
2. **右側（VPS側）** で `/home/ubuntu/` を開く
3. 右側の空白部分を右クリック →「新規」→「ディレクトリ」→ `coop_api` と入力してフォルダ作成
4. 作成した `coop_api` フォルダをダブルクリックして中に入る
5. **左側（PC側）** でダウンロードした `coop_api` フォルダを開く
6. 左側の7ファイルをすべて選択して、右側にドラッグ＆ドロップ

アップロード後、右側が以下の状態になっていればOK:
```
/home/ubuntu/coop_api/
  ├── coop_parser.py
  ├── fetch_coop_mail.py
  ├── coop_api_server.py
  ├── requirements.txt
  ├── .env
  ├── .env.example
  └── coop-api.service
```

> **注意**: `.env` や `.env.example` が見えない場合、WinSCPのメニュー →
> 「オプション」→「環境設定」→「パネル」→「隠しファイルを表示する」にチェック

---

## 手順2: TeraTermで環境構築

TeraTerm を起動して `<VPS_IP>` にSSH接続し、以下のコマンドを **1行ずつ** 実行してください。

### 2-1. ディレクトリ移動 & 確認

```bashls
cd /home/ubuntu/coop_api
ls -la
```

↑ 7ファイルが表示されればアップロード成功

### 2-2. .envファイルのGmail情報を設定

`.env` は他の値が設定済みの状態でアップロードされています。
GmailアドレスとアプリパスワードだけVPS上で設定します。

```bash
nano .env
```

nanoエディタが開くので、上2行を書き換え:

```
GMAIL_ADDRESS=（自分のGmailアドレス）
GMAIL_APP_PASSWORD=（手順0で取得した16文字のパスワード）
```

それ以外の行はそのままでOK。書き換えたら:
- `Ctrl + O` → Enter（保存）
- `Ctrl + X`（終了）

### 2-3. Python仮想環境の作成

```bash
python3 -m venv venv
source venv/bin/activate
```

プロンプトの先頭に `(venv)` が付けばOK:
```
(venv) ubuntu@server:~/coop_api$
```

### 2-4. パッケージインストール

```bash
pip install -r requirements.txt
```

エラーなく完了すればOK。最後に `Successfully installed ...` と表示される。

---

## 手順3: 動作テスト（TeraTerm）

### 3-1. パーサー単体テスト

```bash
python coop_parser.py
```

**期待される出力（抜粋）:**
```
COOP注文メール パース結果
注文商品数: 12件
除外商品数: 1件

🥩 食材（レシピに使える）:
  ✅ 牛バラ肉（元: ...）× 1
  ✅ ささがきごぼう（元: ...）× 1
  ✅ ミニトマト（元: ...）× 1
  ...

❌ 除外された商品:
  ⛔ 皮付きポテト...（数量0点（未注文））
```

→ この出力が出ればパーサーは正常動作。次へ進む。

### 3-2. メール取得テスト

```bash
python fetch_coop_mail.py
```

**成功した場合の出力:**
```
COOPメール取得開始
Gmailに接続中... (xxxxx@gmail.com)
ログイン成功
検索条件: 件名に「eフレンズ注文済メモメール」を含む過去14日のメール
X件のメールが見つかりました（最新1通のみ処理）
パース完了: 食材X件, キットX件, 除外X件
最新データを保存: ...data/coop_latest.json
```

**エラーが出た場合:**
- `IMAP認証エラー` → .envのGMAIL_ADDRESSまたはGMAIL_APP_PASSWORDが間違っている
- `接続エラー` → VPSからGmailへの通信がブロックされている可能性
- `COOPからのメールが見つかりませんでした` → 過去14日以内に「eフレンズ注文済メモメール」が届いているか確認。日数を広げて試す場合は手順3-3のAPIサーバー起動後に以下を実行:
```bash
curl -X POST -H "Authorization: Bearer $API_TOKEN" "http://localhost:8003/api/coop/fetch?days_back=60"
```

保存されたデータの確認:
```bash
cat data/coop_latest.json | python -m json.tool | head -30
```

### 3-3. APIサーバー起動テスト

```bash
python coop_api_server.py
```

`Uvicorn running on http://0.0.0.0:8003` と表示されたら起動成功。

**TeraTermをもう1つ開いて** `<VPS_IP>` に接続し、以下で確認:

```bash
curl http://localhost:8003/
```

↓ こんなJSONが返ればOK:
```json
{"service":"COOP連携API","version":"1.0.0","endpoints":[...]}
```

食材リストも確認:
```bash
curl -H "Authorization: Bearer $API_TOKEN" http://localhost:8003/api/coop/ingredients
```

確認できたら、最初のTeraTermに戻って `Ctrl + C` でサーバーを停止。

---

## 手順4: systemdサービス登録（TeraTerm）

### 4-1. サービスファイルをコピー

```bash
sudo cp /home/ubuntu/coop_api/coop-api.service /etc/systemd/system/
```

### 4-2. サービスの有効化 & 起動

```bash
sudo systemctl daemon-reload
sudo systemctl enable coop-api
sudo systemctl start coop-api
```

### 4-3. 起動確認

```bash
sudo systemctl status coop-api
```

↓ `Active: active (running)` と表示されていればOK:
```
● coop-api.service - COOP連携API Server
     Active: active (running) since ...
```

外部からも確認（PCのブラウザで）:
```
http://<VPS_IP>:8003/
```

---

## 手順5: cron登録（TeraTerm）

毎日7時と20時に自動でメール取得するよう設定します。

### 5-1. crontab編集

```bash
crontab -e
```

エディタが開くので、**一番下に** 以下の1行を追加:

```
0 7,20 * * * /home/ubuntu/coop_api/venv/bin/python /home/ubuntu/coop_api/fetch_coop_mail.py >> /home/ubuntu/coop_api/logs/cron.log 2>&1
```

保存して終了（nanoの場合: `Ctrl + O` → Enter → `Ctrl + X`）

### 5-2. 登録確認

```bash
crontab -l
```

追加した行が表示されればOK。

### 5-3. ログ用ディレクトリ確認

```bash
mkdir -p /home/ubuntu/coop_api/logs
```

---

## 手順6: 最終確認チェックリスト

PCのブラウザまたはTeraTermのcurlで以下を確認:

- [ ] `http://<VPS_IP>:8003/` → サービス情報が返る
- [ ] `curl -H "Authorization: Bearer $API_TOKEN" http://<VPS_IP>:8003/api/coop/ingredients` → 食材リストが返る
- [ ] `sudo systemctl status coop-api` → active (running)
- [ ] `crontab -l` → cronジョブが登録されている
- [ ] `ls /home/ubuntu/coop_api/data/` → JSONファイルが存在する

---

## よく使うコマンド集

### メール手動取得（curl）
```bash
curl -X POST -H "Authorization: Bearer $API_TOKEN" http://localhost:8003/api/coop/fetch
```

### 過去の日数を広げてメール取得
```bash
curl -X POST -H "Authorization: Bearer $API_TOKEN" "http://localhost:8003/api/coop/fetch?days_back=60"
```

### 最新の食材リスト取得
```bash
curl -H "Authorization: Bearer $API_TOKEN" http://localhost:8003/api/coop/ingredients
```

### サービスの再起動
```bash
sudo systemctl restart coop-api
```

### サービスのログ確認
```bash
sudo journalctl -u coop-api -n 50
```

---

## 既存APIとの接続確認

`coop_api_server.py` はレシピ提案時に既存APIに内部転送します。
以下のURLが正しいか、既存のサーバーに合わせて確認してください。

| 用途 | .envの変数名 | デフォルト値 |
|------|------------|------------|
| AIレシピ生成 | RECIPE_GENERATE_URL | `http://localhost:8001/api/recipes/generate` |
| Webレシピ検索 | RECIPE_SEARCH_URL | `http://localhost:8002/api/recipes/search` |

もしパスが異なる場合は `.env` に以下を追加:
```
RECIPE_GENERATE_URL=http://localhost:8001/（正しいパス）
RECIPE_SEARCH_URL=http://localhost:8002/（正しいパス）
```

変更後はサービス再起動:
```bash
sudo systemctl restart coop-api
```

---

## トラブルシューティング

### APIサーバーが起動しない
```bash
sudo journalctl -u coop-api -n 50
```

### ポート8003が使えない
```bash
sudo lsof -i :8003
```

### メール取得がcronで動かない
```bash
# cronのログを確認
cat /home/ubuntu/coop_api/logs/cron.log

# 手動で同じコマンドを実行してエラーを確認
/home/ubuntu/coop_api/venv/bin/python /home/ubuntu/coop_api/fetch_coop_mail.py
```

### Gmailに接続できない
```bash
openssl s_client -connect imap.gmail.com:993 -quiet
```
→ 接続できれば証明書情報が表示される。タイムアウトする場合はファイアウォール設定を確認。
