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
from email.message import Message
import os
import json
import logging
import time
import uuid
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

# ログ設定（共通ログ規約 v1）
_LEVEL_NAMES = {
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warn",
    logging.ERROR: "error",
    logging.CRITICAL: "critical",
}


class StructuredJsonFormatter(logging.Formatter):
    """安全なフィールドだけを1行JSONへ変換する。"""

    def format(self, record: logging.LogRecord) -> str:
        level = _LEVEL_NAMES.get(record.levelno)
        if level is None:
            level = "critical" if record.levelno > logging.CRITICAL else "debug"

        event = getattr(record, "event", None)
        if not event:
            event = "server_log" if record.name.startswith("uvicorn") else "library_log"

        payload = {
            "ts": datetime.now().astimezone().isoformat(timespec="milliseconds"),
            "app": "coop-api",
            "level": level,
            "event": event,
        }

        fields = getattr(record, "event_fields", {})
        if isinstance(fields, dict):
            for key, value in fields.items():
                if key not in payload:
                    payload[key] = value

        if record.exc_info and record.exc_info[0]:
            payload.setdefault("error_class", record.exc_info[0].__name__)

        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def configure_logging() -> None:
    """アプリとuvicornのログをstderrの単一JSONハンドラへ統一する。"""
    handler = logging.StreamHandler()
    handler.setFormatter(StructuredJsonFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True

    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    access_logger.propagate = False
    access_logger.disabled = True

    # httpxのリクエストURL付きINFOログは出力しない。
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def log_event(log: logging.Logger, level: int, event: str, **fields) -> None:
    """固定イベント名と明示した安全なフィールドだけを記録する。"""
    log.log(level, event, extra={"event": event, "event_fields": fields})


configure_logging()
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

def get_email_body(msg: Message) -> str:
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

def _fetch_coop_emails(days_back: int = 14) -> tuple[list[dict], bool]:
    """
    GmailからCOOPデリのメールを取得してパースする

    Args:
        days_back: 何日前までのメールを取得するか（デフォルト14日）

    Returns:
        パース済みの注文データリストと、取得処理が正常終了したか
    """
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        log_event(
            logger,
            logging.ERROR,
            "job_configuration_invalid",
            error_kind="validation",
            has_credentials=False,
        )
        return [], False

    results = []
    mail = None
    had_message_failure = False

    try:
        # Gmail IMAP に接続
        log_event(logger, logging.INFO, "external_call_started", target="gmail_imap")
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        log_event(logger, logging.INFO, "external_call_succeeded", target="gmail_imap")

        # 受信トレイを選択（読み取り専用）
        select_status, _ = mail.select("INBOX", readonly=True)
        if select_status != "OK":
            log_event(
                logger,
                logging.ERROR,
                "external_call_failed",
                target="gmail_imap",
                error_kind="external_api",
                operation="select_inbox",
            )
            return [], False

        # 検索条件を構築
        # Gmail固有のX-GM-RAW拡張で「coopdeli」を含むメールを検索
        # ※ 日本語はIMAPのASCIIエンコード制限で使えないため英数字で検索
        # ※ 転送メールでも本文にcoopdeliの情報が残るためヒットする
        search_criteria = f'(X-GM-RAW "coopdeli newer_than:{days_back}d")'
        log_event(
            logger,
            logging.INFO,
            "email_search_started",
            target="gmail_imap",
            days_back=days_back,
        )

        status, messages = mail.search(None, search_criteria)

        if status != "OK":
            log_event(
                logger,
                logging.ERROR,
                "external_call_failed",
                target="gmail_imap",
                error_kind="external_api",
                operation="search",
            )
            return [], False

        if not messages[0]:
            log_event(logger, logging.INFO, "email_search_completed", match_count=0)
            return [], True

        mail_ids = messages[0].split()
        log_event(
            logger,
            logging.INFO,
            "email_search_completed",
            match_count=len(mail_ids),
            candidate_count=min(len(mail_ids), 5),
        )

        # 新しい順に最大5通を確認（最新が広告メール等だった場合に次を試す）
        for candidate_number, mail_id in enumerate(reversed(mail_ids[-5:]), start=1):
            try:
                status, msg_data = mail.fetch(mail_id, "(RFC822)")
                if status != "OK":
                    had_message_failure = True
                    log_event(
                        logger,
                        logging.WARNING,
                        "external_call_failed",
                        target="gmail_imap",
                        error_kind="external_api",
                        operation="fetch_message",
                        attempt=candidate_number,
                    )
                    continue

                raw_email = msg_data[0][1]
                msg = email.message_from_bytes(raw_email)

                # メールの日付を取得
                date_str = msg.get("Date", "")
                subject = decode_mime_header(msg.get("Subject", ""))
                sender = decode_mime_header(msg.get("From", ""))

                log_event(
                    logger,
                    logging.INFO,
                    "email_candidate_processing",
                    candidate_number=candidate_number,
                )

                # 本文を取得
                body = get_email_body(msg)

                if not body:
                    log_event(
                        logger,
                        logging.WARNING,
                        "email_candidate_skipped",
                        reason="empty_body",
                        candidate_number=candidate_number,
                    )
                    continue

                # 注文確認メールかどうかをチェック
                if "注文番号" not in body and "商品名" not in body:
                    log_event(
                        logger,
                        logging.INFO,
                        "email_candidate_skipped",
                        reason="not_order_confirmation",
                        candidate_number=candidate_number,
                    )
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
                log_event(
                    logger,
                    logging.INFO,
                    "email_parsed",
                    ingredient_count=len(parsed["ingredients"]),
                    kit_count=len(parsed["kits"]),
                    excluded_count=parsed["excluded_count"],
                )
                break  # 注文確認メールが見つかったので終了

            except Exception as exc:
                had_message_failure = True
                log_event(
                    logger,
                    logging.WARNING,
                    "external_call_failed",
                    target="gmail_imap",
                    error_kind="internal",
                    operation="process_message",
                    error_class=type(exc).__name__,
                    attempt=candidate_number,
                )
                continue

        return results, bool(results) or not had_message_failure

    except imaplib.IMAP4.error as exc:
        log_event(
            logger,
            logging.ERROR,
            "external_call_failed",
            target="gmail_imap",
            error_kind="auth",
            error_class=type(exc).__name__,
        )
        return [], False
    except (TimeoutError, OSError) as exc:
        log_event(
            logger,
            logging.ERROR,
            "external_call_failed",
            target="gmail_imap",
            error_kind="timeout" if isinstance(exc, TimeoutError) else "external_api",
            error_class=type(exc).__name__,
        )
        return [], False
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "external_call_failed",
            target="gmail_imap",
            error_kind="internal",
            error_class=type(exc).__name__,
        )
        return [], False
    finally:
        if mail is not None:
            try:
                mail.logout()
            except Exception as exc:
                log_event(
                    logger,
                    logging.WARNING,
                    "external_call_failed",
                    target="gmail_imap",
                    error_kind="external_api",
                    operation="logout",
                    error_class=type(exc).__name__,
                )


def run_coop_mail_import(days_back: int = 14, save: bool = False) -> list[dict]:
    """1回のメール取得をjob_start/job_endで囲み、必要なら結果を保存する。"""
    run_id = uuid.uuid4().hex
    started_at = time.monotonic()
    succeeded = False
    log_event(
        logger,
        logging.INFO,
        "job_start",
        job="coop-mail-import",
        run_id=run_id,
    )

    try:
        results, fetch_succeeded = _fetch_coop_emails(days_back=days_back)
        if save and results:
            save_results(results)
        succeeded = fetch_succeeded
        return results
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "job_failed",
            job="coop-mail-import",
            run_id=run_id,
            error_kind="internal",
            error_class=type(exc).__name__,
        )
        raise
    finally:
        log_event(
            logger,
            logging.INFO if succeeded else logging.ERROR,
            "job_end",
            job="coop-mail-import",
            run_id=run_id,
            status="success" if succeeded else "failure",
            duration_ms=round((time.monotonic() - started_at) * 1000),
        )


def fetch_coop_emails(days_back: int = 14) -> list[dict]:
    """GmailからCOOPデリのメールを取得する（保存は行わない）。"""
    return run_coop_mail_import(days_back=days_back, save=False)


# ============================================================
# JSON保存
# ============================================================

def save_results(results: list[dict]) -> None:
    """パース結果をJSONファイルに保存する"""
    if not results:
        log_event(logger, logging.INFO, "results_save_skipped", reason="no_results")
        return

    # 全結果をまとめたファイル
    all_data = {
        "last_updated": datetime.now().isoformat(timespec="seconds"),
        "orders": results,
    }

    all_file = DATA_DIR / "coop_orders.json"
    with open(all_file, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
    log_event(
        logger,
        logging.INFO,
        "results_saved",
        target="coop_orders",
        order_count=len(results),
    )

    # 最新の注文だけ別ファイルにも保存（アプリからの取得用）
    latest = results[-1]  # 一番新しいもの
    latest_file = DATA_DIR / "coop_latest.json"
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(latest, f, ensure_ascii=False, indent=2)
    log_event(logger, logging.INFO, "results_saved", target="coop_latest", order_count=1)


# ============================================================
# メイン
# ============================================================

def main():
    run_coop_mail_import(days_back=14, save=True)


if __name__ == "__main__":
    main()
