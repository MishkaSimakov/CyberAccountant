"""Demonstration requirements while the verification pipeline is being built."""

import asyncio
import json

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .documents import blockers_for, documents, find_report, report_details, revisions
from .entities import ENTITY, attributes_for

router = APIRouter()


class Requirement(BaseModel):
    id: int
    verdict: bool
    equation: str | None = None
    evidence: str | None = None
    ids_related: list[int] = []
    notes: str = ""
    silenced: bool = False


class RequirementUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    notes: str = Field(default="", max_length=10000)
    silenced: bool = False


class RequirementsResult(BaseModel):
    requirements: list[Requirement]
    entity_documents: dict[int, int]


requirements: dict[int, list[Requirement]] = {}


def report_entities(report_id: int):
    return [
        (document_id, attributes_for(match))
        for document_id in documents.get(report_id, {})
        for match in ENTITY.finditer(revisions[document_id][-1].content)
        if attributes_for(match).get("id")
    ]


async def simulate_verification(report_id: int):
    report = find_report(report_id)
    try:
        await asyncio.sleep(1)
        report.processing_status = "running"
        await asyncio.sleep(5)
        # Demonstration verdicts, using IDs from the report's actual markup.
        fields = [attributes for _, attributes in report_entities(report_id)]
        numbers = [int(item["id"]) for item in fields if item["type"] == "number"]
        identifiers = [item for item in fields if item["type"] == "id"]
        dates = [int(item["id"]) for item in fields if item["type"] == "date"]
        items = []
        if numbers:
            a, b, c = (numbers[index % len(numbers)] for index in range(3))
            items.extend([
                Requirement(id=1, equation=f"#{a} * 2 == #{b}", verdict=True),
                Requirement(id=4, equation=f"#{a} + #{b} == #{c}", verdict=False),
                Requirement(id=5, evidence=f"Число #{a} требует дополнительной проверки.",
                            ids_related=[a, c], verdict=False),
            ])
        if identifiers:
            item = identifiers[0]
            items.append(Requirement(id=2, equation=f'#{item["id"]} == {json.dumps(item["value"], ensure_ascii=False)}',
                                     verdict=True))
        if dates:
            items.append(Requirement(id=3, equation=f"01.01.2026 < #{dates[0]} && #{dates[0]} < 31.05.2026",
                                     verdict=False))
        requirements[report_id] = sorted(items, key=lambda item: item.id)
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


@router.get("/reports/{report_id}/requirements", response_model=RequirementsResult)
async def report_requirements(report_id: int):
    report = find_report(report_id)
    if report.stage != "verification" or report.processing_status != "completed":
        raise HTTPException(409, "Проверка требований ещё не завершена.")
    return RequirementsResult(
        requirements=requirements.get(report_id, []),
        entity_documents={int(attributes["id"]): document_id
                          for document_id, attributes in report_entities(report_id)},
    )


@router.patch("/reports/{report_id}/requirements/{requirement_id}", response_model=Requirement)
async def update_requirement(report_id: int, requirement_id: int, payload: RequirementUpdate):
    items = (await report_requirements(report_id)).requirements
    requirement = next((item for item in items if item.id == requirement_id), None)
    if requirement is None:
        raise HTTPException(404, "Требование не найдено.")
    if payload.silenced and requirement.verdict:
        raise HTTPException(409, "У выполненного требования нет предупреждения.")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(requirement, field, value)
    return requirement
