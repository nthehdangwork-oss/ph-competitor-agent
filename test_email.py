import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

SENDER_EMAIL = os.getenv("SENDER_EMAIL", "").strip()
RECEIVER_EMAIL = os.getenv("RECEIVER_EMAIL", "").strip()
EMAIL_PASSWORD = os.getenv("EMAIL_APP_PASSWORD", "").strip().replace(" ", "")

print("=== PH COMPETITOR AGENT: EMAIL TEST SCRIPT ===")
print(f"Sender Email  : {SENDER_EMAIL}")
print(f"Receiver Email: {RECEIVER_EMAIL}")
print(f"Password set  : {'Yes (' + str(len(EMAIL_PASSWORD)) + ' chars)' if EMAIL_PASSWORD else 'NO'}")

if not SENDER_EMAIL or not EMAIL_PASSWORD:
    print("[ERROR] SENDER_EMAIL hoac EMAIL_APP_PASSWORD chua duoc khai bao trong .env!")
    exit(1)

msg = MIMEMultipart("alternative")
msg["Subject"] = "[TEST] Test Email Sending Flow - PH Competitor Agent"
msg["From"] = SENDER_EMAIL
msg["To"] = RECEIVER_EMAIL

html_content = f"""
<html>
<body style="font-family: Arial, sans-serif; padding: 20px; color: #24292f;">
    <h2 style="color: #0969da; border-bottom: 2px solid #0969da; padding-bottom: 8px;">
        PH Competitor Tracking Agent - Test Email Succeeded!
    </h2>
    <p>Xin chao,</p>
    <p>Day la email kiem tra he thong gui mail tu dong cua bot <b>ph-competitor-agent</b>.</p>
    <div style="background-color: #f6f8fa; border: 1px solid #d0d7de; border-radius: 6px; padding: 16px; margin: 16px 0;">
        <p style="margin: 0 0 8px 0;"><b>Trang thai:</b> Ket noi SMTP TLS 587 thanh cong</p>
        <p style="margin: 0 0 8px 0;"><b>Sender:</b> {SENDER_EMAIL}</p>
        <p style="margin: 0;"><b>Receiver:</b> {RECEIVER_EMAIL}</p>
    </div>
    <p>He thong bao cao dinh ky 14 ngay se duoc gui vao hom thu nay voi day du bang phan tich tinh nang san pham.</p>
</body>
</html>
"""
msg.attach(MIMEText(html_content, "html", "utf-8"))

print("\nConnecting to smtp.gmail.com:587 (TLS)...")
try:
    server = smtplib.SMTP("smtp.gmail.com", 587, timeout=15)
    server.ehlo()
    context = ssl.create_default_context()
    server.starttls(context=context)
    server.ehlo()
    print("TLS handshake completed successfully.")
    
    print(f"Authenticating as {SENDER_EMAIL}...")
    server.login(SENDER_EMAIL, EMAIL_PASSWORD)
    print("Authentication successful!")
    
    server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
    server.quit()
    print(f"\n[SUCCESS] Test email da duoc gui thanh cong toi: {RECEIVER_EMAIL}!")
except smtplib.SMTPAuthenticationError as e:
    print(f"\n[AUTH ERROR] 535 Bad Credentials ({e.smtp_error.decode('utf-8', errors='ignore') if hasattr(e, 'smtp_error') else e})")
    print("Nguyen nhan: Gmail tu choi email hoac mat khau ung dung.")
    print("Luu y: App Password phai duoc tao tu chinh tai khoan:", SENDER_EMAIL)
except Exception as e:
    print(f"\n[ERROR] Ket noi that bai: {type(e).__name__} - {e}")
