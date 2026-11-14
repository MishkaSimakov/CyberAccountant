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
from html import escape
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

from app import storage
from app.database import session_scope
from app.entity_ids import reserve_entity_ids
from app.jobs.verification import process_verification
from app.models import EntityFile, File, Report, ReportEntityCounter, Requirement
from app.schemas.reports import ReportCreate
from app.schemas.requirements import requirement_adapter
from app.services.requirements import verification_result_file
from app.target_row import find_target_row_spans
from app.verification import validate_formula
from processing.entities.corpus import build_registry
from processing.common.formula import BinOp, collect_ids, parse, tokenize
from processing.entities.identifiers import MNEMONIC_CAPACITY, id_for_mnemonic, mnemonic_for_id, read_identities
from processing.entities.markup import parse_entity_tags, validate_attributes, validate_markup

REFERENCE_RE = re.compile(r"#\d+|[a-z]+-[a-z]+")


@dataclass
class Document:
    name: str
    original: str
    text: str
    role: str
    ids: list[int | str | None]


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
        pieces, end = [], 0
        for tag in parse_entity_tags(original):
            attrs = tag.attributes.copy()
            validate_attributes(attrs)
            old_id = entity_key(attrs["id"]) if "id" in attrs else None
            mnemonic = attrs.get("mnemonic")
            if isinstance(old_id, str):
                if mnemonic is not None and mnemonic != old_id:
                    raise ValueError("Conflicting supplied mnemonic identity.")
                mnemonic = old_id
                attrs.pop("id")
            if isinstance(old_id, int):
                if old_id >= MNEMONIC_CAPACITY:
                    raise ValueError("Entity ID exceeds mnemonic capacity.")
                attrs["id"] = str(old_id)
                mnemonic = mnemonic if mnemonic is not None else mnemonic_for_id(old_id)
            if mnemonic is not None:
                id_for_mnemonic(mnemonic)
                attrs["mnemonic"] = mnemonic
            ids.append(old_id if old_id is not None else mnemonic)
            fields = " ".join(f'{key}="{escape(value, quote=True)}"' for key, value in attrs.items())
            pieces.extend((original[end:tag.span[0]], f"<ent {fields}>{original[slice(*tag.body_span)]}</ent>"))
            end = tag.span[1]
        pieces.append(original[end:])
        text = "".join(pieces)
        documents.append(Document(name, original, text, "card" if path == final_report else "data", ids))
    if not any(document.ids for document in documents if document.role == "card"):
        raise ValueError("The final report must contain <ent> tags; plain Markdown needs entity extraction first.")
    supplied_ids, supplied_mnemonics = set(), set()
    for document in documents:
        for tag in parse_entity_tags(document.text):
            attrs = tag.attributes
            if "id" in attrs:
                num = int(attrs["id"])
                if num in supplied_ids:
                    raise ValueError(f"Duplicate entity ID: {num}.")
                supplied_ids.add(num)
            if "mnemonic" in attrs:
                mnemonic = attrs["mnemonic"]
                if mnemonic in supplied_mnemonics:
                    raise ValueError(f"Duplicate entity mnemonic: {mnemonic}.")
                supplied_mnemonics.add(mnemonic)
    return documents


def canonicalize_documents(session, report_id: int, documents: list[Document]) -> None:
    """Retain supplied identities and allocate missing numbers under the report lock."""
    tags = [tag for document in documents for tag in parse_entity_tags(document.text)]
    retained = [int(tag.attributes["id"]) for tag in tags if "id" in tag.attributes]
    # Imported pairs may predate the bijective mapping. Keep future generated
    # mnemonics clear of their supplied words as well as their numeric IDs.
    retained.extend(id_for_mnemonic(tag.attributes["mnemonic"]) for tag in tags if "mnemonic" in tag.attributes)
    next_id = max(retained, default=-1) + 1
    session.add(ReportEntityCounter(report_id=report_id, next_entity_id=next_id))
    session.flush()
    missing = sum("id" not in tag.attributes for tag in tags)
    reserved = iter(reserve_entity_ids(session, report_id, missing))
    for document in documents:
        pieces, end = [], 0
        for tag in parse_entity_tags(document.text):
            attrs = tag.attributes.copy()
            num = int(attrs["id"]) if "id" in attrs else next(reserved)
            attrs["id"] = str(num)
            if "mnemonic" not in attrs:
                attrs["mnemonic"] = mnemonic_for_id(num)
            fields = " ".join(f'{key}="{escape(value, quote=True)}"' for key, value in attrs.items())
            pieces.extend((document.text[end:tag.span[0]], f"<ent {fields}>{document.text[slice(*tag.body_span)]}</ent>"))
            end = tag.span[1]
        pieces.append(document.text[end:])
        document.text = "".join(pieces)
        validate_markup(document.text, require_identities=True)
    read_identities("\n".join(document.text for document in documents))


def validate_inputs(requirements: list[dict], documents: list[Document]) -> None:
    """Reject bad formulas and unknown aliases before opening a database transaction."""
    known = set()
    for document in documents:
        known.update(key for key in document.ids if key is not None)
        for tag in parse_entity_tags(document.text):
            if "id" in tag.attributes:
                known.add(int(tag.attributes["id"]))
            if "mnemonic" in tag.attributes:
                known.add(tag.attributes["mnemonic"])

    def is_boolean(node):
        return isinstance(node, BinOp) and (node.op in {"==", "!=", "<", "<=", ">", ">="}
            or node.op in {"&&", "||"} and is_boolean(node.left) and is_boolean(node.right))

    for requirement in requirements:
        if requirement["kind"] == "formula":
            ast = parse(requirement["equation"])
            if not is_boolean(ast):
                raise ValueError("A requirement formula must be a boolean comparison.")
            keys = collect_ids(ast)
        else:
            keys = {entity_key(key) for key in requirement["ids_related"]}
        if keys - known:
            raise ValueError(f"Unknown entity ID in requirements: {keys - known}")


def remap_requirements(requirements: list[dict], documents: list[Document], file_keys: dict[str, str]):
    registry = build_registry({file_keys[document.name]: (document.role, document.text) for document in documents})
    aliases = {}
    for document in documents:
        identities = read_identities(document.text)
        for identity, old_id in zip(identities, document.ids, strict=True):
            for key in (old_id, identity.num, identity.mnemonic):
                if key is None:
                    continue
                if key in aliases and aliases[key] != identity.num:
                    raise ValueError(f"Conflicting entity alias: {key!r}.")
                aliases[key] = identity.num

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
    validate_inputs(requirements, documents)
    if metadata.target_row:
        matches = False
        for document in documents:
            if document.role != "card":
                continue
            spans = find_target_row_spans(document.text, metadata.target_row)
            matches = any(start <= tag.span[0] and tag.span[1] <= end
                          for tag in parse_entity_tags(document.text) for start, end in spans)
        if not matches:
            raise ValueError(f"Target row not found in final report: {metadata.target_row!r}")

    with session_scope() as session:
        report = Report(**metadata.model_dump(), stage="verification", processing_status="queued")
        session.add(report)
        session.flush()
        session.refresh(report, with_for_update=True)
        canonicalize_documents(session, report.id, documents)
        # Validate every supplied reference before writing any artifacts.
        remap_requirements(requirements, documents, {document.name: document.name for document in documents})
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
        for definition in definitions:
            data = dict(definition)
            data.pop("id")  # Requirement IDs are allocated by this database.
            session.add(Requirement(report_id=report.id, text=data.pop("text"), source=data.pop("source"),
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
