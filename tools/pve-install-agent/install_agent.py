#!/usr/bin/env python3
"""Agent d'install sideloop — TOURNE SUR PVE (pas en k8s).

Le CronJob k8s signe les IPA et écrit un manifest NFS ; cet agent les installe
sur les iPhones par le tunnel Wi-Fi (accès L2/mDNS = capacité de pve, comme
Tailscale). Idempotent : ne réinstalle pas un manifest déjà traité.

Prérequis pve (one-time) :
  * usbmuxd + pymobiledevice3 (voir /opt/grandslam) ;
  * RemotePairing des devices fait (records dans /root/.pymobiledevice3/) ;
  * `pymobiledevice3 remote tunneld` lancé en service (les iPhones sur BELL338) ;
  * export NFS de SIGNED_DIR vers 10.10.10.0/24.

Lancement : systemd timer (voir sideloop-install-agent.timer) ou à la main.
"""
import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

SIGNED_DIR = Path("/mnt/media/sideloop-signed")
PMD = "/root/.local/bin/pymobiledevice3"
TUNNELD_UNIT = "sideloop-tunneld"
# État d'install remonté vers k8s (lu par sideloop /api/status → Nexus).
INSTALL_STATUS = SIGNED_DIR / "install-status.json"

# Robustesse : le tunnel RemoteXPC (iOS 26/27, Wi-Fi) décroche parfois pendant
# les gros transferts (~200 Mo) → on retente avec reconstruction du tunnel.
MAX_ATTEMPTS = 3
# Signatures d'erreur = drop de tunnel transitoire → ça vaut le coup de retenter.
RETRYABLE = (
    "terminated abruptly", "connectionterminated", "incompleteread",
    "connection reset", "streamerror", "timeout", "device error",
    "broken pipe", "eof", "connection refused",
)

_INFO_CACHE: dict[str, tuple[str, str]] = {}


def restart_tunneld() -> None:
    """Reconstruit tous les tunnels (le device réveillé sera redécouvert en mDNS)."""
    subprocess.run(["systemctl", "restart", TUNNELD_UNIT], check=False)


def tunnel_ready(udid: str, timeout: int = 12) -> bool:
    """True si le tunnel du device répond (lockdown joignable)."""
    try:
        r = subprocess.run([PMD, "lockdown", "info", "--tunnel", udid],
                           capture_output=True, text=True, timeout=timeout)
        return '"DeviceName"' in r.stdout
    except Exception:  # noqa: BLE001
        return False


def ensure_tunnel(udid: str, wait: int = 90) -> bool:
    """Garantit un tunnel vivant : si muet, restart tunneld et attend qu'il revienne."""
    if tunnel_ready(udid):
        return True
    print(f"    {udid}: tunnel muet → reconstruction (restart tunneld)")
    restart_tunneld()
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(8)
        if tunnel_ready(udid):
            return True
    return False


def device_info(udid: str) -> tuple[str, str]:
    """(DeviceName, ProductType) de l'appareil via le tunnel. Caché par run.

    ProductType (ex. iPhone16,1) est mappé en nom commercial côté sideloop
    (status.py). Best-effort : ("", "") si échec → le dashboard retombe sur l'UDID."""
    if udid in _INFO_CACHE:
        return _INFO_CACHE[udid]
    name, ptype = "", ""
    try:
        r = subprocess.run([PMD, "lockdown", "info", "--tunnel", udid],
                           capture_output=True, text=True, timeout=30)
        for line in r.stdout.splitlines():
            if '"DeviceName"' in line:
                name = line.split(":", 1)[1].strip().strip('",')
            elif '"ProductType"' in line:
                ptype = line.split(":", 1)[1].strip().strip('",')
    except Exception:  # noqa: BLE001 — best-effort, jamais bloquant
        pass
    _INFO_CACHE[udid] = (name, ptype)
    return name, ptype


def _install_once(ipa: Path, udid: str) -> tuple[bool, str]:
    """Une tentative d'install. --tunnel : passe par tunneld (Wi-Fi)."""
    try:
        r = subprocess.run([PMD, "apps", "install", "--tunnel", udid, str(ipa)],
                           capture_output=True, text=True, timeout=600)
        out = r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return False, "timeout (transfert interrompu ?)"
    if "Installation succeed" in out:
        return True, ""
    return False, out[-300:].strip()[-200:]


def install(ipa: Path, udid: str) -> tuple[bool, str]:
    """Installe l'IPA avec reprise : re-check du tunnel + jusqu'à MAX_ATTEMPTS essais.

    Absorbe les drops de tunnel transitoires (iOS 26/27 sous gros transfert).
    Renvoie (ok, dernière erreur)."""
    last_err = "non tenté"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if not ensure_tunnel(udid):
            last_err = "tunnel injoignable (device en veille / hors Wi-Fi ?)"
            print(f"    {udid}: tunnel KO (essai {attempt}/{MAX_ATTEMPTS})")
            time.sleep(5)
            continue

        ok, err = _install_once(ipa, udid)
        if ok:
            print(f"    {udid}: OK" + (f" (essai {attempt})" if attempt > 1 else ""))
            return True, ""

        last_err = err
        retryable = (not err) or any(k in err.lower() for k in RETRYABLE)
        will_retry = retryable and attempt < MAX_ATTEMPTS
        print(f"    {udid}: ÉCHEC essai {attempt}/{MAX_ATTEMPTS}"
              + (" → retry" if will_retry else ""))
        if not will_retry:
            break
        # Drop de tunnel probable → on le reconstruit avant de retenter.
        restart_tunneld()
        time.sleep(8)

    print(f"     {last_err}")
    return False, last_err


def write_status(sig: str, results: list[dict]) -> None:
    """Écrit install-status.json (schéma sideloop.models.InstallStatus)."""
    payload = {
        "manifest_sig": sig,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "results": results,
    }
    tmp = INSTALL_STATUS.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(INSTALL_STATUS)


def main() -> int:
    manifest = SIGNED_DIR / "manifest.json"
    if not manifest.exists():
        print("Aucun manifest.")
        return 0
    data = json.loads(manifest.read_text())
    # empreinte pour ne pas retraiter le même manifest
    sig = hashlib.sha256(manifest.read_bytes()).hexdigest()[:16]
    done = SIGNED_DIR / f".done-{sig}"
    if done.exists():
        print("Manifest déjà installé.")
        return 0

    all_ok = True
    results: list[dict] = []
    for e in data.get("entries", []):
        ipa = SIGNED_DIR / e["signed_ipa"]
        if not ipa.exists():
            print(f"[{e['name']}] IPA absente: {ipa}")
            all_ok = False
            for udid in e["device_udids"]:
                name, ptype = device_info(udid)
                results.append({"bundle_id": e["bundle_id"], "udid": udid, "ok": False,
                                "at": datetime.now(timezone.utc).isoformat(),
                                "device_name": name, "product_type": ptype,
                                "error": "IPA signée absente"})
            continue
        print(f"[{e['name']}] {e['bundle_id']} → {len(e['device_udids'])} device(s)")
        for udid in e["device_udids"]:
            ok, err = install(ipa, udid)
            all_ok &= ok
            name, ptype = device_info(udid)
            results.append({"bundle_id": e["bundle_id"], "udid": udid, "ok": ok,
                            "at": datetime.now(timezone.utc).isoformat(),
                            "device_name": name, "product_type": ptype, "error": err})

    # On remonte TOUJOURS l'état (succès comme échec) pour Nexus.
    write_status(sig, results)

    if all_ok:
        done.write_text(manifest.read_text())
        print("Tous installés — marqué done.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
