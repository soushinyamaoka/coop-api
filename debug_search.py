"""
メール検索デバッグスクリプト
いくつかの検索方式を試して、どれでCOOPメールがヒットするか確認する
"""

import imaplib
import email
from email.header import decode_header
import os
from dotenv import load_dotenv

load_dotenv()

GMAIL_ADDRESS = os.getenv("GMAIL_ADDRESS", "")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD", "")

def decode_mime_header(header_value):
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

def test_search(mail, label, criteria):
    """検索を試して結果件数と最新メールの件名を表示"""
    print(f"\n--- テスト: {label} ---")
    print(f"  検索条件: {criteria}")
    try:
        status, messages = mail.search(None, criteria)
        if status == "OK" and messages[0]:
            mail_ids = messages[0].split()
            print(f"  ✅ ヒット: {len(mail_ids)}件")

            # 最新3件の件名を表示
            for mid in mail_ids[-3:]:
                status2, msg_data = mail.fetch(mid, "(BODY[HEADER.FIELDS (SUBJECT DATE FROM)])")
                if status2 == "OK":
                    header = msg_data[0][1]
                    msg = email.message_from_bytes(header)
                    subject = decode_mime_header(msg.get("Subject", ""))
                    from_addr = decode_mime_header(msg.get("From", ""))
                    date = msg.get("Date", "")
                    print(f"    件名: {subject}")
                    print(f"    From: {from_addr}")
                    print(f"    日付: {date}")
                    print()
        else:
            print(f"  ❌ ヒットなし")
    except Exception as e:
        print(f"  ❌ エラー: {e}")


def main():
    if not GMAIL_ADDRESS or not GMAIL_APP_PASSWORD:
        print("エラー: .envにGMAIL_ADDRESSとGMAIL_APP_PASSWORDを設定してください")
        return

    print(f"Gmailに接続中... ({GMAIL_ADDRESS})")
    mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
    mail.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
    print("ログイン成功\n")

    mail.select("INBOX", readonly=True)

    # テスト1: 全メール（直近30日）
    test_search(mail, "直近30日の全メール", '(X-GM-RAW "newer_than:30d")')

    # テスト2: X-GM-RAWで件名検索（日本語）
    test_search(mail, "X-GM-RAW subject:eフレンズ", '(X-GM-RAW "subject:eフレンズ注文済メモメール newer_than:30d")')

    # テスト3: X-GM-RAWで件名を部分一致（短いキーワード）
    test_search(mail, "X-GM-RAW subject:eフレンズ（短縮）", '(X-GM-RAW "subject:eフレンズ newer_than:30d")')

    # テスト4: 標準IMAPのSUBJECT検索
    test_search(mail, "IMAP SUBJECT eフレンズ", 'SUBJECT "eフレンズ"')

    # テスト5: X-GM-RAWで本文キーワード
    test_search(mail, "X-GM-RAW 注文番号", '(X-GM-RAW "注文番号 newer_than:30d")')

    # テスト6: X-GM-RAWで本文キーワード（英語ワード混在）
    test_search(mail, "X-GM-RAW coopdeli", '(X-GM-RAW "coopdeli newer_than:30d")')

    # テスト7: FROM coopdeli
    test_search(mail, "FROM coopdeli", '(FROM "coopdeli")')

    # テスト8: FROM coopnet
    test_search(mail, "FROM coopnet", '(FROM "coopnet")')

    # テスト9: 標準IMAPのSUBJECT検索（注文）
    test_search(mail, "IMAP SUBJECT 注文", 'SUBJECT "注文"')

    mail.logout()
    print("\n=== テスト完了 ===")


if __name__ == "__main__":
    main()
