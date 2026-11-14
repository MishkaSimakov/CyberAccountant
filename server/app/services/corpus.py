"""Load reviewed canonical Markdown at the application storage boundary."""

from processing.common.corpus import ReviewedCorpus
from processing.common.documents import DocumentRevision, ReviewedDocument
from processing.entities.corpus import build_registry
from .. import storage


def load_corpus(report_id, files) -> ReviewedCorpus:
    return ReviewedCorpus(report_id, tuple(ReviewedDocument(
        DocumentRevision(report_id, file.file_id, file.version), file.name, file.role,
        storage.artifact_path(file.entities_path).read_text(encoding="utf-8"),
    ) for file in files))


def validation_registry(files):
    return build_registry({str(file.file_id): (
        "card" if file.role == "final_report" else "data",
        storage.artifact_path(file.entities_path).read_text(encoding="utf-8"),
    ) for file in files}, revisions={str(file.file_id): DocumentRevision(file.report_id, file.file_id, file.version)
                                     for file in files})


def input_signature(report, files):
    return (report.contract_number, report.period_start, report.period_end, report.target_row,
            tuple((file.id, file.file_id, file.version, file.name, file.role, file.entities_path,
                   file.status, file.updated_at) for file in files))
