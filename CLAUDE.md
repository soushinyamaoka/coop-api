# COOP連携API

## プロジェクト概要

COOPデリの注文確認メールをGmail IMAP経由で自動取得し、食材を抽出・分類してレシピ提案を行うサーバーサイドAPI。
「今晩なに食べる？」アプリ（Expo/React Native）のバックエンドとして動作する。

主な機能:
- 注文確認メールの自動取得・パース
- 食材の自動分類（食材/調理キット/そのまま/離乳食/調味料・日用品）
- 既存レシピAPIへの転送によるレシピ提案
- 1週間分の献立作成（食材消費管理付き）
- ユーザーによるカテゴリ学習

## ディレクトリ構成と各ファイルの役割

```
coop-api/
├── coop_api_server.py      # FastAPIサーバー本体（全エンドポイント定義）
├── coop_parser.py          # メール本文パーサー（カテゴリ分類・食材名正規化）
├── fetch_coop_mail.py      # Gmail IMAP経由のメール取得・JSON保存
├── coopApi.js              # アプリ側クライアント（React Native/Expo用）
├── requirements.txt        # Pythonパッケージ依存
├── .env                    # 環境変数（Git管理外）
├── .env.example            # .envのテンプレート
├── coop-api.service        # systemdサービス定義
├── setup.sh                # VPS初回セットアップスクリプト
├── deploy-files.txt        # デプロイ対象ファイル一覧
├── test.py                 # Gmail IMAP接続の簡易テスト
├── debug_search.py         # メール検索条件のデバッグ用（9パターン）
├── README.md               # プロジェクト説明
├── coop_api_deploy_guide.md # WinSCP+TeraTerm用デプロイ手順書
├── data/                   # パース結果JSON（VPS上で自動生成）
│   ├── coop_latest.json    #   最新注文データ
│   ├── coop_orders.json    #   全注文履歴
│   └── category_overrides.json  # カテゴリ学習データ
└── logs/                   # ログファイル（VPS上で自動生成）
    └── fetch_coop.log
```

### 主要ファイルの役割

| ファイル | 役割 |
|---------|------|
| `coop_api_server.py` | 全APIエンドポイント、献立作成ロジック、食材在庫管理、ready_meal判定 |
| `coop_parser.py` | メール本文の正規表現パース、全角→半角変換、カテゴリ分類キーワード辞書、食材名正規化 |
| `fetch_coop_mail.py` | Gmail IMAP接続、メール検索(`X-GM-RAW`)、マルチパートメール処理、JSON保存 |
| `coopApi.js` | アプリ側のAPI通信クライアント（未組み込み、配置先は `src/utils/coopApi.js`） |

## 使用技術・ライブラリ

| パッケージ | バージョン | 用途 |
|-----------|-----------|------|
| FastAPI | 0.115.0 | Webフレームワーク |
| Uvicorn | 0.30.0 | ASGIサーバー |
| httpx | 0.27.0 | 非同期HTTP通信（8001/8002への内部転送） |
| python-dotenv | 1.0.1 | 環境変数管理 |

- Python 3.10以上推奨（`list[str]`、`dict[str, dict]`、`X | None` 型ヒント使用）
- 標準ライブラリ: `imaplib`, `email`, `re`, `json`, `asyncio`, `logging`

## サーバー構成・ポート番号

> ## ⚠ VPS安全化後の現行仕様（この文書には旧構成の記述が残っています）
>
> 現行 production contract は以下。以下と異なる旧記述（`0.0.0.0` bind / `ubuntu`ユーザー / `/home/ubuntu` パス / 外部ポート直叩き / `/opt/apps/deploy.sh`）は**VPS安全化前**のもので、現行と異なる場合があります。**利用前に現行構成を確認してください。**
> - **bind**: `127.0.0.1:8003`（`0.0.0.0`で公開しない）。外部公開ポートは **22/80/443 のみ**。外部からは HTTPS入口 `https://coop.homehub-tools.dedyn.io` 経由でアクセスし、`:8003` へは直接到達不可。
> - **runtime user/group**: `coop-api`（systemd runtime を deploy ユーザーへ戻さない）
> - **`.env`**: `root:coop-api 0640`。**deploy artifact に含めない・転送/上書きしない**。runtime は read-only で参照。
> - **scheduled worker**: `/etc/cron.d/coop-api`（`coop-api`ユーザー・07:00/20:00 JST）。旧 deploy crontab へ戻さない。
> - **`/opt/apps/deploy.sh`** は legacy route（新 canonical framework ではない）。
> - 配置先パス・デプロイ手順は VPS安全化で変わり得るため、旧 `/home/ubuntu/...` 記述は現行構成で要確認。

### VPS環境
- さくらVPS（大阪）: `<VPS_IP>`（実IPは.envまたはデプロイスクリプトを参照）
- OS: Ubuntu 22.04 LTS
- runtime user/group: `coop-api`（サービスはこのユーザーで稼働。SSH/deployユーザーとは別）

### ポート構成

| ポート | サービス | パス | 説明 |
|--------|---------|------|------|
| 8001 | AIレシピ生成API | `/home/ubuntu/recipe_api_app.py` | Claude/GPTによるレシピ生成 |
| 8002 | Webレシピ検索API | `/home/ubuntu/recipe-search-api/` | Webスクレイピングによるレシピ検索 |
| 8003 | **COOP連携API** | `/home/ubuntu/coop_api/` | 本プロジェクト |

### APIエンドポイント

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/` | サービス情報 |
| GET | `/api/coop/ingredients` | 最新食材リスト（カテゴリ分類済み） |
| GET | `/api/coop/orders` | 全注文サマリー |
| POST | `/api/coop/fetch?days_back=14` | メール手動取得 |
| POST | `/api/coop/suggest-recipes` | レシピ提案（8001/8002に転送） |
| POST | `/api/coop/meal-plan` | 献立作成（食材消費管理付き、最大7日） |
| PUT | `/api/coop/classify` | カテゴリ手動修正（学習） |

### 認証
- Bearer Token方式: `Authorization: Bearer <API_TOKEN>`
- 全エンドポイントで必要（`/` を除く）

### 外部接続
- Gmail IMAP: `imap.gmail.com:993` (SSL)
- 検索クエリ: `X-GM-RAW "coopdeli newer_than:{days_back}d"`

## ビルド・デプロイ手順

### ローカル開発
```bash
# 仮想環境の作成・有効化
python -m venv venv
source venv/bin/activate      # Linux/Mac
venv\Scripts\activate         # Windows

# パッケージインストール
pip install -r requirements.txt

# サーバー起動
python coop_api_server.py
```

### VPSへのデプロイ

> **現行のデプロイ経路（2026-09-27確認）**: 以下の手動WinSCP手順は旧構成のものです。現行は
> `C:\work\PRG\Sakura\Dev\deploy\recipe-api\deploy-coop-api.bat` → 共通 `deploy.bat coop-api <local_dir>`
> を実行する自動経路で、次を行います。
> 1. `deploy-files.txt` 記載のファイルを `/opt/apps/coop-api/` へscp転送
> 2. sshでリモートの `/opt/apps/deploy.sh coop-api` を実行（VPS管理側の共通script。coop-apiは
>    `USE_VENV=yes` 経路のため、この中で `pip install -q -r requirements.txt` を実行してから
>    service再起動まで行う。手動でのpip install・systemctl再起動は不要）
>
> `.env` と `data/` はこの経路で転送されない（`deploy-files.txt` のコメント参照）。

デプロイ対象は `deploy-files.txt` に記載の5ファイル（`.env` は配布対象外で、転送・上書きしない）:
```
coop_parser.py
jev_classifier.py
fetch_coop_mail.py
coop_api_server.py
requirements.txt
```

以下は上記スクリプトが到達できない場合の手動代替手順（旧`/home/ubuntu`パス表記のまま、現行の
配置先パス・venv構成は要確認）:
```bash
# 1. WinSCP等でファイルを /home/ubuntu/coop_api/ にアップロード

# 2. SSH接続後、パッケージ更新（変更がある場合）
cd /home/ubuntu/coop_api
source venv/bin/activate
pip install -r requirements.txt

# 3. サービス再起動
sudo systemctl restart coop-api

# 4. 動作確認
sudo systemctl status coop-api
curl -H "Authorization: Bearer $API_TOKEN" http://localhost:8003/api/coop/ingredients
```

### 初回セットアップ
```bash
# VPS上で実行
cd /home/ubuntu/coop_api
chmod +x setup.sh
./setup.sh
```
setup.sh が以下を自動実行:
- ディレクトリ作成（data/, logs/）
- Python仮想環境セットアップ
- systemdサービス登録・起動

> **scheduled worker（メール自動取得）について**
> VPS安全化後は、メール取得ワーカー `fetch_coop_mail.py` を **`/etc/cron.d/coop-api`**（実行ユーザー **`coop-api`**）で毎日 **07:00 / 20:00 JST** に実行する。旧 `ubuntu` / deployユーザーの crontab entry からは移行済みで、deployユーザーの crontab へ旧 entry を復活させないこと。`.env`（`root:coop-api 0640`）は worker が read-only で参照する。（※ 07:00 / 20:00 の自然実行の最終確認は現時点では未完了）

## 開発時の注意事項

### .envファイル
`.env.example` をコピーして `.env` を作成する。**`.env` はGit管理外**。
```
GMAIL_ADDRESS=<Gmailアドレス>
GMAIL_APP_PASSWORD=<Googleアプリパスワード（16文字）>
COOP_SENDER=coopdeli
API_HOST=127.0.0.1
API_PORT=8003
API_TOKEN=<任意のトークン>
```

### カテゴリ分類ロジック
- `coop_parser.py` の `classify_item()` がカテゴリ分類の本体
- 判定優先順位: 離乳食 → 食材セット → 調理キット → そのまま → 調味料・日用品 → 食材
- 「セット」は汎用的すぎるため `KIT_KEYWORDS` から除外済み。食材名を含む「セット」は食材として分類される
- `coop_api_server.py` の `_READY_MEAL_KW` も同様の除外を適用

### 食材在庫管理（献立作成）
- `coop_api_server.py` の `_STOCK_TABLE` で食材ごとの初期在庫(g)と1食使用量(g)を定義
- `_detect_quantity_ratio()` で「1/2切」「半玉」等の分量表記を検出し、初期在庫に倍率を適用
- `_is_ready_meal()` で調理キット・冷凍食品等を判定し、レシピ不要の献立として扱う

### メール検索
- IMAPのASCIIエンコード制限により日本語検索は不可
- `X-GM-RAW "coopdeli"` で検索（転送メールでも本文にcoopdeliが含まれるためヒット）
- 最新1通のみ処理（広告メール等が最新の場合は取得失敗する既知の制限あり）

## よく使うコマンド

### ローカル開発
```bash
# サーバー起動
python coop_api_server.py

# パーサーの単体テスト（サンプルメールで動作確認）
python coop_parser.py

# Gmail IMAP接続テスト
python test.py

# メール検索条件デバッグ（9パターンの検索条件を比較）
python debug_search.py

# メール手動取得
python fetch_coop_mail.py
```

### VPS管理
```bash
# サービス状態確認
sudo systemctl status coop-api

# サービス再起動
sudo systemctl restart coop-api

# ログ確認
sudo journalctl -u coop-api -f
cat /var/log/apps/coop-api.log

# cron設定確認（実行ユーザー coop-api / 07:00・20:00 JST）
cat /etc/cron.d/coop-api
```

### API動作確認

> ※ VPS安全化後、`:8003` は外部非公開（`127.0.0.1` bind）。外部からの確認は HTTPS入口 `https://coop.homehub-tools.dedyn.io` を使う。以下の `http://$VPS_IP:8003` 例は **VPS内 localhost** での確認に読み替えること。

```bash
# .envからトークンを読み込む（bash用）
export $(grep API_TOKEN .env | xargs)
export VPS_IP=<VPS_IPを設定>

# 接続テスト
curl http://$VPS_IP:8003/

# 食材リスト取得
curl -H "Authorization: Bearer $API_TOKEN" http://$VPS_IP:8003/api/coop/ingredients

# メール手動取得
curl -X POST -H "Authorization: Bearer $API_TOKEN" "http://$VPS_IP:8003/api/coop/fetch?days_back=14"

# レシピ提案
curl -X POST -H "Authorization: Bearer $API_TOKEN" -H "Content-Type: application/json" \
  -d '{"ingredients":["鶏もも肉","キャベツ"],"servings":2,"mode":"both"}' \
  http://$VPS_IP:8003/api/coop/suggest-recipes

# 献立作成
curl -X POST -H "Authorization: Bearer $API_TOKEN" -H "Content-Type: application/json" \
  -d '{"ingredients":["鶏もも肉","キャベツ","にんじん"],"servings":2}' \
  http://$VPS_IP:8003/api/coop/meal-plan
```

## コミットルール

- コミットは「コミットして」と指示があった時のみ行う
- コミットの前に必ず変更内容に以下の機密情報が含まれていないか確認する
  - APIキー・トークン
  - パスワード
  - 個人情報（メールアドレス・電話番号等）
  - サーバーのIPアドレス・接続情報
  - .envファイルの内容
- 機密情報が含まれる場合は作業を中断してその旨を報告する
- 機密情報が含まれない場合のみコミットを実行する
- コミットメッセージは以下の形式で日本語で書く
  - バグ修正: `fix: 内容`
  - 新機能: `feat: 内容`
  - リファクタリング: `refactor: 内容`
  - その他: `chore: 内容`
- **push は明示指示があるときだけ行う。** 「pushして」と明示されていない場合は commit までで止め、push していないことを報告する。
- force push、履歴改変、既存変更の revert は行わない。
