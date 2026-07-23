#!/usr/bin/env python3
"""Agent d'install sideloop — TOURNE SUR PVE (pas en k8s).

Le CronJob k8s signe les IPA et écrit un manifest NFS ; cet agent les installe
sur les iPhones par le tunnel Wi-Fi (accès L2/mDNS = capacité de pve, comme
Tailscale). Idempotent PAR DEVICE : un device servi avec succès il y a moins de
FRESH_HOURS n'est pas retenté ; un device absent ne bloque jamais les autres.

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
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

SIGNED_DIR = Path("/mnt/media/sideloop-signed")
PMD = "/root/.local/bin/pymobiledevice3"
TUNNELD_UNIT = "sideloop-tunneld"
TUNNELD_URL = "http://127.0.0.1:49151/"
# État d'install remonté vers k8s (lu par sideloop /api/status → Nexus).
INSTALL_STATUS = SIGNED_DIR / "install-status.json"
# Battement de cœur de l'agent : prouve à Nexus que l'agent pve + tunneld vivent.
HEARTBEAT = SIGNED_DIR / "agent-heartbeat.json"
# Suivi PAR DEVICE : dernier install OK par "bundle_id:udid". Un device servi il y a
# moins de FRESH_HOURS est skippé (cert 7 j → 48 h laissent 5 j de marge), les autres
# sont tentés individuellement — remplace l'ancien marqueur .done-<sig> tout-ou-rien.
INSTALLED_STATE = SIGNED_DIR / "installed-state.json"
FRESH_HOURS = 48

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


def _tunneld_udids() -> list[str]:
    """UDID des devices ayant un tunnel actif (vue de tunneld)."""
    try:
        with urllib.request.urlopen(TUNNELD_URL, timeout=5) as r:
            return list(json.load(r).keys())
    except Exception:  # noqa: BLE001
        return []


def write_heartbeat() -> None:
    """Écrit le battement de cœur (à CHAQUE run, même sans install) → Nexus détecte
    un agent ou un tunneld mort (fichier périmé)."""
    try:
        active = subprocess.run(["systemctl", "is-active", TUNNELD_UNIT],
                                capture_output=True, text=True).stdout.strip() == "active"
    except Exception:  # noqa: BLE001
        active = False
    payload = {
        "at": datetime.now(timezone.utc).isoformat(),
        "tunneld_active": active,
        "reachable_udids": _tunneld_udids(),
    }
    try:
        tmp = HEARTBEAT.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(HEARTBEAT)
    except Exception:  # noqa: BLE001 — jamais bloquant
        pass


def tunnel_ready(udid: str, timeout: int = 12) -> bool:
    """True si le tunnel du device répond (lockdown joignable)."""
    try:
        r = subprocess.run([PMD, "lockdown", "info", "--tunnel", udid],
                           capture_output=True, text=True, timeout=timeout)
        return '"DeviceName"' in r.stdout
    except Exception:  # noqa: BLE001
        return False


def reachable_udids(targets: set[str]) -> set[str]:
    """Sous-ensemble des UDID cibles RÉELLEMENT joignables (tunnel qui répond).

    ⚠ tunneld garde des entrées PÉRIMÉES : un device endormi/hors Wi-Fi reste dans
    sa liste de clés alors que son tunnel ne répond plus (lockdown timeout). On ne
    peut donc PAS se fier à _tunneld_udids() seul → on SONDE lockdown pour de vrai
    (tunnel_ready) sur chaque candidat. Sinon on croit un device joignable, on tente
    l'install, elle hangue jusqu'au timeout, et le heartbeat ment à Nexus.

    Fait AU PLUS un restart de tunneld par run (si des cibles manquent) pour laisser
    le mDNS redécouvrir les appareils réveillés, puis re-sonde."""
    def probe(cands: set[str]) -> set[str]:
        return {u for u in cands if tunnel_ready(u)}

    have = probe(targets & set(_tunneld_udids()))
    if targets <= have:
        return have
    print("    tunnels incomplets/périmés → une reconstruction tunneld (mDNS)")
    restart_tunneld()
    deadline = time.time() + 45
    while time.time() < deadline:
        time.sleep(8)
        have = probe(targets & set(_tunneld_udids()))
        if targets <= have:
            break
    return have


def device_info(udid: str) -> tuple[str, str]:
    """(DeviceName, ProductType) de l'appareil via le tunnel. Caché par run.

    ProductType (ex. iPhone16,1) est mappé en nom commercial côté sideloop
    (status.py). Best-effort : ("", "") si échec → le dashboard retombe sur l'UDID.
    Un lockdown ponctuellement lent (device qui vient de recevoir un gros transfert)
    renvoyait "" → nom gelé à l'UDID : on retente une fois, et on ne CACHE qu'un
    résultat utile (sinon un "" transitoire condamnait le nom pour tout le run)."""
    if udid in _INFO_CACHE:
        return _INFO_CACHE[udid]
    name, ptype = "", ""
    for attempt in range(2):
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
        if name or ptype:
            break
        time.sleep(2)
    if name or ptype:
        _INFO_CACHE[udid] = (name, ptype)  # ne fige QUE les résultats exploitables
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
    """Installe l'IPA sur un device DÉJÀ confirmé joignable (voir reachable_udids).

    Garde une reprise courte pour absorber un drop de tunnel transitoire pendant le
    gros transfert (iOS 26/27, ~300 Mo Wi-Fi), mais ne reconstruit le tunnel que
    s'il est réellement tombé — au lieu de restarter tunneld à chaque essai.
    Renvoie (ok, dernière erreur)."""
    last_err = "non tenté"
    for attempt in range(1, MAX_ATTEMPTS + 1):
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
        # Reprise ciblée : ne reconstruire tunneld que si LE tunnel est tombé.
        if not tunnel_ready(udid):
            restart_tunneld()
            time.sleep(8)
        else:
            time.sleep(3)

    print(f"     {last_err}")
    return False, last_err


def load_state() -> dict:
    """installed-state.json : {"bundle_id:udid": {"at": iso-utc, "sig": str}} (installs OK)."""
    try:
        return json.loads(INSTALLED_STATE.read_text())
    except Exception:  # noqa: BLE001 — absent/corrompu = tout est à refaire
        return {}


def save_state(state: dict) -> None:
    tmp = INSTALLED_STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(INSTALLED_STATE)


def freshly_installed(state: dict, bundle_id: str, udid: str, sig: str) -> bool:
    """True si ce device a reçu CETTE signature avec succès il y a moins de FRESH_HOURS.

    ⚠ BUG HISTORIQUE : on ne comparait que le TEMPS, jamais le sig — alors qu'un
    refresh produit un NOUVEL IPA (sig différent). Résultat : après re-signature,
    l'agent voyait "installé il y a < 48 h" et skippait → la version fraîche n'était
    JAMAIS poussée (Nexus disait "signé", le device ne recevait rien). Un sig
    différent force donc la réinstallation, même à < 48 h : c'est tout l'objet
    d'un refresh. Le record stockait déjà `sig` (il n'était juste jamais relu)."""
    rec = state.get(f"{bundle_id}:{udid}")
    if not rec:
        return False
    if rec.get("sig") != sig:      # re-signé depuis → doit être réinstallé
        return False
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(rec["at"])
    except Exception:  # noqa: BLE001 — timestamp illisible = considérer périmé
        return False
    return age < timedelta(hours=FRESH_HOURS)


def previous_results() -> dict[str, dict]:
    """Derniers résultats de install-status.json indexés "bundle_id:udid".

    Les devices skippés (fraîchement servis) gardent ainsi leur dernier état
    dans le statut remonté à Nexus au lieu d'en disparaître."""
    try:
        return {f"{r['bundle_id']}:{r['udid']}": r
                for r in json.loads(INSTALL_STATUS.read_text())["results"]}
    except Exception:  # noqa: BLE001
        return {}


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
    write_heartbeat()  # toujours, avant tout (prouve que l'agent tourne)
    manifest = SIGNED_DIR / "manifest.json"
    if not manifest.exists():
        print("Aucun manifest.")
        return 0
    data = json.loads(manifest.read_text())
    # empreinte du manifest (traçabilité dans install-status.json)
    sig = hashlib.sha256(manifest.read_bytes()).hexdigest()[:16]

    state = load_state()
    results = previous_results()
    all_ok = True
    attempted = 0

    # Joignabilité pré-calculée UNE fois par run (union des devices à installer) :
    # évite de restarter tunneld par-device et d'attendre 90 s sur chaque endormi.
    targets = {u for e in data.get("entries", [])
               for u in e["device_udids"]
               if not freshly_installed(state, e["bundle_id"], u, sig)}
    reach = reachable_udids(targets) if targets else set()
    absent = targets - reach
    if absent:
        print(f"    {len(absent)} device(s) injoignable(s) (hors Wi-Fi/verrouillé) "
              f"— skip rapide, retry au prochain run")

    for e in data.get("entries", []):
        ipa = SIGNED_DIR / e["signed_ipa"]
        todo = [u for u in e["device_udids"]
                if not freshly_installed(state, e["bundle_id"], u, sig)]
        if len(todo) < len(e["device_udids"]):
            print(f"[{e['name']}] {len(e['device_udids']) - len(todo)} device(s) "
                  f"servis il y a < {FRESH_HOURS} h — skip")
        if not todo:
            continue
        if not ipa.exists():
            print(f"[{e['name']}] IPA absente: {ipa}")
            all_ok = False
            for udid in todo:
                name, ptype = device_info(udid)
                results[f"{e['bundle_id']}:{udid}"] = {
                    "bundle_id": e["bundle_id"], "udid": udid, "ok": False,
                    "at": datetime.now(timezone.utc).isoformat(),
                    "device_name": name, "product_type": ptype,
                    "error": "IPA signée absente"}
            continue
        print(f"[{e['name']}] {e['bundle_id']} → {len(todo)} device(s)")
        for udid in todo:
            if udid not in reach:
                # Injoignable : statut sans tenter (pas de thrash, pas de lockdown 30 s).
                # On réutilise le nom déjà connu plutôt que d'interroger l'appareil absent.
                prev = results.get(f"{e['bundle_id']}:{udid}", {})
                results[f"{e['bundle_id']}:{udid}"] = {
                    "bundle_id": e["bundle_id"], "udid": udid, "ok": False,
                    "at": datetime.now(timezone.utc).isoformat(),
                    "device_name": prev.get("device_name", ""),
                    "product_type": prev.get("product_type", ""),
                    "error": "injoignable (hors Wi-Fi / verrouillé)"}
                all_ok = False
                continue
            attempted += 1
            ok, err = install(ipa, udid)
            all_ok &= ok
            name, ptype = device_info(udid)
            # Fallback : ne pas écraser un nom déjà connu par un "" (lockdown lent
            # juste après le gros transfert) → le device garderait l'UDID à l'écran.
            prev = results.get(f"{e['bundle_id']}:{udid}", {})
            name = name or prev.get("device_name", "")
            ptype = ptype or prev.get("product_type", "")
            results[f"{e['bundle_id']}:{udid}"] = {
                "bundle_id": e["bundle_id"], "udid": udid, "ok": ok,
                "at": datetime.now(timezone.utc).isoformat(),
                "device_name": name, "product_type": ptype, "error": err}
            if ok:
                state[f"{e['bundle_id']}:{udid}"] = {
                    "at": datetime.now(timezone.utc).isoformat(), "sig": sig}
                save_state(state)  # au fil de l'eau : un run interrompu garde ses acquis

    # On remonte TOUJOURS l'état (succès comme échec) pour Nexus.
    write_status(sig, list(results.values()))

    if not targets:
        print("Rien à faire — tous les devices sont à jour.")
    elif not attempted:
        print("Aucun device joignable ce run — retry au prochain passage.")
    elif all_ok:
        print("Tous les devices tentés sont installés.")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
