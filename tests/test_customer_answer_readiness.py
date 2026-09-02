"""
STEP 78 — 답변이 오면 **안전하게 반영할 수 있는 상태인가**.

앞선 시험들이 각 문서를 하나씩 지킨다면, 이 파일은 그 사이를 지킵니다.

무엇을 지키는가
===============

1. **답변 하나가 다른 규칙을 자동 확정하지 않는다** — 4가지 원칙(대응표 §5).
2. **문서의 역할이 섞이지 않는다** — 질문 · 영향도 · 대응표 · 결정 기록.
3. **대응표가 말하는 현재 동작이 실제 코드와 같다** — 특히 "결의일자만 보는
   규칙이 없다" 처럼 **없다는 사실**.
4. **실데이터가 없다는 사실이 유지된다** — 합성 숫자가 현황으로 둔갑하지
   않도록.

.. warning::
    ⛔ 이번 STEP 은 구현 STEP 이 아닙니다. 여기서 잠그는 것은 **아직 아무것도
    정해지지 않았다**는 상태 자체입니다.

.. note::
    이 파일 자신의 설명문에 금지 낱말이 들어 있으므로, 검사 대상은 **문서
    본문과 코드**뿐입니다.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from procurement.calculators.rules import date_rules
from procurement.calculators.rules.registry import build_default_registry
from procurement.core.config.settings import Settings
from procurement.core.period import ALLOWED_DATE_FIELDS

_ROOT = Path(__file__).resolve().parents[1]
_DOCS = _ROOT / "docs"

_MAP = _DOCS / "CUSTOMER_RULE_IMPLEMENTATION_MAP.md"
_IMPACT = _DOCS / "UNCONFIRMED_RULES_IMPACT.md"
_QUESTIONS = _DOCS / "CUSTOMER_DATA_QUESTIONS.md"
_DECISIONS = _DOCS / "DECISIONS.md"

#: 각 문서가 맡은 역할. ⛔ 섞이면 미확정이 확정처럼 읽힌다.
DOCUMENT_ROLES = {
    "CUSTOMER_DATA_QUESTIONS.md": "고객에게 물어볼 질문",
    "CUSTOMER_RULE_IMPLEMENTATION_MAP.md": "답변 후 내부 구현 영향 지도",
    "UNCONFIRMED_RULES_IMPACT.md": "미확정 업무규칙의 현재 영향",
    "DECISIONS.md": "실제 확정된 업무규칙",
    "OPERATIONS_CHECKLIST.md": "운영 검수 절차",
    "OPERATIONS_CHECK_RESULT.md": "실제 운영 검수 결과",
}


@pytest.fixture(scope="module")
def rule_map() -> str:
    return _MAP.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def impact() -> str:
    return _IMPACT.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def principles(rule_map: str) -> str:
    """대응표 §5 — 답변 하나가 다른 규칙을 확정하지 않게 하는 원칙들."""
    section = _section(rule_map, "## 5. 답변 하나가 다른 규칙을")
    assert section, "4가지 원칙 절을 찾지 못했습니다"
    return section


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
# 1. 답변 하나가 다른 규칙을 확정하지 않는다
# ======================================================================
class TestOneAnswerSettlesOneThing:
    """⭐ 이 STEP 이 실제로 더한 것."""

    @pytest.mark.parametrize("number", ["원칙 1", "원칙 2", "원칙 3", "원칙 4"])
    def test_each_principle_is_written(self, principles: str, number: str) -> None:
        assert number in principles

    def test_an_answer_settles_only_what_it_names(self, principles: str) -> None:
        """일반 3정책에 답해도 연도 귀속·창업기업·조회 시점은 그대로다."""
        assert "답변에 적힌 범위만 확정한다" in principles
        for untouched in ("연도 귀속 기준", "창업기업 기준", "인증서 조회 시점", "다른 정책 기준"):
            assert untouched in principles

    def test_the_separate_questions_are_named(self, principles: str) -> None:
        """어느 질문끼리 헷갈리기 쉬운지 적어 둔다."""
        for pair in ("W-1-2", "W-11", "Q5-8", "Q5-9", "Q71-B", "Q71-C", "Q71-D"):
            assert pair in principles

    def test_zero_amount_and_blank_account_are_not_the_same_question(self, principles: str) -> None:
        """⚠️ 둘 다 "빈 값" 처럼 보이지만 시스템에서 벌어지는 일이 다르다."""
        assert "저장 자체가 거부되고" in principles
        assert "저장되어 계산에 들어간다" in principles

    def test_no_change_means_no_change(self, principles: str) -> None:
        assert "코드 변경을 요구하지 않는 답이면 코드를 바꾸지 않는다" in principles
        assert '"이왕 손대는 김에" 를 붙이지 않는다' in principles

    def test_a_broken_test_is_diagnosed_before_it_is_touched(self, principles: str) -> None:
        assert "통과시키려고 assertion 을 지우지 않는다" in principles
        assert "`skip` 을 붙이지 않는다" in principles
        assert "구현 버그" in principles


# ======================================================================
# 2. 대응표가 말하는 현재 동작이 코드와 같은가
# ======================================================================
class TestTheMapMatchesTheCode:
    """⛔ 대응표가 틀리면 답변이 왔을 때 엉뚱한 곳을 고친다."""

    def test_the_resolution_only_rule_now_exists(self, rule_map: str) -> None:
        """⭐ 결의일자만 보는 규칙이 **생겼다** — 대응표도 그렇게 적혀 있는가.

        .. note::
            **기대값이 바뀐 이유** — 이 시험은 STEP 83 까지
            ``test_there_is_no_resolution_only_rule`` 이라는 이름으로
            *"``ResolutionDateRule`` 이 없다"* 는 **사실**을 잠그고 있었습니다.
            2026-08-31 고객 최종 회신(``DECISIONS.md`` §0.12.1)으로 일반 3개
            정책의 판정 기준일이 결의일자로 확정되어 STEP 84 에서 규칙을
            신설했으므로, **없다는 사실**이 **있다는 사실**로 바뀌었습니다.
            ⛔ 시험을 지우지 않고 기대값을 뒤집어 그대로 잠급니다.
        """
        rules = {
            name
            for name, value in vars(date_rules).items()
            if inspect.isclass(value) and name.endswith("Rule")
        }
        assert "PaymentDateRule" in rules
        assert "ContractDateRule" in rules
        assert "ResolutionOrContractDateRule" in rules
        assert "ResolutionDateRule" in rules
        assert "STEP 84 구현 완료" in rule_map

    def test_the_registry_offers_every_basis(self) -> None:
        registry = build_default_registry()
        for basis in (
            "PAYMENT_DATE",
            "CONTRACT_DATE",
            "RESOLUTION_DATE",
            "RESOLUTION_OR_CONTRACT_DATE",
        ):
            assert registry.get(basis) is not None

    def test_the_year_axis_allows_the_resolution_date(self) -> None:
        """⚠️ 축 ① 에는 결의일자가 **이미 있다** — 축 ② 와 드는 품이 다르다."""
        assert "resolution_date" in ALLOWED_DATE_FIELDS
        assert "payment_date" in ALLOWED_DATE_FIELDS
        assert "contract_date" in ALLOWED_DATE_FIELDS

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

    def test_the_readiness_note_says_so(self, rule_map: str) -> None:
        section = _section(rule_map, "## 6. 반영 준비 상태")
        assert section
        assert "결의일자 단독 판정 규칙" in section
        assert "축 ① 은 설정, 축 ② 는 코드다" in section
        # STEP 84 에서 실제로 만들었다 — 문서가 "없음" 에 머물러 있으면 안 된다.
        assert "`ResolutionDateRule`" in section

    def test_every_source_path_in_the_map_exists(self, rule_map: str) -> None:
        import re

        packages = "core|database|calculators|reviews|uploads|importers|models|collectors|dashboard"
        paths = set(re.findall(rf"`((?:{packages})/[a-z_/]+\.py)`", rule_map))
        assert len(paths) >= 15
        for path in sorted(paths):
            assert (_ROOT / "src" / "procurement" / path).exists(), path


# ======================================================================
# 3. 문서의 역할이 섞이지 않는다
# ======================================================================
class TestDocumentRolesStaySeparate:
    """질문 · 영향도 · 대응표 · 결정 기록은 서로 다른 것을 담는다."""

    @pytest.mark.parametrize("name", sorted(DOCUMENT_ROLES))
    def test_the_document_exists(self, name: str) -> None:
        assert (_DOCS / name).exists()

    def test_the_map_points_at_the_impact_document(self, rule_map: str) -> None:
        """겹쳐 적지 않고 가리킨다 — 두 곳에 적으면 한쪽만 낡는다."""
        assert "UNCONFIRMED_RULES_IMPACT.md" in rule_map
        assert "상세 추적은" in rule_map

    def test_the_map_stays_internal(self, rule_map: str) -> None:
        assert "고객 질문 문장에 옮기지" in rule_map

    def test_decisions_does_not_settle_the_open_items(self) -> None:
        """⛔ 결정 기록에 미확정이 확정처럼 들어가 있으면 안 된다."""
        decisions = _DECISIONS.read_text(encoding="utf-8")
        for claim in (
            "W-1-2 확정됨",
            "Q5-8 확정됨",
            "Q5-9 확정됨",
            "0원은 실적 제외",
            "음수는 실적 제외",
            "예산과목 공란은 제외",
        ):
            assert claim not in decisions

    def test_the_questions_document_holds_the_request_sheet(self) -> None:
        questions = _QUESTIONS.read_text(encoding="utf-8")
        assert "# 📨 확인 요청서" in questions

    def test_the_impact_document_still_separates_the_two_date_axes(self, impact: str) -> None:
        assert "두 개의 날짜 축을 섞지 않는다" in impact


# ======================================================================
# 4. 실데이터가 없다는 사실이 유지된다
# ======================================================================
class TestTheRealDatabaseIsStillAbsent:
    """⛔ 합성 숫자를 고객 데이터 현황으로 적지 않는다."""

    def test_the_operational_database_is_empty(self) -> None:
        path = _ROOT / "database" / "procurement.db"
        assert path.exists()
        assert path.stat().st_size == 0

    def test_no_data_file_is_committed(self) -> None:
        import subprocess

        listed = subprocess.run(
            ["git", "ls-files"], cwd=_ROOT, capture_output=True, text=True, check=True
        ).stdout.splitlines()
        assert [name for name in listed if name.endswith((".db", ".xlsx", ".xls", ".csv"))] == []

    def test_the_audit_still_says_it_could_not_measure(self) -> None:
        audit = (_DOCS / "BUSINESS_NO_DATA_AUDIT.md").read_text(encoding="utf-8")
        assert "실제 고객 DB 부재로" in audit
        assert "합성 데이터로 대신 조사하지 않았다" in audit

    def test_the_readiness_note_repeats_it(self, rule_map: str) -> None:
        section = _section(rule_map, "## 6. 반영 준비 상태")
        assert "0 bytes" in section
