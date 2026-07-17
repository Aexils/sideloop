"""Agrège l'état de sideloop pour GET /api/status (consommé par Nexus).

Croise trois sources :
  * state.json          — apps gérées + dernière signature (last_signed) ;
  * runs.json           — historique des refresh (CronJob) → dernière erreur ;
  * install-status.json — résultats d'install par device (remontés du pve).

Calcule le compte à rebours d'expiration (profil de provisioning = 7 jours
après la dernière signature réussie) et une liste d'alertes prêtes à notifier.
"""

from datetime import datetime, timedelta, timezone

from . import storage
from .config import settings
from .models import (
    EXPIRING_THRESHOLD_HOURS,
    PROFILE_TTL_DAYS,
    AccountStatus,
    AppStatus,
    DeviceStatus,
    InstallStatus,
    RunRecord,
    SideloopStatus,
)


# ProductType Apple → nom commercial (pour un libellé lisible dans Nexus).
# Étendre au besoin quand d'autres appareils sont ajoutés.
PRODUCT_NAMES = {
    # ── iPhone ──
    "iPhone17,3": "iPhone 16", "iPhone17,4": "iPhone 16 Plus",
    "iPhone17,1": "iPhone 16 Pro", "iPhone17,2": "iPhone 16 Pro Max",
    "iPhone15,4": "iPhone 15", "iPhone15,5": "iPhone 15 Plus",
    "iPhone16,1": "iPhone 15 Pro", "iPhone16,2": "iPhone 15 Pro Max",
    "iPhone14,7": "iPhone 14", "iPhone14,8": "iPhone 14 Plus",
    "iPhone15,2": "iPhone 14 Pro", "iPhone15,3": "iPhone 14 Pro Max",
    "iPhone14,5": "iPhone 13", "iPhone14,4": "iPhone 13 mini",
    "iPhone14,2": "iPhone 13 Pro", "iPhone14,3": "iPhone 13 Pro Max",
    "iPhone13,1": "iPhone 12 mini", "iPhone13,2": "iPhone 12",
    "iPhone13,3": "iPhone 12 Pro", "iPhone13,4": "iPhone 12 Pro Max",
    # ── iPad Air (toutes générations : gén inconnue côté Alexis) ──
    "iPad4,1": "iPad Air", "iPad4,2": "iPad Air", "iPad4,3": "iPad Air",
    "iPad5,3": "iPad Air 2", "iPad5,4": "iPad Air 2",
    "iPad11,3": "iPad Air (3e gén)", "iPad11,4": "iPad Air (3e gén)",
    "iPad13,1": "iPad Air (4e gén)", "iPad13,2": "iPad Air (4e gén)",
    "iPad13,16": "iPad Air (5e gén)", "iPad13,17": "iPad Air (5e gén)",
    "iPad14,8": "iPad Air 11″ (M2)", "iPad14,9": "iPad Air 11″ (M2)",
    "iPad14,10": "iPad Air 13″ (M2)", "iPad14,11": "iPad Air 13″ (M2)",
    "iPad15,3": "iPad Air 11″ (M3)", "iPad15,4": "iPad Air 11″ (M3)",
    "iPad15,5": "iPad Air 13″ (M3)", "iPad15,6": "iPad Air 13″ (M3)",
}


def _display_name(product_type: str, device_name: str) -> str:
    """Nom commercial (« iPhone 15 Pro ») si connu, sinon DeviceName, sinon ""."""
    return PRODUCT_NAMES.get(product_type) or device_name or ""


def _app_status_label(expires_in_sec: int | None, last_signed: datetime | None) -> str:
    if last_signed is None or expires_in_sec is None:
        return "never"
    if expires_in_sec <= 0:
        return "expired"
    if expires_in_sec <= EXPIRING_THRESHOLD_HOURS * 3600:
        return "expiring"
    return "ok"


def _last_error_for(bundle_id: str, runs: list[RunRecord]) -> str:
    """Dernière erreur de signature connue pour ce bundle (run le plus récent)."""
    for run in reversed(runs):
        for r in run.results:
            if r.bundle_id == bundle_id and not r.ok and r.error:
                return r.error
    return ""


def _device_installs(bundle_id: str, inst: InstallStatus) -> list[DeviceStatus]:
    """État d'install par device POUR CE BUNDLE (depuis install-status.json)."""
    out: dict[str, DeviceStatus] = {}
    for r in inst.results:
        if r.bundle_id != bundle_id:
            continue
        d = out.setdefault(r.udid, DeviceStatus(udid=r.udid))
        d.last_install_at = r.at
        d.last_ok = r.ok
        name = _display_name(r.product_type, r.device_name)
        if name:
            d.name = name
        d.failures = 0 if r.ok else d.failures + 1
    return list(out.values())


def _global_devices(inst: InstallStatus) -> list[DeviceStatus]:
    """Dernier état d'install par device, toutes apps confondues."""
    out: dict[str, DeviceStatus] = {}
    for udid in settings.devices:
        out[udid] = DeviceStatus(udid=udid)
    for r in inst.results:
        d = out.setdefault(r.udid, DeviceStatus(udid=r.udid))
        # on garde l'install la plus récente comme "dernier état"
        if d.last_install_at is None or r.at >= d.last_install_at:
            d.last_install_at = r.at
            d.last_ok = r.ok
        name = _display_name(r.product_type, r.device_name)
        if name:
            d.name = name
        if not r.ok:
            d.failures += 1
    return list(out.values())


def build_status() -> SideloopStatus:
    now = datetime.now(timezone.utc)
    apps = storage.load_apps()
    runs = storage.load_runs()
    inst = storage.load_install_status()
    agent = storage.load_heartbeat()

    app_statuses: list[AppStatus] = []
    alerts: list[str] = []

    for app in apps:
        expires_at = None
        expires_in = None
        if app.last_signed is not None:
            ls = app.last_signed
            if ls.tzinfo is None:
                ls = ls.replace(tzinfo=timezone.utc)
            expires_at = ls + timedelta(days=PROFILE_TTL_DAYS)
            expires_in = int((expires_at - now).total_seconds())

        label = _app_status_label(expires_in, app.last_signed)
        last_err = _last_error_for(app.resign_bundle_id, runs)
        installs = _device_installs(app.resign_bundle_id, inst)

        app_statuses.append(AppStatus(
            name=app.name,
            bundle_id=app.resign_bundle_id,
            original_bundle_id=app.original_bundle_id,
            last_signed=app.last_signed,
            expires_at=expires_at,
            expires_in_sec=expires_in,
            status=label,
            last_error=last_err,
            installs=installs,
        ))

        # Alertes prêtes à notifier
        if label == "expired":
            alerts.append(f"{app.name} : signature EXPIRÉE (à réinstaller).")
        elif label == "expiring":
            h = max(0, (expires_in or 0) // 3600)
            alerts.append(f"{app.name} : expire dans {h} h.")
        elif label == "never":
            alerts.append(f"{app.name} : jamais signée.")
        if last_err:
            alerts.append(f"{app.name} : dernière signature en échec ({last_err[:80]}).")

    # Alerte install : seulement si CRITIQUE. Un échec d'install sur une app fraîchement
    # signée = appareil juste endormi/verrouillé, repris au prochain run → PAS une alarme
    # (le statut par-device est déjà visible dans les cartes d'app). Ça ne devient une
    # alerte que si le certificat expire bientôt (ou a expiré) ET qu'un appareil n'a
    # toujours pas reçu l'install : là, le temps presse vraiment pour agir.
    for a in app_statuses:
        if a.status not in ("expiring", "expired"):
            continue
        pending = [d.name or f"{d.udid[:12]}…" for d in a.installs if d.last_ok is not True]
        if pending:
            etat = "cert EXPIRÉ" if a.status == "expired" else "cert bientôt expiré"
            alerts.append(f"{a.name} : {etat}, pas encore installée sur "
                          f"{', '.join(pending)}")

    # Alerte login (2FA cassé) : dernier run avec login KO
    last_run = runs[-1] if runs else None
    if last_run is not None and not last_run.login_ok:
        alerts.append("Login Apple en échec (2FA à refaire ? cf. bootstrap anisette).")

    # Santé de l'agent pve : heartbeat périmé (> 40 min ; le timer tourne aux 30 min)
    # = agent mort / pve down → plus aucune install possible.
    AGENT_STALE_SEC = 40 * 60
    if agent.last_seen is None:
        agent.stale = True
        alerts.append("Agent pve : aucun signe de vie (heartbeat absent).")
    else:
        ls = agent.last_seen
        if ls.tzinfo is None:
            ls = ls.replace(tzinfo=timezone.utc)
        age = int((now - ls).total_seconds())
        agent.stale = age > AGENT_STALE_SEC
        if agent.stale:
            alerts.append(f"Agent pve muet depuis {age // 60} min (pve down ? timer arrêté ?).")
        elif not agent.tunneld_active:
            alerts.append("tunneld arrêté sur pve : les installs Wi-Fi échoueront.")

    account = AccountStatus(
        apple_id=settings.apple_id,
        team_id=settings.team_id,
        app_slots_used=len(apps),
    )

    return SideloopStatus(
        generated_at=now,
        account=account,
        agent=agent,
        devices=_global_devices(inst),
        apps=app_statuses,
        last_refresh_at=last_run.at if last_run else None,
        last_refresh_ok=last_run.ok if last_run else None,
        recent_runs=runs[-10:],
        alerts=alerts,
    )
