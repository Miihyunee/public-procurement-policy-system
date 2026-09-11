"""
STEP 76 — 미확정 항목이 **미확정으로 남아 있는가**.

이 파일은 계산을 시험하지 않습니다. 두 가지만 봅니다.

1. `docs/UNCONFIRMED_RULES_IMPACT.md` 가 각 항목의 **현재 동작과 고칠 자리**를
   담고 있는가.
2. 그 어디에서도 **답이 오지 않은 것을 답이 온 것처럼** 적지 않았는가.

왜 시험으로 잠그는가
====================

영향도를 적다 보면 "현재 이렇게 동작한다" 가 "이렇게 하기로 했다" 로 슬그머니
바뀌기 쉽습니다. 그렇게 되면 고객이 답하지 않은 규칙이 문서에서 확정된 것처럼
읽히고, 그다음 STEP 이 그 문서를 근거로 구현합니다.

.. warning::
    ⛔ 시험 환경에서 기간 기준일로 지급일을 주입한 것(STEP 73)은 **시험을
    돌리기 위한 값**이며 업무규칙이 아닙니다.

.. note::
    이 파일 자신의 설명문에 금지 문구가 들어 있으므로, 검사 대상은 **문서
    본문**뿐입니다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from procurement.calculators.rules.date_rules import (
    PAYMENT_DATE,
    RESOLUTION_DATE,
    RESOLUTION_OR_CONTRACT_DATE,
)
from procurement.core.config.settings import Settings
from procurement.core.performance_exclusion import is_excluded_budget_account
from procurement.core.period import ALLOWED_DATE_FIELDS
from procurement.database.bootstrap import MVP_POLICY_SEEDS

_DOCS = Path(__file__).resolve().parents[1] / "docs"
_IMPACT = _DOCS / "UNCONFIRMED_RULES_IMPACT.md"
_QUESTIONS = _DOCS / "CUSTOMER_DATA_QUESTIONS.md"


@pytest.fixture(scope="module")
def impact() -> str:
    return _IMPACT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def decisions() -> str:
    return (_DOCS / "DECISIONS.md").read_text(encoding="utf-8")


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


# ======================================================================
# 1. 영향도 문서가 일곱 항목을 모두 담았는가
# ======================================================================
class TestTheImpactDocument:
    """무엇이 어디에 걸려 있는지 적혀 있어야 한다."""

    def test_the_document_exists(self) -> None:
        assert _IMPACT.exists()

    @pytest.mark.parametrize("item", ["W-1-2", "Q5-8", "Q5-9", "Q71-A", "Q71-B", "Q71-C", "Q71-D"])
    def test_each_item_is_traced(self, impact: str, item: str) -> None:
        assert item in impact

    @pytest.mark.parametrize(
        "where",
        [
            "PURCHASE_PERIOD_DATE_FIELD",
            "evaluation_basis",
            "date_rules.py",
            "purchase_repository.py",
            "performance_exclusion.py",
            "review_service.py",
        ],
    )
    def test_it_names_the_code_it_affects(self, impact: str, where: str) -> None:
        """⭐ "영향이 있다" 로 끝내지 않고 **어느 파일인지** 적는다."""
        assert where in impact

    def test_the_two_date_axes_are_kept_apart(self, impact: str) -> None:
        """⛔ 연도 귀속 기준일과 인증 유효기간 판정 기준일은 다른 질문이다."""
        assert "두 개의 날짜 축을 섞지 않는다" in impact
        assert "하나를 정해도 다른 하나가 따라 정해지지 않는다" in impact

    def test_it_says_where_to_change_things(self, impact: str) -> None:
        assert "답이 오면 고칠 곳" in impact

    def test_the_priority_is_stated(self, impact: str) -> None:
        assert "가장 먼저 받아야 하는 것은 W-1-2 다" in impact


# ======================================================================
# 2. ⛔ 무엇도 확정되지 않았다
# ======================================================================
class TestNothingWasSettled:
    """답이 오지 않은 것을 온 것처럼 적지 않았는가."""

    @pytest.mark.parametrize(
        "phrase",
        [
            "지급일로 확정",
            "결의일자로 확정",
            "현재 지급일이 업무 기준",
            "W-1-2 해결",
            "0원은 실적 제외",
            "음수는 실적 제외",
            "예산과목 공란은 제외",
            "구매유형 자동 확정",
        ],
    )
    def test_no_forbidden_wording(self, impact: str, phrase: str) -> None:
        assert phrase not in impact

    @pytest.mark.parametrize("item", ["W-1-2", "Q5-8", "Q5-9"])
    def test_the_open_items_are_marked_red(self, impact: str, item: str) -> None:
        assert f"🔴 **{item}**" in impact or f"🔴 {item}" in impact

    @pytest.mark.parametrize("item", ["Q71-A", "Q71-B", "Q71-C", "Q71-D"])
    def test_the_design_judgements_are_marked_yellow(self, impact: str, item: str) -> None:
        """🟡 — 우리 판단이지 고객 확정이 아니다."""
        heading = next(
            line for line in impact.splitlines() if line.startswith("## ") and item in line
        )
        assert "🟡" in heading
        assert "🟢" not in heading

    def test_the_injected_date_field_is_not_a_rule(self, impact: str) -> None:
        assert "시험을 돌리기 위한 값" in impact

    def test_current_behaviour_is_not_called_correct(self, impact: str) -> None:
        assert "업무적으로 옳다는 뜻이 아니다" in impact

    def test_rejected_rows_keep_the_neutral_wording(self, impact: str) -> None:
        section = _section(impact, "## 2. 🔴 Q5-8")
        assert section
        assert '"실적 제외" · "무효" · "삭제" 로 부르지 않는다' in section

    def test_the_customers_word_and_our_design_are_kept_apart(self, impact: str) -> None:
        section = _section(impact, "## 4. 🟡 Q71-A")
        assert section
        assert "원본을 남기기로 한 것은 우리 판단" in section

    def test_comparing_amounts_is_not_a_feature_request(self, impact: str) -> None:
        section = _section(impact, "## 6. 🟡 Q71-C")
        assert section
        assert "둘을 같게 취급하지 않는다" in section

    def test_the_grouping_remark_was_not_widened(self, impact: str) -> None:
        section = _section(impact, "## 7. 🟡 Q71-D")
        assert "별도 요구사항으로" in section


# ======================================================================
# 3. 문서가 적은 "현재 동작" 이 코드와 맞는가
# ======================================================================
class TestTheDocumentMatchesTheCode:
    """⛔ 영향도 문서가 코드와 어긋나면 그다음 판단이 전부 어긋난다."""

    def test_the_general_policies_use_the_resolution_date(self) -> None:
        """일반 3개 정책의 판정 기준일 — 🟢 결의일자.

        .. note::
            **기대값이 바뀐 이유** — 2026-08-31 고객 최종 회신
            (``DECISIONS.md`` §0.12.1). 이 시험은 W-1-2 가 🔴 이던 동안
            ``PAYMENT_DATE`` 라는 **당시 동작**을 적고 있었고, 답이 오면
            깨지도록 둔 파수꾼이었습니다. STEP 84 에서 실제로 깨졌고
            확정 규칙으로 다시 적었습니다. ⛔ 지우지 않았습니다.
        """
        basis = {seed.policy_code: seed.evaluation_basis for seed in MVP_POLICY_SEEDS}
        for code in ("SMALL_BUSINESS", "WOMAN", "DISABLED"):
            assert basis[code] == RESOLUTION_DATE
        # ⛔ 지급일 기준은 더 이상 일반 정책에 붙어 있지 않다.
        assert PAYMENT_DATE not in {basis[c] for c in ("SMALL_BUSINESS", "WOMAN", "DISABLED")}

    def test_the_startup_rule_is_the_confirmed_one(self) -> None:
        """🟢 창업기업의 OR 규칙은 고객 확정이며 W-1-2 와 무관하게 유지된다."""
        basis = {seed.policy_code: seed.evaluation_basis for seed in MVP_POLICY_SEEDS}
        assert basis["STARTUP"] == RESOLUTION_OR_CONTRACT_DATE

    def test_the_year_axis_is_fixed_to_the_resolution_date(self) -> None:
        """🟢 연도 귀속 기준일 = 결의일자 (PM 확정 · STEP 86).

        .. note::
            **기대값이 바뀐 이유** — 이 시험은 *"기본값을 두면 그것이 곧
            확정이 된다"* 는 이유로 **기본값이 없음**을 잠그고 있었습니다.
            🟢 2026-09-02 PM 확정(STEP 86) — *"실적 산정 및 연도 귀속의
            기준일은 원본파일의 결의일자"* 로 확정되었으므로, 이제는
            **확정된 값이 붙어 있는지**를 잠급니다. ⛔ 시험을 지우지 않고
            기대값을 확정 규칙으로 바꿨습니다.
        """
        assert Settings().PURCHASE_PERIOD_DATE_FIELD == "resolution_date"
        # ⛔ 신고기준일은 기간 축에 넣지 않았다.
        assert "issue_date" not in ALLOWED_DATE_FIELDS

    def test_a_blank_budget_account_is_not_excluded(self) -> None:
        """Q5-9 — 공란은 실적 제외 규칙에 걸리지 않는다."""
        assert is_excluded_budget_account(None) is False
        assert is_excluded_budget_account("") is False
        assert is_excluded_budget_account("   ") is False

    def test_the_six_accounts_are_still_exact_matches(self) -> None:
        """Q71-B — 6종은 정확히 같은 값만."""
        assert is_excluded_budget_account("교육훈련비") is True
        assert is_excluded_budget_account("교육훈련비지원") is False


# ======================================================================
# 4. 고객 질문 문서 — 중복을 만들지 않았는가
# ======================================================================
class TestTheCustomerQuestions:
    """⛔ 이미 있는 질문을 새로 만들지 않는다."""

    def test_the_existing_questions_are_untouched(self, questions: str) -> None:
        """W-1-2 · Q5-8 · Q5-9 는 **이미** 질문 자리가 있다."""
        assert "## Q-A. 인증 유효기간을 어떤 날짜로 판정합니까 (W-1-2)" in questions
        assert "## Q5-8." in questions
        assert "## Q5-9." in questions

    @pytest.mark.parametrize("item", ["Q71-A", "Q71-B", "Q71-C", "Q71-D"])
    def test_the_step71_items_now_have_a_question(self, questions: str, item: str) -> None:
        assert f"## {item}." in questions

    def test_the_name_collision_is_flagged(self, questions: str, impact: str) -> None:
        """⚠️ `Q-A` 가 둘이다 — 고객이 이미 받아 본 이름은 바꾸지 않았다."""
        assert "Q-A` ~ `Q-E` 와 번호가 겹치지 않도록" in questions
        assert "`Q-A` 가 둘이다" in impact

    def test_the_questions_are_written_for_a_person(self, questions: str) -> None:
        """⛔ 고객 질문에 내부 용어를 쓰지 않는다."""
        section = _section(questions, "# Q71.")
        assert section
        for jargon in ("API", "SQL", "Calculator", "스키마", "리포지토리", "find_for_"):
            assert jargon not in section

    def test_the_step71_questions_stay_open(self, questions: str) -> None:
        section = _section(questions, "# Q71.")
        assert section.count("🔴") == 4


class TestDecisionsStillCallThemUnconfirmed:
    """⛔ 결정 기록에서도 확정으로 바뀌어 있으면 안 된다."""

    @pytest.mark.parametrize("item", ["W-1-2", "Q5-8"])
    def test_the_item_is_recorded_as_open(self, decisions: str, item: str) -> None:
        assert f"**{item}**" in decisions
        assert "🔴 미확정" in decisions

    def test_the_design_judgements_are_still_separated(self, decisions: str) -> None:
        """STEP 71 에서 갈라 둔 §0.10.8 이 그대로 있는가."""
        assert "### 0.10.8 🟡" in decisions
        assert "고객 확정이 아닌" in decisions
