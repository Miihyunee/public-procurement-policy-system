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
    어느 물리 컬럼이 "결의일자" 인지는 아직 확정되지 않았습니다(W-1-1).
    따라서 규칙은 특정 날짜를 결의일자라고 단정하지 않고 **모델의 두 날짜
    필드를 모두** 확인합니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from procurement.calculators.rules import (
    CONTRACT_DATE,
    PAYMENT_DATE,
    PAYMENT_OR_CONTRACT_DATE,
    ContractDateRule,
    PaymentDateRule,
    PaymentOrContractDateRule,
    RuleContext,
    build_default_registry,
)
from procurement.database.bootstrap import MVP_POLICY_SEEDS
from procurement.database.policy_repository import ALLOWED_EVALUATION_BASIS
from procurement.models import Purchase

#: 인증 유효기간 — PM 지시서 예시와 동일
VALID = [(date(2026, 1, 1), date(2026, 12, 31))]


def _purchase(payment: date, contract: date) -> Purchase:
    """두 날짜만 다른 구매 한 건을 만듭니다."""
    return Purchase(
        business_no="1234567890",
        company_name="A기업",
        contract_date=contract,
        payment_date=payment,
        amount=Decimal("100000"),
    )


def _matches(payment: date, contract: date) -> bool:
    """OR 규칙 판정 결과를 반환합니다."""
    context = RuleContext(purchase=_purchase(payment, contract), validity_ranges=VALID)
    return PaymentOrContractDateRule().matches(context)


class TestPmExamples:
    """PM 지시서에 실린 예시를 그대로 고정합니다."""

    def test_contract_inside_payment_outside_is_accepted(self) -> None:
        """결의일자 2027-01-05(밖) · 계약일자 2026-12-20(안) → **인정**."""
        assert _matches(payment=date(2027, 1, 5), contract=date(2026, 12, 20)) is True

    def test_both_outside_is_rejected(self) -> None:
        """결의일자 2027-01-05(밖) · 계약일자 2027-01-10(밖) → **불인정**."""
        assert _matches(payment=date(2027, 1, 5), contract=date(2027, 1, 10)) is False

    def test_payment_inside_contract_outside_is_accepted(self) -> None:
        """한쪽(지급일 자리)만 안에 있어도 인정."""
        assert _matches(payment=date(2026, 6, 1), contract=date(2025, 3, 1)) is True

    def test_both_inside_is_accepted(self) -> None:
        """둘 다 안에 있으면 당연히 인정."""
        assert _matches(payment=date(2026, 6, 1), contract=date(2026, 3, 1)) is True


class TestBoundaries:
    """경계값은 기존 규칙과 동일하게 **포함**합니다."""

    @pytest.mark.parametrize("day", [date(2026, 1, 1), date(2026, 12, 31)])
    def test_boundary_days_are_included(self, day: date) -> None:
        assert _matches(payment=day, contract=date(2020, 1, 1)) is True
        assert _matches(payment=date(2020, 1, 1), contract=day) is True

    @pytest.mark.parametrize("day", [date(2025, 12, 31), date(2027, 1, 1)])
    def test_just_outside_is_rejected(self, day: date) -> None:
        assert _matches(payment=day, contract=day) is False

    def test_multiple_certifications_any_range_counts(self) -> None:
        """인증을 여러 건 보유하면 그중 하나만 만족해도 인정한다."""
        context = RuleContext(
            purchase=_purchase(date(2024, 5, 1), date(2024, 5, 1)),
            validity_ranges=[
                (date(2026, 1, 1), date(2026, 12, 31)),
                (date(2024, 1, 1), date(2024, 12, 31)),
            ],
        )
        assert PaymentOrContractDateRule().matches(context) is True

    def test_no_certification_is_rejected(self) -> None:
        context = RuleContext(
            purchase=_purchase(date(2026, 6, 1), date(2026, 6, 1)), validity_ranges=[]
        )
        assert PaymentOrContractDateRule().matches(context) is False


class TestNotEquivalentToSingleDateRules:
    """⛔ 단일 날짜 규칙으로 대체할 수 없음을 고정합니다."""

    def test_differs_from_contract_only_rule(self) -> None:
        """계약일 단독 규칙은 이 사례를 놓친다."""
        context = RuleContext(
            purchase=_purchase(payment=date(2026, 6, 1), contract=date(2027, 1, 10)),
            validity_ranges=VALID,
        )
        assert ContractDateRule().matches(context) is False
        assert PaymentOrContractDateRule().matches(context) is True

    def test_differs_from_payment_only_rule(self) -> None:
        """지급일 단독 규칙도 이 사례를 놓친다."""
        context = RuleContext(
            purchase=_purchase(payment=date(2027, 1, 5), contract=date(2026, 12, 20)),
            validity_ranges=VALID,
        )
        assert PaymentDateRule().matches(context) is False
        assert PaymentOrContractDateRule().matches(context) is True


class TestWiring:
    """규칙이 실제로 STARTUP 에 연결되어 있는지 확인합니다."""

    def test_registry_resolves_the_new_basis(self) -> None:
        rule = build_default_registry().get(PAYMENT_OR_CONTRACT_DATE)
        assert isinstance(rule, PaymentOrContractDateRule)

    def test_startup_seed_uses_the_or_rule(self) -> None:
        """STARTUP seed 가 계약일 단독이 아니라 OR 규칙을 쓴다."""
        startup = next(s for s in MVP_POLICY_SEEDS if s.policy_code == "STARTUP")
        assert startup.evaluation_basis == PAYMENT_OR_CONTRACT_DATE

    def test_other_policies_keep_payment_date(self) -> None:
        """다른 정책의 판정 기준은 바뀌지 않았다."""
        for seed in MVP_POLICY_SEEDS:
            if seed.policy_code != "STARTUP":
                assert seed.evaluation_basis == PAYMENT_DATE

    def test_new_basis_is_allowed_by_repository(self) -> None:
        assert PAYMENT_OR_CONTRACT_DATE in ALLOWED_EVALUATION_BASIS

    def test_existing_bases_still_allowed(self) -> None:
        """기존 기준값을 제거하지 않았다(다른 정책·과거 데이터 보호)."""
        assert PAYMENT_DATE in ALLOWED_EVALUATION_BASIS
        assert CONTRACT_DATE in ALLOWED_EVALUATION_BASIS
