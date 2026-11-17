import requests
from google_play_scraper import app as play_app

IOS_APPS = [
    {"name": "Move It",    "id": "1481198245"},
    {"name": "Grab PH",    "id": "647268330"},
    {"name": "inDrive PH", "id": "780125801"},
]

PLAY_APPS = [
    {"name": "Grab PH Driver",   "id": "com.grabtaxi.driver2"},
    {"name": "Grab PH Customer", "id": "com.grabtaxi.passenger"},
    {"name": "Move It Customer", "id": "com.moveit.app.customer"},
    {"name": "inDrive",          "id": "sinet.startup.inDriver"},
    {"name": "Angkas Driver",    "id": "com.angkas.drivernew"},
    {"name": "JoyRide Driver",   "id": "com.joyride.driverv3"},
]

print("=" * 60)
print("APPLE APP STORE RELEASE NOTES")
print("=" * 60)
for app in IOS_APPS:
    r = requests.get(f"https://itunes.apple.com/lookup?id={app['id']}&country=ph", timeout=10)
    info = r.json()["results"][0]
    print(f"\n>>> {app['name']} v{info.get('version')}")
    print(info.get("releaseNotes", "(no release notes)"))

print("\n" + "=" * 60)
print("GOOGLE PLAY STORE RECENT CHANGES")
print("=" * 60)
for app in PLAY_APPS:
    try:
        details = play_app(app["id"], lang="en", country="ph")
        print(f"\n>>> {app['name']} v{details.get('version')}")
        print(details.get("recentChanges") or "(no recent changes)")
    except Exception as e:
        print(f"\n>>> {app['name']}: ERROR - {e}")
