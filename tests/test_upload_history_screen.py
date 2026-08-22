"""STEP 14 — 업로드 이력 화면과 미적재 조회 화면이 지켜야 할 약속.

브라우저를 띄우지 않고 ``index.html`` 소스를 검사합니다. 실제 동작(월별 업로드
리허설·재업로드·검색)은 합성 데이터로 서버를 띄워 따로 확인했으며, 여기서
고정하는 것은 **그 동작이 사라지지 않도록 막는 최소 조건**입니다.

⛔ 특히 "화면이 업무 판단을 하는 길" 이 생기지 않는지를 봅니다.
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


class TestHistorySection:
    """작업 B — 업로드 이력."""

    def test_section_exists(self, page: str) -> None:
        assert 'id="history-table"' in page
        assert 'id="history-detail"' in page
        assert "function loadHistory(" in page

    def test_uses_the_backend_list(self, page: str) -> None:
        body = _function_body(page, "loadHistory")

        assert "/imports/batches" in body

    def test_columns_cover_what_the_operator_needs(self, page: str) -> None:
        body = _function_body(page, "renderHistory")

        for label in ("기간", "파일", "업로드일시", "원본", "적재", "미적재", "상태"):
            assert f'"{label}"' in body, label

    def test_current_flag_comes_from_the_backend(self, page: str) -> None:
        """⛔ 화면이 상태 상수를 비교해 '현재' 를 스스로 정하지 않는다."""
        body = _function_body(page, "renderHistory")

        assert "item.is_current" in body
        assert "SUPERSEDED" not in body
        assert '"ACTIVE"' not in body

    def test_unknown_source_rows_show_a_dash_not_zero(self, page: str) -> None:
        """원본 행 수를 모르는 옛 배치를 0 으로 적으면 거짓말이 된다."""
        body = _function_body(page, "renderHistory")

        assert "item.source_rows === null" in body
        assert "item.unexplained === null" in body

    def test_empty_history_says_so(self, page: str) -> None:
        body = _function_body(page, "renderHistory")

        assert "아직 업로드한 파일이 없습니다" in body

    def test_detail_fetches_one_batch(self, page: str) -> None:
        body = _function_body(page, "showBatchDetail")

        assert "/imports/batches/" in body
        assert "loadHistory" not in body

    def test_no_delete_or_restore_control(self, page: str) -> None:
        """⛔ 배치 삭제·복구 기능을 만들지 않았다 (지시 3번)."""
        body = _function_body(page, "renderHistory")

        for banned in ("삭제", "복구", "되살리", "DELETE"):
            assert banned not in body, banned

    def test_failure_is_reported_not_swallowed(self, page: str) -> None:
        body = _function_body(page, "loadHistory")

        assert "historyError(" in body

    def test_reload_button_has_a_name(self, page: str) -> None:
        assert 'aria-label="업로드 이력 새로고침"' in page


class TestRejectionControls:
    """작업 E — 검색 · 사유 · 정렬 · 페이지. ⛔ 조회일 뿐이다."""

    def test_controls_exist(self, page: str) -> None:
        for element_id in (
            "rejection-search",
            "rejection-reason",
            "rejection-sort",
            "rejection-direction",
            "rejection-prev",
            "rejection-next",
            "rejection-page-info",
        ):
            assert f'id="{element_id}"' in page, element_id

    def test_search_is_debounced(self, page: str) -> None:
        """작업 H — 한 글자마다 요청하지 않는다."""
        body = _function_body(page, "initUpload")

        assert 'el("rejection-search").addEventListener("input", debounce(' in body

    def test_conditions_reset_to_the_first_page(self, page: str) -> None:
        body = _function_body(page, "reloadRejectionsFromFirstPage")

        assert "rejectionPage = 1" in body

    def test_paging_keeps_the_conditions(self, page: str) -> None:
        """쪽을 넘길 때 조건을 다시 만들지 않고 그대로 보낸다."""
        body = _function_body(page, "initUpload")
        paging = body[body.index('el("rejection-prev")') :]

        assert "loadRejections()" in paging
        for banned in ("rejection-search", "rejection-reason", "rejection-sort"):
            assert banned not in paging, banned

    def test_one_request_per_load(self, page: str) -> None:
        body = _function_body(page, "loadRejections")

        assert body.count("fetchJson(") == 1
        assert "/imports/rejections" in body

    def test_reason_options_come_from_the_backend(self, page: str) -> None:
        """⛔ 화면이 사유 목록을 스스로 만들지 않는다."""
        body = _function_body(page, "loadRejectionReasons")

        assert "/imports/trace" in body
        assert "item.label" in body
        for banned in ("NON_POSITIVE_AMOUNT", "MISSING_REQUIRED", "금액이 0 이하"):
            assert banned not in body, banned

    def test_reason_options_are_fetched_once(self, page: str) -> None:
        """작업 H — 열 때마다 다시 받지 않는다."""
        body = _function_body(page, "loadRejectionReasons")

        assert "select.options.length > 1" in body

    def test_reason_options_reuse_the_first_fetch(self, page: str) -> None:
        """⛔ 검토 화면이 이미 받아 둔 것을 또 받지 않는다."""
        body = _function_body(page, "loadRejectionReasons")

        assert "lastTrace ? Promise.resolve(lastTrace)" in body
        assert "lastTrace = trace;" in _function_body(page, "loadTrace")

    def test_empty_result_has_a_message(self, page: str) -> None:
        body = _function_body(page, "loadRejections")

        assert "조건에 맞는 미적재 행이 없습니다" in body

    def test_controls_are_labelled(self, page: str) -> None:
        for label in (
            "미적재 행 검색",
            "미적재 사유 필터",
            "미적재 행 정렬 기준",
            "정렬 방향",
            "미적재 행 이전 쪽",
            "미적재 행 다음 쪽",
        ):
            assert f'aria-label="{label}' in page, label

    def test_no_approval_control_exists(self, page: str) -> None:
        """⛔ 미적재 행을 승인·제외·검토대상 전환하는 버튼이 없다.

        미적재 관련 함수만 봅니다 — 검토 화면의 "다음 검토 대상으로 옮긴다"
        같은 문장까지 싸잡으면 엉뚱한 곳에서 걸립니다. 주석도 걷어냅니다.
        """
        for name in (
            "showUploadRejections",
            "loadRejections",
            "renderRejectionTable",
            "renderUploadTrace",
        ):
            body = _code_only(_function_body(page, name))
            for banned in ("승인", "제외 확정", "검토 대상으로", "실적 포함", "실적 제외"):
                assert banned not in body, (name, banned)


class TestUploadResultNumbers:
    """작업 D — 운영자가 이해할 수 있는 숫자."""

    def test_four_numbers_are_shown(self, page: str) -> None:
        body = _function_body(page, "renderUploadTrace")

        for label in ("원본 행 ", "검토 대상 적재 ", "미적재 ", "설명되지 않는 행 "):
            assert label in body, label

    def test_unexplained_rows_are_flagged(self, page: str) -> None:
        """⛔ 0 이 아니면 조용히 넘기지 않는다."""
        body = _function_body(page, "renderUploadTrace")

        assert "if (unexplained)" in body
        assert "맞지 않습니다" in body

    def test_history_refreshes_after_a_save(self, page: str) -> None:
        body = _function_body(page, "renderUploadResult")

        assert "loadHistory()" in body


class TestScreenStaysConsistent:
    """기존 화면을 깨뜨리지 않았다 (작업 I·J)."""

    def test_ids_are_unique(self, page: str) -> None:
        found = re.findall(r'\bid="([^"]+)"', page)

        duplicates = {value for value in found if found.count(value) > 1}
        assert duplicates == set(), duplicates

    def test_review_shortcuts_are_untouched(self, page: str) -> None:
        handler = _function_body(page, "handleReviewKey")

        assert 'key === "n"' in handler
        assert 'key === "p"' in handler
        assert '"Escape"' in handler
        # ⛔ 여전히 어떤 키도 확정으로 이어지지 않는다.
        for banned in ("confirmReview", "Enter", "runUndo"):
            assert banned not in handler, banned

    def test_focus_visible_still_styled(self, page: str) -> None:
        assert ":focus-visible {" in page

    def test_new_tables_use_header_cells(self, page: str) -> None:
        """표 머리글을 ``th`` 로 쓴다 — 스크린리더가 열을 읽을 수 있도록."""
        for name in ("renderHistory", "renderRejectionTable"):
            body = _function_body(page, name)
            assert 'make("th"' in body, name


def _code_only(script: str) -> str:
    """``//`` · ``*`` 로 시작하는 주석 줄을 걷어낸 코드만 남깁니다."""
    return "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith(("//", "*", "/*"))
    )


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
