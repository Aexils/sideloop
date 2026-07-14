"""Schémas partagés (apps gérées, manifeste d'install, état publié)."""

from datetime import datetime

from pydantic import BaseModel

# ── Cycle de vie de la signature (compte Apple gratuit) ───────────────────
# Le PROFIL de provisioning expire 7 jours après la signature ; le certificat
# lui-même dure ~1 an (réutilisé). C'est donc le profil qui cadence le refresh.
PROFILE_TTL_DAYS = 7
# En dessous de ce seuil (temps restant), une app passe "expiring" → alerte.
EXPIRING_THRESHOLD_HOURS = 48


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


# ── Historique des refresh (écrit par le CronJob, lu par /api/status) ──────
class RunAppResult(BaseModel):
    """Résultat de signature d'UNE app dans un run de refresh."""

    name: str
    bundle_id: str
    ok: bool
    error: str = ""


class RunRecord(BaseModel):
    """Une exécution du CronJob refresh (data/runs.json, on garde les N derniers)."""

    at: datetime
    login_ok: bool = True          # le login Apple (2FA-free) a-t-il tenu ?
    results: list[RunAppResult] = []

    @property
    def ok(self) -> bool:
        return self.login_ok and all(r.ok for r in self.results)


# ── Résultats d'install (écrits par l'AGENT PVE, ferme la boucle) ──────────
class InstallResult(BaseModel):
    """Install d'un bundle sur un device par l'agent pve (via tunnel Wi-Fi)."""

    bundle_id: str
    udid: str
    ok: bool
    at: datetime
    device_name: str = ""          # DeviceName iOS (ex. "iPhone"), remonté par l'agent
    error: str = ""


class InstallStatus(BaseModel):
    """signed/install-status.json — remonté du pve vers k8s (état d'install)."""

    manifest_sig: str = ""
    updated_at: datetime | None = None
    results: list[InstallResult] = []


# ── État agrégé publié par GET /api/status (consommé par Nexus) ────────────
class DeviceStatus(BaseModel):
    """Dernier état d'install connu pour un device (globalement ou par app)."""

    udid: str
    name: str = ""                   # nom lisible (DeviceName iOS), sinon UDID côté UI
    last_install_at: datetime | None = None
    last_ok: bool | None = None      # None = jamais tenté
    failures: int = 0                # échecs consécutifs (fenêtre install-status)


class AppStatus(BaseModel):
    name: str
    bundle_id: str
    original_bundle_id: str = ""
    last_signed: datetime | None = None
    expires_at: datetime | None = None      # last_signed + 7 j
    expires_in_sec: int | None = None        # négatif = déjà expiré
    status: str = "never"                    # ok | expiring | expired | never
    last_error: str = ""                     # dernière erreur de signature
    installs: list[DeviceStatus] = []        # état d'install par device


class AccountStatus(BaseModel):
    apple_id: str
    team_id: str
    app_slots_used: int
    app_slots_limit: int = 3                  # compte gratuit : 3 apps actives


class SideloopStatus(BaseModel):
    """Photographie complète pour le dashboard Nexus."""

    generated_at: datetime
    account: AccountStatus
    devices: list[DeviceStatus] = []
    apps: list[AppStatus] = []
    last_refresh_at: datetime | None = None
    last_refresh_ok: bool | None = None
    recent_runs: list[RunRecord] = []
    alerts: list[str] = []                    # messages prêts à afficher/notifier
