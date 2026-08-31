"""
STEP 70 — **실적 산입 여부**. 2026-08-31 고객 확정 업무규칙을 잠급니다.

빼는 경로는 **둘뿐**입니다
==========================

1. **예산과목 규칙** — 고객이 지목한 6종은 내용과 관계없이 자동으로 뺍니다.
2. **담당자 확정** — 화면에서 사람이 사유를 골라 확정한 건.

.. warning::
    ⛔ **적요 낱말로 빼지 않습니다.** `교육` · `강사` · `임차` · `렌트` ·
    `단기` · `1일` 이 들어 있다는 이유만으로 자동 제외하지 않습니다.

    고객은 교육비·강사료를 **지출결의서와 세금계산서 내역**까지 보고, 단기
    차량 임차는 **사업부서 품의서**를 보고 판단한다고 답했습니다. 그 자료는
    시스템에 없습니다. 없는 근거로 자동 판정하면 사람이 확인하지 않은 판정이
    실적 숫자로 굳습니다.

.. warning::
    ⛔ **임차 기간으로 판정하지 않습니다.** 고객이 *"기간과 상관없이"* 라고
    명시했습니다. "○일 이하" 규칙을 만들지 않습니다.

.. warning::
    ⛔ **구매유형과 다른 개념입니다.** 한 건이 **용역이면서 실적 제외**일 수
    있어야 합니다(:class:`TestTypeAndPerformanceAreSeparate`).

.. note::
    합성 데이터만 씁니다. 고객이 지목한 2건은 **적요 문구만** 재현하며,
    실제 고객 데이터·금액·거래처를 쓰지 않습니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.calculators.procurement_achievement import ProcurementAchievementCalculator
from procurement.core.performance_exclusion import (
    EXCLUDED,
    EXCLUDED_BUDGET_ACCOUNTS,
    INCLUDED,
    REASON_EDUCATION_FEE,
    REASON_LECTURER_FEE,
    REASON_OTHER,
    REASON_SHORT_TERM_VEHICLE_LEASE,
    ExclusionReasonError,
    is_excluded_budget_account,
    validate_exclusion_reason,
)
from procurement.core.period import PAYMENT_DATE, PeriodFilter
from procurement.core.purchase_type import GOODS, SERVICE
from procurement.database.bootstrap import bootstrap
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.database.review_repository import ReviewRepository
from procurement.models import Certification, Company, Purchase
from procurement.models.review import ACTION_EXCLUDED, ACTION_INCLUDED, CONFIRMED

# 합성 사업자등록번호 — 실제 업체가 아닙니다.
_CERTIFIED = "1000000001"
_PLAIN = "1000000002"

_DAY = date(2026, 3, 1)

#: 고객이 지목한 2건 — **적요 문구만** 재현합니다.
CUSTOMER_ROWS = (
    "민원 담당자 교육(교육비, 임차료, 다과비)",
    "민원 담당자 교육 2차(강사비, 재료비, 다과비, 임차료)",
)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "exclusion.db"
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


def _policy_id(db: Path) -> int:
    policy = PolicyRepository(db).find_by_policy_code("SMALL_BUSINESS")
    assert policy is not None and policy.policy_id is not None
    return policy.policy_id


def _certified_company(db: Path) -> int:
    """중소기업 인증을 가진 합성 기업."""
    company = CompanyRepository(db).insert(
        Company(business_no=_CERTIFIED, company_name="합성기업 가", representative_name="홍길동")
    )
    assert company.company_id is not None
    CertificationRepository(db).insert(
        Certification(
            company_id=company.company_id,
            policy_id=_policy_id(db),
            valid_from=date(2020, 1, 1),
            valid_to=date(2030, 12, 31),
        )
    )
    return company.company_id


def _add(
    db: Path,
    amount: str,
    *,
    description: str = "합성 적요",
    budget_account: str | None = "일반운영비",
    company_id: int | None = None,
) -> int:
    saved = PurchaseRepository(db).insert(
        Purchase(
            business_no=_CERTIFIED if company_id else _PLAIN,
            company_name="합성기업 가" if company_id else "합성기업 나",
            contract_date=_DAY,
            payment_date=_DAY,
            resolution_date=_DAY,
            description=description,
            budget_account=budget_account,
            amount=Decimal(amount),
            company_id=company_id,
        )
    )
    assert saved.purchase_id is not None
    return saved.purchase_id


def _total(db: Path, calculator: ProcurementAchievementCalculator) -> Decimal:
    return calculator.calculate_total_purchase(PeriodFilter.for_year(2026, PAYMENT_DATE))


def _policy_total(db: Path, calculator: ProcurementAchievementCalculator) -> Decimal:
    return calculator.calculate_policy_purchase(
        _policy_id(db), PeriodFilter.for_year(2026, PAYMENT_DATE)
    )


def _exclude(client: TestClient, purchase_id: int, reason: str, **extra: object) -> httpx.Response:
    """실적 제외 요청 한 번."""
    payload: dict[str, object] = {"reason": reason}
    payload.update(extra)
    response: httpx.Response = client.put(
        f"/reviews/{purchase_id}/performance-exclusion", json=payload
    )
    return response


# ======================================================================
# W-17 ① 예산과목 규칙 — 자동 제외
# ======================================================================
class TestBudgetAccountRule:
    """고객이 지목한 6종은 **내용과 관계없이** 빠진다."""

    def test_the_six_accounts_are_exactly_what_the_customer_named(self) -> None:
        """⛔ 목록을 임의로 넓히지 않았다."""
        assert EXCLUDED_BUDGET_ACCOUNTS == frozenset(
            {"교육훈련비", "사업추진경비", "의료비", "수도광열비", "기타운영비", "복리후생비"}
        )

    @pytest.mark.parametrize("account", sorted(EXCLUDED_BUDGET_ACCOUNTS))
    def test_each_account_is_excluded_from_the_denominator(
        self, db: Path, calculator: ProcurementAchievementCalculator, account: str
    ) -> None:
        _add(db, "1000", budget_account="일반운영비")
        _add(db, "500", budget_account=account)
        assert _total(db, calculator) == Decimal("1000")

    def test_content_does_not_matter(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        """적요가 무엇이든 예산과목이 6종이면 빠진다 — 고객 답변 그대로."""
        _add(db, "500", description="사무용품 구매", budget_account="교육훈련비")
        assert _total(db, calculator) == Decimal("0")

    def test_surrounding_spaces_are_trimmed(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        _add(db, "500", budget_account="  교육훈련비  ")
        assert _total(db, calculator) == Decimal("0")

    @pytest.mark.parametrize(
        "account", ["교육훈련비지원", "특별교육훈련비", "일반운영비", "외주용역비", "임차료"]
    )
    def test_other_accounts_are_not_excluded(
        self, db: Path, calculator: ProcurementAchievementCalculator, account: str
    ) -> None:
        """⛔ 부분 문자열로 판단하지 않는다 — 정확히 같은 값만 뺀다."""
        _add(db, "500", budget_account=account)
        assert _total(db, calculator) == Decimal("500")

    def test_blank_account_is_not_excluded(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        """예산과목이 비어 있는 행은 그대로 계산에 든다(Q5-9 🔴 미확정)."""
        _add(db, "500", budget_account=None)
        assert _total(db, calculator) == Decimal("500")

    def test_the_rule_also_removes_it_from_the_numerator(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        company_id = _certified_company(db)
        _add(db, "1000", company_id=company_id, budget_account="일반운영비")
        _add(db, "500", company_id=company_id, budget_account="복리후생비")
        assert _policy_total(db, calculator) == Decimal("1000")

    def test_the_row_is_not_deleted(self, db: Path) -> None:
        """⛔ 규칙으로 빠져도 **행은 남는다.**"""
        _add(db, "500", budget_account="의료비")
        assert PurchaseRepository(db).count() == 1

    def test_it_still_appears_in_the_review_list(self, db: Path, client: TestClient) -> None:
        """빠진 건도 검토 화면에는 보인다 — 사유를 확인할 수 있어야 한다."""
        purchase_id = _add(db, "500", budget_account="의료비")
        body: Any = client.get("/reviews").json()
        assert [item["source"]["purchase_id"] for item in body["items"]] == [purchase_id]

    def test_the_screen_says_why(self, db: Path, client: TestClient) -> None:
        purchase_id = _add(db, "500", budget_account="수도광열비")
        performance: Any = client.get(f"/reviews/{purchase_id}").json()["performance"]
        assert performance["status"] == EXCLUDED
        assert performance["by_budget_account_rule"] is True
        assert performance["reason_label"] == "예산과목 규칙"
        assert performance["can_reopen"] is False

    def test_the_rule_cannot_be_undone_from_the_screen(
        self, db: Path, client: TestClient, calculator: ProcurementAchievementCalculator
    ) -> None:
        """⛔ 규칙은 담당자 판단이 아니라 고객이 정한 것이다 — 되돌려도 안 돌아온다."""
        purchase_id = _add(db, "500", budget_account="기타운영비")
        client.request(
            "DELETE", f"/reviews/{purchase_id}/performance-exclusion", json={"changed_by": "담당자"}
        )
        assert _total(db, calculator) == Decimal("0")
        assert client.get(f"/reviews/{purchase_id}").json()["performance"]["status"] == EXCLUDED

    def test_helper_matches_exactly(self) -> None:
        assert is_excluded_budget_account("교육훈련비") is True
        assert is_excluded_budget_account(" 의료비 ") is True
        assert is_excluded_budget_account("교육훈련비지원") is False
        assert is_excluded_budget_account(None) is False


# ======================================================================
# W-17 ② 교육비 · 강사료 — 담당자가 확인 후 확정
# ======================================================================
class TestEducationAndLecturerFee:
    """⛔ 낱말로 자동 제외하지 않는다. 담당자가 확정한다."""

    @pytest.mark.parametrize(
        "description",
        ["교육비 지출", "강사료 지급", "교육훈련 참가비", "직원 교육 관련 지출", "외부 강사 초빙"],
    )
    def test_words_alone_do_not_exclude(
        self, db: Path, calculator: ProcurementAchievementCalculator, description: str
    ) -> None:
        """⭐ 고객이 지출결의서·세금계산서까지 확인한다고 했으므로 자동 판정 불가."""
        _add(db, "500", description=description, budget_account="일반운영비")
        assert _total(db, calculator) == Decimal("500")

    @pytest.mark.parametrize("description", CUSTOMER_ROWS)
    def test_the_two_rows_the_customer_named_are_not_auto_excluded(
        self, db: Path, calculator: ProcurementAchievementCalculator, description: str
    ) -> None:
        """⛔ 고객이 지목했다고 해서 **적요만으로** 자동으로 빼지 않는다.

        어떤 행을 말하는지 담당자가 확인해야 같은 문구의 다른 행이 함께
        사라지지 않는다.
        """
        _add(db, "500", description=description, budget_account="행사운영비")
        assert _total(db, calculator) == Decimal("500")

    @pytest.mark.parametrize("description", CUSTOMER_ROWS)
    def test_the_reviewer_can_exclude_them(
        self,
        db: Path,
        client: TestClient,
        calculator: ProcurementAchievementCalculator,
        description: str,
    ) -> None:
        """⭐ 고객이 지목한 2건을 담당자가 실적에서 뺄 수 있다."""
        purchase_id = _add(db, "500", description=description, budget_account="행사운영비")
        response = _exclude(client, purchase_id, REASON_EDUCATION_FEE, excluded_by="담당자")

        assert response.status_code == 200
        assert response.json()["performance"]["status"] == EXCLUDED
        assert _total(db, calculator) == Decimal("0")

    @pytest.mark.parametrize("reason", [REASON_EDUCATION_FEE, REASON_LECTURER_FEE])
    def test_both_reasons_are_available(self, db: Path, client: TestClient, reason: str) -> None:
        purchase_id = _add(db, "500")
        assert _exclude(client, purchase_id, reason).status_code == 200


# ======================================================================
# W-16 단기 차량 임차
# ======================================================================
class TestShortTermVehicleLease:
    """고객 확정: 단발성 출장을 위해 출장지에서 빌린 차량 — **기간 무관**."""

    @pytest.mark.parametrize(
        "description",
        [
            "출장 차량 1일 렌트",
            "출장지 렌터카 2일",
            "단기 차량 임차",
            "차량 임차료 3월분",
            "업무용 차량 장기 임차",
            "쏘카 이용료",
        ],
    )
    def test_words_alone_do_not_exclude(
        self, db: Path, calculator: ProcurementAchievementCalculator, description: str
    ) -> None:
        """⛔ 임차·렌트·단기·일수 표현으로 자동 판정하지 않는다.

        고객은 **사업부서 품의서**를 보고 판단한다고 답했고, 그 자료는 이
        시스템에 없다.
        """
        _add(db, "500", description=description, budget_account="임차료")
        assert _total(db, calculator) == Decimal("500")

    @pytest.mark.parametrize("days", ["1일", "2일", "3일", "7일", "30일", "1박2일", "2박3일"])
    def test_no_day_threshold_rule_exists(
        self, db: Path, calculator: ProcurementAchievementCalculator, days: str
    ) -> None:
        """⭐ **"○일 이하" 규칙이 없다.** 고객이 "기간과 상관없이" 라고 했다."""
        _add(db, "500", description=f"출장 차량 {days} 렌트", budget_account="임차료")
        assert _total(db, calculator) == Decimal("500")

    def test_the_reviewer_can_exclude_it(
        self, db: Path, client: TestClient, calculator: ProcurementAchievementCalculator
    ) -> None:
        """⭐ 담당자가 품의서를 확인한 뒤 실적에서 뺄 수 있다."""
        purchase_id = _add(db, "500", description="출장 차량 렌트", budget_account="임차료")
        response = _exclude(
            client, purchase_id, REASON_SHORT_TERM_VEHICLE_LEASE, excluded_by="담당자"
        )

        assert response.status_code == 200
        assert response.json()["performance"]["reason_label"] == "단기 차량 임차"
        assert _total(db, calculator) == Decimal("0")

    def test_the_screen_explains_how_to_judge(self, client: TestClient) -> None:
        """화면이 판단 자료(품의서)를 알려 준다 — ⛔ 자동 판정 기준이 아니다."""
        body: Any = client.get("/reviews/exclusion-reasons").json()
        assert "품의서" in body["vehicle_lease_notice"]
        assert "기간과 관계없이" in body["vehicle_lease_notice"]

    def test_the_reason_is_offered(self, client: TestClient) -> None:
        body: Any = client.get("/reviews/exclusion-reasons").json()
        labels = [item["label"] for item in body["items"]]
        assert labels == ["교육비", "강사료", "단기 차량 임차", "기타"]


# ======================================================================
# 달성률에 미치는 영향
# ======================================================================
class TestAchievementEffect:
    """⭐ 제외한 만큼 **분모와 분자가 함께** 줄어든다."""

    def test_denominator_shrinks(
        self, db: Path, client: TestClient, calculator: ProcurementAchievementCalculator
    ) -> None:
        _add(db, "8000")
        target = _add(db, "2000")
        assert _total(db, calculator) == Decimal("10000")

        _exclude(client, target, REASON_OTHER, excluded_by="담당자")

        assert _total(db, calculator) == Decimal("8000")

    def test_numerator_shrinks_too(
        self, db: Path, client: TestClient, calculator: ProcurementAchievementCalculator
    ) -> None:
        """⛔ 분모에서만 빼면 비율이 부풀어 오른다 — 분자에서도 빠져야 한다."""
        company_id = _certified_company(db)
        _add(db, "3000", company_id=company_id)
        target = _add(db, "1000", company_id=company_id)
        assert _policy_total(db, calculator) == Decimal("4000")

        _exclude(client, target, REASON_OTHER, excluded_by="담당자")

        assert _policy_total(db, calculator) == Decimal("3000")

    def test_the_ratio_moves_as_expected(
        self, db: Path, client: TestClient, calculator: ProcurementAchievementCalculator
    ) -> None:
        """지시 §6 의 예 — 분모 10,000 · 분자 3,000 에서 2,000 을 빼면 37.5%."""
        company_id = _certified_company(db)
        _add(db, "3000", company_id=company_id)  # 인증기업 — 분자
        _add(db, "5000")  # 미인증 — 분모만
        target = _add(db, "2000")  # 미인증 — 뺄 대상

        before = calculator.calculate_achievement(
            _policy_id(db), Decimal("30"), PeriodFilter.for_year(2026, PAYMENT_DATE)
        )
        assert before.total_purchase_amount == Decimal("10000")
        assert before.purchase_amount == Decimal("3000")

        _exclude(client, target, REASON_OTHER, excluded_by="담당자")

        after = calculator.calculate_achievement(
            _policy_id(db), Decimal("30"), PeriodFilter.for_year(2026, PAYMENT_DATE)
        )
        assert after.total_purchase_amount == Decimal("8000")
        assert after.purchase_amount == Decimal("3000")
        # 3,000 / 8,000 = 37.5% ÷ 목표 30% = 125%
        assert after.achievement_rate == Decimal("125.00")

    def test_the_dashboard_agrees(self, db: Path, client: TestClient) -> None:
        """API 까지 같은 숫자가 도달한다."""
        _add(db, "8000")
        target = _add(db, "2000")
        _exclude(client, target, REASON_OTHER, excluded_by="담당자")

        body: Any = client.get("/dashboard/summary?year=2026").json()
        assert Decimal(body["total_purchase_amount"]) == Decimal("8000")


# ======================================================================
# 구매유형과 실적 산입은 별개
# ======================================================================
class TestTypeAndPerformanceAreSeparate:
    """⭐ 한 건이 **용역이면서 실적 제외**일 수 있다(지시 §7)."""

    def test_confirming_a_type_does_not_exclude(
        self, db: Path, client: TestClient, calculator: ProcurementAchievementCalculator
    ) -> None:
        purchase_id = _add(db, "1000")
        client.put(
            f"/reviews/{purchase_id}",
            json={"final_purchase_type": SERVICE, "reviewed_by": "담당자"},
        )
        assert _total(db, calculator) == Decimal("1000")

    def test_excluding_does_not_change_the_type(self, db: Path, client: TestClient) -> None:
        purchase_id = _add(db, "1000")
        client.put(
            f"/reviews/{purchase_id}",
            json={"final_purchase_type": SERVICE, "reviewed_by": "담당자"},
        )
        _exclude(client, purchase_id, REASON_LECTURER_FEE, excluded_by="담당자")

        body: Any = client.get(f"/reviews/{purchase_id}").json()
        assert body["review"]["final_purchase_type"] == SERVICE
        assert body["review"]["status"] == CONFIRMED
        assert body["performance"]["status"] == EXCLUDED
        assert body["performance"]["reason_label"] == "강사료"

    def test_both_facts_live_in_different_blocks(self, db: Path, client: TestClient) -> None:
        """⛔ 한 필드에 섞지 않는다."""
        purchase_id = _add(db, "1000")
        client.put(
            f"/reviews/{purchase_id}",
            json={"final_purchase_type": GOODS, "reviewed_by": "담당자"},
        )
        _exclude(client, purchase_id, REASON_OTHER)

        body: Any = client.get(f"/reviews/{purchase_id}").json()
        assert "performance" not in body["review"]
        assert "final_purchase_type" not in body["performance"]

    def test_excluding_does_not_require_a_type(self, db: Path, client: TestClient) -> None:
        """유형을 정하지 않은 건도 실적에서 뺄 수 있다."""
        purchase_id = _add(db, "1000")
        assert _exclude(client, purchase_id, REASON_OTHER).status_code == 200

        body: Any = client.get(f"/reviews/{purchase_id}").json()
        assert body["review"]["final_purchase_type"] is None
        assert body["performance"]["status"] == EXCLUDED


# ======================================================================
# 되돌리기 · 이력 · 원본 보존
# ======================================================================
class TestReversalAndHistory:
    """제외는 되돌릴 수 있고, **무엇을 했는지는 남는다**."""

    def test_reversal_puts_it_back(
        self, db: Path, client: TestClient, calculator: ProcurementAchievementCalculator
    ) -> None:
        purchase_id = _add(db, "1000")
        _exclude(client, purchase_id, REASON_OTHER, excluded_by="담당자")
        assert _total(db, calculator) == Decimal("0")

        response = client.request(
            "DELETE", f"/reviews/{purchase_id}/performance-exclusion", json={"changed_by": "담당자"}
        )

        assert response.status_code == 200
        assert response.json()["performance"]["status"] == INCLUDED
        assert _total(db, calculator) == Decimal("1000")

    def test_who_and_when_is_kept(self, db: Path, client: TestClient) -> None:
        purchase_id = _add(db, "1000")
        _exclude(client, purchase_id, REASON_EDUCATION_FEE, excluded_by="담당자")

        performance: Any = client.get(f"/reviews/{purchase_id}").json()["performance"]
        assert performance["excluded_by"] == "담당자"
        assert performance["excluded_at"] is not None
        assert performance["reason"] == REASON_EDUCATION_FEE

    def test_history_records_both_directions(self, db: Path, client: TestClient) -> None:
        """⭐ 뺀 것도 되돌린 것도 이력에 남는다."""
        purchase_id = _add(db, "1000")
        _exclude(client, purchase_id, REASON_OTHER, excluded_by="담당자")
        client.request(
            "DELETE", f"/reviews/{purchase_id}/performance-exclusion", json={"changed_by": "담당자"}
        )

        actions = [entry.action for entry in ReviewRepository(db).find_history(purchase_id)]
        assert ACTION_EXCLUDED in actions
        assert ACTION_INCLUDED in actions

    def test_existing_confirmation_history_survives(self, db: Path, client: TestClient) -> None:
        """⛔ 기존 검토 이력을 지우지 않는다."""
        purchase_id = _add(db, "1000")
        client.put(
            f"/reviews/{purchase_id}",
            json={"final_purchase_type": SERVICE, "reviewed_by": "담당자"},
        )
        _exclude(client, purchase_id, REASON_OTHER, excluded_by="담당자")

        actions = [entry.action for entry in ReviewRepository(db).find_history(purchase_id)]
        assert CONFIRMED in actions
        assert ACTION_EXCLUDED in actions

    def test_the_row_is_never_deleted(self, db: Path, client: TestClient) -> None:
        """⭐ 고객은 업무상 "삭제" 라 하지만, 시스템은 원본을 보존한다."""
        purchase_id = _add(db, "1000")
        before = PurchaseRepository(db).find_by_id(purchase_id)
        _exclude(client, purchase_id, REASON_OTHER, excluded_by="담당자")

        after = PurchaseRepository(db).find_by_id(purchase_id)
        assert after is not None and before is not None
        assert after.amount == before.amount
        assert after.description == before.description
        assert PurchaseRepository(db).count() == 1

    def test_excluded_rows_stay_in_the_review_list(self, db: Path, client: TestClient) -> None:
        """⭐ 뺀 건이 목록에서 사라지면 되돌릴 방법이 없다."""
        purchase_id = _add(db, "1000")
        _exclude(client, purchase_id, REASON_OTHER, excluded_by="담당자")

        body: Any = client.get("/reviews").json()
        assert [item["source"]["purchase_id"] for item in body["items"]] == [purchase_id]


# ======================================================================
# 사유 검증
# ======================================================================
class TestReasonValidation:
    """⛔ 사유 없이, 또는 아무 사유로나 뺄 수 없다."""

    def test_unknown_reason_is_rejected(self, db: Path, client: TestClient) -> None:
        purchase_id = _add(db, "1000")
        assert _exclude(client, purchase_id, "WHATEVER").status_code == 422

    def test_missing_reason_is_rejected(self, db: Path, client: TestClient) -> None:
        purchase_id = _add(db, "1000")
        response = client.put(f"/reviews/{purchase_id}/performance-exclusion", json={})
        assert response.status_code == 422

    def test_the_budget_rule_reason_cannot_be_chosen(self, db: Path, client: TestClient) -> None:
        """⛔ 규칙이 붙이는 사유를 사람이 고를 수 없다."""
        purchase_id = _add(db, "1000")
        assert _exclude(client, purchase_id, "BUDGET_ACCOUNT_RULE").status_code == 422

    def test_a_rejected_request_changes_nothing(
        self, db: Path, client: TestClient, calculator: ProcurementAchievementCalculator
    ) -> None:
        purchase_id = _add(db, "1000")
        _exclude(client, purchase_id, "WHATEVER")
        assert _total(db, calculator) == Decimal("1000")
        assert client.get(f"/reviews/{purchase_id}").json()["performance"]["status"] == INCLUDED

    def test_unknown_purchase_is_404(self, client: TestClient) -> None:
        assert _exclude(client, 9999, REASON_OTHER).status_code == 404

    def test_validator_rejects_and_accepts(self) -> None:
        assert validate_exclusion_reason(REASON_OTHER) == REASON_OTHER
        with pytest.raises(ExclusionReasonError):
            validate_exclusion_reason("NOPE")


# ======================================================================
# 기존 데이터 보호
# ======================================================================
class TestExistingDataIsUnaffected:
    """⭐ 마이그레이션만으로 기존 달성률이 달라지지 않는다."""

    def test_default_is_included(self, db: Path, client: TestClient) -> None:
        """아무것도 하지 않은 행은 **실적 포함**이다."""
        purchase_id = _add(db, "1000")
        performance: Any = client.get(f"/reviews/{purchase_id}").json()["performance"]
        assert performance["status"] == INCLUDED
        assert performance["reason"] is None

    def test_a_review_row_without_exclusion_is_included(self, db: Path, client: TestClient) -> None:
        """확정만 한 행도 실적에 그대로 남는다."""
        purchase_id = _add(db, "1000")
        client.put(
            f"/reviews/{purchase_id}",
            json={"final_purchase_type": GOODS, "reviewed_by": "담당자"},
        )
        review = ReviewRepository(db).find_by_purchase_id(purchase_id)
        assert review is not None
        assert review.performance_status == INCLUDED

    def test_untouched_rows_keep_the_denominator(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        for amount in ("1000", "2000", "3000"):
            _add(db, amount)
        assert _total(db, calculator) == Decimal("6000")


# ======================================================================
# Q5-3 — 결의번호 없이 거래를 찾는다
# ======================================================================
class TestFindingTransactionsWithoutADocumentNumber:
    """고객은 **적요 + 업체명 또는 사업자번호 + 금액**으로 확인한다고 답했다."""

    @pytest.fixture
    def seeded(self, db: Path) -> dict[str, int]:
        return {
            "alpha": _add(db, "1000", description="사무용품 구매"),
            "beta": _add(db, "2000", description="복사기 임차료"),
        }

    def _ids(self, client: TestClient, query: str) -> list[int]:
        """검색·정렬은 **페이지 방식**에서만 동작합니다.

        ``page`` 를 주지 않으면 ``/reviews`` 는 옛 방식(``limit``·``offset``)
        으로 떨어져 검색·정렬 조건을 보지 않습니다. 화면은 항상 ``page`` 를
        보내므로, 시험도 화면과 **같은 경로**를 씁니다.
        """
        body: Any = client.get(f"/reviews?page=1&page_size=50&{query.lstrip('?')}").json()
        return [item["source"]["purchase_id"] for item in body["items"]]

    def test_search_by_description(self, client: TestClient, seeded: dict[str, int]) -> None:
        assert self._ids(client, "?search=사무용품") == [seeded["alpha"]]

    def test_search_by_company_name(self, client: TestClient, seeded: dict[str, int]) -> None:
        """⭐ 거래처명으로도 찾을 수 있다(STEP 70 에서 넓혔다)."""
        assert set(self._ids(client, "?search=합성기업 나")) == set(seeded.values())

    def test_search_by_business_no(self, client: TestClient, seeded: dict[str, int]) -> None:
        """⭐ 사업자등록번호로도 찾을 수 있다."""
        assert set(self._ids(client, f"?search={_PLAIN}")) == set(seeded.values())

    def test_amount_is_visible_for_comparison(
        self, client: TestClient, seeded: dict[str, int]
    ) -> None:
        """금액은 목록에 그대로 실린다 — 고객이 눈으로 맞대어 본다."""
        body: Any = client.get("/reviews").json()
        amounts = {Decimal(item["source"]["amount"]) for item in body["items"]}
        assert amounts == {Decimal("1000"), Decimal("2000")}

    @pytest.mark.parametrize("direction", ["asc", "desc"])
    def test_sorting_by_resolution_date(self, db: Path, client: TestClient, direction: str) -> None:
        """⭐ 지출 순서를 보기 위해 결의일자로 정렬할 수 있다."""
        early = PurchaseRepository(db).insert(
            Purchase(
                business_no=_PLAIN,
                company_name="합성기업 나",
                contract_date=_DAY,
                payment_date=_DAY,
                resolution_date=date(2026, 1, 1),
                amount=Decimal("100"),
                description="이른 건",
            )
        )
        late = PurchaseRepository(db).insert(
            Purchase(
                business_no=_PLAIN,
                company_name="합성기업 나",
                contract_date=_DAY,
                payment_date=_DAY,
                resolution_date=date(2026, 12, 31),
                amount=Decimal("200"),
                description="늦은 건",
            )
        )
        assert early.purchase_id is not None and late.purchase_id is not None

        ids = self._ids(client, f"?sort=resolution_date&direction={direction}")
        expected = [early.purchase_id, late.purchase_id]
        assert ids == (expected if direction == "asc" else list(reversed(expected)))

    def test_same_description_is_not_grouped(self, db: Path, client: TestClient) -> None:
        """⛔ 적요가 같다고 **같은 지출결의서로 묶지 않는다**(Q5-3 확정).

        묶으면 서로 다른 결의서가 한 건으로 보이고, 담당자가 잘못된 단위로
        판단하게 된다.
        """
        first = _add(db, "1000", description="같은 적요")
        second = _add(db, "2000", description="같은 적요")

        body: Any = client.get("/reviews").json()
        assert sorted(item["source"]["purchase_id"] for item in body["items"]) == sorted(
            [first, second]
        )
        assert body["progress"]["total"] == 2

    def test_no_document_number_field_was_added(self, db: Path) -> None:
        """⛔ 결의번호 컬럼을 만들지 않았다(고객: 그런 번호가 없다)."""
        columns = {
            row["name"] for row in PurchaseRepository(db).execute("PRAGMA table_info(purchase)")
        }
        for absent in ("resolution_no", "resolution_number", "document_no", "voucher_no"):
            assert absent not in columns


# ======================================================================
# 다른 미확정 사항은 건드리지 않았다
# ======================================================================
class TestOtherUnconfirmedRulesUntouched:
    """⛔ W-1-2 와 Q5-8 은 이번에 손대지 않았다."""

    @pytest.mark.parametrize("code", ["SMALL_BUSINESS", "WOMAN", "DISABLED"])
    def test_w1_2_general_policies_still_use_payment_date(self, db: Path, code: str) -> None:
        policy = PolicyRepository(db).find_by_policy_code(code)
        assert policy is not None
        assert policy.evaluation_basis == "PAYMENT_DATE"

    def test_startup_rule_is_unchanged(self, db: Path) -> None:
        policy = PolicyRepository(db).find_by_policy_code("STARTUP")
        assert policy is not None
        assert policy.evaluation_basis == "RESOLUTION_OR_CONTRACT_DATE"

    def test_q5_8_zero_and_negative_still_rejected_at_import(self, db: Path) -> None:
        """⛔ 0원·음수 적재 규칙을 바꾸지 않았다."""
        from procurement.database.purchase_repository import PurchaseValidationError

        for amount in ("0", "-500"):
            with pytest.raises(PurchaseValidationError):
                PurchaseRepository(db).insert(
                    Purchase(
                        business_no=_PLAIN,
                        company_name="합성기업 나",
                        contract_date=_DAY,
                        payment_date=_DAY,
                        amount=Decimal(amount),
                    )
                )
