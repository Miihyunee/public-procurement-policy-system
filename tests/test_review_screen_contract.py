"""STEP 9 — 검토 화면이 지켜야 할 약속.

브라우저를 띄우지 않고 ``index.html`` 소스를 검사합니다. 이 저장소의 기존
화면 테스트(``test_dashboard_page.py`` 등)와 같은 방식입니다.

실제 동작(연속 검토 · 단축키 충돌 · 새로고침 복원)은 합성 데이터로 서버를
띄워 별도로 확인했습니다. 여기서 고정하는 것은 **그 동작이 사라지지 않도록
막는 최소 조건**입니다.

⛔ 특히 "자동 확정으로 새어 나가는 길" 이 생기지 않는지를 봅니다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

INDEX = (
    Path(__file__).resolve().parents[1] / "src" / "procurement" / "web" / "static" / "index.html"
)


@pytest.fixture(scope="module")
def page() -> str:
    return INDEX.read_text(encoding="utf-8")


class TestElementIdsAreUnique:
    """같은 id 가 둘이면 ``getElementById`` 가 엉뚱한 것을 집는다.

    STEP 9 에서 실제로 겪었습니다 — 이력 모달의 id 를 ``review-history`` 로
    두었더니 **과거 이력 필터 select** 와 겹쳐, 이력 내용이 select 안에
    그려졌습니다.
    """

    def test_no_duplicate_ids(self, page: str) -> None:
        found = re.findall(r'\bid="([^"]+)"', page)

        duplicates = {value for value in found if found.count(value) > 1}
        assert duplicates == set(), duplicates


class TestKeyboardShortcutsAreSafe:
    """작업 C — 단축키가 입력·모달과 충돌하지 않는다 (지시 G-3)."""

    def test_typing_guard_exists(self, page: str) -> None:
        """입력 중에는 단축키를 가로채면 안 된다."""
        assert "function isTyping(" in page
        for tag in ('"INPUT"', '"TEXTAREA"', '"SELECT"'):
            assert tag in page, tag
        assert "isContentEditable" in page

    def test_handler_checks_typing_before_moving(self, page: str) -> None:
        handler = _function_body(page, "handleReviewKey")

        assert "isTyping(event.target)" in handler
        # 이동보다 **먼저** 막아야 한다
        assert handler.index("isTyping") < handler.index('=== "n"')

    def test_modal_blocks_list_movement(self, page: str) -> None:
        """모달이 열려 있으면 뒤쪽 목록이 움직이지 않는다."""
        handler = _function_body(page, "handleReviewKey")

        assert "historyIsOpen()" in handler
        assert handler.index("historyIsOpen") < handler.index('=== "n"')

    def test_browser_shortcuts_are_not_stolen(self, page: str) -> None:
        """Ctrl/Cmd/Alt 조합은 브라우저·OS 몫이다."""
        handler = _function_body(page, "handleReviewKey")

        for modifier in ("event.ctrlKey", "event.metaKey", "event.altKey"):
            assert modifier in handler, modifier

    def test_only_navigation_keys_are_bound(self, page: str) -> None:
        """이동(N/P)과 닫기(Escape)뿐이다."""
        handler = _function_body(page, "handleReviewKey")

        assert 'key === "n"' in handler
        assert 'key === "p"' in handler
        assert '"Escape"' in handler

    def test_no_key_confirms_a_review(self, page: str) -> None:
        """⛔ **가장 중요** — 어떤 키도 확정으로 이어지지 않는다.

        Enter 한 번에 확정되면, 담당자가 보지도 않은 건이 확정될 수 있다.
        """
        handler = _function_body(page, "handleReviewKey")

        for banned in ("confirmReview", "Enter", "reopenReview"):
            assert banned not in handler, banned


class TestNextTargetMovesOnly:
    """작업 B — 다음 대상으로 **이동만** 한다."""

    def test_move_cursor_does_not_confirm(self, page: str) -> None:
        body = _function_body(page, "moveCursor")

        for banned in ("confirmReview", "reviewPicks[", "final_purchase_type"):
            assert banned not in body, banned

    def test_move_cursor_keeps_conditions(self, page: str) -> None:
        """페이지를 넘어갈 때 조건을 다시 만들지 않고 그대로 불러온다."""
        body = _function_body(page, "moveCursor")

        assert "loadReviews()" in body
        # 조건을 손대는 흔적이 없어야 한다
        for banned in ("review-search", "review-status", "review-sort"):
            assert banned not in body, banned

    def test_end_of_list_is_a_message_not_an_error(self, page: str) -> None:
        body = _function_body(page, "moveCursor")

        assert "reviewNotice(" in body
        assert "마지막 검토 대상입니다" in body

    def test_confirm_moves_on_without_preselecting(self, page: str) -> None:
        """확정 후 목록을 다시 불러올 뿐, 다음 건을 미리 고르지 않는다.

        ``delete reviewPicks[...]`` 는 방금 확정한 건의 선택을 **지우는** 정상
        동작이다. 막아야 하는 것은 그 반대 — 선택을 **넣는** 대입이다.
        """
        body = _function_body(page, "confirmReview")

        assert "loadReviews()" in body
        assert "delete reviewPicks[purchaseId];" in body
        # ⛔ reviewPicks 에 값을 넣는 대입이 없어야 한다.
        assert not re.search(r"reviewPicks\[[^\]]*\]\s*=", body)


class TestHistoryViewSeparatesCurrentFromPast:
    """작업 A — 지금 값과 과거 기록이 섞이지 않는다."""

    def test_current_value_has_its_own_heading(self, page: str) -> None:
        body = _function_body(page, "showHistory")

        assert "지금 값 (현재 상태 기준)" in body
        assert "변경 이력 (과거 기록" in body

    def test_current_value_comes_from_the_current_review(self, page: str) -> None:
        """⛔ 이력의 한 줄을 현재값으로 쓰지 않는다."""
        body = _function_body(page, "showHistory")

        assert "current.review.final_purchase_type_label" in body
        assert "current.review.status" in body

    def test_pending_is_shown_as_undecided(self, page: str) -> None:
        """미확정인데 과거 유형이 남아 있어도 '(미확정)' 으로 보여준다."""
        body = _function_body(page, "showHistory")

        assert "(미확정)" in body

    def test_history_fetch_is_scoped_to_one_purchase(self, page: str) -> None:
        """이력을 보려고 목록 전체를 다시 가져오지 않는다 (지시 11)."""
        body = _function_body(page, "openHistory")

        assert "/history" in body
        assert "loadReviews" not in body


class TestProgressShowsBothScopes:
    """작업 D — 전체 진행률과 현재 조건 진행률."""

    def test_both_are_rendered(self, page: str) -> None:
        body = _function_body(page, "renderProgress")

        assert "payload.progress" in body
        assert "payload.condition" in body

    def test_identical_scopes_are_not_repeated_twice(self, page: str) -> None:
        """조건이 전체와 같으면 같은 숫자를 두 번 보여주지 않는다."""
        body = _function_body(page, "renderProgress")

        assert "현재 조건 = 전체" in body

    def test_no_judgement_words(self, page: str) -> None:
        """⛔ 진행률에 위험/적정 같은 판정을 붙이지 않는다."""
        body = _function_body(page, "renderProgress")

        for banned in ("위험", "적정", "미달", "경고", "양호"):
            assert banned not in body, banned


class TestUrlKeepsTheConditions:
    """작업 E — URL 에 조건을 유지한다 (지시 G-5)."""

    def test_sync_and_apply_exist(self, page: str) -> None:
        assert "function syncUrl(" in page
        assert "function applyUrl(" in page

    def test_condition_changes_push_history(self, page: str) -> None:
        """조건을 바꾸면 뒤로가기로 되돌아갈 수 있어야 한다."""
        body = _function_body(page, "syncUrl")

        assert "pushState" in body
        assert "replaceState" in body

    def test_reload_restores_before_loading(self, page: str) -> None:
        """목록을 부르기 **전에** URL 조건을 화면에 되돌려야 한다."""
        body = _function_body(page, "initReview")

        assert body.index("applyUrl()") < body.index("loadReviews()")

    def test_popstate_is_handled(self, page: str) -> None:
        assert 'addEventListener("popstate"' in page

    def test_unknown_select_value_is_ignored(self, page: str) -> None:
        """URL 에 이상한 값이 와도 화면이 깨지지 않는다."""
        body = _function_body(page, "applyUrl")

        assert "node.options" in body

    def test_bad_page_number_falls_back_to_one(self, page: str) -> None:
        body = _function_body(page, "applyUrl")

        assert "isNaN(page)" in body
        assert "page < 1" in body


class TestNoAutomaticDecisionInTheScreen:
    """⛔ 화면 어디에도 자동 확정 경로가 없다."""

    def test_no_threshold_comparisons(self, page: str) -> None:
        """``score > 0.9`` 같은 비교가 스크립트에 없어야 한다."""
        script = page.split("<script>")[-1]

        for pattern in (
            r"score\s*[<>]=?\s*0\.",
            r"score_gap\s*[<>]=?\s*0\.",
            r"dominant_ratio\s*[<>]=?\s*\d",
            r"past\.total\s*>=\s*\d",
        ):
            assert not re.search(pattern, script), pattern

    def test_no_preselected_option(self, page: str) -> None:
        """어떤 선택지도 미리 눌려 있지 않다."""
        body = _function_body(page, "reviewPicker")

        assert "선택된 항목이 없습니다" in body
        # 후보 점수를 보고 버튼을 켜는 코드가 없어야 한다
        assert "candidate" not in body

    def test_past_labels_do_not_fill_the_picker(self, page: str) -> None:
        body = _function_body(page, "reviewPastLabels")

        for banned in ("reviewPicks", "confirmReview", "rv-opt"):
            assert banned not in body, banned


def _function_body(page: str, name: str) -> str:
    """``function name(`` 부터 짝이 맞는 닫는 중괄호까지 잘라 냅니다."""
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
