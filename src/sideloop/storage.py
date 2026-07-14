"""Accès au PVC : IPAs sources, index des apps gérées (state.json),
historique des refresh (runs.json) et état d'install remonté par l'agent pve
(install-status.json)."""

import json

from .config import settings
from .models import InstallStatus, ManagedApp, RunRecord

# Nombre de runs de refresh conservés dans l'historique.
RUNS_KEEP = 30


def ensure_dirs() -> None:
    for d in (settings.data_dir, settings.ipa_dir, settings.signed_dir):
        d.mkdir(parents=True, exist_ok=True)


def load_apps() -> list[ManagedApp]:
    if not settings.state_file.exists():
        return []
    raw = json.loads(settings.state_file.read_text())
    return [ManagedApp.model_validate(a) for a in raw]


def save_apps(apps: list[ManagedApp]) -> None:
    ensure_dirs()
    tmp = settings.state_file.with_suffix(".tmp")
    tmp.write_text(json.dumps([a.model_dump(mode="json") for a in apps], indent=2))
    tmp.replace(settings.state_file)


# ── Historique des refresh (data/runs.json) ───────────────────────────────
def load_runs() -> list[RunRecord]:
    f = settings.runs_file
    if not f.exists():
        return []
    raw = json.loads(f.read_text())
    return [RunRecord.model_validate(r) for r in raw]


def append_run(rec: RunRecord) -> None:
    """Ajoute un run, tronque aux RUNS_KEEP plus récents (écriture atomique)."""
    ensure_dirs()
    runs = load_runs()
    runs.append(rec)
    runs = runs[-RUNS_KEEP:]
    tmp = settings.runs_file.with_suffix(".tmp")
    tmp.write_text(json.dumps([r.model_dump(mode="json") for r in runs], indent=2))
    tmp.replace(settings.runs_file)


# ── État d'install remonté par l'agent pve (signed/install-status.json) ────
def load_install_status() -> InstallStatus:
    f = settings.install_status_file
    if not f.exists():
        return InstallStatus()
    return InstallStatus.model_validate_json(f.read_text())
