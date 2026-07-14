"""Accès au PVC : IPAs sources + index des apps gérées (state.json)."""

import json

from .config import settings
from .models import ManagedApp


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
