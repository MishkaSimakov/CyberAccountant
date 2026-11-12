"""Demonstration requirements while the verification pipeline is being built."""

import asyncio

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

from .documents import blockers_for, documents, find_report, report_details

router = APIRouter()


class Requirement(BaseModel):
    id: int
    verdict: bool
    equation: str | None = None
    evidence: str | None = None
    ids_related: list[int] = []


requirements: dict[int, list[Requirement]] = {}


async def simulate_verification(report_id: int):
    report = find_report(report_id)
    try:
        await asyncio.sleep(1)
        report.processing_status = "running"
        await asyncio.sleep(5)
        # Fixtures from docs/DESIGN.md, not conclusions about uploaded documents.
        requirements[report_id] = [
            Requirement(id=1, equation="#123 * 2 == #321", verdict=True),
            Requirement(id=2, equation='#999 == "ВЕД-ЗП-2026-02"', verdict=True),
            Requirement(id=3, equation="01.01.2026 < #777 && #777 < 31.05.2026", verdict=False),
            Requirement(id=4, equation="#1 + #2 == #3", verdict=False),
            Requirement(
                id=5,
                evidence="Число #541 выглядит очень странно: зарплата больше чем 1e9.",
                ids_related=[541, 9654],
                verdict=False,
            ),
        ]
        report.processing_status = "completed"
    except Exception:
        report.processing_status = "failed"


@router.post("/reports/{report_id}/run-verification")
async def run_verification(report_id: int, background_tasks: BackgroundTasks):
    report = find_report(report_id)
    if report.stage == "verification" and report.processing_status in {"queued", "running", "completed"}:
        return await report_details(report_id)
    if report.stage not in {"entities", "verification"}:
        raise HTTPException(409, "Сначала завершите разметку сущностей.")
    items = list(documents.get(report_id, {}).values())
    blockers = blockers_for(items)
    if blockers or any(item.extraction_status != "completed" for item in items):
        raise HTTPException(409, " ".join(blockers) or "Дождитесь разметки всех документов.")
    requirements.pop(report_id, None)
    report.stage = "verification"
    report.processing_status = "queued"
    background_tasks.add_task(simulate_verification, report_id)
    return await report_details(report_id)


@router.get("/reports/{report_id}/requirements", response_model=list[Requirement])
async def report_requirements(report_id: int):
    report = find_report(report_id)
    if report.stage != "verification" or report.processing_status != "completed":
        raise HTTPException(409, "Проверка требований ещё не завершена.")
    return requirements.get(report_id, [])
