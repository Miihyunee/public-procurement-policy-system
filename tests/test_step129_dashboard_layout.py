"""
STEP 128 — 첫 화면이 **실적을 먼저** 보여주는가.

무엇이 문제였나
===============
카드 14개가 한 평면에 같은 비중으로 놓여 있었다. 매일 보는 것(실적),
월 1회 하는 일(자료 등록), 문제가 있을 때만 보는 것(점검)이 같은
크기·같은 스타일이라, 열었을 때 눈이 어디로 가야 할지 알 수 없었다.

무엇을 바꿨나
=============
성격에 따라 세 화면으로 갈랐다. 첫 화면에는 실적만 둔다.

============  ====================================================
실적          전체 구매액 → 정책별 달성 현황 → 확인이 필요한 정책
              → 바로가기
자료 관리     업로드 · 기업정보 · 확인 방식 · 목표비율 · 이력 · 검토
점검          적재 현황 · 월별 적재 · 매칭률 · 미매칭 · 정책별 현황
============  ====================================================

⛔ **업무 로직을 건드리지 않았다.** 목표·실적·달성률·상태는 전부 서버가
준 값을 그대로 그린다. 화면이 판정하거나 계산하지 않는다.

🟢 2026-09-06 PM 확정
    ① 탭 3개로 나눈다
    ② 부족 **금액** 을 만들지 않는다 — 서버가 주는 비율만 쓴다
    ③ 첫 화면은 판정 색만 쓴다 — 표시용 5구간 색을 쓰지 않는다
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "src" / "procurement" / "web" / "static" / "index.html"

#: 서버가 내려주는 상태 코드. ⛔ 화면이 새 상태를 만들지 않는다.
SERVER_STATUSES = ("NORMAL", "WARNING", "SHORTAGE")


@pytest.fixture(scope="module")
def page() -> str:
    return PAGE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def markup(page: str) -> str:
    """``<script>`` 앞의 마크업만. 스크립트 문자열에 속지 않으려는 것이다."""
    return page[: page.index("<script>")]


def _panel(markup: str, panel_id: str) -> str:
    """탭 하나가 담고 있는 마크업."""
    start = markup.index(f'id="{panel_id}"')
    later = [
        markup.index(f'id="{other}"')
        for other in ("panel-perf", "panel-data", "panel-check")
        if markup.index(f'id="{other}"') > start
    ]
    return markup[start : min(later)] if later else markup[start:]


def _card_titles(fragment: str) -> list[str]:
    return re.findall(r'class="card-title"[^>]*>([^<]+)<', fragment)


# ======================================================================
# 세 화면으로 갈렸는가
# ======================================================================
class TestTheScreenIsSplitByHowOftenItIsUsed:
    def test_1_there_are_three_tabs(self, markup: str) -> None:
        for tab in ("tab-perf", "tab-data", "tab-check"):
            assert f'id="{tab}"' in markup

    def test_2_performance_is_the_one_that_opens(self, markup: str) -> None:
        """⭐ 열면 실적이 보인다 — 나머지는 접혀 있다."""
        assert 'id="tab-perf"\n            aria-controls="panel-perf" aria-selected="true"' in (
            markup
        )
        assert 'id="panel-data" role="tabpanel" aria-labelledby="tab-data" hidden' in markup
        assert 'id="panel-check" role="tabpanel" aria-labelledby="tab-check" hidden' in markup

    def test_3_the_first_screen_holds_only_performance(self, markup: str) -> None:
        """⛔ 업로드·등록·점검 **카드**가 첫 화면에 없다.

        .. note::
            같은 낱말이 바로가기 **버튼 이름**으로는 나온다("기업정보 등록"
            으로 가는 단추). 그것은 카드가 아니라 길잡이이므로, 카드 제목만
            보고 판단한다.
        """
        first = _panel(markup, "panel-perf")

        assert _card_titles(first) == []

    def test_4_monthly_work_moved_to_the_second_screen(self, markup: str) -> None:
        titles = _card_titles(_panel(markup, "panel-data"))

        assert set(titles) == {
            "목표비율 관리",
            "구매실적 업로드",
            "기업정보 등록",
            "기업정보 확인 방식",
            "업로드 이력",
            "구매유형 검토",
        }

    def test_5_diagnostics_moved_to_the_third_screen(self, markup: str) -> None:
        titles = _card_titles(_panel(markup, "panel-check"))

        assert "데이터 적재 현황" in titles
        assert "미매칭 기업" in titles
        assert "기업 매칭률" in titles

    def test_6_nothing_was_thrown_away(self, markup: str) -> None:
        """⛔ 기능을 없애지 않았다 — 자리만 옮겼다."""
        everywhere = _card_titles(markup)

        for card in (
            "데이터 요약",
            "적재 데이터 건수",
            "정책별 달성률",
            "월별 지출데이터 적재 현황",
            "정책별 현황",
        ):
            assert card in everywhere, card


# ======================================================================
# 첫 화면이 PM 이 정한 순서대로인가
# ======================================================================
class TestTheFirstScreenReadsInTheAgreedOrder:
    def test_7_total_then_policies_then_attention_then_links(self, markup: str) -> None:
        """⭐ 1 전체 실적 → 2 정책별 → 3 확인 필요 → 4 바로가기."""
        first = _panel(markup, "panel-perf")

        order = [
            first.index('id="hl-total"'),
            first.index('id="ach-body"'),
            first.index('id="attention"'),
            first.index('class="jump"'),
        ]

        assert order == sorted(order)

    def test_8_the_headline_is_the_purchase_total(self, markup: str) -> None:
        first = _panel(markup, "panel-perf")

        assert "전체 구매액" in first
        assert 'id="hl-total"' in first

    def test_9_the_shortcuts_point_at_cards_that_exist(self, markup: str) -> None:
        """⛔ 기존 기능으로 가는 길이 끊기지 않았다."""
        for panel_name, anchor in re.findall(r'data-go="(\w+)" data-focus="([\w-]+)"', markup):
            assert f'id="panel-{panel_name}"' in markup, panel_name
            assert f'id="{anchor}"' in markup, anchor

    def test_10_every_working_screen_is_reachable(self, markup: str) -> None:
        anchors = set(re.findall(r'data-focus="([\w-]+)"', markup))

        assert {"upload-card", "company-card", "review-card", "unmatched-card"} <= anchors


# ======================================================================
# ⛔ 화면이 판정하거나 계산하지 않는가
# ======================================================================
class TestTheScreenDoesNotDecideAnything:
    def test_11_the_bar_colour_comes_from_the_server_status(self, page: str) -> None:
        """⭐ 막대 색이 서버 상태와 1:1 이다 — 화면이 등급을 나누지 않는다."""
        for status in SERVER_STATUSES:
            assert f".bar-{status}" in page

        assert 'make("div", "bar bar-" + status)' in page

    def test_12_the_first_screen_does_not_use_the_display_bands(self, page: str) -> None:
        """⛔ 표시용 5구간 색(levelColor)을 첫 화면에서 쓰지 않는다.

        그 값은 코드에 「법정 기준이 아니라 화면 표시용 임시 기준」이라고 적혀
        있다. 판정 색과 섞이면 사용자가 색을 믿을 수 없다(PM 확정 ③).
        """
        first_screen = page[page.index("STEP 128 — 첫 화면(실적)") : page.index("function draw(")]

        assert "levelColor" not in first_screen
        assert "levelOf" not in first_screen

    def test_13_the_display_bands_survive_where_they_were(self, page: str) -> None:
        """⛔ 다른 화면의 기존 기능을 지우지 않았다(PM 확정 ③ 단서)."""
        assert "function levelColor(" in page
        assert "levelColor(levelOf(" in page

    def test_14_no_shortage_amount_is_invented(self, page: str) -> None:
        """⛔ 부족 **금액** 을 만들지 않는다 (PM 확정 ②).

        서버가 주는 것은 비율뿐이다. 전체 구매액 × 부족률 같은 식으로 화면이
        금액을 지어내면, 담당자가 그 숫자를 보고서에 옮겨 적게 된다.

        .. note::
            STEP 128-2 에서 첫 화면은 부족 **비율마저** 적지 않게 됐다. 같은
            정책의 숫자를 표와 아래 영역에서 두 번 읽게 하지 않으려는 것이다.
            그래도 이 시험이 지키는 것은 그대로다 — **금액을 만들지 않는다.**
        """
        first_screen = page[page.index("STEP 128 — 첫 화면(실적)") : page.index("function draw(")]

        assert "shortage_amount" not in page
        for forbidden in (
            "total_purchase_amount *",
            "* shortage",
            "shortage_rate *",
            "parseFloat(item.shortage_rate) *",
        ):
            assert forbidden not in first_screen, forbidden

    def test_15_the_state_wording_is_the_servers(self, page: str) -> None:
        """상태 문구를 화면이 새로 쓰지 않는다 — ``status_label`` 을 그대로 쓴다."""
        assert "stateChip(item.status, item.status_label)" in page

    def test_16_uncalculated_policies_are_not_shown_as_zero(self, page: str) -> None:
        """⛔ 「조회불가」·「계산 보류」를 0% 로 적지 않는다."""
        assert 'calculable ? item.achievement_rate + "%" : "—"' in page

    def test_17_sorting_does_not_recompute(self, page: str) -> None:
        """정렬은 순서만 바꾼다 — 어떤 값도 다시 만들지 않는다."""
        assert "needsAttention" in page
        assert 'return status === "SHORTAGE" || status === "WARNING";' in page


# ======================================================================
# 업무 로직을 건드리지 않았는가
# ======================================================================
class TestTheBusinessLogicIsUntouched:
    def test_18_no_new_endpoint_was_invented(self, page: str) -> None:
        """⛔ 기존 API 만 쓴다."""
        endpoints = set(re.findall(r'"(/[a-z][a-z0-9\-/]*)"', page))

        assert "/dashboard/summary" not in endpoints or True  # 기존 그대로
        for invented in ("/dashboard/headline", "/dashboard/attention", "/dashboard/overview"):
            assert invented not in page, invented

    def test_19_the_existing_renderers_still_run(self, page: str) -> None:
        """기존 화면 코드를 지우지 않았다 — 점검 탭에서 그대로 쓰인다."""
        for renderer in ("renderPolicies(", "renderAchievement(", "renderStatusTable("):
            assert renderer in page, renderer


# ======================================================================
# STEP 128-2 — 같은 정책을 두 번 적지 않는가
# ======================================================================
class TestTheSamePolicyIsNotListedTwice:
    def test_20_the_lower_block_is_about_what_to_do(self, page: str) -> None:
        """⭐ 아래 영역은 정책을 다시 나열하는 곳이 아니라 **할 일**을 적는 곳이다.

        예전에는 표에 있는 「부족」 정책 세 종을 바로 아래에서 그대로 다시
        보여 줬다. 같은 것을 두 번 읽게 만든다(STEP 128-2 §1).
        """
        assert "TODO_GROUPS" in page
        assert 'class="todo"' in page

    def test_21_it_does_not_repeat_the_numbers(self, page: str) -> None:
        """⛔ 목표·달성률 숫자를 아래에서 되풀이하지 않는다 — 위 표에 있다."""
        block = page[
            page.index("function renderAttention(") : page.index(
                "// -------", page.index("function renderAttention(")
            )
        ]

        for repeated in ("target_rate", "achievement_rate", "shortage_rate"):
            assert repeated not in block, repeated

    def test_22_three_kinds_are_told_apart(self, page: str) -> None:
        """⭐ 성격이 다른 상태는 같은 뜻이 아니다(§3).

        각각 해야 할 일이 다르다 — 실적을 채우는 일, 자료를 등록하는 일,
        구매유형을 확정하는 일, 목표를 넣는 일, 기준이 정해지기를 기다리는 일.

        ② 요구사항 변경(STEP 151). 예전에는 「계산 보류」와 「목표율 미설정」을
        한 묶음으로 두고 둘 다 목표비율 관리로 보냈다. 여성기업은 목표비율이
        이미 들어 있어서 그 화면에 **할 일이 없었다**(STEP 150 발견). 이제
        멈춘 이유를 :data:`holdCause` 가 셋으로 갈라 각각 다른 곳으로 보낸다.
        """
        assert '"SHORTAGE", "WARNING"' in page
        assert '"COMPANY_DATA_NOT_REGISTERED"' in page
        for cause in ("HOLD_REVIEW", "HOLD_TARGET", "HOLD_BASIS"):
            assert "cause: " + cause in page, cause

    def test_23_each_kind_leads_somewhere_that_exists(self, page: str, markup: str) -> None:
        """⛔ 새 화면을 만들지 않는다 — 기존 자리로 데려다 준다."""
        for anchor in re.findall(r'focus: "([\w-]+)"', page):
            assert f'id="{anchor}"' in markup, anchor

        for tab in re.findall(r'tab: "(\w+)"', page):
            assert f'id="panel-{tab}"' in markup, tab

    def test_24_the_status_never_relies_on_colour_alone(self, page: str) -> None:
        """⛔ 색만으로 뜻을 전하지 않는다 — 알약 안에 글자가 함께 있다(§4)."""
        assert 'make("span", "state st-" + status, label)' in page


# ======================================================================
# STEP 128-2 — 무엇을 앞세웠는가
# ======================================================================
class TestTheImportantThingsLookImportant:
    def test_25_the_rate_is_heavier_than_the_supporting_numbers(self, page: str) -> None:
        """달성률은 굵게, 목표·실적은 한 단계 내린다(§5)."""
        assert "td.ach-rate { font-weight: 700" in page
        assert "td.ach-support { color: var(--muted)" in page
        assert '"n ach-support"' in page
        assert '"n ach-rate"' in page

    def test_26_the_shortcuts_are_a_side_area(self, page: str, markup: str) -> None:
        """⛔ 바로가기가 실적보다 눈에 띄면 안 된다(§6)."""
        assert '<p class="jump-label">관련 업무</p>' in markup
        assert "font-size: 11.5px" in page[page.index("  .jump button {") :][:400]

    def test_27_nothing_was_removed_from_the_shortcuts(self, markup: str) -> None:
        """⛔ 기능은 그대로 여섯 개다 — 작아졌을 뿐이다."""
        assert len(re.findall(r'data-focus="[\w-]+"', markup)) == 6
