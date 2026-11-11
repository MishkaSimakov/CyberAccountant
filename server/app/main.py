"""REST API and production client hosting."""

from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Cyber Accountant API")
api = APIRouter(prefix="/api")


@api.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


app.include_router(api)

# The project layout is identical on the host and in Docker.
client_dir = Path(__file__).resolve().parents[2] / "client" / "dist"

# Before the first client build, the API can run alongside Vite independently.
if client_dir.is_dir():
    app.mount("/", StaticFiles(directory=client_dir, html=True), name="client")
