import os
import re
import html
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


def send_telegram_biweekly_report(report_header, snapshot_line, bottom_line_tg_html, action_items_tg_html, md_filepath):
    """
    Send the 30-second executive overview summary (with clickable [Source X] links)
    and attach the full 5-section Markdown report document via Telegram.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[Notifier] Skipped Telegram bi-weekly report: TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not configured.")
        return

    summary_msg = (
        f"📑 <b>{html.escape(report_header)}</b>\n"
        f"<i>{html.escape(snapshot_line)}</i>\n\n"
        f"<b>Section 1: Bottom Line &amp; Key Product Takeaways</b>\n{bottom_line_tg_html}\n\n"
        f"<b>Section 3: What I'd Like Us to Act On</b>\n{action_items_tg_html}\n\n"
        f"📎 <i>Full 5-section report (High-Signal Matrix, Appendix &amp; 1-to-1 References) attached below.</i>"
    )

    res_msg = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={
        "chat_id": TELEGRAM_CHAT_ID,
        "text": summary_msg,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    })
    if not res_msg.ok:
        plain_fallback = re.sub(r'<[^>]+>', '', html.unescape(summary_msg))
        res_plain = requests.post(f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage", json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": plain_fallback[:4000],
            "disable_web_page_preview": True
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
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": "Executive 5-Section Competitor Report (Markdown / Google Docs Ready)"
                },
                files={"document": f}
            )
            if not res_doc.ok:
                print(f"[Notifier] Telegram document upload failed: {res_doc.status_code} - {res_doc.text}")
            else:
                print(f"[Notifier] Telegram document delivered successfully to {TELEGRAM_CHAT_ID}!")


def _side_badge_html(side):
    """Subtle, clean badge styling for standardized Side labels in HTML email."""
    palette = {
        "Driver": ("#ddf4ff", "#0969da"),
        "Passenger": ("#dafbe1", "#1a7f37"),
        "Regulatory": ("#fff8c5", "#9a6700"),
        "Marketplace": ("#fbefff", "#8250df"),
        "Both": ("#f6f8fa", "#24292f"),
    }
    bg, fg = palette.get(side, ("#f6f8fa", "#24292f"))
    return (
        f'<span style="display: inline-block; background-color: {bg}; color: {fg}; '
        f'padding: 2px 8px; border-radius: 10px; font-size: 11.5px; font-weight: 600; white-space: nowrap;">'
        f'{html.escape(str(side))}</span>'
    )


def send_email_biweekly_report(
    report_header,
    snapshot_line,
    bottom_line_email_html,
    high_signal_rows,
    action_items_email_html,
    appendix_rows,
    all_ref_items
):
    """
    Send the 5-section executive HTML email report:
      - Header & 1-line Snapshot
      - Section 1: Bottom Line & Key Product Takeaways (30-Second Read)
      - Section 2: Feature, Policy & Market Launches (High-Signal Matrix)
      - Section 3: What I'd Like Us to Act On (Owner-tagged, zero P0/P1)
      - Section 4: Appendix: Low-Signal, Older & Static Items
      - Section 5: References & Citations (1-to-1 numbered Publisher — [Title](URL) (Date))
    """
    receiver_list = [r.strip() for r in RECEIVER_EMAIL.split(",") if r.strip()]
    if not (SENDER_EMAIL and receiver_list and EMAIL_PASSWORD):
        print("[Notifier] Skipped Email notification: SENDER_EMAIL / RECEIVER_EMAIL / EMAIL_APP_PASSWORD not configured.")
        return

    # Section 2: High-Signal Matrix rows
    if not high_signal_rows:
        main_rows_html = """
        <tr>
            <td colspan="5" style="border: 1px solid #d0d7de; padding: 16px; text-align: center; color: #57606a;">
                <i>No high-signal feature launches, pricing/commission shifts, or binding regulatory updates recorded in the past 14 days. Market remains stable.</i>
            </td>
        </tr>
        """
    else:
        main_rows_html = ""
        for r in high_signal_rows:
            n = r["ref_num"]
            safe_url = html.escape(r["Source_URL"], quote=True)
            main_rows_html += f"""
        <tr>
            <td style="border: 1px solid #d0d7de; padding: 8px 10px; white-space: nowrap; vertical-align: top;">{html.escape(str(r['Date']))}</td>
            <td style="border: 1px solid #d0d7de; padding: 8px 10px; font-weight: 600; white-space: nowrap; vertical-align: top;">{html.escape(str(r['Who']))}</td>
            <td style="border: 1px solid #d0d7de; padding: 8px 10px; vertical-align: top;">{html.escape(str(r['What']))}</td>
            <td style="border: 1px solid #d0d7de; padding: 8px 10px; text-align: center; vertical-align: top;">{_side_badge_html(r['Side'])}</td>
            <td style="border: 1px solid #d0d7de; padding: 8px 10px; text-align: center; white-space: nowrap; vertical-align: top;">
                <a href="{safe_url}" style="color: #0969da; text-decoration: none; font-weight: 500;">[Source {n}]</a>
            </td>
        </tr>
        """

    # Section 4: Appendix rows
    if not appendix_rows:
        appendix_rows_html = """
        <tr>
            <td colspan="5" style="border: 1px solid #d0d7de; padding: 14px; text-align: center; color: #57606a;">
                <i>No low-signal, archival, or static items recorded in this cycle.</i>
            </td>
        </tr>
        """
    else:
        appendix_rows_html = ""
        for ar in appendix_rows:
            n = ar["ref_num"]
            safe_url = html.escape(ar["Source_URL"], quote=True)
            appendix_rows_html += f"""
        <tr>
            <td style="border: 1px solid #d0d7de; padding: 7px 9px; white-space: nowrap; vertical-align: top; color: #57606a;">{html.escape(str(ar['Date']))}</td>
            <td style="border: 1px solid #d0d7de; padding: 7px 9px; font-weight: 600; white-space: nowrap; vertical-align: top; color: #424a53;">{html.escape(str(ar['Who']))}</td>
            <td style="border: 1px solid #d0d7de; padding: 7px 9px; vertical-align: top; color: #424a53;">{html.escape(str(ar['What']))}</td>
            <td style="border: 1px solid #d0d7de; padding: 7px 9px; white-space: nowrap; vertical-align: top; color: #57606a;"><i>{html.escape(str(ar['Filter_Reason']))}</i></td>
            <td style="border: 1px solid #d0d7de; padding: 7px 9px; text-align: center; white-space: nowrap; vertical-align: top;">
                <a href="{safe_url}" style="color: #0969da; text-decoration: none;">[Source {n}]</a>
            </td>
        </tr>
        """

    # Section 5: 1-to-1 References & Citations (Ordered list 1, 2, 3...)
    if not all_ref_items:
        refs_section_html = '<p style="font-size: 12.5px; color: #57606a;"><i>No reference citations recorded for this reporting period.</i></p>'
    else:
        ref_li_items = ""
        for item in all_ref_items:
            safe_url = html.escape(item["Source_URL"], quote=True)
            pub = html.escape(str(item.get("Source_Name", "News PH")))
            title_txt = html.escape(str(item.get("Clean_Title") or item.get("Source_Title") or item["Source_URL"]))
            dt_txt = html.escape(str(item.get("Date", "")))
            ref_li_items += (
                f'<li value="{item["ref_num"]}" style="margin-bottom: 5px;">'
                f'{pub} &mdash; <a href="{safe_url}" style="color: #0969da; text-decoration: none;">{title_txt}</a> ({dt_txt})'
                f'</li>'
            )
        refs_section_html = f'<ol style="padding-left: 22px; margin: 8px 0; font-size: 12.5px; color: #424a53;">{ref_li_items}</ol>'

    safe_sheet_url = html.escape(SHEET_URL, quote=True)
    html_body = f"""
    <html>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif; line-height: 1.55; color: #1f2328; max-width: 960px; margin: 0 auto; padding: 20px;">
        <h2 style="border-bottom: 2px solid #0969da; padding-bottom: 8px; margin-bottom: 10px; color: #1f2328; font-size: 20px;">
            {html.escape(report_header)}
        </h2>
        <div style="font-size: 13px; color: #424a53; background-color: #f6f8fa; border-left: 4px solid #0969da; padding: 10px 14px; margin: 0 0 22px 0;">
            {html.escape(snapshot_line)}
        </div>

        <h3 style="color: #0969da; margin-top: 24px; margin-bottom: 8px; font-size: 16px;">
            Section 1: Bottom Line &amp; Key Product Takeaways
        </h3>
        <div style="font-size: 14px; color: #1f2328;">
            {bottom_line_email_html}
        </div>

        <h3 style="color: #0969da; margin-top: 28px; margin-bottom: 10px; font-size: 16px;">
            Section 2: Feature, Policy &amp; Market Launches
        </h3>
        <table style="border-collapse: collapse; width: 100%; font-size: 13px;">
            <thead>
                <tr style="background-color: #f6f8fa; text-align: left;">
                    <th style="border: 1px solid #d0d7de; padding: 8px 10px; white-space: nowrap;">Date</th>
                    <th style="border: 1px solid #d0d7de; padding: 8px 10px; white-space: nowrap;">Who</th>
                    <th style="border: 1px solid #d0d7de; padding: 8px 10px;">What (Feature / Launch)</th>
                    <th style="border: 1px solid #d0d7de; padding: 8px 10px; text-align: center;">Side</th>
                    <th style="border: 1px solid #d0d7de; padding: 8px 10px; text-align: center;">Ref</th>
                </tr>
            </thead>
            <tbody>{main_rows_html}</tbody>
        </table>

        <h3 style="color: #0969da; margin-top: 28px; margin-bottom: 8px; font-size: 16px;">
            Section 3: What I'd Like Us to Act On
        </h3>
        <div style="font-size: 14px; color: #1f2328;">
            {action_items_email_html}
        </div>

        <h3 style="color: #57606a; margin-top: 30px; margin-bottom: 10px; font-size: 15px;">
            Section 4: Appendix: Low-Signal, Older &amp; Static Items
        </h3>
        <table style="border-collapse: collapse; width: 100%; font-size: 12.5px;">
            <thead>
                <tr style="background-color: #f6f8fa; text-align: left; color: #424a53;">
                    <th style="border: 1px solid #d0d7de; padding: 7px 9px; white-space: nowrap;">Scan Date</th>
                    <th style="border: 1px solid #d0d7de; padding: 7px 9px; white-space: nowrap;">Who</th>
                    <th style="border: 1px solid #d0d7de; padding: 7px 9px;">Item Summary</th>
                    <th style="border: 1px solid #d0d7de; padding: 7px 9px; white-space: nowrap;">Filter Reason</th>
                    <th style="border: 1px solid #d0d7de; padding: 7px 9px; text-align: center;">Ref</th>
                </tr>
            </thead>
            <tbody>{appendix_rows_html}</tbody>
        </table>

        <h3 style="color: #0969da; margin-top: 28px; margin-bottom: 8px; font-size: 15px;">
            Section 5: References &amp; Citations
        </h3>
        {refs_section_html}

        <p style="margin-top: 28px; font-size: 12px; color: #57606a; border-top: 1px solid #d0d7de; padding-top: 12px;">
            <a href="{safe_sheet_url}" style="color: #0969da; text-decoration: none;">Open live tracking workbook on Google Sheets</a>
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
