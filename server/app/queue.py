"""Separate FIFO queues for document processing and report verification."""

import os

from dotenv import load_dotenv
from redis import Redis
from rq import Queue


def _queue(name: str) -> Queue:
    load_dotenv()
    connection = Redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
                                socket_connect_timeout=5)
    return Queue(name, connection=connection, default_timeout=3600)


def ocr_queue() -> Queue:
    return _queue("ocr")


def entities_queue() -> Queue:
    return _queue("entities")


def verification_queue() -> Queue:
    return _queue("verification")


def enqueue_ocr(file_revision_id: int) -> None:
    ocr_queue().enqueue("app.jobs.ocr.process_ocr", file_revision_id)


def enqueue_entities(file_revision_id: int) -> None:
    entities_queue().enqueue("app.jobs.entities.process_entities", file_revision_id)


def enqueue_verification(report_id: int) -> None:
    verification_queue().enqueue("app.jobs.verification.process_verification", report_id)


def enqueue_requirement_generation(report_id: int) -> None:
    verification_queue().enqueue("app.jobs.verification.process_requirement_generation", report_id)
