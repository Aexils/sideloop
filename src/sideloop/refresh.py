"""Entrypoint du CronJob : re-signe chaque app gérée, dépose les IPA + manifest.

L'INSTALL n'est pas ici (elle exige l'accès L2/mDNS au device) : c'est l'agent
pve qui lit `signed/manifest.json` et pousse les IPA par le tunnel Wi-Fi.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path

from . import signing, storage
from .config import settings
from .models import Manifest, SignedEntry


def run() -> int:
    storage.ensure_dirs()
    apps = storage.load_apps()
    if not apps:
        print("[refresh] aucune app gérée (state.json vide) — rien à faire.")
        return 0
    if not settings.devices:
        print("[refresh] ERREUR: aucun device (SIDELOOP_DEVICE_UDIDS vide).", file=sys.stderr)
        return 2

    print(f"[refresh] login Apple (anisette {settings.anisette_url})…")
    try:
        session = signing.login(settings.apple_id, settings.apple_password,
                                settings.anisette_url, settings.team_id)
    except Exception as e:  # noqa: BLE001
        print(f"[refresh] ÉCHEC login: {e}", file=sys.stderr)
        return 3
    print(f"[refresh] connecté (team {session.team_id}, {len(settings.devices)} device(s)).")

    now = datetime.now(timezone.utc)
    entries: list[SignedEntry] = []
    failed = 0
    for app in apps:
        src = settings.ipa_dir / app.source_ipa
        if not src.exists():
            print(f"[refresh] ÉCHEC {app.name}: IPA source absente ({src})", file=sys.stderr)
            failed += 1
            continue
        out = settings.signed_dir / f"{app.resign_bundle_id}.signed.ipa"
        try:
            signing.sign_ipa(session, src, out, app.resign_bundle_id, app.name, settings.devices,
                             zsign_bin=settings.zsign_bin)
            app.last_signed = now
            entries.append(SignedEntry(name=app.name, bundle_id=app.resign_bundle_id,
                                       signed_ipa=out.name, signed_at=now,
                                       device_udids=settings.devices))
            print(f"[refresh] SIGNÉ {app.name} → {out.name}")
        except Exception as e:  # noqa: BLE001 — on continue les autres apps
            print(f"[refresh] ÉCHEC signature {app.name}: {e}", file=sys.stderr)
            failed += 1

    storage.save_apps(apps)
    manifest = Manifest(generated_at=now, entries=entries)
    (settings.signed_dir / "manifest.json").write_text(manifest.model_dump_json(indent=2))
    print(f"[refresh] manifest écrit ({len(entries)} IPA prêtes à installer par l'agent pve).")
    return 10 if failed else 0


def main() -> None:
    sys.exit(run())


if __name__ == "__main__":
    main()
