"""
기간 조건이 계산 경로 전체에 적용되는지 검증합니다.

핵심은 **분모(전체 구매액)와 분자(정책 구매액)에 같은 기간이 적용되는지**입니다.
계산 공식 자체는 변경하지 않았으므로, 기간을 주지 않으면 기존과 동일한 결과가
나와야 합니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from procurement.calculators.procurement_achievement import ProcurementAchievementCalculator
from procurement.core.period import CONTRACT_DATE, PAYMENT_DATE, PeriodFilter
from procurement.dashboard.data_service import DashboardDataService
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Certification, Company, Purchase


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "period.db"
    init_db(path)
    seed_policies(path)
    return path


@pytest.fixture
def calculator(db_path: Path) -> ProcurementAchievementCalculator:
    return ProcurementAchievementCalculator(
        PurchaseRepository(db_path),
        CertificationRepository(db_path),
        PolicyRepository(db_path),
    )


@pytest.fixture
def seeded(db_path: Path) -> int:
    """중소기업 인증기업 1곳 + 연도별 구매를 넣고 policy_id 를 돌려준다.

    - 2025 지급: 100 (인증기업)
    - 2026 지급: 300 (인증기업)
    - 2026 지급: 700 (미인증 — 분모에만 잡힘)
    """
    companies = CompanyRepository(db_path)
    certifications = CertificationRepository(db_path)
    purchases = PurchaseRepository(db_path)
    policies = PolicyRepository(db_path)

    policy = policies.find_by_policy_code("SMALL_BUSINESS")
    assert policy is not None and policy.policy_id is not None

    company = companies.insert(
        Company(business_no="1234567890", company_name="가나상사", representative_name="홍길동")
    )
    assert company.company_id is not None
    certifications.insert(
        Certification(
            company_id=company.company_id,
            policy_id=policy.policy_id,
            valid_from=date(2020, 1, 1),
            valid_to=date(2030, 12, 31),
        )
    )

    purchases.insert(
        _purchase("1234567890", "100", date(2025, 11, 1), date(2025, 12, 1), company.company_id)
    )
    purchases.insert(
        _purchase("1234567890", "300", date(2026, 1, 5), date(2026, 2, 1), company.company_id)
    )
    purchases.insert(_purchase("9999999999", "700", date(2026, 3, 1), date(2026, 3, 5), None))
    return policy.policy_id


def _purchase(
    business_no: str,
    amount: str,
    contract: date,
    payment: date,
    company_id: int | None,
) -> Purchase:
    """합성 구매 1건.

    .. note::
        ``resolution_date`` 를 **계약일과 같은 날**로 채웁니다. 2026-08-31 고객
        확정(``DECISIONS.md`` §0.12.1)으로 일반 정책의 인증 유효기간 판정
        기준일이 결의일자가 되었기 때문입니다 — 비워 두면 이 파일이 검증하려는
        **기간 축(축 ①)** 이 아니라 결의일자 공란 때문에 0 이 나옵니다.

        ⛔ 계산을 바꾼 것이 아니라 **합성 데이터에 빠져 있던 필드를 채운
        것**입니다. 기간 조건은 그대로 ``payment_date`` · ``contract_date`` 로
        시험합니다.
    """
    return Purchase(
        business_no=business_no,
        company_name="테스트업체",
        contract_date=contract,
        payment_date=payment,
        resolution_date=contract,
        amount=Decimal(amount),
        company_id=company_id,
    )


class TestBackwardCompatibility:
    """기간을 주지 않으면 기존과 동일하게 동작한다."""

    def test_total_without_period(
        self, calculator: ProcurementAchievementCalculator, seeded: int
    ) -> None:
        assert calculator.calculate_total_purchase() == Decimal("1100")

    def test_policy_amount_without_period(
        self, calculator: ProcurementAchievementCalculator, seeded: int
    ) -> None:
        assert calculator.calculate_policy_purchase(seeded) == Decimal("400")


class TestTotalPurchaseWithPeriod:
    """분모에 기간이 적용된다."""

    def test_2026_payment_date(
        self, calculator: ProcurementAchievementCalculator, seeded: int
    ) -> None:
        period = PeriodFilter.for_year(2026, PAYMENT_DATE)
        assert calculator.calculate_total_purchase(period) == Decimal("1000")

    def test_2025_payment_date(
        self, calculator: ProcurementAchievementCalculator, seeded: int
    ) -> None:
        period = PeriodFilter.for_year(2025, PAYMENT_DATE)
        assert calculator.calculate_total_purchase(period) == Decimal("100")

    def test_empty_year_returns_zero(
        self, calculator: ProcurementAchievementCalculator, seeded: int
    ) -> None:
        period = PeriodFilter.for_year(2020, PAYMENT_DATE)
        assert calculator.calculate_total_purchase(period) == Decimal("0")

    def test_contract_date_basis_differs(
        self, calculator: ProcurementAchievementCalculator, seeded: int
    ) -> None:
        """계약일 기준이면 2025 계약 → 2025 지급 건이 2025 에 잡힌다."""
        by_payment = calculator.calculate_total_purchase(PeriodFilter.for_year(2026, PAYMENT_DATE))
        by_contract = calculator.calculate_total_purchase(
            PeriodFilter.for_year(2026, CONTRACT_DATE)
        )
        assert by_payment == Decimal("1000")
        assert by_contract == Decimal("1000")
        # 2025 는 기준일에 따라 달라진다 (같은 건이 계약 2025-11 / 지급 2025-12).
        assert calculator.calculate_total_purchase(
            PeriodFilter.for_year(2025, CONTRACT_DATE)
        ) == Decimal("100")


class TestPolicyPurchaseWithPeriod:
    """분자에도 같은 기간이 적용된다."""

    def test_2026(self, calculator: ProcurementAchievementCalculator, seeded: int) -> None:
        period = PeriodFilter.for_year(2026, PAYMENT_DATE)
        assert calculator.calculate_policy_purchase(seeded, period) == Decimal("300")

    def test_2025(self, calculator: ProcurementAchievementCalculator, seeded: int) -> None:
        period = PeriodFilter.for_year(2025, PAYMENT_DATE)
        assert calculator.calculate_policy_purchase(seeded, period) == Decimal("100")


class TestAchievementWithPeriod:
    """달성률이 같은 기간의 분모·분자로 계산된다."""

    def test_2026_achievement(
        self, calculator: ProcurementAchievementCalculator, seeded: int
    ) -> None:
        period = PeriodFilter.for_year(2026, PAYMENT_DATE)
        result = calculator.calculate_achievement(seeded, Decimal("30"), period)
        # 300 / 1000 = 30% → 목표 30% 대비 100%
        assert result.purchase_amount == Decimal("300")
        assert result.total_purchase_amount == Decimal("1000")
        assert result.achievement_rate == Decimal("100.00")

    def test_calculate_all_shares_period(
        self, calculator: ProcurementAchievementCalculator, seeded: int
    ) -> None:
        period = PeriodFilter.for_year(2026, PAYMENT_DATE)
        results = calculator.calculate_all({seeded: Decimal("30")}, period)
        assert results[0].total_purchase_amount == Decimal("1000")

    def test_denominator_and_numerator_use_same_period(
        self, calculator: ProcurementAchievementCalculator, seeded: int
    ) -> None:
        """분모만 걸리고 분자가 전체로 남는 실수를 방지한다."""
        period = PeriodFilter.for_year(2025, PAYMENT_DATE)
        result = calculator.calculate_achievement(seeded, Decimal("50"), period)
        assert result.total_purchase_amount == Decimal("100")
        assert result.purchase_amount == Decimal("100")


class TestDashboardDataServiceWithPeriod:
    """DataService 는 기간을 그대로 전달하기만 한다."""

    def test_passes_period_through(self, db_path: Path, seeded: int) -> None:
        PolicyRepository(db_path).update_target_rate("SMALL_BUSINESS", Decimal("30"))
        calculator = ProcurementAchievementCalculator(
            PurchaseRepository(db_path),
            CertificationRepository(db_path),
            PolicyRepository(db_path),
        )
        service = DashboardDataService(calculator, policy_repository=PolicyRepository(db_path))

        summary = service.build_summary_from_registered_targets(
            PeriodFilter.for_year(2026, PAYMENT_DATE)
        )
        assert summary.total_purchase_amount == Decimal("1000")

    def test_without_period_is_unchanged(self, db_path: Path, seeded: int) -> None:
        calculator = ProcurementAchievementCalculator(
            PurchaseRepository(db_path),
            CertificationRepository(db_path),
            PolicyRepository(db_path),
        )
        service = DashboardDataService(calculator, policy_repository=PolicyRepository(db_path))
        summary = service.build_summary_from_registered_targets()
        assert summary.total_purchase_amount == Decimal("1100")
