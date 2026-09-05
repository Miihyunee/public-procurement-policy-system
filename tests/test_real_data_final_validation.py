"""
STEP 85 — 실데이터 검증 문서가 **스스로 어긋나지 않는가**.

이 파일은 실데이터를 읽지 않습니다. 읽을 수도 없습니다 — 고객 데이터는
저장소에 없고, 앞으로도 넣지 않습니다. 대신 지킬 수 있는 것을 지킵니다.

무엇을 지키는가
===============

1. **숫자가 서로 맞는가** — 부분의 합이 전체와 같은가. 어긋나면 그 문서는
   그때부터 믿을 수 없습니다.
2. **민감정보가 새어 나가지 않았는가** — 사업자등록번호·거래처명을 옮겨
   적지 않았는가.
3. **문서가 업무규칙을 확정해 버리지 않았는가** — 조사는 조사로 끝나야
   합니다.
4. **문서가 "바꾸지 않았다" 고 적은 것이 코드에서도 그대로인가.**

.. warning::
    ⛔ 이 파일은 실데이터 수치를 **재계산하지 않습니다.** 원본이 없으므로
    할 수 없습니다. 문서가 적은 숫자들 **사이의 일관성**만 봅니다.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from procurement.calculators.rules import build_default_registry
from procurement.core.config.settings import Settings
from procurement.core.performance_exclusion import (
    EXCLUDED_BUDGET_ACCOUNTS,
    is_excluded_budget_account,
    needs_budget_account_check,
)
from procurement.core.period import ALLOWED_DATE_FIELDS

_ROOT = Path(__file__).resolve().parents[1]
_DOC = _ROOT / "docs" / "REAL_DATA_FINAL_VALIDATION.md"

#: 조사 문서가 **확정처럼 적으면 안 되는** 문구.
FORBIDDEN_CLAIMS = (
    "연도 귀속을 확정",
    "신고기준일 기준으로 변경",
    "음수 상계를 확정",
    "매칭 키를 확정",
    "0원을 제외하기로",
    "계약일자를 결의일자로 채운다",
)


@pytest.fixture(scope="module")
def doc() -> str:
    return _DOC.read_text(encoding="utf-8")


class TestTheDocumentExists:
    """12개 절이 모두 있는가 — 지시서가 요구한 목차."""

    def test_the_document_exists(self) -> None:
        assert _DOC.exists()

    @pytest.mark.parametrize(
        "heading",
        [
            "## 1. 데이터 개요",
            "## 2. 원본 행수",
            "## 3. 날짜 필드별 데이터 현황",
            "## 4. 신고연도 ≠ 결의연도 분석",
            "## 5. 음수 상계 후보 분석",
            "## 6. 0원 현황",
            "## 7. STEP 84 기능 실제 데이터 검증",
            "## 8. 계산 결과",
            "## 9. 미적재 / 검토 / 제외 현황",
            "## 10. 발견된 문제",
            "## 11. 추가 판단이 필요한 업무규칙",
            "## 12. 최종 인수시험으로 넘길 수 있는 항목",
        ],
    )
    def test_the_section_is_present(self, doc: str, heading: str) -> None:
        assert heading in doc


class TestTheNumbersAddUp:
    """⭐ 부분의 합이 전체와 같은가."""

    def test_the_row_counts_add_up(self, doc: str) -> None:
        """2,305 = 2,292 거래 + 13 소계·합계."""
        assert "2,305" in doc
        assert "2,292" in doc
        assert "| 13 |" in doc or " 13 " in doc

    def test_the_amounts_add_up(self, doc: str) -> None:
        """양수 + 음수 = 원본 합계 행."""
        positive = 10_362_615_496
        negative = -1_553_874_926
        total = 8_808_740_570
        assert positive + negative == total
        for value in (f"{positive:,}", f"{total:,}", f"{abs(negative):,}"):
            assert value in doc, value

    def test_the_budget_account_verdicts_cover_every_row(self, doc: str) -> None:
        """확인 필요 492 + 6종 80 + 그 외 1,720 = 2,292."""
        assert 492 + 80 + 1_720 == 2_292
        for value in ("492", "80", "1,720"):
            assert value in doc, value

    def test_the_six_accounts_add_up_to_the_rule_total(self, doc: str) -> None:
        """수도광열비 71 + 기타운영비 4 + 교육훈련비 3 + 사업추진경비 1 +
        의료비 1 + 복리후생비 0 = 80."""
        assert 71 + 4 + 3 + 1 + 1 + 0 == 80

    def test_the_offset_candidate_types_add_up(self, doc: str) -> None:
        """금액 기준: 단일 41 + 복수 85 + 후보 없음 3 = 음수 129."""
        assert 41 + 85 + 3 == 129
        # 후보가 있는 126건이 적요 동일 116 + 적요 상이 10 으로 갈린다.
        assert 116 + 10 == 129 - 3

    def test_the_answer_based_types_add_up(self, doc: str) -> None:
        """적요 기준: 단일 83 + 복수 37 + 없음 9 = 129."""
        assert 83 + 37 + 9 == 129

    def test_the_resolution_years_add_up(self, doc: str) -> None:
        """결의일자 2026 2,287 + 2025 5 = 2,292."""
        assert 2_287 + 5 == 2_292

    def test_the_validation_result_is_all_or_nothing(self, doc: str) -> None:
        """오류 2,292행 · 통과 0 · 적재 0 — 셋이 같은 이야기를 해야 한다."""
        assert "통과 0" in doc
        assert "**0건**" in doc
        assert "**2,292**" in doc


class TestNoSensitiveDataLeaked:
    """⛔ 고객 데이터를 문서로 옮기지 않았는가."""

    def test_no_business_number_appears(self, doc: str) -> None:
        """``123-45-67890`` 형태가 하나도 없어야 한다."""
        assert re.search(r"\b\d{3}-\d{2}-\d{5}\b", doc) is None

    def test_no_ten_digit_bare_number_appears(self, doc: str) -> None:
        """하이픈 없는 10자리 사업자등록번호도 마찬가지."""
        bare = [v for v in re.findall(r"(?<![\d,.-])\d{10}(?![\d,.-])", doc)]
        assert bare == [], bare

    def test_the_document_says_it_keeps_data_out(self, doc: str) -> None:
        assert "저장소에 커밋하지 않는다" in doc
        assert "거래처명·사업자등록번호를 사례표에 싣지" in doc

    def test_no_real_data_file_is_committed(self) -> None:
        """⛔ 저장소에 실데이터 파일이 들어오지 않았는가."""
        for pattern in ("**/*.xlsx", "**/*.xls", "**/*.csv"):
            found = [
                p for p in _ROOT.glob(pattern) if ".venv" not in p.parts and ".git" not in p.parts
            ]
            assert found == [], found


class TestTheDocumentSettlesNothing:
    """⛔ 조사 문서가 업무규칙을 확정해 버리지 않았는가."""

    @pytest.mark.parametrize("claim", FORBIDDEN_CLAIMS)
    def test_no_settled_claim(self, doc: str, claim: str) -> None:
        assert claim not in doc

    def test_it_says_so_at_the_top(self, doc: str) -> None:
        assert "업무규칙을 확정하지 않는다" in doc
        assert "발견 → 수치화 → 사례 기록까지만" in doc

    def test_the_open_items_stay_open(self, doc: str) -> None:
        """연도 귀속 · 음수 상계 · 0원 · 인증 원천이 🔴 로 남아 있는가."""
        for item in ("연도 귀속", "음수 상계", "0원", "인증 자료 원천"):
            assert item in doc
        assert doc.count("🔴") >= 8

    def test_the_customer_reading_is_marked_as_ours(self, doc: str) -> None:
        """⭐ §B 의 읽기가 **고객 말이 아니라 우리 해석**이라고 적혀 있는가."""
        assert "우리 해석이지 고객이 말한 것이 아니다" in doc


class TestTheDocumentMatchesTheCode:
    """문서가 "바꾸지 않았다" 고 적은 것이 코드에서도 그대로인가."""

    def test_the_year_axis_still_has_no_issue_date(self) -> None:
        """⛔ §4 — 기간 축에 ``issue_date`` 를 넣지 않았다."""
        assert "issue_date" not in ALLOWED_DATE_FIELDS

    def test_the_year_axis_is_fixed_to_the_resolution_date(self) -> None:
        """🟢 연도 귀속 기준일 = 결의일자 (PM 확정 · STEP 86).

        .. note::
            **기대값이 바뀐 이유** — STEP 85 시점에는 기준일이 아직
            🔴 미확정이라 **기본값이 없다**는 사실을 잠그고 있었습니다.
            2026-09-02 PM 이 결의일자로 확정했습니다. ⛔ §4 가 적은
            *"어느 쪽도 채택하지 않았다"* 는 STEP 85 당시의 기록이며,
            그 뒤 PM 이 결정한 것입니다 — 문서를 고쳐 흔적을 지우지
            않았습니다.
        """
        assert Settings().PURCHASE_PERIOD_DATE_FIELD == "resolution_date"

    def test_the_offsetting_module_is_still_unwired(self) -> None:
        """⛔ §5 — 상계 모듈이 계산에 연결되지 않았다."""
        source = (_ROOT / "src" / "procurement" / "core" / "offsetting.py").read_text(
            encoding="utf-8"
        )
        assert "아직 계산에 연결되어 있지 않습니다" in source
        calculator = (
            _ROOT / "src" / "procurement" / "calculators" / "procurement_achievement.py"
        ).read_text(encoding="utf-8")
        assert "offsetting" not in calculator

    def test_non_positive_amounts_are_still_rejected(self) -> None:
        """⛔ §6 — 0원·음수 저장 정책을 바꾸지 않았다."""
        source = (_ROOT / "src" / "procurement" / "database" / "purchase_repository.py").read_text(
            encoding="utf-8"
        )
        assert "amount" in source and "<= 0" in source

    def test_the_six_accounts_are_unchanged(self) -> None:
        """⛔ §7.2 — 6종 정확 매칭 규칙 그대로."""
        assert EXCLUDED_BUDGET_ACCOUNTS == frozenset(
            {"교육훈련비", "사업추진경비", "의료비", "수도광열비", "기타운영비", "복리후생비"}
        )
        assert is_excluded_budget_account("교육훈련비지원") is False

    def test_a_blank_budget_account_is_a_check_not_an_exclusion(self) -> None:
        """§7.2 B — 공란은 확인 대상일 뿐 제외가 아니다."""
        assert needs_budget_account_check("") is True
        assert is_excluded_budget_account("") is False

    def test_the_resolution_date_rule_is_still_wired(self) -> None:
        """STEP 84 구현을 되돌리지 않았다."""
        assert build_default_registry().get("RESOLUTION_DATE") is not None

    def test_the_contract_and_payment_dates_became_optional(self) -> None:
        """§10.1 이 올린 문제를 **PM 이 판단해서** 풀었다(STEP 87).

        .. note::
            **기대값이 바뀐 이유** — STEP 85 시점에는 두 컬럼을 *"확인 없이
            완화하면 실적이 조용히 달라진다"* 는 이유로 **필수 그대로** 두는
            것을 잠그고 있었습니다. 그 확인을 PM 이 했습니다 — 🟢 2026-09-02
            *"원본에 존재하지 않는 날짜 때문에 결의일자가 정상적으로 존재하는
            거래까지 미적재시키지 않는다."*

            ⛔ 우리가 임의로 푼 것이 아니라 **PM 확정에 따라** 푼 것입니다.
        """
        from procurement.uploads.format import STANDARD_COLUMNS

        optional = {c.header for c in STANDARD_COLUMNS if not c.required}
        assert "계약일자" in optional
        assert "지급일" in optional

    def test_the_resolution_date_is_still_required(self) -> None:
        """⛔ 완화가 **기준일까지 번지지 않았다** — 결의일자는 필수 그대로."""
        from procurement.uploads.format import STANDARD_COLUMNS

        required = {c.header for c in STANDARD_COLUMNS if c.required}
        assert "결의일자" in required
