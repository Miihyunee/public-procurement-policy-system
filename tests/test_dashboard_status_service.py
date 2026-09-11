"""
데이터 적재 현황 서비스 테스트.

:class:`DataStatusService` 가 저장소 상태를 **계산 없이** 그대로 집계하는지
검증합니다. Calculator 를 호출하지 않으므로 목표율·달성률과 무관합니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from procurement.dashboard.status_service import DataStatusService
from procurement.database.bootstrap import MVP_POLICY_SEEDS, init_db, seed_policies
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.import_batch_repository import ImportBatchRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Company, Purchase

#: 정본 정책 코드 개수(bootstrap seed 기준).
#:
#: ⚠️ 하드코딩하지 않는다 — 2026-09-03 PM 확정(§0.22 · STEP 97)으로 정책이
#: 5종에서 9종(활성 8 + 비활성 GREEN)으로 늘면서 고정값 ``5`` 가 어긋났다.
#: 정본은 seed 목록이므로 거기에서 센다.
SEED_POLICY_COUNT = len(MVP_POLICY_SEEDS)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "status.db"
    init_db(path)
    seed_policies(path)
    return path


@pytest.fixture
def service(db_path: Path) -> DataStatusService:
    return DataStatusService(
        PurchaseRepository(db_path),
        CompanyRepository(db_path),
        CertificationRepository(db_path),
        PolicyRepository(db_path),
        ImportBatchRepository(db_path),
    )


def _purchase(
    business_no: str,
    amount: str,
    contract: date,
    payment: date,
    company_id: int | None = None,
) -> Purchase:
    return Purchase(
        business_no=business_no,
        company_name="테스트업체",
        contract_date=contract,
        payment_date=payment,
        amount=Decimal(amount),
        company_id=company_id,
    )


class TestEmptyDatabase:
    """데이터가 없는 상태."""

    def test_counts_are_zero(self, service: DataStatusService) -> None:
        status = service.build_status()
        assert status.purchase_count == 0
        assert status.company_count == 0
        assert status.certification_count == 0

    def test_total_amount_is_zero(self, service: DataStatusService) -> None:
        assert service.build_status().purchase_total_amount == Decimal("0")

    def test_dates_are_none(self, service: DataStatusService) -> None:
        status = service.build_status()
        assert status.earliest_payment_date is None
        assert status.latest_payment_date is None
        assert status.earliest_contract_date is None
        assert status.latest_contract_date is None

    def test_seed_policies_are_counted(self, service: DataStatusService) -> None:
        assert service.build_status().policy_count == SEED_POLICY_COUNT

    def test_no_policy_has_target_rate(self, service: DataStatusService) -> None:
        """seed 정책은 전부 ``target_rate = NULL`` 이다(D-15)."""
        assert service.build_status().policy_with_target_rate_count == 0


class TestWithPurchases:
    """구매 데이터가 있는 상태."""

    @pytest.fixture(autouse=True)
    def _seed(self, db_path: Path) -> None:
        repo = PurchaseRepository(db_path)
        repo.insert(_purchase("1234567890", "1000", date(2026, 1, 5), date(2026, 2, 10)))
        repo.insert(_purchase("2234567890", "2500", date(2026, 3, 1), date(2026, 3, 20)))
        repo.insert(_purchase("3234567890", "500", date(2025, 12, 1), date(2026, 1, 15)))

    def test_counts_rows(self, service: DataStatusService) -> None:
        assert service.build_status().purchase_count == 3

    def test_sums_amount_exactly(self, service: DataStatusService) -> None:
        assert service.build_status().purchase_total_amount == Decimal("4000")

    def test_payment_date_range(self, service: DataStatusService) -> None:
        status = service.build_status()
        assert status.earliest_payment_date == date(2026, 1, 15)
        assert status.latest_payment_date == date(2026, 3, 20)

    def test_contract_date_range(self, service: DataStatusService) -> None:
        status = service.build_status()
        assert status.earliest_contract_date == date(2025, 12, 1)
        assert status.latest_contract_date == date(2026, 3, 1)

    def test_all_unmatched_initially(self, service: DataStatusService) -> None:
        status = service.build_status()
        assert status.matched_purchase_count == 0
        assert status.unmatched_purchase_count == 3

    def test_matched_count_reflects_company_id(
        self, service: DataStatusService, db_path: Path
    ) -> None:
        company = CompanyRepository(db_path).insert(
            Company(
                business_no="1234567890",
                company_name="테스트업체",
                representative_name="홍길동",
            )
        )
        assert company.company_id is not None
        purchases = PurchaseRepository(db_path).find_all()
        PurchaseRepository(db_path).update_company_id(
            purchases[0].purchase_id or 0, company.company_id
        )

        status = service.build_status()
        assert status.matched_purchase_count == 1
        assert status.unmatched_purchase_count == 2
        assert status.company_count == 1


class TestTargetRateCount:
    """목표율 설정 수 집계."""

    def test_counts_only_policies_with_target_rate(
        self, service: DataStatusService, db_path: Path
    ) -> None:
        PolicyRepository(db_path).update_target_rate("SMALL_BUSINESS", Decimal("50"))
        assert service.build_status().policy_with_target_rate_count == 1

    def test_unset_target_rate_is_not_counted(
        self, service: DataStatusService, db_path: Path
    ) -> None:
        repo = PolicyRepository(db_path)
        repo.update_target_rate("SMALL_BUSINESS", Decimal("50"))
        repo.update_target_rate("SMALL_BUSINESS", None)
        assert service.build_status().policy_with_target_rate_count == 0
