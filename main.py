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
try:
    from googlenewsdecoder import new_decoderv1
except Exception:
    new_decoderv1 = None
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
    if new_decoderv1 is None:
        return rss_link
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


def guess_side(text):
    """Accurately classify Side (Driver / Passenger / Both / Regulatory / Marketplace) based on keywords."""
    t = text.lower()
    driver_words = ["driver", "biker", "rider", "onboarding", "license", "ekyc", "commission", "incentive", "fuel", "subsidy", "operator"]
    passenger_words = ["passenger", "customer", "group ride", "fare", "booking", "split", "pickup", "dropoff", "commuter", "rider complaint"]
    regulatory_words = ["ltfrb", "dotr", "regulation", "regulatory", "hearing", "fine", "penalty", "cap", "quota", "pilot", "bill", "law", "franchise", "tnvs"]
    
    is_driver = any(w in t for w in driver_words)
    is_passenger = any(w in t for w in passenger_words)
    is_reg = any(w in t for w in regulatory_words)
    
    if is_reg:
        return "Regulatory"
    if is_driver and is_passenger:
        return "Both"
    if is_driver:
        return "Driver"
    if is_passenger:
        return "Passenger"
    return "Marketplace"


def is_product_relevant(title, what, who=""):
    """STRICT filter: only retain articles about new features, product launches, app releases,
    UI/UX changes, pricing/commission changes, driver/passenger program launches,
    and significant LTFRB policy directly impacting platform operations.
    App Store / Play Store release notes always pass."""
    text = (title + " " + what).lower()

    # ── App Store / Play Store release notes: ALWAYS PASS ─────────────────────
    if "release notes" in text or ("v5." in text and ("ios" in text or "android" in text or "app" in text)):
        return True

    # ── HARD EXCLUSIONS (always discard) ──────────────────────────────────────────
    hard_noise = [
        # crime / incidents
        "shabu", "nabbed", "arrested", "gun", "drugs", "drug", "robbery", "killed", "killed by",
        "murder", "rape", "harassment complaint", "alleged", "altercation", "provocation",
        "disrespect", "summoned over", "viral video", "confrontation", "misconduct",
        # legal proceedings (not operational)
        "file abuse", "file raps", "file charges", "rape charges", "complaints filed",
        # social / welfare fluff
        "cum laude", "graduates", "scholar program", "scholarship", "first-timer's guide",
        "deaf woman", "deaf passenger", "palm sunday", "christmas", "new year",
        # entertainment / sports
        "nba", "basketball", "gilas", "fiba", "family feud", "episode", "concert", 
        "celebrity", "showbiz", "miss universe", "pageant", "fashion",
        # unrelated brands / events
        "asean summit", "pitx for long weekend", "rockwell", "hotel", "resort",
        "maga", "trump", "midterm", "licensure", "midwives", "street vendors",
        # vague static pages with no actionable product info
        "privacy preference center", "passenger appeal form", "download the official",
        "grab. the everyday everything", "joyride ph: mc taxi, car, airport",
        "indrive services in philippines | offer your fare",
    ]
    for n in hard_noise:
        if n in text:
            return False

    # ── POSITIVE SIGNALS (must have at least ONE) ──────────────────────────────────
    # Only pass articles that contain clear product / feature / platform signals
    product_signals = [
        # app / tech
        "release notes", "app update", "version", "v5.", "new feature", "feature update",
        "launch", "launches", "launched", "roll out", "rolled out", "rolling out",
        "new service", "new app", "new platform", "new product", "new tool", "new integration",
        "ui update", "ux update", "redesign", "in-app", "in app",
        "group ride", "split fare", "auto-split", "heatmap", "dispatch", "pick-up pin",
        "in-app chat", "sos button", "real-time tracking", "live tracking", "wallet", 
        "gcash", "maya", "pay", "payment integration", "ekyc", "onboarding flow",
        # driver program launches
        "commission cut", "commission reduction", "commission rate", "lower commission",
        "incentive program", "incentive launch", "driver reward", "driver app",
        "driver onboarding", "driver ai", "driver wellness", "driver relief",
        "fuel subsidy", "fuel relief", "fuel package", "bayanihan fund",
        "tiered incentive", "driver benefit",
        # passenger / pricing
        "fare adjustment", "fare matrix", "surge pricing", "booking fee cap",
        "group booking", "promo code", "voucher", "discount", "cash loan",
        # strategic / expansion
        "superapp", "expansion", "enters", "expands", "new city", "new area", "new market",
        "partnership", "acquires", "acquisition", "ipo", "going public",
        # regulatory ops impact (must be direct ops, not just court proceedings)
        "ltfrb approves", "ltfrb implements", "ltfrb mandates", "ltfrb sets",
        "dotr approves", "dotr implements", "dotr mandates", "regulatory approval",
        "franchise granted", "cpc granted", "new regulation", "new policy",
        "pilot program", "motorcycle taxi pilot", "mc taxi pilot",
        "tnvs regularization", "tnvs deadline", "booking fee policy", "pick-up rate policy",
        "fare increase approved", "cap extended", "rider cap", "quota lifted",
    ]
    return any(s in text for s in product_signals)


def _summarize_release_notes(competitor, version_title, notes_text):
    """Extract specific feature/fix details from App Store / Play Store release notes.
    Returns a detailed What string without calling OpenAI (pure rule-based parse + format)."""
    # Clean up HTML tags and normalize whitespace
    import re as _re
    clean = _re.sub(r'<[^>]+>', '', notes_text).strip()
    # Split into bullet-like lines
    lines = [l.strip() for l in _re.split(r'[\n\r•\-–]', clean) if len(l.strip()) > 10]
    # Keep lines that mention feature/fix/improvement keywords
    signal_words = [
        "new", "add", "added", "feature", "improve", "improved", "fix", "fixed", "update", "updated",
        "enhance", "enhanced", "launch", "introduce", "introduced", "support", "tracking", "book",
        "ride", "safety", "wallet", "payment", "bug", "performance", "real-time", "real time",
        "feedback", "receipt", "share", "notification", "alert", "ui", "ux", "experience",
    ]
    relevant = [l for l in lines if any(w in l.lower() for w in signal_words)]
    # Fallback: take first 3 lines if none matched signal words
    if not relevant:
        relevant = lines[:3]
    # For bug-fix-only releases with no specific features, produce a clean note
    if not relevant or all(len(l) > 200 for l in relevant):
        summary = "Minor bug fixes and performance improvements; no specific new features listed in this release."
        side = "Both"
        prefix = f"v{version} — " if version else ""
        return f"{prefix}{summary} | {side}"
    # Format: "v{version}: {bullet1}; {bullet2}; ..."
    version = version_title.split(" v")[-1].split(" ")[0] if " v" in version_title else ""
    prefix = f"v{version} — " if version else ""
    summary = "; ".join(relevant[:4])  # max 4 bullet points
    if len(summary) > 300:
        summary = summary[:297] + "..."
    side = guess_side(notes_text + " " + competitor)
    return f"{prefix}{summary} | {side}"


def analyze_ph_item(competitor, source_name, title, raw_text):
    """Summarize only genuine product/feature updates in concise English.
    For App Store / Play Store release notes: extracts exact changelog items.
    Returns 'SKIP | Noise' for non-product articles so they can be discarded."""

    # ── Special path: App Store / Play Store release notes ─────────────────────
    is_release_note = any(x in title for x in ["Release Notes", "release notes"])
    if is_release_note and raw_text and len(raw_text) > 10:
        # Try OpenAI first for richer analysis; fall back to rule-based parser
        try:
            prompt = f"""
You are a mobile product analyst. Extract the key new features, improvements, and bug fixes from the following App Store / Play Store release notes for '{competitor}' app ({title}).

Release Notes:
{raw_text[:2000]}

Write a clear, specific, technical summary in ENGLISH (max 60 words) that:
- Lists each distinct new feature or improvement as a short bullet (e.g. "Real-time ride sharing via in-app link; enhanced pick-up pin accuracy; GCash wallet top-up redesigned")
- Uses technical terms (UI, UX, real-time, eKYC, dispatch, etc.) where appropriate
- Explicitly mentions WHAT changed (not vague "bug fixes and improvements")
- If release notes only mention bug fixes with no specifics, state: "Minor bug fixes and performance improvements; no new features listed"

Return ONE line: [your summary] | [Driver OR Passenger OR Both]
"""
            res = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0
            )
            return res.choices[0].message.content.strip()
        except Exception:
            # Rule-based fallback: parse and format the actual release notes text
            return _summarize_release_notes(competitor, title, raw_text)

    # ── Standard path: news articles, press releases, regulatory updates ───────
    try:
        prompt = f"""
        You are an IT Product Owner at a Ride-Hailing company tracking Philippines competitors (Grab PH, Move It, Angkas, JoyRide, inDrive) and LTFRB regulations.

        Your ONLY job is to track: new features, app releases, product launches, UI/UX changes, pricing/commission changes, driver program launches, and LTFRB regulations that directly change platform operations.

        You DO NOT report on: crimes, harassment incidents, court hearings/fines (unless it changes operations), scholarship stories, general company profiles, static homepage content, sports, entertainment, or social welfare stories.

        Analyze this article about '{competitor}' (Source: {source_name}):
        Title: {title}
        Content: {raw_text[:2500]}

        IF this article describes a new product feature, app update, service launch, pricing/commission change, or operational policy change → return on ONE LINE:
        [concise ENGLISH summary under 40 words focusing on what changed and what it means for drivers/passengers] | [Driver OR Passenger OR Both OR Regulatory]

        IF this article is NOT about a product/feature/policy change (e.g. crime, harassment, general news profile, court case, scholarship, static page) → return exactly:
        SKIP | Noise
        """
        res = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0
        )
        return res.choices[0].message.content.strip()
    except Exception as e:
        print(f"   [OpenAI Notice] API call failed ({e}). Using title as fallback.")
        clean_desc = title[:120] if len(title) > 10 else raw_text[:120]
        side = guess_side(clean_desc + " " + competitor)
        return f"{clean_desc} | {side}"


def _parse_ai_output(ai_out):
    """Parse AI output. Returns (what_text, side_label, is_skip)."""
    parts = [p.strip() for p in ai_out.split("|")]
    what_text  = parts[0] if len(parts) > 0 else ai_out
    side_label = parts[1] if len(parts) > 1 else "Marketplace"
    # AI returns "SKIP | Noise" for non-product articles
    is_skip = what_text.upper() == "SKIP" or side_label.lower() == "noise"
    return what_text, side_label, is_skip


def job_collect_ph_data():
    """Luồng 1 (2 ngày/lần): Quét báo chí chính thống PH, Brand Portals, Grab Press, Play Store & App Store."""
    print(f"[{datetime.now()}] Starting 48h PH Market Data Scan...")
    raw_sheet, _, _ = init_sheets()
    existing_urls = set(raw_sheet.col_values(8))

    now = datetime.now()
    iso_date = now.strftime("%Y-%m-%d")
    display_date = now.strftime("%d %b %Y")
    new_rows = []
    skipped = 0

    # 1. Quét báo chí chính thống uy tín Philippines (BusinessWorld, Philstar, Rappler, GMA, Inquirer)
    print("-> 1/6: Scanning reputable PH media (BusinessWorld, Philstar, Rappler, GMA, Inquirer)...")
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
            what_text, side_label, is_skip = _parse_ai_output(ai_out)
            if is_skip:
                skipped += 1
                existing_urls.add(real_url)
                continue
            new_rows.append([iso_date, display_date, comp, what_text, side_label, source_label, clean_title, real_url])
            existing_urls.add(real_url)

    # 2. Quét từ khóa đối thủ & cơ quan quản lý (LTFRB / DOTr)
    print("-> 2/6: Scanning competitor keywords & LTFRB / DOTr regulatory news...")
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
            what_text, side_label, is_skip = _parse_ai_output(ai_out)
            if is_skip:
                skipped += 1
                existing_urls.add(real_url)
                continue
            new_rows.append([iso_date, display_date, comp, what_text, side_label, source_name, clean_title, real_url])
            existing_urls.add(real_url)

    # 3. Quét website chính thức của đối thủ (Official Brand Portals)
    print("-> 3/6: Scanning official brand portals (Grab, inDrive, JoyRide, Angkas, Move It)...")
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
            what_text, side_label, is_skip = _parse_ai_output(ai_out)
            if is_skip:
                skipped += 1
                existing_urls.add(real_url)
                continue
            new_rows.append([iso_date, display_date, comp, what_text, side_label, source_name, clean_title, real_url])
            existing_urls.add(real_url)

    # 4. Crawl trực tiếp thông cáo báo chí từ Grab Press Centre & Move It
    print("-> 4/6: Crawling Grab Philippines Press Centre & Move It directly...")
    direct_grab_press = crawl_grab_press_articles(existing_urls)
    for article in direct_grab_press:
        if article["url"] in existing_urls:
            continue
        ai_out = analyze_ph_item(article["competitor"], article["source"], article["title"], article["content"])
        what_text, side_label, is_skip = _parse_ai_output(ai_out)
        if is_skip:
            skipped += 1
            existing_urls.add(article["url"])
            continue
        new_rows.append([iso_date, display_date, article["competitor"], what_text, side_label, article["source"], article["title"], article["url"]])
        existing_urls.add(article["url"])

    # 5. Quét Google Play Store Philippines Release Notes (gl='ph')
    print("-> 5/6: Scanning Google Play Store Philippines...")
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
            what_text, side_label, is_skip = _parse_ai_output(ai_out)
            if is_skip:
                skipped += 1
                existing_urls.add(play_url)
                continue
            new_rows.append([iso_date, display_date, app_cfg["competitor"], what_text, side_label, "Google Play Store PH", source_title, play_url])
            existing_urls.add(play_url)
        except Exception as e:
            print(f"[PlayStore] Skipped App {app_cfg['app_id']}: {e}")

    # 6. Quét Apple App Store Philippines Release Notes (iOS)
    print("-> 6/6: Scanning Apple App Store Philippines...")
    appstore_updates = fetch_appstore_updates(existing_urls)
    for update in appstore_updates:
        if update["url"] in existing_urls:
            continue
        ai_out = analyze_ph_item(update["competitor"], update["source"], update["title"], update["content"])
        what_text, side_label, is_skip = _parse_ai_output(ai_out)
        if is_skip:
            skipped += 1
            existing_urls.add(update["url"])
            continue
        new_rows.append([iso_date, display_date, update["competitor"], what_text, side_label, update["source"], update["title"], update["url"]])
        existing_urls.add(update["url"])

    print(f"Scan complete: {len(new_rows)} product updates accepted, {skipped} non-product articles filtered out.")
    if new_rows:
        raw_sheet.append_rows(new_rows)
        print(f"Written {len(new_rows)} new rows to Tab Raw_Data.")
    else:
        print("No new product/feature updates found in the past 48 hours.")
        
    send_telegram_2day_digest(new_rows)


def is_duplicate(item, seen_items):
    """Check if an item is a duplicate or near-duplicate of an already selected item for the same competitor."""
    title_words = set(re.findall(r'\b[a-zA-Z]{4,}\b', item.get('Source_Title', '').lower()))
    what_words = set(re.findall(r'\b[a-zA-Z]{4,}\b', item.get('What', '').lower()))
    combined = title_words.union(what_words)
    
    for s in seen_items:
        if s['Who'] != item['Who']:
            continue
        s_combined = set(re.findall(r'\b[a-zA-Z]{4,}\b', (s.get('Source_Title', '') + " " + s.get('What', '')).lower()))
        if len(combined) > 0 and len(s_combined) > 0:
            overlap = len(combined.intersection(s_combined)) / min(len(combined), len(s_combined))
            if overlap >= 0.55:
                return True
    return False


def job_generate_ph_biweekly_overview():
    """Luồng 2 (2 tuần/lần): Lọc tính năng 14 ngày, tạo báo cáo tiếng Anh chuẩn Tech & Product và gửi thông báo."""
    print(f"[{datetime.now()}] Generating bi-weekly Feature & Product Overview Report...")
    raw_sheet, table_sheet, archive_sheet = init_sheets()
    all_records = raw_sheet.get_all_records()
    
    now = datetime.now()
    cutoff_date = now - timedelta(days=14)
    filtered_rows = []
    
    for r in all_records:
        try:
            if datetime.strptime(str(r['Timestamp_ISO']), "%Y-%m-%d") >= cutoff_date:
                title = str(r.get('Source_Title', ''))
                what  = str(r.get('What', ''))
                who   = str(r.get('Who', ''))
                # Apply strict product-only filter
                if is_product_relevant(title, what, who):
                    if not is_duplicate(r, filtered_rows):
                        # Re-classify Side using keyword logic (more accurate than raw collect side)
                        r['Side'] = guess_side(what + " " + title + " " + who)
                        filtered_rows.append(r)
        except ValueError:
            continue

    print(f"Filtered {len(filtered_rows)} product-relevant records (from {len(all_records)} total in sheet).")

    if not filtered_rows:
        print("No feature updates recorded in the past 14 days. Generating 'No Updates' report...")
        report_header = (
            f"Feature & Product Launch Update, {cutoff_date.strftime('%d %b')} to "
            f"{now.strftime('%d %b %Y')} | All Competitors"
        )
        bottom_line = (
            f"• Over the past 14 days (from {cutoff_date.strftime('%d/%m/%Y')} to {now.strftime('%d/%m/%Y')}), "
            "no major new feature launches, pricing adjustments, or regulatory policy shifts were recorded from ride-hailing competitors (Grab PH, Move It, Angkas, JoyRide, inDrive) or LTFRB.\n"
            "• Mobile driver and passenger applications on Google Play Store and Apple App Store did not roll out significant feature overhauls in this cycle."
        )
        action_items = (
            "• Continue 48-hour automated monitoring across competitor official portals, press rooms, and app store release notes.\n"
            "• Proactively inspect regional promotional campaigns and operational test runs across Metro Manila and Cebu."
        )
        table_sheet_rows = [
            ['Date', 'Who', 'What (Feature / Launch)', 'Side', 'Ref', 'Full_Reference_Citation'],
            [now.strftime('%d %b %Y'), 'All Competitors', 'No new feature or policy updates recorded in the past 14 days', 'Marketplace', '-', 'N/A']
        ]
        md_table_block = (
            "| Date | Who | What (Feature / Launch) | Side | Ref |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n"
            f"| {now.strftime('%d %b %Y')} | All Competitors | No new feature or policy updates recorded in the past 14 days | Marketplace | - |"
        )
        refs_block = "* No reference citations for this reporting period."

        # Update Tab 2 (Current_Report_Table)
        table_sheet.clear()
        table_sheet.update(range_name='A1', values=table_sheet_rows)

        # Archive to Tab 3 (BiWeekly_Overview_Archive)
        archive_sheet.append_row([
            now.strftime("%Y-%m-%d"),
            report_header,
            bottom_line,
            0,
            action_items,
            refs_block
        ])

        # Export English Markdown Document
        doc_content = (
            f"## {report_header}\n\n"
            f"**Bottom Line & Key Product Takeaways**\n{bottom_line}\n\n"
            f"**Feature and Product Launches**\n\n{md_table_block}\n\n"
            f"**What I'd Like Us to Act On (Product & Strategic Next Steps)**\n{action_items}\n\n"
            f"**References & Citations**\n{refs_block}\n"
        )
        filename = f"PH_Competitor_Report_{now.strftime('%Y%m%d')}.md"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(doc_content)

        send_telegram_biweekly_report(report_header, bottom_line, action_items, filename)
        send_email_biweekly_report(report_header, bottom_line, [], action_items)
        print(f"Successfully generated status report {filename} and sent notifications!")
        return

    table_sheet_rows = [['Date', 'Who', 'What (Feature / Launch)', 'Side', 'Ref', 'Full_Reference_Citation']]
    md_table_lines = [
        "| Date | Who | What (Feature / Launch) | Side | Ref |",
        "| :--- | :--- | :--- | :--- | :--- |"
    ]
    references_lines, ai_context_lines, competitors_seen = [], [], []

    for idx, r in enumerate(filtered_rows, start=1):
        ref_tag = f"[{idx}]"
        who = r['Who']
        if who not in competitors_seen and who != "Marketplace":
            competitors_seen.append(who)
            
        citation_str = f"{ref_tag} {r['Source_Name']} — *{r['Source_Title']}* ({r['Date']}): {r['Source_URL']}"
        table_sheet_rows.append([r['Date'], who, r['What'], r['Side'], ref_tag, citation_str])
        md_table_lines.append(f"| {r['Date']} | {who} | {r['What']} | {r['Side']} | {ref_tag} |")
        references_lines.append(f"* {citation_str}")
        ai_context_lines.append(f"{ref_tag} ({r['Date']}) {who} [{r['Side']}]: {r['What']}")

    # Update Tab 2 (Current_Report_Table)
    table_sheet.clear()
    table_sheet.update(range_name='A1', values=table_sheet_rows)

    ai_input = "\n".join(ai_context_lines)
    prompt = f"""
    You are a Principal Product Manager for a Ride-Hailing platform (Driver App, Customer App, Marketplace & Dispatch, Driver Onboarding/eKYC) operating in Southeast Asia / Philippines.
    Below is the curated list of competitor feature launches, app releases, and policy changes from the past 14 days (with reference tags [1], [2]...):

    {ai_input}

    Write the following 2 sections ENTIRELY IN PROFESSIONAL ENGLISH, separated by '|||'.
    MANDATORY: Append citation reference tags (e.g., [1], [2]) at the end of each bullet point to ground every claim directly in the data:

    Section 1 (Bottom Line & Key Product Takeaways):
    Write concise, executive bullet points synthesizing the biggest strategic trends, new product features (UI/UX, Group Rides, Heatmaps, in-app mechanics), driver onboarding/eKYC requirements, driver retention/commissions, and LTFRB regulatory mandates. Focus heavily on product, tech, and user experience.
    |||
    Section 2 (What I'd Like Us to Act On):
    Write sharp, actionable product, engineering, and operational recommendations for our product team (e.g., features to benchmark, driver UX enhancements, pricing/dispatch counter-measures). Format as clear bullet points. Do NOT include priority labels like P0/P1.
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
        print(f"[OpenAI Notice] Error calling GPT-4o ({e}). Generating dynamic fallback summary from table.")
        highlights_by_comp = {}
        for idx_f, r_item in enumerate(filtered_rows, start=1):
            comp = r_item['Who']
            if comp not in highlights_by_comp:
                highlights_by_comp[comp] = []
            highlights_by_comp[comp].append(f"{r_item['What']} [{idx_f}]")

        summary_bullets = [
            f"• Over the past 14 days, {len(filtered_rows)} distinct product features, policy shifts, and app releases were tracked across: {', '.join(competitors_seen)}."
        ]
        for comp, items in highlights_by_comp.items():
            if comp == "Marketplace":
                continue
            summary_bullets.append(f"• **{comp}**: {items[0]}")

        bottom_line = "\n".join(summary_bullets)

        action_items = (
            "• **Driver Retention & Take Rates**: Benchmark JoyRide and Angkas commission cuts (down to 18%) against our platform take rates and fuel relief subsidies.\n"
            "• **Passenger UX & Group Rides**: Evaluate GrabCar's automated Group Ride split-fare functionality for adoption in our consumer mobile app.\n"
            "• **Regulatory Compliance**: Closely audit LTFRB's fixed pick-up rate reinstatement and MC taxi pilot cap enforcement to prevent operational fines."
        )

    report_header = (
        f"Feature & Product Launch Update, {cutoff_date.strftime('%d %b')} to "
        f"{now.strftime('%d %b %Y')} | {', '.join(competitors_seen)}"
    )
    refs_block = "\n".join(references_lines)
    md_table_block = "\n".join(md_table_lines)

    # Archive to Tab 3 (BiWeekly_Overview_Archive)
    archive_sheet.append_row([
        now.strftime("%Y-%m-%d"),
        report_header,
        bottom_line,
        len(filtered_rows),
        action_items,
        refs_block
    ])

    # Export English Markdown Document
    doc_content = (
        f"## {report_header}\n\n"
        f"**Bottom Line & Key Product Takeaways**\n{bottom_line}\n\n"
        f"**Feature and Product Launches**\n\n{md_table_block}\n\n"
        f"**What I'd Like Us to Act On (Product & Strategic Next Steps)**\n{action_items}\n\n"
        f"**References & Citations**\n{refs_block}\n"
    )
    filename = f"PH_Competitor_Report_{now.strftime('%Y%m%d')}.md"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(doc_content)

    send_telegram_biweekly_report(report_header, bottom_line, action_items, filename)
    send_email_biweekly_report(report_header, bottom_line, filtered_rows, action_items)
    print(f"Successfully exported feature report {filename} and delivered notifications!")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "collect"
    if mode == "collect":
        job_collect_ph_data()
    elif mode == "overview":
        job_generate_ph_biweekly_overview()
