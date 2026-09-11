"""
procurement.calculators.rules.date_rules

기준일(지급일·계약일·결의일자)이 인증 유효기간 내에 있는지로 판정하는 규칙들.

- :class:`ResolutionDateRule` — ``resolution_date`` 기준
  (중소기업 · 여성기업 · 장애인기업 — 2026-08-31 고객 확정)
- :class:`PaymentDateRule` — ``payment_date`` 기준
- :class:`ContractDateRule` — ``contract_date`` 기준
- :class:`ResolutionOrContractDateRule` — **결의일자 또는 계약일자 중 하나라도**
  유효기간에 들면 인정 (창업기업 — 2026-08-14 고객 확정)

앞의 세 규칙은 "기준일이 유효기간(경계 포함) 중 하나라도 만족하는가" 라는 공통
판정을 :class:`DateBasisRule` 에서 공유하고, 어떤 날짜를 기준일로 삼을지만
각자 다르게 정의합니다.

.. warning::
    이 모듈은 **인증 유효기간 판정**(축②)만 다룹니다. **연도 귀속**(축① —
    어느 해의 실적으로 셀지)은 ``settings.PURCHASE_PERIOD_DATE_FIELD`` 와
    :mod:`procurement.core.period` 가 담당하는 **다른 축**이며, 이 모듈의
    어떤 값도 연도 귀속에 쓰이지 않습니다.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import date

from procurement.calculators.rules.base import RuleContext
from procurement.models.purchase import Purchase

#: 판정 기준일 유형 — 대금 지급일 기준 (그 외 일반 정책)
PAYMENT_DATE = "PAYMENT_DATE"

#: 판정 기준일 유형 — 계약일 기준
CONTRACT_DATE = "CONTRACT_DATE"

#: 판정 기준일 유형 — **결의일자** 기준.
#:
#: 2026-08-31 고객 최종 회신(DECISIONS §0.12.1 · §0.12.2):
#: "중소기업 — 결의일자 / 여성기업 — 결의일자 / 장애인기업 — 결의일자",
#: "인증서에 유효기간이 적혀 있고, 그 기간 안에 결의일자가 포함되어 있으면 돼."
#:
#: .. warning::
#:     이 값은 **인증 유효기간 판정 기준일**입니다. 연도 귀속 기준일이 아닙니다.
RESOLUTION_DATE = "RESOLUTION_DATE"

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


def is_within_any(basis: date, validity_ranges: Sequence[tuple[date, date | None]]) -> bool:
    """기준일이 유효기간(경계 포함) 중 하나라도 만족하면 ``True``.

    ``valid_to`` 가 ``None`` 인 구간은 **종료일이 없는 인증**이며,
    ``valid_from`` 이후이기만 하면 계속 유효합니다.

    🟢 2026-09-04 고객 확정(STEP 108): *"사회적기업과 사회적협동조합은
    종료일이 없으며 계속 유효한 것으로 판단한다."*

    .. warning::
        ⛔ 없는 종료일을 지어내지 않습니다 — 인가일 + N년, 연말,
        ``9999-12-31`` 같은 값은 전부 시스템이 만들어낸 규칙입니다.
        종료일이 **있는** 인증(여성기업·창업기업·장애인기업 등)의 판정은
        기존과 완전히 같습니다.
    """
    return any(
        valid_from <= basis and (valid_to is None or basis <= valid_to)
        for valid_from, valid_to in validity_ranges
    )


class DateBasisRule(ABC):
    """기준일이 인증 유효기간 내에 있는지로 판정하는 규칙의 공통 기반.

    유효기간 판정(경계 포함, 여러 구간 중 하나라도 만족 시 인정)은 모든
    날짜 기반 규칙이 동일하므로 여기서 공유하고, 하위 규칙은 어떤 날짜를
    기준일로 사용할지(:meth:`basis_date`)만 정의합니다.
    """

    @abstractmethod
    def basis_date(self, purchase: Purchase) -> date | None:
        """구매에서 판정 기준으로 사용할 날짜를 반환합니다.

        값이 없으면 ``None`` 입니다. ⛔ 다른 날짜로 대체하지 않습니다.
        """
        raise NotImplementedError

    def matches(self, context: RuleContext) -> bool:
        """기준일이 유효기간(경계 포함) 중 하나라도 만족하면 ``True``.

        기존 계산기의 ``_is_within_any`` 판정과 동일하게, ``valid_from <=
        기준일 <= valid_to`` 를 만족하는 구간이 하나라도 있으면 인정합니다.

        .. warning::
            **기준일이 없으면 ``False``** 입니다(🟢 2026-09-02 PM 확정 ·
            STEP 87 로 계약일·지급일이 선택 항목이 되었습니다). ⛔ 없는
            날짜를 다른 날짜로 대신하지 않습니다 — 그렇게 하면 담당자가
            확인하지 않은 판정이 실적 숫자가 됩니다.
        """
        basis = self.basis_date(context.purchase)
        if basis is None:
            return False
        return is_within_any(basis, context.validity_ranges)


class PaymentDateRule(DateBasisRule):
    """대금 지급일(``payment_date``)을 기준으로 판정하는 규칙."""

    def basis_date(self, purchase: Purchase) -> date | None:
        """대금 지급일을 기준일로 반환합니다. 값이 없으면 ``None``."""
        return purchase.payment_date


class ContractDateRule(DateBasisRule):
    """계약일(``contract_date``)을 기준으로 판정하는 규칙."""

    def basis_date(self, purchase: Purchase) -> date | None:
        """계약일을 기준일로 반환합니다. 값이 없으면 ``None``."""
        return purchase.contract_date


class ResolutionDateRule:
    """**결의일자**(``resolution_date``)가 인증 유효기간에 들면 인정하는 규칙.

    2026-08-31 고객 최종 회신(DECISIONS §0.12.1 · §0.12.2)입니다.

        중소기업 — 결의일자 / 여성기업 — 결의일자 / 장애인기업 — 결의일자

        인증서에 유효기간이 적혀 있고, 그 기간 안에 결의일자가 포함되어 있으면 돼.

    .. warning::
        **결의일자가 없는 행(``resolution_date is None``)은 인정하지 않습니다.**
        다른 날짜로 대체하지 않습니다 — 🟢 W-15 고객 확정(*"원본 데이터는
        보존하고 별도 확인 대상으로 처리"*, DECISIONS §0.12.8)에 따라 빈 값은
        빈 값으로 두고, 담당자가 확인하는 별도 목록
        (:meth:`~procurement.database.purchase_repository.PurchaseRepository.find_missing_resolution_date`)
        으로 드러냅니다. ⛔ 임의의 날짜를 넣어 계산하지 않습니다.

    .. note::
        :class:`DateBasisRule` 은 기준일이 **반드시 있는** 구조라 상속하지
        않습니다. ``resolution_date`` 는 ``None`` 일 수 있으므로 ``matches`` 를
        직접 구현합니다.
    """

    def matches(self, context: RuleContext) -> bool:
        """결의일자가 유효기간(경계 포함) 중 하나라도 만족하면 ``True``.

        결의일자가 없으면 ``False`` 입니다 — 다른 날짜로 대체하지 않습니다.
        """
        basis = context.purchase.resolution_date
        if basis is None:
            return False
        return is_within_any(basis, context.validity_ranges)


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

        **값이 있는 날짜만** 봅니다 — 없는 날짜는 판정에서 빠질 뿐,
        다른 날짜로 대체되지 않습니다. 둘 다 없으면 ``False`` 입니다.

        .. note::
            🟢 2026-09-02 PM 확정(STEP 87)으로 계약일자가 선택 항목이
            되었습니다. 고객 원본에 계약일자 컬럼이 없는 경우, 이 규칙은
            **결의일자만으로** 판정합니다. ⛔ 업무규칙(결의일자 OR
            계약일자)은 바뀌지 않았습니다 — 없는 쪽이 빠질 뿐입니다.
        """
        purchase = context.purchase
        bases = [
            basis
            for basis in (purchase.resolution_date, purchase.contract_date)
            if basis is not None
        ]
        return any(is_within_any(basis, context.validity_ranges) for basis in bases)
