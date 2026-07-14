"""Schémas partagés (apps gérées, manifeste d'install)."""

from datetime import datetime

from pydantic import BaseModel


class ManagedApp(BaseModel):
    """Une app à re-signer périodiquement (entrée de state.json)."""

    name: str                       # ex. "SpotifySL"
    source_ipa: str                 # nom de fichier dans data/ipas/
    resign_bundle_id: str           # bundle UNIQUE enregistrable (ex. com.sideloop.spotify)
    original_bundle_id: str = ""    # info (ex. com.spotify.client)
    last_signed: datetime | None = None


class SignedEntry(BaseModel):
    """Une IPA signée prête à installer (lue par l'agent pve)."""

    name: str
    bundle_id: str
    signed_ipa: str                 # nom de fichier dans signed/
    signed_at: datetime
    device_udids: list[str]


class Manifest(BaseModel):
    """signed/manifest.json — l'agent pve installe ces IPAs puis marque done."""

    generated_at: datetime
    entries: list[SignedEntry] = []
