"""
STEP 103 — 여성기업 구매유형별 달성률.

무엇이 달라졌는가
=================
여성기업 목표는 유형별로 갈린다 — 공사 3% · 용역 5% · 물품 5%. 그래서 분모도
유형별이어야 한다. 이번 STEP 은 담당자가 검토 화면에서 **확정한** 구매유형
(``purchase_review.final_purchase_type``)을 분모·분자에 **함께** 적용하는 경로를
계산기에 연결했다.

⛔ **유형을 자동 판정하게 된 것이 아니다.** 확정된 행만 센다. 적요·예산과목·
거래처명으로 유추하지 않는다. 확정되지 않은 행은 어느 유형에도 들어가지 않으며,
버려지는 것도 아니다 — 담당자가 나중에 확정하면 그때 자연히 들어온다.

⛔ 실제 고객 데이터는 쓰지 않는다. 여기의 사업자등록번호·거래처명은 전부
합성값이며, 실데이터 검증을 대신하지 않는다(§14).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procurement.__main__ import main
from procurement.app import create_app
from procurement.calculators import ProcurementAchievementCalculator
from procurement.core.purchase_type import CONSTRUCTION, GOODS, SERVICE
from procurement.core.target_scope import CALCULABLE_SCOPES, PRODUCIBLE_ITEMS, TOTAL
from procurement.database.bootstrap import bootstrap
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.policy_target_repository import PolicyTargetRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.database.review_repository import ReviewRepository
from procurement.models import Certification, Company, Purchase
from procurement.models.classification import ClassificationResult

#: 합성 사업자등록번호 — ⛔ 실제 고객 값이 아니다.
_WOMAN_BNO = "1112233445"
_OTHER_BNO = "9998877665"

#: 분석 결과는 이 시험의 관심사가 아니다 — 담당자 확정만 본다.
_EMPTY_ANALYSIS = ClassificationResult(analyzer_name="manual", analyzer_version="1")


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "step103.db"
    bootstrap(path)
    ReviewRepository(path).create_table()
    return path


def _policy_id(db_path: Path, code: str) -> int:
    policy = PolicyRepository(db_path).find_by_policy_code(code)
    assert policy is not None and policy.policy_id is not None
    return policy.policy_id


def _certify(
    db_path: Path,
    code: str,
    business_no: str = _WOMAN_BNO,
    *,
    valid_from: date = date(2026, 1, 1),
    valid_to: date = date(2026, 12, 31),
) -> None:
    """합성 기업 하나에 정책 인증을 붙인다."""
    companies = CompanyRepository(db_path)
    company = companies.find_by_business_no(business_no)
    if company is None:
        company = companies.insert(
            Company(business_no=business_no, company_name="합성상사", representative_name="홍길동")
        )
    assert company.company_id is not None
    CertificationRepository(db_path).insert(
        Certification(
            company_id=company.company_id,
            policy_id=_policy_id(db_path, code),
            valid_from=valid_from,
            valid_to=valid_to,
        )
    )


def _spend(
    db_path: Path,
    business_no: str,
    amount: str,
    purchase_type: str | None,
    *,
    resolution_date: date = date(2026, 3, 5),
    issue_date: date | None = None,
    budget_account: str | None = None,
) -> int:
    """구매 한 건을 넣고, 유형이 주어지면 담당자 확정까지 기록한다."""
    purchase = PurchaseRepository(db_path).insert(
        Purchase(
            business_no=business_no,
            company_name="합성상사",
            amount=Decimal(amount),
            payment_date=resolution_date,
            contract_date=resolution_date,
            resolution_date=resolution_date,
            issue_date=issue_date,
            budget_account=budget_account,
        )
    )
    assert purchase.purchase_id is not None
    if purchase_type is not None:
        reviews = ReviewRepository(db_path)
        reviews.save_analysis(purchase.purchase_id, _EMPTY_ANALYSIS)
        reviews.confirm(
            purchase.purchase_id, final_purchase_type=purchase_type, reviewed_by="담당자"
        )
    return purchase.purchase_id


def _calculator(db_path: Path) -> ProcurementAchievementCalculator:
    return ProcurementAchievementCalculator(
        PurchaseRepository(db_path),
        CertificationRepository(db_path),
        PolicyRepository(db_path),
    )


def _summary(db_path: Path) -> dict[str, object]:
    client = TestClient(create_app(db_path))
    client.post("/purchases/rematch")
    body: dict[str, object] = client.get("/dashboard/summary?year=2026").json()
    return body


def _woman_item(db_path: Path) -> dict[str, object]:
    policies = _summary(db_path)["policies"]
    assert isinstance(policies, list)
    return next(item for item in policies if item["policy_code"] == "WOMAN")


def _scoped(item: dict[str, object]) -> dict[str, dict[str, object]]:
    achievements = item["scoped_achievements"]
    assert isinstance(achievements, list)
    return {entry["scope"]: entry for entry in achievements}


# ======================================================================
# §11  지시서가 준 합성 E2E 예시가 그대로 나오는가
# ======================================================================
class TestTheWorkedExample:
    """§11 표를 코드가 그대로 재현하는가."""

    @pytest.fixture
    def seeded(self, db_path: Path) -> Path:
        main(["targets", "--year", "2026", "--db", str(db_path)])
        _certify(db_path, "WOMAN")
        for business_no, amount, purchase_type in (
            (_WOMAN_BNO, "1000000", CONSTRUCTION),  # A
            (_OTHER_BNO, "9000000", CONSTRUCTION),  # B
            (_WOMAN_BNO, "2000000", SERVICE),  # C
            (_OTHER_BNO, "8000000", SERVICE),  # D
            (_WOMAN_BNO, "1000000", GOODS),  # E
            (_OTHER_BNO, "9000000", GOODS),  # F
        ):
            _spend(db_path, business_no, amount, purchase_type)
        # 계산기를 직접 부르는 시험이므로 사업자번호 매칭을 먼저 돌린다 —
        # 대시보드 경로와 달리 여기서는 자동으로 일어나지 않는다.
        TestClient(create_app(db_path)).post("/purchases/rematch")
        return db_path

    @pytest.mark.parametrize(
        ("scope", "denominator", "numerator"),
        [
            (CONSTRUCTION, "10000000", "1000000"),
            (SERVICE, "10000000", "2000000"),
            (GOODS, "10000000", "1000000"),
        ],
    )
    def test_each_type_has_its_own_denominator(
        self, seeded: Path, scope: str, denominator: str, numerator: str
    ) -> None:
        """⭐ 같은 유형끼리 비교한다 — 분모가 전체 3,000만이 아니다."""
        calculator = _calculator(seeded)

        assert calculator.calculate_total_purchase(None, scope) == Decimal(denominator)
        assert calculator.calculate_policy_purchase(
            _policy_id(seeded, "WOMAN"), None, scope
        ) == Decimal(numerator)

    @pytest.mark.parametrize(
        ("scope", "rate"),
        [(CONSTRUCTION, "333.33"), (SERVICE, "400.00"), (GOODS, "200.00")],
    )
    def test_the_achievement_rate_matches_the_example(
        self, seeded: Path, scope: str, rate: str
    ) -> None:
        """공사 10%/3% · 용역 20%/5% · 물품 10%/5% — 셋 다 달성."""
        scoped = _scoped(_woman_item(seeded))

        assert scoped[scope]["achievement_rate"] == rate
        assert scoped[scope]["status_label"] == "정상"

    def test_the_policy_row_does_not_pick_one_rate(self, seeded: Path) -> None:
        """⛔ 달성률이 셋인데 하나를 대표로 고르지 않는다."""
        item = _woman_item(seeded)

        assert item["achievement_rate"] is None
        assert item["status_label"] == "유형별 달성률"
        assert len(_scoped(item)) == 3

    def test_the_policy_row_still_shows_the_total_performance(self, seeded: Path) -> None:
        """유형과 무관한 여성기업 전체 실적은 그대로 보인다(100+200+100만)."""
        assert _woman_item(seeded)["purchase_amount"] == "4000000"


# ======================================================================
# §12  경계 시험 TEST 1 ~ 10
# ======================================================================
class TestBoundaries:
    """지시서 §12 가 지목한 열 가지."""

    def test_1_outside_the_certification_window_is_not_counted(self, db_path: Path) -> None:
        """TEST 1 — 사업자번호는 맞지만 결의일자가 인증기간 **밖**이면 미해당."""
        main(["targets", "--year", "2026", "--db", str(db_path)])
        _certify(db_path, "WOMAN", valid_from=date(2025, 1, 1), valid_to=date(2025, 12, 31))
        _spend(db_path, _WOMAN_BNO, "1000000", CONSTRUCTION, resolution_date=date(2026, 3, 5))

        scoped = _scoped(_woman_item(db_path))

        assert scoped[CONSTRUCTION]["purchase_amount"] == "0"
        assert scoped[CONSTRUCTION]["total_purchase_amount"] == "1000000"

    def test_2_a_different_business_number_is_not_counted(self, db_path: Path) -> None:
        """TEST 2 — 사업자번호가 다르면 미해당. ⛔ 이름으로 대체 매칭하지 않는다."""
        main(["targets", "--year", "2026", "--db", str(db_path)])
        _certify(db_path, "WOMAN")
        _spend(db_path, _OTHER_BNO, "1000000", CONSTRUCTION)

        scoped = _scoped(_woman_item(db_path))

        assert scoped[CONSTRUCTION]["purchase_amount"] == "0"
        assert scoped[CONSTRUCTION]["total_purchase_amount"] == "1000000"

    def test_3_an_unconfirmed_type_joins_no_scope(self, db_path: Path) -> None:
        """TEST 3 — ⭐ 유형 미확정 건은 어느 유형에도 들어가지 않는다.

        분모에도 분자에도 없다. 여기가 무너지면 담당자가 정하지 않은 거래가
        임의의 유형으로 집계된다.
        """
        main(["targets", "--year", "2026", "--db", str(db_path)])
        _certify(db_path, "WOMAN")
        _spend(db_path, _WOMAN_BNO, "1000000", None)

        scoped = _scoped(_woman_item(db_path))

        for scope in (CONSTRUCTION, SERVICE, GOODS):
            assert scoped[scope]["total_purchase_amount"] == "0", scope
            assert scoped[scope]["purchase_amount"] == "0", scope

    def test_3b_the_unconfirmed_row_is_not_discarded(self, db_path: Path) -> None:
        """TEST 3 — ⛔ 버리는 것이 아니다. 전체 실적에는 그대로 있다."""
        main(["targets", "--year", "2026", "--db", str(db_path)])
        _certify(db_path, "WOMAN")
        _spend(db_path, _WOMAN_BNO, "1000000", None)

        assert _summary(db_path)["total_purchase_amount"] == "1000000"
        assert _woman_item(db_path)["purchase_amount"] == "1000000"

    def test_3c_confirming_later_brings_it_in(self, db_path: Path) -> None:
        """TEST 3 — 담당자가 나중에 확정하면 **그때** 계산 대상이 된다."""
        main(["targets", "--year", "2026", "--db", str(db_path)])
        _certify(db_path, "WOMAN")
        purchase_id = _spend(db_path, _WOMAN_BNO, "1000000", None)
        assert _scoped(_woman_item(db_path))[CONSTRUCTION]["total_purchase_amount"] == "0"

        reviews = ReviewRepository(db_path)
        reviews.save_analysis(purchase_id, _EMPTY_ANALYSIS)
        reviews.confirm(purchase_id, final_purchase_type=CONSTRUCTION, reviewed_by="담당자")

        scoped = _scoped(_woman_item(db_path))
        assert scoped[CONSTRUCTION]["total_purchase_amount"] == "1000000"
        assert scoped[CONSTRUCTION]["purchase_amount"] == "1000000"

    def test_4_one_spend_counts_for_every_policy_it_qualifies_for(self, db_path: Path) -> None:
        """TEST 4 — 여성 + 창업 + 중소에 같은 금액이 각각 들어간다."""
        main(["targets", "--year", "2026", "--db", str(db_path)])
        for code in ("WOMAN", "STARTUP", "SMALL_BUSINESS"):
            _certify(db_path, code)
        _spend(db_path, _WOMAN_BNO, "1000000", CONSTRUCTION)

        policies = _summary(db_path)["policies"]
        assert isinstance(policies, list)
        by_code = {item["policy_code"]: item for item in policies}

        assert by_code["STARTUP"]["purchase_amount"] == "1000000"
        assert by_code["SMALL_BUSINESS"]["purchase_amount"] == "1000000"
        assert _scoped(by_code["WOMAN"])[CONSTRUCTION]["purchase_amount"] == "1000000"

    def test_5_an_excluded_budget_account_drops_out_of_both_sides(self, db_path: Path) -> None:
        """TEST 5 — 제외 예산과목이면 여성기업 거래라도 분모·분자에서 빠진다."""
        main(["targets", "--year", "2026", "--db", str(db_path)])
        _certify(db_path, "WOMAN")
        _spend(db_path, _WOMAN_BNO, "1000000", CONSTRUCTION, budget_account="교육훈련비")

        scoped = _scoped(_woman_item(db_path))

        assert scoped[CONSTRUCTION]["total_purchase_amount"] == "0"
        assert scoped[CONSTRUCTION]["purchase_amount"] == "0"

    def test_5b_a_similar_account_name_is_not_excluded(self, db_path: Path) -> None:
        """TEST 5 — ⛔ 「교육훈련비지원」 은 정확히 같은 값이 아니므로 남는다."""
        main(["targets", "--year", "2026", "--db", str(db_path)])
        _certify(db_path, "WOMAN")
        _spend(db_path, _WOMAN_BNO, "1000000", CONSTRUCTION, budget_account="교육훈련비지원")

        assert _scoped(_woman_item(db_path))[CONSTRUCTION]["purchase_amount"] == "1000000"

    def test_6_the_resolution_date_decides_not_the_issue_date(self, db_path: Path) -> None:
        """TEST 6 — 신고기준일 2026-01-05 · 결의일자 2025-12-28 → **2025 년**.

        인증은 2026 년만 유효하므로 이 거래는 2026 년 화면에 아예 없고,
        2025 년에서도 인증기간 밖이라 여성기업 실적이 되지 않는다.
        ⛔ issue_date 를 쓰면 2026 년으로 잡혀 결과가 달라진다.
        """
        main(["targets", "--year", "2026", "--db", str(db_path)])
        _certify(db_path, "WOMAN")
        _spend(
            db_path,
            _WOMAN_BNO,
            "777000",
            CONSTRUCTION,
            resolution_date=date(2025, 12, 28),
            issue_date=date(2026, 1, 5),
        )

        client = TestClient(create_app(db_path))
        client.post("/purchases/rematch")

        assert client.get("/dashboard/summary?year=2026").json()["total_purchase_amount"] == "0"
        assert (
            client.get("/dashboard/summary?year=2025").json()["total_purchase_amount"] == "777000"
        )

    def test_7_total_policies_are_unchanged(self, db_path: Path) -> None:
        """TEST 7 — ⭐ 기존 TOTAL 계산이 그대로다.

        중소기업 분모는 유형과 무관한 **전체** 구매금액이어야 한다. 여성기업
        scoped 계산이 들어왔다고 달라지면 안 된다.
        """
        main(["targets", "--year", "2026", "--db", str(db_path)])
        _certify(db_path, "SMALL_BUSINESS")
        _spend(db_path, _WOMAN_BNO, "1000000", CONSTRUCTION)
        _spend(db_path, _OTHER_BNO, "1000000", None)  # 유형 미확정 — 그래도 분모에 든다

        policies = _summary(db_path)["policies"]
        assert isinstance(policies, list)
        small = next(item for item in policies if item["policy_code"] == "SMALL_BUSINESS")

        assert small["total_purchase_amount"] == "2000000"
        assert small["purchase_amount"] == "1000000"
        assert small["target_rate"] == "50"
        assert small["achievement_rate"] == "100.00"
        assert small["scoped_achievements"] == []

    def test_8_the_three_woman_targets_are_independent(self, db_path: Path) -> None:
        """TEST 8 — 공사 목표를 바꿔도 용역·물품 목표는 그대로다."""
        main(["targets", "--year", "2026", "--db", str(db_path)])
        targets = PolicyTargetRepository(db_path)
        woman = _policy_id(db_path, "WOMAN")

        targets.upsert(2026, woman, Decimal("9"), CONSTRUCTION)

        rates = {t.scope: t.target_rate for t in targets.list_for_policy(2026, woman)}
        assert rates == {
            CONSTRUCTION: Decimal("9"),
            SERVICE: Decimal("5"),
            GOODS: Decimal("5"),
        }

    def test_9_years_stay_independent(self, db_path: Path) -> None:
        """TEST 9 — 2025 년 목표를 넣어도 2026 년은 그대로다."""
        main(["targets", "--year", "2026", "--db", str(db_path)])
        targets = PolicyTargetRepository(db_path)
        woman = _policy_id(db_path, "WOMAN")

        targets.upsert(2025, woman, Decimal("1"), CONSTRUCTION)

        assert targets.get(2025, woman, CONSTRUCTION) is not None
        stored_2026 = targets.get(2026, woman, CONSTRUCTION)
        assert stored_2026 is not None
        assert stored_2026.target_rate == Decimal("3")

    def test_10_an_unregistered_policy_is_still_unknown(self, db_path: Path) -> None:
        """TEST 10 — 여성기업 기업정보가 없으면 **조회불가**. ⛔ 0% 가 아니다."""
        main(["targets", "--year", "2026", "--db", str(db_path)])
        _certify(db_path, "SMALL_BUSINESS")
        _spend(db_path, _WOMAN_BNO, "1000000", CONSTRUCTION)

        item = _woman_item(db_path)

        assert item["purchase_amount"] is None
        assert item["achievement_rate"] is None
        assert item["status_label"] == "기업정보 미등록"
        assert item["scoped_achievements"] == []


# ======================================================================
# STEP 104 §20  인증기간 경계가 **유형별 경로에서도** 지켜지는가
# ======================================================================
class TestCertificationWindowBoundaries:
    """경계는 포함이다 — ``valid_from <= 결의일자 <= valid_to``.

    이 규칙은 이미 확정되어 있고 규칙 계층에 시험이 있다. 여기서 다시 보는
    까닭은 STEP 103 이 만든 **유형별 경로**가 그 규칙을 그대로 타는지가
    새로 생긴 면이기 때문이다. 분모는 유형으로 좁히고 분자는 유형 + 인증기간
    둘 다로 좁히므로, 한쪽만 어긋나도 비율이 조용히 틀린다.
    """

    @pytest.fixture
    def bounded(self, db_path: Path) -> Path:
        """인증 유효기간이 2026-03-01 ~ 2026-03-31 인 여성기업 하나."""
        main(["targets", "--year", "2026", "--db", str(db_path)])
        _certify(
            db_path,
            "WOMAN",
            valid_from=date(2026, 3, 1),
            valid_to=date(2026, 3, 31),
        )
        for amount, day in (
            ("100000", date(2026, 3, 1)),  # 시작일 당일
            ("200000", date(2026, 3, 31)),  # 종료일 당일
            ("400000", date(2026, 4, 1)),  # 종료일 다음날
        ):
            _spend(db_path, _WOMAN_BNO, amount, CONSTRUCTION, resolution_date=day)
        return db_path

    def test_the_first_and_last_day_count(self, bounded: Path) -> None:
        """시작일 10만 + 종료일 20만 = 30만. ⛔ 다음날 40만은 들어오지 않는다."""
        assert _scoped(_woman_item(bounded))[CONSTRUCTION]["purchase_amount"] == "300000"

    def test_the_denominator_keeps_every_confirmed_row(self, bounded: Path) -> None:
        """⭐ 분모는 인증기간과 무관하다 — 공사로 확정된 3건 전부(70만).

        인증기간이 분모까지 좁히면 «여성기업이 아닌 공사 구매» 가 분모에서
        사라져 비율이 실제보다 높게 나온다.
        """
        assert _scoped(_woman_item(bounded))[CONSTRUCTION]["total_purchase_amount"] == "700000"


# ======================================================================
# §7  분모가 0 일 때
# ======================================================================
class TestZeroDenominator:
    """⛔ 나눌 것이 없을 때 숫자를 만들지 않는다."""

    def test_a_type_with_no_spend_reports_no_rate(self, db_path: Path) -> None:
        """공사 확정 건이 하나도 없으면 공사 달성률은 ``null`` — 0% 도 100% 도 아니다."""
        main(["targets", "--year", "2026", "--db", str(db_path)])
        _certify(db_path, "WOMAN")
        _spend(db_path, _WOMAN_BNO, "1000000", SERVICE)

        scoped = _scoped(_woman_item(db_path))

        assert scoped[CONSTRUCTION]["total_purchase_amount"] == "0"
        assert scoped[CONSTRUCTION]["achievement_rate"] is None
        assert scoped[CONSTRUCTION]["status_label"] == "계산 보류"

    def test_the_other_types_still_calculate(self, db_path: Path) -> None:
        """한 유형이 비어도 나머지는 정상 계산된다."""
        main(["targets", "--year", "2026", "--db", str(db_path)])
        _certify(db_path, "WOMAN")
        _spend(db_path, _WOMAN_BNO, "1000000", SERVICE)

        assert _scoped(_woman_item(db_path))[SERVICE]["achievement_rate"] == "2000.00"


# ======================================================================
# STEP 104 §17  화면이 세 결과를 **잃지 않는가**
# ======================================================================
class TestTheScreenShowsAllThree:
    """⛔ 공사 3% 가 화면에서 사라지면 안 된다.

    서버가 세 결과를 내보내도 화면이 그리지 않으면 담당자에게는 없는 것과
    같다. STEP 104 §17 에서 실제로 그 상태였고, 이 시험이 그 회귀를 막는다.
    """

    @pytest.fixture
    def page(self, db_path: Path) -> str:
        html: str = TestClient(create_app(db_path)).get("/").text
        return html

    def test_the_dashboard_draws_the_scoped_table(self, page: str) -> None:
        """정책 카드가 ``scoped_achievements`` 를 그린다."""
        assert "function scopedTable" in page
        assert "item.scoped_achievements" in page
        assert "구매유형별 달성률" in page

    def test_the_scoped_table_shows_target_and_rate(self, page: str) -> None:
        """유형마다 **목표와 달성률을 함께** 적는다 — 목표가 빠지면 3% 가 사라진다."""
        table = page.split("function scopedTable")[1].split("function policyCard")[0]

        assert "scoped.target_rate" in table
        assert "scoped.achievement_rate" in table
        assert "scoped.purchase_amount" in table
        assert "scoped.scope_label" in table

    def test_an_uncalculated_type_shows_its_status_not_zero(self, page: str) -> None:
        """⛔ 분모를 못 구한 유형에 0% 를 적지 않는다 — 상태를 적는다."""
        table = page.split("function scopedTable")[1].split("function policyCard")[0]

        assert "scoped.achievement_rate === null" in table
        assert "scoped.status_label" in table

    def test_the_target_screen_still_shows_all_three(self, page: str) -> None:
        """목표율 화면도 세 값을 모두 보여 준다(STEP 103 §13)."""
        assert "ptScopedRow" in page
        assert "item.scoped_targets" in page


# ======================================================================
# §4 · §17  하지 않기로 한 것
# ======================================================================
class TestForbidden:
    """⛔ 금지 목록."""

    def test_no_purchase_type_was_guessed(self) -> None:
        """⛔ 적요·예산과목·거래처명으로 유형을 정하는 코드가 없다."""
        src = Path(__file__).resolve().parents[1] / "src" / "procurement"
        for area in ("calculators", "dashboard", "matchers"):
            for path in (src / area).rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                # 유형 낱말이 나오더라도 **판정**이 아니라 값 통과여야 한다.
                assert "description" not in text or "final_purchase_type" not in text, path.name

    def test_self_support_village_is_still_on_hold(self, db_path: Path) -> None:
        """⛔ §15 — 자활용사촌을 구현하지 않았다."""
        main(["targets", "--year", "2026", "--db", str(db_path)])
        _certify(db_path, "SELF_SUPPORT_VILLAGE")
        _spend(db_path, _WOMAN_BNO, "1000000", GOODS)

        policies = _summary(db_path)["policies"]
        assert isinstance(policies, list)
        village = next(i for i in policies if i["policy_code"] == "SELF_SUPPORT_VILLAGE")

        assert village["achievement_rate"] is None
        assert village["status_label"] == "계산 보류"
        assert village["scoped_achievements"] == []

    def test_producible_items_is_still_not_calculable(self) -> None:
        """⛔ 품목 분모를 열지 않았다."""
        assert PRODUCIBLE_ITEMS not in CALCULABLE_SCOPES
        assert CALCULABLE_SCOPES == {TOTAL, CONSTRUCTION, SERVICE, GOODS}

    def test_scoped_targets_do_not_leak_into_the_total_path(self, db_path: Path) -> None:
        """⭐ 유형별 목표가 **총 구매금액 기준 경로로 새지 않는다.**

        이 STEP 에서 실제로 났던 결함이다. 계산 가능한 분모 기준에 구매유형이
        더해지자, 「계산 가능한 목표」 를 모으는 기존 경로가 여성기업 3% 까지
        함께 집어 갔다. 그대로 두면 여성기업 목표가 **기관 전체 구매금액**을
        분모로 계산되어 화면에 틀린 달성률이 나간다. 한 정책에 유형이 셋이라
        dict 에 담기면서 둘이 사라지기도 했다.
        """
        main(["targets", "--year", "2026", "--db", str(db_path)])
        targets = PolicyTargetRepository(db_path)
        woman = _policy_id(db_path, "WOMAN")

        assert woman not in targets.rates_by_policy_id(2026)
        assert set(targets.scoped_rates_by_policy_id(2026)[woman]) == {
            CONSTRUCTION,
            SERVICE,
            GOODS,
        }

    def test_the_calculator_gained_no_new_method(self) -> None:
        """⛔ §17 최소 변경 — 새 계산 메서드를 만들지 않고 인자만 넓혔다."""
        names = {n for n in dir(ProcurementAchievementCalculator) if n.startswith("calculate")}
        assert names == {
            "calculate_total_purchase",
            "calculate_policy_purchase",
            "calculate_achievement",
            "calculate_all",
        }

    def test_an_old_database_counts_nothing_by_type(self, tmp_path: Path) -> None:
        """⛔ 검토 테이블이 없는 구 스키마에서 유형별 분모가 **열려 버리면 안 된다.**

        확정값이 존재할 수 없으므로 0 이어야 한다. 반대로 «전부 포함» 으로
        열리면 유형이 확정된 적 없는 데이터가 통째로 분모에 들어간다.
        """
        path = tmp_path / "old.db"
        bootstrap(path)  # ⛔ ReviewRepository.create_table() 을 부르지 않는다
        _certify(path, "WOMAN")
        PurchaseRepository(path).insert(
            Purchase(
                business_no=_WOMAN_BNO,
                company_name="합성상사",
                amount=Decimal("1000000"),
                payment_date=date(2026, 3, 5),
                contract_date=date(2026, 3, 5),
                resolution_date=date(2026, 3, 5),
            )
        )

        calculator = _calculator(path)

        assert calculator.calculate_total_purchase(None, CONSTRUCTION) == Decimal("0")
        assert calculator.calculate_total_purchase() == Decimal("1000000")
