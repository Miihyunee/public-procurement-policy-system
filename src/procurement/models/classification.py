"""
procurement.models.classification

적요 분석이 만들어 내는 **구매유형 후보**를 표현합니다.

.. warning::
    ⛔ **최종 확정값을 담지 않습니다.**

    이 모듈에는 ``final_purchase_type`` 같은 필드가 **의도적으로 없습니다.**
    분석은 후보를 제시할 뿐이고, 최종 확정은 담당자만 할 수 있습니다
    (:mod:`procurement.models.review`). 타입 수준에서 "자동 확정" 을 불가능하게
    만드는 것이 이 설계의 목적입니다.

    고객이 확정한 구매유형 분류 규칙은 **예산과목 3건뿐**입니다
    (``DECISIONS.md`` §0.5.3). 점수가 아무리 높아도 **후보일 뿐**입니다.

설계 근거: ``docs/DESCRIPTION_SIMILARITY_DESIGN.md`` §2
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from procurement.core.purchase_type import PURCHASE_TYPE_LABELS, PURCHASE_TYPES

#: 분석 상태 — 아직 분석하지 않음.
NOT_ANALYZED = "NOT_ANALYZED"

#: 분석 상태 — 분석을 마침(후보가 0개일 수도 있음).
ANALYZED = "ANALYZED"

#: 분석 상태 — 분석에 실패함.
FAILED = "FAILED"

#: 허용되는 분석 상태.
ANALYSIS_STATUSES: frozenset[str] = frozenset({NOT_ANALYZED, ANALYZED, FAILED})


class ClassificationError(ValueError):
    """분석 결과 값이 규약을 벗어났을 때 발생합니다."""


@dataclass(frozen=True, kw_only=True)
class TypeCandidate:
    """구매유형 후보 하나.

    Attributes:
        purchase_type: :data:`~procurement.core.purchase_type.PURCHASE_TYPES`
            중 하나. 새 분류 체계를 만들지 않습니다.
        score: 0 이상 1 이하. 클수록 그 유형일 가능성이 높다는 **분석기의
            의견**이며, 업무적 확정이 아닙니다.
        evidence: 왜 이 후보인지에 대한 설명. 담당자가 판단하려면 근거가
            보여야 하므로 비워 두지 않는 것을 권장합니다.
    """

    purchase_type: str
    score: Decimal
    evidence: str = ""

    def __post_init__(self) -> None:
        """허용된 유형과 점수 범위만 받습니다."""
        if self.purchase_type not in PURCHASE_TYPES:
            allowed = " · ".join(sorted(PURCHASE_TYPES))
            raise ClassificationError(
                f"허용되지 않는 구매유형입니다: {self.purchase_type!r} (허용: {allowed})"
            )
        if not Decimal("0") <= self.score <= Decimal("1"):
            raise ClassificationError(f"점수는 0 이상 1 이하여야 합니다: {self.score}")

    @property
    def label(self) -> str:
        """화면 표시용 한글 라벨(공사 · 용역 · 물품)."""
        return PURCHASE_TYPE_LABELS[self.purchase_type]


@dataclass(frozen=True, kw_only=True)
class ClassificationResult:
    """적요 하나에 대한 분석 결과.

    .. warning::
        ⛔ **최종 유형 필드가 없습니다.** 후보와 점수만 담습니다.

    Attributes:
        candidates: 점수 내림차순 후보 목록. **비어 있을 수 있습니다** —
            판단할 수 없으면 억지 후보를 만들지 않는 것이 정직합니다.
        analyzer_name: 어떤 분석기가 만든 결과인지. 방법 비교·재현에 필요합니다.
        analyzer_version: 분석기 버전.
        status: :data:`ANALYZED` / :data:`FAILED` / :data:`NOT_ANALYZED`.
        note: 실패 사유 등 부가 설명.
    """

    candidates: list[TypeCandidate] = field(default_factory=list)
    analyzer_name: str
    analyzer_version: str
    status: str = ANALYZED
    note: str = ""

    def __post_init__(self) -> None:
        """상태값과 후보 정렬을 검증합니다."""
        if self.status not in ANALYSIS_STATUSES:
            allowed = " · ".join(sorted(ANALYSIS_STATUSES))
            raise ClassificationError(
                f"허용되지 않는 분석 상태입니다: {self.status!r} (허용: {allowed})"
            )
        scores = [candidate.score for candidate in self.candidates]
        if scores != sorted(scores, reverse=True):
            raise ClassificationError("후보는 점수 내림차순이어야 합니다.")

    @property
    def top(self) -> TypeCandidate | None:
        """1순위 후보. 후보가 없으면 ``None``.

        ⛔ 이 값이 "확정된 유형" 이라는 뜻이 **아닙니다.** 화면 정렬과 표시에만
        사용합니다.
        """
        return self.candidates[0] if self.candidates else None

    @property
    def runner_up(self) -> TypeCandidate | None:
        """2순위 후보. 없으면 ``None``."""
        return self.candidates[1] if len(self.candidates) > 1 else None

    @property
    def is_ambiguous(self) -> bool:
        """1순위와 2순위가 **갈리는가**(이중 매칭).

        .. warning::
            🔴 **판정 기준(임계값)은 고객·PM 미확정입니다.**

            "0.72 대 0.68 은 애매하고 0.97 대 0.21 은 명확하다" 는 것은
            사람의 감각이지 확정된 업무규칙이 아닙니다. 절대 차이로 볼지,
            상대 비율로 볼지, 1순위 하한을 둘지 모두 정해지지 않았습니다.

            **따라서 임계값을 만들지 않았습니다.** 현재 구현은 "후보가 2개
            이상이면 갈린다" 로만 보며, 이는 담당자가 **먼저 볼 건을 정렬**
            하는 데만 쓰입니다. ⛔ 자동 확정·자동 제외에 쓰지 않습니다.

            → ``docs/DESCRIPTION_SIMILARITY_DESIGN.md`` §4 (결정 대기 ②)
        """
        return len(self.candidates) > 1
