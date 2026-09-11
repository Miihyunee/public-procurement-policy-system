"""STEP 17 — 키보드 전용 검토 조작과 기간별 미적재 사유 요약.

담당자는 매달 수백 건을 연속으로 검토합니다. 마우스로만 조작하면 카드마다
손이 오가야 하므로, **키보드만으로 행을 옮기고 확정 컨트롤까지 닿을 수 있어야**
합니다.

.. warning::
    ⛔ **자동확정을 만들지 않습니다.** 키는 이동과 포커스 이동만 합니다. 어떤
    키도 ``confirmReview`` · ``runUndo`` 를 부르지 않으며, 구매유형을 시스템이
    미리 고르지도 않습니다. 확정은 담당자가 버튼을 눌러야 일어납니다.

⛔ 미적재 사유 요약은 **사실 표시**입니다. "제외" · "검토 불필요" 를 뜻하지
않습니다 — 미적재 행의 처리 방식은 아직 확인 전입니다(Q5-8).

⚠️ 데이터는 전부 **합성**입니다.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.database.bootstrap import bootstrap
from procurement.database.company_repository import CompanyRepository
from procurement.database.import_batch_repository import ImportBatchRepository
from procurement.database.import_rejection_repository import ImportRejectionRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.importers.batch_import_service import BatchImportResult, BatchImportService
from procurement.importers.purchase_importer import PurchaseImporter
from procurement.models.import_rejection import (
    REASON_MISSING_REQUIRED,
    REASON_NON_POSITIVE_AMOUNT,
    REJECTION_REASON_LABELS,
)

INDEX = (
    Path(__file__).resolve().parents[1] / "src" / "procurement" / "web" / "static" / "index.html"
)

MONTHS: dict[str, tuple[date, date]] = {
    "2026-01": (date(2026, 1, 1), date(2026, 1, 31)),
    "2026-02": (date(2026, 2, 1), date(2026, 2, 28)),
    "2026-03": (date(2026, 3, 1), date(2026, 3, 31)),
}

#: 화면·API·CSV 어디에도 나오면 안 되는 말. 아직 아무것도 정해지지 않았다.
BANNED_WORDS = (
    "제외",
    "정상 제외",
    "부적합",
    "실적 제외",
    "검토 불필요",
    "처리 완료",
    "오류 데이터",
    "무시",
)


@pytest.fixture(scope="module")
def page() -> str:
    return INDEX.read_text(encoding="utf-8")


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "keyboard.db"
    bootstrap(path)
    return path


@pytest.fixture
def service(db_path: Path) -> BatchImportService:
    return BatchImportService(
        PurchaseImporter(PurchaseRepository(db_path), CompanyRepository(db_path)),
        ImportBatchRepository(db_path),
        PurchaseRepository(db_path),
        ImportRejectionRepository(db_path),
    )


@pytest.fixture
def client(db_path: Path) -> TestClient:
    return TestClient(create_app(db_path, period_date_field="resolution_date"))


def row(
    *,
    amount: object = "1000000",
    description: str = "합성 적요",
    business_no: str | None = "111-11-11111",
) -> dict[str, Any]:
    return {
        "business_no": business_no,
        "company_name": "합성거래처",
        "contract_date": "2026-03-01",
        "payment_date": "2026-03-20",
        "resolution_date": "2026-03-25",
        "issue_date": "2026-03-10",
        "description": description,
        "budget_account": "임차료",
        "amount": amount,
    }


def upload(
    service: BatchImportService,
    label: str,
    *,
    normal: int,
    negative: int = 0,
    missing: int = 0,
    tag: str | None = None,
) -> BatchImportResult:
    """한 달치를 올린다. ``negative`` 와 ``missing`` 은 서로 다른 사유가 된다."""
    mark = tag or label
    start, end = MONTHS[label]
    rows = [row(description=f"{mark} 적요 {index}") for index in range(normal)]
    rows += [
        row(amount=f"-{index + 1}", description=f"{mark} 음수 {index}") for index in range(negative)
    ]
    rows += [row(business_no=None, description=f"{mark} 누락 {index}") for index in range(missing)]
    return service.import_batch(rows, file_name=f"{label}.xlsx", period_start=start, period_end=end)


def periods(client: TestClient) -> dict[str, dict[str, Any]]:
    body = client.get("/imports/periods").json()
    return {item["label"]: item for item in body["items"]}


def reasons_of(item: dict[str, Any]) -> dict[str, int]:
    return {entry["reason"]: entry["count"] for entry in item["reasons"]}


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


def _code_only(script: str) -> str:
    """``//`` · ``*`` 로 시작하는 주석 줄을 걷어낸 코드만 남깁니다."""
    return "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith(("//", "*", "/*"))
    )


# ----------------------------------------------------------------------
# 3. 키보드 전용 조작
# ----------------------------------------------------------------------
class TestKeyboardMovement:
    """지시 §3-2 — 최소한의 키만, 기본 동작을 깨지 않고."""

    def test_arrow_keys_move_the_cursor(self, page: str) -> None:
        handler = _function_body(page, "handleReviewKey")

        assert '"ArrowDown"' in handler
        assert '"ArrowUp"' in handler
        assert "moveCursor(1, true)" in handler
        assert "moveCursor(-1, true)" in handler

    def test_arrow_keys_only_act_inside_the_list(self, page: str) -> None:
        """⛔ 화면 아무 데서나 방향키를 빼앗지 않는다 — 평소 스크롤이 살아 있다."""
        handler = _function_body(page, "handleReviewKey")

        for key in ('"ArrowDown"', '"ArrowUp"'):
            line = [row for row in handler.splitlines() if key in row][0]
            assert "insideList(event.target)" in line, key

    def test_typing_still_wins(self, page: str) -> None:
        """검색창·선택상자 안에서는 단축키가 동작하지 않는다."""
        handler = _function_body(page, "handleReviewKey")
        typing = _function_body(page, "isTyping")

        assert "isTyping(event.target)" in handler
        for tag in ('"INPUT"', '"TEXTAREA"', '"SELECT"'):
            assert tag in typing, tag

    def test_typing_guard_comes_before_the_arrows(self, page: str) -> None:
        """순서가 중요하다 — 먼저 걸러야 검색창에서 목록이 움직이지 않는다."""
        handler = _function_body(page, "handleReviewKey")

        assert handler.index("isTyping(event.target)") < handler.index('"ArrowDown"')

    def test_browser_shortcuts_are_left_alone(self, page: str) -> None:
        handler = _function_body(page, "handleReviewKey")

        assert "event.ctrlKey || event.metaKey || event.altKey" in handler

    def test_existing_shortcuts_survive(self, page: str) -> None:
        handler = _function_body(page, "handleReviewKey")

        assert 'key === "n"' in handler
        assert 'key === "p"' in handler


class TestKeyboardNeverConfirms:
    """지시 §3-5 · §18 — 자동확정이 생길 길을 막는다."""

    def test_no_key_confirms_or_undoes(self, page: str) -> None:
        handler = _code_only(_function_body(page, "handleReviewKey"))

        for banned in ("confirmReview", "runUndo", "askUndo", "fetch("):
            assert banned not in handler, banned

    def test_enter_only_moves_focus(self, page: str) -> None:
        """Enter 는 카드 **안으로 들어갈** 뿐이다."""
        handler = _function_body(page, "handleReviewKey")

        assert '"Enter"' in handler
        assert "controls[0].focus()" in handler

    def test_moving_the_cursor_sends_nothing(self, page: str) -> None:
        """지시 §14 — 단순 행 이동으로 서버 요청이 생기면 안 된다."""
        body = _code_only(_function_body(page, "focusCursor"))

        for banned in ("fetch(", "fetchJson(", "/reviews"):
            assert banned not in body, banned

    def test_advance_after_confirm_only_moves(self, page: str) -> None:
        body = _code_only(_function_body(page, "advanceAfterConfirm"))

        for banned in ("fetch(", "confirmReview", "reviewPicks"):
            assert banned not in body, banned
        assert "reviewCursor += 1" in body

    def test_confirm_still_needs_a_chosen_value(self, page: str) -> None:
        """⛔ 후보 1순위를 미리 넣어 두지 않는다."""
        body = _function_body(page, "reviewPicker")

        assert "if (!(id in reviewPicks))" in body
        assert "먼저 유형을 선택하세요" in body


class TestCursorIsScreenOnly:
    """지시 §3-4 · §7 — 현재 행은 화면 상태일 뿐이다."""

    def test_cursor_is_not_in_the_url(self, page: str) -> None:
        params = _function_body(page, "reviewParams")

        for banned in ("cursor", "reviewCursor", "focus"):
            assert banned not in params, banned

    def test_url_keys_are_unchanged(self, page: str) -> None:
        """STEP 16 의 조회 상태가 그대로 유지된다."""
        params = _function_body(page, "reviewParams")

        for key in (
            "search=",
            "status=",
            "decision=",
            "history=",
            "candidates=",
            "sort=",
            "direction=",
            "batch_id=",
            "page=",
            "page_size=",
        ):
            assert key in params, key

    def test_cursor_is_never_saved(self, page: str) -> None:
        body = _code_only(_function_body(page, "moveCursor"))

        for banned in ("localStorage", "sessionStorage", "PUT", "POST"):
            assert banned not in body, banned


class TestFocusIsVisible:
    """지시 §3-3 · §13 — 지금 어디에 있는지 보인다."""

    def test_cards_can_take_focus(self, page: str) -> None:
        body = _function_body(page, "reviewCard")

        assert "card.tabIndex = -1" in body

    def test_current_card_is_the_only_tab_stop(self, page: str) -> None:
        """roving tabindex — Tab 한 번에 카드 수백 개를 지나가지 않게."""
        body = _function_body(page, "focusCursor")

        assert "tabIndex = on ? 0 : -1" in body

    def test_current_card_is_announced(self, page: str) -> None:
        body = _function_body(page, "focusCursor")

        assert '"aria-current"' in body

    def test_focus_outline_is_styled(self, page: str) -> None:
        assert ".rv:focus-visible" in page
        assert ":focus-visible {" in page

    def test_focus_style_is_not_a_grade(self, page: str) -> None:
        """⛔ 포커스 표시에 달성률 구간색(LEVEL_COLOR)을 쓰지 않는다."""
        start = page.index(".rv:focus-visible")
        assert "LEVEL_COLOR" not in page[start : start + 200]

    def test_cards_are_labelled(self, page: str) -> None:
        body = _function_body(page, "reviewCard")

        assert '"aria-label"' in body


class TestModalKeyboard:
    """지시 §3-2 · §13 — 확인창에서 키보드가 갇히지도, 새지도 않는다."""

    def test_escape_closes_both_modals(self, page: str) -> None:
        handler = _function_body(page, "handleReviewKey")

        assert "closeUndo()" in handler
        assert "closeHistory()" in handler

    def test_tab_stays_inside_the_modal(self, page: str) -> None:
        handler = _function_body(page, "handleReviewKey")

        assert 'trapTab(event, "review-undo-modal")' in handler
        assert 'trapTab(event, "review-modal")' in handler

    def test_trap_wraps_both_directions(self, page: str) -> None:
        body = _function_body(page, "trapTab")

        assert "event.shiftKey" in body
        assert "last.focus()" in body
        assert "first.focus()" in body

    def test_undo_defaults_to_the_safe_button(self, page: str) -> None:
        """⛔ 기본 포커스는 '취소' 다 — Enter 를 잘못 눌러 확정이 풀리지 않도록."""
        body = _function_body(page, "askUndo")

        assert "cancel.focus()" in body

    def test_focus_returns_after_closing(self, page: str) -> None:
        assert "function rememberFocus(" in page
        assert "function restoreFocus(" in page
        assert "restoreFocus()" in _function_body(page, "closeUndo")
        assert "restoreFocus()" in _function_body(page, "closeHistory")

    def test_undo_keeps_its_meaning(self, page: str) -> None:
        """지시 §3-6 — Undo 는 상태만 되돌린다. 값을 지우지 않는다."""
        body = _function_body(page, "runUndo")

        assert "/reopen" in body
        for banned in ("DELETE", "final_purchase_type"):
            assert banned not in body, banned


class TestContinuousReview:
    """지시 §3-5 — 확정하면 자연스럽게 다음 행으로."""

    def test_confirm_marks_the_row(self, page: str) -> None:
        body = _function_body(page, "confirmReview")

        assert "reviewJustConfirmed = purchaseId" in body

    def test_advance_only_when_the_row_stayed(self, page: str) -> None:
        body = _function_body(page, "advanceAfterConfirm")

        assert "stayed" in body
        assert "reviewCursor + 1 < reviewItems.length" in body

    def test_failure_does_not_advance(self, page: str) -> None:
        """⛔ 실패했으면 넘어가지 않는다 — 고른 값도 남는다."""
        body = _function_body(page, "confirmReview")
        after = body[body.index(".catch(") :]

        assert "reviewJustConfirmed" not in after
        assert "delete reviewPicks" not in after

    def test_confirm_still_refreshes_the_progress_first(self, page: str) -> None:
        """STEP 16 회귀 — 진행률이 목록보다 먼저 갱신되어야 숫자가 맞는다."""
        body = _function_body(page, "confirmReview")

        assert "refreshPeriods().then(function () { loadReviews(); })" in body


# ----------------------------------------------------------------------
# 4. 기간별 미적재 사유 요약
# ----------------------------------------------------------------------
class TestPeriodReasonSummary:
    """지시 §4 — 사유별 건수를 사실 그대로."""

    def test_period_carries_its_reasons(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        upload(service, "2026-03", normal=6, negative=3)

        march = periods(client)["2026-03"]

        assert march["rejected"] == 3
        assert reasons_of(march) == {REASON_NON_POSITIVE_AMOUNT: 3}

    def test_reasons_are_split_by_kind(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        """⚠️ 사유가 여러 개면 나눠서 센다 — 지금 데이터가 한 가지뿐이어도."""
        upload(service, "2026-03", normal=4, negative=2, missing=3)

        found = reasons_of(periods(client)["2026-03"])

        assert found[REASON_NON_POSITIVE_AMOUNT] == 2
        assert found[REASON_MISSING_REQUIRED] == 3

    def test_counts_add_up_to_rejected(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        upload(service, "2026-03", normal=4, negative=2, missing=3)

        march = periods(client)["2026-03"]

        assert sum(reasons_of(march).values()) == march["rejected"]

    def test_no_rejections_gives_an_empty_list(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        upload(service, "2026-02", normal=4)

        february = periods(client)["2026-02"]

        assert february["rejected"] == 0
        assert february["reasons"] == []

    def test_each_period_counts_only_itself(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        upload(service, "2026-01", normal=5, negative=2)
        upload(service, "2026-02", normal=4, missing=1)
        upload(service, "2026-03", normal=6, negative=3)

        found = periods(client)

        assert reasons_of(found["2026-01"]) == {REASON_NON_POSITIVE_AMOUNT: 2}
        assert reasons_of(found["2026-02"]) == {REASON_MISSING_REQUIRED: 1}
        assert reasons_of(found["2026-03"]) == {REASON_NON_POSITIVE_AMOUNT: 3}

    def test_labels_come_from_the_shared_table(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        """⛔ 화면·API 가 사유 이름을 따로 짓지 않는다."""
        upload(service, "2026-03", normal=4, negative=2)

        entry = periods(client)["2026-03"]["reasons"][0]

        assert entry["label"] == REJECTION_REASON_LABELS[REASON_NON_POSITIVE_AMOUNT]

    def test_labels_avoid_deciding_words(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        upload(service, "2026-03", normal=4, negative=2, missing=1)

        for entry in periods(client)["2026-03"]["reasons"]:
            for banned in BANNED_WORDS:
                assert banned not in entry["label"], entry


class TestReasonSummaryUsesCurrentBatch:
    """지시 §4-3 · §11 — 대체된 배치가 섞이면 안 된다."""

    def test_reupload_replaces_rather_than_adds(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        upload(service, "2026-03", normal=6, negative=3)
        upload(service, "2026-03", normal=5, negative=2, tag="재업로드")

        march = periods(client)["2026-03"]

        assert march["rejected"] == 2, "대체된 배치의 미적재가 더해졌다"
        assert reasons_of(march) == {REASON_NON_POSITIVE_AMOUNT: 2}

    def test_reupload_keeps_one_period(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        upload(service, "2026-03", normal=6, negative=3)
        upload(service, "2026-03", normal=5, negative=2, tag="재업로드")

        labels = [item["label"] for item in client.get("/imports/periods").json()["items"]]

        assert labels.count("2026-03") == 1

    def test_period_points_at_the_new_batch(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        first = upload(service, "2026-03", normal=6, negative=3)
        second = upload(service, "2026-03", normal=5, negative=2, tag="재업로드")

        march = periods(client)["2026-03"]

        assert march["batch_id"] == second.batch.batch_id
        assert march["batch_id"] != first.batch.batch_id

    def test_reason_summary_matches_the_rejection_list(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        """화면 두 곳의 숫자가 같아야 한다 — 요약과 목록."""
        upload(service, "2026-03", normal=6, negative=3)
        upload(service, "2026-03", normal=5, negative=2, tag="재업로드")
        march = periods(client)["2026-03"]

        listed = client.get(f"/imports/rejections?batch_id={march['batch_id']}").json()

        assert listed["total"] == march["rejected"]

    def test_old_batch_is_still_in_the_history(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        """⛔ 기록은 지우지 않는다 — 현재 집계에서 빠질 뿐이다."""
        first = upload(service, "2026-03", normal=6, negative=3)
        upload(service, "2026-03", normal=5, negative=2, tag="재업로드")

        history = client.get("/imports/batches").json()["items"]
        old = [item for item in history if item["batch_id"] == first.batch.batch_id][0]

        assert old["is_current"] is False
        assert old["rejected"] == 3


class TestWholeStillAddsUp:
    """지시 §5 — 원본 = 적재 + 미적재 + 설명되지 않는 행(0)."""

    def test_totals_survive_a_reupload(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        upload(service, "2026-01", normal=5, negative=2)
        upload(service, "2026-03", normal=6, negative=3)
        upload(service, "2026-03", normal=5, negative=2, tag="재업로드")

        trace = client.get("/imports/trace").json()

        assert trace["stored"] == 5 + 5
        assert trace["rejected"] == 2 + 2
        assert trace["source_rows"] == trace["stored"] + trace["rejected"]

    def test_period_sums_match_the_whole(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        upload(service, "2026-01", normal=5, negative=2)
        upload(service, "2026-02", normal=4, missing=1)
        upload(service, "2026-03", normal=6, negative=3)
        trace = client.get("/imports/trace").json()

        found = periods(client).values()

        assert sum(item["stored"] for item in found) == trace["stored"]
        assert sum(item["rejected"] for item in found) == trace["rejected"]

    def test_every_batch_is_fully_explained(
        self, service: BatchImportService, client: TestClient
    ) -> None:
        """설명되지 않는 행이 하나도 없어야 한다."""
        upload(service, "2026-01", normal=5, negative=2)
        upload(service, "2026-03", normal=6, negative=3, missing=1)

        for item in client.get("/imports/batches").json()["items"]:
            assert item["unexplained"] == 0, item["batch_id"]


class TestReasonSummaryOnScreen:
    """지시 §4 — 화면이 사유를 만들어 내지 않는다."""

    def test_summary_comes_from_the_backend(self, page: str) -> None:
        body = _function_body(page, "appendReasonNote")

        assert "reason.label" in body
        assert "reason.count" in body

    def test_reason_codes_are_not_hardcoded(self, page: str) -> None:
        """⛔ 사유가 늘어도 화면을 고칠 필요가 없어야 한다."""
        body = _function_body(page, "appendReasonNote")

        for banned in ("NON_POSITIVE_AMOUNT", "MISSING_REQUIRED", "금액이 0 이하"):
            assert banned not in body, banned

    def test_empty_case_is_stated(self, page: str) -> None:
        body = _function_body(page, "appendReasonNote")

        assert "미적재 없음" in body

    def test_whole_period_reuses_the_loaded_trace(self, page: str) -> None:
        """지시 §6 — 같은 숫자를 위해 API 를 다시 부르지 않는다."""
        body = _function_body(page, "appendWholeRejectionNote")

        assert "lastTrace" in body
        for banned in ("fetch(", "fetchJson("):
            assert banned not in body, banned

    def test_period_note_reuses_the_loaded_options(self, page: str) -> None:
        body = _function_body(page, "appendPeriodNote")

        assert "periodOptions" in body
        for banned in ("fetch(", "fetchJson("):
            assert banned not in body, banned

    def test_summary_avoids_deciding_words(self, page: str) -> None:
        body = _code_only(_function_body(page, "appendReasonNote"))
        note = _code_only(_function_body(page, "appendPeriodNote"))

        for banned in BANNED_WORDS:
            assert banned not in body, banned
            assert banned not in note, banned

    def test_rejected_stays_out_of_the_denominator(self, page: str) -> None:
        """⛔ STEP 16 규칙 유지 — 미적재를 진행률 분모에 더하지 않는다."""
        body = _function_body(page, "renderProgress")

        assert "condition.total" in body
        assert "rejected" not in body
