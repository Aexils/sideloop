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
from datetime import datetime, timezone
from pathlib import Path

SIGNED_DIR = Path("/mnt/media/sideloop-signed")
PMD = "/root/.local/bin/pymobiledevice3"
# État d'install remonté vers k8s (lu par sideloop /api/status → Nexus).
INSTALL_STATUS = SIGNED_DIR / "install-status.json"

_INFO_CACHE: dict[str, tuple[str, str]] = {}


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


def install(ipa: Path, udid: str) -> tuple[bool, str]:
    """Installe l'IPA sur un device. Renvoie (ok, erreur tronquée)."""
    # --tunnel : passe par tunneld (Wi-Fi), PAS --udid (qui veut usbmux/USB).
    try:
        r = subprocess.run([PMD, "apps", "install", "--tunnel", udid, str(ipa)],
                           capture_output=True, text=True, timeout=600)
        out = r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        print(f"    {udid}: ÉCHEC (timeout)")
        return False, "timeout (device injoignable ?)"
    ok = "Installation succeed" in out
    print(f"    {udid}: {'OK' if ok else 'ÉCHEC'}")
    if not ok:
        tail = out[-300:]
        print("     ", tail)
        return False, tail.strip()[-200:]
    return True, ""


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
