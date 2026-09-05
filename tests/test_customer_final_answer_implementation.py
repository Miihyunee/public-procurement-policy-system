"""
STEP 84 — 고객 최종 답변 중 **구현 대상 2건**을 잠급니다.

1. **예산과목 공란 → "확인 필요" 표시** (🟢 ``DECISIONS.md`` §0.12.10 · Q5-9)
2. **검토 화면 금액 검색** (🟢 〃 §0.12.5 · Q71-C)

(인증 판정 기준일은 ``test_resolution_date_rule.py`` 가 따로 잠급니다.)

무엇을 지키는가
===============

* 공란은 **알림일 뿐** — ⛔ 자동 제외도, 자동 포함 확정도 하지 않는다.
* 6종 정확 매칭 규칙은 **그대로**다.
* 금액 검색은 **정확히 같은 금액**만 — ⛔ 범위·근사 기준이 없다.
* 기존 검색 조건(적요 · 거래처명 · 사업자등록번호)은 **하나도 줄지 않았다.**

.. note::
    합성 데이터만 씁니다. 실제 거래처명·사업자등록번호를 쓰지 않습니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from procurement.core.amount_search import amount_search_key
from procurement.core.performance_exclusion import (
    BUDGET_ACCOUNT_CHECK_NOTICE,
    EXCLUDED,
    EXCLUDED_BUDGET_ACCOUNTS,
    INCLUDED,
    is_excluded_budget_account,
    needs_budget_account_check,
)
from procurement.database.bootstrap import init_db
from procurement.database.purchase_repository import PurchaseRepository
from procurement.database.review_repository import ReviewRepository
from procurement.models.purchase import Purchase
from procurement.models.review import PurchaseReview
from procurement.reviews.query import ReviewQuery
from procurement.reviews.response import PerformanceResponseModel
from procurement.reviews.review_service import ReviewService

#: 합성 사업자등록번호 — 실제 업체의 번호가 아닙니다.
#:
#: ⚠️ 숫자 검색은 사업자등록번호도 봅니다(부분 일치). 금액 검색만 따로 시험하려면
#: 번호에 시험용 금액(`1000000` 등)이 들어 있으면 안 됩니다.
_BUSINESS_NO = "2648127391"
_OTHER_BUSINESS_NO = "2648127392"


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    path = str(tmp_path / "step84.db")
    init_db(path)
    return path


@pytest.fixture
def purchases(db_path: str) -> PurchaseRepository:
    return PurchaseRepository(db_path)


@pytest.fixture
def service(db_path: str, purchases: PurchaseRepository) -> ReviewService:
    return ReviewService(purchases, ReviewRepository(db_path))


def _purchase(
    *,
    description: str = "합성 적요",
    amount: str = "1000000",
    budget_account: str | None = "외주용역비",
    business_no: str = _BUSINESS_NO,
    company_name: str = "합성기업 가",
) -> Purchase:
    return Purchase(
        business_no=business_no,
        company_name=company_name,
        contract_date=date(2026, 3, 1),
        payment_date=date(2026, 3, 20),
        resolution_date=date(2026, 3, 5),
        amount=Decimal(amount),
        description=description,
        budget_account=budget_account,
    )


# ======================================================================
# 1. 예산과목 공란 — "확인 필요" (§0.12.10)
# ======================================================================
class TestABlankBudgetAccountIsFlagged:
    """공란이면 **확인이 필요하다고 알린다.**"""

    @pytest.mark.parametrize("value", [None, "", "   ", "\t"])
    def test_a_blank_budget_account_needs_a_check(self, value: str | None) -> None:
        assert needs_budget_account_check(value) is True

    @pytest.mark.parametrize("value", ["외주용역비", "교육훈련비", " 의료비 "])
    def test_a_filled_budget_account_does_not(self, value: str) -> None:
        assert needs_budget_account_check(value) is False

    def test_the_review_item_carries_the_flag(self) -> None:
        model = PerformanceResponseModel.from_target(
            PurchaseReview(purchase_id=1), _purchase(budget_account=None)
        )
        assert model.budget_account_check_required is True
        assert model.budget_account_check_notice == BUDGET_ACCOUNT_CHECK_NOTICE

    def test_a_filled_row_carries_no_notice(self) -> None:
        model = PerformanceResponseModel.from_target(
            PurchaseReview(purchase_id=1), _purchase(budget_account="외주용역비")
        )
        assert model.budget_account_check_required is False
        assert model.budget_account_check_notice is None

    def test_the_notice_points_at_g20_and_the_order(self) -> None:
        """고객이 말한 **순서**가 안내에 그대로 있는가."""
        assert "G20" in BUDGET_ACCOUNT_CHECK_NOTICE
        assert "지출결의서" in BUDGET_ACCOUNT_CHECK_NOTICE
        assert "채운 뒤" in BUDGET_ACCOUNT_CHECK_NOTICE


class TestABlankBudgetAccountIsNotADecision:
    """⭐ ⛔ **공란 자체는 포함도 제외도 아니다.**"""

    def test_a_blank_row_is_not_excluded_automatically(self) -> None:
        model = PerformanceResponseModel.from_target(
            PurchaseReview(purchase_id=1), _purchase(budget_account=None)
        )
        assert model.status == INCLUDED
        assert model.by_budget_account_rule is False
        assert model.reason is None

    def test_a_blank_budget_account_is_not_in_the_six_rule(self) -> None:
        """⛔ 공란은 6종 규칙에 걸리지 않는다."""
        for value in (None, "", "   "):
            assert is_excluded_budget_account(value) is False

    def test_the_flag_does_not_confirm_inclusion(self) -> None:
        """⚠️ 담당자가 제외로 확정한 건이면 **그 확정이 그대로 남는다.**

        "확인 필요" 는 알림일 뿐, 상태를 되돌리거나 확정하지 않는다.
        """
        review = PurchaseReview(
            purchase_id=1,
            performance_status=EXCLUDED,
            exclusion_reason="EDUCATION_FEE",
            excluded_by="담당자",
        )
        model = PerformanceResponseModel.from_target(review, _purchase(budget_account=None))
        assert model.status == EXCLUDED
        assert model.budget_account_check_required is True
        assert model.can_reopen is True

    def test_the_six_accounts_are_still_exact_matches(self) -> None:
        """⛔ 6종 규칙은 이번 변경과 무관하다."""
        assert EXCLUDED_BUDGET_ACCOUNTS == frozenset(
            {"교육훈련비", "사업추진경비", "의료비", "수도광열비", "기타운영비", "복리후생비"}
        )
        assert is_excluded_budget_account("교육훈련비지원") is False
        assert is_excluded_budget_account(" 교육훈련비 ") is True

    def test_a_rule_excluded_row_is_never_flagged(self) -> None:
        """6종이 적혀 있으면 공란일 수 없다 — 두 표시가 겹치지 않는다."""
        model = PerformanceResponseModel.from_target(
            PurchaseReview(purchase_id=1), _purchase(budget_account="교육훈련비")
        )
        assert model.by_budget_account_rule is True
        assert model.budget_account_check_required is False

    def test_the_original_row_is_not_modified(self, purchases: PurchaseRepository) -> None:
        """⛔ 원본을 채우지 않는다 — 저장된 값은 그대로 비어 있다."""
        saved = purchases.insert(_purchase(budget_account=None))
        assert saved.purchase_id is not None
        PerformanceResponseModel.from_target(PurchaseReview(purchase_id=saved.purchase_id), saved)

        reloaded = purchases.find_by_id(saved.purchase_id)
        assert reloaded is not None
        assert reloaded.budget_account is None


class TestTheBlankFlagIsVisibleInTheList:
    """목록에서 **식별 가능한가** — 카드를 열지 않아도 보이는가."""

    def test_the_list_marks_the_blank_rows(
        self, purchases: PurchaseRepository, service: ReviewService
    ) -> None:
        blank = purchases.insert(_purchase(description="공란 건", budget_account=None))
        filled = purchases.insert(_purchase(description="채워진 건", budget_account="외주용역비"))
        assert blank.purchase_id is not None and filled.purchase_id is not None

        page = service.search(ReviewQuery())
        flags = {
            item.purchase.purchase_id: PerformanceResponseModel.from_target(
                item.review, item.purchase
            ).budget_account_check_required
            for item in page.items
        }
        assert flags == {blank.purchase_id: True, filled.purchase_id: False}


# ======================================================================
# 2. 금액 검색 (§0.12.5 · Q71-C)
# ======================================================================
class TestTheAmountSearchKey:
    """검색어를 금액으로 읽는 규칙."""

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("1000000", Decimal("1000000")),
            ("1,000,000", Decimal("1000000")),
            ("1,000,000원", Decimal("1000000")),
            (" 1000000 ", Decimal("1000000")),
            ("1000.50", Decimal("1000.50")),
        ],
    )
    def test_it_reads_what_is_on_the_paper(self, text: str, expected: Decimal) -> None:
        """담당자는 화면·지출결의서에 **보이는 그대로** 옮겨 적는다."""
        assert amount_search_key(text) == expected

    @pytest.mark.parametrize("text", ["복사용지", "", "   ", "abc", "-500", "1e6", None])
    def test_it_refuses_what_is_not_an_amount(self, text: str | None) -> None:
        """⛔ 금액으로 읽히지 않으면 금액 조건이 되지 않는다."""
        assert amount_search_key(text) is None


class TestTheReviewSearchFindsAnAmount:
    """🟢 Q71-C — 금액으로 찾을 수 있는가."""

    @pytest.fixture
    def seeded(self, purchases: PurchaseRepository) -> dict[str, int]:
        rows = {
            "big": purchases.insert(_purchase(description="복사용지", amount="1000000")),
            "small": purchases.insert(_purchase(description="사무용품", amount="250000")),
        }
        return {key: row.purchase_id for key, row in rows.items() if row.purchase_id}

    def _found(self, service: ReviewService, text: str) -> set[int]:
        page = service.search(ReviewQuery(search=text))
        return {item.purchase.purchase_id for item in page.items if item.purchase.purchase_id}

    def test_a_plain_number_finds_the_row(
        self, service: ReviewService, seeded: dict[str, int]
    ) -> None:
        assert self._found(service, "1000000") == {seeded["big"]}

    def test_a_formatted_number_finds_the_same_row(
        self, service: ReviewService, seeded: dict[str, int]
    ) -> None:
        assert self._found(service, "1,000,000원") == {seeded["big"]}

    def test_only_the_exact_amount_matches(
        self, service: ReviewService, seeded: dict[str, int]
    ) -> None:
        """⛔ 범위·근사가 아니다 — 1,000,001 은 1,000,000 을 찾지 않는다."""
        assert self._found(service, "1000001") == set()
        assert self._found(service, "100000") == set()

    def test_an_amount_with_no_match_returns_nothing(
        self, service: ReviewService, seeded: dict[str, int]
    ) -> None:
        assert self._found(service, "999999999") == set()


class TestTheExistingSearchStillWorks:
    """⛔ 기존 검색 조건을 **하나도 지우지 않았다.**"""

    @pytest.fixture
    def seeded(self, purchases: PurchaseRepository) -> int:
        saved = purchases.insert(
            _purchase(description="복사용지 구매", amount="1000000", company_name="합성기업 가")
        )
        purchases.insert(
            _purchase(
                description="사무용품",
                amount="250000",
                business_no=_OTHER_BUSINESS_NO,
                company_name="합성기업 나",
            )
        )
        assert saved.purchase_id is not None
        return saved.purchase_id

    def _found(self, service: ReviewService, text: str) -> set[int]:
        page = service.search(ReviewQuery(search=text))
        return {item.purchase.purchase_id for item in page.items if item.purchase.purchase_id}

    def test_the_description_search_still_works(self, service: ReviewService, seeded: int) -> None:
        assert self._found(service, "복사용지") == {seeded}

    def test_the_company_name_search_still_works(self, service: ReviewService, seeded: int) -> None:
        assert seeded in self._found(service, "합성기업 가")

    def test_the_business_no_search_still_works(self, service: ReviewService, seeded: int) -> None:
        assert seeded in self._found(service, _BUSINESS_NO)

    def test_the_hyphenated_business_no_still_works(
        self, service: ReviewService, seeded: int
    ) -> None:
        """STEP 73 에서 고친 것 — 종이에 인쇄된 표기 그대로."""
        assert seeded in self._found(service, "264-81-27391")

    def test_the_amount_search_combines_with_the_other_filters(
        self, purchases: PurchaseRepository, service: ReviewService, seeded: int
    ) -> None:
        """기존 필터와 **함께** 쓸 수 있는가."""
        page = service.search(ReviewQuery(search="1000000", status="PENDING"))
        assert {item.purchase.purchase_id for item in page.items} == {seeded}

    def test_the_amount_search_respects_the_batch_filter(
        self, purchases: PurchaseRepository, service: ReviewService, seeded: int
    ) -> None:
        """⛔ 검색이 다른 조건을 무시하지 않는다."""
        page = service.search(ReviewQuery(search="1000000", batch_id=999))
        assert page.items == []

    def test_no_new_search_field_was_added(self) -> None:
        """⛔ 고객이 말한 셋뿐이다 — 검색칸을 새로 만들지 않았다."""
        fields = set(ReviewQuery.__dataclass_fields__)
        for absent in ("amount", "amount_min", "amount_max", "business_no", "description"):
            assert absent not in fields
