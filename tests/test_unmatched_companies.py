"""STEP 38 — 미매칭 기업 집계(사업자등록번호별).

대시보드는 "기업 미매칭 N건" 이라는 **총계**만 보여 주었습니다. 그 숫자만으로는
담당자가 **어느 기업정보를 먼저 확보해야 하는지** 알 수 없어, 같은 사실을
사업자번호 단위로 접어 금액 비중과 함께 보여줍니다.

.. warning::
    ⛔ **읽기 전용입니다.** 기업·인증·구매 어느 것도 만들거나 바꾸지 않습니다.
    ⛔ **업무규칙을 만들지 않습니다.** 어느 사업자번호를 확보해야 하는지
    판정하지 않고, 집계된 사실만 돌려줍니다.

.. note::
    조회 조건은 ``PurchaseRepository.find_unmatched()`` 와 **완전히 같습니다**
    (``company_id IS NULL`` 전체). :class:`CompanyMatcher` 가 실제로 연결을
    시도하는 대상, 그리고 대시보드의 ``unmatched_purchase_count`` 와 같은
    모집단이어야 화면의 숫자가 서로 맞습니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.dashboard.unmatched_service import (
    DESCENDING,
    SORT_AMOUNT,
    SORT_BUSINESS_NO,
    SORT_COUNT,
    UnmatchedCompanyService,
    UnmatchedQuery,
    UnmatchedQueryError,
)
from procurement.database.bootstrap import init_db
from procurement.database.company_repository import CompanyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models.company import Company
from procurement.models.purchase import Purchase

#: 합성 데이터 — 실제 사업자번호·거래처명을 쓰지 않습니다.
_A = "1000000001"
_B = "2000000002"
_C = "3000000003"

_DAY = date(2026, 3, 2)


def _purchase(business_no: str, amount: str, *, name: str = "합성기업") -> Purchase:
    """저장용 구매 한 건. ⚠️ 금액만 다르고 나머지는 고정입니다."""
    return Purchase(
        business_no=business_no,
        company_name=name,
        contract_date=_DAY,
        payment_date=_DAY,
        amount=Decimal(amount),
    )


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """빈 스키마만 있는 DB."""
    path = tmp_path / "unmatched.db"
    init_db(path)
    return path


@pytest.fixture
def seeded(db: Path) -> Path:
    """미매칭 5건 + 매칭된 1건.

    ============ ===== ============ ==========================
    사업자번호     건수   금액 합계     비고
    ============ ===== ============ ==========================
    ``_A``          3      600       거래처명 표기가 두 가지
    ``_B``          2      400
    ``_C``          1      —         **기업이 등록되어 매칭됨**
    ============ ===== ============ ==========================
    """
    purchases = PurchaseRepository(db)
    companies = CompanyRepository(db)

    names = (("100", "합성기업 가"), ("200", "합성기업 가"), ("300", "합성기업 가(주)"))
    for amount, name in names:
        purchases.insert(_purchase(_A, amount, name=name))
    for amount in ("150", "250"):
        purchases.insert(_purchase(_B, amount))

    # ⛔ 이 기업만 등록한다 — 매칭된 구매가 집계에 섞이지 않는지 보기 위해서다.
    company = companies.insert(
        Company(business_no=_C, company_name="등록된 합성기업", representative_name="합성")
    )
    assert company.company_id is not None
    saved = purchases.insert(_purchase(_C, "9999"))
    assert saved.purchase_id is not None
    purchases.update_company_id(saved.purchase_id, company.company_id)
    return db


def _service(db: Path) -> UnmatchedCompanyService:
    return UnmatchedCompanyService(PurchaseRepository(db))


class TestAggregation:
    """사업자번호별로 접는다."""

    def test_groups_by_business_no(self, seeded: Path) -> None:
        page = _service(seeded).search(UnmatchedQuery())

        assert page.total == 2  # _A · _B — ⛔ _C 는 매칭되어 빠진다
        assert [row.business_no for row in page.items] == [_A, _B]

    def test_counts_and_amounts(self, seeded: Path) -> None:
        page = _service(seeded).search(UnmatchedQuery())
        rows = {row.business_no: row for row in page.items}

        assert rows[_A].purchase_count == 3
        assert rows[_A].total_amount == Decimal("600")
        assert rows[_B].purchase_count == 2
        assert rows[_B].total_amount == Decimal("400")

    def test_amount_share_is_over_the_unmatched_whole(self, seeded: Path) -> None:
        """비중의 분모는 **미매칭 전체**다 — 전체 구매금액이 아니다."""
        page = _service(seeded).search(UnmatchedQuery())
        rows = {row.business_no: row for row in page.items}

        assert rows[_A].amount_share == Decimal("60.00")
        assert rows[_B].amount_share == Decimal("40.00")
        assert sum(row.amount_share for row in page.items) == Decimal("100.00")

    def test_every_company_name_is_kept(self, seeded: Path) -> None:
        """⛔ 표기가 여러 가지면 하나를 골라 대표로 삼지 않는다."""
        page = _service(seeded).search(UnmatchedQuery())
        rows = {row.business_no: row for row in page.items}

        assert rows[_A].company_names == ("합성기업 가", "합성기업 가(주)")

    def test_totals_ignore_the_search_condition(self, seeded: Path) -> None:
        """전체 합계는 조건과 무관해야 화면이 "몇 건 중 몇 건" 을 말할 수 있다."""
        page = _service(seeded).search(UnmatchedQuery(search=_A))

        assert page.total == 1
        assert page.unmatched_purchase_count == 5
        assert page.unmatched_total_amount == Decimal("1000")
        assert page.unmatched_business_no_count == 2


class TestMatchedPurchasesAreExcluded:
    """⛔ 이미 연결된 구매가 섞이면 담당자가 확보 대상을 잘못 고른다."""

    def test_matched_business_no_is_absent(self, seeded: Path) -> None:
        page = _service(seeded).search(UnmatchedQuery())

        assert _C not in [row.business_no for row in page.items]

    def test_matched_amount_is_not_summed(self, seeded: Path) -> None:
        page = _service(seeded).search(UnmatchedQuery())

        # 9,999 원짜리 매칭 건이 더해졌다면 합계가 10,999 가 된다.
        assert page.unmatched_total_amount == Decimal("1000")

    def test_the_population_matches_find_unmatched(self, seeded: Path) -> None:
        """조회 조건이 ``find_unmatched()`` 와 같아야 화면 숫자가 서로 맞는다."""
        expected = PurchaseRepository(seeded).find_unmatched()
        page = _service(seeded).search(UnmatchedQuery())

        assert page.unmatched_purchase_count == len(expected)


class TestSorting:
    """정렬은 화면 편의다 — 업무적 우선순위가 아니다."""

    def test_by_amount_descending(self, seeded: Path) -> None:
        page = _service(seeded).search(UnmatchedQuery(sort=SORT_AMOUNT, direction=DESCENDING))

        assert [row.business_no for row in page.items] == [_A, _B]

    def test_by_amount_ascending(self, seeded: Path) -> None:
        page = _service(seeded).search(UnmatchedQuery(sort=SORT_AMOUNT, direction="asc"))

        assert [row.business_no for row in page.items] == [_B, _A]

    def test_by_count_descending(self, seeded: Path) -> None:
        page = _service(seeded).search(UnmatchedQuery(sort=SORT_COUNT, direction=DESCENDING))

        assert [row.purchase_count for row in page.items] == [3, 2]

    def test_by_business_no_ascending(self, seeded: Path) -> None:
        page = _service(seeded).search(UnmatchedQuery(sort=SORT_BUSINESS_NO, direction="asc"))

        assert [row.business_no for row in page.items] == [_A, _B]

    def test_an_unknown_sort_key_is_refused(self) -> None:
        """⛔ 조용히 기본값으로 되돌리지 않는다 — 화면이 다른 것을 보게 된다."""
        with pytest.raises(UnmatchedQueryError):
            UnmatchedQuery(sort="amount_share")

    def test_an_unknown_direction_is_refused(self) -> None:
        with pytest.raises(UnmatchedQueryError):
            UnmatchedQuery(direction="descending")


class TestPaging:
    def test_page_size_limits_the_window(self, seeded: Path) -> None:
        page = _service(seeded).search(UnmatchedQuery(page_size=1))

        assert len(page.items) == 1
        assert page.total == 2  # ⚠️ total 은 조건에 맞는 **전체** 수다

    def test_second_page_continues(self, seeded: Path) -> None:
        first = _service(seeded).search(UnmatchedQuery(page_size=1, page=1))
        second = _service(seeded).search(UnmatchedQuery(page_size=1, page=2))

        assert first.items[0].business_no != second.items[0].business_no

    def test_a_page_past_the_end_is_empty_not_an_error(self, seeded: Path) -> None:
        page = _service(seeded).search(UnmatchedQuery(page_size=1, page=99))

        assert page.items == ()
        assert page.total == 2

    def test_page_zero_is_refused(self) -> None:
        with pytest.raises(UnmatchedQueryError):
            UnmatchedQuery(page=0)

    def test_an_oversized_page_is_refused(self) -> None:
        with pytest.raises(UnmatchedQueryError):
            UnmatchedQuery(page_size=100_000)


class TestSearch:
    def test_finds_by_business_no(self, seeded: Path) -> None:
        page = _service(seeded).search(UnmatchedQuery(search=_B))

        assert [row.business_no for row in page.items] == [_B]

    def test_finds_by_company_name(self, seeded: Path) -> None:
        page = _service(seeded).search(UnmatchedQuery(search="가(주)"))

        assert [row.business_no for row in page.items] == [_A]

    def test_no_match_is_an_empty_page(self, seeded: Path) -> None:
        page = _service(seeded).search(UnmatchedQuery(search="없는거래처"))

        assert page.items == ()
        assert page.total == 0


class TestEmptyDatabase:
    """미매칭이 하나도 없어도 오류가 아니다."""

    def test_empty_page(self, db: Path) -> None:
        page = _service(db).search(UnmatchedQuery())

        assert page.items == ()
        assert page.total == 0
        assert page.unmatched_purchase_count == 0
        assert page.unmatched_business_no_count == 0

    def test_share_does_not_divide_by_zero(self, db: Path) -> None:
        page = _service(db).search(UnmatchedQuery())

        assert page.unmatched_total_amount == Decimal("0")


class TestHttp:
    """HTTP 계약 — 화면이 실제로 쓰는 모양."""

    def test_returns_the_aggregate(self, seeded: Path) -> None:
        client = TestClient(create_app(seeded))

        response = client.get("/dashboard/unmatched-companies")

        assert response.status_code == 200
        body = response.json()
        assert body["total"] == 2
        assert body["unmatched_purchase_count"] == 5
        assert [item["business_no"] for item in body["items"]] == [_A, _B]

    def test_amounts_are_strings(self, seeded: Path) -> None:
        """⚠️ 금액은 정밀도를 잃지 않도록 문자열로 내려간다(기존 규칙과 동일)."""
        client = TestClient(create_app(seeded))

        body = client.get("/dashboard/unmatched-companies").json()

        assert body["unmatched_total_amount"] == "1000"
        assert body["items"][0]["total_amount"] == "600"
        assert body["items"][0]["amount_share"] == "60.00"

    def test_notice_explains_the_population(self, seeded: Path) -> None:
        client = TestClient(create_app(seeded))

        body = client.get("/dashboard/unmatched-companies").json()

        assert "재매칭" in body["notice"]
        assert body["includes_superseded"] is False

    def test_sort_and_paging_are_honoured(self, seeded: Path) -> None:
        client = TestClient(create_app(seeded))

        body = client.get(
            "/dashboard/unmatched-companies",
            params={"sort": "count", "direction": "asc", "page_size": 1},
        ).json()

        assert [item["purchase_count"] for item in body["items"]] == [2]

    def test_a_bad_condition_is_refused_with_422(self, seeded: Path) -> None:
        """⛔ 조용히 기본값으로 되돌리지 않는다."""
        client = TestClient(create_app(seeded))

        assert client.get("/dashboard/unmatched-companies?sort=nope").status_code == 422
        assert client.get("/dashboard/unmatched-companies?direction=nope").status_code == 422

    def test_the_endpoint_is_read_only(self, seeded: Path) -> None:
        """⛔ 조회가 데이터를 바꾸지 않는다."""
        client = TestClient(create_app(seeded))
        purchases = PurchaseRepository(seeded)
        companies = CompanyRepository(seeded)
        before = (len(purchases.find_all()), companies.count(), len(purchases.find_unmatched()))

        client.get("/dashboard/unmatched-companies")

        after = (len(purchases.find_all()), companies.count(), len(purchases.find_unmatched()))
        assert before == after

    def test_write_verbs_are_not_offered(self, seeded: Path) -> None:
        """⛔ 이 경로에 쓰기 동사를 만들지 않는다."""
        client = TestClient(create_app(seeded))

        for call in (client.post, client.put, client.delete):
            assert call("/dashboard/unmatched-companies").status_code == 405


class TestConsistencyWithDataStatus:
    """대시보드 총계와 같은 숫자를 말해야 한다."""

    def test_same_unmatched_count(self, seeded: Path) -> None:
        client = TestClient(create_app(seeded))

        status = client.get("/dashboard/data-status").json()
        unmatched = client.get("/dashboard/unmatched-companies").json()

        assert unmatched["unmatched_purchase_count"] == status["unmatched_purchase_count"]
