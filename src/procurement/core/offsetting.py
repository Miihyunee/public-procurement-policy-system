"""
procurement.core.offsetting

**음수 거래와 원거래(양수)를 짝지어 상계**하는 판정 로직입니다.

2026-08-20 고객 최종 확정 업무규칙 (`DECISIONS.md` §0.6.3.5)::

    ① 동일 기업 + ② 동일 사업자등록번호 + ③ 동일 금액(절대값)
              ↓  후보 검색
    ┌──────────────────┐
    │ 후보 0건          │ → 짝 없음
    ├──────────────────┤
    │ 후보 1건 (1:1)    │ → ✅ 자동 상계
    ├──────────────────┤
    │ 후보 2건 이상      │ → 🟡 담당자 확인 대상
    └──────────────────┘

후보가 여러 건이면 담당자가 **적요 · 예산과목 · 세금계산서 발행일자 · G20
지출결의서의 세금계산서 내용 · 비고란**을 종합해 판단합니다. 시스템은 그 판단을
대신하지 않고, **확인해야 할 후보를 모아 보여줄 뿐**입니다.

.. warning::
    ⛔ **어떤 자동 우선순위도 만들지 않습니다.**

    ============================ =================================================
    만들지 않는 규칙              근거 (고객 회신)
    ============================ =================================================
    발행일자가 가까운 후보 선택   "후보가 여러 건이면 지출결의서를 확인하여 진행".
                                  발행일자는 **담당자에게 보여줄 참고정보**일 뿐.
    적요가 같은 후보 선택         "적요가 서로 다르더라도 상계할 수 있다".
    예산과목이 공란인 후보 선택   "공란이라고 무조건 삭제할 수 있는 것은 아니다".
    파일 순서 · 최근/최초 거래    확정 사항이 아니다.
    ============================ =================================================

    ⚠️ **2026-08-20 제거된 로직** — 이전 판에는 "발행일자 차이가 최소인 후보를
    자동 선택" 이 있었습니다. 실측에서 자동 상계 72쌍 중 **5쌍이 담당자 처리와
    달랐고**(모두 더 가까운 날짜의 다른 거래를 골랐음), 고객 최종 답변과도
    맞지 않아 **완전히 제거**했습니다. 되살리지 마십시오.

.. warning::
    **G20 은 자동화하지 않습니다.** 로그인 · 지출결의서 조회 · 내용 자동 수집 ·
    비고란 자동 분석은 모두 범위 밖입니다. 시스템의 책임은 "이 건은 후보가 여러
    건이므로 담당자 확인이 필요하다" 를 알리고, 확인에 필요한 후보 정보를
    제공하는 것까지입니다.

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
from typing import Final, Literal

from procurement.models.purchase import Purchase

#: 동일 거래 판별 키 — (기업명, 사업자등록번호, 절대금액).
MatchKey = tuple[str, str, Decimal]

#: 담당자 확인이 필요한 사유.
ReviewReason = Literal["MULTIPLE_CANDIDATES", "CONTESTED_CANDIDATE"]

#: 양수 후보가 2건 이상이라 어느 것인지 정할 수 없다.
MULTIPLE_CANDIDATES: Final[ReviewReason] = "MULTIPLE_CANDIDATES"

#: 후보는 적은데 같은 조건의 음수가 여러 건이어서 1:1 이 성립하지 않는다.
CONTESTED_CANDIDATE: Final[ReviewReason] = "CONTESTED_CANDIDATE"


@dataclass(frozen=True, kw_only=True)
class OffsetPair:
    """상계로 맺어진 (양수, 음수) 한 쌍.

    **후보가 정확히 1건이었던 경우만** 여기에 들어옵니다. 시스템이 여러 후보
    중에서 고른 결과는 존재하지 않습니다.

    Attributes:
        positive: 원거래로 판단한 양수 구매.
        negative: 그 원거래를 취소하는 것으로 판단한 음수 구매.
    """

    positive: Purchase
    negative: Purchase


@dataclass(frozen=True, kw_only=True)
class NeedsManualReviewGroup:
    """후보가 여러 건이라 **담당자가 확인해야 하는** 건.

    담당자는 G20 에서 지출결의서(세금계산서 내용 · 비고란 등)를 조회해 최종
    상계 대상을 판단합니다. 시스템은 그 판단을 대신하지 않고, 대조에 필요한
    후보 정보를 모아 전달합니다.

    Attributes:
        key: 동일 거래 판별 키 (기업명, 사업자등록번호, 절대금액).
        negative: 짝을 정하지 못한 음수 구매.
        candidates: 3조건을 만족하는 **모든** 양수 후보. 순서는 입력 순서이며
            **우선순위가 아닙니다.**
        reason: 확인이 필요한 사유. :data:`MULTIPLE_CANDIDATES` 또는
            :data:`CONTESTED_CANDIDATE`.
        sibling_negatives: 같은 판별 키를 가진 다른 음수 거래. 담당자가 그룹
            전체를 함께 봐야 하는 경우가 있어 함께 전달합니다.
    """

    key: MatchKey
    negative: Purchase
    candidates: list[Purchase] = field(default_factory=list)
    reason: ReviewReason = MULTIPLE_CANDIDATES
    sibling_negatives: list[Purchase] = field(default_factory=list)

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

    def candidate_lines(self) -> tuple[str, ...]:
        """담당자가 G20 에서 대조할 수 있도록 후보를 한 줄씩 정리합니다.

        발행일자 · 적요 · 예산과목을 **나란히** 보여 줍니다. 이 순서는 화면
        표시 순서일 뿐이며 **선택 우선순위가 아닙니다.**
        """
        return tuple(
            f"발행일자 {_show_date(candidate.issue_date)} | "
            f"적요 {candidate.description or '-'} | "
            f"예산과목 {candidate.budget_account or '(공란)'}"
            for candidate in self.candidates
        )


@dataclass(frozen=True, kw_only=True)
class OffsetResult:
    """상계 판정 결과.

    Attributes:
        remaining: 상계되지 않고 **남은** 구매 목록. 계산에 사용할 대상입니다.
            담당자 확인 대상의 거래도 여기에 그대로 남습니다.
        pairs: 자동 상계된 (양수, 음수) 쌍 목록. **후보가 1건이었던 경우만.**
        unmatched_negatives: 같은 키의 양수가 **없어** 짝을 찾지 못한 음수.
            임의로 버리거나 상계하지 않고 **그대로 보고**합니다.
        needs_manual_review: 후보가 여러 건이라 **담당자 확인이 필요한** 건.
            상계하지 않았습니다.
        missing_issue_date: 세금계산서 발행일자가 없는 구매(**참고용 보고**).

            .. warning::
                🟡 **내부 판단 · 미확정 예외사항.** 발행일자가 없어도 후보가
                1건이면 자동 상계합니다. 발행일자가 판정 조건이 아니므로 막을
                근거가 없다는 **우리 판단**이며, **고객이 확정한 규칙은
                아닙니다**(`DECISIONS.md` §0.6.3.5).

                실데이터에는 결측이 **0건**이라 현재 영향은 없습니다. 결측이
                들어오는 데이터가 생기면 이 목록으로 드러나므로 그때 고객에게
                확인합니다. ⛔ 다른 날짜로 대체하지 않습니다.
    """

    remaining: list[Purchase] = field(default_factory=list)
    pairs: list[OffsetPair] = field(default_factory=list)
    unmatched_negatives: list[Purchase] = field(default_factory=list)
    needs_manual_review: list[NeedsManualReviewGroup] = field(default_factory=list)
    missing_issue_date: list[Purchase] = field(default_factory=list)


def _show_date(value: object) -> str:
    """날짜를 표시용 문자열로 만듭니다(없으면 표시만 비웁니다)."""
    return str(value) if value is not None else "(없음)"


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

    동일 거래 후보 조건(**모두** 만족):

    1. 기업명이 같다
    2. 사업자등록번호가 같다
    3. 양수 금액 == 음수 금액의 절대값

    후보가 **정확히 1건이고 음수도 1건일 때만** 자동으로 상계합니다. 그 밖에
    후보가 여러 건이거나 같은 조건의 음수가 여러 건이면 **아무것도 고르지 않고**
    :attr:`OffsetResult.needs_manual_review` 로 넘깁니다.

    Args:
        purchases: 판정 대상 구매 목록.

    Returns:
        :class:`OffsetResult`. ``remaining`` 이 계산에 사용할 목록입니다.

    Examples:
        >>> # A기업 +100,000 과 A기업 −100,000 뿐이면 자동 상계된다.
        >>> # 같은 조건 양수가 2건이면 상계하지 않고 담당자 확인 대상이 된다.
    """
    items = list(purchases)

    positives: dict[MatchKey, list[Purchase]] = {}
    negatives: dict[MatchKey, list[Purchase]] = {}

    for purchase in items:
        # 금액이 0 인 거래는 상계 대상이 아니므로 그대로 남는다.
        if purchase.amount == 0:
            continue
        bucket = positives if purchase.amount > 0 else negatives
        bucket.setdefault(_match_key(purchase), []).append(purchase)

    pairs: list[OffsetPair] = []
    unmatched: list[Purchase] = []
    review: list[NeedsManualReviewGroup] = []

    for key, group_negatives in negatives.items():
        group_positives = positives.get(key, [])

        if not group_positives:
            unmatched.extend(group_negatives)
            continue

        if len(group_positives) == 1 and len(group_negatives) == 1:
            # 1:1 로 명확한 경우에만 자동 상계한다.
            pairs.append(OffsetPair(positive=group_positives[0], negative=group_negatives[0]))
            continue

        # ⛔ 여기서 고르지 않는다. 어떤 컬럼으로도 자동 선택하지 않는다.
        reason = MULTIPLE_CANDIDATES if len(group_positives) > 1 else CONTESTED_CANDIDATE
        for negative in group_negatives:
            review.append(
                NeedsManualReviewGroup(
                    key=key,
                    negative=negative,
                    candidates=list(group_positives),
                    reason=reason,
                    sibling_negatives=[other for other in group_negatives if other is not negative],
                )
            )

    consumed = {id(pair.positive) for pair in pairs} | {id(pair.negative) for pair in pairs}
    remaining = [purchase for purchase in items if id(purchase) not in consumed]
    missing = [
        purchase for purchase in items if purchase.amount != 0 and purchase.issue_date is None
    ]

    return OffsetResult(
        remaining=remaining,
        pairs=pairs,
        unmatched_negatives=unmatched,
        needs_manual_review=review,
        missing_issue_date=missing,
    )


def summarize(result: OffsetResult) -> str:
    """상계 결과를 한 줄로 요약합니다(로그·리포트용)."""
    return (
        f"자동 상계 {len(result.pairs)}쌍 · 남은 거래 {len(result.remaining)}건 · "
        f"짝 없는 음수 {len(result.unmatched_negatives)}건 · "
        f"담당자 확인 {len(result.needs_manual_review)}건 · "
        f"발행일자 없음 {len(result.missing_issue_date)}건"
    )


def confirmed_match_fields() -> Sequence[str]:
    """고객이 확정한 동일 거래 판별 요소를 반환합니다(문서·테스트용).

    **세 가지뿐입니다.** 발행일자·적요·예산과목은 판별 요소가 아니라 담당자가
    확인할 때 참고하는 정보입니다.
    """
    return ("company_name", "business_no", "abs(amount)")
