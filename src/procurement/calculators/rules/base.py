"""
procurement.calculators.rules.base

정책 판정 규칙(Rule)의 핵심 계약(Contract)을 정의합니다.

Rule Engine 은 정책별 "이 구매가 해당 정책 실적으로 인정되는가?" 라는 판단을
:class:`ProcurementAchievementCalculator` 본체에서 분리하기 위한 구조입니다.
계산기는 정책 유형별 ``if/elif`` 분기 대신, 정책의 ``evaluation_basis`` 에
매핑된 :class:`PolicyRule` 을 호출합니다.

.. note::
    본 단계(Issue #18)의 규칙은 **판정 여부(bool)** 만 책임집니다.
    금액 합산은 계산기가 담당하며, 규칙이 ``True`` 를 반환한 구매의
    ``amount`` 를 그대로 더합니다. 건수(item_count)·거래유무
    (VENDOR_EXISTENCE) 등 확장 규칙은 이후 Issue 에서 도입합니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from procurement.models.purchase import Purchase


@dataclass(frozen=True)
class RuleContext:
    """규칙이 한 건의 구매를 판정하는 데 필요한 입력을 담는 값 객체.

    계산기가 정책·기업 매칭·인증 유효기간을 미리 해석한 뒤, 개별 구매 한 건과
    그 구매를 낸 기업의 인증 유효기간 목록을 규칙에 전달합니다. 규칙은 이
    컨텍스트만 보고 판정하며, Repository 나 DB 에 직접 접근하지 않습니다.

    Attributes:
        purchase: 판정 대상 구매 한 건. ``company_id`` 는 이미 인증기업으로
            매칭된 상태로 전달됩니다.
        validity_ranges: 해당 기업이 대상 정책에 대해 보유한 인증 유효기간
            ``(valid_from, valid_to)`` 목록. 같은 정책 인증을 여러 건 보유한
            경우 여러 구간이 담깁니다.
    """

    purchase: Purchase
    validity_ranges: list[tuple[date, date]]


@runtime_checkable
class PolicyRule(Protocol):
    """정책 판정 규칙의 구조적 인터페이스.

    구체 규칙(:class:`PaymentDateRule` 등)은 상속 없이도 ``matches`` 메서드만
    구현하면 이 프로토콜을 만족합니다. 구조적 타이핑(Protocol)을 사용하므로
    규칙 구현체는 특정 기반 클래스에 묶이지 않습니다.
    """

    def matches(self, context: RuleContext) -> bool:
        """주어진 구매가 이 규칙의 정책 실적으로 인정되는지 반환합니다.

        Args:
            context: 판정에 필요한 구매·유효기간 정보.

        Returns:
            정책 실적으로 인정되면 ``True``, 아니면 ``False``.
        """
        ...
