r"""Import reviewed Markdown and requirements, verify, and print the report URL.

With the development stack running, put the bundle inside the checkout and run:
    docker compose -f compose.dev.yaml exec server python scripts/import_verification.py \
        --entities /app/bundle/entries --final-report /app/bundle/report.md \
        --target-row 'Итого'

The entities directory contains plain Markdown files and is searched recursively.
The final report can be inside or outside that directory. Requirements default
to requirements.json beside the entities directory.

Locally, use server/.venv/bin/python and set DATABASE_URL and STORAGE_ROOT to the
same database and artifact directory as the API. No Redis or LLM calls are needed.
"""

import argparse
import json
import re
import sys
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from app import storage
from app.database import session_scope
from app.entities import prepare_entities
from app.jobs.verification import process_verification
from app.models import EntityFile, File, Report, Requirement
from app.schemas.reports import ReportCreate
from app.schemas.requirements import requirement_adapter
from app.services.requirements import verification_result_file
from app.target_row import find_target_row_keys
from app.verification import validate_formula
from checkier.entities import ATTR_RE, ENT_RE, build_registry, parse_tag_attrs
from checkier.formula import tokenize

REFERENCE_RE = re.compile(r"#\d+|[a-z]+-[a-z]+")


@dataclass
class Document:
    name: str
    original: str
    text: str
    role: str
    ids: list[int | str]


def entity_key(value: int | str) -> int | str:
    if isinstance(value, int):
        if value >= 0:
            return value
    elif re.fullmatch(r"#?\d+", value):
        return int(value.removeprefix("#"))
    elif re.fullmatch(r"[a-z]+-[a-z]+", value):
        return value
    raise ValueError(f"Invalid entity ID: {value!r}")


def load_requirements(path: Path) -> list[dict]:
    raw = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(raw, dict):
        raw = raw.get("requirements")
    if not isinstance(raw, list):
        raise ValueError("Requirements JSON must be an array or an object with a requirements array.")
    result = []
    for index, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise ValueError(f"Requirement {index} must be an object.")
        result.append(requirement_adapter.validate_python({"id": index, **item}).model_dump())
    return result


def load_documents(directory: Path, final_report: Path) -> list[Document]:
    directory = directory.resolve()
    if not directory.is_dir():
        raise ValueError(f"Entities directory does not exist: {directory}")
    final_report = final_report.resolve()
    if not final_report.is_file() or final_report.suffix.lower() != ".md":
        raise ValueError(f"Final report must be an existing Markdown file: {final_report}")
    paths = {path.resolve() for path in directory.rglob("*") if path.is_file() and path.suffix.lower() == ".md"}
    paths.add(final_report)
    documents = []
    names = set()
    for path in sorted(paths):
        name = path.relative_to(directory).as_posix() if path.is_relative_to(directory) else path.name
        if name in names:
            raise ValueError(f"Duplicate document name: {name}")
        names.add(name)
        original = path.read_text(encoding="utf-8-sig")
        ids = []

        def strip_id(match):
            attributes = match.group(1)
            parsed = parse_tag_attrs(attributes)
            if ATTR_RE.sub("", attributes).strip() or len(ATTR_RE.findall(attributes)) != len(parsed):
                raise ValueError(f"{path}: malformed or duplicate entity attributes.")
            old_id = parsed.get("id")
            if old_id is None:
                raise ValueError(f"{path}: entity without an id.")
            ids.append(entity_key(old_id))
            attributes = re.sub(r'\bid\s*=\s*"[^"]*"', "", attributes)
            return f"<ent {attributes}>{match.group(2)}</ent>"

        text = prepare_entities(ENT_RE.sub(strip_id, original))
        documents.append(Document(name, original, text, "card" if path == final_report else "data", ids))
    if not any(document.ids for document in documents if document.role == "card"):
        raise ValueError("The final report must contain <ent> tags; plain Markdown needs entity extraction first.")
    return documents


def remap_requirements(requirements: list[dict], documents: list[Document], file_keys: dict[str, str]):
    registry = build_registry({file_keys[document.name]: (document.role, document.text) for document in documents})
    aliases = {}
    for document in documents:
        entities = [entity for entity in registry.entities if entity.file == file_keys[document.name]]
        for entity, old_id in zip(entities, document.ids, strict=True):
            if old_id in aliases:
                raise ValueError(f"Duplicate entity ID: {old_id!r}. IDs must be unique across the bundle.")
            aliases[old_id] = entity.num

    def lookup(key):
        key = entity_key(key)
        if key not in aliases:
            raise ValueError(f"Unknown entity ID in requirements: {key!r}")
        return aliases[key]

    result = []
    for requirement in requirements:
        data = dict(requirement)
        if data["kind"] == "formula":
            equation = data["equation"]
            # Token positions avoid replacing references inside string literals.
            for token in reversed(tokenize(equation)):
                if token.kind == "id":
                    end = REFERENCE_RE.match(equation, token.pos).end()
                    equation = equation[:token.pos] + f"#{lookup(token.value)}" + equation[end:]
            error = validate_formula(equation, registry)
            if error:
                raise ValueError(f"Requirement {data['id']}: {error}")
            data["equation"] = equation
        else:
            data["ids_related"] = [lookup(key) for key in data["ids_related"]]
        result.append(data)
    return result, registry


def import_bundle(requirements_path: Path, entities: Path, final_report: Path, *,
                  name: str = "Imported verification", target_row: str | None = None) -> tuple[int, Path]:
    metadata = ReportCreate(name=name, target_row=target_row)
    requirements = load_requirements(requirements_path)
    documents = load_documents(entities, final_report)
    source_keys = {document.name: document.name for document in documents}
    _, registry = remap_requirements(requirements, documents, source_keys)
    if metadata.target_row and not find_target_row_keys(
            {document.name: (document.role, document.text) for document in documents}, registry, metadata.target_row):
        raise ValueError(f"Target row not found in final report: {metadata.target_row!r}")

    with session_scope() as session:
        report = Report(**metadata.model_dump(), stage="verification", processing_status="queued")
        session.add(report)
        session.flush()
        files = []
        for document in documents:
            content = document.original.encode("utf-8")
            file = File(report_id=report.id, name=document.name, path="", hash=sha256(content).hexdigest(),
                        role="final_report" if document.role == "card" else "supporting", size=len(content),
                        content_type="text/markdown", status="completed", version=1)
            session.add(file)
            session.flush()
            file.path = f"{report.id}/{file.file_id}/original.md"
            original = storage.artifact_path(file.path)
            original.parent.mkdir(parents=True, exist_ok=True)
            original.write_bytes(content)
            file.entities_path = storage.save_entities(report.id, file.file_id, 1, document.text)
            file.markdown_path = file.entities_path
            files.append(file)
        definitions, registry = remap_requirements(requirements, documents,
                                                   {file.name: str(file.file_id) for file in files})
        for requirement_id, definition in enumerate(definitions, start=1):
            data = dict(definition)
            data.pop("id")
            session.add(Requirement(report_id=report.id, requirement_id=requirement_id,
                                    text=data.pop("text"), source=data.pop("source"),
                                    note=data.pop("notes"), is_silenced=data.pop("silenced"), json_data=data))
        session.add_all(EntityFile(report_id=report.id, entity_id=entity.num, file_id=int(entity.file))
                        for entity in registry.entities)
        # Keep the inputs alongside the preserved originals, outside regenerable verification output.
        storage.artifact_path(f"{report.id}/imported-requirements.json").write_bytes(requirements_path.read_bytes())

    print(f"Imported report {report.id}; running verification…", flush=True)
    process_verification(report.id)
    return report.id, verification_result_file(report.id)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--requirements", type=Path, help="JSON array or {requirements: [...]}; "
                                                          "defaults to requirements.json beside --entities.")
    parser.add_argument("--entities", required=True, type=Path,
                        help="Directory searched recursively for Markdown files.")
    parser.add_argument("--final-report", required=True, type=Path, help="Path to the final report's Markdown file.")
    parser.add_argument("--target-row", help="Exact row label; omit to verify all final report fields.")
    parser.add_argument("--name", default="Imported verification")
    parser.add_argument("--base-url", default="http://localhost:5173",
                        help="Frontend URL (production: http://localhost:8000).")
    args = parser.parse_args(argv)
    requirements_path = (args.requirements if args.requirements is not None
                         else args.entities.resolve().parent / "requirements.json")
    report_id, result_path = import_bundle(requirements_path, args.entities, args.final_report,
                                           name=args.name, target_row=args.target_row)
    result = json.loads(result_path.read_text(encoding="utf-8"))
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(f"\nReport: {args.base_url.rstrip('/')}/reports/{report_id}/requirements")
    print(f"JSON: {result_path}")


if __name__ == "__main__":
    main()
