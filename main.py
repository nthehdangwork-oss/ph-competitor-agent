import os
import sys
import re
import html
import unicodedata
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
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
GEMINI_API_KEY = (os.getenv("GEMINI_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
GEMINI_FALLBACK_MODEL = "gemini-3.7-flash"
SHEET_NAME = os.getenv("SHEET_NAME", "PH_RideHailing_Competitor_Tracking")

SCOPES = [
    'https://www.googleapis.com/auth/spreadsheets',
    'https://www.googleapis.com/auth/drive'
]
SHEET_URL = os.getenv("SHEET_URL", "")

gc = None
client = None
ai_quota_exhausted = False


def init_clients():
    """Khởi tạo Google Sheets client và Google Gemini API client khi file cấu hình đã sẵn sàng."""
    global gc, client
    if gc is not None and client is not None:
        return

    errors = []
    if not os.path.exists('service_account.json'):
        errors.append("❌ Thiếu file 'service_account.json' trong thư mục ph-competitor-agent/.")
    if not GEMINI_API_KEY or "AIzaSyXXXX" in GEMINI_API_KEY or "sk-proj-xxxx" in GEMINI_API_KEY:
        errors.append("❌ Chưa cấu hình GEMINI_API_KEY hợp lệ trong file .env (Lấy miễn phí tại https://aistudio.google.com/apikey).")

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
    client = OpenAI(
        api_key=GEMINI_API_KEY,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        max_retries=1,
        timeout=15.0
    )

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


def normalize_plain_text(text):
    """Normalize mathematical/fancy Unicode fonts to standard ASCII/UTF-8 and collapse whitespace."""
    if not text:
        return ""
    norm = unicodedata.normalize("NFKC", str(text))
    norm = html.unescape(norm)
    # Remove emoji / pictographs that clutter executive summaries
    norm = re.sub(r'[\U0001F300-\U0001FAFF\u2600-\u27BF]+', ' ', norm)
    norm = re.sub(r'[\r\n\t|]+', ' ', norm)
    norm = re.sub(r'\s{2,}', ' ', norm).strip()
    return norm


def humanize_url_slug(url):
    """Convert a bare press/blog URL slug into a readable English title when page title was missing."""
    path = urllib.parse.urlparse(url).path.strip("/")
    slug = path.split("/")[-1] if path else ""
    if not slug or len(slug) < 8:
        return ""
    words = slug.replace("-", " ").replace("_", " ").split()
    proper_map = {
        "grab": "Grab", "ph": "PH", "philippines": "Philippines", "grabcar": "GrabCar",
        "grabrewards": "GrabRewards", "ltfrb": "LTFRB", "dotr": "DOTr", "tnvs": "TNVS",
        "mc": "MC", "ev": "EV", "ai": "AI", "ipo": "IPO", "pse": "PSE", "indrive": "inDrive",
        "joyride": "JoyRide", "angkas": "Angkas", "maxicare": "Maxicare", "ncmf": "NCMF",
        "xi": "XI", "usd": "USD", "php": "PHP", "mmda": "MMDA", "mdppa": "MDPPA"
    }
    titled = []
    for idx, w in enumerate(words):
        lw = w.lower()
        if lw in proper_map:
            titled.append(proper_map[lw])
        elif idx == 0:
            titled.append(w.capitalize())
        else:
            titled.append(w)
    res = " ".join(titled)
    res = res.replace("Move it", "Move It").replace("move it", "Move It")
    return res


def guess_side(text, who=""):
    """Standardized 5-label Side classification:
    - Driver: Commissions, fuel relief/vouchers, incentives, driver programs (AI Ambassador, Wellness Day, health/EV perks, onboarding).
    - Passenger: Consumer app features (Group Ride, split fare, real-time tracking, passenger safety, receipts).
    - Regulatory: Decisions, mandates, caps, fines, or official statements from LTFRB, DOTr, or government.
    - Marketplace: City expansion, SuperApp multi-vertical launch, IPO/PSE listing, M&A, executive/corporate strategy.
    - Both: Changes directly affecting both drivers/riders and passengers simultaneously.
    """
    t = normalize_plain_text(text).lower()
    w = (who or "").strip().lower()

    # 1. Explicit Regulatory decisions / government statements
    reg_signals = [
        r"\bltfrb\b", r"\bdotr\b", r"\blto\b", r"^palace:", r"\bmalacañang\b",
        r"\btransportation secretary\b", r"\bregularization\b", r"\btnvs slots\b",
        r"\bfranchise slots\b", r"\bfare matrix\b", r"\bfare adjustment for puvs\b",
        r"\bpick-up fare\b", r"\bpick-up rate\b", r"\briders cap\b", r"\bbiker limit\b",
        r"\bfines move it\b", r"\b555\.7", r"\bfine\b", r"\bmotorcycle taxi bids\b", r"\bregulatory uncertainty\b"
    ]
    if w in ("ltfrb", "dotr") or any(re.search(p, t) for p in reg_signals):
        # Exception: pure corporate IPO/PSE strategy where LTFRB isn't the actor
        if not any(k in t for k in ["going public", "eyes pse listing"]):
            return "Regulatory"

    # 2. Explicit Marketplace / Corporate / Geographic Expansion
    marketplace_signals = [
        r"\bsuperapp to\b", r"\bbrings its superapp\b", r"\bpower tourism\b",
        r"\bgoing public\b", r"\bpse listing\b", r"\bstock exchange\b", r"\bplanned .* ipo\b",
        r"\bacquires\b", r"\bacquisition\b", r"\bmanaging director\b", r"\bnew philippines chief\b",
        r"\bappoints\b", r"\bgoes superapp\b", r"\badds grocery\b", r"\bsame-day grocery\b",
        r"\bindrive\.groceries\b", r"\blaunches grocery and parcel\b"
    ]
    if any(re.search(p, t) for p in marketplace_signals):
        return "Marketplace"

    # 3. Check Driver vs Passenger signals
    driver_signals = [
        r"\bcommission\b", r"\bcommissions\b", r"\bfuel voucher", r"\bfuel price", r"\bfuel crisis",
        r"\bfuel relief\b", r"\brelief package\b", r"\bbayanihan fund\b", r"\bdriver ai ambassador\b",
        r"\bwellness day\b", r"\bdriver well-being\b", r"\bincentive program\b", r"\btiered incentive\b",
        r"\bev charging\b", r"\bev drivers\b", r"\bhealth coverage for driver\b", r"\bmaxicare\b",
        r"\bonboarding facility\b", r"\bdriver registration\b", r"\brewards top drivers\b",
        r"\bsupport driver\b", r"\bsupport its filipino drivers\b"
    ]
    passenger_signals = [
        r"\bgroup ride\b", r"\bsplits the fare\b", r"\bfare splitting\b", r"\bgrab for family\b",
        r"\bride safety for teens\b", r"\btrack and share your ride\b", r"\bdetailed ride receipts\b",
        r"\bpost ride feedback\b", r"\bpassenger safety\b", r"\binstant messaging\b",
        r"\bgrabrewards\b", r"\bcommuter\b"
    ]

    is_driver = any(re.search(p, t) for p in driver_signals)
    is_passenger = any(re.search(p, t) for p in passenger_signals)

    # Explicit dual-impact checks
    if any(k in t for k in ["rider-partner readiness and passenger safety", "data breach", "cybersecurity incident"]):
        return "Both"

    if is_driver and is_passenger:
        return "Both"
    if is_driver:
        return "Driver"
    if is_passenger:
        return "Passenger"

    # Broader fallback checks
    if re.search(r"\b(driver|drivers|biker|bikers)\b", t) and not re.search(r"\b(passenger|passengers|customer)\b", t):
        return "Driver"
    if re.search(r"\b(passenger|passengers|customer|customers)\b", t):
        return "Passenger"
    return "Marketplace"


def is_routine_maintenance_notes(text):
    """Detect App Store / Play Store release notes that only contain routine bug fixes or performance polish."""
    t = normalize_plain_text(text).lower()
    if not t:
        return True
    routine_patterns = [
        "bugs are the future of food",
        "get rid of bugs from your everyday app experience",
        "minor bug fixes",
        "no specific new features",
        "no new features listed",
        "no recent changes",
        "performance improvements and bug fixes",
        "improving performance, polishing design",
    ]
    if any(p in t for p in routine_patterns):
        return True
    # Check if there are any concrete feature nouns beyond generic fix/polish words
    concrete_features = [
        "group ride", "split", "track", "share your ride", "safety", "receipt",
        "feedback", "wallet", "gcash", "maya", "chat", "sos", "pin", "heatmap",
        "dispatch", "ekyc", "grocery", "courier", "family", "audio", "scheduled"
    ]
    if not any(f in t for f in concrete_features) and any(g in t for g in ["bug", "performance", "stability", "polish"]):
        return True
    return False


def is_product_relevant(title, what, who=""):
    """STRICT filter for 2-day collection: retain product, feature, pricing/commission, market expansion,
    app release notes, and operational LTFRB/DOTr updates; reject off-topic noise."""
    text = normalize_plain_text(f"{title} {what}").lower()

    if "release notes" in text or ("v5." in text and ("ios" in text or "android" in text or "app" in text)):
        return True

    hard_noise = [
        "shabu", "nabbed", "arrested", "gun", "drugs", "drug", "robbery", "killed", "killed by",
        "murder", "rape", "harassment complaint", "alleged", "altercation", "provocation",
        "disrespect", "summoned over", "viral video", "confrontation", "misconduct",
        "naaksidente", "patay", "ataul", "salpukin",
        "file abuse", "file raps", "file charges", "rape charges", "complaints filed",
        "summons bus operator", "summons modern jeepney", "transport operator summoned",
        "cum laude", "graduates", "scholar program", "scholarship", "first-timer's guide",
        "deaf woman", "deaf passenger", "palm sunday", "christmas", "new year",
        "nba", "basketball", "gilas", "fiba", "family feud", "episode", "concert",
        "celebrity", "showbiz", "miss universe", "pageant", "fashion", "pope leo", "james bond",
        "giannis", "pj simon", "rizal stadium", "heat index", "one mindanao", "black sea",
        "asean summit", "asean holiday", "edsa bus lane", "pitx for long weekend", "travelers flock to pitx",
        "rockwell", "hotel", "resort", "maga", "trump", "midterm", "licensure", "midwives",
        "street vendors", "eu commission", "nvidia", "fake bank closure", "safc releases",
        "privacy preference center", "passenger appeal form", "download the official",
        "grab. the everyday everything", "joyride ph: mc taxi, car, airport",
        "indrive services in philippines", "page 2 of 9"
    ]
    for n in hard_noise:
        if n in text:
            return False

    valid_competitors = ["grab ph", "move it", "angkas", "joyride ph", "indrive ph", "ltfrb", "dotr"]
    if who and who.lower() not in valid_competitors:
        if not any(k in text for k in ["ride-hailing", "tnvs", "motorcycle taxi", "mc taxi", "grab", "angkas", "joyride", "move it", "indrive"]):
            return False

    product_signals = [
        "release notes", "app update", "version", "v5.", "new feature", "feature",
        "launch", "launches", "launched", "roll out", "rolled out", "rolling out",
        "new service", "new app", "new platform", "new product", "new tool", "new integration",
        "ui update", "ux update", "redesign", "in-app", "in app",
        "group ride", "split fare", "splits the fare", "fare splitting", "heatmap", "dispatch", "pick-up",
        "in-app chat", "instant messaging", "sos", "real-time", "live tracking", "wallet",
        "gcash", "maya", "payment", "ekyc", "onboarding", "for family", "data breach", "cybersecurity",
        "commission", "incentive", "driver reward", "driver app", "driver ai", "wellness day",
        "relief package", "fuel voucher", "fuel subsidy", "fuel relief", "bayanihan fund", "ev charging",
        "health coverage", "maxicare", "rider-partner readiness",
        "fare adjustment", "fare matrix", "surge pricing", "booking fee", "grocery", "groceries", "parcel", "couriers",
        "superapp", "expansion", "expands", "puerto princesa", "partnership", "acquires", "acquisition",
        "ipo", "going public", "pse listing", "managing director", "philippines chief",
        "ltfrb", "dotr", "tnvs", "motorcycle taxi", "mc taxi", "riders cap", "regularization"
    ]
    return any(s in text for s in product_signals)


def clean_complete_sentence(who, title, what, url="", is_release_note=False):
    """Produce a complete, un-truncated, grammatical English summary without '...' or mid-word cutoffs."""
    raw_title = normalize_plain_text(title)
    raw_what = normalize_plain_text(what)

    # 1. Handle App Store / Play Store release notes cleanly
    if is_release_note or "release notes" in raw_title.lower():
        ver_match = re.search(r'\bv(\d+\.\d+(?:\.\d+)?)\b', raw_title + " " + raw_what)
        ver_prefix = f"v{ver_match.group(1)} — " if ver_match else ""
        body = re.sub(r'^v\d+\.\d+(?:\.\d+)?\s*[—\-:]\s*', '', raw_what).strip()

        if is_routine_maintenance_notes(body) or is_routine_maintenance_notes(raw_title):
            if "polishing design" in body.lower():
                return f"{ver_prefix}Routine app maintenance delivering performance optimizations, UI polish, and stability fixes."
            return f"{ver_prefix}Routine maintenance update with minor bug fixes and stability improvements; no new user-facing features."

        # Feature-bearing release notes (e.g., Move It)
        if "move it" in (who or "").lower() and "track and share your ride" in body.lower():
            return (
                f"{ver_prefix}Updated Move It passenger app featuring streamlined Moto Taxi pickup booking, "
                "real-time ride tracking and trip sharing, enhanced safety tools, detailed ride receipts, and post-ride feedback."
            )
        # Generic clean sentence for feature release notes without '...'
        parts = [p.strip().rstrip(".!;") for p in re.split(r'[;•]+', body) if len(p.strip()) > 6]
        if parts and parts[0].lower().startswith("the updated "):
            parts[0] = re.sub(r'\s*-\s*ang panalong.*$', '', parts[0], flags=re.I)
        joined = "; ".join(parts[:4]).strip()
        if not joined.endswith("."):
            joined += "."
        return f"{ver_prefix}{joined}"

    # 2. If What or Title was a bare URL, humanize slug
    if raw_what.startswith("http://") or raw_what.startswith("https://"):
        raw_what = ""
    if raw_title.startswith("http://") or raw_title.startswith("https://"):
        raw_title = humanize_url_slug(raw_title)

    # Pick the fuller candidate when What was cut off mid-sentence
    candidate = raw_what if raw_what else raw_title
    if raw_title and len(raw_title) > len(candidate):
        # Check if candidate looks like a truncated prefix of raw_title
        cand_prefix = candidate[:min(45, len(candidate))].lower()
        if cand_prefix and cand_prefix in raw_title.lower():
            candidate = raw_title

    if not candidate and url:
        candidate = humanize_url_slug(url)

    # 3. Strip social media clutter, hashtags, and attribution trailers
    candidate = re.sub(r'^PASADANG SWABE[^!]*!\s*', '', candidate, flags=re.I)
    candidate = re.sub(r'^(?:JUST IN|BREAKING|LOOK|WATCH|READ)\s*:\s*', '', candidate, flags=re.I)
    candidate = re.sub(r'\s*\|\s*via\s+.*$', '', candidate, flags=re.I)
    candidate = re.sub(r'\s*Link to full story.*$', '', candidate, flags=re.I)
    candidate = re.sub(r'(?:\s+#[A-Za-z0-9_]+)+.*$', '', candidate)
    candidate = candidate.replace("...", "").strip()

    # 4. Fix shouty ALL-CAPS prefixes in headlines
    if candidate.upper().startswith("DOTR APPROVES FARE ADJUSTMENT FOR PUVS"):
        return "DOTr approves fare adjustments for PUVs covering jeepneys, buses, TNVS, and airport taxis following LTFRB recommendations."
    if candidate.upper().startswith("JOYRIDE INVESTIGATES POSSIBLE DATA BREACH"):
        candidate = re.sub(r'^JOYRIDE INVESTIGATES POSSIBLE DATA BREACH\s*', '', candidate, flags=re.I).strip()

    # 5. Drop trailing incomplete sentence fragments (from Facebook / RSS character limits)
    # Split on sentence boundaries '. ' or '! '
    sentences = re.split(r'(?<=[.!])\s+', candidate)
    if len(sentences) > 1:
        last_s = sentences[-1].strip()
        # If the last segment lacks terminal punctuation, it was cut off mid-sentence -> keep complete sentences!
        if not last_s.endswith((".", "!", "?”", '."', '!"')):
            complete_part = " ".join(s.strip() for s in sentences[:-1] if s.strip())
            if len(complete_part) >= 35:
                candidate = complete_part

    # 6. Trim dangling incomplete clauses if single-sentence snippet was cut mid-clause
    dangling_patterns = [
        r",\s*[a-z]{1,15}$",  # e.g., ", blend"
        r"\s+(?:through their tiered|and|or|with|to|for|in|of|by|amid|from|that|which|while|as|at|on|via)$",
    ]
    for dp in dangling_patterns:
        if re.search(r'\bthrough their tiered$', candidate, re.I):
            candidate = re.sub(r'\bthrough their tiered$', 'through its tiered driver incentive system', candidate, flags=re.I)
        else:
            candidate = re.sub(dp, '', candidate, flags=re.I).strip()

    # Clean contact-page multiline address artifacts
    if "onboarding facility" in candidate.lower() and "antipolo" in candidate.lower():
        return "JoyRide PH lists its Antipolo City driver onboarding facility along Marcos Highway (Mon–Sat, 8AM–5PM)."

    candidate = candidate.strip(" ,;-–—")
    if candidate and candidate[0].islower() and not candidate.lower().startswith("indrive"):
        candidate = candidate[0].upper() + candidate[1:]
    if candidate and not candidate.endswith((".", "!", "?")):
        candidate += "."
    return candidate


def _summarize_release_notes(competitor, version_title, notes_text):
    """Extract specific feature/fix details from App Store / Play Store release notes as complete sentences."""
    summary = clean_complete_sentence(competitor, version_title, notes_text, is_release_note=True)
    side = "Both" if is_routine_maintenance_notes(notes_text) else guess_side(notes_text + " " + summary, competitor)
    return f"{summary} | {side}"


def _fallback_analyze_item(competitor, title, raw_text):
    """Rule-based fallback when Gemini API is unavailable or out of quota."""
    if not is_product_relevant(title, raw_text[:600], competitor):
        return "SKIP | Noise"
    summary = clean_complete_sentence(competitor, title, "", is_release_note=False)
    if len(summary) < 15:
        summary = clean_complete_sentence(competitor, raw_text[:300], "", is_release_note=False)
    side = guess_side(summary + " " + title, competitor)
    return f"{summary} | {side}"


def _call_gemini(prompt, temperature=0.0):
    """Call Gemini with automatic fallback to GEMINI_FALLBACK_MODEL on 503/429/404."""
    last_err = None
    for m_name in (GEMINI_MODEL, GEMINI_FALLBACK_MODEL):
        try:
            res = client.chat.completions.create(
                model=m_name,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature
            )
            return res.choices[0].message.content.strip()
        except Exception as e:
            last_err = e
            continue
    raise last_err


def analyze_ph_item(competitor, source_name, title, raw_text):
    """Summarize only genuine product/feature updates in complete English sentences using Gemini API."""
    global ai_quota_exhausted

    is_release_note = any(x in title for x in ["Release Notes", "release notes"])
    if is_release_note and raw_text and len(raw_text) > 10:
        if is_routine_maintenance_notes(raw_text) or ai_quota_exhausted:
            return _summarize_release_notes(competitor, title, raw_text)
        try:
            ver_match = re.search(r'\bv(\d+\.\d+(?:\.\d+)?)\b', title)
            ver_str = f"v{ver_match.group(1)}" if ver_match else "Latest"
            prompt = f"""
You are a mobile product analyst. Summarize the App Store / Play Store release notes for '{competitor}' ({title}).

Release Notes:
{raw_text[:2000]}

Write ONE complete, grammatical English sentence (max 45 words, NO '...' truncation) starting with '{ver_str} — ' that clearly names all new user-facing features, UI/UX improvements, safety tools, or receipt/tracking updates.
If the update only fixes bugs or improves performance with no specific features, output:
{ver_str} — Routine maintenance update with minor bug fixes and stability improvements; no new user-facing features.

Return ONE line: [your complete sentence] | [Driver OR Passenger OR Both]
"""
            out = _call_gemini(prompt, temperature=0.0)
            parts = [p.strip() for p in out.split("|")]
            cleaned = clean_complete_sentence(competitor, title, parts[0], is_release_note=True)
            side = parts[1] if len(parts) > 1 and parts[1] in ("Driver", "Passenger", "Both") else guess_side(cleaned, competitor)
            return f"{cleaned} | {side}"
        except Exception as e:
            if not ai_quota_exhausted:
                print(f"   [Gemini Notice] API unavailable ({e}). Using rule-based release notes parser.")
                ai_quota_exhausted = True
            return _summarize_release_notes(competitor, title, raw_text)

    if ai_quota_exhausted:
        return _fallback_analyze_item(competitor, title, raw_text)

    try:
        prompt = f"""
You are an IT Product Owner tracking Philippines Ride-Hailing & MC Taxi competitors (Grab PH, Move It, Angkas, JoyRide PH, inDrive PH) and LTFRB/DOTr regulations.

Track ONLY: new features, app upgrades, product/vertical launches, pricing/commission/subsidy changes, driver programs, city/market expansions, M&A/IPO, platform cybersecurity incidents, and LTFRB/DOTr regulations impacting operations.
Exclude: crimes, street accidents, altercations, court summons for individual drivers, scholarships, sports, entertainment, or unrelated politics.

Analyze this item about '{competitor}' (Source: {source_name}):
Title: {title}
Content: {raw_text[:2500]}

IF relevant → return on ONE LINE separated by '|':
Part 1: ONE complete, self-contained English sentence (20–45 words, NO '...' truncation) stating specifically what launched or changed and its impact.
Part 2: Exactly ONE standardized Side label:
- Driver (commissions, fuel relief/vouchers, incentives, AI Ambassador, Wellness Day, onboarding)
- Passenger (consumer app features: Group Ride, tracking, safety, receipts)
- Regulatory (decisions/statements from LTFRB, DOTr, or government)
- Marketplace (city expansion, SuperApp expansion, IPO, M&A, corporate strategy)
- Both (changes impacting both drivers and passengers simultaneously)

IF irrelevant noise → return exactly:
SKIP | Noise
"""
        out = _call_gemini(prompt, temperature=0.0)
        parts = [p.strip() for p in out.split("|")]
        if parts[0].upper() == "SKIP" or (len(parts) > 1 and parts[1].lower() == "noise"):
            return "SKIP | Noise"
        cleaned = clean_complete_sentence(competitor, title, parts[0])
        valid_sides = {"Driver", "Passenger", "Regulatory", "Marketplace", "Both"}
        side = parts[1] if len(parts) > 1 and parts[1] in valid_sides else guess_side(cleaned + " " + title, competitor)
        return f"{cleaned} | {side}"
    except Exception as e:
        if not ai_quota_exhausted:
            print(f"   [Gemini Notice] API unavailable ({e}). Switching to rule-based analyzer.")
            ai_quota_exhausted = True
        return _fallback_analyze_item(competitor, title, raw_text)


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


STOPWORDS_DEDUP = {
    "philippines", "philippine", "filipino", "ride", "hailing", "platform",
    "driver", "drivers", "rider", "riders", "passenger", "passengers",
    "announces", "announced", "launches", "launched", "launch", "update",
    "with", "from", "that", "this", "their", "amid", "over", "into", "more"
}


def _significant_tokens(text):
    words = re.findall(r'\b[a-zA-Z]{4,}\b', normalize_plain_text(text).lower())
    return {w for w in words if w not in STOPWORDS_DEDUP}


def _story_signature(item):
    """Canonical event signature to collapse multi-outlet coverage of the exact same story."""
    who = str(item.get("Who", "")).strip().lower()
    txt = normalize_plain_text(f"{item.get('Source_Title', '')} {item.get('What', '')}").lower()

    if who == "indrive ph" and any(k in txt for k in ["grocery", "groceries", "parcel", "couriers"]):
        return ("indrive ph", "superapp_grocery_courier")
    if who == "joyride ph" and any(k in txt for k in ["data breach", "cybersecurity", "exposed rider"]):
        return ("joyride ph", "data_breach_probe")
    if who == "angkas" and any(k in txt for k in ["going public", "pse listing", "stock exchange"]):
        return ("angkas", "pse_ipo_2027")
    if who == "grab ph" and any(k in txt for k in ["managing director", "new philippines chief", "steer grab philippines", "taking the wheel"]):
        return ("grab ph", "new_country_chief")
    if who == "move it" and any(k in txt for k in ["555", "excess riders", "statement on ltfrb decision"]):
        return ("move it", "ltfrb_555m_fine")
    if who == "grab ph" and "group ride" in txt:
        return ("grab ph", "group_ride_split_fare")
    if who == "grab ph" and "bayanihan fund" in txt:
        return ("grab ph", "bayanihan_fund")
    if who == "ltfrb" and "regularization" in txt and ("deadline" in txt or "extends" in txt or "extended" in txt):
        return ("ltfrb", "tnvs_regularization_extension")
    return None


def is_duplicate(item, seen_items, threshold=0.50):
    """Check if an item is a duplicate or near-duplicate of an already selected item for the same competitor."""
    item_url = str(item.get('Source_URL', '')).split('?')[0].rstrip('/').lower()
    t1 = _significant_tokens(f"{item.get('Source_Title', '')} {item.get('What', '')}")
    sig1 = _story_signature(item)

    # Normalize App Store / Play Store app base URL (ignore #vX.Y.Z fragment so only newest version is kept per bucket)
    is_app_note = "release notes" in str(item.get('Source_Title', '')).lower()
    item_app_base = item_url.split('#')[0] if is_app_note else None

    for s in seen_items:
        s_url = str(s.get('Source_URL', '')).split('?')[0].rstrip('/').lower()
        if item_url and item_url == s_url:
            return True
        if sig1 is not None and sig1 == _story_signature(s):
            return True
        if s.get('Who') != item.get('Who'):
            continue
        if is_app_note and "release notes" in str(s.get('Source_Title', '')).lower():
            if item_app_base and item_app_base == s_url.split('#')[0]:
                return True
        t2 = _significant_tokens(f"{s.get('Source_Title', '')} {s.get('What', '')}")
        if t1 and t2:
            overlap = len(t1.intersection(t2)) / min(len(t1), len(t2))
            if overlap >= threshold:
                return True
    return False


def _detect_static_brand_page(who, title, what, url):
    """Return True if the record represents a static brand/landing/help/promo page rather than dated news."""
    u = (url or "").lower().strip()
    t = normalize_plain_text(f"{title} {what}").lower()
    parsed = urllib.parse.urlparse(u)
    path = parsed.path.rstrip("/").lower()

    # Exact bare brand homepages or static portal paths
    static_domains = ("grab.com", "indrive.com", "joyride.com.ph", "joyride.ph", "angkas.com", "moveit.com.ph")
    if any(d in parsed.netloc for d in static_domains):
        if path in ("", "/ph", "/en-ph", "/home", "/consumer", "/contact", "/contact-us", "/help", "/download", "/ph/download"):
            return True
        static_path_Creation = [
            "/contact", "/help", "/download", "/install-", "/cities/",
            "/grab-cash-loan", "/passenger-appeal-form", "/blog/grab-x-acmobility"
        ]
        if any(sp in path for sp in static_path_Creation):
            return True
    if "register.joyride.com.ph" in parsed.netloc:
        return True
    if "/tag/" in path or path.endswith("/archives"):
        return True

    static_titles = [
        "grab promo codes",
        "why choose grab cash loan",
        "grab car archives",
        "grab. the everyday everything app",
        "download grab app for transport",
        "download the official indrive app",
        "indrive services in philippines",
        "ride service in makati",
        "joyride ph: mc taxi, car, airport transfers",
        "joyride delivery accept items",
        "joyride driver registration",
        "the number 1 motor taxi in the philippines",
        "traffic or delivery problems? we got it covered",
        "move it - one tap and we'll keep you moving",
        "passenger appeal form",
        "privacy preference center",
        "need some help?",
        "save more with acmobility",
        "contact us",
    ]
    if any(st in t for st in static_titles):
        return True
    return False


def _extract_url_or_known_pub_date(url, title, what):
    """Extract publication date from URL patterns or known archival article IDs.
    Returns (pub_date_or_None, is_known_archival_bool)."""
    u = (url or "").strip()
    t = normalize_plain_text(f"{title} {what}").lower()

    # 1. Explicit /YYYY/MM/DD/ in URL
    m_ymd = re.search(r'/(20\d{2})/(\d{1,2})/(\d{1,2})(?:/|-|_|$)', u)
    if m_ymd:
        try:
            return datetime(int(m_ymd.group(1)), int(m_ymd.group(2)), int(m_ymd.group(3))), False
        except ValueError:
            pass

    # 2. Explicit -YYYYMMDD in URL slug (e.g., TopGear -20260528-lfrm)
    m_compact = re.search(r'-(20\d{2})(\d{2})(\d{2})(?:\b|-|_)', u)
    if m_compact:
        try:
            return datetime(int(m_compact.group(1)), int(m_compact.group(2)), int(m_compact.group(3))), False
        except ValueError:
            pass

    # 3. Explicit /YYYY/MM/ month-level URL (e.g., /2026/10/...)
    m_ym = re.search(r'/(20\d{2})/(\d{1,2})/', u)
    if m_ym:
        try:
            return datetime(int(m_ym.group(1)), int(m_ym.group(2)), 15), False
        except ValueError:
            pass

    # 4. Known archival milestones / article ID ranges when publisher blocks headless fetch
    if "grab philippines acquires move it" in t:
        return None, True
    if any(slug in u.lower() for slug in [
        "grab-beta-launches-grabrewards",
        "grab-launches-app-instant-messaging",
        "grab-boosts-ride-safety-for-teens-with-grab-for-family",
        "grab-ncmf-region-xi-partner",
    ]):
        return None, True

    m_gma = re.search(r'gmanetwork\.com/news/.+/(\d{6,7})/', u)
    if m_gma and int(m_gma.group(1)) < 1005000:
        return None, True

    m_inq = re.search(r'newsinfo\.inquirer\.net/(\d{7})/', u)
    if m_inq and int(m_inq.group(1)) < 2330000:
        return None, True

    return None, False


def _probe_single_url_meta(url):
    """Lightweight metadata probe (4s timeout) to retrieve published date and <title> for undated URLs."""
    result = {"date": None, "title": None}
    if not url or any(skip in url for skip in ["apps.apple.com", "play.google.com", "facebook.com", "news.google.com/rss/"]):
        return url, result
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"}
    try:
        resp = requests.get(url, headers=headers, timeout=4)
        if resp.status_code == 200 and resp.text:
            html_str = resp.text[:120000]
            m_title = re.search(r'<title[^>]*>(.*?)</title>', html_str, re.I | re.S)
            if m_title:
                clean_t = normalize_plain_text(re.sub(r'<[^>]+>', '', m_title.group(1)))
                clean_t = re.sub(r'\s*[\|\-–]\s*(?:Grab PH|Grab|Inquirer\.net|Rappler|Philstar.*|BusinessWorld.*)$', '', clean_t, flags=re.I)
                if len(clean_t) > 10 and "just a moment" not in clean_t.lower():
                    result["title"] = clean_t
            m_date = re.search(
                r'(?:article:published_time|datePublished|pubdate)[^0-9]{1,25}(20\d{2}-\d{2}-\d{2})',
                html_str,
                re.I
            )
            if m_date:
                result["date"] = datetime.strptime(m_date.group(1), "%Y-%m-%d")
            else:
                try:
                    meta = trafilatura.extract_metadata(html_str)
                    if meta and getattr(meta, "date", None):
                        result["date"] = datetime.strptime(str(meta.date)[:10], "%Y-%m-%d")
                except Exception:
                    pass
    except Exception:
        pass
    return url, result


def _is_pure_offtopic_noise(who, title, what, url):
    """Return True for pure social/crime/entertainment/off-topic noise that should be discarded completely
    (neither High-Signal nor Appendix)."""
    t = normalize_plain_text(f"{title} {what}").lower()
    u = (url or "").lower()

    if "/page/" in u or re.match(r'^page \d+ of \d+', t):
        return True

    offtopic_terms = [
        "shabu", "nabbed", "arrested", "gun", "robbery", "murder", "rape",
        "harassment complaint", "viral harassment", "summoned by ltfrb over viral",
        "summons joyride over harassment", "altercation", "verbal provocation",
        "disrespect to passenger", "summons bus operator", "summons modern jeepney",
        "transport operator summoned", "file abuse raps", "deaf woman",
        "naaksidente", "patay", "ataul", "salpukin", "lpg tanker", "black sea",
        "cum laude", "grabscholar", "first college graduates", "first-timer's guide",
        "5-star-eats", "halal food discovery", "emergency first responder",
        "pasadang swabe", "ride and report",
        "nba", "basketball", "gilas", "fiba", "pj simon", "giannis", "family feud",
        "concert", "celebrity", "showbiz", "james bond", "pope leo", "rizal stadium",
        "scoop opens", "rockwell", "heat index", "one mindanao",
        "asean summit", "asean delegates", "asean holiday", "edsa bus lane",
        "pitx for long weekend", "travelers flock to pitx",
        "midwives", "licensure", "street vendors", "maga", "trump",
        "maria ressa", "interoperability", "eu commission", "nvidia",
        "fake bank closure", "safc releases"
    ]
    if any(term in t for term in offtopic_terms):
        return True

    valid_orgs = {"grab ph", "move it", "angkas", "joyride ph", "indrive ph", "ltfrb", "dotr"}
    if (who or "").strip().lower() not in valid_orgs:
        if not any(k in t for k in ["grab", "move it", "angkas", "joyride", "indrive", "tnvs", "motorcycle taxi", "mc taxi"]):
            return True

    return False


def _clean_citation_title(who, title, what, url):
    """Ensure every reference title in Section 5 is clean, readable, and safe inside Markdown [Title](URL)."""
    t = normalize_plain_text(title)
    if not t or t.startswith("http://") or t.startswith("https://"):
        t = humanize_url_slug(url) or clean_complete_sentence(who, title, what, url).rstrip(".")
    if t.upper().startswith("DOTR APPROVES FARE ADJUSTMENT FOR PUVS"):
        t = "DOTr Approves Fare Adjustment for PUVs Including Jeepneys, Buses, TNVS, and Airport Taxis"
    if t.upper().startswith("JOYRIDE INVESTIGATES POSSIBLE DATA BREACH"):
        t = "JoyRide Investigates Possible Data Breach"
    t = re.sub(r'^(?:JUST IN|BREAKING|LOOK)\s*:\s*', '', t, flags=re.I)
    t = re.sub(r'\s*\|\s*via\s+.*$', '', t, flags=re.I)
    t = re.sub(r'(?:\s+#[A-Za-z0-9_]+)+.*$', '', t)
    # Trim overly long Facebook post titles at first sentence or clean word boundary without trailing prepositions
    if len(t) > 115:
        first_sent = re.split(r'(?<=[.!])\s+', t)[0]
        if 30 <= len(first_sent) <= 135:
            t = first_sent.rstrip(".")
        else:
            t = t[:110].rsplit(" ", 1)[0]
            t = re.sub(r'\s+(?:through|with|for|to|in|on|at|by|from|of|amid|and|or|their|its|the|a|an)$', '', t, flags=re.I)
    return t.replace("[", "(").replace("]", ")").strip()


def _strip_priority_labels(text):
    """Guarantee zero P0/P1/P2 priority labels in Action Items."""
    cleaned = re.sub(r'\[?\bP[0-3]\b\]?\s*[:\-–]?\s*', '', text)
    cleaned = re.sub(r'\(\s*Priority\s*[:\-]?\s*(?:High|Medium|Low|P[0-3])\s*\)', '', cleaned, flags=re.I)
    return cleaned


def _build_thematic_fallback(high_signal_rows):
    """Deterministic Principal-PM synthesizer grouping High-Signal items into 4–5 strategic themes
    (Section 1) and owner-tagged, citation-backed recommendations (Section 3)."""
    buckets = {
        "commission_fuel": [],
        "passenger_ux": [],
        "expansion_corp": [],
        "driver_programs": [],
        "regulatory_sec": [],
    }

    for r in high_signal_rows:
        idx = r["ref_num"]
        tag = f"[Source {idx}]"
        txt = f"{r['What']} {r['Source_Title']}".lower()
        side = r["Side"]

        if any(k in txt for k in ["commission", "fuel voucher", "fuel price", "relief package", "subsidy"]):
            buckets["commission_fuel"].append((r, tag))
        elif side == "Passenger" or any(k in txt for k in ["group ride", "splits the fare", "fare splitting", "track and share", "ride receipts", "for family"]):
            buckets["passenger_ux"].append((r, tag))
        elif side == "Regulatory" or any(k in txt for k in ["ltfrb", "dotr", "regularization", "riders cap", "fines move it", "data breach", "cybersecurity", "regulatory uncertainty"]):
            buckets["regulatory_sec"].append((r, tag))
        elif side == "Marketplace" or any(k in txt for k in ["superapp", "groceries", "couriers", "parcel", "puerto princesa", "ipo", "pse listing", "going public", "managing director", "chief"]):
            buckets["expansion_corp"].append((r, tag))
        else:
            buckets["driver_programs"].append((r, tag))

    s1_bullets = []
    s3_bullets = []

    if buckets["commission_fuel"]:
        items = buckets["commission_fuel"]
        details = []
        for r, tag in items[:4]:
            w = r["Who"]
            low = r["What"].lower()
            if "angkas" in w.lower() and "18" in low:
                details.append(f"Angkas lowering take rates from 20% to 18% or less via tiered incentives {tag}")
            elif "joyride" in w.lower() and "commission" in low:
                details.append(f"JoyRide cutting commissions for MC Taxi and car drivers {tag}")
            elif "indrive" in w.lower() and ("2.7" in low or "one percent" in low or "1%" in low):
                details.append(f"inDrive deploying ₱2.7M in fuel vouchers alongside 1% high-demand commissions {tag}")
            elif "indrive" in w.lower():
                details.append(f"inDrive rolling out driver fuel relief subsidies paired with low commission rates {tag}")
            else:
                details.append(f"{w} introducing targeted driver earnings and fuel relief measures {tag}")
        s1_bullets.append(
            f"• **Driver Commission & Fuel Relief:** Competitors intensified driver retention measures amid fuel cost pressures, highlighted by {', '.join(details)}."
        )
        tags_str = " ".join(t for _, t in items[:3])
        s3_bullets.append(
            f"• **Benchmark Tiered Take-Rates & Fuel Subsidies (Product & Driver Ops):** Model the unit-economics impact of competitor commission reductions (down to 18% tiered caps and 1% high-demand incentives) and targeted fuel vouchers to safeguard active driver supply {tags_str}."
        )

    if buckets["passenger_ux"]:
        items = buckets["passenger_ux"]
        details = []
        for r, tag in items[:3]:
            w = r["Who"]
            low = r["What"].lower()
            if "group ride" in low or "splits the fare" in low:
                details.append(f"Grab PH rolling out automated Group Ride fare splitting with up to 50% per-rider savings {tag}")
            elif "move it" in w.lower():
                details.append(f"Move It upgrading its iOS app with real-time ride tracking, trip sharing, itemized receipts, and enhanced safety tools {tag}")
            else:
                details.append(f"{w} enhancing passenger booking and in-trip UX {tag}")
        s1_bullets.append(
            f"• **Passenger UX & App Upgrades:** Consumer app updates focused on social affordability and trip transparency, led by {' and '.join(details)}."
        )
        tags_str = " ".join(t for _, t in items[:2])
        s3_bullets.append(
            f"• **Evaluate Automated Fare-Splitting & Live Trip Receipts (Consumer App PO):** Assess multi-passenger fare splitting, real-time trip-sharing links, and itemized digital receipts for prioritization in upcoming passenger app sprints {tags_str}."
        )

    if buckets["expansion_corp"]:
        items = buckets["expansion_corp"]
        details = []
        for r, tag in items[:4]:
            w = r["Who"]
            low = r["What"].lower()
            if "groceries" in low or "parcel" in low or "couriers" in low:
                details.append(f"inDrive expanding into a Metro Manila SuperApp with same-day grocery (flat ₱59 fee) and negotiable courier delivery {tag}")
            elif "puerto princesa" in low:
                details.append(f"Grab PH launching superapp services in Puerto Princesa to capture tourism demand {tag}")
            elif "ipo" in low or "pse" in low or "going public" in low:
                details.append(f"Angkas advancing its 2027 PSE listing roadmap to broaden rider ownership and digital services {tag}")
            elif "chief" in low or "managing director" in low:
                details.append(f"{w} appointing new country leadership to steer local growth {tag}")
            else:
                details.append(f"{w} executing new market expansion initiatives {tag}")
        s1_bullets.append(
            f"• **Superapp & Market Expansion:** Platforms accelerated geographic and multi-vertical diversification, including {', '.join(details)}."
        )
        tags_str = " ".join(t for _, t in items[:3])
        s3_bullets.append(
            f"• **Assess Multi-Vertical Delivery & Regional Expansion Defenses (Marketplace & Growth Strategy):** Review competitor SuperApp delivery pricing (flat-fee grocery and negotiable courier tiers) and provincial tourism city playbooks to protect cross-vertical share {tags_str}."
        )

    if buckets["driver_programs"]:
        items = buckets["driver_programs"]
        details = []
        for r, tag in items[:3]:
            w = r["Who"]
            low = r["What"].lower()
            if "wellness" in low:
                details.append(f"inDrive hosting Driver Wellness Day initiatives {tag}")
            elif "mdppa" in low or "readiness" in low:
                details.append(f"Move It partnering with MDPPA on rider readiness and passenger safety standards {tag}")
            elif "maxicare" in low or "health" in low:
                details.append(f"Grab and Move It expanding driver healthcare access with Maxicare {tag}")
            else:
                details.append(f"{w} advancing partner engagement and readiness programs {tag}")
        s1_bullets.append(
            f"• **Driver Engagement & Safety Readiness:** Non-monetary partner retention remained active through {' and '.join(details)}."
        )
        tags_str = " ".join(t for _, t in items[:2])
        s3_bullets.append(
            f"• **Strengthen Partner Welfare & Safety Readiness Touchpoints (Driver Engagement & Ops):** Incorporate structured safety-readiness partnerships and driver wellness milestones into our partner loyalty lifecycle {tags_str}."
        )

    if buckets["regulatory_sec"]:
        items = buckets["regulatory_sec"]
        details = []
        has_breach = False
        for r, tag in items[:4]:
            low = r["What"].lower()
            if "regularization" in low:
                details.append(f"LTFRB extending the TNVS regularization compliance deadline by 30 days {tag}")
            elif "fare adjustment" in low:
                details.append(f"DOTr approving fare adjustments covering TNVS and airport taxis {tag}")
            elif "riders cap" in low:
                details.append(f"DOTr initiating a review of the motorcycle taxi rider cap {tag}")
            elif "fines move it" in low or "555" in low:
                details.append(f"LTFRB penalizing Move It ₱555.75M over excess rider cap compliance {tag}")
            elif "data breach" in low or "cybersecurity" in low:
                has_breach = True
                details.append(f"JoyRide investigating a reported rider data exposure incident {tag}")
            else:
                details.append(f"{r['Who']} addressing regulatory compliance developments {tag}")
        s1_bullets.append(
            f"• **Regulatory & Platform Governance:** State oversight tightened around fleet caps, fare structures, and data protection, marked by {', '.join(details)}."
        )
        tags_str = " ".join(t for _, t in items[:3])
        if has_breach:
            s3_bullets.append(
                f"• **Audit Fleet Cap Compliance, Fare Configurations & Rider Data Security (Strategy, Legal & Security Ops):** Verify strict adherence to LTFRB/DOTr rider caps, TNVS regularization timelines, and PUV/TNVS fare matrices while auditing rider PII access controls {tags_str}."
            )
        else:
            s3_bullets.append(
                f"• **Audit Fleet Cap Compliance & TNVS Fare Matrices (Strategy & Legal/Ops):** Align driver onboarding queues with LTFRB's extended TNVS regularization deadline and MC taxi cap rules while validating pricing against DOTr fare adjustments {tags_str}."
            )

    return "\n".join(s1_bullets), "\n".join(s3_bullets)


def _linkify_sources_markdown(text, ref_url_map):
    """Convert canonical [Source X] tags into clickable Markdown links [\\[Source X\\]](URL)."""
    def _repl(match):
        num = int(match.group(1))
        url = ref_url_map.get(num)
        if url:
            return f"[\\[Source {num}\\]]({url})"
        return match.group(0)
    return re.sub(r'\[Source\s+(\d+)\]', _repl, text)


def _format_bullets_html(text, ref_url_map, for_telegram=False):
    """Convert canonical Markdown bullets with **Bold** and [Source X] into clean HTML."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    out_items = []
    for ln in lines:
        ln = re.sub(r'^[•\-\*]\s*', '', ln).strip()
        escaped = html.escape(ln)
        escaped = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', escaped)

        def _repl_html(match):
            num = int(match.group(1))
            url = ref_url_map.get(num)
            if url:
                safe_url = html.escape(url, quote=True)
                if for_telegram:
                    return f'<a href="{safe_url}">[Source {num}]</a>'
                return f'<a href="{safe_url}" style="color: #0969da; text-decoration: none; font-weight: 500;">[Source {num}]</a>'
            return match.group(0)

        linked = re.sub(r'\[Source\s+(\d+)\]', _repl_html, escaped)
        if for_telegram:
            out_items.append(f"• {linked}")
        else:
            out_items.append(f'<li style="margin-bottom: 8px;">{linked}</li>')

    if for_telegram:
        return "\n".join(out_items)
    return f'<ul style="padding-left: 20px; margin: 8px 0;">{"".join(out_items)}</ul>'


def job_generate_ph_biweekly_overview():
    """Generate the 5-section executive Bi-Weekly Overview Report (Markdown, Google Sheets, Email HTML, Telegram)."""
    print(f"[{datetime.now()}] Generating 5-section Executive Bi-Weekly Overview Report...")
    raw_sheet, table_sheet, archive_sheet = init_sheets()
    all_records = raw_sheet.get_all_records()

    now = datetime.now()
    cutoff_date = now - timedelta(days=14)
    cutoff_iso = cutoff_date.strftime("%Y-%m-%d")

    # 1. Collect all records within the 14-day scan window, sorted newest first
    window_records = []
    for idx_orig, r in enumerate(all_records):
        ts_str = str(r.get("Timestamp_ISO", "")).strip()
        try:
            ts_dt = datetime.strptime(ts_str, "%Y-%m-%d")
            if ts_dt >= cutoff_date:
                r_copy = dict(r)
                r_copy["_ts_dt"] = ts_dt
                r_copy["_orig_idx"] = idx_orig
                window_records.append(r_copy)
        except ValueError:
            continue

    total_scanned_14d = len(window_records)
    # Sort newest scan date first; within same date, prefer later entries (richer recent runs)
    window_records.sort(key=lambda x: (x["_ts_dt"], x["_orig_idx"]), reverse=True)

    # Build lookup of clean direct publisher URLs across all records to unmask opaque Google News RSS links
    direct_url_records = []
    for rec in all_records:
        u_cand = str(rec.get("Source_URL", "")).strip()
        if u_cand and "news.google.com/rss/" not in u_cand:
            direct_url_records.append({
                "Who": normalize_plain_text(rec.get("Who", "")),
                "Source_Title": normalize_plain_text(rec.get("Source_Title", "")),
                "What": normalize_plain_text(rec.get("What", "")),
                "Source_URL": u_cand,
                "_tokens": _significant_tokens(f"{rec.get('Source_Title', '')} {rec.get('What', '')}")
            })

    # 2. First-pass filter: discard pure off-topic noise & separate static/maintenance candidates
    prelim_candidates = []
    urls_to_probe = set()

    for r in window_records:
        who = normalize_plain_text(r.get("Who", ""))
        title = normalize_plain_text(r.get("Source_Title", ""))
        what = normalize_plain_text(r.get("What", ""))
        url = str(r.get("Source_URL", "")).strip()

        if _is_pure_offtopic_noise(who, title, what, url):
            continue

        # Unmask opaque Google News RSS URL if a matching direct URL exists in sheet history
        if "news.google.com/rss/" in url:
            r_toks = _significant_tokens(f"{title} {what}")
            best_url = None
            best_score = 0.0
            for d_rec in direct_url_records:
                if d_rec["Who"] == who and r_toks and d_rec["_tokens"]:
                    ov = len(r_toks.intersection(d_rec["_tokens"])) / min(len(r_toks), len(d_rec["_tokens"]))
                    if ov >= 0.70 and ov > best_score:
                        best_score = ov
                        best_url = d_rec["Source_URL"]
            if best_url:
                url = best_url

        r["Who"] = who
        r["Source_Title"] = title
        r["What"] = what
        r["Source_URL"] = url
        prelim_candidates.append(r)

        # Queue undated web URLs for fast parallel metadata date/title probe
        if not _detect_static_brand_page(who, title, what, url):
            is_rel = "release notes" in title.lower() or "app store" in str(r.get("Source_Name", "")).lower() or "play store" in str(r.get("Source_Name", "")).lower()
            if not is_rel:
                pub_dt, is_arch = _extract_url_or_known_pub_date(url, title, what)
                if pub_dt is None and not is_arch:
                    urls_to_probe.add(url)

    # 3. Fast parallel metadata lookup for undated candidate URLs (4s timeout per URL)
    probed_meta = {}
    if urls_to_probe:
        probe_list = list(urls_to_probe)[:25]
        print(f"Probing publication metadata for {len(probe_list)} undated candidate URLs...")
        with ThreadPoolExecutor(max_workers=6) as pool:
            for u_key, meta_val in pool.map(_probe_single_url_meta, probe_list):
                probed_meta[u_key] = meta_val

    # 4. Route items into High-Signal (Section 2) vs Appendix (Section 4) with deduplication
    high_signal_rows = []
    appendix_rows = []

    for r in prelim_candidates:
        who = r["Who"]
        url = r["Source_URL"]
        meta = probed_meta.get(url, {})

        # Backfill missing/URL-only title from probed page <title> if available
        title = r["Source_Title"]
        if (not title or title.startswith("http")) and meta.get("title"):
            title = meta["title"]
            r["Source_Title"] = title

        what = r["What"]
        src_name = normalize_plain_text(r.get("Source_Name", "")) or "News PH"
        r["Source_Name"] = src_name
        is_rel_note = (
            "release notes" in title.lower()
            or "app store" in src_name.lower()
            or "play store" in src_name.lower()
        )

        # Ensure complete, un-truncated sentence for What / Item Summary
        complete_what = clean_complete_sentence(who, title, what, url, is_release_note=is_rel_note)
        r["What"] = complete_what
        r["Clean_Title"] = _clean_citation_title(who, title, complete_what, url)
        r["Side"] = guess_side(f"{complete_what} {title}", who)

        # Check Appendix Reason 1: Static brand page
        if _detect_static_brand_page(who, title, what, url):
            r["Filter_Reason"] = "Static brand page"
            if not is_duplicate(r, appendix_rows, threshold=0.60):
                appendix_rows.append(r)
            continue

        # Check Appendix Reason 2: Routine maintenance (App Store / Play Store bug-fix releases)
        if is_rel_note and (is_routine_maintenance_notes(what) or is_routine_maintenance_notes(complete_what)):
            r["Filter_Reason"] = "Routine maintenance"
            if not is_duplicate(r, appendix_rows, threshold=0.65):
                appendix_rows.append(r)
            continue

        # Check Appendix Reason 3: Earlier article / Archival news (>30 days before report date)
        pub_dt, is_known_archival = _extract_url_or_known_pub_date(url, title, complete_what)
        if pub_dt is None and meta.get("date"):
            pub_dt = meta["date"]

        if is_known_archival or (pub_dt is not None and (now.date() - pub_dt.date()).days > 30):
            r["Filter_Reason"] = "Earlier article / Archival news"
            if not is_duplicate(r, appendix_rows, threshold=0.55) and not is_duplicate(r, high_signal_rows, threshold=0.55):
                appendix_rows.append(r)
            continue

        # Otherwise verify High-Signal relevance and deduplicate
        if is_product_relevant(title, complete_what, who):
            if not is_duplicate(r, high_signal_rows, threshold=0.52):
                high_signal_rows.append(r)

    # Sort both tables strictly newest date first
    high_signal_rows.sort(key=lambda x: x["_ts_dt"], reverse=True)
    appendix_rows.sort(key=lambda x: x["_ts_dt"], reverse=True)

    # Remove any Appendix item whose exact story is already represented in High-Signal by a newer source
    appendix_rows = [
        ar for ar in appendix_rows
        if ar["Filter_Reason"] != "Earlier article / Archival news" or not is_duplicate(ar, high_signal_rows, threshold=0.55)
    ]

    print(
        f"Triage complete: {len(high_signal_rows)} high-signal updates, "
        f"{len(appendix_rows)} appendix items (from {total_scanned_14d} scanned items in 14d window)."
    )

    start_str = cutoff_date.strftime("%d %b")
    end_str = now.strftime("%d %b %Y")
    range_str = f"{start_str} – {end_str}"

    # 5. Assign sequential 1-to-1 Reference IDs: 1..X for Section 2, X+1..X+Z for Section 4
    ref_url_map = {}
    all_ref_items = []
    competitors_seen = []

    for idx, r in enumerate(high_signal_rows, start=1):
        r["ref_num"] = idx
        ref_url_map[idx] = r["Source_URL"]
        all_ref_items.append(r)
        if r["Who"] not in competitors_seen and r["Who"] != "Marketplace":
            competitors_seen.append(r["Who"])

    offset = len(high_signal_rows)
    for idx_a, ar in enumerate(appendix_rows, start=offset + 1):
        ar["ref_num"] = idx_a
        ref_url_map[idx_a] = ar["Source_URL"]
        all_ref_items.append(ar)

    active_comp_str = ", ".join(competitors_seen) if competitors_seen else "All Competitors"
    report_header = f"Feature & Product Launch Update, {start_str} to {end_str} | {active_comp_str}"
    snapshot_line = (
        f"Reporting Period: {range_str} | Market: Philippines (Ride-Hailing & MC Taxi) | "
        f"Signal Summary: {len(high_signal_rows)} key updates distilled from {total_scanned_14d} scanned items "
        f"({len(appendix_rows)} low-signal/older/static items moved to Appendix)."
    )

    # 6. Build Section 4 (Appendix Markdown) & Section 5 (References Markdown)
    if appendix_rows:
        md_appendix_lines = [
            "| Scan Date | Who | Item Summary | Filter Reason | Ref |",
            "| :--- | :--- | :--- | :--- | :--- |"
        ]
        for ar in appendix_rows:
            n = ar["ref_num"]
            md_appendix_lines.append(
                f"| {ar['Date']} | {ar['Who']} | {ar['What']} | {ar['Filter_Reason']} | [\\[Source {n}\\]]({ar['Source_URL']}) |"
            )
        md_appendix_block = "\n".join(md_appendix_lines)
    else:
        md_appendix_block = (
            "| Scan Date | Who | Item Summary | Filter Reason | Ref |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n"
            f"| {now.strftime('%d %b %Y')} | — | No low-signal, archival, or static items recorded in this cycle. | — | — |"
        )

    if all_ref_items:
        md_ref_lines = []
        plain_ref_lines = []
        for item in all_ref_items:
            n = item["ref_num"]
            md_ref_lines.append(
                f"{n}. {item['Source_Name']} — [{item['Clean_Title']}]({item['Source_URL']}) ({item['Date']})"
            )
            plain_ref_lines.append(
                f"{n}. {item['Source_Name']} — {item['Clean_Title']} ({item['Date']}): {item['Source_URL']}"
            )
        refs_md_block = "\n".join(md_ref_lines)
        refs_plain_block = "\n".join(plain_ref_lines)
    else:
        refs_md_block = "1. No reference citations recorded for this reporting period."
        refs_plain_block = "No reference citations recorded for this reporting period."

    # 7. Handle No-Update Policy when 0 High-Signal items exist
    if not high_signal_rows:
        print("No high-signal updates in the past 14 days. Generating 5-section 'No Updates' stability report...")
        bottom_line_raw = (
            f"• **Market & Competitive Stability:** Across the {range_str} tracking window, no high-signal product launches, commission shifts, or new city expansions were rolled out by Grab PH, Move It, Angkas, JoyRide PH, or inDrive PH.\n"
            "• **App Release Cadence:** Monitored iOS and Android driver/passenger releases were limited to routine maintenance and stability patches without major UX or workflow overhauls.\n"
            "• **Regulatory Posture:** No new binding LTFRB or DOTr fare-matrix or fleet-cap directives took effect during this 14-day cycle."
        )
        action_items_raw = (
            "• **Maintain Automated Market & Release Monitoring (Product & Market Intelligence):** Continue 48-hour automated scans across competitor press rooms, official portals, and App Store/Play Store changelogs.\n"
            "• **Verify Regional Promo & Driver Incentive Baselines (Product & Driver Ops):** Conduct routine spot-checks on Metro Manila and provincial fare/incentive competitiveness while competitor product roadmaps remain quiet."
        )

        table_sheet_rows = [
            ['Date', 'Who', 'What (Feature / Launch)', 'Side', 'Ref', 'Full_Reference_Citation'],
            [now.strftime('%d %b %Y'), 'All Competitors', 'No high-signal feature, policy, or market launches recorded in the past 14 days.', 'Marketplace', '—', 'N/A']
        ]
        md_main_table_block = (
            "| Date | Who | What (Feature / Launch) | Side | Ref |\n"
            "| :--- | :--- | :--- | :--- | :--- |\n"
            f"| {now.strftime('%d %b %Y')} | All Competitors | No high-signal feature, policy, or market launches recorded in the past 14 days. | Marketplace | — |"
        )

        table_sheet.clear()
        table_sheet.update(range_name='A1', values=table_sheet_rows, value_input_option='USER_ENTERED')

        archive_sheet.append_row([
            now.strftime("%Y-%m-%d"),
            f"{report_header}\n{snapshot_line}",
            bottom_line_raw,
            0,
            action_items_raw,
            refs_plain_block
        ])

        bottom_line_md = _linkify_sources_markdown(bottom_line_raw, ref_url_map)
        action_items_md = _linkify_sources_markdown(action_items_raw, ref_url_map)
        bottom_line_email_html = _format_bullets_html(bottom_line_raw, ref_url_map, for_telegram=False)
        action_items_email_html = _format_bullets_html(action_items_raw, ref_url_map, for_telegram=False)
        bottom_line_tg_html = _format_bullets_html(bottom_line_raw, ref_url_map, for_telegram=True)
        action_items_tg_html = _format_bullets_html(action_items_raw, ref_url_map, for_telegram=True)

        doc_content = (
            f"## {report_header}\n"
            f"*{snapshot_line}*\n\n"
            f"### Section 1: Bottom Line & Key Product Takeaways\n{bottom_line_md}\n\n"
            f"### Section 2: Feature, Policy & Market Launches\n\n{md_main_table_block}\n\n"
            f"### Section 3: What I'd Like Us to Act On\n{action_items_md}\n\n"
            f"### Section 4: Appendix: Low-Signal, Older & Static Items\n\n{md_appendix_block}\n\n"
            f"### Section 5: References & Citations\n{refs_md_block}\n"
        )
        filename = f"PH_Competitor_Report_{now.strftime('%Y%m%d')}.md"
        with open(filename, "w", encoding="utf-8") as f:
            f.write(doc_content)

        send_telegram_biweekly_report(report_header, snapshot_line, bottom_line_tg_html, action_items_tg_html, filename)
        send_email_biweekly_report(
            report_header, snapshot_line, bottom_line_email_html,
            [], action_items_email_html, appendix_rows, all_ref_items
        )
        print(f"Successfully generated 5-section stability report {filename} and sent notifications!")
        return

    # 8. Build Section 2 High-Signal Matrix (Markdown + Google Sheets)
    table_sheet_rows = [['Date', 'Who', 'What (Feature / Launch)', 'Side', 'Ref', 'Full_Reference_Citation']]
    md_table_lines = [
        "| Date | Who | What (Feature / Launch) | Side | Ref |",
        "| :--- | :--- | :--- | :--- | :--- |"
    ]
    ai_context_lines = []

    for r in high_signal_rows:
        n = r["ref_num"]
        url_escaped_sheets = r["Source_URL"].replace('"', '""')
        sheet_ref_formula = f'=HYPERLINK("{url_escaped_sheets}", "[Source {n}]")'
        citation_plain = f"[Source {n}] {r['Source_Name']} — {r['Clean_Title']} ({r['Date']}): {r['Source_URL']}"

        table_sheet_rows.append([r["Date"], r["Who"], r["What"], r["Side"], sheet_ref_formula, citation_plain])
        md_table_lines.append(
            f"| {r['Date']} | {r['Who']} | {r['What']} | {r['Side']} | [\\[Source {n}\\]]({r['Source_URL']}) |"
        )
        ai_context_lines.append(f"[Source {n}] ({r['Date']}) {r['Who']} [Side: {r['Side']}]: {r['What']}")

    # Update Tab 2 (Current_Report_Table) with clickable HYPERLINK formulas
    table_sheet.clear()
    table_sheet.update(range_name='A1', values=table_sheet_rows, value_input_option='USER_ENTERED')

    # 9. Synthesize Section 1 (30-Second Thematic Bottom Line) & Section 3 (Owner-Tagged Action Items)
    ai_input = "\n".join(ai_context_lines)
    max_src = len(high_signal_rows)
    prompt = f"""
You are a Principal Product Manager for a Ride-Hailing & MC Taxi platform operating in the Philippines.
Below is the verified High-Signal Matrix of competitor updates from the past 14 days, numbered [Source 1] through [Source {max_src}]:

{ai_input}

Write TWO executive sections in crisp, 100% professional English, separated by '|||'.

STRICT RULES:
1. Section 1 (Bottom Line & Key Product Takeaways):
   - Group insights into 4–5 strategic themes (NOT a company-by-company list). Bold each theme title at the start of the bullet, e.g.:
     • **Driver Commission & Fuel Relief:** ... [Source X]
     • **Passenger UX & App Upgrades:** ... [Source Y]
     • **Superapp & Market Expansion:** ... [Source Z]
     • **Driver Engagement & Readiness:** ... [Source W]
     • **Regulatory & Platform Governance:** ... [Source V]
   - Write complete, concise sentences (never truncate with '...').
   - MANDATORY: Cite exact [Source X] tags right at the end of each claim/sentence. Only use [Source 1] to [Source {max_src}].

2. Section 3 (What I'd Like Us to Act On):
   - Write 3–5 concise, concrete action bullets for our team.
   - Start each bullet with a bold action title followed by the responsible owner team in parentheses, e.g.:
     • **Benchmark Tiered Commission Structure (Product & Driver Ops):** ... [Source X]
     • **Evaluate Group Ride Split-Fare Flow (Consumer App PO):** ... [Source Y]
     • **Audit TNVS Regularization & Fare Matrix Compliance (Strategy & Legal/Ops):** ... [Source Z]
   - STRICTLY FORBIDDEN: Do NOT use P0, P1, P2, or any priority labels.
   - Every recommendation MUST be directly grounded in actual items from the table above and end with exact [Source X] tag(s). Do NOT invent off-table initiatives.

Output ONLY Section 1 bullets, followed by '|||', followed by Section 3 bullets (no section headers).
"""

    bottom_line_raw, action_items_raw = "", ""
    try:
        raw_resp = _call_gemini(prompt, temperature=0.15)
        parts = [p.strip() for p in raw_resp.split("|||")]
        if len(parts) == 2:
            cand_s1 = parts[0].strip()
            cand_s3 = _strip_priority_labels(parts[1].strip())
            # Normalize any [1] shorthand to [Source 1] if model wrote [1]
            cand_s1 = re.sub(r'\[(\d+)\]', r'[Source \1]', cand_s1)
            cand_s3 = re.sub(r'\[(\d+)\]', r'[Source \1]', cand_s3)
            cited_nums = [int(m) for m in re.findall(r'\[Source\s+(\d+)\]', cand_s1 + " " + cand_s3)]
            if (
                cited_nums
                and all(1 <= num <= max_src for num in cited_nums)
                and "**" in cand_s1
                and "(" in cand_s3
                and "..." not in cand_s1
                and "..." not in cand_s3
            ):
                bottom_line_raw = cand_s1
                action_items_raw = cand_s3
    except Exception as e:
        print(f"[Gemini Notice] Synthesis API unavailable ({e}). Using thematic Principal-PM synthesizer.")

    if not bottom_line_raw or not action_items_raw:
        bottom_line_raw, action_items_raw = _build_thematic_fallback(high_signal_rows)

    md_main_table_block = "\n".join(md_table_lines)
    bottom_line_md = _linkify_sources_markdown(bottom_line_raw, ref_url_map)
    action_items_md = _linkify_sources_markdown(action_items_raw, ref_url_map)

    bottom_line_email_html = _format_bullets_html(bottom_line_raw, ref_url_map, for_telegram=False)
    action_items_email_html = _format_bullets_html(action_items_raw, ref_url_map, for_telegram=False)
    bottom_line_tg_html = _format_bullets_html(bottom_line_raw, ref_url_map, for_telegram=True)
    action_items_tg_html = _format_bullets_html(action_items_raw, ref_url_map, for_telegram=True)

    # 10. Archive to Tab 3 (BiWeekly_Overview_Archive)
    archive_sheet.append_row([
        now.strftime("%Y-%m-%d"),
        f"{report_header}\n{snapshot_line}",
        bottom_line_raw,
        len(high_signal_rows),
        action_items_raw,
        refs_plain_block
    ])

    # 11. Export 5-Section Executive English Markdown Document
    doc_content = (
        f"## {report_header}\n"
        f"*{snapshot_line}*\n\n"
        f"### Section 1: Bottom Line & Key Product Takeaways\n{bottom_line_md}\n\n"
        f"### Section 2: Feature, Policy & Market Launches\n\n{md_main_table_block}\n\n"
        f"### Section 3: What I'd Like Us to Act On\n{action_items_md}\n\n"
        f"### Section 4: Appendix: Low-Signal, Older & Static Items\n\n{md_appendix_block}\n\n"
        f"### Section 5: References & Citations\n{refs_md_block}\n"
    )
    filename = f"PH_Competitor_Report_{now.strftime('%Y%m%d')}.md"
    with open(filename, "w", encoding="utf-8") as f:
        f.write(doc_content)

    # 12. Deliver Notifications (Telegram + HTML Email)
    send_telegram_biweekly_report(report_header, snapshot_line, bottom_line_tg_html, action_items_tg_html, filename)
    send_email_biweekly_report(
        report_header, snapshot_line, bottom_line_email_html,
        high_signal_rows, action_items_email_html, appendix_rows, all_ref_items
    )
    print(f"Successfully exported 5-section executive report {filename} and delivered notifications!")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "collect"
    if mode == "collect":
        job_collect_ph_data()
    elif mode == "overview":
        job_generate_ph_biweekly_overview()
