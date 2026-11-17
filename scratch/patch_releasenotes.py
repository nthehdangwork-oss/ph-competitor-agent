"""Patch existing release notes rows in Google Sheet with actual content."""
import os, re, requests
from dotenv import load_dotenv
load_dotenv()
from google.oauth2.service_account import Credentials
import gspread

SCOPES = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
creds = Credentials.from_service_account_file("service_account.json", scopes=SCOPES)
gc = gspread.authorize(creds)
sh = gc.open_by_url(os.getenv("SHEET_URL")).worksheet("Raw_Data")
rows = sh.get_all_values()

IOS_APPS = [
    {"competitor": "Move It",    "type": "iOS App", "app_id": "1481198245"},
    {"competitor": "Grab PH",    "type": "iOS App", "app_id": "647268330"},
    {"competitor": "inDrive PH", "type": "iOS App", "app_id": "780125801"},
]

def extract(notes, version):
    clean = re.sub(r"<[^>]+>", "", notes).strip()
    lines = [l.strip() for l in re.split(r"[\n\r\u2022\-\u2013]", clean) if len(l.strip()) > 10]
    sigs = [
        "new", "add", "fix", "improve", "update", "track", "book", "ride",
        "safety", "wallet", "payment", "bug", "performance", "real-time",
        "feedback", "receipt", "share", "notification", "ux", "experience",
    ]
    rel = [l for l in lines if any(w in l.lower() for w in sigs)]
    if not rel:
        return f"v{version} — Minor bug fixes and performance improvements; no specific new features listed."
    s = "; ".join(rel[:4])[:297]
    return f"v{version} — {s}"

updates = {}
for app in IOS_APPS:
    app_id = app["app_id"]
    resp = requests.get(f"https://itunes.apple.com/lookup?id={app_id}&country=ph", timeout=10)
    info = resp.json()["results"][0]
    version = info.get("version", "")
    notes   = info.get("releaseNotes", "")
    key = f"{app['competitor']} {app['type']} v{version} Release Notes"
    new_what = extract(notes, version)
    updates[key] = new_what
    print(f"Fetched: {key}")
    print(f"  → {new_what[:100]}")

# Patch rows where What == Source_Title == "X iOS App v5.X.X Release Notes" (old bare label)
patched = 0
for i, row in enumerate(rows[1:], start=2):
    what = row[3] if len(row) > 3 else ""
    if what.endswith("Release Notes") and "v5." in what:
        new_what = updates.get(what)
        if new_what:
            sh.update_cell(i, 4, new_what)
            print(f"\nRow {i}: PATCHED")
            print(f"  OLD: {what}")
            print(f"  NEW: {new_what[:100]}")
            patched += 1

print(f"\nDone. Patched {patched} rows in Google Sheet.")
