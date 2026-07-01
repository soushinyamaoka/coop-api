#!/bin/bash
# ============================================================
# COOP連携API セットアップスクリプト
# さくらVPSでの初回セットアップ用
# ============================================================

set -e

APP_DIR="/home/ubuntu/coop_api"
PYTHON="/usr/bin/python3"
VENV_DIR="$APP_DIR/venv"

echo "=== COOP連携API セットアップ ==="

# 1. ディレクトリ作成
echo "[1/5] ディレクトリ作成..."
mkdir -p "$APP_DIR/data"
mkdir -p "$APP_DIR/logs"

# 2. Python仮想環境
echo "[2/5] Python仮想環境セットアップ..."
$PYTHON -m venv "$VENV_DIR"
source "$VENV_DIR/bin/activate"
pip install --upgrade pip
pip install -r "$APP_DIR/requirements.txt"

# 3. .envファイル確認
echo "[3/5] .envファイル確認..."
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo "⚠️  .envファイルを作成しました。Gmail認証情報を設定してください："
    echo "    nano $APP_DIR/.env"
fi

# 4. systemdサービス登録
echo "[4/5] systemdサービス登録..."
sudo cp "$APP_DIR/coop-api.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable coop-api
sudo systemctl start coop-api
echo "✅ APIサーバー起動: http://localhost:8003"

# 5. cron設定
echo "[5/5] cron設定..."
CRON_CMD="0 7,20 * * * $VENV_DIR/bin/python $APP_DIR/fetch_coop_mail.py >> $APP_DIR/logs/cron.log 2>&1"

# 既存のcronに追加（重複チェック）
(crontab -l 2>/dev/null | grep -v "fetch_coop_mail"; echo "$CRON_CMD") | crontab -
echo "✅ cron設定完了: 毎日7時と20時にメール取得"

echo ""
echo "=== セットアップ完了 ==="
echo "次のステップ:"
echo "  1. .envにGmail認証情報を設定: nano $APP_DIR/.env"
echo "  2. 手動テスト: curl http://localhost:8003/"
echo "  3. メール取得テスト: $VENV_DIR/bin/python $APP_DIR/fetch_coop_mail.py"
