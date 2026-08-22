"""STEP 10 — 확정 취소(Undo).

담당자가 잘못 확정한 건을 **되돌리는** 기능입니다. 여기서 지키려는 약속은
하나로 요약됩니다.

    ⛔ **되돌리기는 지우기가 아니다.**

    ``final_purchase_type`` · ``reviewed_by`` · ``reviewed_at`` · 메모는
    그대로 두고 **상태만** ``REOPENED`` 로 바꿉니다. 담당자가 무엇을
    골랐었는지 화면에서 계속 볼 수 있어야 하고, 감사 관점에서도 "확정한 적
    있다" 는 사실이 사라지면 안 되기 때문입니다.

구성:

* ``TestUndoLifecycle`` — 상태 · 값 보존 · 이력
* ``TestUndoApi`` — ``POST /reviews/{id}/reopen`` 의 성공/거부 응답
* ``TestUndoScreen`` — ``index.html`` 의 확인창 · 실패 처리
* ``TestUndoAccessibility`` — 포커스 · aria · 단축키 충돌
* ``TestUndoProgress`` — 진행률 증감
* ``TestUndoList`` — 필터·정렬·검색·페이지 조건 유지

⚠️ 데이터는 전부 **합성**입니다. 고객 원본은 쓰지 않습니다.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.core.purchase_type import CONSTRUCTION, GOODS, SERVICE
from procurement.database.bootstrap import bootstrap
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
from procurement.reviews.past_labels import MIXED_TYPES, SINGLE_TYPE
from procurement.reviews.query import DECIDED, DESCENDING, UNDECIDED, ReviewQuery
from procurement.reviews.review_service import (
    ReviewNotFoundError,
    ReviewService,
    ReviewStateError,
)

INDEX = (
    Path(__file__).resolve().parents[1] / "src" / "procurement" / "web" / "static" / "index.html"
)


# ----------------------------------------------------------------------
# 준비
# ----------------------------------------------------------------------
@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "undo.db"
    bootstrap(path)
    return path


@pytest.fixture
def purchases(db_path: Path) -> PurchaseRepository:
    return PurchaseRepository(db_path)


@pytest.fixture
def reviews(db_path: Path) -> ReviewRepository:
    return ReviewRepository(db_path)


@pytest.fixture
def service(purchases: PurchaseRepository, reviews: ReviewRepository) -> ReviewService:
    return ReviewService(purchases, reviews)


@pytest.fixture
def client(db_path: Path) -> TestClient:
    return TestClient(create_app(db_path, period_date_field="payment_date"))


def add(
    repository: PurchaseRepository,
    description: str,
    *,
    company: str = "가나건설",
    amount: str = "1000000",
) -> int:
    """합성 구매 한 건."""
    saved = repository.insert(
        Purchase(
            business_no="111-11-11111",
            company_name=company,
            contract_date=date(2026, 3, 1),
            payment_date=date(2026, 3, 20),
            resolution_date=date(2026, 3, 25),
            issue_date=date(2026, 3, 10),
            description=description,
            budget_account="외주용역비",
            amount=Decimal(amount),
        )
    )
    assert saved.purchase_id is not None
    return saved.purchase_id


def analysis(*pairs: tuple[str, str]) -> ClassificationResult:
    return ClassificationResult(
        candidates=[
            TypeCandidate(purchase_type=code, score=Decimal(score), evidence="합성 근거")
            for code, score in pairs
        ],
        analyzer_name="test-analyzer",
        analyzer_version="1",
        status=ANALYZED,
    )


def ids(service: ReviewService, **kwargs: object) -> list[int]:
    page = service.search(ReviewQuery(**kwargs))  # type: ignore[arg-type]
    return [target.purchase.purchase_id or 0 for target in page.items]


@pytest.fixture(scope="module")
def page() -> str:
    return INDEX.read_text(encoding="utf-8")


def _function_body(page: str, name: str) -> str:
    """``function name(`` 부터 짝이 맞는 닫는 중괄호까지."""
    start = page.index("function " + name + "(")
    depth = 0
    started = False
    for index in range(start, len(page)):
        char = page[index]
        if char == "{":
            depth += 1
            started = True
        elif char == "}":
            depth -= 1
            if started and depth == 0:
                return page[start : index + 1]
    raise AssertionError(f"{name} 의 끝을 찾지 못했습니다")


# ----------------------------------------------------------------------
# Lifecycle
# ----------------------------------------------------------------------
class TestUndoLifecycle:
    """작업 A — CONFIRMED → REOPENED, 그리고 **값은 남는다**."""

    def test_status_becomes_reopened(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        purchase_id = add(purchases, "확정했다가 되돌릴 적요")
        service.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")

        target = service.reopen(purchase_id, reopened_by="김담당")

        assert target.review.review_status == REOPENED

    def test_confirmed_values_survive(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """⛔ 가장 중요 — 되돌려도 확정값·확정자·확정 시각이 남는다.

        ``DELETE`` 나 ``final_purchase_type = NULL`` 로 구현했다면 여기서
        깨진다.
        """
        purchase_id = add(purchases, "값이 남아야 하는 적요")
        service.confirm(
            purchase_id,
            final_purchase_type=CONSTRUCTION,
            reviewed_by="김담당",
            review_note="현장 확인함",
        )
        before = service.get_target(purchase_id).review

        after = service.reopen(purchase_id, reopened_by="이담당").review

        assert after.final_purchase_type == CONSTRUCTION
        assert after.reviewed_by == "김담당"
        assert after.reviewed_at == before.reviewed_at
        assert after.review_note == "현장 확인함"

    def test_the_row_is_not_deleted(
        self, service: ReviewService, purchases: PurchaseRepository, reviews: ReviewRepository
    ) -> None:
        purchase_id = add(purchases, "행이 남아야 하는 적요")
        service.confirm(purchase_id, final_purchase_type=GOODS, reviewed_by="김담당")

        service.reopen(purchase_id)

        assert reviews.find_by_purchase_id(purchase_id) is not None

    def test_analysis_columns_are_untouched(
        self, service: ReviewService, purchases: PurchaseRepository, reviews: ReviewRepository
    ) -> None:
        """되돌리기는 분석 결과도 건드리지 않는다."""
        purchase_id = add(purchases, "분석까지 된 적요")
        reviews.save_analysis(purchase_id, analysis((SERVICE, "0.90"), (CONSTRUCTION, "0.40")))
        service.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")

        after = service.reopen(purchase_id).review

        assert after.analyzer_name == "test-analyzer"
        assert [candidate.purchase_type for candidate in after.candidates] == [
            SERVICE,
            CONSTRUCTION,
        ]

    def test_history_records_the_undo(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """되돌린 사실 자체가 이력에 남는다 — 앞뒤 유형이 같은 한 줄로."""
        purchase_id = add(purchases, "이력에 남는 적요")
        service.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")

        service.reopen(purchase_id, reopened_by="이담당", note="다시 봄")

        entry = service.history(purchase_id)[-1]
        assert entry.action == ACTION_REOPENED
        assert entry.changed_by == "이담당"
        assert entry.note == "다시 봄"
        # 값을 바꾼 것이 아니라 상태를 되돌린 것이므로 앞뒤가 같다.
        assert entry.before_type == SERVICE
        assert entry.after_type == SERVICE

    def test_full_cycle_is_in_order(
        self, service: ReviewService, purchases: PurchaseRepository, reviews: ReviewRepository
    ) -> None:
        """ANALYZED → CONFIRMED → REOPENED → CONFIRMED 가 통째로 남는다."""
        purchase_id = add(purchases, "한 바퀴 도는 적요")
        reviews.save_analysis(purchase_id, analysis((SERVICE, "0.80")))
        service.confirm(purchase_id, final_purchase_type=CONSTRUCTION, reviewed_by="김담당")
        service.reopen(purchase_id, reopened_by="이담당")
        service.confirm(purchase_id, final_purchase_type=GOODS, reviewed_by="이담당")

        actions = [entry.action for entry in service.history(purchase_id)]

        assert actions == [ACTION_ANALYZED, ACTION_CONFIRMED, ACTION_REOPENED, ACTION_CONFIRMED]

    def test_reconfirm_with_a_different_type(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """되돌린 뒤 다른 유형으로 다시 확정할 수 있다."""
        purchase_id = add(purchases, "다시 고르는 적요")
        service.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")
        service.reopen(purchase_id)

        target = service.confirm(purchase_id, final_purchase_type=GOODS, reviewed_by="이담당")

        assert target.review.review_status == CONFIRMED
        assert target.review.final_purchase_type == GOODS
        assert target.review.reviewed_by == "이담당"

    def test_repeated_undo_and_reconfirm(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """여러 번 되돌리고 다시 확정해도 이력이 쌓이기만 한다."""
        purchase_id = add(purchases, "여러 번 만지는 적요")
        for chosen in (SERVICE, CONSTRUCTION, GOODS):
            service.confirm(purchase_id, final_purchase_type=chosen, reviewed_by="김담당")
            service.reopen(purchase_id, reopened_by="김담당")
        service.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")

        actions = [entry.action for entry in service.history(purchase_id)]

        assert actions.count(ACTION_CONFIRMED) == 4
        assert actions.count(ACTION_REOPENED) == 3

    def test_reanalysis_after_undo_keeps_the_confirmed_values(
        self, service: ReviewService, purchases: PurchaseRepository, reviews: ReviewRepository
    ) -> None:
        """되돌린 뒤 다시 분석해도 예전 확정 정보는 남는다."""
        purchase_id = add(purchases, "되돌린 뒤 재분석할 적요")
        service.confirm(purchase_id, final_purchase_type=CONSTRUCTION, reviewed_by="김담당")
        service.reopen(purchase_id)

        reviews.save_analysis(purchase_id, analysis((GOODS, "0.95")))

        after = service.get_target(purchase_id).review
        assert after.review_status == REOPENED
        assert after.final_purchase_type == CONSTRUCTION
        assert after.reviewed_by == "김담당"

    def test_undo_requires_a_confirmed_review(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """확정한 적 없는 건은 되돌릴 것이 없다."""
        purchase_id = add(purchases, "확정한 적 없는 적요")

        with pytest.raises(ReviewStateError):
            service.reopen(purchase_id)

    def test_second_undo_is_refused(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """이미 되돌린 건을 또 되돌리지 않는다 — 이력이 헛돌지 않도록."""
        purchase_id = add(purchases, "두 번 되돌릴 수 없는 적요")
        service.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")
        service.reopen(purchase_id)

        with pytest.raises(ReviewStateError):
            service.reopen(purchase_id)

        assert [entry.action for entry in service.history(purchase_id)].count(ACTION_REOPENED) == 1

    def test_missing_purchase_is_not_found(self, service: ReviewService) -> None:
        with pytest.raises(ReviewNotFoundError):
            service.reopen(999_999)


class TestUndoAndPastLabels:
    """작업 N — 되돌린 건은 **과거 확정 이력에서 빠진다**.

    ``past_labels`` 의 정의가 "지금 CONFIRMED 상태인 같은 적요들" 이므로,
    되돌린 건은 참고 자료에서 자연히 빠집니다. 그 건 **자체**의 확정값이
    지워지는 것과는 다른 이야기입니다.
    """

    def test_undo_removes_the_item_from_past_labels(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        first = add(purchases, "같은 적요")
        second = add(purchases, "같은 적요")
        third = add(purchases, "같은 적요")
        for purchase_id in (first, second):
            service.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")

        before = service.get_target(third).past_labels
        service.reopen(first)
        after = service.get_target(third).past_labels

        assert before.total == 2
        assert after.total == 1
        assert after.consistency == SINGLE_TYPE

    def test_reconfirming_with_another_type_makes_it_mixed(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        first = add(purchases, "갈리는 적요")
        second = add(purchases, "갈리는 적요")
        third = add(purchases, "갈리는 적요")
        for purchase_id in (first, second):
            service.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")
        service.reopen(first)

        service.confirm(first, final_purchase_type=CONSTRUCTION, reviewed_by="이담당")

        after = service.get_target(third).past_labels
        assert after.total == 2
        assert after.consistency == MIXED_TYPES


# ----------------------------------------------------------------------
# API
# ----------------------------------------------------------------------
class TestUndoApi:
    """작업 B — 기존 ``/reviews/{id}/reopen`` 을 그대로 쓴다."""

    def _confirmed(self, client: TestClient, db_path: Path) -> int:
        purchase_id = add(PurchaseRepository(db_path), "API 로 되돌릴 적요")
        response = client.put(
            f"/reviews/{purchase_id}",
            json={"final_purchase_type": SERVICE, "reviewed_by": "김담당"},
        )
        assert response.status_code == 200
        return purchase_id

    def test_undo_returns_the_reopened_item(self, client: TestClient, db_path: Path) -> None:
        purchase_id = self._confirmed(client, db_path)

        response = client.post(f"/reviews/{purchase_id}/reopen", json={"reopened_by": "이담당"})

        assert response.status_code == 200
        review = response.json()["review"]
        assert review["status"] == REOPENED
        # ⛔ 값이 지워지지 않았다.
        assert review["final_purchase_type"] == SERVICE
        assert review["reviewed_by"] == "김담당"
        assert review["reviewed_at"] is not None

    def test_response_keeps_all_four_blocks(self, client: TestClient, db_path: Path) -> None:
        """응답 모양을 바꾸지 않았다 — 화면이 그대로 그릴 수 있어야 한다."""
        purchase_id = self._confirmed(client, db_path)

        body = client.post(f"/reviews/{purchase_id}/reopen", json={}).json()

        assert set(body) >= {"source", "analysis", "review", "past_labels"}

    def test_unknown_purchase_is_404(self, client: TestClient) -> None:
        response = client.post("/reviews/999999/reopen", json={})

        assert response.status_code == 404

    def test_already_reopened_is_409(self, client: TestClient, db_path: Path) -> None:
        purchase_id = self._confirmed(client, db_path)
        client.post(f"/reviews/{purchase_id}/reopen", json={})

        again = client.post(f"/reviews/{purchase_id}/reopen", json={})

        assert again.status_code == 409

    def test_pending_is_409(self, client: TestClient, db_path: Path) -> None:
        """확정한 적 없는 건 — 404 가 아니라 **상태 충돌**이다."""
        purchase_id = add(PurchaseRepository(db_path), "아직 확정 안 한 적요")

        response = client.post(f"/reviews/{purchase_id}/reopen", json={})

        assert response.status_code == 409

    def test_duplicate_requests_change_the_state_once(
        self, client: TestClient, db_path: Path
    ) -> None:
        """버튼을 여러 번 눌러도 되돌리기는 한 번만 일어난다."""
        purchase_id = self._confirmed(client, db_path)

        statuses = [
            client.post(f"/reviews/{purchase_id}/reopen", json={}).status_code for _ in range(5)
        ]

        assert statuses == [200, 409, 409, 409, 409]
        entries = client.get(f"/reviews/{purchase_id}/history").json()["items"]
        assert [entry["action"] for entry in entries].count(ACTION_REOPENED) == 1

    def test_rejected_undo_does_not_touch_the_row(self, client: TestClient, db_path: Path) -> None:
        """409 로 거부된 요청은 아무것도 바꾸지 않는다."""
        purchase_id = add(PurchaseRepository(db_path), "거부될 요청의 적요")
        before = client.get(f"/reviews/{purchase_id}").json()["review"]

        client.post(f"/reviews/{purchase_id}/reopen", json={})

        assert client.get(f"/reviews/{purchase_id}").json()["review"] == before
        assert before["status"] == PENDING

    def test_error_body_explains_the_state(self, client: TestClient, db_path: Path) -> None:
        """담당자에게 보일 수 있도록 현재 상태를 말로 담는다."""
        purchase_id = add(PurchaseRepository(db_path), "사유가 필요한 적요")

        detail = client.post(f"/reviews/{purchase_id}/reopen", json={}).json()["detail"]

        assert "확정된 건만" in detail


# ----------------------------------------------------------------------
# 화면
# ----------------------------------------------------------------------
class TestUndoScreen:
    """작업 C·D·I·J — 확인창을 거쳐야만 되돌린다."""

    def test_confirm_dialog_exists(self, page: str) -> None:
        assert 'id="review-undo-modal"' in page
        assert 'id="review-undo-box"' in page
        assert "function askUndo(" in page

    def test_button_opens_the_dialog_instead_of_undoing(self, page: str) -> None:
        """⛔ 버튼을 누르자마자 요청을 보내지 않는다."""
        body = _function_body(page, "reviewCard")

        assert "askUndo(item)" in body
        assert "/reopen" not in body
        assert "runUndo" not in body

    def test_dialog_shows_the_current_decision(self, page: str) -> None:
        """무엇을 되돌리는지 보여준다 — 참고용 표시일 뿐이다."""
        body = _function_body(page, "askUndo")

        assert "현재 확정 유형" in body
        assert "확정자" in body
        assert "확정일시" in body

    def test_dialog_does_not_pick_a_new_type(self, page: str) -> None:
        """⛔ 확인창에서 새 유형을 고르게 하지 않는다 — 되돌리기만 한다."""
        body = _function_body(page, "askUndo")

        for banned in ("reviewPicks", "confirmReview", "rv-opt"):
            assert banned not in body, banned

    def test_opening_the_dialog_sends_nothing(self, page: str) -> None:
        body = _function_body(page, "askUndo")

        assert "fetch(" not in body
        assert "loadReviews" not in body

    def test_cancel_only_closes(self, page: str) -> None:
        body = _function_body(page, "closeUndo")

        assert "hidden = true" in body
        assert "fetch(" not in body
        assert "loadReviews" not in body

    def test_outside_click_is_a_cancel(self, page: str) -> None:
        body = _function_body(page, "initReview")

        assert 'el("review-undo-modal").addEventListener("click"' in body
        assert "closeUndo();" in body

    def test_request_is_sent_only_from_run_undo(self, page: str) -> None:
        """되돌리기 요청 경로는 ``runUndo`` 하나뿐이다.

        확인창을 거치지 않는 옛 ``reopenReview`` 는 STEP 10 에서 지웠다.
        """
        script = page.split("<script>")[-1]

        assert script.count("/reopen") == 1
        assert "function reopenReview(" not in script
        assert "/reopen" in _function_body(page, "runUndo")

    def test_double_click_guard(self, page: str) -> None:
        """작업 I — 같은 건에 두 번 보내지 않는다."""
        body = _function_body(page, "runUndo")

        assert "if (undoInFlight[purchaseId]) { return; }" in body
        assert "undoInFlight[purchaseId] = true;" in body
        assert "delete undoInFlight[purchaseId];" in body
        assert "button.disabled = true;" in body

    def test_success_refreshes_the_list(self, page: str) -> None:
        body = _function_body(page, "runUndo")

        assert "closeUndo();" in body
        assert "loadReviews();" in body
        assert "reviewNotice(" in body

    def test_failure_does_not_look_like_success(self, page: str) -> None:
        """⛔ 실패했는데 창이 닫히고 안내가 뜨면 담당자가 속는다."""
        body = _function_body(page, "runUndo")
        failure = body[body.index(".catch(") :]

        assert "reviewError(" in failure
        assert "closeUndo" not in failure
        assert "reviewNotice" not in failure
        assert "loadReviews" not in failure
        # 다시 시도할 수 있도록 버튼을 되살린다.
        assert "button.disabled = false;" in failure

    def test_no_automatic_retry(self, page: str) -> None:
        """⛔ 상태를 바꾸는 요청을 조용히 다시 보내지 않는다 (지시 J)."""
        script = page.split("<script>")[-1]

        for banned in ("setTimeout(runUndo", "setInterval(runUndo", "retryCount", "maxRetries"):
            assert banned not in script, banned

    def test_error_messages_cover_each_case(self, page: str) -> None:
        body = _function_body(page, "failureMessage")

        assert "status === 409" in body
        assert "status === 404" in body
        assert "서버 오류" in body
        assert "서버와 연결할 수 없습니다" in body

    def test_confirm_failure_keeps_the_choice(self, page: str) -> None:
        """확정이 실패하면 다음 건으로 넘어가지 않는다 (지시 J)."""
        body = _function_body(page, "confirmReview")
        failure = body[body.index(".catch(") :]

        assert "reviewError(" in failure
        assert "delete reviewPicks" not in failure
        assert "moveCursor" not in failure

    def test_ids_are_still_unique(self, page: str) -> None:
        """이력 모달과 id 가 겹치지 않는다 (STEP 9 에서 실제로 겪은 사고)."""
        found = re.findall(r'\bid="([^"]+)"', page)

        duplicates = {value for value in found if found.count(value) > 1}
        assert duplicates == set(), duplicates


class TestUndoAccessibility:
    """작업 K·L — 키보드만으로도 쓸 수 있어야 한다."""

    def test_focus_visible_is_styled(self, page: str) -> None:
        """마우스 클릭에는 테두리가 생기지 않고 키보드 이동에만 보인다."""
        assert ":focus-visible {" in page

    def test_dialog_is_announced_as_an_alert(self, page: str) -> None:
        assert 'role="alertdialog"' in page
        assert 'aria-label="확정 취소 확인"' in page
        assert 'aria-modal="true"' in page

    def test_default_focus_is_the_safe_choice(self, page: str) -> None:
        """⛔ 기본 포커스가 '확정 취소' 에 있으면 Enter 한 번에 풀린다."""
        body = _function_body(page, "askUndo")

        assert "cancel.focus();" in body
        assert "ok.focus()" not in body

    def test_focus_returns_after_closing(self, page: str) -> None:
        assert "rememberFocus();" in _function_body(page, "askUndo")
        assert "restoreFocus();" in _function_body(page, "closeUndo")
        assert "restoreFocus();" in _function_body(page, "closeHistory")

    def test_history_modal_moves_focus_inside(self, page: str) -> None:
        assert "focusInside(box);" in _function_body(page, "showHistory")

    def test_buttons_have_labels(self, page: str) -> None:
        assert '"aria-label", "확정 취소하고 다시 검토 상태로 되돌리기"' in page
        assert '"aria-label", "구매 " + purchaseId + " 의 검토 변경 이력 보기"' in page

    def test_escape_closes_the_undo_dialog_first(self, page: str) -> None:
        """확인창이 열려 있으면 Esc 는 그 창을 닫는다 — 목록은 움직이지 않는다."""
        handler = _function_body(page, "handleReviewKey")

        assert "undoIsOpen()" in handler
        assert handler.index("undoIsOpen") < handler.index('=== "n"')

    def test_no_shortcut_triggers_the_undo(self, page: str) -> None:
        """⛔ 어떤 키도 되돌리기를 실행하지 않는다."""
        handler = _function_body(page, "handleReviewKey")

        for banned in ("runUndo", "askUndo", "confirmReview", "Enter"):
            assert banned not in handler, banned

    def test_shortcut_hint_is_shown(self, page: str) -> None:
        """작업 L — 단축키를 화면에 적어 둔다."""
        assert "확정 단축키는 없습니다" in page
        assert "<kbd>N</kbd>" in page
        assert "<kbd>Esc</kbd>" in page


# ----------------------------------------------------------------------
# 진행률 · 목록
# ----------------------------------------------------------------------
class TestUndoProgress:
    """작업 G — 되돌리면 확정 수가 줄고, 다시 확정하면 늘어난다."""

    def test_whole_progress_moves_both_ways(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        first = add(purchases, "진행률 적요 1")
        second = add(purchases, "진행률 적요 2")
        for purchase_id in (first, second):
            service.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")
        assert service.progress().confirmed == 2

        service.reopen(first)
        assert service.progress().confirmed == 1
        assert service.progress().pending == 1

        service.confirm(first, final_purchase_type=GOODS, reviewed_by="김담당")
        assert service.progress().confirmed == 2

    def test_total_never_changes(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """분모는 구매 건수다 — 되돌린다고 대상이 사라지지 않는다."""
        purchase_id = add(purchases, "분모 적요")
        add(purchases, "분모 적요 2")
        service.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")

        service.reopen(purchase_id)

        assert service.progress().total == 2

    def test_condition_progress_follows(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """조건 안 진행률도 같이 움직인다."""
        first = add(purchases, "조건 적요")
        second = add(purchases, "조건 적요")
        for purchase_id in (first, second):
            service.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")

        service.reopen(first)

        condition = service.search(ReviewQuery(search="조건 적요")).condition
        assert condition.total == 2
        assert condition.confirmed == 1
        assert condition.pending == 1


class TestUndoList:
    """작업 F — 되돌린 건이 필터에서 제대로 움직인다."""

    def test_leaves_the_confirmed_filter(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        purchase_id = add(purchases, "확정 목록 적요")
        service.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")
        assert ids(service, status=CONFIRMED) == [purchase_id]

        service.reopen(purchase_id)

        assert ids(service, status=CONFIRMED) == []

    def test_appears_in_the_reopened_filter(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        purchase_id = add(purchases, "재검토 목록 적요")
        service.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")

        service.reopen(purchase_id)

        assert ids(service, status=REOPENED) == [purchase_id]

    def test_counts_as_undecided(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        """확정값은 남아 있지만 **확정된 상태는 아니다**."""
        purchase_id = add(purchases, "확정 여부 적요")
        service.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")

        service.reopen(purchase_id)

        assert ids(service, decision=UNDECIDED) == [purchase_id]
        assert ids(service, decision=DECIDED) == []

    def test_search_condition_still_applies(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        kept = add(purchases, "검색어 포함 적요")
        other = add(purchases, "관계없는 적요")
        for purchase_id in (kept, other):
            service.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")

        service.reopen(kept)

        assert ids(service, search="검색어", status=REOPENED) == [kept]

    def test_sort_order_is_kept(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        first = add(purchases, "정렬 적요 1")
        second = add(purchases, "정렬 적요 2")
        third = add(purchases, "정렬 적요 3")
        for purchase_id in (first, second, third):
            service.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")

        service.reopen(second)

        listed = ids(service, search="정렬 적요", sort="purchase_id", direction=DESCENDING)
        assert listed == [third, second, first]

    def test_page_condition_is_kept(
        self, service: ReviewService, purchases: PurchaseRepository
    ) -> None:
        created = [add(purchases, f"쪽 적요 {index}") for index in range(5)]
        for purchase_id in created:
            service.confirm(purchase_id, final_purchase_type=SERVICE, reviewed_by="김담당")

        service.reopen(created[2])

        page = service.search(ReviewQuery(search="쪽 적요", page=2, page_size=2))
        assert [target.purchase.purchase_id for target in page.items] == created[2:4]
        assert page.page.total == 5
