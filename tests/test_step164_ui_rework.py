"""
STEP 164 — 화면을 읽고 바로 다음 행동을 할 수 있게.

무엇이 문제였나 (STEP 163 실측)
================================
- 실적 화면은 «—» 로 가득 찬 표를 먼저 보여 주고, 계산하지 못한 이유는 표
  **아래**에 있었다. 그 아래 바로가기 6개가 **모두 같은 굵기**여서 다음
  행동이 여섯 갈래로 흩어졌다.
- 구매유형 검토는 한 건이 세로 450px 카드였고, 같은 안내문이 **모든 행에**
  반복됐다(175건이면 175번). 거르개 12개가 늘 펼쳐져 있었다.
- 단추 55개가 **전부 같은 모양**이라 「검증만」과 「검증 후 저장」이
  구별되지 않았고, 처음엔 둘 다 **이유 없이** 흐려져 있었다.
- 도움말이 PDF 한 덩어리뿐이라 화면을 보며 따라 할 수 없었다.

무엇을 바꿨나 (🟢 2026-09-11 PM 확정)
=====================================
1. 「지금 하실 일」을 표 **위**로 올리고, 맨 앞 하나만 주 CTA 로 키웠다.
2. 바로가기는 접었다.
3. 검토 목록은 **한 줄에 한 결정**. 자세한 것은 접기. 반복 안내문은 한 번만.
4. 진행률 막대를 검토 화면 맨 위에 두었다.
5. 단추를 주 CTA · 보조 · 위험 세 등급으로 갈랐다. 비활성에는 사유를 적는다.
6. 화면 안내(온보딩)를 넣었다.
7. 글자 크기·굵기 토큰을 정의했다.

⛔ API · DB · 계산 로직 · 상태값을 바꾸지 않았다.
⛔ 기능을 없애지 않았다 — 접었을 뿐이고, 펼치면 그대로 있다.
⛔ 외부 리소스를 쓰지 않는다 — 폐쇄망에서 돌아야 한다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.database.bootstrap import bootstrap

ROOT = Path(__file__).resolve().parents[1]
PAGE_PATH = ROOT / "src" / "procurement" / "web" / "static" / "index.html"


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE_PATH.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def markup(page: str) -> str:
    """``<script>`` 앞의 마크업만 — 스크립트 안 문자열에 속지 않는다."""
    return page[: page.index("<script>")]


@pytest.fixture(scope="module")
def styles(page: str) -> str:
    return page[page.index("<style>") : page.index("</style>")]


def _function_body(page: str, name: str) -> str:
    start = page.index("function " + name + "(")
    depth = 0
    opened = False
    for index in range(start, len(page)):
        char = page[index]
        if char == "{":
            depth += 1
            opened = True
        elif char == "}":
            depth -= 1
            if opened and depth == 0:
                return page[start : index + 1]
    raise AssertionError(f"{name} 의 끝을 찾지 못했다")


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    db = tmp_path / "t.db"
    bootstrap(db)
    return TestClient(create_app(db))


# ======================================================================
# 실적 화면
# ======================================================================
class TestThePerformanceScreenLeadsWithOneThingToDo:
    def test_the_reason_comes_before_the_table(self, markup: str) -> None:
        """⭐ 계산하지 못한 사유가 표보다 **위**에 있다."""
        panel = markup[markup.index('id="panel-perf"') : markup.index('id="panel-check"')]
        assert panel.index('id="attention"') < panel.index('id="ach-body"')

    def test_only_the_first_item_becomes_the_main_button(self, page: str) -> None:
        """⛔ 주 CTA 는 화면당 하나다. 둘이면 둘 다 주가 아니게 된다."""
        body = _function_body(page, "renderAttention")
        assert "var first = placed === hits.length;" in body
        assert '(first ? " todo-first" : "")' in body
        assert '"todo-go" + (first ? " control is-primary" : "")' in body

    def test_the_first_item_says_what_it_is(self, page: str) -> None:
        assert '"지금 하실 일"' in _function_body(page, "renderAttention")

    def test_the_shortcuts_are_folded_but_still_all_there(self, markup: str) -> None:
        """⛔ 기능을 없애지 않았다 — 접었을 뿐이다."""
        assert '<details class="jump" id="jump-more">' in markup
        assert '<summary class="jump-label">다른 작업 보기</summary>' in markup
        assert len(re.findall(r'data-focus="[\w-]+"', markup)) == 6

    def test_the_dash_still_means_not_calculated(self, page: str) -> None:
        """⛔ «—» 와 0% 의 뜻을 바꾸지 않았다."""
        assert "목표율이 없거나 계산이 보류된 정책은 달성률 자리에 «—» 로 표시합니다. " in page
        assert "0% 가 아닙니다" in page


# ======================================================================
# 구매유형 검토 화면
# ======================================================================
class TestTheReviewScreenIsOneRowOneDecision:
    def test_the_progress_bar_is_at_the_top(self, markup: str) -> None:
        """맨 위에서 「남은 N건 / 전체 M건 · %」 를 본다."""
        card = markup[markup.index('id="review-card"') :]
        assert card.index('id="review-progress-bar"') < card.index('id="review-list"')
        for part in ("review-progress-left", "review-progress-total",
                     "review-progress-pct", "review-progress-fill"):
            assert f'id="{part}"' in card, part

    def test_the_progress_numbers_come_from_the_server(self, page: str) -> None:
        """⛔ 진행률을 화면이 새로 세지 않는다."""
        body = _function_body(page, "renderProgress")
        assert "whole.total - whole.confirmed" in body
        assert "whole.confirmed / whole.total" in body

    def test_the_repeated_guidance_is_shown_once(self, page: str, markup: str) -> None:
        """⭐ 카드마다 반복하던 안내문이 목록 위 한 곳으로 옮겨졌다."""
        assert 'id="review-guide"' in markup
        # ⛔ 더 이상 카드마다 붙이지 않는다.
        assert 'make("div", "rv-why", vehicleLeaseNotice)' not in page
        # ⛔ 문구 자체를 지운 것은 아니다 — 서버 값을 그대로 쓴다.
        assert "vehicleLeaseNotice" in page

    def test_each_row_starts_as_one_line(self, page: str) -> None:
        body = _function_body(page, "reviewCard")
        assert "reviewSummaryLine(item)" in body
        assert body.index("reviewSummaryLine(item)") < body.index("reviewSource(item.source)")

    def test_the_one_line_carries_what_the_decision_needs(self, page: str) -> None:
        body = _function_body(page, "reviewSummaryLine")
        for needed in ("company_name", "description", "amount",
                       "reviewStatusLabel(item.review.status)",
                       "final_purchase_type_label"):
            assert needed in body, needed

    def test_a_long_description_is_still_readable_in_full(self, page: str) -> None:
        """⛔ 잘린 글자를 볼 방법이 없어지면 안 된다."""
        body = _function_body(page, "reviewSummaryLine")
        assert "what.title = item.source.description" in body

    def test_the_detail_is_folded_but_complete(self, page: str) -> None:
        """⛔ 지운 것이 없다 — 펼치면 예전과 같은 내용이 그대로 나온다."""
        body = _function_body(page, "reviewCard")
        assert 'make("details", "rv-detail")' in body
        for kept in ("reviewSource(item.source)", "descriptionHints(item.description_hints)",
                     "companyHistory(item.company_labels)", "reviewStateLine(item.review)",
                     "performanceBlock(item)"):
            assert kept in body, kept

    def test_the_decision_controls_stay_outside_the_fold(self, page: str) -> None:
        """⭐ 고르고 확정하는 것은 접지 않는다 — 그것이 이 화면의 목적이다."""
        body = _function_body(page, "reviewCard")
        assert "card.appendChild(reviewPicker(item))" in body

    def test_the_advanced_filters_are_folded_but_all_there(self, markup: str) -> None:
        """⛔ 거르개를 하나도 없애지 않았다."""
        assert '<details class="fold-filters" id="review-more-filters">' in markup
        fold = markup[markup.index('id="review-more-filters"') : markup.index('id="review-notice"')]
        for kept in ("review-decision", "review-history", "review-candidates",
                     "review-sort", "review-direction", "review-page-size",
                     "review-export", "review-export-history"):
            assert kept in fold, kept
        # 자주 쓰는 것은 접히지 않는다.
        head = markup[markup.index('id="review-card"') : markup.index('id="review-more-filters"')]
        for visible in ("review-search", "review-policy", "review-status", "review-period"):
            assert visible in head, visible

    def test_one_at_a_time_stays(self, page: str) -> None:
        """⛔ 일괄 확정을 넣지 않았다 (🟢 PM 확정 · 이번 범위 밖)."""
        assert "일괄 확정" not in page
        assert 'type="checkbox"' not in _function_body(page, "reviewCard")


# ======================================================================
# 단추 세 등급
# ======================================================================
class TestButtonsSayWhichOneMatters:
    def test_the_three_tiers_exist(self, styles: str) -> None:
        assert ".control.is-primary" in styles
        assert ".control.is-danger" in styles

    def test_one_main_button_per_screen(self, markup: str) -> None:
        """주 CTA 는 카드마다 하나씩만."""
        # ⛔ 안내(온보딩)·확인창은 업무 화면 위에 뜨는 상자이므로 세지 않는다.
        panel = markup[markup.index('id="panel-data"') : markup.index('id="replace-dialog"')]
        assert panel.count("control is-primary") == 4  # 목표비율 · 업로드 · 기업정보 · 조회

    def test_the_labels_say_what_happens(self, page: str) -> None:
        for label in ("검증하고 저장하기", "목표비율 저장", "조회하고 저장하기",
                      "확정 취소 (다시 검토 대상이 됩니다)"):
            assert label in page, label

    def test_a_disabled_button_says_why(self, markup: str, page: str) -> None:
        """⛔ 이유 없이 흐린 단추를 두지 않는다.

        ② 요구사항 변경 (🟢 2026-09-11 PM 확정 · STEP 165) — 사유를 직접
        ``hidden = true`` 로 감추던 한 줄 대신 ``uploadWhy(사유)`` 한 곳을
        지난다. 단계가 셋(파일 고르기 → 검증 → 저장)으로 늘어 사유도
        단계마다 달라지기 때문이다. 흐린 단추에 사유를 반드시 적는다는
        규칙 자체는 그대로이고, 오히려 더 여러 단계에서 지켜진다.
        """
        assert 'id="upload-why"' in markup
        assert "파일을 먼저 선택하세요." in markup
        # 사유를 쓰고 지우는 곳이 한 군데로 모였다.
        assert "function uploadWhy(reason)" in page
        assert "node.hidden = !reason;" in page
        # 단계마다 사유가 실제로 적힌다.
        assert 'uploadWhy("검증을 먼저 실행하세요.");' in page
        assert 'uploadWhy(uploadValidated ? "" : "오류를 수정한 파일을 다시 선택하세요.");' in page

    def test_the_dangerous_ones_are_marked(self, page: str) -> None:
        assert 'make("button", "control is-danger", "확정 취소 (다시 검토 대상이 됩니다)")' in page
        assert 'make("button", "control is-danger", "실적에서 제외")' in page

    def test_undo_still_asks_first(self, page: str) -> None:
        """⛔ 되돌리기 어려운 작업은 바로 실행하지 않는다."""
        assert "askUndo(item)" in page


# ======================================================================
# 화면 안내(온보딩)
# ======================================================================
class TestTheTourHelpsWithoutGettingInTheWay:
    def test_the_help_button_exists(self, markup: str) -> None:
        assert 'id="help-tour"' in markup
        assert "도움말" in markup

    def test_all_four_controls_exist(self, markup: str) -> None:
        for control in ("tour-prev", "tour-next", "tour-skip"):
            assert f'id="{control}"' in markup, control
        assert '"완료"' in PAGE_PATH.read_text(encoding="utf-8")

    def test_it_runs_once_and_remembers(self, page: str) -> None:
        body = _function_body(page, "initTour")
        assert "if (!tourDone()) { window.setTimeout(startTour, 1200); }" in body
        assert "markTourDone" in _function_body(page, "tourNext")
        assert "setItem" in _function_body(page, "markTourDone")

    def test_a_blocked_storage_does_not_show_it_forever(self, page: str) -> None:
        """⛔ 저장소를 막아 둔 PC 에서 매번 뜨지 않는다."""
        body = _function_body(page, "tourDone")
        assert "catch (error) { return true; }" in body

    def test_a_missing_target_is_skipped(self, page: str) -> None:
        """⛔ 없는 것을 가리키지 않는다."""
        body = _function_body(page, "tourAvailable")
        assert "return !!node && !node.hidden;" in body

    def test_an_offscreen_target_is_brought_into_view(self, page: str) -> None:
        assert "scrollIntoView({ block: \"center\", inline: \"nearest\" })" in (
            _function_body(page, "placeTour")
        )

    def test_resizing_does_not_leave_the_ring_behind(self, page: str) -> None:
        assert 'window.addEventListener("resize"' in _function_body(page, "initTour")

    def test_it_never_blocks_the_working_screen(self, page: str) -> None:
        """⭐ 이 STEP 에서 가장 중요한 안전장치."""
        for name in ("initTour", "startTour", "tourShow"):
            assert "catch (error)" in _function_body(page, name), name
        assert "Escape" in _function_body(page, "initTour")

    def test_the_tour_covers_the_two_screens(self, page: str) -> None:
        block = page[page.index("var TOUR_STEPS = ["):]
        block = block[: block.index("];")]
        assert block.count('tab: "perf"') >= 3
        assert block.count('tab: "data"') >= 3


# ======================================================================
# 글자 · 폐쇄망
# ======================================================================
class TestTypographyAndOfflineRules:
    @pytest.mark.parametrize(
        "token",
        ["--t-page", "--t-section", "--t-card", "--t-body", "--t-sub", "--t-button",
         "--t-badge", "--t-th", "--t-td", "--t-error", "--t-number"],
    )
    def test_every_token_is_defined(self, styles: str, token: str) -> None:
        assert f"{token}:" in styles

    def test_the_font_stack_is_the_agreed_one(self, styles: str) -> None:
        stack = styles[styles.index("--font:") : styles.index("--font:") + 220]
        for name in ("Pretendard", "Noto Sans KR", "Malgun Gothic"):
            assert name in stack, name

    def test_numbers_do_not_shift(self, styles: str) -> None:
        assert "font-variant-numeric: tabular-nums" in styles

    def test_no_external_resource_is_pulled_in(self, page: str) -> None:
        """⛔ 폐쇄망에서 돈다 — 인터넷에서 아무것도 받아오지 않는다."""
        # 실제로 **받아오는** 자리만 본다. ``SVG_NS`` 같은 이름공간 문자열은
        # 네트워크를 쓰지 않는다.
        for pulled in re.findall(r'(?:src|href)\s*=\s*["\']([^"\']+)["\']', page):
            assert not pulled.startswith(("http://", "https://", "//")), pulled
        for banned in ("@import url(", "fonts.googleapis.com", "fonts.gstatic.com",
                       "cdn.jsdelivr.net", "unpkg.com"):
            assert banned not in page, banned

    def test_the_font_travels_inside_the_installer(self, page: str) -> None:
        """⭐ 글꼴 파일이 화면 안에 담겨 있다 — 인터넷이 없어도 적용된다."""
        assert page.count("@font-face") >= 2
        assert page.count("url(data:font/woff2;base64,") == 2
        assert 'font-weight: 400;' in page
        assert 'font-weight: 600 700;' in page

    def test_the_font_licence_is_recorded(self) -> None:
        """⛔ 남의 저작물을 조건 없이 담지 않는다."""
        doc = (ROOT / "docs" / "THIRD_PARTY_LICENSES.md").read_text(encoding="utf-8")
        assert "Pretendard" in doc
        assert "SIL Open Font License" in doc

    def test_the_page_still_renders(self, client: TestClient) -> None:
        response = client.get("/")
        assert response.status_code == 200
        assert 'id="tour"' in response.text
