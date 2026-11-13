"""Application log routing, run metadata, and process-safe multiline records."""

import logging
import multiprocessing
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

import pytest

from app.logging import create_file_log, create_report_log, file_log_run


def _write_records(args):
    path, worker = args
    records = []
    with file_log_run(path, "extraction", report_id=7, document_id=worker, revision_id=worker + 100,
                      revision=2) as logger:
        for index in range(12):
            record = f"BEGIN {worker}:{index}\n" + f"Журнал {worker}:{index}\r\n" * 4_000 + f"END {worker}:{index}"
            logger.info("%s", record)
            records.append(record)
    return records


@pytest.mark.parametrize("processes", [False, True])
def test_concurrent_workers_preserve_complete_multiline_records(tmp_path, processes):
    path = tmp_path / "shared.log"
    executor = (ProcessPoolExecutor(max_workers=3, mp_context=multiprocessing.get_context("spawn"))
                if processes else ThreadPoolExecutor(max_workers=3))
    with executor as pool:
        records = list(pool.map(_write_records, [(path, worker) for worker in range(3)]))
    content = path.read_bytes()
    assert content.count(b"\nRUN ") == 3
    for batch in records:
        for record in batch:
            assert content.count(record.encode("utf-8")) == 1


@pytest.mark.parametrize("fails", [False, True])
def test_document_run_binds_metadata_and_reports_only_lifecycle(tmp_path, fails):
    document_path = tmp_path / "document.log"
    report_path = tmp_path / "report.log"
    registry = dict(logging.Logger.manager.loggerDict)
    captured = []

    class Capture(logging.Handler):
        def emit(self, record):
            captured.append(record)

    def run():
        with file_log_run(document_path, "entity_extraction", report_id=7, document_id=3,
                          revision_id=17, revision=2, report_log_path=report_path) as logger:
            handler = Capture()
            logger.logger.addHandler(handler)
            logger.info("Detailed document text\r\nsecond line")
            if fails:
                raise ValueError("private failure details")

    if fails:
        with pytest.raises(ValueError, match="private failure"):
            run()
    else:
        run()
    record = captured[0]
    assert (record.report_id, record.document_id, record.revision_id, record.revision, record.stage) == (
        7, 3, 17, 2, "entity_extraction")
    assert record.run_id
    report = report_path.read_text()
    assert len(report.splitlines()) == 2
    assert report.count(f"run={record.run_id}") == 2
    assert "Document job started" in report
    assert ("Document job failed (ValueError)" if fails else "Document job completed") in report
    assert "Detailed document text" not in report
    assert "private failure details" not in report
    assert b"Detailed document text\r\nsecond line" in document_path.read_bytes()
    if fails:
        assert "Traceback" in document_path.read_text()
        assert "ERROR (ValueError): private failure details" in document_path.read_text()
    assert dict(logging.Logger.manager.loggerDict) == registry


def test_application_log_creation_preserves_history(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE_ROOT", str(tmp_path))
    for create, args, expected in [(create_report_log, (7,), "7/processing.log"),
                                   (create_file_log, (7, 3), "7/3/processing.log")]:
        relative = create(*args)
        assert relative == expected
        path = tmp_path / relative
        path.write_bytes(b"Earlier log\r\n")
        assert create(*args) == relative
        assert path.read_bytes() == b"Earlier log\r\n"
