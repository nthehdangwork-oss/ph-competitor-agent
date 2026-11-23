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

SENDER_EMAIL = (os.getenv("SENDER_EMAIL") or "").strip()
RECEIVER_EMAIL = (os.getenv("RECEIVER_EMAIL") or "").strip()
EMAIL_PASSWORD = (os.getenv("EMAIL_APP_PASSWORD") or "").replace(" ", "").strip()


def send_telegram_2day_digest(new_rows):
    """
    Send a 48-hour digest of new competitor feature and product updates via Telegram.
    Row structure: [iso_date, display_date, comp, what_text, side_label, source_name, clean_title, url]
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Notifier] Skipped Telegram notification: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not configured.")
        return

    if not new_rows:
        msg = (
            "🇵🇭 *PH COMPETITOR UPDATE (48h)*\n\n"
            "ℹ️ *Status:* No new feature launches, product updates, or regulatory shifts detected from Grab PH, Move It, Angkas, JoyRide, inDrive, or LTFRB in the past 48 hours.\n\n"
            f"📊 [Open Google Sheets for details]({SHEET_URL})"
        )
    else:
        lines = [f"🇵🇭 *PH COMPETITOR UPDATE ({len(new_rows)} new updates)*\n"]
        for r in new_rows[:10]:
            comp = str(r[2]).replace("*", "").replace("_", "").replace("[", "(").replace("]", ")")
            what = str(r[3]).replace("*", "").replace("_", "").replace("`", "'")
            side = str(r[4]).replace("*", "").replace("_", "")
            url = r[7]
            lines.append(f"• *[{comp} - {side}]* {what} ([Source]({url}))\n")
        lines.append(f"📊 [Open Google Sheets for details]({SHEET_URL})")
        msg = "\n".join(lines)

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    res = requests.post(url, json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": msg,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    })
    if not res.ok:
        # Fallback to plain text if Telegram Markdown parser rejects special characters in URL/text
        res_plain = requests.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg.replace("*", ""),
            "disable_web_page_preview": True
        })
        if not res_plain.ok:
            print(f"[Notifier] Telegram 2-day digest send failed: {res.status_code} - {res.text}")
        else:
            print(f"[Notifier] Telegram 2-day digest delivered (plain text fallback) to {TELEGRAM_CHAT_ID}!")
    else:
        print(f"[Notifier] Telegram 2-day digest delivered successfully to {TELEGRAM_CHAT_ID}!")


def send_telegram_biweekly_report(report_header, bottom_line, action_items, md_filepath):
    """
    Send the bi-weekly executive overview summary and attach the full Markdown report document via Telegram.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Notifier] Skipped Telegram bi-weekly report: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not configured.")
        return

    summary_msg = (
        f"📑 *{report_header}*\n\n"
        f"*Bottom Line & Key Product Takeaways:*\n{bottom_line}\n\n"
        f"*What I'd Like Us to Act On:*\n{action_items}\n\n"
        f"📎 _Full detailed product report (including Feature Matrix & References) attached below._"
    )
    res_msg = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": summary_msg,
        "parse_mode": "Markdown"
    })
    if not res_msg.ok:
        res_plain = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": summary_msg.replace("*", "").replace("_", "")
        })
        if not res_plain.ok:
            print(f"[Notifier] Telegram biweekly summary send failed: {res_msg.status_code} - {res_msg.text}")
        else:
            print(f"[Notifier] Telegram biweekly summary delivered (plain text fallback) to {TELEGRAM_CHAT_ID}!")
    else:
        print(f"[Notifier] Telegram biweekly summary delivered successfully to {TELEGRAM_CHAT_ID}!")

    if os.path.exists(md_filepath):
        with open(md_filepath, "rb") as f:
            res_doc = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendDocument",
                data={"chat_id": TELEGRAM_CHAT_ID, "caption": "Detailed Competitor Product Report (Markdown/Doc Format)"},
                files={"document": f}
            )
            if not res_doc.ok:
                print(f"[Notifier] Telegram document upload failed: {res_doc.status_code} - {res_doc.text}")
            else:
                print(f"[Notifier] Telegram document delivered successfully to {TELEGRAM_CHAT_ID}!")


def send_email_biweekly_report(report_header, bottom_line, filtered_rows, action_items):
    """
    Send an HTML-formatted executive email report styled after Google Docs (Date | Who | What | Side | Ref).
    """
    receiver_list = [r.strip() for r in RECEIVER_EMAIL.split(",") if r.strip()]
    if not (SENDER_EMAIL and receiver_list and EMAIL_PASSWORD):
        print("[Notifier] Skipped Email notification: SENDER_EMAIL / RECEIVER_EMAIL / EMAIL_APP_PASSWORD not configured.")
        return

    table_rows_html = ""
    refs_html = ""
    if not filtered_rows:
        table_rows_html = """
        <tr>
            <td colspan="5" style="border: 1px solid #d0d7de; padding: 16px; text-align: center; color: #57606a;">
                <i>No new features, pricing shifts, or major policy changes recorded from competitors in the past 14 days.</i>
            </td>
        </tr>
        """
        refs_html = """<li style="color: #57606a;"><i>No reference citations for this reporting period.</i></li>"""
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
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #24292f; max-width: 900px; margin: 0 auto; padding: 20px;">
        <h2 style="border-bottom: 2px solid #0969da; padding-bottom: 6px; color: #1f2328;">{report_header}</h2>
        
        <h3 style="color: #0969da; margin-top: 24px;">Bottom Line</h3>
        <div style="font-size: 14px; color: #32383f;">{bottom_line.replace(chr(10), '<br>')}</div>
        
        <h3 style="color: #0969da; margin-top: 28px;">Feature and Product Launches</h3>
        <table style="border-collapse: collapse; width: 100%; font-size: 13px;">
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
        
        <h3 style="color: #0969da; margin-top: 28px;">What I'd Like Us to Act On</h3>
        <div style="font-size: 14px; color: #32383f;">{action_items.replace(chr(10), '<br>')}</div>
        
        <h3 style="color: #0969da; margin-top: 28px;">References</h3>
        <ul style="list-style-type: none; padding-left: 0; font-size: 12px; color: #57606a;">{refs_html}</ul>
        
        <p style="margin-top: 28px; font-size: 12px; color: #57606a; border-top: 1px solid #d0d7de; padding-top: 12px;">
            <a href="{SHEET_URL}">Open original tracking sheet on Google Sheets</a>
        </p>
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"[PH Competitor Product Report] {report_header}"
    msg["From"] = SENDER_EMAIL
    msg["To"] = ", ".join(receiver_list)
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        try:
            server = smtplib.SMTP("smtp.gmail.com", 587, timeout=15)
            server.starttls()
            server.login(SENDER_EMAIL, EMAIL_PASSWORD)
            server.sendmail(SENDER_EMAIL, receiver_list, msg.as_string())
            server.quit()
            print(f"[Notifier] Successfully delivered email report to {', '.join(receiver_list)} via TLS:587!")
        except Exception:
            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
                server.login(SENDER_EMAIL, EMAIL_PASSWORD)
                server.sendmail(SENDER_EMAIL, receiver_list, msg.as_string())
            print(f"[Notifier] Successfully delivered email report to {', '.join(receiver_list)} via SSL:465!")
    except Exception as e:
        print(f"[Notifier] Email delivery failed (will retry automatically on GitHub Actions): {e}")
