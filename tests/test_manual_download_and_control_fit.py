"""STEP 146 — 잘리는 칸·제목에 낀 안내·프로그램 안의 사용자 매뉴얼.

담당자 화면에서 **글자가 잘려 있었습니다.**

    확정자          이름 (비우면 이력에 안 ᅡ
    조회 출처       여성기업 (WOMA
    사업자등록번호  쉼표 또는 줄바꿈으로 구

원인은 「모자란 최소 폭」이 아니라 **폭을 글자 수로 어림한 방식** 자체였습니다.
`ch` 는 숫자 ``0`` 한 글자 폭이라 한글이 섞이면 어긋나고, 창 폭·글꼴이 바뀌면
또 어긋납니다. 그래서 값을 키워 맞추는 대신 칸이 **자기 내용 폭**을 갖게
했습니다(``field-sizing: content``).

함께 고친 것.

  · 구매유형 검토의 진행률이 제목 오른쪽 끝에 세로로 세 줄 붙어 있던 것을
    제목 **아래 한 줄**로 폈고, 아무것도 올리지 않은 상태에서 ``0 / 0`` 세
    줄을 늘어놓지 않게 했습니다.
  · 사용자 매뉴얼(PDF)을 프로그램에 담아 화면에서 바로 받을 수 있게 했습니다.

.. warning::
    ⛔ **업무규칙은 하나도 바뀌지 않았습니다.** 진행률 분모·미적재 처리·구매유형
    판정은 그대로이며, 이 파일의 검사가 그 사실을 함께 고정합니다.

⚠️ 데이터는 전부 **합성**입니다. 매뉴얼 응답은 파일 크기와 헤더만 봅니다 —
   고객 자료가 아닙니다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.web import MANUAL_FILE_NAME, MANUAL_PDF_PATH, manual_exists, manual_size

INDEX = (
    Path(__file__).resolve().parents[1] / "src" / "procurement" / "web" / "static" / "index.html"
)


@pytest.fixture(scope="module")
def page() -> str:
    return INDEX.read_text(encoding="utf-8")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """빈 DB 로 띄운 앱. 매뉴얼은 DB 와 무관하다."""
    return TestClient(create_app(db_path=tmp_path / "manual.db"))


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


def _style_block(page: str) -> str:
    """``<style>`` 안쪽만."""
    start = page.index("<style>")
    return page[start : page.index("</style>", start)]


# ----------------------------------------------------------------------
# ① 칸이 잘리지 않는다
# ----------------------------------------------------------------------
class TestControlsSizeToTheirOwnContent:
    """글자 수로 어림한 폭 대신 **내용 폭**을 쓴다."""

    def test_inputs_and_selects_size_to_content(self, page: str) -> None:
        style = _style_block(page)

        assert "@supports (field-sizing: content)" in style
        assert ".control input, .control select { field-sizing: content; }" in style

    def test_flexbox_cannot_shrink_a_field_below_its_content(self, page: str) -> None:
        """⛔ 자리가 모자라면 **줄이 바뀔 뿐** 글자가 잘리면 안 된다."""
        style = _style_block(page)
        supports = style[style.index("@supports (field-sizing: content)") :]
        block = supports[: supports.index("\n  }") + 4]

        assert "min-width: min-content" in block
        # 지원되는 환경에서는 선택상자 폭 상한을 풀어 준다 —
        # `15ch` 가 「여성기업 (WOMAN)」 을 잘라 냈던 자리다.
        assert "max-width: 100%" in block

    def test_the_ch_guesses_stay_only_as_a_fallback(self, page: str) -> None:
        """⚠️ `field-sizing` 을 모르는 환경에는 예전 값이 남아 있어야 한다."""
        style = _style_block(page)
        fallback = style[: style.index("@supports (field-sizing: content)")]

        assert "min-width: 12ch" in fallback
        assert "max-width: 15ch" in fallback

    def test_no_field_hides_its_hint_with_an_ellipsis(self, page: str) -> None:
        """⛔ 입력 칸 안내 글자를 말줄임표로 잘라 「보이는 것처럼」 만들지 않는다.

        ② 요구사항 변경 (🟢 2026-09-11 PM 확정 · STEP 164) — 검사 범위를
        **입력 칸(.control) 규칙**으로 좁혔다.

        검토 목록이 「한 줄에 한 건」이 되면서 적요 한 줄에 말줄임이
        필요해졌다. 그 글자는 ``title`` 로도, 「자세히 보기」를 펼쳐도 전문이
        그대로 나오므로 볼 방법이 없어지지 않는다.

        ⛔ 입력 칸과 그 안내 글자에서는 여전히 금지다 — 거기서 잘리면
        담당자가 무엇을 넣어야 하는지 알 방법이 없다.
        """
        style = _style_block(page)
        control_rules = "\n".join(
            line for line in style.splitlines() if ".control" in line or "control-" in line
        )

        for banned in ("text-overflow: ellipsis", "text-overflow:ellipsis"):
            assert banned not in control_rules, banned

    def test_the_clipped_hints_are_still_written_in_full(self, page: str) -> None:
        """⛔ 잘렸다고 안내 문구를 줄여 없애지 않았다."""
        for hint in (
            "이름 (비우면 이력에 안 남음)",
            "쉼표 또는 줄바꿈으로 구분",
            "적요 · 거래처명 · 사업자번호",
        ):
            assert hint in page, hint


class TestButtonLabelsDoNotWrap:
    """⛔ 「새로 / 고침」처럼 한 단추가 두 줄이 되지 않는다."""

    def test_buttons_and_link_buttons_keep_one_line(self, page: str) -> None:
        style = _style_block(page)

        assert "button.control { cursor: pointer; font-family: inherit; white-space: nowrap; }" in (
            style
        )
        assert "a.control { cursor: pointer; text-decoration: none; white-space: nowrap; }" in style

    def test_link_buttons_look_like_buttons(self, page: str) -> None:
        """내려받기 링크만 밑줄이 그어져 있으면 종류가 달라 보인다."""
        style = _style_block(page)

        assert "a.control:hover" in style


# ----------------------------------------------------------------------
# ② 구매유형 검토의 진행률 자리
# ----------------------------------------------------------------------
class TestProgressSitsUnderTheTitle:
    """제목 오른쪽 끝이 아니라 제목 아래 한 줄이다."""

    def test_progress_is_not_inside_the_card_head(self, page: str) -> None:
        """검토 카드의 `card-head` 안에 진행률이 없어야 한다."""
        # 검토 카드의 `card-head` 여는 태그부터 진행률까지를 잘라, 그 사이에서
        # `<div>` 와 `</div>` 를 세어 본다. head 가 닫혔다면 균형이 맞는다.
        card = page[: page.index('id="review-progress"')]
        head_start = card.rindex('class="card-head"')
        between = card[head_start:]

        assert between.count("<div") == between.count("</div>"), (
            "card-head 가 진행률 앞에서 닫히지 않았습니다 — 제목 옆에 다시 붙었습니다"
        )

    def test_progress_lays_out_horizontally(self, page: str) -> None:
        style = _style_block(page)

        assert ".card-sub.rv-progress {" in style
        assert 'class="card-sub rv-progress"' in page

    def test_progress_is_still_announced(self, page: str) -> None:
        """⛔ 자리를 옮겼을 뿐 스크린리더 안내를 없애지 않았다."""
        block = page[page.index('id="review-progress"') :][:220]

        assert 'role="status"' in block
        assert 'aria-live="polite"' in block


class TestEmptyStateSaysWhatToDo:
    """아직 아무것도 올리지 않았을 때 0 세 개를 늘어놓지 않는다."""

    def test_zero_total_gets_a_sentence(self, page: str) -> None:
        body = _function_body(page, "renderProgress")

        assert "if (!whole.total)" in body
        assert "검토할 구매실적이 아직 없습니다" in body

    def test_the_out_of_scope_note_still_runs_when_empty(self, page: str) -> None:
        """⚠️ 올린 행이 **전부** 검토 대상 밖일 수 있다 — 그건 다른 사실이다."""
        body = _function_body(page, "renderProgress")
        empty = body[body.index("if (!whole.total)") :]
        empty = empty[: empty.index("return;")]

        assert "appendPeriodNote(node)" in empty

    def test_empty_state_makes_no_judgement(self, page: str) -> None:
        """⛔ 「자료 없음」이 위험·미달을 뜻하지 않는다."""
        body = _function_body(page, "renderProgress")

        for banned in ("위험", "적정", "미달", "경고", "양호"):
            assert banned not in body, banned

    def test_denominator_rule_is_untouched(self, page: str) -> None:
        """⛔ STEP 16 규칙 유지 — 미적재를 진행률 분모에 더하지 않는다."""
        body = _function_body(page, "renderProgress")

        assert "condition.total" in body
        assert "rejected" not in body


class TestWholeScopeNoteDoesNotRepeatTheNoticeBelow:
    """같은 사유 목록이 두 줄 아래 또 나오면 다른 것으로 읽힌다."""

    def test_only_the_count_is_repeated(self, page: str) -> None:
        body = _function_body(page, "appendWholeRejectionNote")

        assert "아래 참고" in body
        # 사유 목록을 여기서 다시 펴지 않는다.
        assert "appendReasonNote" not in body

    def test_the_count_still_comes_from_the_loaded_trace(self, page: str) -> None:
        """⚠️ 같은 숫자를 위해 API 를 다시 부르지 않는다."""
        body = _function_body(page, "appendWholeRejectionNote")

        assert "lastTrace" in body
        for banned in ("fetch(", "fetchJson("):
            assert banned not in body, banned

    def test_period_scope_still_shows_the_full_reason_list(self, page: str) -> None:
        """⚠️ 기간을 고른 경우 아래 안내는 전체 기준이므로 목록이 필요하다."""
        body = _function_body(page, "appendPeriodNote")

        assert "appendReasonNote" in body

    def test_wording_stays_factual(self, page: str) -> None:
        """⛔ 「제외」·「부적합」 이라고 쓰지 않는다 — 처리 방식은 확인 전이다."""
        body = _function_body(page, "appendWholeRejectionNote")

        for banned in ("제외", "부적합", "검토 불필요", "오류 데이터", "삭제"):
            assert banned not in body, banned


# ----------------------------------------------------------------------
# ③ 사용자 매뉴얼을 프로그램에서 받는다
# ----------------------------------------------------------------------
class TestManualIsBundled:
    """매뉴얼 파일이 프로그램에 들어 있다."""

    def test_the_pdf_is_in_the_package(self) -> None:
        assert manual_exists(), f"{MANUAL_PDF_PATH} 가 없습니다"

    def test_it_really_is_a_pdf(self) -> None:
        assert MANUAL_PDF_PATH.read_bytes()[:5] == b"%PDF-"

    def test_it_is_not_an_empty_placeholder(self) -> None:
        """⛔ 「자리만 잡아 둔」 빈 파일을 담지 않는다."""
        assert manual_size() > 200_000

    def test_the_package_path_is_ascii(self) -> None:
        """⚠️ 패키징 도구가 다루는 경로다 — 한글 파일명을 두지 않는다."""
        MANUAL_PDF_PATH.name.encode("ascii")

    def test_the_download_name_is_the_korean_one(self) -> None:
        assert MANUAL_FILE_NAME.endswith(".pdf")
        assert "사용자매뉴얼" in MANUAL_FILE_NAME


class TestManualEndpoint:
    """``/docs/manual``."""

    def test_get_returns_the_pdf(self, client: TestClient) -> None:
        response = client.get("/docs/manual")

        assert response.status_code == 200
        assert response.headers["content-type"] == "application/pdf"
        assert response.content[:5] == b"%PDF-"
        assert len(response.content) == manual_size()

    def test_the_file_name_is_sent_utf8_encoded(self, client: TestClient) -> None:
        """한글 파일명은 ``filename*=UTF-8''`` 로 보낸다."""
        response = client.get("/docs/manual")

        assert "filename*=UTF-8''" in response.headers["content-disposition"]
        assert response.headers["content-disposition"].startswith("attachment;")

    def test_head_answers_without_sending_the_file(self, client: TestClient) -> None:
        """화면은 「담겨 있는지」만 묻는다 — 2 MB 를 미리 받지 않는다."""
        response = client.head("/docs/manual")

        assert response.status_code == 200
        assert response.headers["content-length"] == str(manual_size())
        assert response.content == b""

    def test_the_response_is_not_cached_as_a_screen(self, client: TestClient) -> None:
        """⛔ 화면 HTML 을 대신 보내지 않는다."""
        response = client.get("/docs/manual")

        assert b"<html" not in response.content[:400].lower()


class TestManualLinkOnScreen:
    """화면 오른쪽 위 「사용자 매뉴얼」."""

    def test_the_link_points_at_the_endpoint(self, page: str) -> None:
        assert 'id="manual-link"' in page
        assert 'href="/docs/manual"' in page
        assert "사용자 매뉴얼" in page

    def test_it_is_hidden_until_the_manual_is_confirmed(self, page: str) -> None:
        """⛔ 없는데 있는 것처럼 단추를 두지 않는다."""
        block = page[page.index('id="manual-link"') :][:400]

        assert "hidden" in block

    def test_the_check_costs_no_download(self, page: str) -> None:
        body = _function_body(page, "setupManualLink")

        assert 'method: "HEAD"' in body
        assert "manual-link" in body

    def test_a_failed_check_leaves_the_link_hidden(self, page: str) -> None:
        """⛔ 매뉴얼 때문에 업무 화면에 오류를 띄우지 않는다."""
        body = _function_body(page, "setupManualLink")

        assert ".catch(" in body
        for banned in ("showError", "errorBox", 'el("error-box")'):
            assert banned not in body, banned

    def test_the_check_runs_on_start(self, page: str) -> None:
        assert "setupManualLink();" in page


class TestManualIsShippedByThePackaging:
    """빌드에서 조용히 빠지면 단추만 사라진다 — 사양에 고정한다."""

    def test_the_spec_copies_the_pdf_into_the_package(self) -> None:
        spec = (
            Path(__file__).resolve().parents[1] / "packaging" / "procurement-backend.spec"
        ).read_text(encoding="utf-8")

        declaration = 'MANUAL = ROOT / "src" / "procurement" / "web" / "manual"'
        assert f'{declaration} / "user_manual.pdf"' in spec
        assert '(str(MANUAL), "procurement/web/manual")' in spec

    def test_the_pdf_can_be_rebuilt_from_the_markdown(self) -> None:
        """⚠️ 되돌릴 수 없는 바이너리를 남기지 않는다 — 만드는 방법이 있어야 한다."""
        script = Path(__file__).resolve().parents[1] / "scripts" / "build_manual_pdf.py"

        assert script.exists()
        text = script.read_text(encoding="utf-8")
        assert "USER_MANUAL.md" in text
        assert "user_manual.pdf" in text


class TestManualIsDocumented:
    """고객 문서에 단추가 적혀 있다."""

    def test_the_user_manual_mentions_the_button(self) -> None:
        text = (Path(__file__).resolve().parents[1] / "docs" / "USER_MANUAL.md").read_text(
            encoding="utf-8"
        )

        assert "**사용자 매뉴얼**" in text

    def test_the_install_guide_mentions_the_button(self) -> None:
        text = (Path(__file__).resolve().parents[1] / "docs" / "INSTALL_GUIDE.md").read_text(
            encoding="utf-8"
        )

        assert "사용자 매뉴얼" in text
