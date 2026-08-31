"""
STEP 77 — 고객 확인 요청서와 구현 대응표가 **선을 지키는가**.

이번 STEP 은 코드를 고치지 않습니다. 그래서 이 파일이 지키는 것도 계산이
아니라 **두 문서의 경계**입니다.

무엇을 지키는가
===============

1. 답을 받아야 하는 항목이 **하나도 빠지지 않았는가**.
2. 답이 오지 않은 것을 **확정된 것처럼 적지 않았는가**.
3. 고객이 읽는 문장에 **내부 용어가 새어 나가지 않았는가**.
4. 대응표가 가리키는 **파일이 실제로 있는가**.

.. warning::
    ⛔ 가장 위험한 것은 "코드에 있다" 가 "고객이 확정했다" 로 바뀌는
    일입니다. 🟡 은 시간이 지나도 저절로 🟢 이 되지 않습니다.

.. note::
    이 파일 자신의 설명문에 금지 낱말이 들어 있으므로, 검사 대상은 **문서
    본문**뿐입니다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_DOCS = _ROOT / "docs"
_MAP = _DOCS / "CUSTOMER_RULE_IMPLEMENTATION_MAP.md"
_QUESTIONS = _DOCS / "CUSTOMER_DATA_QUESTIONS.md"

#: 고객이 답해야 하는 항목 전부.
OPEN_ITEMS = (
    "W-1-2",
    "Q5-8",
    "Q5-9",
    "Q71-A",
    "Q71-B",
    "Q71-C",
    "Q71-D",
    "W-11",
    "W-12",
    "W-13",
    "W-14",
    "W-15",
    "W-6",
)

#: 고객 문장에 나오면 안 되는 내부 용어.
INTERNAL_TERMS = (
    "API",
    "SQL",
    "repository",
    "calculator",
    "Calculator",
    "purchase_review",
    "ACTIVE",
    "SUPERSEDED",
    "find_for_calculation",
    "find_for_review",
    "evaluation_basis",
    "migration",
    "마이그레이션",
    "스키마",
)

#: 확정되지 않은 것을 확정처럼 적는 문구.
FORBIDDEN_CLAIMS = (
    "지급일 확정",
    "결의일자 확정",
    "0원 제외 확정",
    "음수 제외 확정",
    "음수 상계 확정",
    "공란 제외 확정",
    "금액 검색 확정",
    "자동 그룹핑 확정",
    "구매유형 자동 확정",
)


@pytest.fixture(scope="module")
def rule_map() -> str:
    return _MAP.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def questions() -> str:
    return _QUESTIONS.read_text(encoding="utf-8")


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


@pytest.fixture(scope="module")
def request_sheet(questions: str) -> str:
    """고객이 실제로 받아 보는 장."""
    section = _section(questions, "# 📨 확인 요청서")
    assert section, "확인 요청서 절을 찾지 못했습니다"
    return section


# ======================================================================
# 1. 빠진 항목이 없는가
# ======================================================================
class TestNothingIsMissing:
    """답을 받아야 하는 것이 전부 적혀 있어야 한다."""

    def test_the_map_exists(self) -> None:
        assert _MAP.exists()

    @pytest.mark.parametrize("item", OPEN_ITEMS)
    def test_the_map_lists_the_item(self, rule_map: str, item: str) -> None:
        assert item in rule_map

    @pytest.mark.parametrize("item", OPEN_ITEMS)
    def test_the_map_says_where_it_lives(self, rule_map: str, item: str) -> None:
        """항목만 적고 끝내지 않는다 — **어디를 고칠지**까지 있어야 한다."""
        assert "구현 위치" in rule_map
        assert item in rule_map

    def test_the_request_sheet_covers_the_urgent_ones(self, request_sheet: str) -> None:
        """고객이 이 장만 보고도 답할 수 있어야 한다."""
        for item in ("Q-A", "Q5-8", "Q5-9", "Q71-A", "Q71-B", "Q71-C", "Q71-D", "Q-B"):
            assert item in request_sheet

    def test_the_remaining_ones_are_pointed_at(self, request_sheet: str) -> None:
        for item in ("Q-C", "Q-D", "Q-E", "W-6"):
            assert item in request_sheet

    def test_every_request_item_is_open(self, request_sheet: str) -> None:
        """요청서의 모든 항목이 🔴 이어야 한다 — 답을 받으려고 보내는 것이다."""
        headings = [line for line in request_sheet.splitlines() if line.startswith("## ")]
        assert len(headings) == 9
        assert all("🔴" in heading for heading in headings)


# ======================================================================
# 2. ⛔ 아무것도 확정되지 않았다
# ======================================================================
class TestNothingWasSettled:
    """답이 오지 않은 것을 온 것처럼 적지 않았는가."""

    @pytest.mark.parametrize("claim", FORBIDDEN_CLAIMS)
    def test_no_settled_claim_in_the_map(self, rule_map: str, claim: str) -> None:
        assert claim not in rule_map

    @pytest.mark.parametrize("claim", FORBIDDEN_CLAIMS)
    def test_no_settled_claim_in_the_request_sheet(self, request_sheet: str, claim: str) -> None:
        assert claim not in request_sheet

    @pytest.mark.parametrize("item", ["W-1-2", "Q5-8", "Q5-9"])
    def test_the_open_rules_are_marked_red(self, rule_map: str, item: str) -> None:
        row = next(line for line in rule_map.splitlines() if line.startswith(f"| **{item}**"))
        assert "🔴 미회신" in row
        assert "**금지**" in row

    @pytest.mark.parametrize("item", ["Q71-A", "Q71-B", "Q71-C", "Q71-D"])
    def test_the_design_judgements_are_marked_yellow(self, rule_map: str, item: str) -> None:
        row = next(line for line in rule_map.splitlines() if line.startswith(f"| **{item}**"))
        assert "🟡" in row
        assert "🟢" not in row
        assert "**금지**" in row

    def test_being_in_the_code_is_not_confirmation(self, rule_map: str) -> None:
        """⭐ 이 문서가 존재하는 이유 자체다."""
        assert '"코드에 있다" 를 "고객이 확정했다" 로 바꾸지 않는다' in rule_map
        assert "저절로 🟢 이 되지 않는다" in rule_map

    def test_the_map_is_a_plan_not_an_approval(self, rule_map: str) -> None:
        assert "답변 전까지 어느 것도 구현하지 않는다" in rule_map
        assert "계획이지 승인이 아니다" in rule_map

    def test_the_confirmed_parts_are_not_re_asked(self, request_sheet: str) -> None:
        """🟢 이미 확정된 것을 다시 묻지 않는다 — 고객을 혼란스럽게 한다."""
        assert "이 6개를 빼는 것 자체는 다시 여쭙는 것이 아닙니다" in request_sheet
        assert "이미 답해 주셨습니다" in request_sheet

    def test_the_question_does_not_lead_the_answer(self, request_sheet: str) -> None:
        """⛔ 특정 답을 유도하지 않는다."""
        for leading in ("결의일자가 맞으시죠", "지급일자로 확정하겠습니다", "변경하겠습니다"):
            assert leading not in request_sheet

    def test_the_amount_remark_is_not_a_feature_request(self, rule_map: str) -> None:
        section = _section(rule_map, "### 2.6")
        assert section
        assert "기능 요구로 해석하지 않는다" in section

    def test_the_grouping_remark_is_not_a_request(self, rule_map: str) -> None:
        section = _section(rule_map, "### 2.7")
        assert section
        assert "요청이 아니다" in section


# ======================================================================
# 3. 고객 문장에 내부 용어가 없는가
# ======================================================================
class TestTheCustomerTextIsPlain:
    """⛔ 고객이 읽는 문장에 구현 용어를 쓰지 않는다."""

    @pytest.mark.parametrize("term", INTERNAL_TERMS)
    def test_no_internal_term_in_the_request_sheet(self, request_sheet: str, term: str) -> None:
        assert term not in request_sheet

    def test_no_snake_case_identifier_leaked(self, request_sheet: str) -> None:
        """`find_for_calculation` 같은 이름이 통째로 새어 나가지 않았는가."""
        assert re.search(r"[a-z]+_[a-z_]+\(", request_sheet) is None

    def test_the_map_may_use_internal_terms(self, rule_map: str) -> None:
        """⚠️ 반대로 **대응표는 내부 문서**이므로 파일 이름을 적어야 한다."""
        assert "database/purchase_repository.py" in rule_map
        assert "내부 문서다" in rule_map

    def test_the_map_forbids_copying_itself_to_the_customer(self, rule_map: str) -> None:
        assert "고객 질문 문장에 옮기지" in rule_map


# ======================================================================
# 4. 대응표가 가리키는 파일이 실제로 있는가
# ======================================================================
class TestThePathsAreReal:
    """⛔ 없는 파일을 가리키면 답변이 왔을 때 헤맨다."""

    def test_every_referenced_source_file_exists(self, rule_map: str) -> None:
        packages = "core|database|calculators|reviews|uploads|importers|models|collectors|dashboard"
        paths = set(re.findall(rf"`((?:{packages})/[a-z_]+\.py)`", rule_map))
        assert paths
        for path in sorted(paths):
            assert (_ROOT / "src" / "procurement" / path).exists(), path

    def test_the_referenced_documents_exist(self, rule_map: str) -> None:
        for name in ("UNCONFIRMED_RULES_IMPACT.md", "CUSTOMER_DATA_QUESTIONS.md", "DECISIONS.md"):
            assert name in rule_map
            assert (_DOCS / name).exists()


# ======================================================================
# 5. 이번 STEP 은 구현이 아니다
# ======================================================================
class TestThisStepChangedNoRules:
    """⚠️ 정리하는 STEP 이다 — 만드는 STEP 이 아니다."""

    def test_the_order_of_work_is_written_down(self, rule_map: str) -> None:
        """답이 오면 무엇부터 하는지 적혀 있어야 다음 STEP 이 흔들리지 않는다."""
        section = _section(rule_map, "## 3. 답변이 오면 하는 일")
        assert section
        assert "답변에 없는 것을 일반화하지 않는다" in section

    def test_the_priority_puts_the_numbers_first(self, rule_map: str) -> None:
        section = _section(rule_map, "## 4. 우선순위")
        assert section
        assert "W-1-2" in section
        assert "직접" in section

    def test_purchase_type_stays_manual(self, rule_map: str) -> None:
        section = _section(rule_map, "### 2.9")
        assert section
        assert "자동분류를 만들지 않는다" in section
        assert "담당자 확정" in section
