"""
STEP 71 — 고객 답변의 **범위**를 잠급니다.

STEP 70 은 고객이 확정한 규칙(예산과목 6종 · 교육비/강사료 · 단기 차량 임차)이
**어떻게 동작하는가**를 잠갔습니다. 이 파일은 그 옆에서 다른 것을 지킵니다 —
**고객이 말하지 않은 것을 말한 것처럼 굳히지 않았는가.**

세 가지를 봅니다
================

1. **단기 차량 임차 · 기간 무관** — 하루짜리든 2박3일짜리든 자동으로 빠지지
   않고, 담당자가 품의서를 확인한 뒤 **똑같이** 뺄 수 있다.
2. **지출결의서 단위 묶음을 만들지 않았다** — 고객은 *"어느 방식이 편한지
   모르겠다"* 고 답했다. 선호를 밝히지 않은 것은 **요청이 아니다.**
3. **문서가 고객 확정과 우리 판단을 갈라 놓았는가** — 원본 보존 · 모집단 분리 ·
   되돌리기 제한 · 금액 표시는 우리가 고른 방식이다.

.. warning::
    ⛔ 이 파일은 **업무규칙을 새로 만들지 않습니다.** 고객이 답한 범위를 넘지
    않았다는 것만 확인합니다.

.. note::
    합성 데이터만 씁니다.
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.calculators.procurement_achievement import ProcurementAchievementCalculator
from procurement.core.performance_exclusion import (
    EXCLUDED,
    REASON_SHORT_TERM_VEHICLE_LEASE,
)
from procurement.core.period import PAYMENT_DATE, PeriodFilter
from procurement.database.bootstrap import bootstrap
from procurement.database.certification_repository import CertificationRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Purchase
from procurement.reviews.query import SORT_KEYS

_PLAIN = "1000000002"
_DAY = date(2026, 3, 1)

_DOCS = Path(__file__).resolve().parents[1] / "docs"
_DECISIONS = _DOCS / "DECISIONS.md"
_QUESTIONS = _DOCS / "CUSTOMER_DATA_QUESTIONS.md"

#: 고객이 직접 말한 출장 형태 — 기간 표현이 제각각이다.
TRIP_SHAPES = ("하루", "1박2일", "2박3일")


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "answer_scope.db"
    bootstrap(path)
    PolicyRepository(path).update_target_rate("SMALL_BUSINESS", Decimal("30"))
    return path


@pytest.fixture
def client(db: Path) -> TestClient:
    return TestClient(create_app(db, period_date_field=PAYMENT_DATE))


@pytest.fixture
def calculator(db: Path) -> ProcurementAchievementCalculator:
    return ProcurementAchievementCalculator(
        PurchaseRepository(db), CertificationRepository(db), PolicyRepository(db)
    )


def _add(db: Path, amount: str, *, description: str, budget_account: str = "임차료") -> int:
    saved = PurchaseRepository(db).insert(
        Purchase(
            business_no=_PLAIN,
            company_name="합성기업 나",
            contract_date=_DAY,
            payment_date=_DAY,
            resolution_date=_DAY,
            description=description,
            budget_account=budget_account,
            amount=Decimal(amount),
        )
    )
    assert saved.purchase_id is not None
    return saved.purchase_id


def _total(calculator: ProcurementAchievementCalculator) -> Decimal:
    return calculator.calculate_total_purchase(PeriodFilter.for_year(2026, PAYMENT_DATE))


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
# W-16 — 출장 기간이 달라도 처리는 하나다
# ======================================================================
class TestTripLengthDoesNotChangeAnything:
    """고객: *"하루 · 1박2일 · 2박3일 등 기간과 상관없이"* 단기 차량으로 본다."""

    @pytest.mark.parametrize("shape", TRIP_SHAPES)
    def test_no_trip_length_is_auto_excluded(
        self, db: Path, calculator: ProcurementAchievementCalculator, shape: str
    ) -> None:
        """⛔ 어떤 기간 표현도 **혼자서는** 실적을 빼지 못한다.

        판단 자료는 **사업부서 품의서**이고 그 자료는 시스템에 없다.
        """
        _add(db, "500", description=f"출장 {shape} 렌터카")
        assert _total(calculator) == Decimal("500")

    @pytest.mark.parametrize("shape", TRIP_SHAPES)
    def test_the_reviewer_excludes_them_all_the_same_way(
        self,
        db: Path,
        client: TestClient,
        calculator: ProcurementAchievementCalculator,
        shape: str,
    ) -> None:
        """⭐ 기간이 달라도 **같은 사유 하나**로 뺀다 — 기간별 갈래가 없다."""
        purchase_id = _add(db, "500", description=f"출장 {shape} 렌터카")
        response = client.put(
            f"/reviews/{purchase_id}/performance-exclusion",
            json={"reason": REASON_SHORT_TERM_VEHICLE_LEASE, "excluded_by": "담당자"},
        )

        assert response.status_code == 200
        assert response.json()["performance"]["status"] == EXCLUDED
        assert _total(calculator) == Decimal("0")

    def test_no_duration_field_exists_to_judge_by(self, db: Path) -> None:
        """⛔ 임차 기간 칸을 만들지 않았다 — 기준이 아니기 때문이다."""
        columns = {
            row["name"] for row in PurchaseRepository(db).execute("PRAGMA table_info(purchase)")
        }
        for absent in ("lease_days", "lease_period", "rental_days", "trip_days"):
            assert absent not in columns


# ======================================================================
# Q5-3 — 묶음 단위는 고객이 고르지 않았다
# ======================================================================
class TestNoExpenseDocumentGrouping:
    """고객: *"내용 정리만 잘 된다면 어느 방식이 편한지 모르겠다"*.

    ⛔ 선호를 밝히지 않은 것은 **묶어 달라는 요청이 아니다.** 지출결의서 단위
    묶음을 새로 만들지 않았다.
    """

    def test_no_grouping_option_is_offered(self) -> None:
        """정렬 기준에 결의서 묶음 축이 없다."""
        for absent in ("expense_document", "voucher", "group", "resolution_no"):
            assert absent not in SORT_KEYS

    def test_the_list_returns_transactions_not_groups(self, db: Path, client: TestClient) -> None:
        """같은 업체 · 같은 날 · 같은 적요라도 **건별로** 나온다."""
        first = _add(db, "1000", description="동일 적요", budget_account="일반운영비")
        second = _add(db, "2000", description="동일 적요", budget_account="일반운영비")

        body: Any = client.get("/reviews").json()
        assert sorted(item["source"]["purchase_id"] for item in body["items"]) == sorted(
            [first, second]
        )

    def test_no_group_field_in_the_response(self, db: Path, client: TestClient) -> None:
        """⛔ 응답 어디에도 "이 건들은 한 결의서다" 라고 말하는 칸이 없다."""
        _add(db, "1000", description="동일 적요", budget_account="일반운영비")
        item: Any = client.get("/reviews").json()["items"][0]
        for absent in ("group", "group_id", "expense_document", "voucher_id"):
            assert absent not in item
            assert absent not in item["source"]

    def test_what_the_customer_asked_for_is_there_instead(
        self, db: Path, client: TestClient
    ) -> None:
        """⭐ 고객이 실제로 말한 대조 수단 — **적요 · 업체 · 사업자번호 · 금액**."""
        purchase_id = _add(db, "1234", description="사무용품 구매", budget_account="일반운영비")
        found: Any = client.get("/reviews?page=1&page_size=50&search=사무용품").json()

        assert [item["source"]["purchase_id"] for item in found["items"]] == [purchase_id]
        assert Decimal(found["items"][0]["source"]["amount"]) == Decimal("1234")

    def test_resolution_date_sorting_serves_the_time_order_request(self) -> None:
        """고객: *"나중에는 지출 시간 순서대로 정리해야 한다"* — 정렬로 된다."""
        assert "resolution_date" in SORT_KEYS


# ======================================================================
# 문서 — 고객 확정과 우리 판단이 갈라져 있는가
# ======================================================================
class TestDesignJudgementsAreNotDocumentedAsConfirmed:
    """⛔ 우리가 고른 방식을 고객 확정사항처럼 적어 두지 않는다."""

    def test_the_four_judgements_are_collected_in_one_place(self) -> None:
        section = _section(_DECISIONS.read_text(encoding="utf-8"), "### 0.10.8")
        assert section
        assert "find_for_review" in section
        assert "find_for_calculation" in section
        assert "원본 보존" in section
        assert "되돌리기" in section
        assert "금액" in section

    def test_that_section_is_marked_as_a_judgement_not_a_confirmation(self) -> None:
        text = _DECISIONS.read_text(encoding="utf-8")
        heading = next(line for line in text.splitlines() if line.startswith("### 0.10.8"))
        assert "🟡" in heading
        assert "🟢" not in heading

    @pytest.mark.parametrize("number", ["0.10.1", "0.10.2", "0.10.6"])
    def test_the_customer_confirmed_sections_are_marked_green(self, number: str) -> None:
        """고객이 직접 답한 세 절만 🟢 다."""
        text = _DECISIONS.read_text(encoding="utf-8")
        heading = next(line for line in text.splitlines() if line.startswith(f"### {number} "))
        assert "🟢" in heading

    def test_keeping_the_original_row_is_pending_confirmation(self) -> None:
        """고객은 "삭제" 라고 답했다 — 보존은 **우리 판단**이며 확인 대기다."""
        text = _QUESTIONS.read_text(encoding="utf-8")
        assert "🟡 확인 대기" in text
        assert re.search(r"원본까지 지우기를 원하시면", text)

    def test_the_grouping_preference_is_recorded_as_open(self) -> None:
        text = _QUESTIONS.read_text(encoding="utf-8")
        assert "묶음 단위는 아직 확정이 아닙니다" in text
