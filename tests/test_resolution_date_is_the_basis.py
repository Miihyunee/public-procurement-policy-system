"""
STEP 86 — **결의일자가 기준일**이라는 확정을 코드에 못 박습니다.

🟢 2026-09-02 PM 확정:

    실적 산정 및 연도 귀속의 기준일은 원본파일의 **결의일자**이다.
    신고기준일 · 계약일자 · 지급일자는 실적 산정 기준일로 사용하지 않는다.

이 파일이 지키는 것
===================

1. **연도 귀속**(축 ①)이 결의일자로 나뉜다 — 신고기준일이 다른 해여도.
2. **인증 유효기간 판정**(축 ②)이 결의일자로 이루어진다.
3. **신고기준일이 계산 어디에도 닿지 않는다** — 구조적 사실로 확인.
4. 🟢 창업기업의 *결의일자 **또는** 계약일자* 규칙은 **그대로**다.

.. warning::
    ⛔ 이 파일은 계약일자·지급일자를 새로 요구하거나 다른 날짜로 대체하지
    않습니다. 합성 데이터에 값을 넣는 것은 **저장 모델이 요구하기 때문**이며,
    그 값이 판정에 쓰이지 않는다는 것이 여기서 확인하려는 바입니다.

.. note::
    합성 데이터만 씁니다.
"""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from procurement.calculators import procurement_achievement
from procurement.calculators.procurement_achievement import ProcurementAchievementCalculator
from procurement.calculators.rules import (
    RESOLUTION_DATE as RULE_RESOLUTION_DATE,
)
from procurement.calculators.rules import (
    ResolutionDateRule,
    ResolutionOrContractDateRule,
    build_default_registry,
)
from procurement.core import period as period_module
from procurement.core.config.settings import Settings
from procurement.core.period import ALLOWED_DATE_FIELDS, RESOLUTION_DATE, PeriodFilter
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Certification, Company, Purchase

#: 합성 사업자등록번호 — 실제 업체의 번호가 아닙니다.
_CERTIFIED = "1000000001"

#: 인증 유효기간.
_VALID_FROM = date(2026, 1, 1)
_VALID_TO = date(2026, 12, 31)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "basis.db"
    init_db(path)
    seed_policies(path)
    return path


@pytest.fixture
def calculator(db: Path) -> ProcurementAchievementCalculator:
    return ProcurementAchievementCalculator(
        PurchaseRepository(db), CertificationRepository(db), PolicyRepository(db)
    )


def _policy_id(db: Path, code: str) -> int:
    policy = PolicyRepository(db).find_by_policy_code(code)
    assert policy is not None and policy.policy_id is not None
    return policy.policy_id


def _certified_company(db: Path, code: str = "SMALL_BUSINESS") -> int:
    companies = CompanyRepository(db)
    company = companies.find_by_business_no(_CERTIFIED)
    if company is None:
        company = companies.insert(
            Company(
                business_no=_CERTIFIED, company_name="합성기업 가", representative_name="홍길동"
            )
        )
    assert company.company_id is not None
    CertificationRepository(db).insert(
        Certification(
            company_id=company.company_id,
            policy_id=_policy_id(db, code),
            valid_from=_VALID_FROM,
            valid_to=_VALID_TO,
        )
    )
    return company.company_id


def _add(
    db: Path,
    amount: str,
    *,
    resolution: date,
    issue: date,
    company_id: int | None = None,
) -> None:
    """합성 구매 1건.

    ``contract_date`` · ``payment_date`` 는 저장 모델이 요구하므로 채우되,
    **결의일자와 다른 해**로 둡니다. 판정이 그 값을 보면 결과가 달라지므로,
    결과가 달라지지 않는다는 것이 곧 "보지 않는다" 는 증거가 됩니다.
    """
    PurchaseRepository(db).insert(
        Purchase(
            business_no=_CERTIFIED,
            company_name="합성기업 가",
            contract_date=date(2024, 7, 1),
            payment_date=date(2024, 8, 1),
            resolution_date=resolution,
            issue_date=issue,
            amount=Decimal(amount),
            company_id=company_id,
            description="합성 적요",
        )
    )


# ======================================================================
# 1. 연도 귀속 (축 ①)
# ======================================================================
class TestTheYearComesFromTheResolutionDate:
    """지시서 §3 — 결의일자의 연도가 곧 실적 연도다."""

    def test_the_setting_is_fixed_to_the_resolution_date(self) -> None:
        assert Settings().PURCHASE_PERIOD_DATE_FIELD == "resolution_date"

    @pytest.mark.parametrize(
        ("resolution", "year", "expected"),
        [
            (date(2026, 1, 15), 2026, "1000"),
            (date(2026, 1, 15), 2025, "0"),
            (date(2025, 12, 29), 2025, "1000"),
            (date(2025, 12, 29), 2026, "0"),
        ],
    )
    def test_the_row_lands_in_the_resolution_year(
        self,
        db: Path,
        calculator: ProcurementAchievementCalculator,
        resolution: date,
        year: int,
        expected: str,
    ) -> None:
        """⭐ **신고기준일이 다른 해여도** 결의일자의 해로 잡힌다.

        아래 데이터는 실데이터에서 실제로 관찰된 형태입니다 — 12월 말 결의,
        이듬해 1월 초 신고(`REAL_DATA_FINAL_VALIDATION.md` §4).
        """
        _add(db, "1000", resolution=resolution, issue=date(2026, 1, 5))
        period = PeriodFilter.for_year(year, RESOLUTION_DATE)
        assert calculator.calculate_total_purchase(period) == Decimal(expected)

    def test_the_issue_year_does_not_move_the_row(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        """신고기준일만 바꿔도 귀속 연도가 움직이지 않는다."""
        _add(db, "300", resolution=date(2026, 3, 1), issue=date(2025, 12, 31))
        _add(db, "700", resolution=date(2026, 3, 2), issue=date(2027, 1, 2))
        period = PeriodFilter.for_year(2026, RESOLUTION_DATE)
        assert calculator.calculate_total_purchase(period) == Decimal("1000")

    def test_the_numerator_uses_the_same_axis(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        """분모와 분자가 **같은 축**으로 잘린다."""
        company_id = _certified_company(db)
        _add(db, "400", resolution=date(2026, 5, 1), issue=date(2027, 1, 2), company_id=company_id)
        _add(db, "600", resolution=date(2025, 5, 1), issue=date(2026, 1, 2), company_id=company_id)

        period = PeriodFilter.for_year(2026, RESOLUTION_DATE)
        assert calculator.calculate_total_purchase(period) == Decimal("400")
        assert calculator.calculate_policy_purchase(
            _policy_id(db, "SMALL_BUSINESS"), period
        ) == Decimal("400")


# ======================================================================
# 2. 인증 유효기간 판정 (축 ②)
# ======================================================================
class TestTheCertificationCheckUsesTheResolutionDate:
    """지시서 §4 — 인증 유효기간과 대조하는 날짜는 결의일자다."""

    @pytest.mark.parametrize("code", ["SMALL_BUSINESS", "WOMAN", "DISABLED"])
    def test_the_general_policies_are_wired_to_the_resolution_rule(
        self, db: Path, code: str
    ) -> None:
        policy = PolicyRepository(db).find_by_policy_code(code)
        assert policy is not None
        assert policy.evaluation_basis == RULE_RESOLUTION_DATE
        assert isinstance(build_default_registry().get(policy.evaluation_basis), ResolutionDateRule)

    def test_inside_the_validity_period_counts(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        company_id = _certified_company(db)
        _add(db, "1000", resolution=_VALID_FROM, issue=date(2027, 1, 2), company_id=company_id)
        assert calculator.calculate_policy_purchase(_policy_id(db, "SMALL_BUSINESS")) == Decimal(
            "1000"
        )

    def test_outside_the_validity_period_does_not(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        company_id = _certified_company(db)
        # 신고기준일은 유효기간 **안**이지만 결의일자는 밖이다.
        _add(db, "1000", resolution=date(2027, 1, 5), issue=_VALID_TO, company_id=company_id)
        assert calculator.calculate_policy_purchase(_policy_id(db, "SMALL_BUSINESS")) == Decimal(
            "0"
        )

    def test_the_startup_rule_is_unchanged(self, db: Path) -> None:
        """🟢 창업기업은 결의일자 **또는** 계약일자 그대로."""
        policy = PolicyRepository(db).find_by_policy_code("STARTUP")
        assert policy is not None
        assert policy.evaluation_basis == "RESOLUTION_OR_CONTRACT_DATE"
        assert isinstance(
            build_default_registry().get(policy.evaluation_basis), ResolutionOrContractDateRule
        )


# ======================================================================
# 3. 신고기준일은 계산에 닿지 않는다 (지시서 §2)
# ======================================================================
class TestTheIssueDateNeverDecides:
    """⛔ 구조적으로 **닿을 수 없다**는 것을 확인한다."""

    def test_the_issue_date_is_not_a_period_axis(self) -> None:
        assert "issue_date" not in ALLOWED_DATE_FIELDS
        assert ALLOWED_DATE_FIELDS == frozenset(
            {"payment_date", "contract_date", "resolution_date"}
        )

    def test_the_period_module_never_mentions_the_issue_date(self) -> None:
        assert "issue_date" not in inspect.getsource(period_module)

    def test_the_calculator_never_mentions_the_issue_date(self) -> None:
        """⭐ 계산기 본문에 낱말 자체가 없으면 실수로도 쓸 수 없다."""
        assert "issue_date" not in inspect.getsource(procurement_achievement)

    def test_no_rule_reads_the_issue_date(self) -> None:
        """판정 규칙 어느 것도 신고기준일을 보지 않는다."""
        from procurement.calculators.rules import date_rules

        assert "issue_date" not in inspect.getsource(date_rules)

    def test_the_period_filter_rejects_the_issue_date(self) -> None:
        """⛔ 설정으로 우회해 넣으려 해도 거부된다."""
        from procurement.core.period import PeriodValidationError

        with pytest.raises(PeriodValidationError):
            PeriodFilter(start=_VALID_FROM, end=_VALID_TO, date_field="issue_date")


# ======================================================================
# 4. 계약일자·지급일자는 실적 기준일이 아니다 (지시서 §1 · §8)
# ======================================================================
class TestTheContractAndPaymentDatesAreNotTheBasis:
    """⛔ 이번 확정으로 두 날짜는 실적 기준일에서 빠졌다."""

    @pytest.mark.parametrize("code", ["SMALL_BUSINESS", "WOMAN", "DISABLED"])
    def test_no_general_policy_uses_them(self, db: Path, code: str) -> None:
        policy = PolicyRepository(db).find_by_policy_code(code)
        assert policy is not None
        assert policy.evaluation_basis not in ("PAYMENT_DATE", "CONTRACT_DATE")

    def test_they_are_still_stored(self, db: Path) -> None:
        """⛔ 값을 버리지 않는다 — 저장은 그대로다(창업기업이 계약일자를 쓴다)."""
        _add(db, "100", resolution=date(2026, 3, 1), issue=date(2026, 3, 1))
        stored = PurchaseRepository(db).find_all()[0]
        assert stored.contract_date == date(2024, 7, 1)
        assert stored.payment_date == date(2024, 8, 1)

    def test_a_wildly_different_pair_changes_nothing(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        """⭐ 계약일·지급일이 2024년이어도 2026년 실적으로 잡힌다."""
        company_id = _certified_company(db)
        _add(db, "500", resolution=date(2026, 6, 1), issue=date(2026, 6, 2), company_id=company_id)
        period = PeriodFilter.for_year(2026, RESOLUTION_DATE)
        assert calculator.calculate_total_purchase(period) == Decimal("500")
        assert calculator.calculate_policy_purchase(
            _policy_id(db, "SMALL_BUSINESS"), period
        ) == Decimal("500")
