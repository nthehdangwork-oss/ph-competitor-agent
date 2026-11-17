"""
Backfill script: Update existing Release Notes rows in Google Sheet 
with actual detailed changelog content parsed from App Store / Play Store.
Run once to fix historical data, then future collect runs will auto-populate correctly.
"""
import os, sys, re, requests
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
import gspread
from google_play_scraper import app as play_app

load_dotenv()
sys.path.insert(0, '.')

SCOPES = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_file('service_account.json', scopes=SCOPES)
gc = gspread.authorize(creds)

SHEET_URL = os.getenv("SHEET_URL")
spreadsheet = gc.open_by_url(SHEET_URL)
raw_sheet = spreadsheet.worksheet('Raw_Data')

# Fetch all App Store release notes fresh
IOS_APPS = [
    {"competitor": "Move It",    "type": "iOS App", "app_id": "1481198245"},
    {"competitor": "Grab PH",    "type": "iOS App", "app_id": "647268330"},
    {"competitor": "inDrive PH", "type": "iOS App", "app_id": "780125801"},
]

PLAY_APPS = [
    {"competitor": "Grab PH",    "type": "Driver App",           "app_id": "com.grabtaxi.driver2"},
    {"competitor": "Grab PH",    "type": "Customer App",         "app_id": "com.grabtaxi.passenger"},
    {"competitor": "Angkas",     "type": "Driver App",           "app_id": "com.angkas.drivernew"},
    {"competitor": "JoyRide PH", "type": "Driver App",           "app_id": "com.joyride.driverv3"},
    {"competitor": "Move It",    "type": "Customer App",         "app_id": "com.moveit.app.customer"},
    {"competitor": "inDrive PH", "type": "Customer & Driver App","app_id": "sinet.startup.inDriver"},
]

def extract_bullets(notes_text, version):
    """Rule-based: extract feature/fix bullets from release notes text."""
    clean = re.sub(r'<[^>]+>', '', notes_text).strip()
    lines = [l.strip() for l in re.split(r'[\n\r•\-–]', clean) if len(l.strip()) > 10]
    signal_words = [
        "new", "add", "added", "feature", "improve", "improved", "fix", "fixed", "update",
        "enhance", "enhanced", "launch", "introduce", "support", "tracking", "book",
        "ride", "safety", "wallet", "payment", "bug", "performance", "real-time", "real time",
        "feedback", "receipt", "share", "notification", "alert", "ui", "ux", "experience",
    ]
    relevant = [l for l in lines if any(w in l.lower() for w in signal_words)]
    if not relevant:
        return f"v{version} — Minor bug fixes and performance improvements; no specific new features listed."
    summary = "; ".join(relevant[:4])
    if len(summary) > 300:
        summary = summary[:297] + "..."
    return f"v{version} — {summary}"

# Build lookup: title_keyword -> detailed_what
detail_map = {}

print("Fetching App Store release notes...")
for app in IOS_APPS:
    r = requests.get(f"https://itunes.apple.com/lookup?id={app['app_id']}&country=ph", timeout=10)
    info = r.json()["results"][0]
    version = info.get("version", "Latest")
    notes   = info.get("releaseNotes", "")
    title_key = f"{app['competitor']} {app['type']} v{version} Release Notes"
    what = extract_bullets(notes, version) if notes else f"v{version} — No release notes available."
    detail_map[title_key] = what
    print(f"  {title_key}: {what[:80]}...")

print("\nFetching Play Store recent changes...")
for app in PLAY_APPS:
    try:
        details = play_app(app["app_id"], lang="en", country="ph")
        version = details.get("version", "Latest")
        notes   = details.get("recentChanges", "")
        title_key = f"{app['competitor']} {app['type']} v{version} Release Notes"
        what = extract_bullets(notes, version) if notes else f"v{version} — No recent changes listed."
        detail_map[title_key] = what
        print(f"  {title_key}: {what[:80]}...")
    except Exception as e:
        print(f"  Skipped {app['app_id']}: {e}")

# Now scan Google Sheet and patch rows that have generic "Release Notes" in What column
print("\nScanning Google Sheet for rows to patch...")
all_rows = raw_sheet.get_all_values()
header = all_rows[0]
# Columns (0-indexed): Timestamp_ISO, Date, Who, What, Side, Source_Name, Source_Title, Source_URL
COL_WHAT  = header.index("What")
COL_TITLE = header.index("Source_Title")

patched = 0
for i, row in enumerate(all_rows[1:], start=2):  # start=2 because gspread is 1-indexed, row 1 is header
    source_title = row[COL_TITLE] if len(row) > COL_TITLE else ""
    current_what = row[COL_WHAT] if len(row) > COL_WHAT else ""

    # Only patch rows where What is still a bare "Release Notes" label (old data)
    if "Release Notes" in current_what and "v" not in current_what[:20]:
        # Find matching detail from detail_map
        matched_detail = None
        for key, detail in detail_map.items():
            if source_title and source_title.strip() == key.strip():
                matched_detail = detail
                break
            # fuzzy: check if competitor + version both appear in source_title
            parts = key.split(" v")
            if len(parts) == 2 and parts[0] in source_title and f"v{parts[1].split(' ')[0]}" in source_title:
                matched_detail = detail
                break

        if matched_detail:
            cell_ref = f"{chr(65 + COL_WHAT)}{i}"  # e.g. D12
            raw_sheet.update_acell(cell_ref, matched_detail)
            print(f"  Row {i}: '{source_title}' → patched What with '{matched_detail[:80]}...'")
            patched += 1

print(f"\nDone! Patched {patched} rows with detailed release notes content.")
