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

from collections.abc import Sequence
from dataclasses import dataclass

from procurement.core.description_classifier import DescriptionClassifier
from procurement.core.description_key import normalize_description
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
from procurement.reviews.past_labels import (
    EMPTY_SUMMARY,
    MIXED_TYPES,
    PastLabelIndex,
    PastLabelSummary,
)
from procurement.reviews.query import (
    ANY,
    DECIDED,
    HAS_HISTORY,
    HISTORY_AGREES,
    HISTORY_MIXED,
    MANY_CANDIDATES,
    NO_CANDIDATE,
    NO_HISTORY_ONLY,
    ONE_CANDIDATE,
    PRESENT_FIRST,
    UNDECIDED,
    PageInfo,
    ReviewQuery,
    sort_bucket,
)

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


@dataclass(frozen=True, kw_only=True)
class ReviewPage:
    """조건에 맞는 한 페이지.

    Attributes:
        items: 이 페이지의 항목.
        page: 페이지 상태(현재 페이지 · 크기 · 전체 건수).
    """

    items: list[ReviewTarget]
    page: PageInfo


def _keeps(target: ReviewTarget, query: ReviewQuery) -> bool:
    """조건에 맞는 항목인가.

    ⛔ 값을 바꾸지 않고 **보여줄지 말지**만 정합니다.
    """
    review = target.review

    if query.search:
        needle = normalize_description(query.search)
        if needle and needle not in normalize_description(target.purchase.description):
            return False

    if query.status != ANY and review.review_status != query.status:
        return False

    if query.decision == DECIDED and review.review_status != CONFIRMED:
        return False
    if query.decision == UNDECIDED and review.review_status == CONFIRMED:
        return False

    count = len(review.candidates)
    if query.candidates == NO_CANDIDATE and count != 0:
        return False
    if query.candidates == ONE_CANDIDATE and count != 1:
        return False
    if query.candidates == MANY_CANDIDATES and count < 2:
        return False

    if query.ambiguous_only and not review.is_ambiguous:
        return False

    return _keeps_by_history(target, query.history)


def _keeps_by_history(target: ReviewTarget, history: str) -> bool:
    """과거 이력 조건에 맞는가."""
    if history == ANY:
        return True

    past = target.past_labels
    if history == HAS_HISTORY:
        return past.total > 0
    if history == NO_HISTORY_ONLY:
        return past.total == 0
    if history == HISTORY_MIXED:
        return past.consistency == MIXED_TYPES

    # 아래 둘은 **분석 후보와 과거를 비교**하므로 둘 다 있어야 의미가 있다.
    top = target.review.top_candidate
    if top is None or past.total == 0:
        return False
    differs = past.differs_from(top.purchase_type)
    return not differs if history == HISTORY_AGREES else differs


def _sort_value(target: ReviewTarget, key: str) -> object | None:
    """정렬에 쓸 값. **값이 없으면** ``None``.

    ``None`` 은 "0" 이나 "빈 문자열" 과 다릅니다 — 예를 들어 후보가 1개면
    점수차라는 것이 **존재하지 않습니다.**
    """
    purchase = target.purchase
    review = target.review
    past = target.past_labels

    if key == "resolution_date":
        return purchase.resolution_date
    if key == "issue_date":
        return purchase.issue_date
    if key == "amount":
        return purchase.amount
    if key == "description":
        return normalize_description(purchase.description) or None
    if key == "status":
        return review.review_status
    if key == "candidate_count":
        return len(review.candidates)
    if key == "score_gap":
        candidates = review.candidates
        return candidates[0].score - candidates[1].score if len(candidates) > 1 else None
    if key == "has_history":
        return past.total
    if key == "dominant_ratio":
        return past.dominant_ratio if past.total else None
    return purchase.purchase_id


def _sorted(targets: list[ReviewTarget], key: str, *, descending: bool) -> list[ReviewTarget]:
    """정렬합니다. **값 없는 항목은 방향과 무관하게 늘 뒤**로 갑니다.

    한 번에 ``reverse=True`` 로 정렬하면 "값 없음" 표식까지 뒤집혀, 내림차순일
    때 빈 값이 **맨 앞으로** 몰려옵니다. 담당자 입장에서는 정렬 방향을 바꿨을
    뿐인데 빈 칸부터 보이는 셈이라, 값이 있는 것과 없는 것을 나눠 정렬합니다.

    같은 값끼리는 구매 ID 오름차순으로 마무리해, 같은 조건이면 늘 같은 순서가
    나오게 합니다.
    """
    present: list[ReviewTarget] = []
    missing: list[ReviewTarget] = []
    for target in targets:
        bucket = sort_bucket(_sort_value(target, key))
        (present if bucket == PRESENT_FIRST else missing).append(target)

    def identity(target: ReviewTarget) -> int:
        return target.purchase.purchase_id or 0

    present.sort(key=identity)
    present.sort(key=lambda target: _sort_value(target, key), reverse=descending)  # type: ignore[arg-type,return-value]
    missing.sort(key=identity)
    return present + missing


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
        # 과거 이력 색인과, 그것을 만들 때의 DB 지문.
        # 지문이 그대로면 다시 만들지 않는다 (:meth:`_past_label_index`).
        self._index: PastLabelIndex | None = None
        self._index_fingerprint: tuple[int, str | None] | None = None

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
        index = self._past_label_index(purchases, stored)

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

    def search(self, query: ReviewQuery, *, period: PeriodFilter | None = None) -> ReviewPage:
        """조건에 맞는 **한 페이지**를 반환합니다.

        거르고 줄 세우는 일을 **서버에서** 합니다. 전체를 브라우저로 내려보내지
        않으므로, 건수가 늘어도 화면이 받는 양은 페이지 크기로 고정됩니다.

        Args:
            query: 검색·필터·정렬·페이지 조건.
            period: 기간 조건. ``None`` 이면 제한 없음.

        Returns:
            :class:`ReviewPage` — 해당 페이지의 항목과 **조건에 맞는 전체 건수**.
        """
        targets = self.search_all(query, period=period)
        start = query.offset
        return ReviewPage(
            items=targets[start : start + query.page_size],
            page=PageInfo(page=query.page, page_size=query.page_size, total=len(targets)),
        )

    def search_all(
        self, query: ReviewQuery, *, period: PeriodFilter | None = None
    ) -> list[ReviewTarget]:
        """조건에 맞는 **전부**를 반환합니다(페이지 무시).

        CSV 내보내기처럼 "지금 보고 있는 조건의 전체" 가 필요한 경우에만
        씁니다. 화면 목록은 :meth:`search` 를 써서 한 페이지만 가져갑니다.
        """
        purchases = self._purchase_repository.find_for_calculation(period)
        stored = self._review_repository.find_all()
        reviews = {review.purchase_id: review for review in stored}
        index = self._past_label_index(purchases, stored)

        targets: list[ReviewTarget] = []
        for purchase in purchases:
            if purchase.purchase_id is None:
                continue
            review = reviews.get(purchase.purchase_id) or PurchaseReview(
                purchase_id=purchase.purchase_id
            )
            target = ReviewTarget(
                purchase=purchase,
                review=review,
                past_labels=index.summary_for(purchase.description),
            )
            if _keeps(target, query):
                targets.append(target)

        return _sorted(targets, query.sort, descending=query.descending)

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
    def _past_label_index(
        self,
        purchases: Sequence[Purchase] | None = None,
        reviews: Sequence[PurchaseReview] | None = None,
    ) -> PastLabelIndex:
        """과거 이력 색인을 돌려줍니다. **확정 내용이 그대로면 다시 만들지
        않습니다.**

        판단 근거는 서비스가 기억하는 값이 아니라
        :meth:`~procurement.database.review_repository.ReviewRepository.confirmed_fingerprint`
        — 즉 **DB 의 현재 상태**입니다. 그래서 다음 경우가 전부 안전합니다.

        - 이 서비스로 확정·재검토한 경우
        - 테스트나 다른 코드가 Repository 를 **직접** 고친 경우
        - 확정 유형만 바꾼 경우(건수는 같지만 갱신 시각이 달라짐)
        - ``CONFIRMED`` → ``REOPENED`` (건수가 줄어듦)

        Args:
            purchases: 이미 읽어 둔 구매 목록. 있으면 재조회하지 않습니다.
            reviews: 이미 읽어 둔 검토 목록. 〃

        Returns:
            :class:`~procurement.reviews.past_labels.PastLabelIndex`.
        """
        fingerprint = self._review_repository.confirmed_fingerprint()
        if self._index is not None and self._index_fingerprint == fingerprint:
            return self._index

        rows = (
            list(purchases)
            if purchases is not None
            else self._purchase_repository.find_for_calculation(None)
        )
        states = list(reviews) if reviews is not None else self._review_repository.find_all()
        self._index = PastLabelIndex(rows, states)
        self._index_fingerprint = fingerprint
        return self._index

    def _past_labels_for(self, purchase: Purchase) -> PastLabelSummary:
        """한 건에 대한 과거 확정 이력.

        ⛔ 자기 자신의 확정도 이력에 포함됩니다 — 화면에서 "이 적요는 지금까지
        용역 3건으로 확정됨" 을 그대로 보여주는 것이 목적이기 때문입니다.
        """
        return self._past_label_index().summary_for(purchase.description)

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
