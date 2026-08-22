"""
tests.test_startup_either_date_rule

창업기업(STARTUP) 판정 규칙 검증 — **2026-08-14 고객 확정**.

    창업기업은 결의일자와 계약일자가 기업 인증 유효기간에 해당할 경우
    **모두** 실적으로 인정한다.

즉 두 날짜에 대한 **OR 조건**입니다. 한쪽만 유효기간에 들어도 인정합니다.

이 파일은 PM 지시서에 실린 예시를 그대로 고정합니다. 나중에 누군가
``STARTUP = CONTRACT_DATE`` 나 ``STARTUP = 결의일자`` 단독으로 되돌리면
반드시 깨집니다.

.. note::
    **2026-08-15 PM 결정 반영.** 결의일자는 ``Purchase.resolution_date`` 라는
    **별도 필드**로 확정되었습니다(``payment_date`` 를 결의일자로 재정의하지
    않는다). 그전까지 이 규칙은 어느 컬럼이 결의일자인지 확정되지 않아
    ``payment_date`` 와 ``contract_date`` 를 함께 보았으나, 이제는
    ``resolution_date`` 와 ``contract_date`` 를 봅니다.

    **업무규칙(결의일자 OR 계약일자)은 바뀌지 않았고, 결의일자가 담기는 물리
    필드만 확정되었습니다.**
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from procurement.calculators.rules import (
    CONTRACT_DATE,
    PAYMENT_DATE,
    RESOLUTION_OR_CONTRACT_DATE,
    ContractDateRule,
    PaymentDateRule,
    ResolutionOrContractDateRule,
    RuleContext,
    build_default_registry,
)
from procurement.database.bootstrap import MVP_POLICY_SEEDS
from procurement.database.policy_repository import ALLOWED_EVALUATION_BASIS
from procurement.models import Purchase

#: 인증 유효기간 — PM 지시서 예시와 동일
VALID = [(date(2026, 1, 1), date(2026, 12, 31))]

#: 판정에 쓰이지 않는 날짜. 지급일이 판정에 섞이면 이 값 때문에 결과가 달라진다.
IRRELEVANT_PAYMENT_DATE = date(2026, 6, 15)


def _purchase(resolution: date | None, contract: date) -> Purchase:
    """결의일자·계약일자만 다른 구매 한 건을 만듭니다."""
    return Purchase(
        business_no="1234567890",
        company_name="A기업",
        contract_date=contract,
        payment_date=IRRELEVANT_PAYMENT_DATE,
        resolution_date=resolution,
        amount=Decimal("100000"),
    )


def _matches(resolution: date | None, contract: date) -> bool:
    """OR 규칙 판정 결과를 반환합니다."""
    context = RuleContext(purchase=_purchase(resolution, contract), validity_ranges=VALID)
    return ResolutionOrContractDateRule().matches(context)


class TestPmExamples:
    """PM 지시서에 실린 예시를 그대로 고정합니다."""

    def test_contract_inside_resolution_outside_is_accepted(self) -> None:
        """결의일자 2027-01-05(밖) · 계약일자 2026-12-20(안) → **인정**."""
        assert _matches(resolution=date(2027, 1, 5), contract=date(2026, 12, 20)) is True

    def test_both_outside_is_rejected(self) -> None:
        """결의일자 2027-01-05(밖) · 계약일자 2027-01-10(밖) → **불인정**."""
        assert _matches(resolution=date(2027, 1, 5), contract=date(2027, 1, 10)) is False

    def test_resolution_inside_contract_outside_is_accepted(self) -> None:
        """결의일자만 안에 있어도 인정."""
        assert _matches(resolution=date(2026, 6, 1), contract=date(2025, 3, 1)) is True

    def test_both_inside_is_accepted(self) -> None:
        """둘 다 안에 있으면 당연히 인정."""
        assert _matches(resolution=date(2026, 6, 1), contract=date(2026, 3, 1)) is True


class TestPaymentDateIsNotUsed:
    """⛔ ``payment_date`` 는 판정에 쓰이지 않는다 (2026-08-15 PM 결정)."""

    def test_payment_date_inside_does_not_rescue_the_row(self) -> None:
        """지급일이 유효기간 안이어도, 두 기준일이 밖이면 **불인정**이다.

        지급일을 결의일자처럼 쓰던 이전 동작이라면 이 사례가 인정되었을 것이다.
        """
        purchase = _purchase(resolution=date(2027, 1, 5), contract=date(2027, 1, 10))
        assert VALID[0][0] <= purchase.payment_date <= VALID[0][1]  # 지급일은 기간 안
        context = RuleContext(purchase=purchase, validity_ranges=VALID)

        assert ResolutionOrContractDateRule().matches(context) is False

    def test_legacy_row_without_resolution_date_uses_contract_only(self) -> None:
        """``resolution_date`` 가 없는 기존 행은 **계약일자만으로** 판정한다.

        값이 없는 날짜를 ``payment_date`` 로 대체하지 않는다(PM 금지 사항).
        """
        assert _matches(resolution=None, contract=date(2026, 3, 1)) is True
        assert _matches(resolution=None, contract=date(2027, 1, 10)) is False


class TestBoundaries:
    """경계값은 기존 규칙과 동일하게 **포함**합니다."""

    @pytest.mark.parametrize("day", [date(2026, 1, 1), date(2026, 12, 31)])
    def test_boundary_days_are_included(self, day: date) -> None:
        assert _matches(resolution=day, contract=date(2020, 1, 1)) is True
        assert _matches(resolution=date(2020, 1, 1), contract=day) is True

    @pytest.mark.parametrize("day", [date(2025, 12, 31), date(2027, 1, 1)])
    def test_just_outside_is_rejected(self, day: date) -> None:
        assert _matches(resolution=day, contract=day) is False

    def test_multiple_certifications_any_range_counts(self) -> None:
        """인증을 여러 건 보유하면 그중 하나만 만족해도 인정한다."""
        context = RuleContext(
            purchase=_purchase(date(2024, 5, 1), date(2024, 5, 1)),
            validity_ranges=[
                (date(2026, 1, 1), date(2026, 12, 31)),
                (date(2024, 1, 1), date(2024, 12, 31)),
            ],
        )
        assert ResolutionOrContractDateRule().matches(context) is True

    def test_no_certification_is_rejected(self) -> None:
        context = RuleContext(
            purchase=_purchase(date(2026, 6, 1), date(2026, 6, 1)), validity_ranges=[]
        )
        assert ResolutionOrContractDateRule().matches(context) is False


class TestNotEquivalentToSingleDateRules:
    """⛔ 단일 날짜 규칙으로 대체할 수 없음을 고정합니다."""

    def test_differs_from_contract_only_rule(self) -> None:
        """계약일 단독 규칙은 이 사례를 놓친다."""
        context = RuleContext(
            purchase=_purchase(resolution=date(2026, 6, 1), contract=date(2027, 1, 10)),
            validity_ranges=VALID,
        )
        assert ContractDateRule().matches(context) is False
        assert ResolutionOrContractDateRule().matches(context) is True

    def test_differs_from_payment_only_rule(self) -> None:
        """지급일 단독 규칙과도 결과가 다르다."""
        purchase = _purchase(resolution=date(2027, 1, 5), contract=date(2027, 1, 10))
        context = RuleContext(purchase=purchase, validity_ranges=VALID)

        assert PaymentDateRule().matches(context) is True
        assert ResolutionOrContractDateRule().matches(context) is False


class TestWiring:
    """규칙이 실제로 STARTUP 에 연결되어 있는지 확인합니다."""

    def test_registry_resolves_the_new_basis(self) -> None:
        rule = build_default_registry().get(RESOLUTION_OR_CONTRACT_DATE)
        assert isinstance(rule, ResolutionOrContractDateRule)

    def test_startup_seed_uses_the_or_rule(self) -> None:
        """STARTUP seed 가 계약일 단독이 아니라 OR 규칙을 쓴다."""
        startup = next(s for s in MVP_POLICY_SEEDS if s.policy_code == "STARTUP")
        assert startup.evaluation_basis == RESOLUTION_OR_CONTRACT_DATE

    def test_other_policies_keep_payment_date(self) -> None:
        """다른 정책의 판정 기준은 바뀌지 않았다."""
        for seed in MVP_POLICY_SEEDS:
            if seed.policy_code != "STARTUP":
                assert seed.evaluation_basis == PAYMENT_DATE

    def test_new_basis_is_allowed_by_repository(self) -> None:
        assert RESOLUTION_OR_CONTRACT_DATE in ALLOWED_EVALUATION_BASIS

    def test_existing_bases_still_allowed(self) -> None:
        """기존 기준값을 제거하지 않았다(다른 정책·과거 데이터 보호)."""
        assert PAYMENT_DATE in ALLOWED_EVALUATION_BASIS
        assert CONTRACT_DATE in ALLOWED_EVALUATION_BASIS
