"""REST API and production client hosting."""

from datetime import date
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.types import Scope

from .documents import router as document_router
from .ocr import check_ocr_languages
from .requirements import router as requirements_router
from .schemas.reports import Report, ReportCreate


@asynccontextmanager
async def lifespan(app: FastAPI):
    check_ocr_languages()
    yield


app = FastAPI(title="Cyber Accountant API", lifespan=lifespan)
api = APIRouter(prefix="/api/v1")

# Temporary in-memory storage until the processing pipeline and database are added.
report_store = [
    Report(
        id=1,
        name="01_Карточка_учета_фактических_затрат_этап_3",
        contract_number="24/03/15-01",
        period_start=date(2026, 1, 1),
        period_end=date(2026, 5, 31),
        target_row="3 этап, 2026 г.",
    ),
]


@api.get("/health")
async def health():
    return {"status": "ok"}


@api.get("/reports", response_model=list[Report])
async def reports():
    return report_store


@api.post("/reports", response_model=Report, status_code=201)
async def create_report(payload: ReportCreate):
    report = Report(id=max((item.id for item in report_store), default=0) + 1, **payload.model_dump())
    report_store.insert(0, report)
    return report


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


# The project layout is identical on the host and in Docker.
client_dir = Path(__file__).resolve().parents[2] / "client" / "dist"

# Before the first client build, the API can run alongside Vite independently.
if client_dir.is_dir():
    app.mount("/", ClientStaticFiles(directory=client_dir, html=True), name="client")
