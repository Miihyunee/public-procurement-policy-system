"""STEP 9 — 연속 검토 업무 흐름.

담당자가 여러 건을 이어서 검토할 때 필요한 것들:

1. **변경 이력** — 누가 · 언제 · 무엇에서 무엇으로
2. **현재 조건 진행률** — 전체 진행률과 **다른 값**
3. **상태 변경 후 목록 일관성** — 확정하면 미확정 목록에서 빠진다
4. **다음 대상** — 현재 정렬 순서 그대로 다음 칸

⛔ 어느 것도 유형을 자동으로 고르거나 확정하지 않습니다.

⚠️ 데이터는 전부 **합성**입니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from procurement.core.purchase_type import CONSTRUCTION, GOODS, SERVICE
from procurement.database.bootstrap import init_db
from procurement.database.purchase_repository import PurchaseRepository
from procurement.database.review_repository import ReviewRepository
from procurement.models.classification import ANALYZED, ClassificationResult, TypeCandidate
from procurement.models.purchase import Purchase
from procurement.models.review import (
    ACTION_ANALYZED,
    ACTION_CONFIRMED,
    ACTION_REOPENED,
    CONFIRMED,
    PENDING,
    REOPENED,
)
from procurement.reviews.query import (
    DECIDED,
    DESCENDING,
    UNDECIDED,
    ReviewQuery,
)
from procurement.reviews.review_service import ConditionProgress, ReviewService


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "workflow.db")
    init_db(path)
    return path


@pytest.fixture
def purchases(db_path: str) -> PurchaseRepository:
    return PurchaseRepository(db_path)


@pytest.fixture
def reviews(db_path: str) -> ReviewRepository:
    return ReviewRepository(db_path)


@pytest.fixture
def service(purchases: PurchaseRepository, reviews: ReviewRepository) -> ReviewService:
    return ReviewService(purchases, reviews)


def add(repository: PurchaseRepository, description: str, *, amount: str = "1000000") -> int:
    purchase = repository.insert(
        Purchase(
            business_no="111-11-11111",
            company_name="가나건설",
            contract_date=date(2026, 3, 1),
            payment_date=date(2026, 3, 20),
            amount=Decimal(amount),
            resolution_date=date(2026, 3, 25),
            issue_date=date(2026, 3, 10),
            description=description,
            budget_account="외주용역비",
        )
    )
    assert purchase.purchase_id is not None
    return purchase.purchase_id


def analysis(*pairs: tuple[str, str]) -> ClassificationResult:
    return ClassificationResult(
        candidates=[
            TypeCandidate(purchase_type=label, score=Decimal(score), evidence="합성 근거")
            for label, score in pairs
        ],
        analyzer_name="bm25",
        analyzer_version="1",
        status=ANALYZED,
    )


def ids(service: ReviewService, **kwargs: object) -> list[int]:
    page = service.search(ReviewQuery(**kwargs))  # type: ignore[arg-type]
    return [target.purchase.purchase_id or 0 for target in page.items]


class TestHistory:
    """작업 A — 변경 이력 (지시 G-1)."""

    def test_no_history_yet(self, service: ReviewService, purchases: PurchaseRepository) -> None:
        """아직 아무것도 안 한 건은 이력이 비어 있다 — 오류가 아니다."""
        purchase_id = add(purchases, "손대지 않은 적요")

        assert service.history(purchase_id) == []

    def test_single_entry(self, service: ReviewService, purchases: PurchaseRepository) -> None:
        purchase_id = add(purchases, "확정만 한 적요")
        service.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")

        entries = service.history(purchase_id)

        assert len(entries) == 1
        assert entries[0].action == ACTION_CONFIRMED
        assert entries[0].changed_by == "김담당"

    def test_full_lifecycle_is_recorded_in_order(
        self, service: ReviewService, purchases: PurchaseRepository, reviews: ReviewRepository
    ) -> None:
        """ANALYZED → CONFIRMED → REOPENED → CONFIRMED 가 시간순으로 남는다."""
        purchase_id = add(purchases, "여러 번 만진 적요")
        reviews.save_analysis(purchase_id, analysis((SERVICE, "0.90")))
        service.confirm(purchase_id, final_purchase_type=CONSTRUCTION, reviewed_by="김담당")
        service.reopen(purchase_id, reopened_by="이담당", note="다시 봄")
        service.confirm(purchase_id, final_purchase_type=GOODS, reviewed_by="이담당")

        actions = [entry.action for entry in service.history(purchase_id)]

        assert actions == [
            ACTION_ANALYZED,
            ACTION_CONFIRMED,
            ACTION_REOPENED,
            ACTION_CONFIRMED,
        ]

    def test_before_and_after_types(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """무엇에서 무엇으로 바뀌었는지 각 줄에 남는다."""
        purchase_id = add(purchases, "유형이 바뀐 적요")
        service.confirm(purchase_id, final_purchase_type=CONSTRUCTION, reviewed_by="김담당")
        service.reopen(purchase_id, reopened_by="이담당")
        service.confirm(purchase_id, final_purchase_type=GOODS, reviewed_by="이담당")

        entries = service.history(purchase_id)

        assert (entries[0].before_type, entries[0].after_type) == (None, CONSTRUCTION)
        assert (entries[-1].before_type, entries[-1].after_type) == (CONSTRUCTION, GOODS)

    def test_history_does_not_change_the_current_value(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """⛔ 이력을 조회해도 현재 확정값이 흔들리지 않는다."""
        purchase_id = add(purchases, "적요")
        service.confirm(purchase_id, final_purchase_type=GOODS, reviewed_by="김담당")

        service.history(purchase_id)
        current = service.get_target(purchase_id)

        assert current.review.final_purchase_type == GOODS
        assert current.review.review_status == CONFIRMED

    def test_current_value_differs_from_older_entries(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """지금 값은 **마지막 상태**이지, 이력의 아무 줄이 아니다.

        화면이 이력의 첫 줄을 현재값으로 착각하면 "공사로 확정됨" 이라고
        잘못 보여주게 된다.
        """
        purchase_id = add(purchases, "적요")
        service.confirm(purchase_id, final_purchase_type=CONSTRUCTION, reviewed_by="김담당")
        service.reopen(purchase_id, reopened_by="이담당")
        service.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="이담당")

        entries = service.history(purchase_id)
        current = service.get_target(purchase_id).review

        assert entries[0].after_type == CONSTRUCTION  # 과거
        assert current.final_purchase_type == SERVICE  # 지금

    def test_hold_is_recorded_as_none(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        purchase_id = add(purchases, "보류한 적요")
        service.confirm(purchase_id, final_purchase_type=None, reviewed_by="김담당")

        assert service.history(purchase_id)[-1].after_type is None


class TestConditionProgress:
    """작업 D — 현재 조건 진행률 (지시 G-4)."""

    def test_empty(self) -> None:
        progress = ConditionProgress()

        assert (progress.total, progress.confirmed, progress.pending) == (0, 0, 0)
        assert progress.ratio == Decimal("0.00")

    def test_ratio(self) -> None:
        progress = ConditionProgress(total=46, confirmed=31)

        assert progress.pending == 15
        assert progress.ratio == Decimal("67.39")

    def test_full_condition_equals_overall(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """조건이 없으면 조건 진행률 = 전체 진행률."""
        first = add(purchases, "가")
        add(purchases, "나")
        service.confirm(first, final_purchase_type=SERVICE, reviewed_by="김담당")

        page = service.search(ReviewQuery())
        overall = service.progress()

        assert (page.condition.total, page.condition.confirmed) == (
            overall.total,
            overall.confirmed,
        )

    def test_filtered_condition_differs(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """확정 필터를 걸면 조건 진행률은 100% 가 된다 — 전체와 다른 값."""
        first = add(purchases, "가")
        add(purchases, "나")
        add(purchases, "다")
        service.confirm(first, final_purchase_type=SERVICE, reviewed_by="김담당")

        page = service.search(ReviewQuery(decision=DECIDED))

        assert (page.condition.total, page.condition.confirmed) == (1, 1)
        assert page.condition.ratio == Decimal("100.00")
        assert service.progress().total == 3  # 전체는 그대로

    def test_undecided_filter_shows_zero_confirmed(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """미확정만 보고 있으면 조건 안 확정은 0 이다 — 필터의 성질상 당연하다."""
        first = add(purchases, "가")
        add(purchases, "나")
        service.confirm(first, final_purchase_type=SERVICE, reviewed_by="김담당")

        page = service.search(ReviewQuery(decision=UNDECIDED))

        assert (page.condition.total, page.condition.confirmed) == (1, 0)

    def test_zero_matches(self, service: ReviewService, purchases: PurchaseRepository) -> None:
        add(purchases, "가")

        page = service.search(ReviewQuery(search="없는 적요"))

        assert page.condition.total == 0
        assert page.condition.ratio == Decimal("0.00")

    def test_progress_updates_after_confirming(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        purchase_id = add(purchases, "가")
        add(purchases, "나")
        assert service.search(ReviewQuery()).condition.confirmed == 0

        service.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")

        assert service.search(ReviewQuery()).condition.confirmed == 1

    def test_progress_is_only_a_count(self) -> None:
        """⛔ 위험/적정 같은 판정 필드가 없어야 한다."""
        names = {
            name.lower()
            for name in list(ConditionProgress.__dataclass_fields__) + list(vars(ConditionProgress))
        }

        for banned in ("risk", "level", "grade", "status", "healthy", "warning"):
            assert banned not in names, banned


class TestListStaysConsistentAfterStateChange:
    """작업 F — 상태를 바꾸면 목록도 따라 바뀐다 (지시 G-2 일부)."""

    def test_confirmed_item_leaves_the_undecided_list(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        first = add(purchases, "가")
        second = add(purchases, "나")
        assert ids(service, decision=UNDECIDED) == [first, second]

        service.confirm(first, final_purchase_type=SERVICE, reviewed_by="김담당")

        assert ids(service, decision=UNDECIDED) == [second]

    def test_reopened_item_leaves_the_confirmed_list(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        purchase_id = add(purchases, "가")
        service.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")
        assert ids(service, status=CONFIRMED) == [purchase_id]

        service.reopen(purchase_id, reopened_by="이담당")

        assert ids(service, status=CONFIRMED) == []
        assert ids(service, status=REOPENED) == [purchase_id]

    def test_condition_total_shrinks_as_you_work(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """미확정만 보며 연속 확정하면 남은 건수가 줄어든다."""
        made = [add(purchases, f"적요 {index}") for index in range(3)]

        totals = []
        for purchase_id in made:
            totals.append(service.search(ReviewQuery(decision=UNDECIDED)).condition.total)
            service.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")
        totals.append(service.search(ReviewQuery(decision=UNDECIDED)).condition.total)

        assert totals == [3, 2, 1, 0]

    def test_emptied_list_is_not_an_error(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """마지막 건을 확정하면 목록이 빈다 — 오류가 아니라 '할 일 없음'."""
        purchase_id = add(purchases, "마지막 적요")
        service.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")

        page = service.search(ReviewQuery(decision=UNDECIDED))

        assert page.items == []
        assert page.page.total == 0
        assert page.page.total_pages == 1


class TestNextTargetOrdering:
    """작업 B — '다음 대상' 은 **현재 정렬 순서 그대로** (지시 G-2)."""

    @pytest.fixture(autouse=True)
    def seed(self, purchases: PurchaseRepository) -> None:
        self.cheap = add(purchases, "가 적요", amount="100")
        self.middle = add(purchases, "나 적요", amount="5000")
        self.pricey = add(purchases, "다 적요", amount="900000")

    def test_order_follows_the_chosen_sort(self, service: ReviewService) -> None:
        """⛔ 새 우선순위를 계산하지 않는다. 담당자가 고른 정렬 그대로."""
        assert ids(service, sort="amount") == [self.cheap, self.middle, self.pricey]
        assert ids(service, sort="amount", direction=DESCENDING) == [
            self.pricey,
            self.middle,
            self.cheap,
        ]

    def test_confirming_keeps_the_rest_in_order(self, service: ReviewService) -> None:
        """한 건을 확정해도 남은 것들의 순서는 그대로다."""
        service.confirm(self.middle, final_purchase_type=SERVICE, reviewed_by="김담당")

        assert ids(service, sort="amount", decision=UNDECIDED) == [self.cheap, self.pricey]

    def test_search_and_sort_survive_paging(self, service: ReviewService) -> None:
        """쪽을 넘겨도 검색·정렬이 유지된다."""
        first = service.search(ReviewQuery(search="적요", sort="amount", page=1, page_size=2))
        second = service.search(ReviewQuery(search="적요", sort="amount", page=2, page_size=2))

        assert [t.purchase.purchase_id for t in first.items] == [self.cheap, self.middle]
        assert [t.purchase.purchase_id for t in second.items] == [self.pricey]
        assert first.page.total == second.page.total == 3

    def test_page_size_is_respected(self, service: ReviewService) -> None:
        assert len(service.search(ReviewQuery(page_size=2)).items) == 2

    def test_no_next_page_at_the_end(self, service: ReviewService) -> None:
        """마지막 쪽에서는 '다음' 이 없다고 알려 준다 — 화면이 이걸로 멈춘다."""
        last = service.search(ReviewQuery(page=2, page_size=2))

        assert not last.page.has_next
        assert last.page.has_previous


class TestContinuousReviewDoesNotAutoDecide:
    """⛔ 연속 검토가 자동 판정으로 새지 않는다."""

    def test_moving_on_does_not_touch_the_next_item(
        self, service: ReviewService, purchases: PurchaseRepository, reviews: ReviewRepository
    ) -> None:
        """앞 건을 확정해도 **다음 건은 손대지 않은 상태** 그대로다."""
        first = add(purchases, "앞 건")
        second = add(purchases, "뒤 건")
        reviews.save_analysis(second, analysis((SERVICE, "1.00")))

        service.confirm(first, final_purchase_type=CONSTRUCTION, reviewed_by="김담당")

        after = service.get_target(second).review
        assert after.review_status == PENDING
        assert after.final_purchase_type is None
        assert after.reviewed_by is None

    def test_history_of_the_next_item_stays_empty(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        first = add(purchases, "같은 적요")
        second = add(purchases, "같은 적요")

        service.confirm(first, final_purchase_type=SERVICE, reviewed_by="김담당")

        # 과거 이력은 보이지만, 그것이 확정으로 옮겨가지 않는다.
        target = service.get_target(second)
        assert target.past_labels.total == 1
        assert target.review.final_purchase_type is None
        assert service.history(second) == []
