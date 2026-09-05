"""
STEP 72 — 운영 검수 체크리스트가 **빠뜨린 것이 없는가**.

이 파일은 계산을 시험하지 않습니다. `docs/OPERATIONS_CHECKLIST.md` 가

1. 검수 순서를 빠짐없이 담고 있는지,
2. 고객 확정사항을 정확히 옮겼는지,
3. 미확정 업무규칙을 **확정처럼 적지 않았는지**,
4. 문서에 적힌 API 경로가 **실제로 존재하는지**

를 확인합니다.

.. warning::
    ⛔ 이 문서가 업무규칙을 만들면 안 됩니다. 검수 절차 문서가 "0원은 실적
    제외" 같은 문장을 담으면, 그것을 읽은 검수자가 **고객이 답하지 않은
    규칙**을 실적 숫자에 반영하게 됩니다.

.. note::
    문서 문구를 검사하므로, 이 시험 파일 자신의 설명문에 금지 표현이 들어
    있습니다. 문서 본문만 읽고 시험 코드는 읽지 않으므로 서로 간섭하지
    않습니다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from procurement.core.performance_exclusion import EXCLUDED_BUDGET_ACCOUNTS
from procurement.uploads.format import REQUIRED_HEADERS

_CHECKLIST = Path(__file__).resolve().parents[1] / "docs" / "OPERATIONS_CHECKLIST.md"


@pytest.fixture(scope="module")
def text() -> str:
    return _CHECKLIST.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """``heading`` 으로 시작하는 절의 본문. 없으면 빈 문자열."""
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if line.startswith(heading):
            level = len(line) - len(line.lstrip("#"))
            rest = lines[index + 1 :]
            for offset, following in enumerate(rest):
                stripped = following.lstrip("#")
                if following.startswith("#") and len(following) - len(stripped) <= level:
                    return "\n".join(rest[:offset])
            return "\n".join(rest)
    return ""


class TestTheDocumentExists:
    """문서가 있고 체크박스로 쓸 수 있는 형태인가."""

    def test_the_checklist_exists(self) -> None:
        assert _CHECKLIST.exists()

    def test_it_has_checkboxes_to_tick(self, text: str) -> None:
        """PM 이 하나씩 체크하며 쓰는 문서다."""
        assert text.count("□") > 80


class TestTheVerificationOrder:
    """검수 순서 16 단계가 모두 있는가."""

    @pytest.mark.parametrize(
        "step",
        [
            "원본 Excel 확보",
            "파일 기본 구조 확인",
            "업로드",
            "validation 결과 확인",
            "적재 / 미적재 정합성 확인",
            "ACTIVE 배치 확인",
            "기간 조건 확인",
            "계산 대상 건수·금액 확인",
            "기업 매칭 상태 확인",
            "구매유형 검토",
            "실적 제외 대상 검토",
            "분모 / 분자 확인",
            "정책별 달성률 확인",
            "대시보드 표시값 확인",
            "재업로드 필요 시 중복 여부 확인",
            "최종 검수 완료",
        ],
    )
    def test_each_stage_is_in_the_flow(self, text: str, step: str) -> None:
        assert step in text


class TestTheCoreVerificationItems:
    """검수의 뼈대가 되는 항목들."""

    @pytest.mark.parametrize(
        "item",
        [
            "원본 행 수",
            "적재 행 수",
            "미적재 행 수",
            "ACTIVE",
            "SUPERSEDED",
            "분모",
            "분자",
            "달성률",
            "실적 제외",
            "기업 매칭",
            "구매유형",
            "재업로드",
        ],
    )
    def test_the_item_is_present(self, text: str, item: str) -> None:
        assert item in text

    def test_the_row_count_identity_is_stated(self, text: str) -> None:
        """원본 = 적재 + 미적재 라는 관계 자체가 적혀 있어야 한다."""
        assert "원본 행 수 = 적재 행 수 + 미적재 행 수" in text
        assert "unexplained" in text

    def test_double_counting_is_the_headline_batch_check(self, text: str) -> None:
        """재업로드 검수의 핵심은 **중복 합산이 없는가**이다."""
        assert "중복 합산되지 않" in text

    def test_the_numerator_must_shrink_too(self, text: str) -> None:
        """⭐ 분모에서만 빼면 달성률이 실제보다 높아진다."""
        assert "분모에서만 빼는 방식이 되어서는 안 된다" in text

    def test_the_period_boundary_is_inclusive(self, text: str) -> None:
        assert "시작일 데이터가 포함되는가" in text
        assert "종료일 데이터가 포함되는가" in text

    def test_the_kpi_tile_is_not_the_denominator(self, text: str) -> None:
        """적재된 구매금액 합계 ≠ 계산 대상 전체 구매액 (STEP 68)."""
        assert "적재된 구매금액 합계" in text
        assert "계산 분모와 같은 숫자로 보지 않는다" in text

    def test_internal_column_names_must_not_reach_the_screen(self, text: str) -> None:
        assert "resolution_date 같은 내부 컬럼명이 사용자 화면에 노출되지 않는가" in text


class TestCustomerConfirmedRules:
    """고객이 답한 것을 **그대로** 옮겼는가."""

    @pytest.mark.parametrize(
        "item",
        ["단기 차량 임차", "사업부서 품의서", "교육비", "강사료", "지출결의서", "세금계산서"],
    )
    def test_the_confirmed_wording_is_present(self, text: str, item: str) -> None:
        assert item in text

    def test_the_six_budget_accounts_are_all_listed(self, text: str) -> None:
        """⭐ 여섯 개가 **전부** 있어야 한다 — 하나 빠지면 검수에서 새어 나간다."""
        for account in EXCLUDED_BUDGET_ACCOUNTS:
            assert account in text

    def test_no_seventh_account_was_invented(self, text: str) -> None:
        """⛔ 예산과목을 임의로 더하지 않았다."""
        section = _section(text, "## H.1")
        assert section
        listed = {
            line.strip()
            for line in section.splitlines()
            if line.strip().endswith("비") and " " not in line.strip()
        }
        assert listed == set(EXCLUDED_BUDGET_ACCOUNTS)

    def test_exact_match_not_substring(self, text: str) -> None:
        assert "부분 문자열이 아니라 정확히 같은 값" in text
        assert "교육훈련비지원" in text

    def test_no_day_threshold_is_used_for_vehicle_lease(self, text: str) -> None:
        """고객: "하루·1박2일·2박3일 등 기간과 상관없이"."""
        assert "기간 임계값을 쓰지 않는다" in text
        assert "기간과 상관없이" in text

    def test_words_alone_never_exclude(self, text: str) -> None:
        assert "낱말이 있다는 이유만으로 자동으로 빼지 않는다" in text
        assert "적요 낱말만으로 자동 제외하지 않는다" in text

    def test_the_two_named_rows_are_checked_individually(self, text: str) -> None:
        assert "민원 담당자 교육" in text
        assert "같은 적요라는 이유만으로 다른 거래까지 일괄 제외하지 않는다" in text

    def test_no_document_number_and_no_grouping(self, text: str) -> None:
        """Q5-3 — 결의번호 없음 · 자동 그룹핑 없음."""
        assert "결의번호 없음" in text
        assert "자동 그룹핑 없음" in text
        assert "지출결의서 단위로 임의 묶음을 만들지 않는다" in text

    def test_the_customers_own_comparison_keys_are_offered(self, text: str) -> None:
        """적요 · 거래처명 · 사업자등록번호 · 금액."""
        section = _section(text, "# STEP G")
        for key in ("적요로 검색", "거래처명으로 검색", "사업자등록번호로 검색", "금액"):
            assert key in section


class TestUnconfirmedRulesAreProtected:
    """미확정 항목을 검수자가 임의로 정하지 않도록 막았는가."""

    @pytest.mark.parametrize("item", ["W-1-2", "Q5-8", "Q5-9", "W-11 ~ W-15", "구매유형 자동분류"])
    def test_the_warning_block_names_it(self, text: str, item: str) -> None:
        section = _section(text, "# ⚠️ 운영 검수 중 임의 결정 금지")
        assert section
        assert item in section

    def test_findings_are_recorded_not_decided(self, text: str) -> None:
        """⭐ 답이 없는 것과 잘못 동작하는 것은 다르다."""
        assert "고객 업무규칙 확인 필요" in text
        assert '"시스템 오류" 로 처리하지 말고' in text

    def test_the_final_verdict_offers_that_choice(self, text: str) -> None:
        section = _section(text, "## 최종 판정")
        verdicts = ("정상", "담당자 확인 필요", "데이터 재확인 필요", "고객 업무규칙 확인 필요")
        for verdict in verdicts:
            assert verdict in section

    @pytest.mark.parametrize(
        "phrase",
        [
            "0원은 실적 제외",
            "음수는 실적 제외",
            "예산과목 공란은 제외",
            "구매유형 자동 확정",
            "0원·음수 행은 실적에서 제외",
        ],
    )
    def test_no_unconfirmed_rule_is_stated_as_settled(self, text: str, phrase: str) -> None:
        """⛔ 검수 문서가 미확정 규칙을 확정처럼 적으면 안 된다."""
        assert phrase not in text

    def test_rejected_rows_keep_the_neutral_wording(self, text: str) -> None:
        """⛔ Q5-8 이 열려 있는 동안 미적재 행을 판정하는 말로 부르지 않는다."""
        section = _section(text, "# STEP C")
        assert section
        pattern = r"(무효 데이터|삭제된 데이터|오류 데이터|부적합|검토 불필요)"
        forbidden = re.findall(pattern, section)
        # 이 낱말들은 "쓰지 않는다" 표 안에서만 나온다.
        for word in forbidden:
            for line in section.splitlines():
                if word in line:
                    assert "쓰지 않는다" in line or line.strip().startswith("|")

    def test_zero_and_negative_rows_are_left_open(self, text: str) -> None:
        assert "Q5-8 미확정" in text
        assert "검수자가 정하지 않는다" in text


class TestDesignJudgementsAreNotPromoted:
    """⛔ STEP 71 에서 갈라 둔 설계 판단 4건을 고객 사양으로 승격하지 않는다."""

    def test_the_four_are_marked_as_our_choice(self, text: str) -> None:
        section = _section(text, "# ⚠️ 고객이 요구한 사양이 아닌 것")
        assert section
        assert "find_for_review" in section
        assert "find_for_calculation" in section
        assert "원본 행을 지우지 않고 보존" in section
        assert "되돌릴 수 없다" in section
        assert "검색어" in section

    def test_it_says_they_are_unconfirmed(self, text: str) -> None:
        section = _section(text, "# ⚠️ 고객이 요구한 사양이 아닌 것")
        assert "고객이 정한 사양처럼 설명하지 않는다" in section
        assert "0.10.8" in section


class TestTheDocumentPointsAtRealTools:
    """문서가 **존재하지 않는 도구**를 가리키면 검수자가 헤맨다."""

    def test_every_listed_endpoint_exists(self, text: str) -> None:
        from fastapi.routing import APIRoute

        from procurement.app import create_app

        app = create_app(Path("unused.db"))
        real = {route.path for route in app.routes if isinstance(route, APIRoute)}

        section = _section(text, "## 0.1 관련 조사 도구")
        assert section
        listed = set(re.findall(r"`(?:GET|POST|PUT|DELETE) (/[^`?]*)", section))
        assert listed
        assert listed <= real

    def test_the_standard_columns_match_the_code(self, text: str) -> None:
        """⛔ 새 필수 컬럼을 문서가 임의로 만들지 않았다."""
        section = _section(text, "# STEP A")
        assert section
        for header in REQUIRED_HEADERS:
            assert header in section

    def test_no_investigation_script_is_promised(self, text: str) -> None:
        """STEP 66 은 스크립트를 만들지 않았다 — 없는 도구를 적지 않는다."""
        assert "별도 조사 스크립트는 없다" in text
        scripts = Path(__file__).resolve().parents[1] / "scripts"
        assert not (scripts / "step66_survey.py").exists()
