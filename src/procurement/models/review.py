"""
procurement.models.review

**담당자 검토(DB-2)** 도메인 모델.

한 건의 검토는 두 부분으로 나뉩니다.

==================== ================================================
자동 분석 결과        분석기가 씀. ``analysis_*`` · ``candidates``
담당자 확정 결과      담당자만 씀. ``final_purchase_type`` · ``reviewed_*``
==================== ================================================

.. warning::
    ⛔ **자동 분석이 담당자 확정값을 덮지 않습니다.**

    두 영역이 **서로 다른 필드**에 있고, Repository 도 서로 다른 메서드로만
    씁니다(:meth:`~procurement.database.review_repository.ReviewRepository.save_analysis`
    / :meth:`...confirm`). 재분석을 몇 번 돌려도 ``final_purchase_type`` 은
    그대로입니다.

.. warning::
    ⛔ **원본(DB-1)을 수정하지 않습니다.** 이 모델은 ``purchase_id`` 로 원본을
    참조만 하며, 적요·금액 등 원본 값을 복사해 두지 않습니다.

설계 근거: ``docs/DATABASE_PIPELINE_DESIGN.md`` §3
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from procurement.core.purchase_type import PURCHASE_TYPE_LABELS, PURCHASE_TYPES
from procurement.models.classification import NOT_ANALYZED, TypeCandidate

#: 검토 상태 — 아직 확정하지 않음(초기값).
PENDING = "PENDING"

#: 검토 상태 — 담당자가 확정함.
CONFIRMED = "CONFIRMED"

#: 검토 상태 — 확정을 되돌려 다시 보는 중.
REOPENED = "REOPENED"

#: 허용되는 검토 상태.
REVIEW_STATUSES: frozenset[str] = frozenset({PENDING, CONFIRMED, REOPENED})

#: 이력 행위 — 분석 결과가 기록됨.
ACTION_ANALYZED = "ANALYZED"

#: 이력 행위 — 담당자가 확정함.
ACTION_CONFIRMED = "CONFIRMED"

#: 이력 행위 — 확정을 되돌림.
ACTION_REOPENED = "REOPENED"

#: 허용되는 이력 행위.
REVIEW_ACTIONS: frozenset[str] = frozenset({ACTION_ANALYZED, ACTION_CONFIRMED, ACTION_REOPENED})


class ReviewValidationError(ValueError):
    """검토 값이 규약을 벗어났을 때 발생합니다."""


def validate_final_purchase_type(value: str | None) -> None:
    """담당자가 고른 최종 유형이 허용값인지 확인합니다.

    ``None`` 은 **"판단 보류"** 를 뜻하며 정상입니다. 담당자가 모를 때 아무
    값이나 고르게 만들지 않기 위해 필요한 상태입니다.

    Args:
        value: ``CONSTRUCTION`` · ``SERVICE`` · ``GOODS`` 또는 ``None``.

    Raises:
        ReviewValidationError: 허용되지 않는 값인 경우.
    """
    if value is None:
        return
    if value not in PURCHASE_TYPES:
        allowed = " · ".join(sorted(PURCHASE_TYPES))
        raise ReviewValidationError(
            f"허용되지 않는 구매유형입니다: {value!r} (허용: {allowed} 또는 판단 보류)"
        )


@dataclass(kw_only=True)
class PurchaseReview:
    """구매 한 건에 대한 검토 상태 (DB-2 현재 상태).

    Attributes:
        purchase_id: DB-1 ``purchase.purchase_id`` 참조. 1:1 입니다.

        analysis_status: :data:`~procurement.models.classification.ANALYZED` 등.
        analyzer_name: 어떤 분석기가 만든 후보인지.
        analyzer_version: 분석기 버전.
        analyzed_at: 분석 시각.
        candidates: 분석기가 준 후보 목록(점수 내림차순). 비어 있을 수 있습니다.
        analysis_note: 분석 관련 부가 설명.

        review_status: :data:`PENDING` / :data:`CONFIRMED` / :data:`REOPENED`.
        final_purchase_type: **담당자가 고른** 최종 유형. ``None`` 은 판단 보류.
            ⛔ 분석기가 이 값을 쓰지 않습니다.
        reviewed_by: 확정자.
        reviewed_at: 확정 시각.
        review_note: 담당자 메모.

        review_id: 내부 고유 ID. 저장 전에는 ``None``.
        created_at / updated_at: 저장 시 채워집니다.
    """

    purchase_id: int

    analysis_status: str = NOT_ANALYZED
    analyzer_name: str | None = None
    analyzer_version: str | None = None
    analyzed_at: datetime | None = None
    candidates: list[TypeCandidate] = field(default_factory=list)
    analysis_note: str | None = None

    review_status: str = PENDING
    final_purchase_type: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    review_note: str | None = None

    review_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def top_candidate(self) -> TypeCandidate | None:
        """1순위 후보. ⛔ 확정값이 아닙니다(표시·정렬용)."""
        return self.candidates[0] if self.candidates else None

    @property
    def is_ambiguous(self) -> bool:
        """후보가 갈리는가.

        .. warning::
            🔴 임계값은 미확정입니다. 현재는 "후보 2개 이상" 으로만 봅니다.
            담당자가 **먼저 볼 건을 정렬**하는 데만 쓰며, ⛔ 자동 확정·자동
            제외에 쓰지 않습니다
            (``docs/DESCRIPTION_SIMILARITY_DESIGN.md`` §4).
        """
        return len(self.candidates) > 1

    @property
    def is_confirmed(self) -> bool:
        """담당자가 확정했는가."""
        return self.review_status == CONFIRMED

    @property
    def final_purchase_type_label(self) -> str | None:
        """최종 유형의 한글 라벨. 판단 보류면 ``None``."""
        if self.final_purchase_type is None:
            return None
        return PURCHASE_TYPE_LABELS[self.final_purchase_type]


@dataclass(frozen=True, kw_only=True)
class ReviewHistoryEntry:
    """검토 변경 이력 한 건 (append-only).

    확정값을 덮어써도 **이전 값이 여기 남습니다.** 지시 9번의 "원본 · AI 분석 ·
    담당자 결정 · 확정일 · 확정자" 추적 요구를 이 테이블이 담당합니다.

    Attributes:
        purchase_id: 대상 구매.
        action: :data:`ACTION_ANALYZED` / :data:`ACTION_CONFIRMED` /
            :data:`ACTION_REOPENED`.
        changed_at: 변경 시각.
        changed_by: 변경자. 분석기가 기록한 경우 분석기 이름.
        before_type: 변경 전 최종 유형(없으면 ``None``).
        after_type: 변경 후 최종 유형(없으면 ``None``).
        note: 설명·메모.
        candidates: 그 시점의 분석 후보 스냅샷.
        history_id: 내부 고유 ID. 저장 전에는 ``None``.
    """

    purchase_id: int
    action: str
    changed_at: datetime
    changed_by: str | None = None
    before_type: str | None = None
    after_type: str | None = None
    note: str | None = None
    candidates: list[TypeCandidate] = field(default_factory=list)
    history_id: int | None = None


@dataclass(frozen=True, kw_only=True)
class ReviewProgress:
    """검토 진행 상황 집계.

    Attributes:
        total: 검토 대상 건수.
        confirmed: 담당자가 확정한 건수.
        pending: 아직 확정하지 않은 건수(재검토 포함).
        ambiguous: 후보가 갈려 **먼저 볼 것을 권하는** 건수.
        not_analyzed: 아직 분석하지 않은 건수.
    """

    total: int = 0
    confirmed: int = 0
    pending: int = 0
    ambiguous: int = 0
    not_analyzed: int = 0
