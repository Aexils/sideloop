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
from pathlib import Path

SIGNED_DIR = Path("/mnt/media/sideloop-signed")
PMD = "/root/.local/bin/pymobiledevice3"


def install(ipa: Path, udid: str) -> bool:
    # --tunnel : passe par tunneld (Wi-Fi), PAS --udid (qui veut usbmux/USB).
    r = subprocess.run([PMD, "apps", "install", "--tunnel", udid, str(ipa)],
                       capture_output=True, text=True, timeout=600)
    ok = "Installation succeed" in (r.stdout + r.stderr)
    print(f"    {udid}: {'OK' if ok else 'ÉCHEC'}")
    if not ok:
        print("     ", (r.stdout + r.stderr)[-300:])
    return ok


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
    for e in data.get("entries", []):
        ipa = SIGNED_DIR / e["signed_ipa"]
        if not ipa.exists():
            print(f"[{e['name']}] IPA absente: {ipa}"); all_ok = False; continue
        print(f"[{e['name']}] {e['bundle_id']} → {len(e['device_udids'])} device(s)")
        for udid in e["device_udids"]:
            all_ok &= install(ipa, udid)

    if all_ok:
        done.write_text(manifest.read_text())
        print("Tous installés — marqué done.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
