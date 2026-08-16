"""
procurement.calculators.rules.date_rules

기준일(지급일·계약일·결의일자)이 인증 유효기간 내에 있는지로 판정하는 규칙들.

- :class:`PaymentDateRule` — ``payment_date`` 기준 (일반 정책)
- :class:`ContractDateRule` — ``contract_date`` 기준
- :class:`ResolutionOrContractDateRule` — **결의일자 또는 계약일자 중 하나라도**
  유효기간에 들면 인정 (창업기업 — 2026-08-14 고객 확정)

앞의 두 규칙은 "기준일이 유효기간(경계 포함) 중 하나라도 만족하는가" 라는 공통
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

#: 판정 기준일 유형 — 계약일 기준
CONTRACT_DATE = "CONTRACT_DATE"

#: 판정 기준일 유형 — **결의일자 또는 계약일자 중 하나라도** 유효기간에 들면
#: 인정 (창업기업).
#:
#: 2026-08-14 고객 확정: "창업기업은 결의일자와 계약일자가 기업 인증 유효기간에
#: 해당할 경우 모두 실적으로 인정한다."
#:
#: .. note::
#:     2026-08-15 PM 결정으로 결의일자가 ``Purchase.resolution_date`` 라는 별도
#:     필드로 확정되기 전까지는 ``PAYMENT_OR_CONTRACT_DATE`` 라는 이름으로
#:     ``payment_date`` 와 ``contract_date`` 를 보았습니다. 결의일자가 확정된
#:     지금은 이름과 대상이 모두 결의일자로 바뀌었으며, 기존 DB 값은
#:     :func:`~procurement.database.bootstrap.migrate_policy_evaluation_basis`
#:     가 갱신합니다.
RESOLUTION_OR_CONTRACT_DATE = "RESOLUTION_OR_CONTRACT_DATE"


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
    """계약일(``contract_date``)을 기준으로 판정하는 규칙."""

    def basis_date(self, purchase: Purchase) -> date:
        """계약일을 기준일로 반환합니다."""
        return purchase.contract_date


class ResolutionOrContractDateRule:
    """**결의일자 또는 계약일자 중 하나라도** 인증 유효기간에 들면 인정하는 규칙.

    2026-08-14 고객 확정 규칙(창업기업)입니다.

        창업기업은 결의일자와 계약일자가 기업 인증 유효기간에 해당할 경우
        **모두** 실적으로 인정한다.

    즉 **OR 조건**입니다. 한쪽만 유효기간 안에 있어도 인정합니다.

    ==========================  ==========================  ==========
    ``resolution_date``         ``contract_date``           판정
    ==========================  ==========================  ==========
    유효기간 안                  유효기간 안                  ✅ 인정
    유효기간 안                  유효기간 밖                  ✅ 인정
    유효기간 밖                  유효기간 안                  ✅ 인정
    유효기간 밖                  유효기간 밖                  ❌ 불인정
    ==========================  ==========================  ==========

    .. warning::
        **``payment_date`` 는 보지 않습니다.** 2026-08-15 PM 결정에 따라
        결의일자는 ``resolution_date`` 라는 별도 필드이며, ``payment_date``
        (지출완료일)는 결의일자가 아닙니다.

        따라서 ``resolution_date`` 가 ``None`` 인 **기존 행**(이 필드 도입 이전에
        적재된 행)은 **계약일자만으로** 판정합니다. 값이 없는 날짜를
        ``payment_date`` 로 대체하지 않습니다 — 그렇게 하면 PM 이 명시적으로
        금지한 "``payment_date`` 를 결의일자로 재정의" 가 되기 때문입니다.

    .. note::
        :class:`DateBasisRule` 은 기준일을 **하나만** 고르는 구조라 상속하지
        않습니다. 이 규칙은 두 날짜를 함께 보므로 ``matches`` 를 직접 구현합니다.
    """

    def matches(self, context: RuleContext) -> bool:
        """두 날짜 중 하나라도 유효기간(경계 포함)에 들면 ``True``.

        ``resolution_date`` 가 없는 행은 그 날짜를 판정에서 제외합니다.
        """
        purchase = context.purchase
        bases = [purchase.contract_date]
        if purchase.resolution_date is not None:
            bases.append(purchase.resolution_date)
        return any(
            valid_from <= basis <= valid_to
            for basis in bases
            for valid_from, valid_to in context.validity_ranges
        )
