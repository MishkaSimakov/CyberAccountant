"""One FIFO queue for all document OCR jobs."""

import os

from dotenv import load_dotenv
from redis import Redis
from rq import Queue


def ocr_queue() -> Queue:
    load_dotenv()
    connection = Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
                                socket_connect_timeout=5)
    return Queue("ocr", connection=connection, default_timeout=3600)


def enqueue_ocr(file_revision_id: int) -> None:
    ocr_queue().enqueue("app.jobs.ocr.process_ocr", file_revision_id)
