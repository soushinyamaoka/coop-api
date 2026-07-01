# COOP連携API — 注文メールからレシピを自動提案

COOPデリの注文確認メールをGmail（IMAP）経由で自動取得し、
食材を抽出・分類して「今晩なに食べる？」アプリでレシピを提案する仕組み。

## 全体構成

```
[Gmail]                         [さくらVPS]                    [アプリ]
  │                               │                              │
  │  IMAP(993)                    │  ポート8003                   │
  │◄──────────────────── fetch_coop_mail.py                      │
  │                          (cron: 7時/20時)                    │
  │                               │                              │
  │                               ▼                              │
  │                        data/coop_latest.json                 │
  │                               │                              │
  │                        coop_api_server.py ◄──── GET /api/coop/ingredients
  │                               │                              │
  │                               │   POST /api/coop/suggest-recipes
  │                               │──────► 8001(AI生成)          │
  │                               │──────► 8002(Web検索)         │
  │                               │                              │
  │                               │◄──────────────────────────── │
  │                               │   レシピ結果を返す            │
```

## ファイル構成

### サーバー側（VPSにデプロイ）
| ファイル | 説明 |
|---------|------|
| `coop_parser.py` | メール本文パーサー（全角変換・カテゴリ分類・食材名正規化） |
| `fetch_coop_mail.py` | IMAP経由でGmailからメール取得 → パース → JSON保存 |
| `coop_api_server.py` | FastAPIサーバー（ポート8003）— 5つのエンドポイント |
| `requirements.txt` | Python依存パッケージ |
| `.env.example` | 環境変数のテンプレート |
| `setup.sh` | VPS初回セットアップスクリプト |
| `coop-api.service` | systemdサービス定義 |

### アプリ側（Expoプロジェクトに組み込み）
| ファイル | 配置先 |
|---------|--------|
| `app_components/coopApi.js` | `src/utils/coopApi.js` |
| `app_components/CoopIngredientsScreen.js` | `src/screens/CoopIngredientsScreen.js` |

## セットアップ手順

### 1. Gmailアプリパスワード発行
1. https://myaccount.google.com → セキュリティ → 2段階認証を有効化
2. アプリパスワードを生成（名前: 「COOP連携」）
3. 表示された16文字のパスワードをメモ

### 2. VPSにデプロイ
```bash
# ファイルをVPSに転送（WinSCPまたはscp）
scp -r coop_api/ ubuntu@<VPS_IP>:/home/ubuntu/coop_api/

# VPSにSSH接続
ssh ubuntu@<VPS_IP>

# .envを編集
cd /home/ubuntu/coop_api
cp .env.example .env
nano .env  # Gmail認証情報とAPIトークンを設定

# セットアップ実行
chmod +x setup.sh
./setup.sh

# 動作確認
curl http://localhost:8003/
```

### 3. メール取得テスト
```bash
# 手動でメール取得
cd /home/ubuntu/coop_api
source venv/bin/activate
python fetch_coop_mail.py

# 結果確認
cat data/coop_latest.json | python -m json.tool
```

### 4. アプリに組み込み
```bash
# ファイルをコピー
cp app_components/coopApi.js <expo-project>/src/utils/
cp app_components/CoopIngredientsScreen.js <expo-project>/src/screens/

# coopApi.js の COOP_API_BASE をVPSのIPに変更
```

ナビゲーションに画面を追加（例: React Navigation）:
```jsx
import CoopIngredientsScreen from '../screens/CoopIngredientsScreen';

// Stack.Navigator 内に追加
<Stack.Screen
  name="CoopIngredients"
  component={CoopIngredientsScreen}
  options={{ title: 'COOPから探す' }}
/>
```

ホーム画面にボタンを追加:
```jsx
<TouchableOpacity onPress={() => navigation.navigate('CoopIngredients')}>
  <Text>🛒 COOPの注文から探す</Text>
</TouchableOpacity>
```

## APIエンドポイント一覧

| メソッド | パス | 説明 |
|---------|------|------|
| GET | `/api/coop/ingredients` | 最新の食材リスト（カテゴリ分類済み） |
| GET | `/api/coop/orders` | 全注文サマリー一覧 |
| POST | `/api/coop/fetch?days_back=14` | メール手動取得 |
| POST | `/api/coop/suggest-recipes` | 選択食材からレシピ提案 |
| PUT | `/api/coop/classify` | 商品カテゴリの手動修正（学習） |

## カテゴリ自動分類の仕組み

1. **キーワード辞書方式**（Phase 1 — 実装済み）
   - 「キット」「セット」→ 調理キット
   - 「バナナ」「ヨーグルト」「牛乳」→ そのまま
   - 「ベビー」「うらごし」「離乳」→ 離乳食
   - それ以外 → 食材

2. **ユーザー学習**（Phase 2 — 実装済み）
   - アプリで長押し → カテゴリ変更
   - `PUT /api/coop/classify` で学習データ保存
   - 次回から自動適用
