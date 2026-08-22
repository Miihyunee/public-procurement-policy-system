"""STEP 18 — 검토 카드의 정보 밀도.

한 건을 판단하는 데 필요한 것(적요·거래처·금액·일자·예산과목·상태·확정
조작)은 **늘 카드 위쪽에** 있고, 참고 정보(분석 결과·과거 확정 이력)는
**기본 접힘**입니다.

.. warning::
    ⛔ **정보를 지운 것이 아닙니다.** 늘 펼쳐져 있던 것을 필요할 때 펼쳐 보게
    바꿨을 뿐이며, 펼치면 예전과 같은 내용이 그대로 나옵니다. 이 파일의 여러
    검사가 그 사실을 고정합니다.

⛔ 업무규칙은 하나도 바뀌지 않았습니다 — 구매유형을 시스템이 고르지 않고,
자동확정도 없으며, 미적재 행의 처리 방식은 여전히 확인 전입니다(Q5-8).

⚠️ 화면 소스를 검사합니다. 실제 동작(접기·펼치기·키보드·확정)은 실브라우저로
따로 확인했으며, 여기서 고정하는 것은 **그 동작이 사라지지 않도록 막는 최소
조건**입니다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

INDEX = (
    Path(__file__).resolve().parents[1] / "src" / "procurement" / "web" / "static" / "index.html"
)


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


def _code_only(script: str) -> str:
    """``//`` · ``*`` 로 시작하는 주석 줄을 걷어낸 코드만 남깁니다."""
    return "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith(("//", "*", "/*"))
    )


# ----------------------------------------------------------------------
# 3. A — 핵심 판단 정보는 늘 보인다
# ----------------------------------------------------------------------
class TestCoreInformationIsAlwaysShown:
    """지시 §3 — 카드가 열리면 판단에 필요한 것이 먼저 보인다."""

    def test_source_fields_stay_in_the_card(self, page: str) -> None:
        body = _function_body(page, "reviewSource")

        for label in ("신고기준일", "결의일자", "거래처명", "사업자번호", "예산과목"):
            assert label in body, label
        assert "source.description" in body
        assert "금액(VAT 포함)" in body

    def test_source_is_not_foldable(self, page: str) -> None:
        """⛔ 핵심 정보를 접지 않는다."""
        body = _function_body(page, "reviewSource")

        assert "foldable(" not in body
        assert "details" not in body

    def test_state_line_shows_status_and_choice(self, page: str) -> None:
        body = _function_body(page, "reviewStateLine")

        assert "검토 상태" in body
        assert "선택된 구매유형" in body
        assert "review.status" in body

    def test_state_line_reads_the_server_value(self, page: str) -> None:
        """⛔ 상태나 유형을 화면이 만들어 내지 않는다."""
        body = _code_only(_function_body(page, "reviewStateLine"))

        for banned in ("candidates", "analysis", "score", "past"):
            assert banned not in body, banned

    def test_card_order_puts_the_core_first(self, page: str) -> None:
        body = _function_body(page, "reviewCard")

        assert body.index("reviewSource(") < body.index("reviewStateLine(")
        assert body.index("reviewStateLine(") < body.index("analysisFold(")
        assert body.index("analysisFold(") < body.index("pastFold(")

    def test_confirm_controls_come_before_the_folds(self, page: str) -> None:
        """확정 조작이 참고 정보보다 위에 있어야 스크롤이 짧아진다."""
        body = _function_body(page, "reviewCard")

        assert body.index("reviewPicker(item)") < body.index("analysisFold(")
        assert body.index('make("button", "control", "확정 취소")') < body.index("analysisFold(")

    def test_both_states_get_the_folds(self, page: str) -> None:
        """확정된 카드에서도 참고 정보가 사라지지 않는다."""
        body = _function_body(page, "reviewCard")
        after_branch = body[body.index("} else {") :]

        assert "analysisFold(item.analysis)" in after_branch
        assert "pastFold(item.past_labels)" in after_branch
        # ⛔ 확정 분기에서 일찍 빠져나가면 접힘 블록이 붙지 않는다.
        assert "return card;\n    }" not in body


# ----------------------------------------------------------------------
# 4·5. B·C — 참고 정보는 기본 접힘
# ----------------------------------------------------------------------
class TestFoldsAreClosedByDefault:
    """지시 §4 · §5 — 기본 상태는 접힘."""

    def test_foldable_uses_native_details(self, page: str) -> None:
        """지시 §7 — 브라우저 기본 접근성을 우선 쓴다."""
        body = _function_body(page, "foldable")

        assert 'make("details"' in body
        assert 'make("summary")' in body

    def test_foldable_never_opens_itself(self, page: str) -> None:
        """``open`` 을 켜지 않으므로 기본은 닫힘이다."""
        body = _code_only(_function_body(page, "foldable"))

        assert ".open = true" not in body
        assert 'setAttribute("open"' not in body

    def test_analysis_is_folded(self, page: str) -> None:
        body = _function_body(page, "analysisFold")

        assert "foldable(reviewAnalysis(analysis)" in body

    def test_past_labels_are_folded(self, page: str) -> None:
        body = _function_body(page, "pastFold")

        assert "foldable(reviewPastLabels(past)" in body

    def test_folded_line_says_what_is_inside(self, page: str) -> None:
        """접힌 채로도 안에 무엇이 몇 건 있는지 알 수 있어야 한다."""
        assert "analysis.candidate_count" in _function_body(page, "analysisFold")
        assert "past.total" in _function_body(page, "pastFold")

    def test_warnings_survive_the_fold(self, page: str) -> None:
        """접어 두면 놓치는 표시는 펼침 줄로 옮겼다 — 지운 것이 아니다."""
        assert "확인 권장 — 후보가 갈립니다" in _function_body(page, "analysisFold")
        assert "과거 판단이 갈렸던 적요입니다" in _function_body(page, "pastFold")

    def test_marker_is_not_a_grade(self, page: str) -> None:
        """⛔ 접기 표시에 달성률 구간색을 쓰지 않는다."""
        start = page.index(".rv-fold {")
        assert "LEVEL_COLOR" not in page[start : start + 700]


class TestNothingWasDeleted:
    """지시 §8 — 밀도 개선은 정보 삭제가 아니다."""

    def test_analysis_body_keeps_every_field(self, page: str) -> None:
        body = _function_body(page, "reviewAnalysis")

        for label in ("분석 방법", "후보 개수", "1·2순위 점수차", "순위"):
            assert label in body, label
        assert "candidate.label" in body
        assert "candidate.score" in body
        assert "candidate.evidence" in body

    def test_analysis_keeps_the_empty_case(self, page: str) -> None:
        body = _function_body(page, "reviewAnalysis")

        assert "분석 후보가 없습니다" in body

    def test_past_body_keeps_every_field(self, page: str) -> None:
        body = _function_body(page, "reviewPastLabels")

        for label in ("일관성", "최다 유형", "유형 수"):
            assert label in body, label
        assert "과거 확정 이력 없음" in body
        assert "differs_from_top_candidate" in body

    def test_confirmed_block_keeps_every_field(self, page: str) -> None:
        body = _function_body(page, "reviewCard")

        for label in ("확정 유형", "확정자", "확정 시각", "메모"):
            assert label in body, label

    def test_missing_actor_is_still_named_on_screen(self, page: str) -> None:
        """지시 §5 — DB 는 null 을 유지하고, 화면만 읽어 준다."""
        body = _function_body(page, "actorName")

        assert "담당자 미입력" in body

    def test_history_button_still_exists(self, page: str) -> None:
        body = _function_body(page, "reviewCard")

        assert body.count("historyButton(item.source.purchase_id)") == 2

    def test_note_field_survives(self, page: str) -> None:
        body = _function_body(page, "reviewPicker")

        assert "메모 (선택)" in body
        assert "item.review.review_note" in body


# ----------------------------------------------------------------------
# 7. 키보드 UX 유지
# ----------------------------------------------------------------------
class TestKeyboardStillWorks:
    """지시 §7 — STEP 17 의 키보드 UX 를 깨지 않는다."""

    def test_arrow_and_letter_keys_survive(self, page: str) -> None:
        handler = _function_body(page, "handleReviewKey")

        assert '"ArrowDown"' in handler
        assert '"ArrowUp"' in handler
        assert 'key === "n"' in handler
        assert 'key === "p"' in handler

    def test_enter_still_only_moves_focus(self, page: str) -> None:
        handler = _function_body(page, "handleReviewKey")

        assert '"Enter"' in handler
        assert "controls[0].focus()" in handler
        for banned in ("confirmReview", "runUndo", "askUndo"):
            assert banned not in handler, banned

    def test_escape_still_closes_the_modals(self, page: str) -> None:
        handler = _function_body(page, "handleReviewKey")

        assert "closeUndo()" in handler
        assert "closeHistory()" in handler

    def test_enter_lands_on_a_confirm_control_not_a_fold(self, page: str) -> None:
        """확정 조작이 접힘 블록보다 앞에 있으므로 첫 컨트롤은 유형 버튼이다."""
        card = _function_body(page, "reviewCard")
        controls = _function_body(page, "cardControls")

        assert "summary" not in controls
        assert card.index("reviewPicker(item)") < card.index("analysisFold(")

    def test_roving_tabindex_is_untouched(self, page: str) -> None:
        body = _function_body(page, "focusCursor")

        assert "tabIndex = on ? 0 : -1" in body

    def test_no_custom_toggle_was_built(self, page: str) -> None:
        """커스텀 토글 대신 ``details`` 를 썼으므로 aria 를 손으로 관리하지 않는다."""
        body = _function_body(page, "foldable")

        assert "aria-expanded" not in body
        assert "addEventListener" not in body


# ----------------------------------------------------------------------
# 15. 펼쳐도 서버에 묻지 않는다
# ----------------------------------------------------------------------
class TestFoldingCostsNothing:
    """지시 §15 — 접기/펼치기 때문에 요청이 늘면 안 된다."""

    def test_folding_sends_no_request(self, page: str) -> None:
        for name in ("foldable", "analysisFold", "pastFold", "reviewStateLine"):
            body = _code_only(_function_body(page, name))
            for banned in ("fetch(", "fetchJson(", "/reviews", "/imports"):
                assert banned not in body, f"{name}: {banned}"

    def test_card_renders_from_the_payload(self, page: str) -> None:
        body = _code_only(_function_body(page, "reviewCard"))

        for banned in ("fetch(", "fetchJson("):
            assert banned not in body, banned

    def test_no_new_api_was_added(self, page: str) -> None:
        """지시 §15 — 새 API 없음. 화면이 부르는 경로는 그대로다."""
        assert "/reviews/options" in page
        assert "/imports/periods" in page
        assert "/imports/trace" in page

    def test_no_ui_library_was_added(self, page: str) -> None:
        """지시 §12 — 새 라이브러리·프레임워크를 넣지 않는다."""
        for banned in ("<script src=", "cdn.", "unpkg", "jsdelivr", "import "):
            assert banned not in page, banned


# ----------------------------------------------------------------------
# 9·10·11. 기존 기능 회귀
# ----------------------------------------------------------------------
class TestEarlierStepsSurvive:
    """지시 §9 · §10 · §11 — STEP 16~17 이 그대로 살아 있다."""

    def test_url_keys_are_unchanged(self, page: str) -> None:
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

    def test_fold_state_is_not_in_the_url(self, page: str) -> None:
        """지시 §9 — 접힘 상태는 URL 에 넣지 않는다."""
        params = _function_body(page, "reviewParams")

        for banned in ("open", "fold", "expand", "details"):
            assert banned not in params, banned

    def test_progress_still_excludes_rejected_rows(self, page: str) -> None:
        body = _function_body(page, "renderProgress")

        assert "condition.total" in body
        assert "rejected" not in body

    def test_period_note_still_shown(self, page: str) -> None:
        body = _function_body(page, "appendPeriodNote")

        assert "검토 대상" in body
        assert "미적재" in body
        assert "appendReasonNote" in body

    def test_reason_summary_wording_is_unchanged(self, page: str) -> None:
        body = _function_body(page, "appendReasonNote")

        assert "미적재 없음" in body
        for banned in ("제외", "부적합", "검토 불필요", "오류 데이터"):
            assert banned not in body, banned

    def test_confirm_and_undo_paths_are_unchanged(self, page: str) -> None:
        confirm = _function_body(page, "confirmReview")
        undo = _function_body(page, "runUndo")

        assert '"PUT"' in confirm
        assert "refreshPeriods().then(function () { loadReviews(); })" in confirm
        assert "/reopen" in undo
        assert "refreshPeriods().then(function () { loadReviews(); })" in undo

    def test_confirm_still_requires_a_chosen_value(self, page: str) -> None:
        """⛔ 자동확정 없음 — 담당자가 고르지 않으면 확정되지 않는다."""
        body = _function_body(page, "reviewPicker")

        assert "if (!(id in reviewPicks))" in body

    def test_nothing_is_preselected(self, page: str) -> None:
        body = _function_body(page, "reviewPicker")

        assert "선택된 항목이 없습니다" in body
        for banned in ("candidates[0]", "dominant_label", "score"):
            assert banned not in body, banned
