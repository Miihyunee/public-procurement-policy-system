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

        STEP 70 에서 **실적 제외**(2026-08-31 고객 확정)를 위해 한 줄을 더
        더했습니다: ``/reviews/exclusion-reasons`` — 제외 사유 선택지를 서버가
        내려주는 경로입니다(화면이 선택지를 지어내지 않게).

        제외 확정·되돌리기는 ``"/reviews/" + id + "/performance-exclusion"`` 로
        조립되므로 여기 잡히는 문자열은 이미 허용된 ``/reviews/`` 뿐입니다.
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
            # STEP 70 — 실적 제외 사유 선택지. 정규식이 하이픈 앞까지만 잡는다.
            "/reviews/exclusion",
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
        """자유 텍스트 열에만 붙는다 — 숫자·식별 열에는 붙지 않는다.

        STEP 31 에서 예산과목이 같은 자유 텍스트로 합류해 3 → 4개가 되었다.
        이 테스트가 지키던 사실은 개수가 아니라 *어느 열이 어느 쪽인가* 이므로,
        검사 대상을 열별로 좁힌다.
        """
        body = _function_body(page, "renderRejectionTable")

        for field in ("row.description", "row.company_name", "row.reason_label"):
            assert f'make("td", "rv-text", {field}' in body, field
        # ⛔ 업로드 · 원본 행 · 금액은 숫자다. 줄바꿈 규칙이 붙으면 안 된다.
        for field in ("row.batch_id", "row.row_number", "row.amount"):
            assert f'make("td", "rv-text", {field}' not in body, field
        assert body.count('make("td", "num"') == 3

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

    def test_the_rejection_table_still_wraps_its_free_text(self, page: str) -> None:
        """⛔ STEP 28(파일명)이 미적재 표의 자유 텍스트 열을 건드리지 않았다.

        STEP 31 에서 예산과목이 같은 규칙에 합류했다. 이 테스트가 지키던 사실은
        "이력 표 작업이 미적재 표의 줄바꿈을 없애지 않았다" 이므로, 개수 대신
        STEP 27 이 정한 세 열이 그대로인지를 본다.
        """
        body = _function_body(page, "renderRejectionTable")

        for field in ("row.description", "row.company_name", "row.reason_label"):
            assert f'make("td", "rv-text", {field}' in body, field

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


# ----------------------------------------------------------------------
# STEP 30 — 480px 이하의 업로드 이력 표
# ----------------------------------------------------------------------
def _media_block(styles: str, condition: str) -> str:
    """``@media (condition) { ... }`` 한 덩어리(중첩 중괄호 포함)."""
    start = styles.index("@media (" + condition + ") {")
    depth = 0
    for index in range(start, len(styles)):
        if styles[index] == "{":
            depth += 1
        elif styles[index] == "}":
            depth -= 1
            if depth == 0:
                return styles[start : index + 1]
    raise AssertionError(f"@media ({condition}) 의 끝을 찾지 못했습니다")


class TestHistoryTableOnPhoneWidths:
    """휴대폰 세로(480px 이하)에서만 이력 표를 더 좁힌다.

    실측 628 → 402px(480px 화면, 상자 402px) — 내부 가로 스크롤이 사라집니다.
    좁힌 방법은 **줄바꿈과 여백**뿐입니다. 열 9개·값 전부 그대로이고 잘린 칸과
    말줄임표는 어느 폭에서도 0건입니다.

    ⚠️ 360px 에서는 표 401px 로 상자(282px)에 들어가지 않아 내부 스크롤이
       남습니다. 9개 열을 나란히 두는 한 더 줄일 자리가 없어(측정치는 보고서
       참조) 이번에는 억지로 없애지 않았습니다.
    """

    def test_the_change_is_scoped_to_phone_widths(self, styles: str) -> None:
        """① 768px 은 STEP 29 결과 그대로다 — 480px 이하에서만 바뀐다."""
        block = _media_block(styles, "max-width: 480px")

        assert "#history-table" in block

    def test_wider_screens_keep_the_shared_table_rules(self, styles: str) -> None:
        """⑫ 기본 규칙(모든 폭)에는 이력 표 전용 예외가 없다."""
        outside = styles.replace(_media_block(styles, "max-width: 480px"), "")

        assert "#history-table" not in outside

    def test_the_upload_stamp_may_wrap_on_phones(self, styles: str) -> None:
        """`2026-08-22 07:56:25` 은 날짜와 시각 사이의 기존 공백에서만 끊긴다."""
        block = _media_block(styles, "max-width: 480px")

        assert "#history-table td.rv-stamp { white-space: normal; }" in block

    def test_the_stamp_is_never_broken_mid_value(self, styles: str) -> None:
        """⛔ 날짜·시각이 한복판에서 쪼개지면 읽다가 오해한다."""
        block = _media_block(styles, "max-width: 480px")

        assert "overflow-wrap" not in block
        assert "word-break" not in block

    def test_the_stamp_value_is_built_as_before(self, page: str) -> None:
        """④·⑧ 값을 만드는 방법은 그대로다 — 클래스 이름만 붙었다."""
        body = _function_body(page, "renderHistory")

        assert 'make("td", "rv-stamp",' in body
        assert '(item.uploaded_at || "").replace("T", " ").slice(0, 19))' in body

    def test_headers_may_wrap_on_phones(self, styles: str) -> None:
        """`설명 안 됨`(77px)처럼 값보다 헤더가 열 폭을 정하는 칸이 있다."""
        block = _media_block(styles, "max-width: 480px")

        assert "#history-table th { white-space: normal; }" in block

    def test_the_detail_button_keeps_its_touch_height(self, styles: str) -> None:
        """좌우 여백만 줄인다 — 위아래 7px 은 그대로라 높이 34px 이 유지된다."""
        block = _media_block(styles, "max-width: 480px")

        assert "#history-table .control { padding: 7px 6px; }" in block

    def test_nothing_is_hidden_to_win_space(self, styles: str) -> None:
        """⑩·⑪ 정보를 숨겨서 좁힌 것이 아니다."""
        block = _media_block(styles, "max-width: 480px")

        for banned in ("display: none", "text-overflow", "overflow: hidden", "visibility: hidden"):
            assert banned not in block, banned

    def test_all_nine_columns_are_still_drawn(self, page: str) -> None:
        """⑤ 열을 지우거나 합치지 않았다."""
        body = _function_body(page, "renderHistory")

        assert (
            '["기간", "파일", "업로드일시", "원본", "적재", "미적재", "설명 안 됨", "상태", ""]'
            in body
        )
        assert body.count('make("td"') == 9

    def test_no_value_is_shortened(self, page: str) -> None:
        """⑥·⑦·⑧·⑨ 파일명·기간·숫자·상태를 화면이 줄이지 않는다."""
        body = _function_body(page, "renderHistory")

        assert 'make("td", "rv-text", item.file_name)' in body
        assert 'item.period_start + " ~ " + item.period_end)' in body
        assert 'make("td", "num", numberFormat(item.stored))' in body
        assert 'item.is_current ? "현재" : "대체됨"' in body
        for banned in ("…", "textOverflow", "maxLength"):
            assert banned not in body, banned

    def test_step_29_period_rule_is_untouched(self, styles: str) -> None:
        """⑦ STEP 29 규칙을 다시 설계하지 않았다."""
        rule = _rule(styles, ".rv-trace td.rv-period")

        assert "white-space: normal" in rule
        assert "overflow-wrap" not in rule

    def test_step_28_file_name_rule_is_untouched(self, styles: str) -> None:
        """⑥ STEP 28 규칙도 그대로다 — 좁은 화면에서도 완화하지 않는다."""
        rule = _rule(styles, ".rv-trace td.rv-text")

        assert "min-width: 10ch" in rule
        assert "max-width: 24ch" in rule
        assert "min-width: 10ch" not in _media_block(styles, "max-width: 480px")

    def test_the_rejection_table_is_not_touched(self, styles: str) -> None:
        """⑫ 미적재 표(`#upload-trace-rows`)는 이 블록의 대상이 아니다."""
        block = _media_block(styles, "max-width: 480px")

        assert "upload-trace-rows" not in block
        assert "white-space: nowrap" in _rule(styles, ".rv-trace th, .rv-trace td")

    def test_no_extra_request_is_made(self, page: str) -> None:
        """§11 — 좁은 화면 대응은 CSS 다. 서버를 다시 부르지 않는다."""
        body = _code_only(_function_body(page, "renderHistory"))

        assert "fetch" not in body
        assert "matchMedia" not in body


# ----------------------------------------------------------------------
# STEP 31 — 480px 이하의 미적재 표
# ----------------------------------------------------------------------
def _rejection_media(styles: str) -> str:
    """미적재 표를 다루는 ``@media (max-width: 480px)`` 블록.

    같은 조건의 블록이 둘이다 — 앞은 이력 표(STEP 30), 뒤가 미적재 표다.
    두 표의 규칙을 한 블록에 섞지 않으려고 일부러 나눠 두었다.
    """
    first = _media_block(styles, "max-width: 480px")
    rest = styles[styles.index(first) + len(first) :]
    return _media_block(rest, "max-width: 480px")


class TestRejectionTableOnPhoneWidths:
    """휴대폰 세로(480px 이하)에서 미적재 표를 좁힌다.

    실측 534 → 486px(480px 화면). 실제 값이 든 예산과목까지 넣으면 751 → 499px
    이고, **768px 에서 상자를 넘기던 것(751 > 690)이 690 으로 들어옵니다.**

    좁힌 방법은 세 가지뿐입니다 — 예산과목에 기존 자유 텍스트 규칙 적용,
    헤더 줄바꿈, 칸 여백. 값은 하나도 지우거나 자르지 않았습니다.

    ⚠️ 480px 에서도 내부 스크롤은 남습니다(486 > 402). 7열을 나란히 두는 한
       더 줄일 자리가 없어(측정치는 보고서 참조) 억지로 없애지 않았습니다.
    """

    def test_the_table_is_named_so_rules_can_pick_it(self, page: str) -> None:
        """미적재 표는 상자가 둘(업로드·검토)이라 id 로는 한쪽만 잡힌다."""
        body = _function_body(page, "renderRejectionTable")

        assert 'make("table", "rv-reject")' in body

    def test_the_budget_column_shares_the_free_text_rule(self, page: str) -> None:
        """예산과목도 자유 텍스트다 — 규칙을 새로 만들지 않고 같은 것을 쓴다.

        실측: 값이 들어오면 이 열 하나가 272px 을 차지해 768px 화면에서도
        표가 상자를 넘겼다(751 > 690).
        """
        body = _function_body(page, "renderRejectionTable")

        assert 'make("td", "rv-text", row.budget_account || "-")' in body

    def test_the_free_text_columns_all_wrap(self, page: str) -> None:
        """② 적요·거래처·사유의 STEP 27 규칙은 그대로다(+ 예산과목)."""
        body = _function_body(page, "renderRejectionTable")

        for field in ("row.description", "row.company_name", "row.reason_label"):
            assert f'make("td", "rv-text", {field}' in body, field
        assert body.count('make("td", "rv-text"') == 4

    def test_the_free_text_rule_itself_is_unchanged(self, styles: str) -> None:
        """③ `min-width: 10ch` 를 낮추지 않는다 — 480px 이하에서도 그대로다."""
        rule = _rule(styles, ".rv-trace td.rv-text")

        assert "white-space: normal" in rule
        assert "overflow-wrap: anywhere" in rule
        assert "min-width: 10ch" in rule
        assert "max-width: 24ch" in rule
        assert "min-width" not in _media_block(styles, "max-width: 480px")

    def test_headers_may_wrap_on_phones(self, styles: str) -> None:
        """⑦ 헤더가 값보다 넓은 열이 있다 — 헤더**만** 줄바꿈한다."""
        block = _rejection_media(styles)

        assert ".rv-reject th { white-space: normal; }" in block

    def test_padding_is_reduced_on_phones(self, styles: str) -> None:
        """⑧ 좌우 여백 8 → 4px. 위아래 4px 은 건드리지 않는다."""
        block = _rejection_media(styles)

        assert ".rv-reject th, .rv-reject td { padding: 4px 4px; }" in block

    def test_numbers_and_amounts_keep_nowrap(self, page: str, styles: str) -> None:
        """⑥ 금액 `-113,400,000` 이 쪼개지면 읽다가 오해한다.

        실측: 이 열은 줄바꿈을 허용해도 공백이 없어 폭이 1px 도 줄지 않는다.
        얻는 것 없이 위험만 생기므로 값 셀은 손대지 않는다.
        """
        body = _function_body(page, "renderRejectionTable")

        assert 'make("td", "num", row.batch_id === null ? "-" : "#" + row.batch_id)' in body
        assert 'make("td", "num", String(row.row_number))' in body
        assert "Number(row.amount).toLocaleString()" in body
        assert "white-space: nowrap" in _rule(styles, ".rv-trace th, .rv-trace td")
        # 좁은 화면 블록에서 `white-space` 를 받는 것은 헤더뿐이다.
        block = _rejection_media(styles)
        assert block.count("white-space") == 1
        assert ".rv-reject th { white-space: normal; }" in block

    def test_no_value_cell_is_reflowed_on_phones(self, styles: str) -> None:
        """⛔ 좁은 화면 규칙이 값 셀의 줄바꿈을 새로 열지 않는다."""
        block = _rejection_media(styles)

        assert "td { white-space" not in block
        assert "word-break" not in block
        assert "overflow-wrap" not in block

    def test_the_seven_columns_keep_their_order(self, page: str) -> None:
        """① 열 순서와 개수는 그대로다."""
        body = _function_body(page, "renderRejectionTable")

        assert '["업로드", "원본 행", "적요", "거래처", "금액", "예산과목", "사유"]' in body
        assert body.count('make("td"') == 7

    def test_nothing_is_hidden_or_cut(self, styles: str) -> None:
        """④·⑤·⑩ 정보를 숨겨서 좁힌 것이 아니다."""
        block = _rejection_media(styles)

        for banned in ("display: none", "text-overflow", "overflow: hidden", "visibility: hidden"):
            assert banned not in block, banned

    def test_no_value_is_shortened_in_script(self, page: str) -> None:
        """⑪ 값을 JS 에서 자르지 않는다 — 서버가 준 그대로 넣는다."""
        body = _code_only(_function_body(page, "renderRejectionTable"))

        for banned in ("slice(", "substr", "substring", "…", "textOverflow"):
            assert banned not in body, banned

    def test_the_rule_never_reaches_the_history_table(self, styles: str) -> None:
        """⑨·⑫ 이력 표는 이 규칙의 대상이 아니다 — 선택자가 다르다."""
        block = _rejection_media(styles)

        assert "history-table" not in block
        assert "rv-reject" not in _media_block(styles, "max-width: 480px")

    def test_the_history_table_rules_still_stand(self, styles: str, page: str) -> None:
        """⑨ STEP 28~30 의 이력 표 규칙을 하나도 건드리지 않았다."""
        history = _media_block(styles, "max-width: 480px")

        assert "#history-table td.rv-stamp { white-space: normal; }" in history
        assert "#history-table .control { padding: 7px 6px; }" in history
        assert "white-space: normal" in _rule(styles, ".rv-trace td.rv-period")
        assert 'make("td", "rv-text", item.file_name)' in _function_body(page, "renderHistory")

    def test_the_phone_rules_are_scoped_to_480px(self, styles: str) -> None:
        """⑫ 768px 결과(690/690)가 바뀌면 안 된다 — 480px 이하에만 둔다."""
        outside = styles.replace(_media_block(styles, "max-width: 480px"), "", 1)
        outside = outside.replace(_rejection_media(styles), "", 1)

        assert "rv-reject" not in outside

    def test_no_extra_request_is_made(self, page: str) -> None:
        """§12 — 좁은 화면 대응은 CSS 다. 서버를 다시 부르지 않는다."""
        body = _code_only(_function_body(page, "renderRejectionTable"))

        assert "fetch" not in body
        assert "matchMedia" not in body


# ----------------------------------------------------------------------
# STEP 33 — 480px 이하의 검토 카드 확정 선택줄
# ----------------------------------------------------------------------
def _picker_media(styles: str) -> str:
    """확정 선택줄을 다루는 ``@media (max-width: 480px)`` 블록.

    같은 조건의 블록이 여럿이다. 선택줄 블록은 ``.rv-pick`` 을 고르는 것이다.
    """
    rest = styles
    while "@media (max-width: 480px) {" in rest:
        block = _media_block(rest, "max-width: 480px")
        if ".rv-pick" in block:
            return block
        rest = rest[rest.index(block) + len(block) :]
    raise AssertionError("`.rv-pick` 을 다루는 480px 블록을 찾지 못했습니다")


class TestReviewCardNarrowPickLayout:
    """휴대폰 세로(480px 이하)에서 확정 선택줄이 몇 줄로 놓일지를 폭이 정한다.

    실측 360px: 선택줄 155 → 118px, 카드 694 → 657px(긴 데이터 757 → 720px).
    배치는 ``[공사 용역] / [물품 판단 보류] / [메모 확정]`` 2×2 가 됩니다.
    480px 은 ``[공사 용역 물품 판단 보류] / [메모 확정]`` 로 **높이 76px 그대로**,
    768px 이상은 한 줄로 STEP 32 와 동일합니다.

    ⛔ 버튼·메모·확정 6개 요소는 어느 폭에서도 그대로 있습니다. 지우거나
       숨기거나 라벨을 줄여서 공간을 얻지 않았습니다.
    """

    def test_the_rule_is_scoped_to_phone_widths(self, styles: str) -> None:
        """768px 이상은 STEP 32 결과 그대로여야 한다."""
        block = _picker_media(styles)

        assert ".rv-pick" in block

    def test_wider_screens_keep_the_original_pick_rules(self, styles: str) -> None:
        """기본 규칙(모든 폭)은 손대지 않았다."""
        assert "display: flex" in _rule(styles, ".rv-pick")
        assert "flex-wrap: wrap" in _rule(styles, ".rv-pick")
        assert "flex: 1 1 200px" in _rule(styles, ".rv-note")

    def test_the_type_buttons_share_one_basis(self, styles: str) -> None:
        """공통 기준 폭을 줘야 한 줄에 몇 개가 들어갈지 화면 폭이 정한다.

        6.5em(≈85px)은 480px 에서 넷이 한 줄, 360px 에서 둘씩 두 줄이 되는 값이다
        (실측 범위 6.2~6.5em). ⚠️ 6.8em 부터는 480px 이 3+1 로 깨진다.
        """
        block = _picker_media(styles)

        assert ".rv-pick .rv-opt { flex: 1 1 6.5em; }" in block

    def test_the_note_stands_beside_the_confirm_button(self, styles: str) -> None:
        """기준 폭 200px 이면 360px 에서 메모와 확정이 각각 한 줄을 차지한다."""
        block = _picker_media(styles)

        assert ".rv-pick .rv-note { flex: 1 1 160px; }" in block

    def test_buttons_are_never_shrunk(self, styles: str) -> None:
        """⛔ 공간을 얻으려고 버튼을 줄이지 않는다 — 터치 높이 34px 이 걸려 있다."""
        block = _picker_media(styles)

        for banned in ("padding", "font-size", "height", "transform", "zoom"):
            assert banned not in block, banned

    def test_nothing_is_hidden_to_win_space(self, styles: str) -> None:
        """⛔ 숨김·말줄임으로 좁히지 않는다."""
        block = _picker_media(styles)

        for banned in (
            "display: none",
            "visibility: hidden",
            "text-overflow",
            "overflow: hidden",
            "word-break",
        ):
            assert banned not in block, banned

    def test_every_control_is_still_drawn(self, page: str) -> None:
        """유형 버튼 4개 · 메모 · 확정이 그대로 있다 — 라벨도 순서도 그대로."""
        body = _function_body(page, "reviewPicker")

        assert 'make("button", "rv-opt", option.label)' in body
        assert 'make("input", "control control-input rv-note")' in body
        assert 'make("button", "control", "확정")' in body
        assert "메모 (선택)" in body

    def test_the_button_labels_come_from_the_server(self, page: str) -> None:
        """⛔ 화면이 유형 이름을 만들거나 줄이지 않는다."""
        body = _code_only(_function_body(page, "reviewPicker"))

        assert "reviewOptions.forEach" in body
        for banned in ("slice(", "substr", "substring", "…"):
            assert banned not in body, banned

    def test_the_history_row_is_not_affected(self, page: str, styles: str) -> None:
        """이력 보기 · 확정 취소 줄도 `.rv-pick` 이지만 대상이 아니다.

        그 줄에는 `.rv-opt` 도 `.rv-note` 도 없으므로 선택자가 닿지 않는다.
        실측으로도 이력 보기 버튼은 전 폭에서 80×34px 그대로다.
        """
        block = _picker_media(styles)
        card = _function_body(page, "reviewCard")

        assert block.count(".rv-pick") == 2  # .rv-opt · .rv-note 두 줄뿐
        assert "rv-opt" not in _function_body(page, "historyButton")
        assert 'make("div", "rv-pick")' in card

    def test_the_two_tables_are_not_affected(self, styles: str) -> None:
        """⛔ STEP 30·31 의 두 표는 이 블록의 대상이 아니다."""
        block = _picker_media(styles)

        assert "history-table" not in block
        assert "rv-reject" not in block
        assert "rv-trace" not in block

    def test_no_extra_request_is_made(self, page: str) -> None:
        """§11 — 좁은 화면 대응은 CSS 다. 서버를 다시 부르지 않는다."""
        body = _code_only(_function_body(page, "reviewPicker"))

        assert "matchMedia" not in body
        assert 'addEventListener("resize"' not in body
