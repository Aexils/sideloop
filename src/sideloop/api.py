"""API + frontend : gérer les apps à re-signer (upload IPA, liste).

Le refresh (signature) et l'install ne sont PAS ici : le refresh est un CronJob,
l'install un agent pve. Cette API sert juste à alimenter le state.json.
"""

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from . import status as status_mod
from . import storage
from .models import ManagedApp, SideloopStatus

WEB_DIR = Path(__file__).parent / "web"
app = FastAPI(title="sideloop", version="0.1.0")


@app.on_event("startup")
def _startup() -> None:
    storage.ensure_dirs()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.get("/api/status")
def status() -> SideloopStatus:
    """État agrégé (apps + expiration + devices + refresh + alertes).

    Consommé par Nexus (poll toutes les ~30 s) pour le dashboard et les alertes.
    """
    return status_mod.build_status()


@app.get("/api/apps")
def list_apps() -> list[ManagedApp]:
    return storage.load_apps()


@app.post("/api/apps")
async def add_app(
    file: UploadFile = File(...),
    name: str = Form(...),
    resign_bundle_id: str = Form(...),
    original_bundle_id: str = Form(""),
) -> ManagedApp:
    if not file.filename or not file.filename.endswith(".ipa"):
        raise HTTPException(400, "Un fichier .ipa est attendu.")
    from .config import settings
    storage.ensure_dirs()
    fname = Path(file.filename).name
    (settings.ipa_dir / fname).write_bytes(await file.read())
    apps = [a for a in storage.load_apps() if a.resign_bundle_id != resign_bundle_id]
    entry = ManagedApp(name=name, source_ipa=fname, resign_bundle_id=resign_bundle_id,
                       original_bundle_id=original_bundle_id)
    apps.append(entry)
    storage.save_apps(apps)
    return entry


@app.delete("/api/apps/{resign_bundle_id}")
def delete_app(resign_bundle_id: str) -> dict:
    apps = [a for a in storage.load_apps() if a.resign_bundle_id != resign_bundle_id]
    storage.save_apps(apps)
    return {"deleted": resign_bundle_id}


def main() -> None:
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
