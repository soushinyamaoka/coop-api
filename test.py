import imaplib
import email

# Gmailに接続
mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
mail.login("あなたのメール@gmail.com", "abcd efgh ijkl mnop")  # アプリパスワード

# 受信トレイを開く
mail.select("INBOX")

# COOPからのメールを検索
status, messages = mail.search(None, '(FROM "coopdeli")')

# 最新のメールを取得
mail_ids = messages[0].split()
latest_id = mail_ids[-1]  # 一番新しいもの

status, msg_data = mail.fetch(latest_id, "(RFC822)")
raw_email = msg_data[0][1]
msg = email.message_from_bytes(raw_email)

# 本文を取り出す
for part in msg.walk():
    if part.get_content_type() == "text/plain":
        body = part.get_payload(decode=True).decode("utf-8", errors="replace")
        print(body)  # ここにCOOPの注文内容が入る

mail.logout()