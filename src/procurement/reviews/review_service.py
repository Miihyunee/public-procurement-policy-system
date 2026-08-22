"""
procurement.reviews.review_service

담당자 검토의 **업무 흐름**을 담당합니다.

::

    DB-1 (원본, 읽기 전용)  ─┐
                             ├→ ReviewService → 검토 API → 화면
    DB-2 (검토, 읽기·쓰기)  ─┘

.. warning::
    ⛔ **원본을 수정하지 않습니다.**

    :class:`~procurement.database.purchase_repository.PurchaseRepository` 는
    **조회에만** 사용합니다. 이 모듈에는 ``insert`` · ``update_`` 호출이
    없으며, 이를 테스트로 고정합니다.

.. warning::
    ⛔ **자동 확정하지 않습니다.**

    분석 점수가 아무리 높아도 :attr:`ReviewTarget.review` 의
    ``final_purchase_type`` 은 담당자가 :meth:`ReviewService.confirm` 을
    호출하기 전까지 ``None`` 입니다. 고객이 확정한 분류 규칙은 예산과목
    3건뿐입니다(``DECISIONS.md`` §0.5.3).

설계 근거: ``docs/REVIEW_INTERFACE_DESIGN.md``
"""

from __future__ import annotations

from dataclasses import dataclass

from procurement.core.description_classifier import DescriptionClassifier
from procurement.core.period import PeriodFilter
from procurement.database.purchase_repository import PurchaseRepository
from procurement.database.review_repository import ReviewRepository
from procurement.models.purchase import Purchase
from procurement.models.review import (
    CONFIRMED,
    PurchaseReview,
    ReviewHistoryEntry,
    ReviewProgress,
)
from procurement.reviews.past_labels import EMPTY_SUMMARY, PastLabelIndex, PastLabelSummary

#: 필터 — 전체.
FILTER_ALL = "ALL"

#: 필터 — 담당자가 확정한 건.
FILTER_CONFIRMED = "CONFIRMED"

#: 필터 — 아직 확정하지 않은 건(재검토 포함).
FILTER_PENDING = "PENDING"

#: 필터 — 후보가 갈려 **먼저 볼 것을 권하는** 건.
FILTER_AMBIGUOUS = "AMBIGUOUS"

#: 허용되는 필터.
REVIEW_FILTERS: frozenset[str] = frozenset(
    {FILTER_ALL, FILTER_CONFIRMED, FILTER_PENDING, FILTER_AMBIGUOUS}
)


class ReviewNotFoundError(LookupError):
    """대상 구매가 없을 때 발생합니다."""


class ReviewFilterError(ValueError):
    """허용되지 않는 필터를 받았을 때 발생합니다."""


@dataclass(frozen=True, kw_only=True)
class ReviewTarget:
    """검토 화면에 보여줄 한 건.

    **원본과 검토 결과를 분리해서** 담습니다. 화면이 둘을 섞을 수 없게 하는
    것이 목적입니다.

    Attributes:
        purchase: DB-1 원본. ⛔ 읽기 전용입니다.
        review: DB-2 검토 상태(분석 결과 + 담당자 확정).
        past_labels: 같은 적요를 **과거에 어떻게 확정했는지**. 참고 정보이며
            ⛔ 자동 확정에 쓰지 않습니다.
    """

    purchase: Purchase
    review: PurchaseReview
    past_labels: PastLabelSummary = EMPTY_SUMMARY


class ReviewService:
    """검토 대상을 모으고, 담당자의 확정을 기록합니다."""

    def __init__(
        self,
        purchase_repository: PurchaseRepository,
        review_repository: ReviewRepository,
        classifier: DescriptionClassifier | None = None,
    ) -> None:
        """서비스를 초기화합니다.

        Args:
            purchase_repository: DB-1 조회용. ⛔ **쓰기에 사용하지 않습니다.**
            review_repository: DB-2 저장소.
            classifier: 적요 분석기. ``None`` 이면 분석을 돌리지 않습니다
                (분석 방법은 아직 미선택 — `DESCRIPTION_SIMILARITY_DESIGN.md`).
        """
        self._purchase_repository = purchase_repository
        self._review_repository = review_repository
        self._classifier = classifier

    # ------------------------------------------------------------------
    # 조회
    # ------------------------------------------------------------------
    def list_targets(
        self,
        *,
        review_filter: str = FILTER_ALL,
        period: PeriodFilter | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[ReviewTarget]:
        """검토 대상 목록을 반환합니다.

        검토 행이 아직 없는 구매도 포함하며, 그런 건은 ``PENDING`` 상태의 빈
        검토로 표현됩니다. ⛔ 조회만으로 DB-2 에 행을 만들지 않습니다.

        Args:
            review_filter: :data:`FILTER_ALL` · :data:`FILTER_CONFIRMED` ·
                :data:`FILTER_PENDING` · :data:`FILTER_AMBIGUOUS`.
            period: 기간 조건. ``None`` 이면 제한 없음.
            limit: 최대 건수. ``None`` 이면 전체.
            offset: 건너뛸 건수.

        Returns:
            :class:`ReviewTarget` 목록. 구매 ID 오름차순.

        Raises:
            ReviewFilterError: 허용되지 않는 필터인 경우.
        """
        if review_filter not in REVIEW_FILTERS:
            allowed = " · ".join(sorted(REVIEW_FILTERS))
            raise ReviewFilterError(
                f"허용되지 않는 필터입니다: {review_filter!r} (허용: {allowed})"
            )

        purchases = self._purchase_repository.find_for_calculation(period)
        stored = self._review_repository.find_all()
        reviews = {review.purchase_id: review for review in stored}
        # 목록 한 번에 대해 색인을 한 번만 만든다 (건마다 재조회하면 O(n²)).
        index = PastLabelIndex(purchases, stored)

        targets: list[ReviewTarget] = []
        for purchase in purchases:
            if purchase.purchase_id is None:
                continue
            review = reviews.get(purchase.purchase_id) or PurchaseReview(
                purchase_id=purchase.purchase_id
            )
            if not self._matches(review, review_filter):
                continue
            targets.append(
                ReviewTarget(
                    purchase=purchase,
                    review=review,
                    past_labels=index.summary_for(purchase.description),
                )
            )

        end = None if limit is None else offset + limit
        return targets[offset:end]

    def get_target(self, purchase_id: int) -> ReviewTarget:
        """검토 대상 한 건을 반환합니다.

        Args:
            purchase_id: DB-1 구매 ID.

        Returns:
            :class:`ReviewTarget`.

        Raises:
            ReviewNotFoundError: 해당 구매가 없는 경우.
        """
        purchase = self._purchase_repository.find_by_id(purchase_id)
        if purchase is None:
            raise ReviewNotFoundError(f"존재하지 않는 구매입니다: purchase_id={purchase_id}")
        review = self._review_repository.find_by_purchase_id(purchase_id) or PurchaseReview(
            purchase_id=purchase_id
        )
        return ReviewTarget(
            purchase=purchase, review=review, past_labels=self._past_labels_for(purchase)
        )

    def history(self, purchase_id: int) -> list[ReviewHistoryEntry]:
        """변경 이력을 시간순으로 반환합니다."""
        return self._review_repository.find_history(purchase_id)

    def progress(self, period: PeriodFilter | None = None) -> ReviewProgress:
        """검토 진행 상황을 집계합니다.

        분모는 **검토 대상 구매 수**입니다. 검토 행이 없는 구매도 미확정으로
        셉니다 — 그래야 "1,203 / 2,292" 처럼 실제 남은 일이 보입니다.

        Args:
            period: 기간 조건. ``None`` 이면 제한 없음.
        """
        purchases = self._purchase_repository.find_for_calculation(period)
        reviews = {review.purchase_id: review for review in self._review_repository.find_all()}

        total = confirmed = ambiguous = analyzed = 0
        for purchase in purchases:
            if purchase.purchase_id is None:
                continue
            total += 1
            review = reviews.get(purchase.purchase_id)
            if review is None:
                continue
            if review.is_confirmed:
                confirmed += 1
            if review.is_ambiguous:
                ambiguous += 1
            if review.analyzer_name is not None:
                analyzed += 1

        return ReviewProgress(
            total=total,
            confirmed=confirmed,
            pending=total - confirmed,
            ambiguous=ambiguous,
            not_analyzed=total - analyzed,
        )

    # ------------------------------------------------------------------
    # 쓰기
    # ------------------------------------------------------------------
    def confirm(
        self,
        purchase_id: int,
        *,
        final_purchase_type: str | None,
        reviewed_by: str | None = None,
        review_note: str | None = None,
    ) -> ReviewTarget:
        """담당자의 최종 선택을 확정합니다.

        Args:
            purchase_id: DB-1 구매 ID.
            final_purchase_type: ``CONSTRUCTION`` · ``SERVICE`` · ``GOODS``
                또는 ``None``(**판단 보류**).
            reviewed_by: 확정자.
            review_note: 메모.

        Returns:
            갱신된 :class:`ReviewTarget`.

        Raises:
            ReviewNotFoundError: 해당 구매가 없는 경우.
            ReviewValidationError: 허용되지 않는 유형값인 경우.
        """
        purchase = self._require_purchase(purchase_id)
        review = self._review_repository.confirm(
            purchase_id,
            final_purchase_type=final_purchase_type,
            reviewed_by=reviewed_by,
            review_note=review_note,
        )
        return ReviewTarget(
            purchase=purchase, review=review, past_labels=self._past_labels_for(purchase)
        )

    def reopen(
        self, purchase_id: int, *, reopened_by: str | None = None, note: str | None = None
    ) -> ReviewTarget:
        """확정을 되돌려 다시 검토 상태로 만듭니다.

        Raises:
            ReviewNotFoundError: 해당 구매가 없는 경우.
        """
        purchase = self._require_purchase(purchase_id)
        review = self._review_repository.reopen(purchase_id, reopened_by=reopened_by, note=note)
        return ReviewTarget(
            purchase=purchase, review=review, past_labels=self._past_labels_for(purchase)
        )

    def analyze(self, purchase_id: int) -> ReviewTarget:
        """적요를 분석해 후보를 DB-2 에 기록합니다.

        .. warning::
            ⛔ **확정값을 덮지 않습니다.** 분석 컬럼만 갱신됩니다
            (:meth:`~procurement.database.review_repository.ReviewRepository.save_analysis`).

        Args:
            purchase_id: DB-1 구매 ID.

        Returns:
            갱신된 :class:`ReviewTarget`. 분석기가 없으면 상태 변화 없이
            현재 값을 그대로 반환합니다.

        Raises:
            ReviewNotFoundError: 해당 구매가 없는 경우.
        """
        purchase = self._require_purchase(purchase_id)
        if self._classifier is None:
            return self.get_target(purchase_id)

        result = self._classifier.classify(purchase.description)
        review = self._review_repository.save_analysis(purchase_id, result)
        return ReviewTarget(
            purchase=purchase, review=review, past_labels=self._past_labels_for(purchase)
        )

    def analyze_all(self, period: PeriodFilter | None = None) -> int:
        """검토 대상 전체를 분석합니다.

        Args:
            period: 기간 조건. ``None`` 이면 제한 없음.

        Returns:
            분석한 건수. 분석기가 없으면 ``0``.
        """
        if self._classifier is None:
            return 0

        analyzed = 0
        for purchase in self._purchase_repository.find_for_calculation(period):
            if purchase.purchase_id is None:
                continue
            result = self._classifier.classify(purchase.description)
            self._review_repository.save_analysis(purchase.purchase_id, result)
            analyzed += 1
        return analyzed

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------
    def _past_labels_for(self, purchase: Purchase) -> PastLabelSummary:
        """한 건에 대한 과거 확정 이력.

        ⛔ 자기 자신의 확정도 이력에 포함됩니다 — 화면에서 "이 적요는 지금까지
        용역 3건으로 확정됨" 을 그대로 보여주는 것이 목적이기 때문입니다.
        """
        index = PastLabelIndex(
            self._purchase_repository.find_for_calculation(None),
            self._review_repository.find_all(),
        )
        return index.summary_for(purchase.description)

    def _require_purchase(self, purchase_id: int) -> Purchase:
        """구매를 조회하고 없으면 예외를 냅니다."""
        purchase = self._purchase_repository.find_by_id(purchase_id)
        if purchase is None:
            raise ReviewNotFoundError(f"존재하지 않는 구매입니다: purchase_id={purchase_id}")
        return purchase

    @staticmethod
    def _matches(review: PurchaseReview, review_filter: str) -> bool:
        """필터 조건에 맞는지 판정합니다."""
        if review_filter == FILTER_ALL:
            return True
        if review_filter == FILTER_CONFIRMED:
            return review.review_status == CONFIRMED
        if review_filter == FILTER_PENDING:
            return review.review_status != CONFIRMED
        return review.is_ambiguous
