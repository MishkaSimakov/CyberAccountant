"""Transactional reservation of report-local entity IDs."""

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from processing.entities.identifiers import MNEMONIC_CAPACITY

from .models import ReportEntityCounter


def reserve_entity_ids(session: Session, report_id: int, count: int) -> range:
    """Reserve a contiguous half-open range in the caller's transaction.

    Zero is a no-op returning range(0), without accessing the database.
    Invalid counts and exhausted mnemonic capacity raise ValueError. A missing
    report raises the database's foreign-key error. This function never commits
    or rolls back; rolling back the transaction releases its reservations.
    """
    if type(count) is not int or not 0 <= count <= MNEMONIC_CAPACITY:
        raise ValueError(f"Entity ID count must be an integer in [0, {MNEMONIC_CAPACITY}].")
    if count == 0:
        return range(0)

    counter = ReportEntityCounter.__table__
    statement = insert(counter).values(report_id=report_id, next_entity_id=count)
    statement = statement.on_conflict_do_update(
        index_elements=[counter.c.report_id],
        set_={"next_entity_id": counter.c.next_entity_id + count},
        where=counter.c.next_entity_id <= MNEMONIC_CAPACITY - count,
    ).returning(counter.c.next_entity_id)
    end = session.execute(statement).scalar_one_or_none()
    if end is None:
        raise ValueError("Entity ID reservation exceeds mnemonic capacity.")
    return range(end - count, end)
