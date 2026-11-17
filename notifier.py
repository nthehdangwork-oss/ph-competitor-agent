import os
import requests
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SHEET_URL = os.getenv("SHEET_URL", "#")

SENDER_EMAIL = os.getenv("SENDER_EMAIL")
RECEIVER_EMAIL = os.getenv("RECEIVER_EMAIL")
EMAIL_PASSWORD = os.getenv("EMAIL_APP_PASSWORD")


def send_telegram_2day_digest(new_rows):
    """
    Gửi thông báo tóm tắt các tin mới phát hiện trong chu kỳ 2 ngày/lần qua Telegram.
    Cấu trúc mỗi row: [iso_date, display_date, comp, what_text, side_label, source_name, clean_title, url]
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Notifier] Bỏ qua gửi Telegram vì chưa cấu hình TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID.")
        return

    if not new_rows:
        msg = (
            "🇵🇭 *PH COMPETITOR UPDATE (48h)*\n\n"
            "ℹ️ *Trạng thái:* Không có biến động mới nào từ Grab PH, Move It, Angkas, JoyRide, inDrive hay LTFRB trong 48 giờ qua.\n\n"
            f"📊 [Mở Google Sheets xem chi tiết]({SHEET_URL})"
        )
    else:
        lines = [f"🇵🇭 *PH COMPETITOR UPDATE ({len(new_rows)} tin mới)*\n"]
        for r in new_rows[:10]:
            comp = r[2]
            what = r[3]
            side = r[4]
            url = r[7]
            lines.append(f"• *[{comp} - {side}]* {what} ([Nguồn]({url}))\n")
        lines.append(f"📊 [Mở Google Sheets xem chi tiết]({SHEET_URL})")
        msg = "\n".join(lines)

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    })


def send_telegram_biweekly_report(report_header, bottom_line, action_items, md_filepath):
    """
    Gửi bản tóm tắt Overview 2 tuần và đính kèm trực tiếp file báo cáo Markdown/Doc qua Telegram.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return

    summary_msg = (
        f"📑 *{report_header}*\n\n"
        f"*Điểm cốt lõi (Bottom line):*\n{bottom_line}\n\n"
        f"*Đề xuất hành động cần thực hiện:*\n{action_items}\n\n"
        f"📎 _File báo cáo đầy đủ (kèm bảng Feature launches & References) được đính kèm bên dưới._"
    )
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": summary_msg,
        "parse_mode": "Markdown"
    })

    if os.path.exists(md_filepath):
        with open(md_filepath, "rb") as f:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument",
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": "Báo cáo chi tiết định dạng File Doc/Markdown"},
                files={"document": f}
            )


def send_email_biweekly_report(report_header, bottom_line, filtered_rows, action_items):
    """
    Gửi Email định dạng HTML có sẵn bảng kẻ ô chuẩn Google Docs (Date | Who | What | Side | Ref).
    """
    if not (SENDER_EMAIL and RECEIVER_EMAIL and EMAIL_PASSWORD):
        print("[Notifier] Bỏ qua gửi Email vì chưa cấu hình SENDER_EMAIL / EMAIL_APP_PASSWORD.")
        return

    table_rows_html = ""
    refs_html = ""
    if not filtered_rows:
        table_rows_html = """
        <tr>
            <td colspan="5" style="border: 1px solid #d0d7de; padding: 16px; text-align: center; color: #57606a;">
                <i>Không ghi nhận tính năng, chính sách giá hoặc thay đổi pháp lý mới nào từ đối thủ trong 14 ngày qua.</i>
            </td>
        </tr>
        """
        refs_html = """<li style="color: #57606a;"><i>Không có tài liệu tham khảo mới trong kỳ báo cáo này.</i></li>"""
    else:
        for idx, r in enumerate(filtered_rows, start=1):
            ref_tag = f"[{idx}]"
            table_rows_html += f"""
        <tr>
            <td style="border: 1px solid #d0d7de; padding: 8px; white-space: nowrap;">{r['Date']}</td>
            <td style="border: 1px solid #d0d7de; padding: 8px; font-weight: bold;">{r['Who']}</td>
            <td style="border: 1px solid #d0d7de; padding: 8px;">{r['What']}</td>
            <td style="border: 1px solid #d0d7de; padding: 8px;">{r['Side']}</td>
            <td style="border: 1px solid #d0d7de; padding: 8px; text-align: center;"><a href="{r['Source_URL']}">{ref_tag}</a></td>
        </tr>
        """
            refs_html += (
                f"""<li style="margin-bottom: 4px;">{ref_tag} {r['Source_Name']} — """
                f"""<i>{r['Source_Title']}</i> ({r['Date']}): <a href="{r['Source_URL']}">{r['Source_URL']}</a></li>"""
            )

    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #24292f; max-width: 900px;">
        <h2 style="border-bottom: 2px solid #0969da; padding-bottom: 6px;">{report_header}</h2>
        
        <h3>Điểm cốt lõi (Bottom line)</h3>
        <div>{bottom_line.replace(chr(10), '<br>')}</div>
        
        <h3>Tính năng và sản phẩm mới ra mắt (Feature and product launches)</h3>
        <table style="border-collapse: collapse; width: 100%; font-size: 14px;">
            <thead>
                <tr style="background-color: #f6f8fa; text-align: left;">
                    <th style="border: 1px solid #d0d7de; padding: 8px;">Date</th>
                    <th style="border: 1px solid #d0d7de; padding: 8px;">Who</th>
                    <th style="border: 1px solid #d0d7de; padding: 8px;">What</th>
                    <th style="border: 1px solid #d0d7de; padding: 8px;">Side</th>
                    <th style="border: 1px solid #d0d7de; padding: 8px; text-align: center;">Ref</th>
                </tr>
            </thead>
            <tbody>{table_rows_html}</tbody>
        </table>
        
        <h3>Đề xuất hành động cần thực hiện (What I'd like us to act on)</h3>
        <div>{action_items.replace(chr(10), '<br>')}</div>
        
        <h3>Tài liệu tham khảo (References)</h3>
        <ul style="list-style-type: none; padding-left: 0; font-size: 13px;">{refs_html}</ul>
        
        <p style="margin-top: 20px; font-size: 12px; color: #57606a;">
            <a href="{SHEET_URL}">Mở bảng dữ liệu gốc trên Google Sheets</a>
        </p>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[PH Competitor Report] {report_header}"
    msg["From"] = SENDER_EMAIL
    msg["To"] = RECEIVER_EMAIL
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(SENDER_EMAIL, EMAIL_PASSWORD)
            server.sendmail(SENDER_EMAIL, RECEIVER_EMAIL, msg.as_string())
        print(f"[Notifier] Đã gửi email báo cáo thành công tới {RECEIVER_EMAIL}!")
    except Exception as e:
        print(f"[Notifier] Gửi email báo cáo thất bại (sẽ tự động gửi lại trên GitHub Actions): {e}")
