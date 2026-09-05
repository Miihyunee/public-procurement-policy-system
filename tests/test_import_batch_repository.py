"""
``import_batch`` 저장소 테스트.

배치 저장·조회·대체 처리와, 대체된 배치의 행이 **계산 대상에서 빠지는지**를
검증합니다. 대체는 행을 지우지 않고 상태로만 구분합니다(D-25).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from procurement.database.bootstrap import init_db
from procurement.database.import_batch_repository import (
    ImportBatchRepository,
    ImportBatchValidationError,
)
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import ImportBatch, Purchase
from procurement.models.import_batch import STATUS_ACTIVE, STATUS_SUPERSEDED

JULY = (date(2026, 7, 1), date(2026, 7, 31))
AUGUST = (date(2026, 8, 1), date(2026, 8, 31))


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "batch.db"
    init_db(path)
    return path


@pytest.fixture
def repo(db_path: Path) -> ImportBatchRepository:
    return ImportBatchRepository(db_path)


def _batch(file_name: str = "2026-07.xlsx", period: tuple[date, date] = JULY) -> ImportBatch:
    return ImportBatch(file_name=file_name, period_start=period[0], period_end=period[1])


class TestInsert:
    """저장."""

    def test_assigns_batch_id(self, repo: ImportBatchRepository) -> None:
        assert repo.insert(_batch()).batch_id is not None

    def test_defaults_to_active(self, repo: ImportBatchRepository) -> None:
        assert repo.insert(_batch()).status == STATUS_ACTIVE

    def test_fills_timestamps(self, repo: ImportBatchRepository) -> None:
        saved = repo.insert(_batch())
        assert saved.uploaded_at is not None
        assert saved.created_at is not None

    def test_rejects_empty_file_name(self, repo: ImportBatchRepository) -> None:
        with pytest.raises(ImportBatchValidationError):
            repo.insert(_batch(file_name="   "))

    def test_rejects_reversed_period(self, repo: ImportBatchRepository) -> None:
        with pytest.raises(ImportBatchValidationError):
            repo.insert(
                ImportBatch(
                    file_name="x.xlsx",
                    period_start=date(2026, 7, 31),
                    period_end=date(2026, 7, 1),
                )
            )

    def test_rejects_unknown_status(self, repo: ImportBatchRepository) -> None:
        with pytest.raises(ImportBatchValidationError):
            repo.insert(
                ImportBatch(
                    file_name="x.xlsx",
                    period_start=JULY[0],
                    period_end=JULY[1],
                    status="FAILED",
                )
            )

    def test_preserves_amount_precision(self, repo: ImportBatchRepository) -> None:
        batch = ImportBatch(
            file_name="x.xlsx",
            period_start=JULY[0],
            period_end=JULY[1],
            total_amount=Decimal("1234567.89"),
        )
        saved = repo.insert(batch)
        assert saved.batch_id is not None
        found = repo.find_by_id(saved.batch_id)
        assert found is not None
        assert found.total_amount == Decimal("1234567.89")


class TestFind:
    """조회."""

    def test_find_by_id_returns_none_when_missing(self, repo: ImportBatchRepository) -> None:
        assert repo.find_by_id(999) is None

    def test_find_active_by_period_matches_exact_range(self, repo: ImportBatchRepository) -> None:
        repo.insert(_batch())
        found = repo.find_active_by_period(*JULY)
        assert found is not None
        assert found.period_start == JULY[0]

    def test_find_active_by_period_ignores_other_period(self, repo: ImportBatchRepository) -> None:
        repo.insert(_batch())
        assert repo.find_active_by_period(*AUGUST) is None

    def test_find_active_by_period_ignores_superseded(self, repo: ImportBatchRepository) -> None:
        first = repo.insert(_batch())
        second = repo.insert(_batch())
        assert first.batch_id is not None and second.batch_id is not None
        repo.supersede(first.batch_id, second.batch_id)
        found = repo.find_active_by_period(*JULY)
        assert found is not None
        assert found.batch_id == second.batch_id

    def test_find_by_file_hash(self, repo: ImportBatchRepository) -> None:
        batch = ImportBatch(
            file_name="x.xlsx", period_start=JULY[0], period_end=JULY[1], file_hash="abc"
        )
        repo.insert(batch)
        assert len(repo.find_by_file_hash("abc")) == 1
        assert repo.find_by_file_hash("other") == []

    def test_find_all_includes_superseded(self, repo: ImportBatchRepository) -> None:
        first = repo.insert(_batch())
        second = repo.insert(_batch())
        assert first.batch_id is not None and second.batch_id is not None
        repo.supersede(first.batch_id, second.batch_id)
        assert len(repo.find_all()) == 2

    def test_count(self, repo: ImportBatchRepository) -> None:
        assert repo.count() == 0
        repo.insert(_batch())
        assert repo.count() == 1


class TestSupersede:
    """대체 처리."""

    def test_marks_status(self, repo: ImportBatchRepository) -> None:
        first = repo.insert(_batch())
        second = repo.insert(_batch())
        assert first.batch_id is not None and second.batch_id is not None
        repo.supersede(first.batch_id, second.batch_id)
        found = repo.find_by_id(first.batch_id)
        assert found is not None
        assert found.status == STATUS_SUPERSEDED
        assert found.superseded_by == second.batch_id

    def test_returns_false_for_missing_batch(self, repo: ImportBatchRepository) -> None:
        assert repo.supersede(999, 1) is False

    def test_second_supersede_is_noop(self, repo: ImportBatchRepository) -> None:
        first = repo.insert(_batch())
        second = repo.insert(_batch())
        assert first.batch_id is not None and second.batch_id is not None
        assert repo.supersede(first.batch_id, second.batch_id) is True
        assert repo.supersede(first.batch_id, second.batch_id) is False

    def test_does_not_delete_rows(self, repo: ImportBatchRepository) -> None:
        first = repo.insert(_batch())
        second = repo.insert(_batch())
        assert first.batch_id is not None and second.batch_id is not None
        repo.supersede(first.batch_id, second.batch_id)
        assert repo.find_by_id(first.batch_id) is not None


class TestUpdateTotals:
    """적재 결과 기록."""

    def test_updates_counts(self, repo: ImportBatchRepository) -> None:
        batch = repo.insert(_batch())
        assert batch.batch_id is not None
        repo.update_totals(batch.batch_id, 12, Decimal("500"))
        found = repo.find_by_id(batch.batch_id)
        assert found is not None
        assert found.row_count == 12
        assert found.total_amount == Decimal("500")

    def test_returns_false_for_missing_batch(self, repo: ImportBatchRepository) -> None:
        assert repo.update_totals(999, 1, Decimal("1")) is False


class TestCalculationTarget:
    """대체된 배치의 행은 계산 대상에서 빠진다."""

    def test_superseded_rows_excluded(self, db_path: Path) -> None:
        batches = ImportBatchRepository(db_path)
        purchases = PurchaseRepository(db_path)

        old = batches.insert(_batch())
        new = batches.insert(_batch())
        assert old.batch_id is not None and new.batch_id is not None

        purchases.insert(_purchase(Decimal("100"), old.batch_id))
        purchases.insert(_purchase(Decimal("200"), new.batch_id))

        batches.supersede(old.batch_id, new.batch_id)

        amounts = [p.amount for p in purchases.find_for_calculation()]
        assert amounts == [Decimal("200")]

    def test_find_all_still_returns_everything(self, db_path: Path) -> None:
        """``find_all()`` 의 동작은 바뀌지 않는다(하위 호환)."""
        batches = ImportBatchRepository(db_path)
        purchases = PurchaseRepository(db_path)
        old = batches.insert(_batch())
        new = batches.insert(_batch())
        assert old.batch_id is not None and new.batch_id is not None
        purchases.insert(_purchase(Decimal("100"), old.batch_id))
        purchases.insert(_purchase(Decimal("200"), new.batch_id))
        batches.supersede(old.batch_id, new.batch_id)

        assert len(purchases.find_all()) == 2

    def test_rows_without_batch_are_included(self, db_path: Path) -> None:
        """배치 없이 적재된 행(기존 데이터)은 계속 계산에 포함된다."""
        purchases = PurchaseRepository(db_path)
        purchases.insert(_purchase(Decimal("100"), None))
        assert len(purchases.find_for_calculation()) == 1


def _purchase(amount: Decimal, batch_id: int | None) -> Purchase:
    return Purchase(
        business_no="1234567890",
        company_name="테스트업체",
        contract_date=date(2026, 7, 1),
        payment_date=date(2026, 7, 20),
        amount=amount,
        batch_id=batch_id,
    )
