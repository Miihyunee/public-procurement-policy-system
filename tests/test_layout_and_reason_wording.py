"""STEP 19 — 가로 스크롤 정리와 미적재 사유 문구 통일.

두 가지를 고정합니다.

1. **가로 스크롤이 생기지 않는다.** grid 칸이 줄어들 수 있게 열어 두어, 넘치는
   표는 화면 전체가 아니라 **제 안에서** 스크롤합니다. ⛔ ``overflow-x: hidden``
   으로 덮어서 해결하지 않습니다 — 그러면 표를 볼 방법이 사라집니다.
2. **같은 사실은 같은 말로.** 미적재 사유 요약을 검토 화면과 업로드 화면이
   같은 함수 하나로 그립니다.

.. warning::
    ⛔ 업무규칙은 하나도 바뀌지 않았습니다. 미적재 행은 여전히 "원본에는 있으나
    현재 검토 대상 DB 에 적재되지 않은 행" 이고, 처리 방식은 확인 전입니다
    (Q5-8). Q5-1 ~ Q5-9 전부 미확정입니다.

⚠️ 화면 소스를 검사합니다. 실제 렌더링 폭은 실브라우저(1920/1440/1280/1024/768)
로 따로 측정했으며, 여기서 고정하는 것은 **그 결과가 사라지지 않도록 막는 최소
조건**입니다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

INDEX = (
    Path(__file__).resolve().parents[1] / "src" / "procurement" / "web" / "static" / "index.html"
)

#: 미적재 행에 붙이면 안 되는 말. Q5-8 이 확인되기 전까지는 사실만 적는다.
BANNED_WORDS = (
    "제외",
    "잘못된 데이터",
    "무효",
    "실적 불인정",
    "처리 완료",
    "삭제",
    "부적합",
    "오류 데이터",
    "검토 불필요",
)

#: 사용자에게 그대로 보여서는 안 되는 내부 코드.
REASON_CODES = ("NON_POSITIVE_AMOUNT", "MISSING_REQUIRED", "UNPARSABLE")


@pytest.fixture(scope="module")
def page() -> str:
    return INDEX.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def styles(page: str) -> str:
    """``<style>`` 안의 CSS 에서 주석을 걷어낸 것.

    ⚠️ 주석을 남겨 두면 "``overflow-x: hidden`` 을 쓰지 않는다" 같은 **설명**이
    금지어 검사에 걸린다. 검사 대상은 실제로 적용되는 선언이다.
    """
    found = re.search(r"<style>(.*?)</style>", page, re.S)
    assert found is not None, "index.html 에 <style> 이 없습니다"
    return re.sub(r"/\*.*?\*/", "", found.group(1), flags=re.S)


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


def _rule(styles: str, selector: str) -> str:
    """``selector { ... }`` 한 덩어리."""
    start = styles.index(selector + " {")
    return styles[start : styles.index("}", start) + 1]


# ----------------------------------------------------------------------
# A. 가로 스크롤
# ----------------------------------------------------------------------
class TestNoHorizontalOverflow:
    """지시 §1 · §2 — 원인을 고치고, 숨기지 않는다."""

    def test_grid_cells_may_shrink(self, styles: str) -> None:
        """실제 원인 — grid 칸의 ``min-width: auto`` 를 열어 준다.

        칸이 줄어들 수 없으면, 안에 있는 넓은 표가 칸을 밀어내고 그 결과 문서
        전체에 가로 스크롤이 생긴다(실측: 어느 화면에서나 문서 폭 1354px 고정).
        """
        assert "min-width: 0" in _rule(styles, ".row > *")

    def test_overflow_is_not_hidden_away(self, styles: str) -> None:
        """⛔ ``overflow-x: hidden`` 으로 덮지 않는다 — 표를 볼 방법이 사라진다."""
        for selector in ("html", "body", ".wrap", ".row"):
            rule = _rule(styles, selector) if selector + " {" in styles else ""
            assert "overflow-x: hidden" not in rule, selector
        assert "overflow-x: hidden" not in styles
        assert "overflow: hidden" not in _rule(styles, ".row")

    def test_wide_tables_scroll_inside_themselves(self, styles: str) -> None:
        """넘치는 표는 제 안에서 스크롤한다 — 문서를 넓히지 않는다."""
        rule = _rule(styles, ".rv-trace")

        assert "overflow-x: auto" in rule

    def test_table_cells_still_do_not_wrap(self, styles: str) -> None:
        """열 값이 줄바꿈되면 읽기 어렵다 — 대신 표가 스크롤한다."""
        assert "white-space: nowrap" in _rule(styles, ".rv-trace th, .rv-trace td")

    def test_layout_widths_were_not_redesigned(self, styles: str) -> None:
        """지시 §2 — 전체 레이아웃을 다시 설계하지 않는다."""
        assert "max-width: 1240px" in _rule(styles, ".wrap")
        assert "100vw" not in styles

    def test_narrow_screen_fallbacks_exist(self, styles: str) -> None:
        """좁은 화면에서 여러 칸 배치가 한 칸으로 접힌다."""
        assert "@media (max-width: 940px)" in styles
        assert "grid-template-columns: 1fr" in styles

    def test_controls_wrap_instead_of_overflowing(self, styles: str) -> None:
        """조건 컨트롤 줄은 넘치는 대신 줄바꿈한다."""
        assert "flex-wrap: wrap" in _rule(styles, ".upload-actions")


# ----------------------------------------------------------------------
# C. 미적재 사유 문구 통일
# ----------------------------------------------------------------------
class TestReasonWordingIsShared:
    """지시 §7 · §8 — 같은 사실을 화면마다 다르게 말하지 않는다."""

    def test_every_screen_uses_one_renderer(self, page: str) -> None:
        for name in ("appendPeriodNote", "renderUploadTrace", "showBatchDetail", "renderTrace"):
            assert "appendReasonNote(" in _function_body(page, name), name

    def test_no_screen_formats_reasons_on_its_own(self, page: str) -> None:
        """⛔ 사유 줄을 손으로 만드는 자리가 남아 있으면 문구가 다시 갈린다."""
        for name in ("renderUploadTrace", "showBatchDetail", "renderTrace"):
            body = _code_only(_function_body(page, name))
            assert "reason.label" not in body, name
            assert "item.label" not in body, name

    def test_shared_renderer_keeps_the_agreed_wording(self, page: str) -> None:
        body = _function_body(page, "appendReasonNote")

        assert "미적재 사유 (검토 대상 밖 · 처리 방식 확인 전)" in body

    def test_empty_case_is_stated(self, page: str) -> None:
        body = _function_body(page, "appendReasonNote")

        assert "미적재 없음" in body

    def test_upload_keeps_its_fuller_empty_sentence(self, page: str) -> None:
        """0건일 때 업로드 결과는 더 자세한 문장을 그대로 쓴다."""
        body = _function_body(page, "renderUploadTrace")

        assert "미적재 행 없음 — 원본 행이 모두 검토 대상에 들어왔습니다." in body

    def test_counts_come_from_the_backend(self, page: str) -> None:
        body = _function_body(page, "appendReasonNote")

        assert "reason.label" in body
        assert "reason.count" in body

    def test_reason_codes_are_never_shown(self, page: str) -> None:
        """지시 §9 — 내부 코드를 사용자에게 보여주지 않는다."""
        for name in (
            "appendReasonNote",
            "renderUploadTrace",
            "showBatchDetail",
            "renderTrace",
            "appendPeriodNote",
        ):
            body = _function_body(page, name)
            for code in REASON_CODES:
                assert code not in body, f"{name}: {code}"

    def test_no_frontend_reason_dictionary(self, page: str) -> None:
        """⛔ 사유 코드 → 이름 매핑을 화면이 갖지 않는다."""
        body = _function_body(page, "appendReasonNote")

        assert "금액이 0 이하" not in body
        assert "필수값 누락" not in body

    def test_wording_avoids_business_judgement(self, page: str) -> None:
        """지시 §8 — 아직 아무것도 정해지지 않았다."""
        for name in (
            "appendReasonNote",
            "renderUploadTrace",
            "showBatchDetail",
            "renderTrace",
            "appendPeriodNote",
        ):
            body = _code_only(_function_body(page, name))
            for banned in BANNED_WORDS:
                assert banned not in body, f"{name}: {banned}"

    def test_reason_summary_costs_no_request(self, page: str) -> None:
        """지시 §11 — 사유를 보여주려고 API 를 다시 부르지 않는다."""
        body = _code_only(_function_body(page, "appendReasonNote"))

        for banned in ("fetch(", "fetchJson(", "/imports", "/reviews"):
            assert banned not in body, banned

    def test_each_screen_uses_data_it_already_has(self, page: str) -> None:
        """업로드 결과·이력 상세는 자기 응답에 이미 있는 ``reasons`` 를 쓴다."""
        assert "result.rejection_reasons" in _function_body(page, "renderUploadTrace")
        assert "item.reasons" in _function_body(page, "showBatchDetail")
        assert "trace.reasons" in _function_body(page, "renderTrace")


# ----------------------------------------------------------------------
# B·D. 기존 기능 회귀
# ----------------------------------------------------------------------
class TestNothingElseMoved:
    """지시 §5 · §6 · §16 · §17 — STEP 16~18 이 그대로 살아 있다."""

    def test_card_structure_is_unchanged(self, page: str) -> None:
        body = _function_body(page, "reviewCard")

        assert body.index("reviewSource(") < body.index("reviewStateLine(")
        assert body.index("reviewStateLine(") < body.index("analysisFold(")
        assert body.index("analysisFold(") < body.index("pastFold(")

    def test_folds_are_still_closed_by_default(self, page: str) -> None:
        body = _code_only(_function_body(page, "foldable"))

        assert 'make("details"' in body
        assert ".open = true" not in body

    def test_keyboard_map_is_unchanged(self, page: str) -> None:
        handler = _function_body(page, "handleReviewKey")

        for key in ('"ArrowDown"', '"ArrowUp"', 'key === "n"', 'key === "p"', '"Escape"', '"Tab"'):
            assert key in handler, key

    def test_enter_still_never_confirms(self, page: str) -> None:
        handler = _function_body(page, "handleReviewKey")

        assert "controls[0].focus()" in handler
        for banned in ("confirmReview", "runUndo", "askUndo"):
            assert banned not in handler, banned

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

    def test_progress_still_excludes_rejected_rows(self, page: str) -> None:
        body = _function_body(page, "renderProgress")

        assert "condition.total" in body
        assert "rejected" not in body

    def test_period_note_still_reports_out_of_scope_rows(self, page: str) -> None:
        body = _function_body(page, "appendPeriodNote")

        assert "검토 대상 밖" in body

    def test_confirm_and_undo_paths_are_unchanged(self, page: str) -> None:
        confirm = _function_body(page, "confirmReview")
        undo = _function_body(page, "runUndo")

        assert '"PUT"' in confirm
        assert "/reopen" in undo
        for body in (confirm, undo):
            assert "refreshPeriods().then(function () { loadReviews(); })" in body

    def test_csv_links_are_unchanged(self, page: str) -> None:
        assert "/reviews/export.csv?" in page
        assert "/imports/trace.csv" in page

    def test_no_new_endpoint_appeared(self, page: str) -> None:
        """화면이 부르는 엔드포인트는 **알려진 목록뿐**이다.

        STEP 19 에서는 "새 API 를 만들지 않았다" 를 확인하는 검사였습니다.
        STEP 20 에서 검토 **변경 이력** CSV(``/reviews/history.csv``)를 새로
        열었으므로 목록에 한 줄을 더했습니다 — 지키려는 사실(화면이 아무 경로나
        부르지 않는다)은 그대로이며, 목록에 없는 경로가 생기면 여전히 실패합니다.
        """
        found = set(re.findall(r'"(/(?:imports|reviews)[a-z_./{}]*)', page))
        allowed = {
            "/imports/periods",
            "/imports/batches",
            "/imports/batches/",
            "/imports/rejections",
            "/imports/trace",
            "/imports/trace.csv",
            "/reviews",
            "/reviews/",
            "/reviews/options",
            "/reviews/export.csv",
            "/reviews/history.csv",
        }
        assert found <= allowed, found - allowed


# ----------------------------------------------------------------------
# STEP 27 — 미적재 표의 열 폭
# ----------------------------------------------------------------------
class TestRejectionTableColumnWidths:
    """긴 자유 텍스트 열만 줄바꿈시켜 표를 좁힌다.

    전부 ``nowrap`` 이면 표의 최소 폭이 내용 길이만큼 늘어나(실측 1,296px)
    좁은 화면에서 담당자가 표 안에서 한참 옆으로 밀어야 했습니다. 실측으로
    1,296 → 1,162px(데스크톱) · 690px(768px 화면)로 줄었고, 어느 폭에서도
    **잘린 칸 0개 · ellipsis 0개**입니다.
    """

    def test_long_text_columns_may_wrap(self, styles: str) -> None:
        rule = _rule(styles, ".rv-trace td.rv-text")

        assert "white-space: normal" in rule
        assert "overflow-wrap: anywhere" in rule

    def test_the_wrapping_columns_are_bounded(self, styles: str) -> None:
        """한 글자씩 흐르지도, 한 열이 표를 독차지하지도 않게."""
        rule = _rule(styles, ".rv-trace td.rv-text")

        assert "min-width" in rule
        assert "max-width" in rule

    def test_nothing_is_cut_off(self, styles: str) -> None:
        """⛔ 줄을 바꿔 전부 보여준다 — 말줄임표로 감추지 않는다."""
        rule = _rule(styles, ".rv-trace td.rv-text")

        assert "text-overflow" not in rule
        assert "ellipsis" not in styles

    def test_numbers_still_do_not_wrap(self, styles: str) -> None:
        """``-113,400,000`` 이 줄바꿈되면 읽다가 오해하기 쉽다."""
        assert "white-space: nowrap" in _rule(styles, ".rv-trace th, .rv-trace td")

    def test_only_the_free_text_cells_get_the_class(self, page: str) -> None:
        body = _function_body(page, "renderRejectionTable")

        assert body.count('make("td", "rv-text"') == 3  # 적요 · 거래처 · 사유
        assert 'make("td", "num"' in body  # 업로드 · 원본 행 · 금액은 그대로

    def test_the_columns_are_unchanged(self, page: str) -> None:
        """지시 — 기존 컬럼 순서와 데이터 내용은 바꾸지 않는다."""
        body = _function_body(page, "renderRejectionTable")

        assert '["업로드", "원본 행", "적요", "거래처", "금액", "예산과목", "사유"]' in body

    def test_the_table_still_scrolls_inside_itself(self, styles: str) -> None:
        """더 좁아지면(실측 480px 이하) 표 안에서만 밀린다 — 문서가 아니라."""
        assert "overflow-x: auto" in _rule(styles, ".rv-trace")

    def test_the_page_level_fix_is_still_there(self, styles: str) -> None:
        """STEP 19 의 grid 칸 수축 허용이 없으면 문서 전체가 다시 밀린다."""
        assert "min-width: 0" in _rule(styles, ".row > *")


# ----------------------------------------------------------------------
# STEP 28 — 업로드 이력 표의 파일명 열
# ----------------------------------------------------------------------
class TestHistoryTableFileNameColumn:
    """긴 파일명만 줄바꿈시켜 이력 표를 좁힌다.

    실측 870 → 760px(768px 화면). 파일명은 잘리지 않고 줄을 바꿔 **전부**
    보입니다 — 49자 파일명으로 확인했을 때 어느 폭에서도 잘린 칸 0개입니다.
    ⚠️ 기간·업로드일시·숫자 열은 ``nowrap`` 그대로입니다.
    """

    def test_the_file_name_cell_may_wrap(self, page: str) -> None:
        body = _function_body(page, "renderHistory")

        assert 'make("td", "rv-text", item.file_name)' in body

    def test_only_the_file_name_wraps(self, page: str) -> None:
        """⛔ 기간·일시·숫자까지 줄바꿈되면 값이 이상하게 쪼개진다."""
        body = _function_body(page, "renderHistory")

        assert body.count('"rv-text"') == 1

    def test_dates_and_numbers_keep_their_cells(self, page: str) -> None:
        """기간·숫자 값은 여전히 각자의 칸에 **가공 없이** 들어간다.

        STEP 29 에서 기간 칸에 ``rv-period`` 클래스가 붙었다. 이 테스트가
        지키던 사실은 클래스 이름이 아니라 *값이 그대로라는 것*이므로,
        검사 대상을 셀에 들어가는 표현식으로 좁힌다.
        """
        body = _function_body(page, "renderHistory")

        assert 'item.period_start + " ~ " + item.period_end)' in body
        assert 'make("td", "num", numberFormat(item.stored))' in body

    def test_the_file_name_is_passed_through_whole(self, page: str) -> None:
        """⛔ 화면이 파일명을 줄이지 않는다 — 서버가 준 값을 그대로 넣는다."""
        body = _function_body(page, "renderHistory")
        cell = [line for line in body.splitlines() if "item.file_name" in line][0]

        for banned in ("slice(", "substr", "substring", "…", "..."):
            assert banned not in cell, banned

    def test_nothing_is_hidden_or_cut(self, styles: str) -> None:
        """지시 §4 — ellipsis 도 overflow hidden 도 쓰지 않는다."""
        rule = _rule(styles, ".rv-trace td.rv-text")

        assert "text-overflow" not in rule
        assert "overflow: hidden" not in rule

    def test_the_columns_are_unchanged(self, page: str) -> None:
        body = _function_body(page, "renderHistory")

        assert (
            '["기간", "파일", "업로드일시", "원본", "적재", "미적재", "설명 안 됨", "상태", ""]'
            in body
        )

    def test_the_rejection_table_still_wraps_three_columns(self, page: str) -> None:
        """⛔ STEP 27 의 미적재 표 동작을 건드리지 않았다."""
        body = _function_body(page, "renderRejectionTable")

        assert body.count('make("td", "rv-text"') == 3

    def test_both_tables_share_one_rule(self, styles: str) -> None:
        """같은 사실(긴 자유 텍스트)은 규칙 하나로 다룬다 — 복제하지 않는다."""
        assert styles.count(".rv-trace td.rv-text {") == 1


# ----------------------------------------------------------------------
# STEP 29 — 업로드 이력 표의 기간 열
# ----------------------------------------------------------------------
class TestHistoryTablePeriodColumn:
    """기간 열만 추가로 줄바꿈해 768px 화면의 내부 스크롤을 없앤다.

    실측 760 → 690px(768px 화면, 상자 690px) — 표가 상자 안에 들어와
    가로 스크롤이 사라집니다. 끊기는 자리는 ``~`` 앞뒤의 **기존 공백**뿐이라
    날짜 하나(``2026-04-01``)는 어느 폭에서도 쪼개지지 않습니다.
    """

    def test_the_period_cell_may_wrap(self, styles: str) -> None:
        """① 기간 열에만 줄바꿈 허용 규칙이 붙는다."""
        rule = _rule(styles, ".rv-trace td.rv-period")

        assert "white-space: normal" in rule

    def test_the_period_text_is_not_reshaped(self, page: str) -> None:
        """② 날짜 문자열을 화면이 가공하지 않는다 — 서버 값 그대로다."""
        body = _function_body(page, "renderHistory")
        cell = [line for line in body.splitlines() if "item.period_start" in line][0]

        assert 'item.period_start + " ~ " + item.period_end' in cell
        for banned in ("slice(", "substr", "substring", "replace(", "split(", "<wbr", "&shy"):
            assert banned not in cell, banned

    def test_the_period_cell_does_not_break_anywhere(self, styles: str) -> None:
        """③ ``overflow-wrap`` 을 쓰면 날짜 한복판에서 끊긴다 — 쓰지 않는다."""
        rule = _rule(styles, ".rv-trace td.rv-period")

        assert "overflow-wrap" not in rule

    def test_the_period_cell_does_not_break_words(self, styles: str) -> None:
        """④ ``word-break`` 도 같은 이유로 쓰지 않는다."""
        rule = _rule(styles, ".rv-trace td.rv-period")

        assert "word-break" not in rule

    def test_other_columns_still_do_not_wrap(self, page: str, styles: str) -> None:
        """⑤ 일시·숫자·상태·버튼 열은 ``nowrap`` 그대로다."""
        assert "white-space: nowrap" in _rule(styles, ".rv-trace td")

        body = _function_body(page, "renderHistory")
        assert body.count('"rv-period"') == 1

    def test_the_file_name_column_is_untouched(self, page: str, styles: str) -> None:
        """⑥ STEP 28 의 파일명 열 규칙은 그대로다."""
        assert 'make("td", "rv-text", item.file_name)' in _function_body(page, "renderHistory")
        assert "overflow-wrap: anywhere" in _rule(styles, ".rv-trace td.rv-text")

    def test_the_columns_are_unchanged(self, page: str) -> None:
        """⑦ 9개 열의 순서와 개수는 그대로다."""
        body = _function_body(page, "renderHistory")

        assert (
            '["기간", "파일", "업로드일시", "원본", "적재", "미적재", "설명 안 됨", "상태", ""]'
            in body
        )
        assert body.count('make("td"') == 9

    def test_the_rejection_table_keeps_step_27_widths(self, styles: str) -> None:
        """⑧ 미적재 표의 STEP 27 폭 규칙을 건드리지 않았다."""
        rule = _rule(styles, ".rv-trace td.rv-text")

        assert "min-width: 10ch" in rule
        assert "max-width: 24ch" in rule
