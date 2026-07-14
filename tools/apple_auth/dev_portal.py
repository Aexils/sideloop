"""Preuve : appel developerservices2 (listTeams) avec le token app."""
import plistlib
import uuid
import sys
import requests

from grandslam.gsa import authenticate, Anisette
from devauth import fetch_app_token

CLIENT_ID = "XABBG36SBA"
PROTO = "QH65B2"
BASE = f"https://developerservices2.apple.com/services/{PROTO}"


def dev_request(action, gs_token, adsid, anisette, extra=None):
    body = {
        "clientId": CLIENT_ID,
        "protocolVersion": PROTO,
        "requestId": str(uuid.uuid4()).upper(),
        "userLocale": ["en_US"],
    }
    if extra:
        body.update(extra)
    headers = {
        "Content-Type": "text/x-xml-plist",
        "User-Agent": "Xcode",
        "Accept": "text/x-xml-plist",
        "X-Apple-I-Identity-Id": adsid,
        "X-Apple-GS-Token": gs_token,
    }
    headers.update(anisette.generate_headers(client_info=True))
    url = f"{BASE}/{action}?clientId={CLIENT_ID}"
    r = requests.post(url, data=plistlib.dumps(body), headers=headers, verify=False, timeout=20)
    print(f"POST {action} -> {r.status_code}")
    try:
        return plistlib.loads(r.content)
    except Exception:
        print("  non-plist:", r.text[:300])
        return None


if __name__ == "__main__":
    with open("/root/.sideloader-pw") as f:
        pw = f.read().strip()
    ani = Anisette("http://127.0.0.1:6969/")
    spd = authenticate("levasseur.alexis@uqam.ca", pw, ani)
    adsid = spd["adsid"]
    toks = fetch_app_token(ani, spd, "com.apple.gs.xcode.auth")
    gs_token = toks["t"]["com.apple.gs.xcode.auth"]["token"]
    print("=== listTeams via developerservices2 ===")
    r = dev_request("listTeams.action", gs_token, adsid, ani)
    if r:
        teams = r.get("teams", [])
        for t in teams:
            print("  TEAM:", t.get("name"), "| id:", t.get("teamId"), "| type:", t.get("type"))
        if not teams:
            print("  reponse:", {k: str(v)[:80] for k, v in r.items()})
