"""Atomic ID reservations against real PostgreSQL, including competing sessions."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import Mock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import database
from app.entity_ids import reserve_entity_ids
from app.models import Report, ReportEntityCounter
from processing.entities.identifiers import MNEMONIC_CAPACITY


@pytest.fixture
def report_ids(db):
    with database.session_scope() as session:
        reports = [Report(name="First report"), Report(name="Second report")]
        session.add_all(reports)
        session.flush()
        return tuple(report.id for report in reports)


@pytest.mark.parametrize("count", [-1, True, False, 1.5, "1", None, MNEMONIC_CAPACITY + 1, 10 ** 100])
def test_invalid_count_does_not_access_database(count):
    session = Mock(spec=Session)
    with pytest.raises(ValueError, match="count"):
        reserve_entity_ids(session, 1, count)
    assert session.mock_calls == []


def test_zero_is_a_no_op():
    session = Mock(spec=Session)
    assert reserve_entity_ids(session, 1, 0) == range(0)
    assert session.mock_calls == []


def test_reservations_are_contiguous_and_report_local(report_ids):
    first, second = report_ids
    with database.session_scope() as session:
        assert reserve_entity_ids(session, first, 3) == range(0, 3)
        assert reserve_entity_ids(session, first, 2) == range(3, 5)
        assert reserve_entity_ids(session, second, 4) == range(0, 4)
        assert reserve_entity_ids(session, first, 0) == range(0)

    with database.session_scope() as session:
        assert session.get(ReportEntityCounter, first).next_entity_id == 5
        assert session.get(ReportEntityCounter, second).next_entity_id == 4
        assert reserve_entity_ids(session, first, 1) == range(5, 6)


@pytest.mark.parametrize("initial", [0, 9])
def test_caller_rollback_releases_ids_and_does_not_commit(report_ids, initial):
    report_id = report_ids[0]
    if initial:
        with database.session_scope() as session:
            reserve_entity_ids(session, report_id, initial)

    with pytest.raises(RuntimeError, match="abort"):
        with database.session_scope() as session:
            assert reserve_entity_ids(session, report_id, 3) == range(initial, initial + 3)
            with database.session_scope() as observer:
                counter = observer.get(ReportEntityCounter, report_id)
                assert (counter.next_entity_id if counter else 0) == initial
            raise RuntimeError("abort")

    with database.session_scope() as session:
        counter = session.get(ReportEntityCounter, report_id)
        assert (counter.next_entity_id if counter else 0) == initial
        assert reserve_entity_ids(session, report_id, 3) == range(initial, initial + 3)


def test_capacity_boundary_and_rejected_reservation_preserve_counter(report_ids):
    report_id = report_ids[0]
    with database.session_scope() as session:
        assert reserve_entity_ids(session, report_id, MNEMONIC_CAPACITY - 2) == range(MNEMONIC_CAPACITY - 2)
        with pytest.raises(ValueError, match="capacity"):
            reserve_entity_ids(session, report_id, 3)
        assert reserve_entity_ids(session, report_id, 2) == range(MNEMONIC_CAPACITY - 2, MNEMONIC_CAPACITY)
        with pytest.raises(ValueError, match="capacity"):
            reserve_entity_ids(session, report_id, 1)
        assert reserve_entity_ids(session, report_id, 0) == range(0)

    with database.session_scope() as session:
        assert session.get(ReportEntityCounter, report_id).next_entity_id == MNEMONIC_CAPACITY
        assert reserve_entity_ids(session, report_ids[1], MNEMONIC_CAPACITY) == range(MNEMONIC_CAPACITY)


def test_reservation_requires_existing_report(db):
    with pytest.raises(IntegrityError):
        with database.session_scope() as session:
            reserve_entity_ids(session, 12345, 1)


def concurrent_session():
    """Bound database waits so a broken allocation cannot hang the test suite."""
    session = Session(database.get_engine())
    session.execute(text("SET LOCAL lock_timeout = '5s'"))
    session.execute(text("SET LOCAL statement_timeout = '10s'"))
    return session


@pytest.mark.parametrize("initial", [0, 11])
def test_concurrent_first_and_existing_allocations_do_not_overlap(report_ids, initial):
    report_id = report_ids[0]
    if initial:
        with database.session_scope() as session:
            reserve_entity_ids(session, report_id, initial)
    counts = [1, 3, 2, 5]
    ready = Barrier(len(counts), timeout=10)

    def reserve(count):
        with concurrent_session() as session:
            ready.wait()
            result = reserve_entity_ids(session, report_id, count)
            session.commit()
            return result

    with ThreadPoolExecutor(max_workers=len(counts)) as executor:
        futures = [executor.submit(reserve, count) for count in counts]
        reservations = [future.result(timeout=15) for future in futures]

    assert [len(reserved) for reserved in reservations] == counts
    ordered = sorted(reservations, key=lambda reserved: reserved.start)
    assert [num for reserved in ordered for num in reserved] == list(range(initial, initial + sum(counts)))
    with database.session_scope() as session:
        assert session.get(ReportEntityCounter, report_id).next_entity_id == initial + sum(counts)


@pytest.mark.parametrize("initial", [0, 7])
def test_different_reports_reserve_concurrently_before_either_commits(report_ids, initial):
    if initial:
        with database.session_scope() as session:
            for report_id in report_ids:
                reserve_entity_ids(session, report_id, initial)
    ready = Barrier(2, timeout=10)
    reserved = Barrier(2, timeout=10)

    def reserve(report_id):
        with concurrent_session() as session:
            ready.wait()
            result = reserve_entity_ids(session, report_id, 3)
            # Both reports must allocate while the other transaction is open.
            reserved.wait()
            session.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(reserve, report_id) for report_id in report_ids]
        assert [future.result(timeout=15) for future in futures] == [range(initial, initial + 3)] * 2

    with database.session_scope() as session:
        for report_id in report_ids:
            assert session.get(ReportEntityCounter, report_id).next_entity_id == initial + 3


def test_concurrent_reservations_cannot_exceed_capacity(report_ids):
    report_id = report_ids[0]
    with database.session_scope() as session:
        reserve_entity_ids(session, report_id, MNEMONIC_CAPACITY - 3)
    ready = Barrier(2, timeout=10)

    def reserve():
        with concurrent_session() as session:
            ready.wait()
            try:
                result = reserve_entity_ids(session, report_id, 2)
            except ValueError as error:
                assert "capacity" in str(error)
                result = None
            session.commit()
            return result

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(reserve) for _ in range(2)]
        results = [future.result(timeout=15) for future in futures]
    assert results.count(None) == 1
    assert range(MNEMONIC_CAPACITY - 3, MNEMONIC_CAPACITY - 1) in results
    with database.session_scope() as session:
        assert session.get(ReportEntityCounter, report_id).next_entity_id == MNEMONIC_CAPACITY - 1
        assert reserve_entity_ids(session, report_id, 1) == range(MNEMONIC_CAPACITY - 1, MNEMONIC_CAPACITY)
