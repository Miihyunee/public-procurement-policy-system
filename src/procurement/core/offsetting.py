"""
procurement.core.offsetting

**음수 거래와 원거래(양수)를 짝지어 상계**하는 판정 로직입니다.

2026-08-14 고객 확정 규칙:

    음수 거래 이전에 기업명, 사업자번호, 금액이 똑같은 양수 금액이 있으면
    동일 거래로 판단한다.

따라서 동일 거래 판별 키는 **기업명 + 사업자등록번호 + 절대금액**이며,
추가로 **양수 거래가 음수 거래보다 이전**이어야 합니다.

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

.. note::
    상계는 **1:1** 로 맺습니다. 하나의 양수 거래가 여러 음수 거래에 중복
    매칭되지 않으며, 같은 조건의 양수가 여럿이면 **음수와 가장 가까운(가장 늦은)
    이전 거래**부터 사용합니다. 원거래에 가장 가까운 것이 취소 대상일 가능성이
    높고, 이 방식이라야 남은 거래가 결정적으로 정해집니다.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from procurement.models.purchase import Purchase


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
class OffsetResult:
    """상계 판정 결과.

    Attributes:
        remaining: 상계되지 않고 **남은** 구매 목록. 계산에 사용할 대상입니다.
        pairs: 상계로 맺어진 (양수, 음수) 쌍 목록.
        unmatched_negatives: 짝을 찾지 못한 음수 구매 목록. 임의로 버리거나
            상계하지 않고 **그대로 보고**합니다.
    """

    remaining: list[Purchase] = field(default_factory=list)
    pairs: list[OffsetPair] = field(default_factory=list)
    unmatched_negatives: list[Purchase] = field(default_factory=list)


def _match_key(purchase: Purchase) -> tuple[str, str, Decimal]:
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


def offset_negative_purchases(
    purchases: Iterable[Purchase],
    *,
    date_of: str,
) -> OffsetResult:
    """음수 거래를 이전의 동일 거래(양수)와 상계합니다.

    동일 거래 판별 조건(**모두** 만족해야 함):

    1. 기업명이 같다
    2. 사업자등록번호가 같다
    3. 양수 금액 == 음수 금액의 절대값
    4. 양수 거래가 음수 거래보다 **이전**이다

    Args:
        purchases: 판정 대상 구매 목록.
        date_of: "이전/이후" 를 가릴 때 사용할 날짜 필드 이름.
            ``"payment_date"`` 또는 ``"contract_date"``.

            **기본값을 두지 않습니다.** 어느 날짜가 결의일자인지 확정되지
            않았으므로(W-1-1) 호출자가 명시해야 합니다.

    Returns:
        :class:`OffsetResult`. ``remaining`` 이 계산에 사용할 목록입니다.

    Raises:
        ValueError: ``date_of`` 가 허용되지 않는 필드명인 경우.

    Examples:
        >>> # A기업 +100,000 (3/1) 과 A기업 -100,000 (3/10) 은 상계되어 둘 다 빠진다.
    """
    if date_of not in ("payment_date", "contract_date"):
        raise ValueError(
            f"date_of 는 'payment_date' 또는 'contract_date' 여야 합니다: {date_of!r}"
        )

    items = list(purchases)

    def sort_key(purchase: Purchase) -> tuple[date, int]:
        return (getattr(purchase, date_of), purchase.purchase_id or 0)

    positives = sorted((p for p in items if p.amount > 0), key=sort_key)
    negatives = sorted((p for p in items if p.amount < 0), key=sort_key)

    # 키별 양수 후보를 시간순으로 모아 두고, 짝을 지을 때마다 소비한다.
    available: dict[tuple[str, str, Decimal], list[Purchase]] = {}
    for positive in positives:
        available.setdefault(_match_key(positive), []).append(positive)

    pairs: list[OffsetPair] = []
    unmatched: list[Purchase] = []
    consumed: set[int] = set()

    for negative in negatives:
        candidates = available.get(_match_key(negative), [])
        partner = _take_latest_before(candidates, negative, sort_key)
        if partner is None:
            unmatched.append(negative)
            continue
        pairs.append(OffsetPair(positive=partner, negative=negative))
        consumed.add(id(partner))
        consumed.add(id(negative))

    # 금액이 0 인 거래는 상계 대상이 아니므로 그대로 남는다.
    remaining = [p for p in items if id(p) not in consumed]

    return OffsetResult(remaining=remaining, pairs=pairs, unmatched_negatives=unmatched)


def _take_latest_before(
    candidates: list[Purchase],
    negative: Purchase,
    sort_key: Callable[[Purchase], tuple[date, int]],
) -> Purchase | None:
    """음수 거래보다 **이전**인 후보 중 가장 늦은 것을 꺼냅니다(소비).

    같은 조건의 양수가 여럿이면 음수에 가장 가까운 것을 씁니다. 하나의 양수가
    여러 음수에 중복 매칭되지 않도록 꺼낸 항목은 목록에서 제거합니다.

    Args:
        candidates: 같은 판별 키를 가진 양수 후보(시간순 정렬).
        negative: 짝을 찾는 음수 구매.
        sort_key: ``(날짜, purchase_id)`` 를 만드는 함수.

    Returns:
        짝이 될 양수 구매. 없으면 ``None``.
    """
    target = sort_key(negative)

    for index in range(len(candidates) - 1, -1, -1):
        if sort_key(candidates[index]) < target:
            return candidates.pop(index)
    return None


def summarize(result: OffsetResult) -> str:
    """상계 결과를 한 줄로 요약합니다(로그·리포트용)."""
    return (
        f"상계 {len(result.pairs)}쌍 · 남은 거래 {len(result.remaining)}건 · "
        f"짝 없는 음수 {len(result.unmatched_negatives)}건"
    )


def confirmed_match_fields() -> Sequence[str]:
    """고객이 확정한 동일 거래 판별 요소를 반환합니다(문서·테스트용)."""
    return ("company_name", "business_no", "abs(amount)", "이전 거래")
