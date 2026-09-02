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
from decimal import Decimal

from procurement.core.amount_search import amount_search_key
from procurement.core.description_classifier import DescriptionClassifier
from procurement.core.description_key import normalize_description
from procurement.core.period import PeriodFilter
from procurement.database.purchase_repository import PurchaseRepository
from procurement.database.review_repository import ReviewRepository
from procurement.matchers.business_no import business_no_search_key
from procurement.models.purchase import Purchase
from procurement.models.review import (
    CONFIRMED,
    PurchaseReview,
    ReviewHistoryEntry,
    ReviewProgress,
)
from procurement.reviews.company_labels import CompanyLabelIndex
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


class ReviewStateError(ValueError):
    """지금 상태에서 할 수 없는 동작을 요청했을 때 발생합니다."""


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
        company_labels: 같은 **업체**(사업자등록번호 기준)를 과거에 어떻게
            확정했는지. 적요
            이력과 **같은 기준**으로 세며(확정 + 판단 보류 제외), 묶는 키만
            거래처명입니다. ⛔ 자동 확정에 쓰지 않습니다.
    """

    purchase: Purchase
    review: PurchaseReview
    past_labels: PastLabelSummary = EMPTY_SUMMARY
    company_labels: PastLabelSummary = EMPTY_SUMMARY


@dataclass(frozen=True, kw_only=True)
class ConditionProgress:
    """**지금 걸어 둔 조건 안에서의** 확정 진행 상황.

    상단의 전체 진행률과 다릅니다. 미확정만 걸러 보고 있을 때 "전체 1,740 /
    2,292" 만 보이면 지금 남은 일이 얼마인지 알 수 없기 때문입니다.

    .. warning::
        ⛔ **평가 기준이 아닙니다.** "50% 미만이면 위험" 같은 판정을 하지
        않으며, 그런 값을 담는 필드도 두지 않았습니다. 현황을 세어 보여줄
        뿐입니다.

    Attributes:
        total: 조건에 맞는 건수.
        confirmed: 그중 담당자가 확정한 건수.
    """

    total: int = 0
    confirmed: int = 0

    @property
    def pending(self) -> int:
        """조건 안에서 아직 확정하지 않은 건수(재검토 포함)."""
        return self.total - self.confirmed

    @property
    def ratio(self) -> Decimal:
        """확정 비율(%). 조건에 맞는 건이 없으면 0."""
        if self.total <= 0:
            return Decimal("0.00")
        return (Decimal(self.confirmed) / Decimal(self.total) * 100).quantize(Decimal("0.01"))


def _condition_progress(targets: Sequence[ReviewTarget]) -> ConditionProgress:
    """조건에 맞는 목록에서 확정 건수를 셉니다."""
    return ConditionProgress(
        total=len(targets),
        confirmed=sum(1 for target in targets if target.review.review_status == CONFIRMED),
    )


@dataclass(frozen=True, kw_only=True)
class ReviewPage:
    """조건에 맞는 한 페이지.

    Attributes:
        items: 이 페이지의 항목.
        page: 페이지 상태(현재 페이지 · 크기 · 전체 건수).
        condition: 조건 안에서의 확정 진행 상황.
    """

    items: list[ReviewTarget]
    page: PageInfo
    condition: ConditionProgress = ConditionProgress()


def keeps_batch(purchase: Purchase, batch_id: int | None) -> bool:
    """기간(=배치) 조건에 맞는 구매인가.

    ⚠️ **검토 조회의 모든 경로가 이 함수 하나를 씁니다.** 조건을 경로마다 따로
    쓰면 어느 한쪽이 빠지고, 담당자는 **거르지 않은 목록을 걸러진 것으로**
    보게 됩니다(STEP 20 에서 실제로 발견된 문제).

    ⛔ 날짜를 다시 계산하지 않고 배치로만 좁힙니다 — 어느 날짜로 기간을 나눌지는
    아직 확정되지 않은 업무규칙입니다(D-24).

    ⛔ 대체된(SUPERSEDED) 배치를 여기서 판단하지 않습니다. 애초에
    ``find_for_review`` 가 현재 배치의 구매만 주므로, 대체된 배치 ID 로
    물으면 맞는 구매가 없어 자연히 0건이 됩니다.

    Args:
        purchase: 대상 구매.
        batch_id: 배치 ID. ``None`` 이면 조건 없음이므로 모두 통과합니다.
    """
    return batch_id is None or purchase.batch_id == batch_id


def _keeps(target: ReviewTarget, query: ReviewQuery) -> bool:
    """조건에 맞는 항목인가.

    ⛔ 값을 바꾸지 않고 **보여줄지 말지**만 정합니다.
    """
    review = target.review

    if not keeps_batch(target.purchase, query.batch_id):
        return False

    if query.search:
        # 고객은 결의번호가 없어 **적요 + 업체명 또는 사업자등록번호 + 금액**을
        # 맞대어 지출결의서를 찾는다고 답했습니다(2026-08-31 · Q5-3). 식별값을
        # 한 칸에서 함께 찾습니다 — 담당자가 어느 칸에 넣을지 고르지 않아도 되게.
        #
        # 🟢 2026-08-31 고객 최종 회신(DECISIONS §0.12.5 · Q71-C):
        #     "검토화면에서 금액, 사업자등록번호, 적요 정도는 검색기능이
        #      있으면 좋겠어."
        # 셋 중 **금액만 없었으므로** 금액을 같은 칸에 더했습니다.
        # ⛔ 고객이 말한 셋뿐입니다 — 다른 검색 조건을 함께 만들지 않았습니다.
        needle = normalize_description(query.search)
        haystacks = (
            target.purchase.description,
            target.purchase.company_name,
            target.purchase.business_no,
        )
        # 사업자등록번호는 **한 번 더** 본다. 종이(지출결의서·세금계산서)에는
        # `123-45-67890` 으로 인쇄되고 DB 에는 숫자만 저장되어, 담당자가 있는
        # 그대로 옮겨 적으면 0건이 나온다 — 그리고 0건은 "그런 거래가 없다"
        # 로 읽힌다(STEP 73 검수에서 발견).
        number = business_no_search_key(query.search)
        # 금액도 같은 이유로 **보이는 그대로**(`1,000,000원`) 받는다.
        # ⛔ 정확히 같은 금액만 찾는다 — 범위·근사 기준을 만들지 않는다.
        money = amount_search_key(query.search)
        matched = (
            any(needle in normalize_description(value) for value in haystacks)
            or (bool(number) and number in business_no_search_key(target.purchase.business_no))
            or (money is not None and money == target.purchase.amount)
        )
        if needle and not matched:
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
        # 거래처 이력 색인. 적요 색인과 **같은 지문**으로 다시 만든다.
        self._company_index: CompanyLabelIndex | None = None
        self._company_fingerprint: tuple[int, str | None] | None = None

    # ------------------------------------------------------------------
    # 조회
    # ------------------------------------------------------------------
    def list_targets(
        self,
        *,
        review_filter: str = FILTER_ALL,
        period: PeriodFilter | None = None,
        batch_id: int | None = None,
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
            batch_id: 기간(=배치) 조건. ``None`` 이면 제한 없음.

                ⚠️ :meth:`search` 경로와 **같은 조건**(:func:`keeps_batch`)을
                씁니다. 예전에는 이 경로만 배치를 보지 않아, ``batch_id`` 를
                줘도 전체가 나왔습니다(STEP 20 발견 · STEP 21 수정).
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

        purchases = self._purchase_repository.find_for_review(period)
        stored = self._review_repository.find_all()
        reviews = {review.purchase_id: review for review in stored}
        index = self._past_label_index(purchases, stored)
        # 거래처 이력도 **같은 목록**으로 만든다 — 재조회하지 않는다.
        company_index = self._company_label_index(purchases, stored)

        targets: list[ReviewTarget] = []
        for purchase in purchases:
            if purchase.purchase_id is None:
                continue
            review = reviews.get(purchase.purchase_id) or PurchaseReview(
                purchase_id=purchase.purchase_id
            )
            if not keeps_batch(purchase, batch_id):
                continue
            if not self._matches(review, review_filter):
                continue
            targets.append(
                ReviewTarget(
                    purchase=purchase,
                    review=review,
                    past_labels=index.summary_for(purchase.description),
                    company_labels=company_index.summary_for(purchase.business_no),
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
            :class:`ReviewPage` — 해당 페이지의 항목, **조건에 맞는 전체 건수**,
            그리고 **조건 안에서의 확정 진행 상황**.
        """
        targets = self.search_all(query, period=period)
        start = query.offset
        return ReviewPage(
            items=targets[start : start + query.page_size],
            page=PageInfo(page=query.page, page_size=query.page_size, total=len(targets)),
            condition=_condition_progress(targets),
        )

    def search_all(
        self, query: ReviewQuery, *, period: PeriodFilter | None = None
    ) -> list[ReviewTarget]:
        """조건에 맞는 **전부**를 반환합니다(페이지 무시).

        CSV 내보내기처럼 "지금 보고 있는 조건의 전체" 가 필요한 경우에만
        씁니다. 화면 목록은 :meth:`search` 를 써서 한 페이지만 가져갑니다.
        """
        purchases = self._purchase_repository.find_for_review(period)
        stored = self._review_repository.find_all()
        reviews = {review.purchase_id: review for review in stored}
        index = self._past_label_index(purchases, stored)
        # 거래처 이력도 **같은 목록**으로 만든다 — 재조회하지 않는다.
        company_index = self._company_label_index(purchases, stored)

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
                company_labels=company_index.summary_for(purchase.business_no),
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
            purchase=purchase,
            review=review,
            past_labels=self._past_labels_for(purchase),
            company_labels=self._company_labels_for(purchase),
        )

    def history(self, purchase_id: int) -> list[ReviewHistoryEntry]:
        """변경 이력을 시간순으로 반환합니다."""
        return self._review_repository.find_history(purchase_id)

    def history_of_batch(self, batch_id: int | None) -> list[tuple[Purchase, ReviewHistoryEntry]]:
        """한 기간(=배치)에 속한 구매들의 **변경 이력 전부**를 반환합니다.

        기간은 화면·목록·CSV 와 **같은 뜻**이어야 하므로, 여기서도 날짜를 다시
        계산하지 않고 :meth:`PurchaseRepository.find_for_review` 가 주는
        **현재 배치의 구매**만 대상으로 삼습니다. 그래서 대체된(SUPERSEDED)
        배치의 이력은 자연히 빠집니다 — 그 배치의 구매가 애초에 나오지 않기
        때문입니다.

        ⛔ 이력을 고르거나 줄이지 않습니다. 확정 → 취소 → 재확정이면 세 줄이
        그대로 나옵니다. 어떤 줄이 "진짜" 인지 판단하지 않습니다.

        Args:
            batch_id: 기간(=현재 배치) ID. ``None`` 이면 현재 배치 전부.

        Returns:
            ``(구매, 이력)`` 쌍. 구매 ID · 변경 시각 순입니다.
        """
        by_id: dict[int, Purchase] = {
            purchase.purchase_id: purchase
            for purchase in self._purchase_repository.find_for_review(None)
            if purchase.purchase_id is not None and keeps_batch(purchase, batch_id)
        }
        entries = self._review_repository.find_history_of(by_id)
        return [(by_id[entry.purchase_id], entry) for entry in entries]

    def progress(self, period: PeriodFilter | None = None) -> ReviewProgress:
        """검토 진행 상황을 집계합니다.

        분모는 **검토 대상 구매 수**입니다. 검토 행이 없는 구매도 미확정으로
        셉니다 — 그래야 "1,203 / 2,292" 처럼 실제 남은 일이 보입니다.

        Args:
            period: 기간 조건. ``None`` 이면 제한 없음.
        """
        purchases = self._purchase_repository.find_for_review(period)
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
            purchase=purchase,
            review=review,
            past_labels=self._past_labels_for(purchase),
            company_labels=self._company_labels_for(purchase),
        )

    def reopen(
        self, purchase_id: int, *, reopened_by: str | None = None, note: str | None = None
    ) -> ReviewTarget:
        """확정을 되돌려 다시 검토 상태로 만듭니다 (화면의 "확정 취소").

        .. warning::
            ⛔ **지우지 않습니다.** ``final_purchase_type`` · ``reviewed_by`` ·
            ``reviewed_at`` · 메모를 그대로 두고 **상태만** ``REOPENED`` 로
            바꿉니다. 담당자가 무엇을 골랐었는지 화면에서 계속 볼 수 있어야
            하고, 이력도 그대로 남아야 하기 때문입니다.

        .. note::
            **확정된 건만** 되돌릴 수 있습니다. 확정한 적 없는 건을 "되돌린다"
            는 것은 뜻이 통하지 않고, 허용하면 확정 이력이 없는데 상태만
            ``REOPENED`` 인 행이 생깁니다.

        Raises:
            ReviewNotFoundError: 해당 구매가 없는 경우.
            ReviewStateError: 확정된 건이 아닌 경우.
        """
        purchase = self._require_purchase(purchase_id)
        current = self._review_repository.find_by_purchase_id(purchase_id)
        if current is None or current.review_status != CONFIRMED:
            state = "검토 시작 전" if current is None else current.review_status
            raise ReviewStateError(
                f"확정된 건만 되돌릴 수 있습니다: purchase_id={purchase_id} (현재 {state})"
            )
        review = self._review_repository.reopen(purchase_id, reopened_by=reopened_by, note=note)
        return ReviewTarget(
            purchase=purchase,
            review=review,
            past_labels=self._past_labels_for(purchase),
            company_labels=self._company_labels_for(purchase),
        )

    def exclude_from_performance(
        self,
        purchase_id: int,
        *,
        reason: str,
        excluded_by: str | None = None,
        note: str | None = None,
    ) -> ReviewTarget:
        """이 구매를 **실적 계산에서 뺍니다**(2026-08-31 고객 확정 · §0.10).

        고객이 지출결의서·세금계산서·품의서를 확인한 뒤 내리는 판단을 화면에서
        확정하는 자리입니다.

        .. warning::
            ⛔ **구매유형을 건드리지 않습니다.** 유형과 실적 산입 여부는 다른
            개념이며 다른 필드에 남습니다 — "용역으로 확정했고 강사료라서
            실적에서 뺐다" 가 함께 보여야 합니다.

        .. warning::
            ⛔ **적요로 자동 판정하지 않습니다.** 이 메서드는 사람이 부를 때만
            동작합니다. 낱말을 보고 자동으로 부르는 코드를 만들지 않습니다.

        Args:
            purchase_id: DB-1 구매 ID.
            reason: 제외 사유 코드(:mod:`procurement.core.performance_exclusion`).
            excluded_by: 확정한 사람.
            note: 메모.

        Returns:
            갱신된 :class:`ReviewTarget`.

        Raises:
            ReviewNotFoundError: 해당 구매가 없는 경우.
            ExclusionReasonError: 허용되지 않는 사유 코드인 경우.
        """
        purchase = self._require_purchase(purchase_id)
        review = self._review_repository.exclude_from_performance(
            purchase_id, reason=reason, excluded_by=excluded_by, note=note
        )
        return ReviewTarget(
            purchase=purchase,
            review=review,
            past_labels=self._past_labels_for(purchase),
            company_labels=self._company_labels_for(purchase),
        )

    def include_in_performance(
        self, purchase_id: int, *, changed_by: str | None = None, note: str | None = None
    ) -> ReviewTarget:
        """실적 제외를 **되돌립니다**.

        ⛔ 제외했던 이력을 지우지 않습니다 — 되돌렸다는 사실도 이력에 남습니다.

        .. note::
            **예산과목 규칙으로 빠진 건은 되돌릴 수 없습니다.** 그것은 담당자의
            판단이 아니라 고객이 확정한 규칙이므로, 화면에서 되돌린다고 계산에
            들어오지 않습니다. 규칙을 바꾸려면 고객 확인이 필요합니다.

        Args:
            purchase_id: DB-1 구매 ID.
            changed_by: 되돌린 사람.
            note: 사유.

        Returns:
            갱신된 :class:`ReviewTarget`.

        Raises:
            ReviewNotFoundError: 해당 구매가 없는 경우.
        """
        purchase = self._require_purchase(purchase_id)
        review = self._review_repository.include_in_performance(
            purchase_id, changed_by=changed_by, note=note
        )
        return ReviewTarget(
            purchase=purchase,
            review=review,
            past_labels=self._past_labels_for(purchase),
            company_labels=self._company_labels_for(purchase),
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
            purchase=purchase,
            review=review,
            past_labels=self._past_labels_for(purchase),
            company_labels=self._company_labels_for(purchase),
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
        for purchase in self._purchase_repository.find_for_review(period):
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
            else self._purchase_repository.find_for_review(None)
        )
        states = list(reviews) if reviews is not None else self._review_repository.find_all()
        self._index = PastLabelIndex(rows, states)
        self._index_fingerprint = fingerprint
        return self._index

    def _company_label_index(
        self,
        purchases: Sequence[Purchase] | None = None,
        reviews: Sequence[PurchaseReview] | None = None,
    ) -> CompanyLabelIndex:
        """거래처 이력 색인. **적요 색인과 같은 방식**으로 만들고 캐시합니다.

        모집단·확정 기준·갱신 판단(``confirmed_fingerprint``)이 모두
        :meth:`_past_label_index` 와 같습니다. 두 블록이 다른 기준으로 세면
        화면의 숫자가 서로 어긋납니다.

        Args:
            purchases: 이미 읽어 둔 구매 목록. 있으면 재조회하지 않습니다.
            reviews: 이미 읽어 둔 검토 목록. 〃

        Returns:
            :class:`~procurement.reviews.company_labels.CompanyLabelIndex`.
        """
        fingerprint = self._review_repository.confirmed_fingerprint()
        if self._company_index is not None and self._company_fingerprint == fingerprint:
            return self._company_index

        rows = (
            list(purchases)
            if purchases is not None
            else self._purchase_repository.find_for_review(None)
        )
        states = list(reviews) if reviews is not None else self._review_repository.find_all()
        self._company_index = CompanyLabelIndex(rows, states)
        self._company_fingerprint = fingerprint
        return self._company_index

    def _past_labels_for(self, purchase: Purchase) -> PastLabelSummary:
        """한 건에 대한 과거 확정 이력.

        ⛔ 자기 자신의 확정도 이력에 포함됩니다 — 화면에서 "이 적요는 지금까지
        용역 3건으로 확정됨" 을 그대로 보여주는 것이 목적이기 때문입니다.
        """
        return self._past_label_index().summary_for(purchase.description)

    def _company_labels_for(self, purchase: Purchase) -> PastLabelSummary:
        """한 건에 대한 **같은 업체**의 과거 확정 이력.

        묶는 키는 **사업자등록번호**입니다(2026-08-30 고객 확정 · §0.9.5 원칙
        4). 거래처명 표기가 달라도 번호가 같으면 한 업체로 셉니다.

        ⛔ 자기 자신의 확정도 이력에 포함됩니다 — :meth:`_past_labels_for`
        와 **같은 규칙**입니다. 한쪽만 제외하면 두 블록의 숫자가 서로
        어긋나 담당자가 어느 쪽을 믿어야 할지 알 수 없게 됩니다.
        """
        return self._company_label_index().summary_for(purchase.business_no)

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
