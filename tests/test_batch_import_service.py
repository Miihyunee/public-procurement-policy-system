"""
월별 누적 적재 시나리오 테스트.

PM 이 제시한 운영 시나리오를 그대로 검증합니다.

1. 7월 데이터 업로드 → 7월 배치 생성
2. 8월 데이터 업로드 → 7월 데이터 유지, 7+8월 누적 조회 가능
3. 8월 수정본 재업로드 → 기존 8월 배치를 대체, **중복 합산되지 않음**
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from procurement.calculators.procurement_achievement import ProcurementAchievementCalculator
from procurement.core.period import PAYMENT_DATE, PeriodFilter
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.import_batch_repository import ImportBatchRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.importers import BatchImportService, PurchaseImporter
from procurement.models.import_batch import STATUS_SUPERSEDED

JULY = (date(2026, 7, 1), date(2026, 7, 31))
AUGUST = (date(2026, 8, 1), date(2026, 8, 31))


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "monthly.db"
    init_db(path)
    seed_policies(path)
    return path


@pytest.fixture
def service(db_path: Path) -> BatchImportService:
    return BatchImportService(
        PurchaseImporter(PurchaseRepository(db_path), CompanyRepository(db_path)),
        ImportBatchRepository(db_path),
        PurchaseRepository(db_path),
    )


@pytest.fixture
def calculator(db_path: Path) -> ProcurementAchievementCalculator:
    return ProcurementAchievementCalculator(
        PurchaseRepository(db_path),
        CertificationRepository(db_path),
        PolicyRepository(db_path),
    )


def _row(business_no: str, amount: str, day: date) -> dict[str, Any]:
    return {
        "business_no": business_no,
        "company_name": "테스트업체",
        "contract_date": day.isoformat(),
        "payment_date": day.isoformat(),
        "amount": amount,
    }


JULY_ROWS = [
    _row("1234567890", "1000", date(2026, 7, 10)),
    _row("2234567890", "2000", date(2026, 7, 20)),
]
AUGUST_ROWS = [_row("3234567890", "3000", date(2026, 8, 15))]
AUGUST_FIXED_ROWS = [
    _row("3234567890", "3500", date(2026, 8, 15)),
    _row("4234567890", "500", date(2026, 8, 16)),
]


class TestFirstUpload:
    """① 7월 업로드."""

    def test_creates_batch(self, service: BatchImportService) -> None:
        result = service.import_batch(
            JULY_ROWS, file_name="2026-07.xlsx", period_start=JULY[0], period_end=JULY[1]
        )
        assert result.batch.batch_id is not None
        assert result.batch.is_active

    def test_records_row_count_and_total(self, service: BatchImportService) -> None:
        result = service.import_batch(
            JULY_ROWS, file_name="2026-07.xlsx", period_start=JULY[0], period_end=JULY[1]
        )
        assert result.batch.row_count == 2
        assert result.batch.total_amount == Decimal("3000")

    def test_nothing_superseded(self, service: BatchImportService) -> None:
        result = service.import_batch(
            JULY_ROWS, file_name="2026-07.xlsx", period_start=JULY[0], period_end=JULY[1]
        )
        assert result.replaced is False

    def test_rows_are_linked_to_batch(self, service: BatchImportService, db_path: Path) -> None:
        result = service.import_batch(
            JULY_ROWS, file_name="2026-07.xlsx", period_start=JULY[0], period_end=JULY[1]
        )
        assert result.batch.batch_id is not None
        rows = PurchaseRepository(db_path).find_by_batch(result.batch.batch_id)
        assert len(rows) == 2


class TestAccumulation:
    """② 8월 업로드 — 7월 데이터가 유지되고 누적된다."""

    @pytest.fixture(autouse=True)
    def _upload_two_months(self, service: BatchImportService) -> None:
        service.import_batch(
            JULY_ROWS, file_name="2026-07.xlsx", period_start=JULY[0], period_end=JULY[1]
        )
        service.import_batch(
            AUGUST_ROWS, file_name="2026-08.xlsx", period_start=AUGUST[0], period_end=AUGUST[1]
        )

    def test_july_rows_remain(self, db_path: Path) -> None:
        assert len(PurchaseRepository(db_path).find_for_calculation()) == 3

    def test_both_batches_active(self, db_path: Path) -> None:
        batches = ImportBatchRepository(db_path).find_all()
        assert [batch.is_active for batch in batches] == [True, True]

    def test_year_total_is_cumulative(self, calculator: ProcurementAchievementCalculator) -> None:
        period = PeriodFilter.for_year(2026, PAYMENT_DATE)
        assert calculator.calculate_total_purchase(period) == Decimal("6000")

    def test_july_only_query(self, db_path: Path) -> None:
        july = PeriodFilter(start=JULY[0], end=JULY[1], date_field=PAYMENT_DATE)
        rows = PurchaseRepository(db_path).find_for_calculation(july)
        assert sum(row.amount for row in rows) == Decimal("3000")


class TestReupload:
    """③ 8월 수정본 재업로드 — 대체되고 중복 합산되지 않는다."""

    @pytest.fixture(autouse=True)
    def _upload_and_fix(self, service: BatchImportService) -> None:
        service.import_batch(
            JULY_ROWS, file_name="2026-07.xlsx", period_start=JULY[0], period_end=JULY[1]
        )
        service.import_batch(
            AUGUST_ROWS, file_name="2026-08.xlsx", period_start=AUGUST[0], period_end=AUGUST[1]
        )

    def test_reports_replacement(self, service: BatchImportService) -> None:
        result = service.import_batch(
            AUGUST_FIXED_ROWS,
            file_name="2026-08-수정본.xlsx",
            period_start=AUGUST[0],
            period_end=AUGUST[1],
        )
        assert result.replaced is True
        assert result.superseded_batch is not None
        assert result.superseded_batch.status == STATUS_SUPERSEDED

    def test_previous_total_is_reported(self, service: BatchImportService) -> None:
        """조용히 덮어쓰지 않고 대체 전 건수·금액을 알려준다."""
        result = service.import_batch(
            AUGUST_FIXED_ROWS,
            file_name="2026-08-수정본.xlsx",
            period_start=AUGUST[0],
            period_end=AUGUST[1],
        )
        assert result.superseded_batch is not None
        assert result.superseded_batch.row_count == 1
        assert result.superseded_batch.total_amount == Decimal("3000")

    def test_no_double_counting(
        self, service: BatchImportService, calculator: ProcurementAchievementCalculator
    ) -> None:
        service.import_batch(
            AUGUST_FIXED_ROWS,
            file_name="2026-08-수정본.xlsx",
            period_start=AUGUST[0],
            period_end=AUGUST[1],
        )
        period = PeriodFilter.for_year(2026, PAYMENT_DATE)
        # 7월 3000 + 8월 수정본 4000 (기존 8월 3000 은 제외)
        assert calculator.calculate_total_purchase(period) == Decimal("7000")

    def test_july_untouched(self, service: BatchImportService, db_path: Path) -> None:
        service.import_batch(
            AUGUST_FIXED_ROWS,
            file_name="2026-08-수정본.xlsx",
            period_start=AUGUST[0],
            period_end=AUGUST[1],
        )
        july = PeriodFilter(start=JULY[0], end=JULY[1], date_field=PAYMENT_DATE)
        rows = PurchaseRepository(db_path).find_for_calculation(july)
        assert sum(row.amount for row in rows) == Decimal("3000")

    def test_old_rows_are_not_deleted(self, service: BatchImportService, db_path: Path) -> None:
        """대체된 배치의 행도 남는다(추적 가능)."""
        service.import_batch(
            AUGUST_FIXED_ROWS,
            file_name="2026-08-수정본.xlsx",
            period_start=AUGUST[0],
            period_end=AUGUST[1],
        )
        assert len(PurchaseRepository(db_path).find_all()) == 5
        assert len(PurchaseRepository(db_path).find_for_calculation()) == 4

    def test_only_one_active_batch_per_period(
        self, service: BatchImportService, db_path: Path
    ) -> None:
        service.import_batch(
            AUGUST_FIXED_ROWS,
            file_name="2026-08-수정본.xlsx",
            period_start=AUGUST[0],
            period_end=AUGUST[1],
        )
        active = ImportBatchRepository(db_path).find_active_by_period_all(*AUGUST)
        assert len(active) == 1

    def test_no_conflicts_detected(self, service: BatchImportService) -> None:
        service.import_batch(
            AUGUST_FIXED_ROWS,
            file_name="2026-08-수정본.xlsx",
            period_start=AUGUST[0],
            period_end=AUGUST[1],
        )
        assert service.find_conflicts(*AUGUST) == []


class TestDuplicateFile:
    """같은 파일을 그대로 다시 올린 경우 — 경고만 남기고 막지 않는다."""

    def test_warns_on_same_hash(self, service: BatchImportService) -> None:
        service.import_batch(
            JULY_ROWS,
            file_name="2026-07.xlsx",
            period_start=JULY[0],
            period_end=JULY[1],
            file_hash="same-hash",
        )
        result = service.import_batch(
            JULY_ROWS,
            file_name="2026-07.xlsx",
            period_start=JULY[0],
            period_end=JULY[1],
            file_hash="same-hash",
        )
        assert result.duplicate_of is not None

    def test_still_replaces(self, service: BatchImportService) -> None:
        service.import_batch(
            JULY_ROWS,
            file_name="2026-07.xlsx",
            period_start=JULY[0],
            period_end=JULY[1],
            file_hash="same-hash",
        )
        result = service.import_batch(
            JULY_ROWS,
            file_name="2026-07.xlsx",
            period_start=JULY[0],
            period_end=JULY[1],
            file_hash="same-hash",
        )
        assert result.replaced is True

    def test_no_warning_without_hash(self, service: BatchImportService) -> None:
        result = service.import_batch(
            JULY_ROWS, file_name="2026-07.xlsx", period_start=JULY[0], period_end=JULY[1]
        )
        assert result.duplicate_of is None


class TestReportText:
    """사람이 읽는 요약."""

    def test_mentions_replacement(self, service: BatchImportService) -> None:
        service.import_batch(
            AUGUST_ROWS, file_name="2026-08.xlsx", period_start=AUGUST[0], period_end=AUGUST[1]
        )
        result = service.import_batch(
            AUGUST_FIXED_ROWS,
            file_name="2026-08-수정본.xlsx",
            period_start=AUGUST[0],
            period_end=AUGUST[1],
        )
        assert "대체" in result.format_report()


class TestBackwardCompatibleImport:
    """배치 없이 적재하는 기존 방식은 그대로 동작한다."""

    def test_import_rows_without_batch(self, db_path: Path) -> None:
        importer = PurchaseImporter(PurchaseRepository(db_path), CompanyRepository(db_path))
        report = importer.import_rows(JULY_ROWS)
        assert report.stored_count == 2
        stored = PurchaseRepository(db_path).find_all()
        assert all(purchase.batch_id is None for purchase in stored)
