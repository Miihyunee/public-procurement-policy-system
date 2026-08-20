"""
procurement.core.offsetting

**음수 거래와 원거래(양수)를 짝지어 상계**하는 판정 로직입니다.

2026-08-20 고객 확정 업무규칙 (`DECISIONS.md` §0.6.3.4)::

    ① 동일 기업 + ② 동일 사업자등록번호 + ③ 동일 금액(절대값)  →  후보 확인
          ↓  후보가 여러 건이면
    ④ 적요 확인   ⑤ 예산과목 공란 여부 확인   ⑥ 세금계산서 발행일자 확인
          ↓
    ⑦ 발행일자가 가장 가까운 (+)/(−) 를 1:1 매칭
          ↓  발행일자 차이가 동률이면
    ⑧ 담당자가 G20 에서 지출결의서 조회 가능 여부를 확인해 판단

이 모듈이 자동으로 하는 것은 **①②③ 과 ⑦** 입니다.

.. warning::
    **⑧ 은 시스템이 판정하지 않습니다.** G20 은 계정이 필요한 외부 프로그램이며
    우리 시스템은 로그인·조회·자동화를 하지 않습니다. 따라서 발행일자 차이가
    동률인 건은 **임의로 고르지 않고** :class:`NeedsManualReviewGroup` 으로
    남겨 담당자가 판단하게 합니다.

.. warning::
    ⛔ **④⑤ 를 판정 조건으로 쓰지 않습니다.**

    ==================== ==========================================
    만들지 않는 규칙      근거
    ==================== ==========================================
    적요가 같아야 상계    고객: "99.5% 동일하지만 100%는 아니다".
                          실측에서 적요 완전일치를 필수로 걸면 담당자가
                          상계한 22행을 놓친다.
    예산과목 공란 = 상계  고객: "공란이라고 무조건 삭제할 수 없다".
                          실측 음수 129건 중 128건이 공란이라 판별력이 없다.
    ==================== ==========================================

    두 값은 :class:`~procurement.models.purchase.Purchase` 에 그대로 보관되어
    담당자가 눈으로 확인할 수 있게만 합니다.

.. warning::
    ⛔ **"양수가 음수보다 먼저" 를 필터로 쓰지 않습니다.**

    고객은 업무 흐름상 (+) 발행 → (−) 발행이 일반적이라고 설명했지만, 이는
    **경향이지 불변식이 아닙니다.** 담당자가 상계 표시한 126쌍 중 2쌍은 (+) 가
    나중 발행이었고(후보가 하나뿐인 1:1 그룹), 조건으로 넣으면 이를 놓칩니다.
    판정은 **선후 방향이 아니라 차이의 절대값**으로 합니다.

.. warning::
    **기표번호 · 결의번호 · LN_SQ · GRP_NB 를 식별자로 쓰지 않습니다.**
    샘플 실측에서 음수 125건 전부 비어 있었고, 고객이 확정한 판별 기준도
    아닙니다.

.. warning::
    **이 모듈은 아직 계산에 연결되어 있지 않습니다.**

    현재 :class:`~procurement.database.purchase_repository.PurchaseRepository`
    가 ``amount <= 0`` 인 구매의 저장을 거부하므로, **음수 거래는 DB 에 존재할
    수 없습니다**(D-003 · C-2 · Issue #49). 계산 경로 연결은 별도 PM 승인
    사항입니다(§0.6.3.3 D 단계).
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
        distance_days: 두 거래의 세금계산서 발행일자 차이(일). 항상 0 이상입니다.
    """

    positive: Purchase
    negative: Purchase
    distance_days: int


@dataclass(frozen=True, kw_only=True)
class NeedsManualReviewGroup:
    """발행일자 차이가 **동률**이라 시스템이 정할 수 없는 건.

    고객 업무규칙상 이때는 담당자가 **G20 에서 지출결의서 조회 가능 여부**를
    확인해 판단합니다. 우리 시스템은 G20 에 접근하지 않으므로 자동으로 고르지
    않고 그대로 넘깁니다.

    Attributes:
        key: 동일 거래 판별 키 (기업명, 사업자등록번호, 절대금액).
        negative: 짝을 정하지 못한 음수 구매.
        candidates: 발행일자 차이가 **똑같이 가장 가까운** 양수 후보들.
        distance_days: 그 최소 차이(일).
    """

    key: MatchKey
    negative: Purchase
    candidates: list[Purchase] = field(default_factory=list)
    distance_days: int = 0

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
            담당자 확인 대상과 발행일자 결측 건도 여기에 그대로 남습니다.
        pairs: 상계로 맺어진 (양수, 음수) 쌍 목록.
        unmatched_negatives: 같은 키의 양수가 **없어** 짝을 찾지 못한 음수.
            임의로 버리거나 상계하지 않고 **그대로 보고**합니다.
        needs_manual_review: 발행일자 차이가 동률이라 **담당자 확인이 필요한**
            건. 상계하지 않았습니다.
        missing_issue_date: 세금계산서 발행일자가 없어 판정할 수 없는 구매.
            ⛔ 다른 날짜로 대체하지 않고 그대로 보고합니다.
    """

    remaining: list[Purchase] = field(default_factory=list)
    pairs: list[OffsetPair] = field(default_factory=list)
    unmatched_negatives: list[Purchase] = field(default_factory=list)
    needs_manual_review: list[NeedsManualReviewGroup] = field(default_factory=list)
    missing_issue_date: list[Purchase] = field(default_factory=list)


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


def _distance_days(positive: Purchase, negative: Purchase) -> int:
    """두 거래의 세금계산서 발행일자 차이(일)를 반환합니다.

    **선후 방향은 보지 않습니다.** 절대값만 씁니다.
    """
    assert positive.issue_date is not None
    assert negative.issue_date is not None
    return abs((positive.issue_date - negative.issue_date).days)


def offset_negative_purchases(purchases: Iterable[Purchase]) -> OffsetResult:
    """음수 거래를 동일 거래(양수)와 상계합니다.

    동일 거래 후보 조건(**모두** 만족):

    1. 기업명이 같다
    2. 사업자등록번호가 같다
    3. 양수 금액 == 음수 금액의 절대값

    후보 중에서는 **세금계산서 발행일자(``issue_date``) 차이가 가장 작은** 건을
    1:1 로 맺습니다. 최소 차이인 후보가 **둘 이상이면 상계하지 않고**
    :attr:`OffsetResult.needs_manual_review` 로 보고합니다(임의 선택 금지).

    Args:
        purchases: 판정 대상 구매 목록.

    Returns:
        :class:`OffsetResult`. ``remaining`` 이 계산에 사용할 목록입니다.

    Examples:
        >>> # +1/10 · +1/15 · −1/20 이면 1/15 쪽과 상계된다(5일 차이).
        >>> # +1/15 · −1/20 · +1/25 이면 둘 다 5일이라 담당자 확인 대상이 된다.
    """
    items = list(purchases)

    positives: dict[MatchKey, list[Purchase]] = {}
    negatives: dict[MatchKey, list[Purchase]] = {}
    missing: list[Purchase] = []

    for purchase in items:
        if purchase.amount == 0:
            # 금액이 0 인 거래는 상계 대상이 아니므로 그대로 남는다.
            continue
        if purchase.issue_date is None:
            # ⛔ 없는 발행일자를 결의일자·계약일자·지급일로 대체하지 않는다.
            missing.append(purchase)
            continue
        bucket = positives if purchase.amount > 0 else negatives
        bucket.setdefault(_match_key(purchase), []).append(purchase)

    pairs: list[OffsetPair] = []
    unmatched: list[Purchase] = []
    review: list[NeedsManualReviewGroup] = []

    for key, group_negatives in negatives.items():
        group_pairs, group_review, group_unmatched = _match_group(
            key, positives.get(key, []), group_negatives
        )
        pairs.extend(group_pairs)
        review.extend(group_review)
        unmatched.extend(group_unmatched)

    consumed = {id(pair.positive) for pair in pairs} | {id(pair.negative) for pair in pairs}
    remaining = [purchase for purchase in items if id(purchase) not in consumed]

    return OffsetResult(
        remaining=remaining,
        pairs=pairs,
        unmatched_negatives=unmatched,
        needs_manual_review=review,
        missing_issue_date=missing,
    )


def _match_group(
    key: MatchKey,
    positives: Sequence[Purchase],
    negatives: Sequence[Purchase],
) -> tuple[list[OffsetPair], list[NeedsManualReviewGroup], list[Purchase]]:
    """같은 판별 키를 가진 한 그룹 안에서 짝을 맺습니다.

    **가장 가까운 것부터** 확정합니다. 남은 음수 중 최소 거리가 가장 작은 건을
    먼저 처리하므로, 처리 순서가 입력 순서에 좌우되지 않습니다.

    최소 거리 후보가 둘 이상인 음수는 **짝을 맺지 않고 빼 둡니다.** 그 음수가
    쓸 뻔한 양수는 다른 음수가 쓸 수 있도록 남겨 둡니다.

    Args:
        key: 그룹의 판별 키.
        positives: 같은 키의 양수 구매.
        negatives: 같은 키의 음수 구매.

    Returns:
        ``(맺어진 쌍, 담당자 확인 대상, 짝 없는 음수)``.
    """
    available = list(positives)
    pending = list(negatives)
    order = {id(negative): index for index, negative in enumerate(negatives)}

    pairs: list[OffsetPair] = []
    review: list[NeedsManualReviewGroup] = []

    while pending and available:
        # 남은 음수마다 "가장 가까운 양수까지의 거리" 를 구하고, 그 값이 가장
        # 작은 음수부터 확정한다. 동점이면 입력 순서로 가른다(결과 안정성).
        nearest = min(
            (
                (
                    min(_distance_days(positive, negative) for positive in available),
                    order[id(negative)],
                    negative,
                )
                for negative in pending
            ),
            key=lambda scored: (scored[0], scored[1]),
        )
        distance, _, negative = nearest
        closest = [
            positive for positive in available if _distance_days(positive, negative) == distance
        ]

        pending.remove(negative)

        if len(closest) > 1:
            # ⛔ 여기서 고르지 않는다 — 고객 업무규칙상 담당자가 G20 지출결의서
            # 조회 여부로 판단하는 지점이다(§0.6.3.4 ⑧).
            review.append(
                NeedsManualReviewGroup(
                    key=key,
                    negative=negative,
                    candidates=list(closest),
                    distance_days=distance,
                )
            )
            continue

        partner = closest[0]
        available.remove(partner)
        pairs.append(OffsetPair(positive=partner, negative=negative, distance_days=distance))

    return pairs, review, list(pending)


def summarize(result: OffsetResult) -> str:
    """상계 결과를 한 줄로 요약합니다(로그·리포트용)."""
    return (
        f"상계 {len(result.pairs)}쌍 · 남은 거래 {len(result.remaining)}건 · "
        f"짝 없는 음수 {len(result.unmatched_negatives)}건 · "
        f"담당자 확인 {len(result.needs_manual_review)}건 · "
        f"발행일자 없음 {len(result.missing_issue_date)}건"
    )


def confirmed_match_fields() -> Sequence[str]:
    """고객이 확정한 동일 거래 판별 요소를 반환합니다(문서·테스트용)."""
    return ("company_name", "business_no", "abs(amount)", "nearest issue_date")
