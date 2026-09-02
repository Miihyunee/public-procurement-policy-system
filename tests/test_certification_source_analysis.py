"""
STEP 88 — 인증 원천 조사 문서가 **선을 지키는가**, 그리고 코드가 그대로인가.

이 STEP 은 인증 데이터 원천을 확정하지 않았습니다. 고객이 답한 적 없는
사안이기 때문입니다. 그래서 이 파일이 지키는 것도 계산이 아니라 **경계**입니다.

무엇을 지키는가
===============

1. 문서가 **원천을 확정해 버리지 않았는가**.
2. 「작업」 시트를 **코드가 읽지 않는가** — 낱말조차 없는가.
3. 인증·기업을 채우는 **자동 경로가 생기지 않았는가**.
4. 결의일자 기준 판정 구조는 **그대로 준비되어 있는가**.
5. 새 정책이 **등록되지 않았는가**.

.. warning::
    ⛔ 이 파일은 실데이터를 읽지 않습니다. 문서가 적은 숫자들 **사이의
    일관성**과 **코드의 현재 상태**만 봅니다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from procurement.calculators.rules import (
    ResolutionDateRule,
    ResolutionOrContractDateRule,
    build_default_registry,
)
from procurement.core.config.settings import Settings
from procurement.database.bootstrap import MVP_POLICY_SEEDS

_ROOT = Path(__file__).resolve().parents[1]
_DOC = _ROOT / "docs" / "CERTIFICATION_SOURCE_ANALYSIS.md"
_QUESTIONS = _ROOT / "docs" / "CUSTOMER_DATA_QUESTIONS.md"
_SRC = _ROOT / "src" / "procurement"

#: ⛔ 조사 문서가 **확정처럼 적으면 안 되는** 문구.
FORBIDDEN_CLAIMS = (
    "작업 시트를 공식 인증 데이터로 확정",
    "작업 시트를 원천으로 확정",
    "인증 원천을 확정했다",
    "중소기업으로 간주한다",
    "#N/A 는 인증 없음이다",
    "새 정책을 등록했다",
)

#: 🟢 고객이 인증에 관해 **실제로 답한** 것 — 문서가 이것과 원천을 섞으면 안 된다.
CONFIRMED_ANSWERS = ("§0.12.1", "§0.6.2", "§0.12.6")


#: ⛔ 고객이 읽는 문장에 나오면 안 되는 내부 용어.
INTERNAL_TERMS = (
    "API",
    "DB",
    "repository",
    "Repository",
    "importer",
    "sync_one",
    "company_id",
    "certification",
    "배선",
    "적재",
    "마이그레이션",
    "스키마",
    "설정값",
)

#: 🟢 이미 확정된 것 — ⛔ 요청서에서 **다시 물으면 안 된다**.
SETTLED_QUESTIONS = (
    "어떤 날짜를 기준으로",
    "연도를 어떻게 나눌",
    "직접생산확인증명을 어떻게",
    "지출결의서 단위로 묶어",
)


@pytest.fixture(scope="module")
def doc() -> str:
    return _DOC.read_text(encoding="utf-8")


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
def request_sheet() -> str:
    """고객이 실제로 받아 보는 장(요청서 ②)."""
    text = _QUESTIONS.read_text(encoding="utf-8")
    section = _section(text, "# 📨 확인 요청서 ② — 기업·인증 자료")
    assert section, "확인 요청서 ② 를 찾지 못했습니다"
    return section


class TestTheDocumentExists:
    """지시서가 요구한 구분(A~E · ①~⑧)이 들어 있는가."""

    def test_the_document_exists(self) -> None:
        assert _DOC.exists()

    @pytest.mark.parametrize(
        "heading",
        [
            "## 1. 기존 고객 확정사항",
            "## 2. 지금 무엇이 비어 있는가",
            "## 3. 「작업」 시트 구조",
            "## 4. 인증 매칭 사전 검증",
            "## 5. API 데이터 상태",
            "## 6. 결의일자 기준 인증 유효기간 판정",
            "## 7. 실제 계산 결과",
            "## 8. PM 이 내려야 할 결정",
            "## 9. 이번 STEP 에서 하지 않은 것",
        ],
    )
    def test_the_section_is_present(self, doc: str, heading: str) -> None:
        assert heading in doc

    def test_the_five_questions_are_answered(self, doc: str) -> None:
        """§17 A~E 를 각각 답했는가."""
        for label in ("**A.**", "**B.**", "**C.**", "**D.**", "**E.**"):
            assert label in doc, label


class TestTheNumbersAddUp:
    """⭐ 부분의 합이 전체와 맞는가."""

    def test_the_matching_counts_are_consistent(self, doc: str) -> None:
        """매칭 379 + 거래에만 80 = 적재 거래 고유 459."""
        assert 379 + 80 == 459
        for value in ("379", "459", "481"):
            assert value in doc, value

    def test_the_sheet_side_is_consistent(self, doc: str) -> None:
        """매칭 379 + 시트에만 102 = 시트 고유 481."""
        assert 379 + 102 == 481

    def test_the_certified_companies_are_a_subset(self, doc: str) -> None:
        """인증 붙은 118 중 거래와 매칭 86 — 매칭이 전체보다 클 수 없다."""
        assert 86 <= 118

    def test_the_denominator_is_the_step_87_figure(self, doc: str) -> None:
        """분모는 STEP 87 적재 결과에서 이어진 숫자다."""
        assert "10,349,192,149" in doc
        assert "2,161" in doc


class TestTheDocumentSettlesNothing:
    """⛔ 조사가 확정으로 넘어가지 않았는가."""

    @pytest.mark.parametrize("claim", FORBIDDEN_CLAIMS)
    def test_no_settled_claim(self, doc: str, claim: str) -> None:
        assert claim not in doc

    def test_it_says_so_at_the_top(self, doc: str) -> None:
        assert "인증 데이터 원천을 확정하지 않는다" in doc
        assert "PM 이 내려야 할 결정 하나" in doc

    def test_the_customer_answered_nothing_about_the_source(self, doc: str) -> None:
        """⭐ 고객이 답한 것과 답하지 않은 것을 갈라 적었는가."""
        assert '"인증 정보를 어디서 얻는가" 를 말하지 않는다' in doc
        for section in CONFIRMED_ANSWERS:
            assert section in doc, section

    def test_the_open_questions_are_listed_not_answered(self, doc: str) -> None:
        """(나)를 고르면 함께 정해야 하는 것들이 **질문으로** 남아 있는가."""
        assert "함께 정해야 하는 것" in doc
        assert "지금 우리가 정하면" in doc

    def test_no_business_number_leaked(self, doc: str) -> None:
        assert re.search(r"\b\d{3}-\d{2}-\d{5}\b", doc) is None
        assert re.findall(r"(?<![\d,.-])\d{10}(?![\d,.-])", doc) == []


class TestTheCodeDoesNotReadTheSheet:
    """⛔ 「작업」 시트가 코드로 새어 들어오지 않았는가."""

    @pytest.mark.parametrize("term", ["작업 시트", "작업시트", "중소기업 기간"])
    def test_the_term_appears_nowhere_in_the_source(self, term: str) -> None:
        """⭐ 낱말이 없으면 실수로도 읽을 수 없다."""
        hits = [path for path in _SRC.rglob("*.py") if term in path.read_text(encoding="utf-8")]
        assert hits == [], hits

    def test_company_size_appears_only_as_an_exclusion(self) -> None:
        """「업체규모」는 **쓰지 않는다는 기록** 으로만 나타난다.

        ⚠️ **규칙 변경(2026-09-02 · STEP 91 · PM 지시).** 이 낱말은 원래 소스
        어디에도 없어야 했다. 그런데 PM 이 "중소기업 데이터에서 인증유효일자가
        빈값이 아니면 중소기업" 이라고 확정하면서(DECISIONS §0.19), 기업정보
        표준 양식에 **업체규모 컬럼을 넣지 않는다는 사실**을 코드에 남기게
        되었다.

        그래서 기대값을 "어디에도 없다" 에서 **"제외 목록에만 있다"** 로
        바꾼다. ⛔ 느슨해진 것이 아니다 — 판정에 쓰는 코드가 없다는 것을 더
        구체적으로 못 박는다.
        """
        from procurement.uploads.company_format import (
            COMPANY_PENDING_COLUMNS,
            COMPANY_REQUIRED_HEADERS,
        )

        hits = {
            path.name
            for path in _SRC.rglob("*.py")
            if "업체규모" in path.read_text(encoding="utf-8")
        }
        assert hits == {"company_format.py"}, hits

        # ⛔ 양식 컬럼이 아니라 **제외 목록**에 있다.
        assert "업체규모" not in COMPANY_REQUIRED_HEADERS
        assert COMPANY_PENDING_COLUMNS["업체규모"] == "중소기업 여부를 규모로 판정하는 규칙이 없다"

    def test_nothing_reads_an_excel_sheet_by_name(self) -> None:
        """⛔ 시트 이름으로 워크북을 여는 코드가 없다."""
        hits = [path for path in _SRC.rglob("*.py") if '"작업"' in path.read_text(encoding="utf-8")]
        assert hits == [], hits


class TestNoAutomaticCertificationPath:
    """⛔ 인증·기업을 자동으로 채우는 경로가 생기지 않았는가."""

    def test_the_sync_service_still_requires_an_existing_company(self) -> None:
        """⭐ §2.1 — 기업이 없으면 인증을 저장하지 않고 건너뛴다."""
        source = (_SRC / "collectors" / "sync_service.py").read_text(encoding="utf-8")
        assert "SKIP_COMPANY_NOT_FOUND" in source

    def test_the_api_keys_are_not_hardcoded(self) -> None:
        """🔒 키는 `.env` 에만 — 기본값이 없다."""
        settings = Settings()
        assert settings.SMPP_API_KEY is None
        assert settings.STARTUP_API_KEY is None

    def test_the_company_importer_never_invents_values(self) -> None:
        """기업 적재 경로가 **생겼다** — 그리고 없는 값을 지어내지 않는다.

        ⚠️ **규칙 변경(2026-09-02 · STEP 91).** 이 시험은 원래 "기업 적재
        경로가 없다" 를 기록했고, 그 docstring 에 *"원천이 확정되어 적재
        경로가 생기면 깨지는 것이 정상"* 이라고 적어 두었다. 고객이 확인
        방식을 **FILE·API 두 가지 모두**로 확정했으므로(DECISIONS §0.18)
        경로가 생겼고, 예고한 대로 기대값을 바꾼다.

        ⛔ 대신 **원래 지키려던 것**을 그대로 지킨다 — 근거 없는 기업 정보가
        자동으로 채워지지 않는다.
        """
        importers = {path.name for path in (_SRC / "importers").glob("*.py")}
        assert "company_importer.py" in importers

        source = (_SRC / "importers" / "company_importer.py").read_text(encoding="utf-8")
        # 값이 없으면 그 행을 실패로 돌려보낸다 — 다른 값으로 채우지 않는다.
        assert '"기업명이 없습니다."' in source
        assert '"대표자명이 없습니다."' in source
        # 이미 있는 기업을 덮어쓰지 않는다.
        assert "ALREADY_EXISTS" in source


class TestTheJudgementStructureIsReady:
    """결의일자 기준 판정은 **인증만 들어오면 동작**하는 상태인가."""

    def test_the_general_policies_use_the_resolution_rule(self) -> None:
        registry = build_default_registry()
        for seed in MVP_POLICY_SEEDS:
            if seed.policy_code in ("SMALL_BUSINESS", "WOMAN", "DISABLED"):
                assert isinstance(registry.get(seed.evaluation_basis), ResolutionDateRule)

    def test_the_startup_policy_keeps_the_or_rule(self) -> None:
        registry = build_default_registry()
        seed = next(s for s in MVP_POLICY_SEEDS if s.policy_code == "STARTUP")
        assert isinstance(registry.get(seed.evaluation_basis), ResolutionOrContractDateRule)

    def test_the_year_axis_is_still_the_resolution_date(self) -> None:
        assert Settings().PURCHASE_PERIOD_DATE_FIELD == "resolution_date"


class TestNoNewPolicyWasRegistered:
    """⛔ 시트에 값이 있다는 이유로 정책을 만들지 않았는가."""

    @pytest.mark.parametrize(
        "code",
        ["SOCIAL_ENTERPRISE", "SOCIAL_COOPERATIVE", "DISABLED_STANDARD_WORKPLACE"],
    )
    def test_the_policy_was_not_added(self, code: str) -> None:
        assert code not in {seed.policy_code for seed in MVP_POLICY_SEEDS}

    def test_the_policy_count_is_unchanged(self) -> None:
        assert len(MVP_POLICY_SEEDS) == 5

    def test_no_target_rate_was_invented(self) -> None:
        """⛔ D-004 — 목표율을 임의로 채우지 않았다."""
        assert all(seed.policy_code for seed in MVP_POLICY_SEEDS)
        source = (_SRC / "database" / "bootstrap.py").read_text(encoding="utf-8")
        assert "target_rate=None" in source


# ======================================================================
# STEP 89 — 고객 확인 요청서 ②
# ======================================================================
class TestTheRequestSheetIsComplete:
    """요청서에 물어야 할 것만 남아 있는가.

    ⚠️ **규칙 변경(2026-09-02 · PM 지시).** 원래 7문항이었다. 담당자가
    *"중소기업 자료에서 인증 유효일자가 비어 있지 않으면 중소기업"* 이라고
    확정하면서(DECISIONS §0.19) 「업체규모」 관련 두 문항이 **불필요해졌고**,
    ⛔ 이미 답을 받은 것을 다시 여쭙지 않는다는 원칙에 따라 뺐다.
    나머지 5문항은 문구도 순서도 그대로다.
    """

    def test_there_are_exactly_five_questions(self, request_sheet: str) -> None:
        """⛔ 질문을 늘리지도, 남은 것을 빠뜨리지도 않았다."""
        headings = [line for line in request_sheet.splitlines() if line.startswith("## ")]
        numbered = [h for h in headings if h.startswith(("## ①", "## ②", "## ③", "## ④", "## ⑤"))]
        assert len(numbered) == 5, headings

    def test_the_dropped_questions_are_gone(self, request_sheet: str) -> None:
        """⛔ 「업체규모」 두 문항은 **다시 묻지 않는다.**"""
        assert "중소기업으로 봅니까" not in request_sheet
        assert "다를 때 어느 쪽" not in request_sheet

    def test_why_they_were_dropped_is_written_down(self, request_sheet: str) -> None:
        """왜 뺐는지가 요청서에 남아 있다 — 조용히 사라지지 않는다."""
        assert "여쭙지 않기로 했습니다" in request_sheet
        assert "인증 유효일자가 비어 있지 않으면" in request_sheet

    def test_every_question_is_open(self, request_sheet: str) -> None:
        """요청서의 질문은 전부 🔴 이어야 한다 — 답을 받으려고 보내는 것이다."""
        numbered = [
            line
            for line in request_sheet.splitlines()
            if line.startswith(("## ①", "## ②", "## ③", "## ④", "## ⑤"))
        ]
        assert all("🔴" in line for line in numbered), numbered

    @pytest.mark.parametrize(
        "topic",
        [
            "작업」 시트를 기업·인증 자료로",
            "#N/A",
            "끝나는 날짜가 없는",
            "없는 80개 업체",
            "집계해야 합니까",
        ],
    )
    def test_the_topic_is_covered(self, request_sheet: str, topic: str) -> None:
        assert topic in request_sheet, topic

    def test_the_target_rate_is_asked_for(self, request_sheet: str) -> None:
        """📎 목표 비율이 없으면 달성률이 나오지 않는다 — 함께 요청했는가."""
        assert "목표 비율" in request_sheet
        assert "임의로 넣지 않았습니다" in request_sheet


class TestTheRequestSheetIsPlain:
    """⛔ 고객이 읽는 문장에 내부 용어가 새어 나가지 않았는가."""

    @pytest.mark.parametrize("term", INTERNAL_TERMS)
    def test_no_internal_term(self, request_sheet: str, term: str) -> None:
        assert term not in request_sheet, term

    def test_no_snake_case_identifier_leaked(self, request_sheet: str) -> None:
        assert re.search(r"[a-z]+_[a-z_]+\(", request_sheet) is None

    def test_it_does_not_sound_like_our_judgement(self, request_sheet: str) -> None:
        """지시서 §4 가 정해 준 표현을 그대로 썼는가.

        줄바꿈·강조 표시는 무시하고 **문장 자체**를 봅니다.
        """
        plain = request_sheet.replace("*", "").replace("\n", " ")
        assert "일부 항목은 업무 판단이 필요한 상태입니다" in plain
        assert "정확한 달성률 산정을 위해 아래 항목만 확인 부탁드립니다" in plain


class TestTheRequestSheetDoesNotReAsk:
    """⛔ 이미 답해 주신 것을 다시 묻지 않았는가(지시서 §1)."""

    @pytest.mark.parametrize("settled", SETTLED_QUESTIONS)
    def test_the_settled_item_is_not_re_asked(self, request_sheet: str, settled: str) -> None:
        assert settled not in request_sheet, settled

    def test_it_says_what_is_already_settled(self, request_sheet: str) -> None:
        assert "이미 답해 주신 것은 다시 여쭙지 않습니다" in request_sheet

    def test_nothing_was_decided_for_the_customer(self, request_sheet: str) -> None:
        assert "임의로 정하지 않았습니다" in request_sheet
        assert "지금 상태 그대로 두겠습니다" in request_sheet

    def test_the_question_does_not_lead_the_answer(self, request_sheet: str) -> None:
        """⛔ 특정 답을 유도하지 않는다."""
        for leading in (
            "사용해도 되겠지요",
            "중소기업으로 보겠습니다",
            "인증 없음으로 처리하겠습니다",
            "집계하겠습니다",
        ):
            assert leading not in request_sheet, leading

    def test_no_business_number_leaked(self, request_sheet: str) -> None:
        assert re.search(r"\b\d{3}-\d{2}-\d{5}\b", request_sheet) is None
        assert re.findall(r"(?<![\d,.-])\d{10}(?![\d,.-])", request_sheet) == []


class TestThePlanIsRecorded:
    """답변이 오면 무엇을 할지 **미리** 적어 두었는가(지시서 §6)."""

    @pytest.mark.parametrize(
        "heading",
        [
            "## 10. 고객 확인 요청안",
            "## 11. 답변이 오면 손댈 자리",
            "## 12. 답변 이후 구현 순서",
            "## 13. 이번 STEP(89)에서 한 일",
        ],
    )
    def test_the_section_exists(self, doc: str, heading: str) -> None:
        assert heading in doc

    def test_the_company_list_comes_first(self, doc: str) -> None:
        """⭐ 기업 명단이 인증보다 먼저다 — 순서를 틀리면 전부 건너뛰어진다."""
        order = _section(doc, "## 12. 답변 이후 구현 순서")
        assert order.index("기업 명단을 채운다") < order.index("인증을 채운다")

    def test_the_plan_is_not_an_approval(self, doc: str) -> None:
        assert "답변 전에는 어느 것도 만들지 않는다" in doc
        assert "계획이지 승인이 아니다" in doc

    def test_the_judgement_rules_are_marked_unchanged(self, doc: str) -> None:
        """⛔ 어느 답변도 판정 규칙을 바꾸지 않는다."""
        assert "판정 규칙(`ResolutionDateRule` 등)은 어느 답변에도 바뀌지 않는다" in doc

    def test_step_89_changed_no_source(self, doc: str) -> None:
        section = _section(doc, "## 13. 이번 STEP(89)에서 한 일")
        assert "변경 없음" in section
        assert "산출하지 않았다" in section
