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


def _submit_new_cert(session: Session, key: Path) -> None:
    """Révoque les certs existants et en soumet un nouveau pour NOTRE clé."""
    r = _ok(dev_request(session, "ios/listAllDevelopmentCerts.action", {}), "listCerts")
    for c in r.get("certificates", []):
        sn = c.get("serialNumber") or c.get("serialNum")
        dev_request(session, "ios/revokeDevelopmentCert.action", {"serialNumber": sn})
    csr = key.parent / "csr.pem"
    subprocess.run(
        ["openssl", "req", "-new", "-key", str(key), "-out", str(csr),
         "-subj", "/CN=sideloop/O=sideloop"], check=True, capture_output=True,
    )
    _ok(dev_request(session, "ios/submitDevelopmentCSR.action",
                    {"csrContent": csr.read_text(),
                     "machineId": str(uuid.uuid4()).upper(),
                     "machineName": "sideloop"}), "submitCSR")


def _pubkey_of_cert(cert_der: bytes) -> bytes:
    return subprocess.run(["openssl", "x509", "-inform", "der", "-pubkey", "-noout"],
                          input=cert_der, capture_output=True, check=True).stdout


def _pubkey_of_key(key: Path) -> bytes:
    return subprocess.run(["openssl", "pkey", "-in", str(key), "-pubout"],
                          capture_output=True, check=True).stdout


def _profile_certs(profile: Path) -> list[bytes]:
    p7 = subprocess.run(
        ["openssl", "smime", "-inform", "der", "-verify", "-noverify", "-in", str(profile)],
        capture_output=True, check=True)
    return plistlib.loads(p7.stdout)["DeveloperCertificates"]


def ensure_identity(session: Session, app_id_id: str, cert_dir: Path, workdir: Path) -> tuple[Path, bytes, Path]:
    """Retourne (clé, cert_der, profil), en RÉUTILISANT le cert persisté si possible.

    Ne re-soumet un CSR que si notre clé n'a plus de cert associé (révoqué/expiré).
    Le cert dure ~1 an ; seul le profil (7 jours) est retéléchargé à chaque run.
    """
    cert_dir.mkdir(parents=True, exist_ok=True)
    key = cert_dir / "key.pem"
    profile = workdir / "app.mobileprovision"

    if not key.exists():
        subprocess.run(["openssl", "genrsa", "-out", str(key), "2048"],
                       check=True, capture_output=True)
        _submit_new_cert(session, key)

    download_profile(session, app_id_id, profile)
    our_pub = _pubkey_of_key(key)
    for cert_der in _profile_certs(profile):
        if _pubkey_of_cert(cert_der) == our_pub:
            return key, cert_der, profile          # cert réutilisé ✅

    # Notre cert a disparu du profil → en refaire un, puis re-télécharger.
    _submit_new_cert(session, key)
    download_profile(session, app_id_id, profile)
    for cert_der in _profile_certs(profile):
        if _pubkey_of_cert(cert_der) == our_pub:
            return key, cert_der, profile
    raise RuntimeError("cert introuvable dans le profil après soumission")


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


def build_p12(cert_der: bytes, key: Path, out_p12: Path) -> None:
    """cert (DER) + clé → p12 (sans mot de passe)."""
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
             app_name: str, device_udids: list[str], cert_dir: Path,
             zsign_bin: str = "zsign") -> Path:
    """Pipeline complet : device(s) → identité (cert réutilisé) → App ID → profil → zsign.

    `cert_dir` = dossier PERSISTANT (PVC) où vit la clé/cert réutilisés.
    `bundle_id` doit être UNIQUE et enregistrable (com.spotify.client est réservé →
    re-bundle via zsign -b).
    """
    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        for udid in device_udids:
            ensure_device(session, udid)
        app_id_id = ensure_app_id(session, bundle_id, app_name)
        key, cert_der, profile = ensure_identity(session, app_id_id, cert_dir, work)
        p12 = work / "id.p12"
        build_p12(cert_der, key, p12)
        r = subprocess.run(
            [zsign_bin, "-k", str(p12), "-p", "", "-m", str(profile),
             "-b", bundle_id, "-o", str(ipa_out), str(ipa_in)],
            capture_output=True, text=True,
        )
        if r.returncode != 0 or not ipa_out.exists():
            raise RuntimeError(f"zsign a échoué: {(r.stdout + r.stderr)[-500:]}")
    return ipa_out
