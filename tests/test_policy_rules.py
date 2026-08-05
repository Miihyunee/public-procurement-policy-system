"""
Rule Engine 단위 테스트.

정책 판정 규칙(:class:`PolicyRule`)과 레지스트리(:class:`RuleRegistry`)를
계산기와 독립적으로 검증합니다. DB 없이 순수 객체만으로 판정 로직을 확인합니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from procurement.calculators.rules import (
    CONTRACT_DATE,
    PAYMENT_DATE,
    ContractDateRule,
    DateBasisRule,
    PaymentDateRule,
    PolicyRule,
    RuleContext,
    RuleRegistry,
    build_default_registry,
)
from procurement.models.purchase import Purchase


def _purchase(
    payment_date: date = date(2026, 3, 15),
    contract_date: date = date(2026, 3, 1),
    company_id: int | None = 1,
) -> Purchase:
    """테스트용 Purchase 를 생성합니다."""
    return Purchase(
        business_no="0000000000",
        company_id=company_id,
        company_name="공급업체",
        contract_date=contract_date,
        payment_date=payment_date,
        amount=Decimal("1000000"),
    )


def _context(
    payment_date: date = date(2026, 3, 15),
    contract_date: date = date(2026, 3, 1),
    ranges: list[tuple[date, date]] | None = None,
) -> RuleContext:
    if ranges is None:
        ranges = [(date(2026, 1, 1), date(2026, 6, 30))]
    return RuleContext(
        purchase=_purchase(payment_date=payment_date, contract_date=contract_date),
        validity_ranges=ranges,
    )


class TestPaymentDateRule:
    """지급일 기준 규칙을 검증합니다."""

    def test_basis_date_is_payment_date(self) -> None:
        rule = PaymentDateRule()
        purchase = _purchase(payment_date=date(2026, 5, 5), contract_date=date(2026, 1, 1))
        assert rule.basis_date(purchase) == date(2026, 5, 5)

    def test_matches_when_payment_in_range(self) -> None:
        rule = PaymentDateRule()
        assert rule.matches(_context(payment_date=date(2026, 3, 15))) is True

    def test_not_match_when_payment_out_of_range(self) -> None:
        rule = PaymentDateRule()
        assert rule.matches(_context(payment_date=date(2026, 8, 1))) is False

    def test_ignores_contract_date(self) -> None:
        """지급일이 밖이면 계약일이 안에 있어도 인정하지 않습니다."""
        rule = PaymentDateRule()
        ctx = _context(payment_date=date(2026, 8, 1), contract_date=date(2026, 3, 1))
        assert rule.matches(ctx) is False

    def test_start_boundary_inclusive(self) -> None:
        rule = PaymentDateRule()
        ctx = _context(
            payment_date=date(2026, 1, 1), ranges=[(date(2026, 1, 1), date(2026, 6, 30))]
        )
        assert rule.matches(ctx) is True

    def test_end_boundary_inclusive(self) -> None:
        rule = PaymentDateRule()
        ctx = _context(
            payment_date=date(2026, 6, 30), ranges=[(date(2026, 1, 1), date(2026, 6, 30))]
        )
        assert rule.matches(ctx) is True

    def test_before_start_excluded(self) -> None:
        rule = PaymentDateRule()
        ctx = _context(
            payment_date=date(2025, 12, 31), ranges=[(date(2026, 1, 1), date(2026, 6, 30))]
        )
        assert rule.matches(ctx) is False

    def test_matches_when_any_range_satisfied(self) -> None:
        """여러 유효기간 중 하나라도 만족하면 인정합니다."""
        rule = PaymentDateRule()
        ctx = _context(
            payment_date=date(2026, 6, 15),
            ranges=[(date(2026, 1, 1), date(2026, 1, 31)), (date(2026, 6, 1), date(2026, 6, 30))],
        )
        assert rule.matches(ctx) is True

    def test_no_match_when_no_ranges(self) -> None:
        rule = PaymentDateRule()
        assert rule.matches(_context(ranges=[])) is False


class TestContractDateRule:
    """계약일 기준 규칙을 검증합니다."""

    def test_basis_date_is_contract_date(self) -> None:
        rule = ContractDateRule()
        purchase = _purchase(payment_date=date(2026, 8, 1), contract_date=date(2026, 2, 1))
        assert rule.basis_date(purchase) == date(2026, 2, 1)

    def test_matches_when_contract_in_range(self) -> None:
        rule = ContractDateRule()
        ctx = _context(contract_date=date(2026, 2, 1), payment_date=date(2026, 8, 1))
        assert rule.matches(ctx) is True

    def test_ignores_payment_date(self) -> None:
        """계약일이 밖이면 지급일이 안에 있어도 인정하지 않습니다."""
        rule = ContractDateRule()
        ctx = _context(contract_date=date(2025, 12, 1), payment_date=date(2026, 3, 15))
        assert rule.matches(ctx) is False


class TestPolicyRuleProtocol:
    """규칙이 PolicyRule 프로토콜을 만족하는지 확인합니다."""

    def test_rules_satisfy_protocol(self) -> None:
        assert isinstance(PaymentDateRule(), PolicyRule)
        assert isinstance(ContractDateRule(), PolicyRule)

    def test_date_rules_share_base(self) -> None:
        assert isinstance(PaymentDateRule(), DateBasisRule)
        assert isinstance(ContractDateRule(), DateBasisRule)


class TestRuleRegistry:
    """레지스트리 등록/조회를 검증합니다."""

    def test_register_and_get(self) -> None:
        registry = RuleRegistry()
        rule = PaymentDateRule()
        registry.register("CUSTOM", rule)
        assert registry.get("CUSTOM") is rule

    def test_get_unregistered_raises_without_default(self) -> None:
        registry = RuleRegistry()
        with pytest.raises(KeyError):
            registry.get("UNKNOWN")

    def test_get_unregistered_returns_default(self) -> None:
        default = PaymentDateRule()
        registry = RuleRegistry(default_rule=default)
        assert registry.get("UNKNOWN") is default

    def test_register_overwrites(self) -> None:
        registry = RuleRegistry()
        first = PaymentDateRule()
        second = PaymentDateRule()
        registry.register("K", first)
        registry.register("K", second)
        assert registry.get("K") is second


class TestBuildDefaultRegistry:
    """기본 레지스트리 구성을 검증합니다."""

    def test_payment_date_maps_to_payment_rule(self) -> None:
        registry = build_default_registry()
        assert isinstance(registry.get(PAYMENT_DATE), PaymentDateRule)

    def test_contract_date_maps_to_contract_rule(self) -> None:
        registry = build_default_registry()
        assert isinstance(registry.get(CONTRACT_DATE), ContractDateRule)

    def test_unknown_basis_falls_back_to_payment_rule(self) -> None:
        """미등록 기준값은 지급일 기준으로 처리하여 기존 동작을 보존합니다."""
        registry = build_default_registry()
        assert isinstance(registry.get("VENDOR_EXISTENCE"), PaymentDateRule)
