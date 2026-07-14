"""Entrypoint du CronJob : re-signe chaque app gérée, dépose les IPA + manifest.

L'INSTALL n'est pas ici (elle exige l'accès L2/mDNS au device) : c'est l'agent
pve qui lit `signed/manifest.json` et pousse les IPA par le tunnel Wi-Fi.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

from . import signing, storage
from .config import settings
from .models import Manifest, RunAppResult, RunRecord, SignedEntry


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
    print(f"[refresh] login Apple (anisette {settings.anisette_url})…")
    try:
        session = signing.login(settings.apple_id, settings.apple_password,
                                settings.anisette_url, settings.team_id)
    except Exception as e:  # noqa: BLE001
        print(f"[refresh] ÉCHEC login: {e}", file=sys.stderr)
        # On historise l'échec de login (= alerte 2FA côté Nexus) avant de sortir.
        storage.append_run(RunRecord(at=now, login_ok=False, results=[]))
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
