"""
procurement.core.offsetting

**음수 거래와 원거래(양수)를 짝지어 상계**하는 판정 로직입니다.

2026-08-19 고객 확정(정정) 규칙 — 동일 거래 판별 조건은 **세 가지**입니다.

1. 동일 금액 (양수 금액 == 음수 금액의 절대값)
2. 동일 기업명
3. 동일 사업자등록번호

**날짜는 상계 조건이 아닙니다.** 같은 날이든 다른 날이든, 양수가 음수보다
앞이든 뒤든 판정에 영향을 주지 않습니다.

.. note::
    **이전 구현과 달라진 점** — 2026-08-14 판에는 "양수 거래가 음수 거래보다
    이전이어야 한다" 는 네 번째 조건이 있었습니다. 고객 확인 결과 이 조건은
    확정 기준이 아니었고, 실제로 담당자가 표시한 상계 126쌍 중 30쌍을 놓치는
    원인이었습니다(같은 날인데 양수가 파일 아래 행 28쌍 · 양수가 나중 날짜
    2쌍). 날짜 조건을 없애면 126쌍이 모두 재현됩니다.

.. warning::
    **기표번호 · 결의번호 · LN_SQ · GRP_NB 를 식별자로 쓰지 않습니다.**
    샘플 실측에서 음수 125건 전부 비어 있었고, 고객이 확정한 판별 기준도
    아닙니다.

.. warning::
    **이 모듈은 아직 계산에 연결되어 있지 않습니다.**

    현재 :class:`~procurement.database.purchase_repository.PurchaseRepository`
    가 ``amount <= 0`` 인 구매의 저장을 거부하므로, **음수 거래는 DB 에 존재할
    수 없습니다.** 저장 제약(D-003 · C-2 · Issue #49)이 풀리기 전에는 이 로직을
    붙여도 대상 데이터가 없습니다. 연결 시점은 PM 결정 사항입니다.

    지금 이 모듈을 두는 이유는 확정된 업무규칙을 **검증 가능한 형태로 고정**해,
    나중에 붙일 때 규칙을 다시 해석하지 않기 위함입니다.

다건 후보 — **아직 규칙이 없습니다**
------------------------------------

같은 판별 키(기업 · 사업자번호 · 금액)를 가진 양수가 음수보다 **많으면**, 그
중 어느 것을 소비할지에 따라 **남는 거래가 달라집니다**. 남는 거래의 적요 ·
예산과목이 달라지므로 이후 구매유형 분류에 영향을 줄 수 있습니다.

이 선택 기준은 고객이 아직 확정하지 않았습니다. 따라서 이 모듈은 **임의의
순서 규칙(날짜가 가까운 것 · 먼저/나중 · 파일 순서 · 적요 유사도 등)을 만들지
않고**, 해당 그룹을 :class:`AmbiguousGroup` 으로 **그대로 보고**합니다. 그룹에
속한 거래는 상계하지 않고 ``remaining`` 에 남습니다.

.. note::
    양수와 음수의 **개수가 같은** 그룹은 다건이어도 보류 대상이 아닙니다.
    어느 쪽과 짝을 짓든 그룹 전체가 소비되어 남는 거래가 동일하기 때문입니다
    (이때 :class:`OffsetPair` 의 조합은 입력 순서를 따르며, 업무적 의미는
    없습니다). 실제로 담당자가 표시한 126쌍은 전부 이 경우였습니다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from decimal import Decimal

from procurement.models.purchase import Purchase

#: 동일 거래 판별 키 — (기업명, 사업자등록번호, 절대금액).
MatchKey = tuple[str, str, Decimal]


@dataclass(frozen=True, kw_only=True)
class OffsetPair:
    """상계로 맺어진 (양수, 음수) 한 쌍.

    Attributes:
        positive: 원거래로 판단한 양수 구매.
        negative: 그 원거래를 취소하는 것으로 판단한 음수 구매.
    """

    positive: Purchase
    negative: Purchase


@dataclass(frozen=True, kw_only=True)
class AmbiguousGroup:
    """상계 후보가 여러 건이라 **어느 것을 쓸지 정할 수 없는** 그룹.

    고객이 선택 기준을 확정할 때까지 이 그룹은 상계하지 않고 보고만 합니다.

    Attributes:
        key: 동일 거래 판별 키 (기업명, 사업자등록번호, 절대금액).
        positives: 같은 키를 가진 양수 구매 목록.
        negatives: 같은 키를 가진 음수 구매 목록.
    """

    key: MatchKey
    positives: list[Purchase] = field(default_factory=list)
    negatives: list[Purchase] = field(default_factory=list)

    @property
    def company_name(self) -> str:
        """기업명."""
        return self.key[0]

    @property
    def business_no(self) -> str:
        """사업자등록번호."""
        return self.key[1]

    @property
    def amount(self) -> Decimal:
        """절대금액."""
        return self.key[2]


@dataclass(frozen=True, kw_only=True)
class OffsetResult:
    """상계 판정 결과.

    Attributes:
        remaining: 상계되지 않고 **남은** 구매 목록. 계산에 사용할 대상입니다.
            보류된 다건 그룹의 거래도 여기에 그대로 남습니다.
        pairs: 상계로 맺어진 (양수, 음수) 쌍 목록.
        unmatched_negatives: 같은 키의 양수가 **하나도 없어** 짝을 찾지 못한 음수
            구매 목록. 임의로 버리거나 상계하지 않고 **그대로 보고**합니다.
        ambiguous_groups: 후보가 여러 건이라 선택 기준이 필요한 그룹 목록.
            상계하지 않았습니다 — 고객 확인 대상입니다.
    """

    remaining: list[Purchase] = field(default_factory=list)
    pairs: list[OffsetPair] = field(default_factory=list)
    unmatched_negatives: list[Purchase] = field(default_factory=list)
    ambiguous_groups: list[AmbiguousGroup] = field(default_factory=list)


def _match_key(purchase: Purchase) -> MatchKey:
    """동일 거래 판별 키를 만듭니다.

    고객이 확정한 세 요소만 사용합니다 — **기업명 · 사업자등록번호 · 절대금액**.
    기업명은 앞뒤 공백만 정리하며, 그 밖의 정규화(띄어쓰기 제거, 법인격 표기
    통일 등)는 하지 않습니다. 확정되지 않은 규칙을 만들지 않기 위함입니다.
    """
    return (
        (purchase.company_name or "").strip(),
        (purchase.business_no or "").strip(),
        abs(purchase.amount),
    )


def offset_negative_purchases(purchases: Iterable[Purchase]) -> OffsetResult:
    """음수 거래를 동일 거래(양수)와 상계합니다.

    동일 거래 판별 조건(**모두** 만족해야 함):

    1. 기업명이 같다
    2. 사업자등록번호가 같다
    3. 양수 금액 == 음수 금액의 절대값

    **날짜는 조건이 아닙니다.** 따라서 이 함수는 날짜 필드를 읽지 않으며,
    ``resolution_date`` 등이 비어 있어도 판정할 수 있습니다.

    같은 키의 양수 개수와 음수 개수가 **다르면**(양수가 하나라도 있는 경우)
    어느 양수를 소비할지 정할 수 없으므로 **상계하지 않고**
    :attr:`OffsetResult.ambiguous_groups` 로 보고합니다. 선택 기준은 고객
    확인 대기 중이며, 임의로 만들지 않습니다.

    Args:
        purchases: 판정 대상 구매 목록.

    Returns:
        :class:`OffsetResult`. ``remaining`` 이 계산에 사용할 목록입니다.

    Examples:
        >>> # A기업 +100,000 과 A기업 -100,000 은 날짜와 무관하게 상계된다.
    """
    items = list(purchases)

    positives: dict[MatchKey, list[Purchase]] = {}
    negatives: dict[MatchKey, list[Purchase]] = {}
    for purchase in items:
        if purchase.amount > 0:
            positives.setdefault(_match_key(purchase), []).append(purchase)
        elif purchase.amount < 0:
            negatives.setdefault(_match_key(purchase), []).append(purchase)
        # 금액이 0 인 거래는 상계 대상이 아니므로 그대로 남는다.

    pairs: list[OffsetPair] = []
    unmatched: list[Purchase] = []
    ambiguous: list[AmbiguousGroup] = []
    consumed: set[int] = set()

    for key, group_negatives in negatives.items():
        group_positives = positives.get(key, [])

        if not group_positives:
            unmatched.extend(group_negatives)
            continue

        if len(group_positives) != len(group_negatives):
            # 어느 양수를 쓸지에 따라 남는 거래가 달라진다 — 기준 미확정.
            ambiguous.append(
                AmbiguousGroup(
                    key=key,
                    positives=list(group_positives),
                    negatives=list(group_negatives),
                )
            )
            continue

        # 개수가 같으므로 그룹 전체가 소비된다. 짝의 조합은 입력 순서를 따르며,
        # 어떻게 조합하든 남는 거래는 동일하다(업무규칙을 만들지 않는다).
        for positive, negative in zip(group_positives, group_negatives, strict=True):
            pairs.append(OffsetPair(positive=positive, negative=negative))
            consumed.add(id(positive))
            consumed.add(id(negative))

    remaining = [p for p in items if id(p) not in consumed]

    return OffsetResult(
        remaining=remaining,
        pairs=pairs,
        unmatched_negatives=unmatched,
        ambiguous_groups=ambiguous,
    )


def summarize(result: OffsetResult) -> str:
    """상계 결과를 한 줄로 요약합니다(로그·리포트용)."""
    ambiguous_negatives = sum(len(group.negatives) for group in result.ambiguous_groups)
    return (
        f"상계 {len(result.pairs)}쌍 · 남은 거래 {len(result.remaining)}건 · "
        f"짝 없는 음수 {len(result.unmatched_negatives)}건 · "
        f"기준 미확정 보류 {len(result.ambiguous_groups)}그룹({ambiguous_negatives}건)"
    )


def confirmed_match_fields() -> Sequence[str]:
    """고객이 확정한 동일 거래 판별 요소를 반환합니다(문서·테스트용)."""
    return ("company_name", "business_no", "abs(amount)")
