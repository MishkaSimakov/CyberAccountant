"""REST API and production client hosting."""

from datetime import date
from pathlib import Path
from typing import Literal, Self

from fastapi import APIRouter, FastAPI
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator

app = FastAPI(title="Cyber Accountant API")
api = APIRouter(prefix="/api/v1")


class ReportCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    contract_number: str | None = Field(default=None, max_length=100)
    period_start: date | None = None
    period_end: date | None = None
    target_row: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def validate_period(self) -> Self:
        if (self.period_start is None) != (self.period_end is None):
            raise ValueError("Укажите начало и конец периода подтверждающих документов.")
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError("Конец периода не может быть раньше начала.")
        return self


class Report(ReportCreate):
    id: int
    stage: Literal["documents", "entities", "verification"] = "documents"
    processing_status: Literal["idle", "queued", "running", "completed", "failed"] = "idle"


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


app.include_router(api)

# The project layout is identical on the host and in Docker.
client_dir = Path(__file__).resolve().parents[2] / "client" / "dist"

# Before the first client build, the API can run alongside Vite independently.
if client_dir.is_dir():
    app.mount("/", StaticFiles(directory=client_dir, html=True), name="client")
