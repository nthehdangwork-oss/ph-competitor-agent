import os
import sys
import re
import urllib.parse
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv
import gspread
from google.oauth2.service_account import Credentials
import feedparser
import trafilatura
from googlenewsdecoder import new_decoderv1
from google_play_scraper import app as play_app
from openai import OpenAI

from notifier import (
    send_telegram_2day_digest,
    send_telegram_biweekly_report,
    send_email_biweekly_report
)

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
SHEET_NAME = os.getenv("SHEET_NAME", "PH_RideHailing_Competitor_Tracking")

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]
SHEET_URL = os.getenv("SHEET_URL", "")

gc = None
client = None


def init_clients():
    """Khởi tạo Google Sheets client và OpenAI client khi file cấu hình đã sẵn sàng."""
    global gc, client
    if gc is not None and client is not None:
        return

    errors = []
    if not os.path.exists('service_account.json'):
        errors.append("❌ Thiếu file 'service_account.json' trong thư mục ph-competitor-agent/.")
    if not OPENAI_API_KEY or "sk-proj-xxxx" in OPENAI_API_KEY:
        errors.append("❌ Chưa cấu hình OPENAI_API_KEY hợp lệ trong file .env.")

    if errors:
        print("\n" + "=" * 70)
        print("⚠️  CHƯA THỂ CHẠY AGENT - THIẾU THÔNG TIN CẤU HÌNH BẮT BUỘC:")
        for err in errors:
            print(f"   {err}")
        print("\n👉 Vui lòng xem hướng dẫn chi tiết để thiết lập tài khoản & file .env.")
        print("=" * 70 + "\n")
        sys.exit(1)

    creds = Credentials.from_service_account_file('service_account.json', scopes=SCOPES)
    gc = gspread.authorize(creds)
    client = OpenAI(api_key=OPENAI_API_KEY)

# 1. Truy vấn từ khóa theo đối thủ & Cơ quan quản lý (LTFRB / DOTr)
PH_KEYWORD_SOURCES = [
    {"competitor": "Grab PH", "query": '"Grab Philippines" OR "GrabCar" AND (LTFRB OR TNVS OR driver OR fare OR surge)'},
    {"competitor": "Move It", "query": '"Move It" AND (Philippines OR rider OR "MC taxi" OR LTFRB)'},
    {"competitor": "Angkas", "query": '"Angkas" AND (rider OR biker OR fare OR LTFRB OR "motorcycle taxi")'},
    {"competitor": "JoyRide PH", "query": '"JoyRide" AND (Philippines OR rider OR driver OR LTFRB OR "MC taxi")'},
    {"competitor": "inDrive PH", "query": '"inDrive" AND (Philippines OR LTFRB OR TNVS OR driver OR fare)'},
    {"competitor": "LTFRB", "query": '("LTFRB" OR "DOTr") AND (TNVS OR "motorcycle taxi" OR "surge pricing" OR "fare matrix" OR Grab OR Angkas OR "JoyRide" OR "Move It" OR inDrive)'}
]

# 2. Báo chí kinh tế & truyền thông chính thống uy tín Philippines
PH_REPUTABLE_MEDIA_SOURCES = [
    {"source": "BusinessWorld", "query": 'site:bworldonline.com (Grab OR Angkas OR "JoyRide" OR "Move It" OR inDrive OR LTFRB OR "motorcycle taxi" OR TNVS)'},
    {"source": "Philstar", "query": 'site:philstar.com (Grab OR Angkas OR "JoyRide" OR "Move It" OR inDrive OR LTFRB OR "motorcycle taxi" OR TNVS)'},
    {"source": "Rappler", "query": 'site:rappler.com (Grab OR Angkas OR "JoyRide" OR "Move It" OR inDrive OR LTFRB OR "motorcycle taxi" OR TNVS)'},
    {"source": "GMA News", "query": 'site:gmanetwork.com (Grab OR Angkas OR "JoyRide" OR "Move It" OR inDrive OR LTFRB OR "motorcycle taxi" OR TNVS)'},
    {"source": "Inquirer", "query": 'site:inquirer.net (Grab OR Angkas OR "JoyRide" OR "Move It" OR inDrive OR LTFRB OR "motorcycle taxi" OR TNVS)'}
]

# 3. Kênh theo dõi trực tiếp các website chính thức của đối thủ
PH_OFFICIAL_BRAND_QUERIES = [
    {"competitor": "Grab PH", "query": 'site:grab.com/ph/ (press OR driver OR "GrabCar" OR fare OR "Move It")'},
    {"competitor": "inDrive PH", "query": 'site:indrive.com/en-ph/ ("fair services" OR driver OR "city rides" OR fare)'},
    {"competitor": "JoyRide PH", "query": 'site:joyride.com.ph (driver OR "MC Taxi" OR fare OR "JoyRide Car")'},
    {"competitor": "Angkas", "query": 'site:angkas.com (biker OR rider OR safety OR fare)'},
    {"competitor": "Move It", "query": 'site:moveit.com.ph (rider OR fare OR safety OR "motorcycle taxi")'}
]

# 4. Danh sách ứng dụng theo dõi trên Google Play Store Philippines (gl='ph')
PH_PLAYSTORE_APPS = [
    {"competitor": "Grab PH", "type": "Driver App", "app_id": "com.grabtaxi.driver2"},
    {"competitor": "Grab PH", "type": "Customer App", "app_id": "com.grabtaxi.passenger"},
    {"competitor": "Angkas", "type": "Driver App", "app_id": "com.angkas.drivernew"},
    {"competitor": "Angkas", "type": "Customer App", "app_id": "com.angkas.customer"},
    {"competitor": "JoyRide PH", "type": "Driver App", "app_id": "com.joyride.driverv3"},
    {"competitor": "JoyRide PH", "type": "Customer App", "app_id": "com.joyride.rider"},
    {"competitor": "Move It", "type": "Customer App", "app_id": "com.moveit.app.customer"},
    {"competitor": "inDrive PH", "type": "Customer & Driver App", "app_id": "sinet.startup.inDriver"}
]

# 5. Danh sách ứng dụng theo dõi trên Apple App Store Philippines (iOS Lookup API)
PH_APPSTORE_APPS = [
    {"competitor": "Move It", "type": "iOS App", "app_id": "1481198245"},
    {"competitor": "Grab PH", "type": "iOS App", "app_id": "647268330"},
    {"competitor": "inDrive PH", "type": "iOS App", "app_id": "780125801"}
]


def detect_competitor(title, content=""):
    """Tự động nhận diện đối thủ từ tiêu đề và nội dung bài viết."""
    text = (title + " " + content).lower()
    if "move it" in text or "moveit" in text:
        return "Move It"
    if "angkas" in text:
        return "Angkas"
    if "joyride" in text or "joy ride" in text:
        return "JoyRide PH"
    if "indrive" in text or "in-drive" in text:
        return "inDrive PH"
    if "grab" in text:
        return "Grab PH"
    if "ltfrb" in text or "dotr" in text:
        return "LTFRB"
    return "Marketplace"


def crawl_grab_press_articles(existing_urls):
    """Crawl trực tiếp các thông cáo báo chí mới từ Press Centre của Grab Philippines & Move It."""
    press_urls = [
        {"url": "https://www.grab.com/ph/press/", "comp": "Grab PH"},
        {"url": "https://www.grab.com/ph/press/others/", "comp": "Move It"}
    ]
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
    articles = []

    for item in press_urls:
        try:
            r = requests.get(item["url"], headers=headers, timeout=10)
            if r.status_code != 200:
                continue
            links = list(set([
                m for m in re.findall(r'https://www\.grab\.com/ph/press/[a-z0-9\-]+/[a-z0-9\-]+/?', r.text)
                if len(m) > 40 and not m.endswith('/page/')
            ]))
            for link in links[:3]:
                if link in existing_urls:
                    continue
                d = trafilatura.fetch_url(link)
                content = trafilatura.extract(d)
                title_m = re.search(r'<title>(.*?)</title>', d or '', re.I)
                title = title_m.group(1).replace(' - Grab PH', '').replace(' | Grab PH', '').strip() if title_m else link
                comp = item["comp"] if "move it" not in title.lower() else "Move It"
                articles.append({
                    "competitor": comp,
                    "title": title,
                    "url": link,
                    "content": content or title,
                    "source": "Grab PH Official Press"
                })
        except Exception as e:
            print(f"[Grab Press] Bỏ qua {item['url']}: {e}")

    return articles


def fetch_appstore_updates(existing_urls):
    """Quét release notes mới nhất từ Apple App Store Philippines qua iTunes Lookup API."""
    updates = []
    for app in PH_APPSTORE_APPS:
        try:
            r = requests.get(f"https://itunes.apple.com/lookup?id={app['app_id']}&country=ph", timeout=10)
            data = r.json()
            results = data.get("results", [])
            if not results:
                continue
            app_info = results[0]
            version = app_info.get("version", "Latest")
            release_notes = app_info.get("releaseNotes", "")
            track_url = app_info.get("trackViewUrl", f"https://apps.apple.com/ph/app/id{app['app_id']}")
            app_url = f"{track_url}#v{version}"

            if not release_notes or app_url in existing_urls:
                continue

            title = f"{app['competitor']} {app['type']} v{version} Release Notes"
            updates.append({
                "competitor": app["competitor"],
                "title": title,
                "url": app_url,
                "content": release_notes,
                "source": "Apple App Store PH"
            })
        except Exception as e:
            print(f"[AppStore] Bỏ qua app {app['competitor']}: {e}")

    return updates


def resolve_google_news_url(rss_link):
    """Giải mã link redirect của Google News sang URL gốc của tờ báo Philippines."""
    try:
        decoded = new_decoderv1(rss_link, interval=1)
        if decoded.get("status"):
            return decoded["decoded_url"]
    except Exception:
        pass
    return rss_link


def init_sheets():
    """Tự động kiểm tra và khởi tạo 3 Tab chuẩn trên Google Sheets nếu chưa có."""
    init_clients()
    try:
        if SHEET_URL and "YOUR_SHEET_ID" not in SHEET_URL and SHEET_URL.startswith("http"):
            spreadsheet = gc.open_by_url(SHEET_URL)
        else:
            spreadsheet = gc.open(SHEET_NAME)
    except Exception as e:
        print(f"\n❌ [LỖI TRUY CẬP GOOGLE SHEETS]: Không thể mở bảng tính: {e}")
        print("👉 Vui lòng kiểm tra:")
        print(f"   1. Đã chia sẻ (Share) Google Sheet cho Email trong 'service_account.json' với quyền 'Editor' chưa?")
        print(f"   2. Tên Sheet '{SHEET_NAME}' hoặc SHEET_URL trong file .env đã chính xác chưa?\n")
        sys.exit(1)
    
    # Tab 1: Raw_Data (Cập nhật 2 ngày/lần)
    try:
        raw_sheet = spreadsheet.worksheet('Raw_Data')
    except gspread.WorksheetNotFound:
        raw_sheet = spreadsheet.add_worksheet(title='Raw_Data', rows=2000, cols=8)
        raw_sheet.append_row([
            'Timestamp_ISO', 'Date', 'Who', 'What',
            'Side', 'Source_Name', 'Source_Title', 'Source_URL'
        ])
        
    # Tab 2: Current_Report_Table (Bảng sạch đã đánh số [1], [2]... cho kỳ 2 tuần hiện tại)
    try:
        table_sheet = spreadsheet.worksheet('Current_Report_Table')
    except gspread.WorksheetNotFound:
        table_sheet = spreadsheet.add_worksheet(title='Current_Report_Table', rows=200, cols=6)
        table_sheet.append_row([
            'Date', 'Who', 'What', 'Side', 'Ref', 'Full_Reference_Citation'
        ])

    # Tab 3: BiWeekly_Overview_Archive (Lưu trữ lịch sử các kỳ báo cáo 2 tuần)
    try:
        archive_sheet = spreadsheet.worksheet('BiWeekly_Overview_Archive')
    except gspread.WorksheetNotFound:
        archive_sheet = spreadsheet.add_worksheet(title='BiWeekly_Overview_Archive', rows=200, cols=6)
        archive_sheet.append_row([
            'Report_Date', 'Report_Header', 'Bottom_Line',
            'Feature_Launches_Count', 'What_Id_Like_Us_To_Act_On', 'References_Block'
        ])
        
    return raw_sheet, table_sheet, archive_sheet


def analyze_ph_item(competitor, source_name, title, raw_text):
    """Gọi gpt-4o-mini tóm tắt tin tức sang trường What và phân loại Side (kèm fallback an toàn)."""
    try:
        prompt = f"""
    Bạn là IT Product Owner theo dõi đối thủ ngành Gọi xe (Ride-hailing) tại Philippines.
    Hãy phân tích thông tin sau về '{competitor}' (Nguồn: {source_name}):

    Tiêu đề: {title}
    Nội dung: {raw_text[:3000]}

    Trả về kết quả trên MỘT DÒNG DUY NHẤT gồm 2 phần ngăn cách bởi dấu '|':
    Phần 1 (What): Tóm tắt cụ thể bằng tiếng Việt (dưới 55 từ) về tính năng mới, thay đổi UI/UX (Demand Heatmap, Dispatch Modal, Pick-up Pins), quy trình Onboarding/eKYC (Professional License, NBI Clearance), ví GCash/Maya, cơ chế Rating/Auto-forgiveness, mức giá/chiết khấu hoặc quyết định của LTFRB.
    Phần 2 (Side): Chỉ chọn đúng 1 trong 5 nhãn sau: Driver / Passenger / Both / Marketplace / Regulatory
    """
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        return res.choices[0].message.content.strip()
    except Exception as e:
        print(f"   [OpenAI Notice] Lỗi gọi API ({e}). Đang dùng tiêu đề gốc làm tóm tắt dự phòng.")
        clean_desc = title[:120] if len(title) > 10 else raw_text[:120]
        return f"{clean_desc} | Marketplace"


def job_collect_ph_data():
    """Luồng 1 (2 ngày/lần): Quét báo chí chính thống PH, Brand Portals, Grab Press, Play Store & App Store."""
    print(f"[{datetime.now()}] Bắt đầu quét dữ liệu thị trường Philippines (Chu kỳ 2 ngày)...")
    raw_sheet, _, _ = init_sheets()
    existing_urls = set(raw_sheet.col_values(8))

    now = datetime.now()
    iso_date = now.strftime("%Y-%m-%d")
    display_date = now.strftime("%d %b %Y")
    new_rows = []

    # 1. Quét báo chí chính thống uy tín Philippines (BusinessWorld, Philstar, Rappler, GMA, Inquirer)
    print("-> 1/6: Đang quét các nguồn báo chí uy tín Philippines (BusinessWorld, Philstar, Rappler, GMA, Inquirer)...")
    for item in PH_REPUTABLE_MEDIA_SOURCES:
        source_label = item["source"]
        rss_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(item['query'])}&hl=en-PH&gl=PH&ceid=PH:en"
        feed = feedparser.parse(rss_url)
        for entry in feed.entries[:3]:
            real_url = resolve_google_news_url(entry.link)
            if real_url in existing_urls or entry.link in existing_urls:
                continue
            clean_title = entry.title.rsplit(" - ", 1)[0].strip()
            downloaded = trafilatura.fetch_url(real_url)
            content = trafilatura.extract(downloaded)
            text_to_analyze = content if (content and len(content) > 150) else entry.get("summary", clean_title)
            comp = detect_competitor(clean_title, text_to_analyze)
            ai_out = analyze_ph_item(comp, source_label, clean_title, text_to_analyze)
            parts = [p.strip() for p in ai_out.split("|")]
            what_text = parts[0] if len(parts) > 0 else ai_out
            side_label = parts[1] if len(parts) > 1 else "Marketplace"
            new_rows.append([iso_date, display_date, comp, what_text, side_label, source_label, clean_title, real_url])
            existing_urls.add(real_url)

    # 2. Quét từ khóa đối thủ & cơ quan quản lý (LTFRB / DOTr)
    print("-> 2/6: Đang quét từ khóa đối thủ & cơ quan quản lý (LTFRB / DOTr)...")
    for item in PH_KEYWORD_SOURCES:
        comp = item["competitor"]
        rss_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(item['query'])}&hl=en-PH&gl=PH&ceid=PH:en"
        feed = feedparser.parse(rss_url)
        for entry in feed.entries[:3]:
            real_url = resolve_google_news_url(entry.link)
            if real_url in existing_urls or entry.link in existing_urls:
                continue
            source_name = entry.get("source", {}).get("title", "News PH")
            clean_title = entry.title.rsplit(" - ", 1)[0].strip()
            downloaded = trafilatura.fetch_url(real_url)
            content = trafilatura.extract(downloaded)
            text_to_analyze = content if (content and len(content) > 150) else entry.get("summary", clean_title)
            ai_out = analyze_ph_item(comp, source_name, clean_title, text_to_analyze)
            parts = [p.strip() for p in ai_out.split("|")]
            what_text = parts[0] if len(parts) > 0 else ai_out
            side_label = parts[1] if len(parts) > 1 else "Marketplace"
            new_rows.append([iso_date, display_date, comp, what_text, side_label, source_name, clean_title, real_url])
            existing_urls.add(real_url)

    # 3. Quét website chính thức của đối thủ (Official Brand Portals)
    print("-> 3/6: Đang quét trang chính thức của Grab, inDrive, JoyRide, Angkas, Move It...")
    for item in PH_OFFICIAL_BRAND_QUERIES:
        comp = item["competitor"]
        rss_url = f"https://news.google.com/rss/search?q={urllib.parse.quote(item['query'])}&hl=en-PH&gl=PH&ceid=PH:en"
        feed = feedparser.parse(rss_url)
        for entry in feed.entries[:2]:
            real_url = resolve_google_news_url(entry.link)
            if real_url in existing_urls or entry.link in existing_urls:
                continue
            source_name = f"{comp} Official Portal"
            clean_title = entry.title.rsplit(" - ", 1)[0].strip()
            downloaded = trafilatura.fetch_url(real_url)
            content = trafilatura.extract(downloaded)
            text_to_analyze = content if (content and len(content) > 150) else entry.get("summary", clean_title)
            ai_out = analyze_ph_item(comp, source_name, clean_title, text_to_analyze)
            parts = [p.strip() for p in ai_out.split("|")]
            what_text = parts[0] if len(parts) > 0 else ai_out
            side_label = parts[1] if len(parts) > 1 else "Marketplace"
            new_rows.append([iso_date, display_date, comp, what_text, side_label, source_name, clean_title, real_url])
            existing_urls.add(real_url)

    # 4. Crawl trực tiếp thông cáo báo chí từ Grab Press Centre & Move It
    print("-> 4/6: Đang crawl trực tiếp Grab Philippines Press Centre & Move It...")
    direct_grab_press = crawl_grab_press_articles(existing_urls)
    for article in direct_grab_press:
        if article["url"] in existing_urls:
            continue
        ai_out = analyze_ph_item(article["competitor"], article["source"], article["title"], article["content"])
        parts = [p.strip() for p in ai_out.split("|")]
        what_text = parts[0] if len(parts) > 0 else ai_out
        side_label = parts[1] if len(parts) > 1 else "Marketplace"
        new_rows.append([iso_date, display_date, article["competitor"], what_text, side_label, article["source"], article["title"], article["url"]])
        existing_urls.add(article["url"])

    # 5. Quét Google Play Store Philippines Release Notes (gl='ph')
    print("-> 5/6: Đang quét Google Play Store Philippines...")
    for app_cfg in PH_PLAYSTORE_APPS:
        try:
            details = play_app(app_cfg["app_id"], lang='en', country='ph')
            version = details.get("version", "Latest")
            recent_changes = details.get("recentChanges")
            play_url = f"https://play.google.com/store/apps/details?id={app_cfg['app_id']}&gl=ph#v{version}"
            if not recent_changes or play_url in existing_urls:
                continue
            source_title = f"{app_cfg['competitor']} {app_cfg['type']} v{version} Release Notes"
            ai_out = analyze_ph_item(app_cfg["competitor"], "Google Play Store PH", source_title, recent_changes)
            parts = [p.strip() for p in ai_out.split("|")]
            what_text = parts[0] if len(parts) > 0 else recent_changes
            side_label = parts[1] if len(parts) > 1 else ("Driver" if "Driver" in app_cfg["type"] else "Passenger")
            new_rows.append([iso_date, display_date, app_cfg["competitor"], what_text, side_label, "Google Play Store PH", source_title, play_url])
            existing_urls.add(play_url)
        except Exception as e:
            print(f"[PlayStore] Bỏ qua App {app_cfg['app_id']}: {e}")

    # 6. Quét Apple App Store Philippines Release Notes (iOS)
    print("-> 6/6: Đang quét Apple App Store Philippines...")
    appstore_updates = fetch_appstore_updates(existing_urls)
    for update in appstore_updates:
        if update["url"] in existing_urls:
            continue
        ai_out = analyze_ph_item(update["competitor"], update["source"], update["title"], update["content"])
        parts = [p.strip() for p in ai_out.split("|")]
        what_text = parts[0] if len(parts) > 0 else ai_out
        side_label = parts[1] if len(parts) > 1 else "Marketplace"
        new_rows.append([iso_date, display_date, update["competitor"], what_text, side_label, update["source"], update["title"], update["url"]])
        existing_urls.add(update["url"])

    if new_rows:
        raw_sheet.append_rows(new_rows)
        print(f"Đã ghi {len(new_rows)} dòng mới vào Tab Raw_Data.")
    else:
        print("Không có tin tức mới nào trong 48 giờ qua.")
        
    send_telegram_2day_digest(new_rows)


def job_generate_ph_biweekly_overview():
    """Luồng 2 (2 tuần/lần): Lọc tin 14 ngày, đánh số [1], [2], tạo File Doc Tiếng Việt và gửi báo cáo."""
    print(f"[{datetime.now()}] Đang tạo báo cáo Overview 2 tuần...")
    raw_sheet, table_sheet, archive_sheet = init_sheets()
    all_records = raw_sheet.get_all_records()
    
    now = datetime.now()
    cutoff_date = now - timedelta(days=14)
    filtered_rows = []
    
    for r in all_records:
        try:
            if datetime.strptime(str(r['Timestamp_ISO']), "%Y-%m-%d") >= cutoff_date:
                filtered_rows.append(r)
        except ValueError:
            continue

    if not filtered_rows:
        print("Không có dữ liệu mới trong 14 ngày qua. Tạo báo cáo trạng thái 'Không có cập nhật'...")
        report_header = (
            f"Cập nhật tính năng & chính sách mới, {cutoff_date.strftime('%d %b')} đến "
            f"{now.strftime('%d %b %Y')} | Toàn thị trường"
        )
        bottom_line = (
            f"• Trong 14 ngày qua (từ {cutoff_date.strftime('%d/%m/%Y')} đến {now.strftime('%d/%m/%Y')}), "
            "hệ thống không ghi nhận cập nhật tính năng mới, thay đổi chính sách cước phí hoặc quy định pháp lý nào từ các đối thủ gọi xe (Grab PH, Move It, Angkas, JoyRide, inDrive) và cơ quan LTFRB.\n"
            "• Các ứng dụng tài xế và khách hàng trên Google Play Store không phát hành phiên bản cập nhật lớn trong chu kỳ này."
        )
        action_items = (
            "• Tiếp tục duy trì giám sát tự động các kênh báo chí Philippines và Play Store theo chu kỳ 2 ngày/lần.\n"
            "• Chủ động rà soát thêm các chiến dịch truyền thông hoặc ưu đãi cục bộ tại Metro Manila/Cebu nếu cần."
        )
        table_sheet_rows = [
            ['Date', 'Who', 'What', 'Side', 'Ref', 'Full_Reference_Citation'],
            [now.strftime('%d %b %Y'), 'Toàn thị trường', 'Không ghi nhận biến động mới trong 14 ngày qua', 'Marketplace', '-', 'N/A']
        ]
        md_table_block = (
            "| Date | Who | What | Side | Ref |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n"
            f"| {now.strftime('%d %b %Y')} | Toàn thị trường | Không ghi nhận biến động mới trong 14 ngày qua | Marketplace | - |"
        )
        refs_block = "* Không có tài liệu tham khảo mới trong kỳ báo cáo này."

        # Cập nhật Tab 2 (Current_Report_Table)
        table_sheet.clear()
        table_sheet.update(range_name='A1', values=table_sheet_rows)

        # Lưu trữ vào Tab 3 (BiWeekly_Overview_Archive)
        archive_sheet.append_row([
            now.strftime("%Y-%m-%d"),
            report_header,
            bottom_line,
            0,
            action_items,
            refs_block
        ])

        # Xuất ra File Doc/Markdown chuẩn tiếng Việt
        doc_content = (
            f"## {report_header}\n\n"
            f"**Điểm cốt lõi (Bottom line)**\n{bottom_line}\n\n"
            f"**Tính năng và sản phẩm mới ra mắt (Feature and product launches)**\n\n{md_table_block}\n\n"
            f"**Đề xuất hành động cần thực hiện (What I'd like us to act on)**\n{action_items}\n\n"
            f"**Tài liệu tham khảo (References)**\n{refs_block}\n"
        )
        filename = f"PH_Competitor_Report_{now.strftime('%Y%m%d')}.md"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(doc_content)

        send_telegram_biweekly_report(report_header, bottom_line, action_items, filename)
        send_email_biweekly_report(report_header, bottom_line, [], action_items)
        print(f"Đã xuất thành công báo cáo trạng thái {filename} và gửi thông báo!")
        return

    table_sheet_rows = [['Date', 'Who', 'What', 'Side', 'Ref', 'Full_Reference_Citation']]
    md_table_lines = [
        "| Date | Who | What | Side | Ref |",
        "| :--- | :--- | :--- | :--- | :--- |"
    ]
    references_lines, ai_context_lines, competitors_seen = [], [], []

    for idx, r in enumerate(filtered_rows, start=1):
        ref_tag = f"[{idx}]"
        who = r['Who']
        if who not in competitors_seen:
            competitors_seen.append(who)
            
        citation_str = f"{ref_tag} {r['Source_Name']} — *{r['Source_Title']}* ({r['Date']}): {r['Source_URL']}"
        table_sheet_rows.append([r['Date'], who, r['What'], r['Side'], ref_tag, citation_str])
        md_table_lines.append(f"| {r['Date']} | {who} | {r['What']} | {r['Side']} | {ref_tag} |")
        references_lines.append(f"* {citation_str}")
        ai_context_lines.append(f"{ref_tag} ({r['Date']}) {who} [{r['Side']}]: {r['What']}")

    # Cập nhật Tab 2 (Current_Report_Table)
    table_sheet.clear()
    table_sheet.update(range_name='A1', values=table_sheet_rows)

    ai_input = "\n".join(ai_context_lines)
    prompt = f"""
    Bạn là Product Owner mảng Ride-hailing (Driver Onboarding, Driver App, Customer App) tại thị trường Philippines.
    Dưới đây là danh sách các cập nhật của đối thủ trong 14 ngày qua (đã được đánh số tham chiếu [1], [2]...):

    {ai_input}

    Hãy viết 2 phần sau hoàn toàn bằng tiếng Việt, ngăn cách nhau bởi ký hiệu '|||'.
    BẮT BUỘC gắn mã số tham chiếu (ví dụ: [1], [2]) ở cuối mỗi ý để liên kết chính xác với bảng dữ liệu:

    Phần 1 (Điểm cốt lõi - Bottom line):
    Viết các gạch đầu dòng tóm tắt nhanh những xu hướng chiến lược và thay đổi lớn nhất trong 14 ngày qua về sản phẩm (UI/UX, eKYC/Onboarding), chính sách giữ chân tài xế và pháp lý LTFRB.
    |||
    Phần 2 (Đề xuất hành động cần thực hiện - What I'd like us to act on):
    Viết các nhận xét và hướng hành động cụ thể cho đội ngũ theo từng ý gạch đầu dòng rõ ràng, tuyệt đối KHÔNG kèm mức độ ưu tiên P0/P1.
    """

    try:
        res = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2
        )
        parts = [p.strip() for p in res.choices[0].message.content.split("|||")]
        bottom_line = parts[0] if len(parts) > 0 else ""
        action_items = parts[1] if len(parts) > 1 else ""
    except Exception as e:
        print(f"[OpenAI Notice] Lỗi gọi GPT-4o ({e}). Đang tạo tóm tắt dự phòng từ bảng dữ liệu.")
        bottom_line = f"• Trong 14 ngày qua ghi nhận {len(filtered_rows)} cập nhật từ các đối thủ: {', '.join(competitors_seen)}."
        action_items = "• Tiếp tục theo dõi phản ứng của thị trường và động thái tiếp theo từ các đối thủ."

    report_header = (
        f"Cập nhật tính năng & chính sách mới, {cutoff_date.strftime('%d %b')} đến "
        f"{now.strftime('%d %b %Y')} | {', '.join(competitors_seen)}"
    )
    refs_block = "\n".join(references_lines)
    md_table_block = "\n".join(md_table_lines)

    # Lưu trữ vào Tab 3 (BiWeekly_Overview_Archive)
    archive_sheet.append_row([
        now.strftime("%Y-%m-%d"),
        report_header,
        bottom_line,
        len(filtered_rows),
        action_items,
        refs_block
    ])

    # Xuất ra File Doc/Markdown chuẩn tiếng Việt
    doc_content = (
        f"## {report_header}\n\n"
        f"**Điểm cốt lõi (Bottom line)**\n{bottom_line}\n\n"
        f"**Tính năng và sản phẩm mới ra mắt (Feature and product launches)**\n\n{md_table_block}\n\n"
        f"**Đề xuất hành động cần thực hiện (What I'd like us to act on)**\n{action_items}\n\n"
        f"**Tài liệu tham khảo (References)**\n{refs_block}\n"
    )
    filename = f"PH_Competitor_Report_{now.strftime('%Y%m%d')}.md"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(doc_content)

    send_telegram_biweekly_report(report_header, bottom_line, action_items, filename)
    send_email_biweekly_report(report_header, bottom_line, filtered_rows, action_items)
    print(f"Đã xuất thành công báo cáo {filename} và gửi thông báo!")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "collect"
    if mode == "collect":
        job_collect_ph_data()
    elif mode == "overview":
        job_generate_ph_biweekly_overview()
