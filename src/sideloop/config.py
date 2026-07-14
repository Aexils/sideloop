"""Configuration via variables d'environnement (injectées par la chart Helm)."""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SIDELOOP_")

    # ── Compte Apple (dédié) ──────────────────────────────────────────────
    apple_id: str = ""                 # ex. "levasseur.alexis@uqam.ca"
    apple_password: str = ""           # depuis un Secret (jamais dans les values)
    team_id: str = ""                  # ex. "245M5C8BJT"

    # Serveur anisette v3 STABLE (machine trustée = 0 re-2FA). Service du namespace.
    anisette_url: str = "http://anisette:6969/"

    # ── Stockage ──────────────────────────────────────────────────────────
    # PVC : IPAs sources + state.json (apps gérées).
    data_dir: Path = Path("/data")
    # Sortie : IPAs signées + manifest.json, LUES par l'agent pve (via NFS).
    signed_dir: Path = Path("/signed")

    zsign_bin: str = "zsign"

    # UDID des appareils cibles (séparés par des virgules).
    device_udids: str = ""             # ex. "00008130-...,00008120-..."

    @property
    def ipa_dir(self) -> Path:
        return self.data_dir / "ipas"

    @property
    def state_file(self) -> Path:
        return self.data_dir / "state.json"

    @property
    def runs_file(self) -> Path:
        """Historique des refresh (écrit par le CronJob, lu par /api/status)."""
        return self.data_dir / "runs.json"

    @property
    def install_status_file(self) -> Path:
        """État d'install remonté par l'agent pve (via NFS = signed_dir)."""
        return self.signed_dir / "install-status.json"

    @property
    def devices(self) -> list[str]:
        return [u.strip() for u in self.device_udids.split(",") if u.strip()]


settings = Settings()
