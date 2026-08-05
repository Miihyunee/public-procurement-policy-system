"""
procurement.calculators.rules.date_rules

기준일(지급일·계약일)이 인증 유효기간 내에 있는지로 판정하는 규칙들.

기존 :class:`ProcurementAchievementCalculator` 가 내부에서 수행하던 판정
로직을 그대로 옮긴 것으로, 동작은 100% 동일합니다.

- :class:`PaymentDateRule` — 대금 지급일(``payment_date``) 기준 (일반 정책)
- :class:`ContractDateRule` — 계약일(``contract_date``) 기준 (예: 창업기업)

두 규칙은 "기준일이 유효기간(경계 포함) 중 하나라도 만족하는가" 라는 공통
판정을 :class:`DateBasisRule` 에서 공유하고, 어떤 날짜를 기준일로 삼을지만
각자 다르게 정의합니다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from procurement.calculators.rules.base import RuleContext
from procurement.models.purchase import Purchase

#: 판정 기준일 유형 — 대금 지급일 기준 (그 외 일반 정책)
PAYMENT_DATE = "PAYMENT_DATE"

#: 판정 기준일 유형 — 계약일 기준 (창업기업)
CONTRACT_DATE = "CONTRACT_DATE"


class DateBasisRule(ABC):
    """기준일이 인증 유효기간 내에 있는지로 판정하는 규칙의 공통 기반.

    유효기간 판정(경계 포함, 여러 구간 중 하나라도 만족 시 인정)은 모든
    날짜 기반 규칙이 동일하므로 여기서 공유하고, 하위 규칙은 어떤 날짜를
    기준일로 사용할지(:meth:`basis_date`)만 정의합니다.
    """

    @abstractmethod
    def basis_date(self, purchase: Purchase) -> date:
        """구매에서 판정 기준으로 사용할 날짜를 반환합니다."""
        raise NotImplementedError

    def matches(self, context: RuleContext) -> bool:
        """기준일이 유효기간(경계 포함) 중 하나라도 만족하면 ``True``.

        기존 계산기의 ``_is_within_any`` 판정과 동일하게, ``valid_from <=
        기준일 <= valid_to`` 를 만족하는 구간이 하나라도 있으면 인정합니다.
        """
        basis = self.basis_date(context.purchase)
        return any(
            valid_from <= basis <= valid_to for valid_from, valid_to in context.validity_ranges
        )


class PaymentDateRule(DateBasisRule):
    """대금 지급일(``payment_date``)을 기준으로 판정하는 규칙."""

    def basis_date(self, purchase: Purchase) -> date:
        """대금 지급일을 기준일로 반환합니다."""
        return purchase.payment_date


class ContractDateRule(DateBasisRule):
    """계약일(``contract_date``)을 기준으로 판정하는 규칙 (예: 창업기업)."""

    def basis_date(self, purchase: Purchase) -> date:
        """계약일을 기준일로 반환합니다."""
        return purchase.contract_date
