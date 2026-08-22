"""STEP 6 — DB-2 전체 흐름 안정성.

지시 7번이 요구한 세 가지를 **끝에서 끝까지** 확인합니다.

::

    분석 → 저장 → 확정 → 다시 분석 → 재검토 → 다른 유형으로 확정

무엇을 지키는지:

1. **재분석이 확정값을 덮지 않는다** — ``final_purchase_type`` ·
   ``reviewed_by`` · ``reviewed_at`` 가 분석 때문에 바뀌지 않는다
2. **재검토가 가능하고 이력이 남는다** — ``CONFIRMED`` → ``REOPENED`` →
   다른 유형으로 ``CONFIRMED``
3. **판단 보류를 저장할 수 있다** — ``None`` 이 "값 없음" 으로 정상 저장된다

⚠️ 여기 쓰는 데이터는 전부 **합성 데이터**입니다. 실제 거래처명·사업자번호를
쓰지 않습니다.
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
from procurement.reviews.review_service import ReviewService


@pytest.fixture
def db_path(tmp_path: Path) -> str:
    """격리된 빈 DB."""
    path = str(tmp_path / "lifecycle.db")
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
    """⛔ 분석기를 주입하지 않는다 — 운영 기본값과 같은 상태."""
    return ReviewService(purchases, reviews)


def add_purchase(
    repository: PurchaseRepository, description: str = "시설물 유지관리 노무비"
) -> int:
    """합성 구매 1건을 넣고 ID 를 돌려준다."""
    purchase = repository.insert(
        Purchase(
            business_no="111-11-11111",
            company_name="가나건설",
            contract_date=date(2026, 3, 1),
            payment_date=date(2026, 3, 20),
            amount=Decimal("5500000"),
            resolution_date=date(2026, 3, 25),
            issue_date=date(2026, 3, 10),
            description=description,
            budget_account="외주용역비",
        )
    )
    assert purchase.purchase_id is not None
    return purchase.purchase_id


def analysis(*pairs: tuple[str, str]) -> ClassificationResult:
    """후보 목록으로 분석 결과를 만든다."""
    return ClassificationResult(
        candidates=[
            TypeCandidate(purchase_type=label, score=Decimal(score), evidence="합성 근거")
            for label, score in pairs
        ],
        analyzer_name="bm25",
        analyzer_version="1",
        status=ANALYZED,
    )


class TestReanalysisDoesNotOverwriteTheDecision:
    """⛔ 재분석이 담당자 확정을 덮지 않는다 (지시 7-1)."""

    def test_full_cycle_keeps_the_confirmed_type(
        self, purchases: PurchaseRepository, reviews: ReviewRepository
    ) -> None:
        """분석 → 확정 → 재분석 후에도 확정값이 그대로다."""
        purchase_id = add_purchase(purchases)

        reviews.save_analysis(purchase_id, analysis((SERVICE, "0.90"), (CONSTRUCTION, "0.10")))
        confirmed = reviews.confirm(
            purchase_id, final_purchase_type=CONSTRUCTION, reviewed_by="김담당"
        )
        # 분석기는 용역이라 했지만 담당자는 공사로 확정했다.
        assert confirmed.final_purchase_type == CONSTRUCTION

        # 분석을 **정반대 결론**으로 다시 돌린다.
        after = reviews.save_analysis(purchase_id, analysis((GOODS, "0.99"), (SERVICE, "0.01")))

        assert after.final_purchase_type == CONSTRUCTION
        assert after.review_status == CONFIRMED
        assert after.reviewed_by == "김담당"
        # 분석 컬럼은 정상적으로 갱신된다
        assert after.top_candidate is not None
        assert after.top_candidate.purchase_type == GOODS

    def test_reviewed_at_is_not_touched_by_analysis(
        self, purchases: PurchaseRepository, reviews: ReviewRepository
    ) -> None:
        """확정 시각이 재분석으로 바뀌면 '누가 언제 정했나' 가 무너진다."""
        purchase_id = add_purchase(purchases)
        confirmed = reviews.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")

        after = reviews.save_analysis(purchase_id, analysis((GOODS, "0.99")))

        assert after.reviewed_at == confirmed.reviewed_at

    def test_repeated_analysis_is_stable(
        self, purchases: PurchaseRepository, reviews: ReviewRepository
    ) -> None:
        """열 번을 다시 돌려도 확정값은 그대로다."""
        purchase_id = add_purchase(purchases)
        reviews.confirm(purchase_id, final_purchase_type=CONSTRUCTION, reviewed_by="김담당")

        for _ in range(10):
            reviews.save_analysis(purchase_id, analysis((GOODS, "0.99")))

        stored = reviews.find_by_purchase_id(purchase_id)
        assert stored is not None
        assert stored.final_purchase_type == CONSTRUCTION

    def test_analysis_before_any_review_leaves_the_decision_empty(
        self, purchases: PurchaseRepository, reviews: ReviewRepository
    ) -> None:
        """⛔ 분석만으로는 절대 확정되지 않는다 (점수 1.0 이어도)."""
        purchase_id = add_purchase(purchases)

        after = reviews.save_analysis(purchase_id, analysis((SERVICE, "1.00")))

        assert after.final_purchase_type is None
        assert after.review_status == PENDING
        assert after.reviewed_by is None
        assert after.reviewed_at is None


class TestReopenAndReconfirm:
    """확정을 되돌려 다른 유형으로 다시 확정할 수 있다 (지시 7-2)."""

    def test_confirmed_to_reopened_to_other_type(
        self, purchases: PurchaseRepository, reviews: ReviewRepository
    ) -> None:
        purchase_id = add_purchase(purchases)

        reviews.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")
        reopened = reviews.reopen(purchase_id, reopened_by="이담당", note="공사로 봐야 함")
        assert reopened.review_status == REOPENED
        # ⛔ 되돌려도 이전 선택을 지우지 않는다 — 무엇을 골랐었는지 보여야 한다
        assert reopened.final_purchase_type == SERVICE

        again = reviews.confirm(purchase_id, final_purchase_type=CONSTRUCTION, reviewed_by="이담당")

        assert again.review_status == CONFIRMED
        assert again.final_purchase_type == CONSTRUCTION
        assert again.reviewed_by == "이담당"

    def test_history_records_the_whole_journey(
        self, purchases: PurchaseRepository, reviews: ReviewRepository
    ) -> None:
        """분석 · 확정 · 재검토 · 재확정이 모두 이력에 남는다."""
        purchase_id = add_purchase(purchases)

        reviews.save_analysis(purchase_id, analysis((SERVICE, "0.90")))
        reviews.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")
        reviews.reopen(purchase_id, reopened_by="이담당", note="다시 봄")
        reviews.confirm(purchase_id, final_purchase_type=CONSTRUCTION, reviewed_by="이담당")

        actions = [entry.action for entry in reviews.find_history(purchase_id)]

        assert actions == [
            ACTION_ANALYZED,
            ACTION_CONFIRMED,
            ACTION_REOPENED,
            ACTION_CONFIRMED,
        ]

    def test_history_keeps_before_and_after(
        self, purchases: PurchaseRepository, reviews: ReviewRepository
    ) -> None:
        """무엇에서 무엇으로 바뀌었는지 추적할 수 있어야 한다."""
        purchase_id = add_purchase(purchases)
        reviews.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")
        reviews.reopen(purchase_id, reopened_by="이담당")
        reviews.confirm(purchase_id, final_purchase_type=CONSTRUCTION, reviewed_by="이담당")

        last = reviews.find_history(purchase_id)[-1]

        assert last.before_type == SERVICE
        assert last.after_type == CONSTRUCTION

    def test_history_is_append_only(
        self, purchases: PurchaseRepository, reviews: ReviewRepository
    ) -> None:
        """이력은 지워지지 않는다 — 다시 확정해도 앞의 기록이 남는다."""
        purchase_id = add_purchase(purchases)
        for label in (SERVICE, CONSTRUCTION, GOODS, SERVICE):
            reviews.confirm(purchase_id, final_purchase_type=label, reviewed_by="김담당")

        history = reviews.find_history(purchase_id)

        assert len(history) == 4
        assert [entry.after_type for entry in history] == [
            SERVICE,
            CONSTRUCTION,
            GOODS,
            SERVICE,
        ]


class TestHoldDecision:
    """판단 보류를 정상 저장할 수 있다 (지시 7-3)."""

    def test_hold_is_stored_as_none(
        self, purchases: PurchaseRepository, reviews: ReviewRepository
    ) -> None:
        """⛔ 판단 보류는 새 유형 코드가 아니라 **값 없음**이다."""
        purchase_id = add_purchase(purchases)

        held = reviews.confirm(purchase_id, final_purchase_type=None, reviewed_by="김담당")

        assert held.final_purchase_type is None
        assert held.final_purchase_type_label is None
        # 그러나 "아직 안 봤다" 와는 다르다 — 사람이 보고 보류한 것이다
        assert held.review_status == CONFIRMED
        assert held.reviewed_by == "김담당"

    def test_hold_is_distinguishable_from_untouched(
        self, purchases: PurchaseRepository, reviews: ReviewRepository
    ) -> None:
        """'보류' 와 '손도 안 댐' 은 상태로 구분된다."""
        held_id = add_purchase(purchases, "보류할 적요")
        untouched_id = add_purchase(purchases, "손대지 않을 적요")

        reviews.confirm(held_id, final_purchase_type=None, reviewed_by="김담당")
        reviews.ensure(untouched_id)

        held = reviews.find_by_purchase_id(held_id)
        untouched = reviews.find_by_purchase_id(untouched_id)
        assert held is not None and untouched is not None

        assert held.final_purchase_type is untouched.final_purchase_type is None
        assert held.review_status == CONFIRMED
        assert untouched.review_status == PENDING

    def test_hold_survives_reanalysis(
        self, purchases: PurchaseRepository, reviews: ReviewRepository
    ) -> None:
        """보류도 사람의 결정이다 — 분석이 덮어쓰면 안 된다."""
        purchase_id = add_purchase(purchases)
        reviews.confirm(purchase_id, final_purchase_type=None, reviewed_by="김담당")

        after = reviews.save_analysis(purchase_id, analysis((SERVICE, "0.99")))

        assert after.final_purchase_type is None
        assert after.review_status == CONFIRMED

    def test_hold_can_be_changed_to_a_type_later(
        self, purchases: PurchaseRepository, reviews: ReviewRepository
    ) -> None:
        """보류했다가 나중에 결론을 내릴 수 있다."""
        purchase_id = add_purchase(purchases)
        reviews.confirm(purchase_id, final_purchase_type=None, reviewed_by="김담당")
        reviews.reopen(purchase_id, reopened_by="김담당")

        decided = reviews.confirm(purchase_id, final_purchase_type=GOODS, reviewed_by="김담당")

        assert decided.final_purchase_type == GOODS


class TestServiceLevelJourney:
    """서비스 계층에서도 같은 흐름이 성립한다."""

    def test_end_to_end_through_the_service(
        self, service: ReviewService, purchases: PurchaseRepository, reviews: ReviewRepository
    ) -> None:
        purchase_id = add_purchase(purchases)

        reviews.save_analysis(purchase_id, analysis((SERVICE, "0.95"), (CONSTRUCTION, "0.05")))
        service.confirm(purchase_id, final_purchase_type=CONSTRUCTION, reviewed_by="김담당")
        reviews.save_analysis(purchase_id, analysis((GOODS, "0.99")))
        service.reopen(purchase_id, reopened_by="이담당")
        target = service.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="이담당")

        assert target.review.final_purchase_type == SERVICE
        assert len(service.history(purchase_id)) == 5

    def test_original_is_never_modified(
        self, service: ReviewService, purchases: PurchaseRepository, reviews: ReviewRepository
    ) -> None:
        """⛔ DB-1 불변 — 검토를 아무리 해도 원본이 그대로다."""
        purchase_id = add_purchase(purchases)
        before = purchases.find_by_id(purchase_id)
        assert before is not None

        reviews.save_analysis(purchase_id, analysis((GOODS, "0.99")))
        service.confirm(purchase_id, final_purchase_type=CONSTRUCTION, reviewed_by="김담당")
        service.reopen(purchase_id, reopened_by="이담당")
        service.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="이담당")

        after = purchases.find_by_id(purchase_id)
        assert after is not None
        assert after.description == before.description
        assert after.budget_account == before.budget_account
        assert after.amount == before.amount

    def test_past_labels_reflect_confirmed_history(
        self, service: ReviewService, purchases: PurchaseRepository, reviews: ReviewRepository
    ) -> None:
        """같은 적요를 확정하면 과거 이력에 나타난다."""
        first = add_purchase(purchases, "복합기 토너 및 사무실 청소")
        second = add_purchase(purchases, "복합기 토너 및 사무실 청소")

        service.confirm(first, final_purchase_type=SERVICE, reviewed_by="김담당")
        target = service.get_target(second)

        assert target.past_labels.total == 1
        assert target.past_labels.labels[0].purchase_type == SERVICE

    def test_past_labels_show_a_conflict(
        self, service: ReviewService, purchases: PurchaseRepository, reviews: ReviewRepository
    ) -> None:
        """같은 적요가 갈리면 충돌로 표시된다 (STEP 5 에서 실제로 발견된 형태)."""
        first = add_purchase(purchases, "복합기 토너 및 사무실 청소")
        second = add_purchase(purchases, "복합기 토너 및 사무실 청소")
        third = add_purchase(purchases, "복합기  토너 및 사무실 청소")  # 띄어쓰기만 다름

        service.confirm(first, final_purchase_type=SERVICE, reviewed_by="김담당")
        service.confirm(second, final_purchase_type=GOODS, reviewed_by="이담당")

        summary = service.get_target(third).past_labels

        assert summary.has_conflict
        assert summary.type_count == 2
        assert summary.total == 2

    def test_held_decisions_are_not_counted_as_history(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """⛔ 판단 보류는 '과거에 이렇게 정했다' 가 아니다."""
        first = add_purchase(purchases, "판단이 어려운 적요")
        second = add_purchase(purchases, "판단이 어려운 적요")

        service.confirm(first, final_purchase_type=None, reviewed_by="김담당")

        assert service.get_target(second).past_labels.total == 0
