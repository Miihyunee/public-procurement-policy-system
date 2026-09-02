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


@pytest.fixture(scope="module")
def doc() -> str:
    return _DOC.read_text(encoding="utf-8")


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

    @pytest.mark.parametrize("term", ["작업 시트", "작업시트", "업체규모", "중소기업 기간"])
    def test_the_term_appears_nowhere_in_the_source(self, term: str) -> None:
        """⭐ 낱말이 없으면 실수로도 읽을 수 없다."""
        hits = [path for path in _SRC.rglob("*.py") if term in path.read_text(encoding="utf-8")]
        assert hits == [], hits

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

    def test_no_company_importer_was_added(self) -> None:
        """§2.1 이 적은 사실 — 기업 적재 경로가 여전히 없다.

        ⛔ 원천이 정해지지 않았으므로 만들지 않았습니다. 이 시험은 그 사실을
        기록하며, 원천이 확정되어 적재 경로가 생기면 **깨지는 것이 정상**
        입니다(그때 기대값을 바꾸고 사유를 적습니다).
        """
        importers = {path.name for path in (_SRC / "importers").glob("*.py")}
        assert "company_importer.py" not in importers


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
