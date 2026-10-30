"""Entrypoint du CronJob : re-signe chaque app gérée, dépose les IPA + manifest.

L'INSTALL n'est pas ici (elle exige l'accès L2/mDNS au device) : c'est l'agent
pve qui lit `signed/manifest.json` et pousse les IPA par le tunnel Wi-Fi.
"""

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import signing, storage
from .config import settings
from .models import Manifest, RunAppResult, RunRecord, SignedEntry


def _aware(dt: datetime) -> datetime:
    """Les dates de state.json/runs.json peuvent être naïves : ce sont des UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def run() -> int:
    storage.ensure_dirs()
    apps = storage.load_apps()
    if not apps:
        print("[refresh] aucune app gérée (state.json vide) — rien à faire.")
        return 0
    if not settings.devices:
        print("[refresh] ERREUR: aucun device (SIDELOOP_DEVICE_UDIDS vide).", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc)

    # Les runs « rien à faire » ne s'historisent PAS : runs.json ne garde que
    # ce qu'Apple a répondu, et le compte à rebours d'expiration alerte tout seul
    # si le Mac reste injoignable trop longtemps.
    if settings.resign_after_hours:
        cutoff = now - timedelta(hours=settings.resign_after_hours)
        if all(a.last_signed and _aware(a.last_signed) > cutoff for a in apps):
            print(f"[refresh] signatures de moins de {settings.resign_after_hours} h "
                  "— rien à faire.")
            return 0
    if settings.login_retry_hours:
        runs = storage.load_runs()
        last = runs[-1] if runs else None
        if (last is not None and not last.login_ok
                and _aware(last.at) > now - timedelta(hours=settings.login_retry_hours)):
            print(f"[refresh] login refusé à {last.at:%H:%M} UTC — prochain essai "
                  f"{settings.login_retry_hours} h après, pas avant.")
            return 0
    if settings.anisette_native and not signing.anisette_reachable(settings.anisette_url):
        print(f"[refresh] anisette du Mac injoignable ({settings.anisette_url}) — "
              "Mac éteint ou en veille, on repassera.")
        return 0

    # Sonde l'anisette AVANT de parler à Apple : une provision ADI périmée fait
    # répondre 503 à gsa.apple.com, ce qui ressort en "Invalid file" incompréhensible.
    # Mieux vaut nommer la panne que la déduire (cf. 2026-09-10).
    stale = signing.probe_anisette(settings.anisette_url)
    if stale:
        print(f"[refresh] ÉCHEC anisette: {stale}", file=sys.stderr)
        storage.append_run(RunRecord(at=now, login_ok=False, login_error=stale, results=[]))
        return 3

    print(f"[refresh] login Apple (anisette {settings.anisette_url})…")
    try:
        session = signing.login(settings.apple_id, settings.apple_password,
                                settings.anisette_url, settings.team_id,
                                native=settings.anisette_native)
    except Exception as e:  # noqa: BLE001
        print(f"[refresh] ÉCHEC login: {e}", file=sys.stderr)
        # On historise l'échec AVEC sa cause (affichée telle quelle par Nexus).
        storage.append_run(RunRecord(at=now, login_ok=False, login_error=str(e), results=[]))
        return 3
    print(f"[refresh] connecté (team {session.team_id}, {len(settings.devices)} device(s)).")

    entries: list[SignedEntry] = []
    results: list[RunAppResult] = []
    failed = 0
    for app in apps:
        src = settings.ipa_dir / app.source_ipa
        if not src.exists():
            msg = f"IPA source absente ({src})"
            print(f"[refresh] ÉCHEC {app.name}: {msg}", file=sys.stderr)
            results.append(RunAppResult(name=app.name, bundle_id=app.resign_bundle_id,
                                        ok=False, error=msg))
            failed += 1
            continue
        out = settings.signed_dir / f"{app.resign_bundle_id}.signed.ipa"
        try:
            signing.sign_ipa(session, src, out, app.resign_bundle_id, app.name, settings.devices,
                             cert_dir=settings.data_dir / "cert", zsign_bin=settings.zsign_bin)
            app.last_signed = now
            entries.append(SignedEntry(name=app.name, bundle_id=app.resign_bundle_id,
                                       signed_ipa=out.name, signed_at=now,
                                       device_udids=settings.devices))
            results.append(RunAppResult(name=app.name, bundle_id=app.resign_bundle_id, ok=True))
            print(f"[refresh] SIGNÉ {app.name} → {out.name}")
        except Exception as e:  # noqa: BLE001 — on continue les autres apps
            print(f"[refresh] ÉCHEC signature {app.name}: {e}", file=sys.stderr)
            results.append(RunAppResult(name=app.name, bundle_id=app.resign_bundle_id,
                                        ok=False, error=str(e)))
            failed += 1

    storage.save_apps(apps)
    storage.append_run(RunRecord(at=now, login_ok=True, results=results))
    manifest = Manifest(generated_at=now, entries=entries)
    (settings.signed_dir / "manifest.json").write_text(manifest.model_dump_json(indent=2))
    print(f"[refresh] manifest écrit ({len(entries)} IPA prêtes à installer par l'agent pve).")
    return 10 if failed else 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
