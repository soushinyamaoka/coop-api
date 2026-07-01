"""
COOP注文メール取得スクリプト（IMAP + Gmailアプリパスワード）
- Gmailに接続してCOOPデリからのメールを取得
- パースして食材リストに変換
- JSONファイルに保存
- cronで定期実行を想定

使い方:
  1. .envファイルにGmail認証情報を設定
  2. python3 fetch_coop_mail.py
  3. data/ ディレクトリにJSONが出力される
"""

import imaplib
import email
from email.header import decode_header
import os
import json
import logging
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from coop_parser import parse_coop_email

# ============================================================
# 設定
# ============================================================

# .envファイルから環境変数を読み込み
load_dotenv()

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

# データ保存先
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# ログ設定
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / "fetch_coop.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger(__name__)


# ============================================================
# メールヘッダのデコード
# ============================================================

def decode_mime_header(header_value: str) -> str:
    """MIMEエンコードされたヘッダをデコードする"""
    if header_value is None:
        return ""
    decoded_parts = decode_header(header_value)
    result = []
    for part, charset in decoded_parts:
        if isinstance(part, bytes):
            result.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            result.append(part)
    return "".join(result)


# ============================================================
# メール本文の取得
# ============================================================

def get_email_body(msg: email.message.Message) -> str:
    """メールオブジェクトから本文（テキスト）を取得する"""
    body = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            content_disposition = str(part.get("Content-Disposition", ""))

            # 添付ファイルはスキップ
            if "attachment" in content_disposition:
                continue

            if content_type == "text/plain":
                charset = part.get_content_charset() or "utf-8"
                payload = part.get_payload(decode=True)
                if payload:
                    body = payload.decode(charset, errors="replace")
                    break  # text/plain が見つかったら終了
            elif content_type == "text/html" and not body:
                # text/plainがない場合のフォールバック
                charset = part.get_content_charset() or "utf-8"
                payload = part.get_payload(decode=True)
                if payload:
                    # HTMLタグを簡易除去
                    import re
                    html = payload.decode(charset, errors="replace")
                    body = re.sub(r'<[^>]+>', '', html)
    else:
        charset = msg.get_content_charset() or "utf-8"
        payload = msg.get_payload(decode=True)
        if payload:
            body = payload.decode(charset, errors="replace")

    return body


# ============================================================
# IMAP接続 & メール取得
# ============================================================

def fetch_coop_emails(days_back: int = 14) -> list[dict]:
    """
    GmailからCOOPデリのメールを取得してパースする
    
    Args:
        days_back: 何日前までのメールを取得するか（デフォルト14日）
    
    Returns:
        パース済みの注文データリスト
    """
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        logger.error("GMAIL_ADDRESS と GMAIL_APP_PASSWORD を .env に設定してください")
        return []

    results = []

    try:
        # Gmail IMAP に接続
        logger.info(f"Gmailに接続中... ({GMAIL_ADDRESS})")
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        logger.info("ログイン成功")

        # 受信トレイを選択（読み取り専用）
        mail.select("INBOX", readonly=True)

        # 検索条件を構築
        # Gmail固有のX-GM-RAW拡張で「coopdeli」を含むメールを検索
        # ※ 日本語はIMAPのASCIIエンコード制限で使えないため英数字で検索
        # ※ 転送メールでも本文にcoopdeliの情報が残るためヒットする
        search_criteria = f'(X-GM-RAW "coopdeli newer_than:{days_back}d")'
        logger.info(f"検索条件: 「coopdeli」を含む過去{days_back}日のメール")

        status, messages = mail.search(None, search_criteria)

        if status != "OK" or not messages[0]:
            logger.info("COOPからのメールが見つかりませんでした")
            mail.logout()
            return []

        mail_ids = messages[0].split()
        logger.info(f"{len(mail_ids)}件のメールが見つかりました（新しい順に最大5通を確認）")

        # 新しい順に最大5通を確認（最新が広告メール等だった場合に次を試す）
        for mail_id in reversed(mail_ids[-5:]):
            try:
                status, msg_data = mail.fetch(mail_id, "(RFC822)")
                if status != "OK":
                    continue

                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)

                # メールの日付を取得
                date_str = msg.get("Date", "")
                subject = decode_mime_header(msg.get("Subject", ""))
                sender = decode_mime_header(msg.get("From", ""))

                logger.info(f"処理中: {subject} ({date_str})")

                # 本文を取得
                body = get_email_body(msg)

                if not body:
                    logger.warning(f"本文が空です: {subject}")
                    continue

                # 注文確認メールかどうかをチェック
                if "注文番号" not in body and "商品名" not in body:
                    logger.info(f"注文確認メールではありません: {subject}")
                    continue

                # パース
                parsed = parse_coop_email(body)
                parsed["email_subject"] = subject
                parsed["email_date"] = date_str
                parsed["email_sender"] = sender

                # 注文日を推定（メール日付を使用）
                try:
                    from email.utils import parsedate_to_datetime
                    email_dt = parsedate_to_datetime(date_str)
                    parsed["order_date"] = email_dt.strftime("%Y-%m-%d")
                except Exception:
                    parsed["order_date"] = datetime.now().strftime("%Y-%m-%d")

                results.append(parsed)
                logger.info(
                    f"パース完了: 食材{len(parsed['ingredients'])}件, "
                    f"キット{len(parsed['kits'])}件, "
                    f"除外{parsed['excluded_count']}件"
                )
                break  # 注文確認メールが見つかったので終了

            except Exception as e:
                logger.error(f"メール処理エラー (ID: {mail_id}): {e}")
                continue

        mail.logout()
        logger.info("Gmail接続を終了しました")

    except imaplib.IMAP4.error as e:
        logger.error(f"IMAP認証エラー: {e}")
        logger.error("アプリパスワードが正しいか確認してください")
    except Exception as e:
        logger.error(f"接続エラー: {e}")

    return results


# ============================================================
# JSON保存
# ============================================================

def save_results(results: list[dict]) -> None:
    """パース結果をJSONファイルに保存する"""
    if not results:
        logger.info("保存するデータがありません")
        return

    # 全結果をまとめたファイル
    all_data = {
        "last_updated": datetime.now().isoformat(timespec="seconds"),
        "orders": results,
    }

    all_file = DATA_DIR / "coop_orders.json"
    with open(all_file, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
    logger.info(f"全データを保存: {all_file}")

    # 最新の注文だけ別ファイルにも保存（アプリからの取得用）
    latest = results[-1]  # 一番新しいもの
    latest_file = DATA_DIR / "coop_latest.json"
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(latest, f, ensure_ascii=False, indent=2)
    logger.info(f"最新データを保存: {latest_file}")


# ============================================================
# メイン
# ============================================================

def main():
    logger.info("=" * 50)
    logger.info("COOPメール取得開始")
    logger.info("=" * 50)

    results = fetch_coop_emails(days_back=14)

    if results:
        save_results(results)
        logger.info(f"完了: {len(results)}件の注文を処理しました")
    else:
        logger.info("取得した注文はありませんでした")

    logger.info("=" * 50)


if __name__ == "__main__":
    main()
