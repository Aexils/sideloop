"""Echange de token app (flux GrandSlam 'apptokens') -> token pour developerservices2."""
import hmac
import hashlib
import plistlib
import sys

from grandslam.gsa import authenticate, authenticated_request, Anisette
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def decrypt_gcm(key: bytes, data: bytes) -> bytes:
    # Format Apple 'et' : [version 3o][iv 16o][ciphertext+tag]
    version = data[:3]
    iv = data[3:3 + 16]
    ct = data[3 + 16:]
    return AESGCM(key).decrypt(iv, ct, version)


def fetch_app_token(anisette, spd, app):
    sk = spd["sk"]
    adsid = spd["adsid"]
    h = hmac.new(sk, digestmod=hashlib.sha256)
    h.update(b"apptokens")
    h.update(adsid.encode())
    h.update(app.encode())
    checksum = h.digest()
    r = authenticated_request({
        "u": adsid,
        "app": [app],
        "c": spd["c"],
        "t": spd["GsIdmsToken"],
        "checksum": checksum,
        "o": "apptokens",
    }, anisette)
    print("apptokens response keys:", list(r.keys()))
    et = r["et"]
    print("et len:", len(et), "first3:", et[:3])
    decrypted = decrypt_gcm(sk, et)
    print("DECRYPTED[:150]:", decrypted[:150])
    HDR = b"<?xml version='' encoding='UTF-8'?><!DOCTYPE plist PUBLIC '-//Apple//DTD PLIST 1.0//EN' 'http://www.apple.com/DTDs/PropertyList-1.0.dtd'>"
    try:
        tokens = plistlib.loads(decrypted)
    except Exception:
        tokens = plistlib.loads(HDR + decrypted)
    return tokens


if __name__ == "__main__":
    with open("/root/.sideloader-pw") as f:
        pw = f.read().strip()
    ani = Anisette("http://127.0.0.1:6969/")
    spd = authenticate("levasseur.alexis@uqam.ca", pw, ani)
    app = sys.argv[1] if len(sys.argv) > 1 else "com.apple.gs.xcode.auth"
    print("=== fetch app token pour:", app)
    toks = fetch_app_token(ani, spd, app)
    print("TOKENS:", {k: (str(v)[:40] + "...") for k, v in toks.get("t", {}).get(app, {}).items()} if "t" in toks else toks)
