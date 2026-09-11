"""STEP 8 — 검색 · 필터 · 정렬 · 페이지.

담당자가 **직접 고른 조건**으로 목록을 좁혀 보는 기능입니다.

⛔ 여기 있는 어떤 조건도 값을 바꾸지 않고, "먼저 보라" 고 정하지도 않습니다.

⚠️ 데이터는 전부 **합성**입니다. 실제 거래처명·사업자번호를 쓰지 않습니다.
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
from procurement.models.review import CONFIRMED, PENDING, REOPENED
from procurement.reviews.query import (
    ANY,
    ASCENDING,
    DECIDED,
    DEFAULT_PAGE_SIZE,
    DESCENDING,
    HAS_HISTORY,
    HISTORY_AGREES,
    HISTORY_DIFFERS,
    HISTORY_MIXED,
    MANY_CANDIDATES,
    MAX_PAGE_SIZE,
    NO_CANDIDATE,
    NO_HISTORY_ONLY,
    ONE_CANDIDATE,
    SORT_KEYS,
    UNDECIDED,
    PageInfo,
    ReviewQuery,
    ReviewQueryError,
)
from procurement.reviews.review_service import ReviewService


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "query.db")
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


def add(
    repository: PurchaseRepository,
    description: str,
    *,
    amount: str = "1000000",
    month: int = 3,
) -> int:
    """합성 구매 1건."""
    purchase = repository.insert(
        Purchase(
            business_no="111-11-11111",
            company_name="가나건설",
            contract_date=date(2026, month, 1),
            payment_date=date(2026, month, 20),
            amount=Decimal(amount),
            resolution_date=date(2026, month, 25),
            issue_date=date(2026, month, 10),
            description=description,
            budget_account="외주용역비",
        )
    )
    assert purchase.purchase_id is not None
    return purchase.purchase_id


def analyze(*pairs: tuple[str, str]) -> ClassificationResult:
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
    """조건에 맞는 구매 ID 목록(페이지 적용)."""
    page = service.search(ReviewQuery(**kwargs))  # type: ignore[arg-type]
    return [target.purchase.purchase_id or 0 for target in page.items]


class TestQueryValidation:
    """허용값을 벗어나면 **조용히 넘어가지 않는다.**"""

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("status", "WRONG"),
            ("decision", "WRONG"),
            ("history", "WRONG"),
            ("candidates", "WRONG"),
            ("sort", "wrong"),
            ("direction", "sideways"),
        ],
    )
    def test_unknown_value_is_rejected(self, field: str, value: str) -> None:
        """기본값으로 되돌리면 담당자가 **고른 것과 다른 목록**을 본다."""
        with pytest.raises(ReviewQueryError):
            ReviewQuery(**{field: value})  # type: ignore[arg-type]

    @pytest.mark.parametrize("page", [0, -1])
    def test_page_must_be_positive(self, page: int) -> None:
        with pytest.raises(ReviewQueryError, match="페이지는 1 이상"):
            ReviewQuery(page=page)

    @pytest.mark.parametrize("size", [0, -5, MAX_PAGE_SIZE + 1])
    def test_page_size_is_bounded(self, size: int) -> None:
        """상한이 없으면 실수로 전체를 끌어올 수 있다."""
        with pytest.raises(ReviewQueryError, match="페이지 크기"):
            ReviewQuery(page_size=size)

    def test_defaults_are_permissive(self) -> None:
        query = ReviewQuery()

        assert query.status == query.decision == query.history == query.candidates == ANY
        assert query.page == 1
        assert query.page_size == DEFAULT_PAGE_SIZE
        assert not query.ambiguous_only

    def test_offset_follows_the_page(self) -> None:
        assert ReviewQuery(page=1, page_size=20).offset == 0
        assert ReviewQuery(page=3, page_size=20).offset == 40


class TestSearch:
    """적요 검색."""

    @pytest.fixture(autouse=True)
    def seed(self, purchases: PurchaseRepository) -> None:
        add(purchases, "복합기 토너 및 사무실 청소")
        add(purchases, "LED 등기구 교체공사")
        add(purchases, "청소 용역 대금")

    def test_no_search_returns_everything(self, service: ReviewService) -> None:
        assert len(ids(service)) == 3

    def test_partial_match(self, service: ReviewService) -> None:
        assert len(ids(service, search="청소")) == 2

    def test_spacing_is_ignored(self, service: ReviewService) -> None:
        """실데이터의 적요는 띄어쓰기가 일정하지 않다."""
        assert ids(service, search="사무실청소") == ids(service, search="사무실 청소")

    def test_case_is_ignored(self, service: ReviewService) -> None:
        assert len(ids(service, search="led")) == 1

    def test_no_match_is_empty_not_an_error(self, service: ReviewService) -> None:
        assert ids(service, search="존재하지 않는 적요") == []

    @pytest.mark.parametrize("needle", ["—", "%", "'", '"', "()", "\\", "*"])
    def test_special_characters_are_literal(self, service: ReviewService, needle: str) -> None:
        """⛔ 와일드카드로 해석하지 않는다 — ``%`` 가 전체를 부르면 안 된다."""
        assert ids(service, search=needle) == []

    def test_search_does_not_change_any_value(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        before = [(p.purchase_id, p.description) for p in purchases.find_for_calculation(None)]
        ids(service, search="청소")

        assert [
            (p.purchase_id, p.description) for p in purchases.find_for_calculation(None)
        ] == before


class TestStatusAndDecisionFilters:
    """상태 · 확정 여부."""

    @pytest.fixture(autouse=True)
    def seed(self, purchases: PurchaseRepository, reviews: ReviewRepository) -> None:
        self.pending = add(purchases, "미확정 적요")
        self.confirmed = add(purchases, "확정 적요")
        self.reopened = add(purchases, "재검토 적요")
        reviews.confirm(self.confirmed, final_purchase_type=SERVICE, reviewed_by="김담당")
        reviews.confirm(self.reopened, final_purchase_type=GOODS, reviewed_by="김담당")
        reviews.reopen(self.reopened, reopened_by="이담당")

    def test_status_pending(self, service: ReviewService) -> None:
        assert ids(service, status=PENDING) == [self.pending]

    def test_status_confirmed(self, service: ReviewService) -> None:
        assert ids(service, status=CONFIRMED) == [self.confirmed]

    def test_status_reopened(self, service: ReviewService) -> None:
        assert ids(service, status=REOPENED) == [self.reopened]

    def test_decided_means_confirmed_only(self, service: ReviewService) -> None:
        assert ids(service, decision=DECIDED) == [self.confirmed]

    def test_undecided_includes_reopened(self, service: ReviewService) -> None:
        """재검토 중인 건은 **아직 확정되지 않은** 것이다."""
        assert ids(service, decision=UNDECIDED) == [self.pending, self.reopened]


class TestCandidateFilter:
    """후보 수."""

    @pytest.fixture(autouse=True)
    def seed(self, purchases: PurchaseRepository, reviews: ReviewRepository) -> None:
        self.none = add(purchases, "후보 없는 적요")
        self.one = add(purchases, "후보 하나인 적요")
        self.many = add(purchases, "후보 여럿인 적요")
        reviews.save_analysis(self.one, analyze((SERVICE, "0.90")))
        reviews.save_analysis(self.many, analyze((SERVICE, "0.52"), (CONSTRUCTION, "0.48")))

    def test_no_candidate(self, service: ReviewService) -> None:
        """분석하지 않은 건과 후보 0건이 함께 잡힌다 — 둘 다 '판단 재료 없음'."""
        assert ids(service, candidates=NO_CANDIDATE) == [self.none]

    def test_exactly_one(self, service: ReviewService) -> None:
        assert ids(service, candidates=ONE_CANDIDATE) == [self.one]

    def test_two_or_more(self, service: ReviewService) -> None:
        assert ids(service, candidates=MANY_CANDIDATES) == [self.many]

    def test_ambiguous_only(self, service: ReviewService) -> None:
        assert ids(service, ambiguous_only=True) == [self.many]


class TestHistoryFilter:
    """과거 확정 이력."""

    @pytest.fixture(autouse=True)
    def seed(self, purchases: PurchaseRepository, reviews: ReviewRepository) -> None:
        # 과거에 용역으로만 확정된 적요
        seed_one = add(purchases, "일관된 적요")
        reviews.confirm(seed_one, final_purchase_type=SERVICE, reviewed_by="김담당")
        self.agrees = add(purchases, "일관된 적요")
        reviews.save_analysis(self.agrees, analyze((SERVICE, "0.90")))

        # 과거와 다른 1순위
        self.differs = add(purchases, "일관된 적요")
        reviews.save_analysis(self.differs, analyze((GOODS, "0.90")))

        # 과거가 갈린 적요
        mixed_a = add(purchases, "갈린 적요")
        mixed_b = add(purchases, "갈린 적요")
        reviews.confirm(mixed_a, final_purchase_type=SERVICE, reviewed_by="김담당")
        reviews.confirm(mixed_b, final_purchase_type=GOODS, reviewed_by="이담당")
        self.mixed = add(purchases, "갈린 적요")

        # 이력이 전혀 없는 적요
        self.fresh = add(purchases, "처음 보는 적요")

    def test_has_history(self, service: ReviewService) -> None:
        assert self.fresh not in ids(service, history=HAS_HISTORY, page_size=50)

    def test_no_history(self, service: ReviewService) -> None:
        assert ids(service, history=NO_HISTORY_ONLY) == [self.fresh]

    def test_mixed_history(self, service: ReviewService) -> None:
        found = ids(service, history=HISTORY_MIXED, page_size=50)

        assert self.mixed in found
        assert self.agrees not in found

    def test_agrees_with_the_past(self, service: ReviewService) -> None:
        assert self.agrees in ids(service, history=HISTORY_AGREES, page_size=50)
        assert self.differs not in ids(service, history=HISTORY_AGREES, page_size=50)

    def test_differs_from_the_past(self, service: ReviewService) -> None:
        assert ids(service, history=HISTORY_DIFFERS, page_size=50) == [self.differs]

    def test_comparison_filters_need_both_sides(self, service: ReviewService) -> None:
        """후보가 없거나 이력이 없으면 '같다/다르다' 를 말할 수 없다."""
        for history in (HISTORY_AGREES, HISTORY_DIFFERS):
            assert self.fresh not in ids(service, history=history, page_size=50)


class TestCombinedFilters:
    """복합 조건."""

    @pytest.fixture(autouse=True)
    def seed(self, purchases: PurchaseRepository, reviews: ReviewRepository) -> None:
        self.wanted = add(purchases, "청소 용역 대금")
        reviews.save_analysis(self.wanted, analyze((SERVICE, "0.52"), (GOODS, "0.48")))
        other = add(purchases, "청소 용역 대금")
        reviews.save_analysis(other, analyze((SERVICE, "0.90")))
        reviews.confirm(other, final_purchase_type=SERVICE, reviewed_by="김담당")
        add(purchases, "관계없는 적요")

    def test_search_plus_undecided_plus_many(self, service: ReviewService) -> None:
        assert ids(service, search="청소", decision=UNDECIDED, candidates=MANY_CANDIDATES) == [
            self.wanted
        ]

    def test_conflicting_filters_yield_nothing(self, service: ReviewService) -> None:
        assert ids(service, candidates=NO_CANDIDATE, ambiguous_only=True) == []


class TestSorting:
    """정렬 — 담당자가 축을 고른다."""

    @pytest.fixture(autouse=True)
    def seed(self, purchases: PurchaseRepository, reviews: ReviewRepository) -> None:
        self.cheap = add(purchases, "가 적요", amount="100", month=1)
        self.pricey = add(purchases, "나 적요", amount="900000", month=6)
        self.middle = add(purchases, "다 적요", amount="5000", month=3)
        # 점수차: cheap 만 후보 2개 → 나머지는 '값 없음'
        reviews.save_analysis(self.cheap, analyze((SERVICE, "0.90"), (GOODS, "0.10")))
        reviews.save_analysis(self.middle, analyze((SERVICE, "0.90")))

    def test_amount_ascending(self, service: ReviewService) -> None:
        assert ids(service, sort="amount") == [self.cheap, self.middle, self.pricey]

    def test_amount_descending(self, service: ReviewService) -> None:
        assert ids(service, sort="amount", direction=DESCENDING) == [
            self.pricey,
            self.middle,
            self.cheap,
        ]

    def test_date_ascending(self, service: ReviewService) -> None:
        assert ids(service, sort="resolution_date") == [
            self.cheap,
            self.middle,
            self.pricey,
        ]

    def test_description_ascending(self, service: ReviewService) -> None:
        assert ids(service, sort="description") == [self.cheap, self.pricey, self.middle]

    def test_missing_values_stay_last_ascending(self, service: ReviewService) -> None:
        """점수차가 **없는** 건은 뒤에 온다."""
        assert ids(service, sort="score_gap")[0] == self.cheap

    def test_missing_values_stay_last_descending_too(self, service: ReviewService) -> None:
        """⛔ 방향을 바꿨다고 빈 값이 앞으로 몰려오면 안 된다.

        한 번에 ``reverse=True`` 로 정렬하면 '값 없음' 표식까지 뒤집혀
        빈 값이 맨 앞에 온다. 그 회귀를 여기서 막는다.
        """
        order = ids(service, sort="score_gap", direction=DESCENDING)

        assert order[0] == self.cheap
        assert set(order[1:]) == {self.middle, self.pricey}

    def test_every_declared_key_works(self, service: ReviewService) -> None:
        """선택지로 내놓은 정렬 기준은 전부 동작해야 한다."""
        for key in SORT_KEYS:
            for direction in (ASCENDING, DESCENDING):
                assert len(ids(service, sort=key, direction=direction)) == 3

    def test_order_is_stable(self, service: ReviewService) -> None:
        """같은 값끼리는 늘 같은 순서 — 새로고침할 때마다 뒤바뀌면 못 쓴다."""
        first = ids(service, sort="status")
        assert first == ids(service, sort="status")


class TestPagination:
    """페이지."""

    @pytest.fixture(autouse=True)
    def seed(self, purchases: PurchaseRepository) -> None:
        self.all_ids = [add(purchases, f"적요 {index:02d}") for index in range(10)]

    def test_first_page(self, service: ReviewService) -> None:
        page = service.search(ReviewQuery(page=1, page_size=4))

        assert [t.purchase.purchase_id for t in page.items] == self.all_ids[:4]
        assert (page.page.total, page.page.total_pages) == (10, 3)
        assert not page.page.has_previous
        assert page.page.has_next

    def test_middle_page(self, service: ReviewService) -> None:
        page = service.search(ReviewQuery(page=2, page_size=4))

        assert [t.purchase.purchase_id for t in page.items] == self.all_ids[4:8]
        assert page.page.has_previous and page.page.has_next

    def test_last_page_is_partial(self, service: ReviewService) -> None:
        page = service.search(ReviewQuery(page=3, page_size=4))

        assert [t.purchase.purchase_id for t in page.items] == self.all_ids[8:]
        assert page.page.has_previous
        assert not page.page.has_next

    def test_exact_boundary(self, service: ReviewService) -> None:
        """건수가 페이지 크기로 딱 나누어떨어지면 빈 페이지를 만들지 않는다."""
        page = service.search(ReviewQuery(page=2, page_size=5))

        assert len(page.items) == 5
        assert page.page.total_pages == 2
        assert not page.page.has_next

    def test_page_past_the_end_is_empty(self, service: ReviewService) -> None:
        page = service.search(ReviewQuery(page=99, page_size=4))

        assert page.items == []
        assert page.page.total == 10

    def test_total_counts_matches_not_page(self, service: ReviewService) -> None:
        """``total`` 은 **조건에 맞는 전체**이지 이 페이지의 수가 아니다.

        화면이 "1 / 4 쪽 · 전체 10건" 을 그리려면 페이지 밖의 수를 알아야
        한다.
        """
        # '적요 05' 는 하나뿐이므로 조건에 맞는 전체가 1건이다.
        narrow = service.search(ReviewQuery(page=1, page_size=3, search="적요 05"))
        assert (len(narrow.items), narrow.page.total) == (1, 1)

        # 조건에 맞는 것이 10건이면, 3건만 담겨도 total 은 10 이어야 한다.
        wide = service.search(ReviewQuery(page=1, page_size=3, search="적요"))
        assert len(wide.items) == 3
        assert wide.page.total == 10
        assert wide.page.total_pages == 4

    def test_filtered_result_is_paged_too(self, service: ReviewService) -> None:
        page = service.search(ReviewQuery(page=1, page_size=2, search="적요 0"))

        assert len(page.items) == 2
        assert page.page.total_pages >= 1


class TestEmptyPageInfo:
    """결과가 0건일 때."""

    def test_zero_results_still_have_one_page(self) -> None:
        """0 쪽이라고 하면 화면이 '0 / 0 쪽' 을 그린다."""
        info = PageInfo(page=1, page_size=20, total=0)

        assert info.total_pages == 1
        assert not info.has_previous
        assert not info.has_next

    def test_rounding_up(self) -> None:
        assert PageInfo(page=1, page_size=20, total=21).total_pages == 2
        assert PageInfo(page=1, page_size=20, total=40).total_pages == 2
        assert PageInfo(page=1, page_size=20, total=41).total_pages == 3


class TestSearchAllIgnoresPaging:
    """CSV 내보내기용 — 조건은 같되 페이지는 무시."""

    def test_returns_everything_matching(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        for index in range(7):
            add(purchases, f"적요 {index}")

        found = service.search_all(ReviewQuery(page=1, page_size=2))

        assert len(found) == 7
