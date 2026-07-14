"""Signature d'IPA avec un Apple ID gratuit — implémentation maison, validée.

Chaîne prouvée le 2026-07-13 (Spotify re-signé + installé sans fil sur iOS 27) :
  auth GrandSlam (2FA-free via anisette stable trustée) → token developerservices2
  → portail dev (addDevice / cert / App ID / profil) → zsign.

Dépendances runtime (fournies par l'image Docker) :
  * `grandslam` (JJTech0130) PATCHÉ — voir tools/apple_auth/grandslam-gsa.patch
    (sms_second_factor implémenté ; authenticate() retourne le spd).
  * serveur anisette v3 (stable, même machine trustée) — URL via settings.
  * binaire `zsign`.

⚠ L'INSTALL n'est PAS ici : elle se fait sur pve (accès L2/mDNS au device). Ce
module produit une IPA signée ; l'agent pve la pousse par le tunnel Wi-Fi.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import plistlib
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import requests

from grandslam.gsa import Anisette, authenticate

CLIENT_ID = "XABBG36SBA"
PROTOCOL = "QH65B2"
DEV_BASE = f"https://developerservices2.apple.com/services/{PROTOCOL}"
XCODE_APP = "com.apple.gs.xcode.auth"

# developerservices2 accepte des plists ; les actions ios/* exigent le teamId.
requests.packages.urllib3.disable_warnings()  # anisette/gsa en verify désactivé côté grandslam


@dataclass
class Session:
    anisette: Anisette
    adsid: str
    gs_token: str
    team_id: str


def _decrypt_gcm(key: bytes, data: bytes) -> bytes:
    """Déchiffre un token app Apple (`et`) : [version 3o][iv 16o][ct+tag], AAD=version."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    version, iv, ct = data[:3], data[3:19], data[19:]
    return AESGCM(key).decrypt(iv, ct, version)


def _fetch_app_token(anisette: Anisette, spd: dict, app: str) -> str:
    """Flux GrandSlam `apptokens` → token app-specific (déchiffré)."""
    from grandslam.gsa import authenticated_request

    checksum = hmac.new(spd["sk"], digestmod=hashlib.sha256)
    checksum.update(b"apptokens")
    checksum.update(spd["adsid"].encode())
    checksum.update(app.encode())
    r = authenticated_request(
        {
            "u": spd["adsid"],
            "app": [app],
            "c": spd["c"],
            "t": spd["GsIdmsToken"],
            "checksum": checksum.digest(),
            "o": "apptokens",
        },
        anisette,
    )
    decrypted = _decrypt_gcm(spd["sk"], r["et"])
    tokens = _loads_plist(decrypted)
    return tokens["t"][app]["token"]


# Les plists Apple renvoyés chiffrés arrivent SANS en-tête XML — le préfixer.
_PLIST_HEADER = (
    b"<?xml version='1.0' encoding='UTF-8'?>"
    b"<!DOCTYPE plist PUBLIC '-//Apple//DTD PLIST 1.0//EN' "
    b"'http://www.apple.com/DTDs/PropertyList-1.0.dtd'>"
)


def _loads_plist(data: bytes) -> dict:
    try:
        return plistlib.loads(data)
    except Exception:
        return plistlib.loads(_PLIST_HEADER + data)


def login(apple_id: str, password: str, anisette_url: str, team_id: str) -> Session:
    """Auth complète → Session utilisable pour le portail dev.

    Le 2FA n'est PAS géré ici : l'anisette doit être une machine DÉJÀ trustée
    (2FA fait une fois hors-ligne). Un login sur machine non-trustée lèvera.
    """
    anisette = Anisette(anisette_url)
    spd = authenticate(apple_id, password, anisette)
    if not isinstance(spd, dict) or "adsid" not in spd:
        raise RuntimeError(
            "Login GrandSlam échoué (2FA requis ? anisette non trustée ?). "
            "L'anisette doit être la machine trustée une fois par SMS."
        )
    gs_token = _fetch_app_token(anisette, spd, XCODE_APP)
    return Session(anisette=anisette, adsid=spd["adsid"], gs_token=gs_token, team_id=team_id)


def dev_request(session: Session, action: str, extra: dict | None = None) -> dict:
    """Requête plist vers developerservices2 (les actions ios/* reçoivent le teamId)."""
    body = {
        "clientId": CLIENT_ID,
        "protocolVersion": PROTOCOL,
        "requestId": str(uuid.uuid4()).upper(),
        "userLocale": ["en_US"],
    }
    if extra:
        body.update(extra)
    if action.startswith("ios/"):
        body.setdefault("teamId", session.team_id)
    headers = {
        "Content-Type": "text/x-xml-plist",
        "User-Agent": "Xcode",
        "Accept": "text/x-xml-plist",
        "X-Apple-I-Identity-Id": session.adsid,
        "X-Apple-GS-Token": session.gs_token,
    }
    headers.update(session.anisette.generate_headers(client_info=True))
    r = requests.post(
        f"{DEV_BASE}/{action}?clientId={CLIENT_ID}",
        data=plistlib.dumps(body), headers=headers, verify=False, timeout=30,
    )
    return plistlib.loads(r.content)


def _ok(r: dict, what: str) -> dict:
    if r.get("resultCode") not in (0, None):
        raise RuntimeError(f"{what}: {r.get('resultCode')} {r.get('userString')}")
    return r


def ensure_device(session: Session, udid: str, name: str = "iPhone") -> None:
    r = dev_request(session, "ios/addDevice.action",
                    {"deviceNumber": udid, "name": name, "DTDK_Platform": "ios"})
    # resultCode 35 = déjà enregistré → OK
    if r.get("resultCode") not in (0, 35, None):
        raise RuntimeError(f"addDevice: {r.get('resultCode')} {r.get('userString')}")


def ensure_cert(session: Session, workdir: Path) -> Path:
    """Révoque les certs existants, en soumet un nouveau (on garde la clé).

    Retourne le chemin de la clé privée PEM (le cert est extrait du profil ensuite).
    """
    r = _ok(dev_request(session, "ios/listAllDevelopmentCerts.action", {}), "listCerts")
    for c in r.get("certificates", []):
        sn = c.get("serialNumber") or c.get("serialNum")
        dev_request(session, "ios/revokeDevelopmentCert.action", {"serialNumber": sn})
    key = workdir / "key.pem"
    csr = workdir / "csr.pem"
    subprocess.run(
        ["openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes",
         "-keyout", str(key), "-out", str(csr), "-subj", "/CN=sideloop/O=sideloop"],
        check=True, capture_output=True,
    )
    _ok(dev_request(session, "ios/submitDevelopmentCSR.action",
                    {"csrContent": csr.read_text(),
                     "machineId": str(uuid.uuid4()).upper(),
                     "machineName": "sideloop"}), "submitCSR")
    return key


def ensure_app_id(session: Session, bundle_id: str, name: str) -> str:
    r = dev_request(session, "ios/addAppId.action", {"identifier": bundle_id, "name": name})
    app_id_id = (r.get("appId") or {}).get("appIdId")
    if app_id_id:
        return app_id_id
    # déjà existant → retrouver
    for a in _ok(dev_request(session, "ios/listAppIds.action", {}), "listAppIds").get("appIds", []):
        if a.get("identifier") == bundle_id:
            return a["appIdId"]
    raise RuntimeError(f"App ID introuvable/non créable pour {bundle_id}: {r.get('userString')}")


def download_profile(session: Session, app_id_id: str, out: Path) -> None:
    r = _ok(dev_request(session, "ios/downloadTeamProvisioningProfile.action",
                        {"appIdId": app_id_id}), "profile")
    enc = (r.get("provisioningProfile") or {}).get("encodedProfile")
    if not enc:
        raise RuntimeError("profil sans encodedProfile")
    out.write_bytes(enc)


def build_p12(profile: Path, key: Path, out_p12: Path) -> None:
    """Extrait le cert du profil (il l'inclut) + clé → p12 (sans mot de passe)."""
    p7 = subprocess.run(
        ["openssl", "smime", "-inform", "der", "-verify", "-noverify", "-in", str(profile)],
        capture_output=True, check=True,
    )
    cert_der = plistlib.loads(p7.stdout)["DeveloperCertificates"][0]
    with tempfile.TemporaryDirectory() as td:
        der = Path(td) / "c.der"
        pem = Path(td) / "c.pem"
        der.write_bytes(cert_der)
        subprocess.run(["openssl", "x509", "-inform", "der", "-in", str(der), "-out", str(pem)],
                       check=True, capture_output=True)
        subprocess.run(["openssl", "pkcs12", "-export", "-inkey", str(key), "-in", str(pem),
                        "-out", str(out_p12), "-passout", "pass:", "-legacy"],
                       check=True, capture_output=True)


def sign_ipa(session: Session, ipa_in: Path, ipa_out: Path, bundle_id: str,
             app_name: str, device_udids: list[str], zsign_bin: str = "zsign") -> Path:
    """Pipeline complet : device(s) → cert → App ID → profil → p12 → zsign.

    `bundle_id` doit être UNIQUE et enregistrable par le compte (l'ID d'origine
    d'apps connues comme com.spotify.client est réservé → re-bundle via zsign -b).
    """
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        for udid in device_udids:
            ensure_device(session, udid)
        key = ensure_cert(session, work)
        app_id_id = ensure_app_id(session, bundle_id, app_name)
        profile = work / "app.mobileprovision"
        download_profile(session, app_id_id, profile)
        p12 = work / "id.p12"
        build_p12(profile, key, p12)
        r = subprocess.run(
            [zsign_bin, "-k", str(p12), "-p", "", "-m", str(profile),
             "-b", bundle_id, "-o", str(ipa_out), str(ipa_in)],
            capture_output=True, text=True,
        )
        if r.returncode != 0 or not ipa_out.exists():
            raise RuntimeError(f"zsign a échoué: {(r.stdout + r.stderr)[-500:]}")
    return ipa_out
