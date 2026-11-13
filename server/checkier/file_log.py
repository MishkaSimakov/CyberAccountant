"""Append-only document logs shared by processing stages."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import fcntl

current_log_path: ContextVar[Path | None] = ContextVar("current_log_path", default=None)


def append_log(path: Path, text: str) -> None:
    """Keep complete entries together even when processes share a log."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as output:
        fcntl.flock(output, fcntl.LOCK_EX)
        output.write(text)
        output.flush()


@contextmanager
def file_log_run(path: Path, stage: str):
    """Route model outputs into this file for the duration of one stage run."""
    timestamp = datetime.now(timezone.utc).isoformat()
    append_log(path, f"\n{'=' * 80}\nRUN {uuid4().hex} | {timestamp} | {stage}\n{'=' * 80}\n")
    token = current_log_path.set(path)
    try:
        yield
    except Exception as error:
        append_log(path, f"\nERROR ({type(error).__name__}): {error}\n")
        raise
    finally:
        current_log_path.reset(token)
