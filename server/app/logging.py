"""Application-owned, append-only processing logs and contextual run loggers."""

import logging
from contextlib import ExitStack, contextmanager
from pathlib import Path
from uuid import uuid4

import fcntl

from .storage import artifact_path


def append_log(path: Path, text: str) -> None:
    """Keep an entire multiline record together across threads and processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as output:
        fcntl.flock(output, fcntl.LOCK_EX)
        output.write(text)
        output.flush()


def _create_log(relative: str) -> str:
    path = artifact_path(relative)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(exist_ok=True)
    return relative


def create_file_log(report_id: int, file_id: int) -> str:
    return _create_log(f"{report_id}/{file_id}/processing.log")


def create_report_log(report_id: int) -> str:
    return _create_log(f"{report_id}/processing.log")


class _AppendHandler(logging.Handler):
    def __init__(self, path: Path):
        super().__init__()
        self.path = path

    def emit(self, record: logging.LogRecord) -> None:
        append_log(self.path, self.format(record) + "\n")


@contextmanager
def _file_logger(path: Path, metadata: dict):
    # Private loggers never enter logging's global registry or propagate raw data.
    logger = logging.Logger("processing", level=logging.DEBUG)
    logger.propagate = False
    handler = _AppendHandler(path)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | run=%(run_id)s | report=%(report_id)s | "
        "document=%(document_id)s | revision_id=%(revision_id)s | revision=%(revision)s | stage=%(stage)s | %(message)s"
    ))
    logger.addHandler(handler)
    try:
        yield logging.LoggerAdapter(logger, metadata, merge_extra=True)
    finally:
        logger.removeHandler(handler)
        handler.close()


@contextmanager
def file_log_run(path: Path, stage: str, *, report_id: int | None = None,
                 document_id: int | None = None, revision_id: int | None = None,
                 revision: int | None = None, report_log_path: Path | None = None):
    """Yield an explicit logger; document runs also report a short lifecycle."""
    metadata = dict(run_id=uuid4().hex, report_id=report_id, document_id=document_id,
                    revision_id=revision_id, revision=revision, stage=stage)
    with ExitStack() as stack:
        logger = stack.enter_context(_file_logger(path, metadata))
        report_logger = (stack.enter_context(_file_logger(report_log_path, metadata))
                         if report_log_path is not None else None)
        logger.info("\n%s\nRUN %s | %s\n%s", "=" * 80, metadata["run_id"], stage, "=" * 80)
        if report_logger is not None:
            report_logger.info("Document job started")
        try:
            yield logger
        except Exception as error:
            logger.exception("ERROR (%s): %s", type(error).__name__, error)
            if report_logger is not None:
                report_logger.error("Document job failed (%s)", type(error).__name__)
            raise
        else:
            logger.info("Run completed")
            if report_logger is not None:
                report_logger.info("Document job completed")
