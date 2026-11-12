"""REST API and production client hosting."""

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.types import Scope

from .api.documents import router as document_router
from .ocr import check_ocr_languages
from .api.requirements import router as requirements_router
from .api.reports import router as reports_router
from .services import ServiceError


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_ocr_languages()
    yield


app = FastAPI(title="Cyber Accountant API", lifespan=lifespan)
api = APIRouter(prefix="/api/v1")


@app.exception_handler(ServiceError)
async def service_error_handler(request, error: ServiceError):
    return JSONResponse(status_code=error.status_code, content={"detail": error.detail})


@api.get("/health")
async def health():
    return {"status": "ok"}


api.include_router(reports_router)
api.include_router(document_router)
api.include_router(requirements_router)
app.include_router(api)


class ClientStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope):
        try:
            return await super().get_response(path, scope)
        except HTTPException as error:
            if error.status_code != 404 or path.split("/")[0] in {"api", "assets"} or Path(path).suffix:
                raise
            return await super().get_response("index.html", scope)


client_dir = Path(os.environ.get("CLIENT_DIST_DIR", Path(__file__).resolve().parents[2] / "client" / "dist"))

# Before the first client build, the API can run alongside Vite independently.
if client_dir.is_dir():
    app.mount("/", ClientStaticFiles(directory=client_dir, html=True), name="client")
