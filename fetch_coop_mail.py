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
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from coop_parser import (
    classify_item,
    extract_order_rows,
    extract_product_names,
    parse_coop_email,
    source_fingerprint,
)

# ============================================================
# 設定
# ============================================================

# .envファイルから環境変数を読み込み
load_dotenv()

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

# データ保存先
DATA_DIR = Path(__file__).parent / "data"

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
_warned_missing_jev_key = False


class ImportBusyError(Exception):
    """Raised when another process or thread owns the import lock."""


@contextmanager
def _coop_import_lock():
    """Take a non-blocking OS lock shared by cron and API imports."""
    lock_path = DATA_DIR / ".coop_import.lock"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "a+b")
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            if os.fstat(lock_file.fileno()).st_size == 0:
                lock_file.seek(0)
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            try:
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                raise ImportBusyError from exc
        else:
            import fcntl

            try:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise ImportBusyError from exc
        locked = True
        yield
    finally:
        if locked:
            if os.name == "nt":
                import msvcrt

                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
        lock_file.close()


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

def _fetch_coop_emails(
    days_back: int = 14, skip_if_unchanged: bool = False, collect_all: bool = False
) -> tuple[list[dict], bool]:
    """
    GmailからCOOPデリのメールを取得してパースする

    Args:
        days_back: 何日前までのメールを取得するか（デフォルト14日）
        collect_all: True なら期間内の注文確認メールを全件パースする（過去分取り込み用）。
            外部APIの呼び出し量を抑えるため、分類はキーワード判定のみを使う。

    Returns:
        パース済みの注文データリスト（古い順）と、取得処理が正常終了したか
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
        # 通常は新しい順に最大5通を確認（最新が広告メール等だった場合に次を試す）。
        # 過去分取り込みでは期間内の全件を確認する。
        candidates = list(reversed(mail_ids)) if collect_all else list(reversed(mail_ids[-5:]))
        log_event(
            logger,
            logging.INFO,
            "email_search_completed",
            match_count=len(mail_ids),
            candidate_count=len(candidates),
        )

        for candidate_number, mail_id in enumerate(candidates, start=1):
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

                # 同一注文なら分類（Jev問い合わせを含む）より前に終了する。
                parsed_fingerprint = source_fingerprint(body)
                if skip_if_unchanged:
                    try:
                        latest = json.loads((DATA_DIR / "coop_latest.json").read_text(encoding="utf-8"))
                        previous_fingerprint = latest.get("source_fingerprint") if isinstance(latest, dict) else None
                    except (OSError, UnicodeError, json.JSONDecodeError):
                        previous_fingerprint = None
                    if previous_fingerprint and previous_fingerprint == parsed_fingerprint:
                        log_event(
                            logger,
                            logging.INFO,
                            "import_skipped",
                            reason="unchanged",
                            item_count=len(extract_order_rows(body)),
                        )
                        return [], True

                # パース
                classifier = None
                from jev_classifier import classifier_mode
                mode, disabled_reason = ("keyword", None) if collect_all else classifier_mode()
                if disabled_reason:
                    global _warned_missing_jev_key
                    if not _warned_missing_jev_key:
                        log_event(logger, logging.WARNING, "jev_classifier_disabled", reason=disabled_reason)
                        _warned_missing_jev_key = True
                if mode == "jev":
                    typesafe_key = os.getenv("TYPESAFE_API_KEY", "")
                    if typesafe_key:
                        from jev_classifier import classify_products_sync
                        decisions, jev_stats = classify_products_sync(
                            extract_product_names(body),
                            api_key=typesafe_key,
                            cache_path=DATA_DIR / "category_jev_cache.json",
                            overrides_path=DATA_DIR / "category_overrides.json",
                        )
                        def classifier(name):
                            clean_name = name.strip()
                            return decisions.get(
                                clean_name,
                                {"choice": classify_item(clean_name), "source": "keyword"},
                            )
                        log_event(logger, logging.INFO, "jev_classification_summary", **jev_stats)
                parsed = parse_coop_email(body, classifier=classifier)
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
                if not collect_all:
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

        results.reverse()  # 新しい順に処理したので古い順へ戻す（末尾が最新）
        if collect_all:
            # 過去分取込は全件が対象。1通でも取得・解析に失敗したら一部取得なので失敗とする。
            # 取得できた分は履歴へ保存され、再実行しても重複しない。
            return results, not had_message_failure
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


def run_coop_mail_import(
    days_back: int = 14, save: bool = False, skip_if_unchanged: bool = False,
    backfill: bool = False,
) -> list[dict]:
    """1回のメール取得をjob_start/job_endで囲み、必要なら結果を保存する。

    backfill=True は期間内の注文メールを全件取り込み、注文履歴にだけ追記する
    （最新注文ファイル coop_latest.json は更新しない）。
    """
    run_id = uuid.uuid4().hex
    started_at = time.monotonic()
    succeeded = False
    log_event(
        logger,
        logging.INFO,
        "job_start",
        job="coop-mail-backfill" if backfill else "coop-mail-import",
        run_id=run_id,
    )

    try:
        try:
            with _coop_import_lock():
                log_event(logger, logging.INFO, "import_lock", result="acquired")
                results, fetch_succeeded = _fetch_coop_emails(
                    days_back=days_back,
                    skip_if_unchanged=skip_if_unchanged and not backfill,
                    collect_all=backfill,
                )
                saved_ok = True
                if save and results:
                    if backfill:
                        saved_ok = save_results(results, update_latest=False) is not False
                    else:
                        saved_ok = save_results(results) is not False
                succeeded = fetch_succeeded and saved_ok
                return results
        except ImportBusyError:
            log_event(logger, logging.INFO, "import_lock", result="busy")
            log_event(logger, logging.INFO, "import_skipped", reason="locked")
            succeeded = True
            if skip_if_unchanged:
                return []
            raise
    except ImportBusyError:
        # busyは別プロセス/スレッドとの正常な競合であり、内部failureではない。
        # job_failedは出さず、finallyのjob_end(success)のみで記録する。
        raise
    except Exception as exc:
        log_event(
            logger,
            logging.ERROR,
            "job_failed",
            job="coop-mail-backfill" if backfill else "coop-mail-import",
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
            job="coop-mail-backfill" if backfill else "coop-mail-import",
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

def _order_key(order: dict) -> tuple[str, str]:
    """注文履歴の重複判定キー。同じメールの再取込は同じ注文日・指紋になる。
    指紋が欠ける旧データはメール件名・日時で代用する。"""
    fingerprint = order.get("source_fingerprint") or f"{order.get('email_subject', '')}|{order.get('email_date', '')}"
    return order.get("order_date", ""), fingerprint


def _load_order_history(all_file: Path) -> list[dict] | None:
    """既存の注文履歴を読む。ファイルが無ければ空、読めなければ None（上書きで履歴を失わないため）。"""
    if not all_file.exists():
        return []
    try:
        data = json.loads(all_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    orders = data.get("orders") if isinstance(data, dict) else None
    return orders if isinstance(orders, list) else None


def save_results(results: list[dict], update_latest: bool = True) -> bool:
    """パース結果を注文履歴（coop_orders.json）へ追記し、最新注文を coop_latest.json に保存する。

    注文履歴を保存できなかった場合は False を返す（呼び出し側がジョブ失敗として扱う）。
    その場合も、最新注文（coop_latest.json）の保存は従来どおり続ける。
    """
    if not results:
        log_event(logger, logging.INFO, "results_save_skipped", reason="no_results")
        return True

    DATA_DIR.mkdir(exist_ok=True)

    # 注文履歴：既存に追記（重複は除外）し、注文日の古い順に並べる
    history_saved = True
    all_file = DATA_DIR / "coop_orders.json"
    history = _load_order_history(all_file)
    if history is None:
        history_saved = False
        log_event(
            logger,
            logging.ERROR,
            "results_save_failed",
            target="coop_orders",
            reason="history_unreadable",
        )
    else:
        known = {_order_key(order) for order in history if isinstance(order, dict)}
        added = 0
        for order in results:
            key = _order_key(order)
            if key in known:
                continue
            history.append(order)
            known.add(key)
            added += 1
        try:
            if added:
                history.sort(key=lambda order: order.get("order_date", "") if isinstance(order, dict) else "")
                all_data = {
                    "last_updated": datetime.now().isoformat(timespec="seconds"),
                    "orders": history,
                }
                with open(all_file, "w", encoding="utf-8") as f:
                    json.dump(all_data, f, ensure_ascii=False, indent=2)
        except OSError as exc:
            history_saved = False
            log_event(
                logger,
                logging.ERROR,
                "results_save_failed",
                target="coop_orders",
                reason="history_write_failed",
                error_class=type(exc).__name__,
            )
        else:
            log_event(
                logger,
                logging.INFO,
                "results_saved",
                target="coop_orders",
                order_count=len(history),
                added_count=added,
            )

    if not update_latest:
        return history_saved

    # 最新の注文だけ別ファイルにも保存（アプリからの取得用）
    latest = results[-1]  # 一番新しいもの
    latest_file = DATA_DIR / "coop_latest.json"
    with open(latest_file, "w", encoding="utf-8") as f:
        json.dump(latest, f, ensure_ascii=False, indent=2)
    log_event(logger, logging.INFO, "results_saved", target="coop_latest", order_count=1)
    return history_saved


# ============================================================
# メイン
# ============================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(description="COOP注文メールを取得して保存する")
    parser.add_argument(
        "--backfill-days",
        type=int,
        default=None,
        help="指定日数分の過去の注文メールを全件取り込み、注文履歴にだけ追記する（手動実行用）",
    )
    args = parser.parse_args()
    if args.backfill_days is not None:
        if args.backfill_days < 1:
            parser.error("--backfill-days は1以上を指定してください")
        run_coop_mail_import(days_back=args.backfill_days, save=True, backfill=True)
        return
    run_coop_mail_import(days_back=14, save=True, skip_if_unchanged=True)


if __name__ == "__main__":
    main()
