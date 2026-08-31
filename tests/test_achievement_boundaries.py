"""
STEP 67 — 달성률 계산의 **경계조건**을 잠급니다.

이 파일은 계산을 바꾸지 않습니다. **지금 무엇을 계산하고 있는지**를 실행
가능한 형태로 고정합니다. 마감이 가까울수록 "이 숫자가 무엇인지" 가 흔들리지
않는 것이 중요하기 때문입니다.

세 가지를 **구분해서** 잠급니다
================================

.. list-table::
   :header-rows: 1

   * - 구분
     - 이 파일에서
   * - **A. 고객이 확정한 업무규칙**
     - 반드시 잠근다 (창업기업 OR 규칙 · 거래처 이력은 참고정보)
   * - **B. 현재 시스템의 기술적 동작**
     - 사실대로 기록한다 (배치 · 기간 · 인증 경계)
   * - **C. 고객 미확정 업무규칙**
     - ⛔ **잠그지 않는다.** "아직 구현되지 않았다" 만 확인한다

.. warning::
    ⛔ **W-16(단기 차량 임차) · W-17(실적 제외) · Q5-3(결의서 묶음)을 이
    파일에서 구현하거나 확정하지 않습니다.** :class:`TestUnconfirmedRulesAreNotImplemented`
    는 그것들이 **없다는 사실**을 확인할 뿐이며, 고객이 답하면 그 시험이
    바뀌는 것이 정상입니다.

.. warning::
    ⚠️ **일반 정책(중소·여성·장애인)의 인증 판정 기준일은 현재 ``payment_date``
    입니다.** 이는 **고객이 확정한 규칙이 아니라 현행 동작**이며, W-1-2(Q-A)가
    🔴 미확정으로 남아 있습니다. 그래서 이 파일은 그것을 "**현재 동작**" 으로만
    적고 "확정 규칙" 으로 적지 않습니다. 고객이 결의일자 기준으로 답하면 이
    시험은 **바뀌어야 합니다.**

    확정된 것은 **창업기업**뿐입니다 — 결의일자 **또는** 계약일자(§0.6.2).

.. note::
    합성 데이터만 씁니다. 실제 고객 데이터를 만들거나 커밋하지 않습니다.
"""

from __future__ import annotations

import inspect
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from procurement.calculators.procurement_achievement import ProcurementAchievementCalculator
from procurement.core.period import CONTRACT_DATE, PAYMENT_DATE, RESOLUTION_DATE, PeriodFilter
from procurement.core.purchase_type import CONSTRUCTION, GOODS, SERVICE
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.import_batch_repository import ImportBatchRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.database.review_repository import ReviewRepository
from procurement.models import Certification, Company, Purchase
from procurement.models.import_batch import STATUS_ACTIVE, ImportBatch

# 합성 사업자등록번호 — 실제 업체의 번호가 아닙니다.
_CERTIFIED = "1000000001"
_PLAIN = "1000000002"

#: 인증 유효기간. 경계 시험은 이 두 날짜를 그대로 씁니다.
_VALID_FROM = date(2026, 3, 1)
_VALID_TO = date(2026, 3, 31)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "boundaries.db"
    init_db(path)
    seed_policies(path)
    return path


@pytest.fixture
def calculator(db: Path) -> ProcurementAchievementCalculator:
    return ProcurementAchievementCalculator(
        PurchaseRepository(db), CertificationRepository(db), PolicyRepository(db)
    )


def _policy_id(db: Path, code: str) -> int:
    policy = PolicyRepository(db).find_by_policy_code(code)
    assert policy is not None and policy.policy_id is not None
    return policy.policy_id


def _certified_company(db: Path, code: str = "SMALL_BUSINESS") -> int:
    """인증기업 1곳을 만들고 ``company_id`` 를 돌려줍니다."""
    companies = CompanyRepository(db)
    company = companies.find_by_business_no(_CERTIFIED)
    if company is None:
        company = companies.insert(
            Company(
                business_no=_CERTIFIED, company_name="합성기업 가", representative_name="홍길동"
            )
        )
    assert company.company_id is not None
    CertificationRepository(db).insert(
        Certification(
            company_id=company.company_id,
            policy_id=_policy_id(db, code),
            valid_from=_VALID_FROM,
            valid_to=_VALID_TO,
        )
    )
    return company.company_id


def _add(
    db: Path,
    amount: str,
    *,
    company_id: int | None = None,
    payment: date = date(2026, 3, 15),
    contract: date = date(2026, 3, 15),
    resolution: date | None = date(2026, 3, 15),
    batch_id: int | None = None,
) -> int:
    saved = PurchaseRepository(db).insert(
        Purchase(
            business_no=_CERTIFIED if company_id else _PLAIN,
            company_name="합성기업 가" if company_id else "합성기업 나",
            contract_date=contract,
            payment_date=payment,
            resolution_date=resolution,
            amount=Decimal(amount),
            company_id=company_id,
            batch_id=batch_id,
            description="합성 적요",
        )
    )
    assert saved.purchase_id is not None
    return saved.purchase_id


def _batch(db: Path, status: str = STATUS_ACTIVE) -> int:
    saved = ImportBatchRepository(db).insert(
        ImportBatch(
            file_name="synthetic.xlsx",
            period_start=date(2026, 1, 1),
            period_end=date(2026, 12, 31),
            row_count=1,
            status=status,
        )
    )
    assert saved.batch_id is not None
    return saved.batch_id


# ======================================================================
# Test 1 — 배치 상태 (구분 B)
# ======================================================================
class TestBatchStatus:
    """계산 대상은 **ACTIVE 배치 또는 배치 없음** 이다."""

    def test_active_batch_is_counted(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        _add(db, "1000", batch_id=_batch(db))
        assert calculator.calculate_total_purchase() == Decimal("1000")

    def test_batchless_row_is_counted(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        """배치 도입 이전 행(``batch_id`` 없음)이 갑자기 사라지지 않는다."""
        _add(db, "1000", batch_id=None)
        assert calculator.calculate_total_purchase() == Decimal("1000")

    def test_superseded_batch_is_excluded(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        old, new = _batch(db), _batch(db)
        _add(db, "900", batch_id=old)
        _add(db, "100", batch_id=new)
        ImportBatchRepository(db).supersede(old, superseded_by=new)

        assert calculator.calculate_total_purchase() == Decimal("100")

    def test_reupload_does_not_double_count(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        """⭐ 같은 기간을 다시 올려도 **합쳐지지 않는다.**

        대체된 배치가 남아 있어도 계산에는 새 배치만 들어간다. 이것이 깨지면
        재업로드할 때마다 실적이 부풀어 오른다.
        """
        first = _batch(db)
        _add(db, "500", batch_id=first)
        _add(db, "500", batch_id=first)
        assert calculator.calculate_total_purchase() == Decimal("1000")

        second = _batch(db)
        _add(db, "700", batch_id=second)
        ImportBatchRepository(db).supersede(first, superseded_by=second)

        assert calculator.calculate_total_purchase() == Decimal("700")

    def test_superseded_rows_are_not_deleted(self, db: Path) -> None:
        """⛔ 대체는 **계산에서 빼는 것**이지 행을 지우는 것이 아니다."""
        old, new = _batch(db), _batch(db)
        _add(db, "900", batch_id=old)
        _add(db, "100", batch_id=new)
        ImportBatchRepository(db).supersede(old, superseded_by=new)

        assert len(PurchaseRepository(db).find_all()) == 2


# ======================================================================
# Test 2 — 기간 경계 (구분 B)
# ======================================================================
class TestPeriodBoundary:
    """기간은 **양 끝을 포함**하고, 분모·분자에 **같이** 적용된다."""

    @pytest.mark.parametrize(
        ("day", "expected"),
        [
            (date(2026, 1, 1), "1000"),  # 시작일 당일 — 포함
            (date(2026, 12, 31), "1000"),  # 종료일 당일 — 포함
            (date(2025, 12, 31), "0"),  # 하루 앞 — 제외
            (date(2027, 1, 1), "0"),  # 하루 뒤 — 제외
        ],
    )
    def test_year_boundary_is_inclusive(
        self,
        db: Path,
        calculator: ProcurementAchievementCalculator,
        day: date,
        expected: str,
    ) -> None:
        _add(db, "1000", payment=day)
        period = PeriodFilter.for_year(2026, PAYMENT_DATE)
        assert calculator.calculate_total_purchase(period) == Decimal(expected)

    def test_null_date_row_drops_out(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        """기준 날짜가 비어 있으면 그 행은 기간 조회에서 빠진다.

        ⛔ 다른 날짜로 대체하지 않는다(§0.7.2). 이 사실은 화면에 **건수로
        표시**된다(STEP 59~61).
        """
        _add(db, "1000", resolution=None)
        period = PeriodFilter.for_year(2026, RESOLUTION_DATE)
        assert calculator.calculate_total_purchase(period) == Decimal("0")
        assert PurchaseRepository(db).count_missing_resolution_date() == (1, Decimal("1000"))

    def test_same_period_applies_to_both_sides(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        """⭐ 분모와 분자에 **같은 기간**이 걸린다 — 한쪽만 걸리면 비율이 거짓이 된다."""
        company_id = _certified_company(db)
        policy_id = _policy_id(db, "SMALL_BUSINESS")
        _add(db, "300", company_id=company_id, payment=date(2026, 3, 10))
        _add(db, "700", payment=date(2026, 3, 10))
        _add(db, "999", company_id=company_id, payment=date(2025, 3, 10))  # 다른 해

        period = PeriodFilter.for_year(2026, PAYMENT_DATE)
        assert calculator.calculate_total_purchase(period) == Decimal("1000")
        assert calculator.calculate_policy_purchase(policy_id, period) == Decimal("300")

    def test_no_period_means_no_limit(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        _add(db, "300", payment=date(2020, 1, 1))
        _add(db, "700", payment=date(2030, 1, 1))
        assert calculator.calculate_total_purchase(None) == Decimal("1000")


# ======================================================================
# Test 3 · 4 — 인증 유효기간과 인증기업 여부
# ======================================================================
class TestCertificationValidity:
    """인증 유효기간은 **양 끝을 포함**한다(현행 동작 · 구분 B).

    .. warning::
        ⚠️ 여기서 쓰는 기준일이 ``payment_date`` 인 것은 **현행 동작**이며,
        고객이 확정한 규칙이 아닙니다(W-1-2 · Q-A 🔴 미확정). 이 시험은
        "지금 이렇게 동작한다" 를 기록할 뿐, "이것이 옳다" 고 말하지 않습니다.
    """

    @pytest.mark.parametrize(
        ("day", "expected"),
        [
            (_VALID_FROM, "1000"),  # 시작일 당일 — 포함
            (_VALID_TO, "1000"),  # 종료일 당일 — 포함
            (date(2026, 2, 28), "0"),  # 시작일 하루 앞 — 제외
            (date(2026, 4, 1), "0"),  # 종료일 하루 뒤 — 제외
        ],
    )
    def test_validity_boundary_is_inclusive(
        self,
        db: Path,
        calculator: ProcurementAchievementCalculator,
        day: date,
        expected: str,
    ) -> None:
        company_id = _certified_company(db)
        _add(db, "1000", company_id=company_id, payment=day)
        assert calculator.calculate_policy_purchase(_policy_id(db, "SMALL_BUSINESS")) == Decimal(
            expected
        )

    def test_uncertified_company_is_not_in_the_numerator(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        """비인증기업은 분자에 들어가지 않는다 — **분모에는 들어간다.**"""
        _certified_company(db)
        _add(db, "700")  # company_id 없음
        assert calculator.calculate_policy_purchase(_policy_id(db, "SMALL_BUSINESS")) == Decimal(
            "0"
        )
        assert calculator.calculate_total_purchase() == Decimal("700")

    def test_certification_of_another_policy_does_not_count(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        """중소기업 인증이 여성기업 실적이 되지 않는다."""
        company_id = _certified_company(db, "SMALL_BUSINESS")
        _add(db, "1000", company_id=company_id)
        assert calculator.calculate_policy_purchase(_policy_id(db, "WOMAN")) == Decimal("0")

    def test_certification_dates_cannot_be_null(self, db: Path) -> None:
        """인증 유효기간은 **비어 있을 수 없다** — 경계 판정에 빈 값이 오지 않는다."""
        columns = CertificationRepository(db).execute("PRAGMA table_info(certification)")
        notnull = {row["name"]: row["notnull"] for row in columns}
        assert notnull["valid_from"] == 1
        assert notnull["valid_to"] == 1


class TestStartupOrRule:
    """⭐ **고객 확정 규칙**(구분 A) — 창업기업은 결의일자 **또는** 계약일자.

    2026-08-14 고객 확정(§0.6.2). 한쪽만 유효기간에 들어도 인정합니다.
    """

    @pytest.mark.parametrize(
        ("resolution", "contract", "expected"),
        [
            (date(2026, 3, 10), date(2026, 3, 20), "1000"),  # 둘 다 안 → 인정
            (date(2026, 3, 10), date(2026, 6, 1), "1000"),  # 결의일자만 안 → 인정
            (date(2026, 6, 1), date(2026, 3, 20), "1000"),  # 계약일자만 안 → 인정
            (date(2026, 6, 1), date(2026, 6, 1), "0"),  # 둘 다 밖 → 불인정
        ],
    )
    def test_either_date_is_enough(
        self,
        db: Path,
        calculator: ProcurementAchievementCalculator,
        resolution: date,
        contract: date,
        expected: str,
    ) -> None:
        company_id = _certified_company(db, "STARTUP")
        _add(
            db,
            "1000",
            company_id=company_id,
            resolution=resolution,
            contract=contract,
            payment=date(2026, 9, 9),  # ⛔ 지급일은 보지 않는다
        )
        assert calculator.calculate_policy_purchase(_policy_id(db, "STARTUP")) == Decimal(expected)

    def test_payment_date_is_ignored(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        """⛔ 창업기업 판정에 ``payment_date`` 를 쓰지 않는다(2026-08-15 PM 결정)."""
        company_id = _certified_company(db, "STARTUP")
        _add(
            db,
            "1000",
            company_id=company_id,
            resolution=date(2026, 6, 1),
            contract=date(2026, 6, 1),
            payment=_VALID_FROM,  # 지급일만 유효기간 안
        )
        assert calculator.calculate_policy_purchase(_policy_id(db, "STARTUP")) == Decimal("0")

    def test_missing_resolution_falls_back_to_contract_only(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        """결의일자가 없으면 **계약일자만으로** 판정한다.

        ⛔ 빈 결의일자를 지급일로 대체하지 않는다 — PM 이 명시적으로 금지한
        "``payment_date`` 를 결의일자로 재정의" 가 되기 때문이다.
        """
        company_id = _certified_company(db, "STARTUP")
        _add(
            db,
            "1000",
            company_id=company_id,
            resolution=None,
            contract=date(2026, 3, 20),
            payment=date(2026, 9, 9),
        )
        assert calculator.calculate_policy_purchase(_policy_id(db, "STARTUP")) == Decimal("1000")


class TestGeneralPolicyBasisIsCurrentBehaviour:
    """⚠️ 일반 정책의 기준일 — **현행 동작**이며 확정 규칙이 아니다(구분 B).

    W-1-2(Q-A)가 🔴 미확정입니다. 고객이 *"결의일자 기준"* 이 인증 유효기간
    판정까지 포함한다고 답하면 **이 시험이 바뀌어야 합니다.** 그때 이 클래스가
    깨지는 것이 정상이며, 그것이 곧 "무엇이 달라지는가" 의 알림입니다.
    """

    @pytest.mark.parametrize("code", ["SMALL_BUSINESS", "WOMAN", "DISABLED"])
    def test_general_policies_use_payment_date_today(self, db: Path, code: str) -> None:
        policy = PolicyRepository(db).find_by_policy_code(code)
        assert policy is not None
        assert policy.evaluation_basis == "PAYMENT_DATE"

    def test_startup_uses_the_confirmed_or_rule(self, db: Path) -> None:
        policy = PolicyRepository(db).find_by_policy_code("STARTUP")
        assert policy is not None
        assert policy.evaluation_basis == "RESOLUTION_OR_CONTRACT_DATE"

    def test_resolution_date_does_not_decide_general_policies_yet(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        """결의일자만 유효기간 안이면 **현재는 인정되지 않는다.**

        ⛔ 이것을 "옳다" 고 말하는 시험이 아니다. W-1-2 가 확정되기 전의
        **현재 상태**를 적어 두어, 바뀔 때 조용히 지나가지 않게 한다.
        """
        company_id = _certified_company(db, "SMALL_BUSINESS")
        _add(
            db,
            "1000",
            company_id=company_id,
            resolution=_VALID_FROM,  # 유효기간 안
            payment=date(2026, 9, 9),  # 유효기간 밖
        )
        assert calculator.calculate_policy_purchase(_policy_id(db, "SMALL_BUSINESS")) == Decimal(
            "0"
        )


# ======================================================================
# Test 5 · 6 · 7 — 검토 · 구매유형 · 거래처 이력은 계산에 닿지 않는다
# ======================================================================
class TestCalculatorIsIsolatedFromReview:
    """⭐ **구분 A** — 참고정보 · 담당자 판정 · 계산 대상은 서로 다른 것이다.

    이 경계가 무너지면 담당자가 검토 화면에서 무엇을 누르는 순간 실적 숫자가
    따라 움직이게 됩니다. 고객이 실적 제외 기준(W-17)을 확정하기 전에는
    그런 일이 일어나서는 안 됩니다.
    """

    def _seed(self, db: Path) -> tuple[int, int]:
        company_id = _certified_company(db)
        purchase_id = _add(db, "300", company_id=company_id)
        _add(db, "700")
        return purchase_id, _policy_id(db, "SMALL_BUSINESS")

    def test_calculator_does_not_take_a_review_repository(self) -> None:
        """⭐ **구조적 사실** — 계산기는 검토 저장소를 받지 않는다.

        인자에 없으면 검토 결과가 계산에 닿을 길이 애초에 없다.
        """
        params = inspect.signature(ProcurementAchievementCalculator.__init__).parameters
        assert set(params) == {
            "self",
            "purchase_repository",
            "certification_repository",
            "policy_repository",
            "rule_registry",
        }
        assert not any("review" in name for name in params)

    @pytest.mark.parametrize("status_flow", [["PENDING"], ["CONFIRMED"], ["REOPENED"]])
    def test_review_status_does_not_move_the_numbers(
        self,
        db: Path,
        calculator: ProcurementAchievementCalculator,
        status_flow: list[str],
    ) -> None:
        purchase_id, policy_id = self._seed(db)
        before = (
            calculator.calculate_total_purchase(),
            calculator.calculate_policy_purchase(policy_id),
        )

        reviews = ReviewRepository(db)
        if "CONFIRMED" in status_flow or "REOPENED" in status_flow:
            reviews.confirm(
                purchase_id, final_purchase_type=GOODS, reviewed_by="담당자", review_note=None
            )
        if "REOPENED" in status_flow:
            reviews.reopen(purchase_id, reopened_by="담당자", note=None)

        after = (
            calculator.calculate_total_purchase(),
            calculator.calculate_policy_purchase(policy_id),
        )
        assert after == before

    @pytest.mark.parametrize("purchase_type", [None, GOODS, SERVICE, CONSTRUCTION])
    def test_purchase_type_does_not_move_the_numbers(
        self,
        db: Path,
        calculator: ProcurementAchievementCalculator,
        purchase_type: str | None,
    ) -> None:
        """⛔ 구매유형을 무엇으로 확정해도 분모·분자가 그대로다.

        고객이 W-17(실적 제외 기준)을 확정하기 전까지 구매유형은 실적 산입
        조건이 아니다.
        """
        purchase_id, policy_id = self._seed(db)
        before = (
            calculator.calculate_total_purchase(),
            calculator.calculate_policy_purchase(policy_id),
        )

        ReviewRepository(db).confirm(
            purchase_id,
            final_purchase_type=purchase_type,
            reviewed_by="담당자",
            review_note=None,
        )

        after = (
            calculator.calculate_total_purchase(),
            calculator.calculate_policy_purchase(policy_id),
        )
        assert after == before

    def test_company_history_does_not_move_the_numbers(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        """⭐ 같은 사업자번호의 과거 확정 이력이 쌓여도 계산은 그대로다(STEP 64).

        거래처 과거 이력은 **참고정보**이며, 현재 거래의 유형이나 실적 산입을
        자동으로 정하지 않는다(§0.9.5 원칙 5).
        """
        company_id = _certified_company(db)
        current = _add(db, "300", company_id=company_id)
        _add(db, "700")
        policy_id = _policy_id(db, "SMALL_BUSINESS")
        before = (
            calculator.calculate_total_purchase(),
            calculator.calculate_policy_purchase(policy_id),
        )

        # 같은 사업자번호로 공사·용역·물품 이력을 잔뜩 쌓는다.
        reviews = ReviewRepository(db)
        for purchase_type in (CONSTRUCTION, CONSTRUCTION, SERVICE, GOODS):
            past = _add(db, "0.01", company_id=company_id)
            reviews.confirm(
                past, final_purchase_type=purchase_type, reviewed_by="담당자", review_note=None
            )

        # 이력 때문이 아니라 **행이 늘어서** 분모가 는다 — 그만큼만 는다.
        assert calculator.calculate_total_purchase() == before[0] + Decimal("0.04")
        assert calculator.calculate_policy_purchase(policy_id) == before[1] + Decimal("0.04")
        assert current is not None

    def test_company_name_change_does_not_move_the_numbers(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        """거래처명이 달라도 계산은 사업자번호·인증으로만 정해진다."""
        company_id = _certified_company(db)
        _add(db, "300", company_id=company_id)
        policy_id = _policy_id(db, "SMALL_BUSINESS")
        before = calculator.calculate_policy_purchase(policy_id)

        PurchaseRepository(db).execute(
            "UPDATE purchase SET company_name = ? WHERE company_id = ?",
            ("전혀 다른 이름", company_id),
        )

        assert calculator.calculate_policy_purchase(policy_id) == before


# ======================================================================
# Test 8 — 미확정 업무규칙은 구현되어 있지 않다 (구분 C)
# ======================================================================
class TestUnconfirmedRulesAreNotImplemented:
    """⛔ 고객이 답하지 않은 규칙이 **몰래 들어와 있지 않은지** 확인한다.

    .. note::
        이 클래스는 "이 동작이 옳다" 를 잠그는 것이 아닙니다. 고객이 답하면
        **바뀌어야 하는** 시험이며, 그때 깨지는 것이 정상입니다.
    """

    def test_w16_vehicle_lease_is_not_excluded(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        """W-16 미구현 — 적요에 임차·렌트가 있어도 지금은 빠지지 않는다."""
        for text in ("업무용 차량 장기 임차료", "출장 차량 1일 렌트", "렌터카 이용료"):
            saved = PurchaseRepository(db).insert(
                Purchase(
                    business_no=_PLAIN,
                    company_name="합성기업 나",
                    contract_date=date(2026, 3, 1),
                    payment_date=date(2026, 3, 1),
                    resolution_date=date(2026, 3, 1),
                    amount=Decimal("1000"),
                    description=text,
                    budget_account="임차료",
                )
            )
            assert saved.purchase_id is not None

        assert calculator.calculate_total_purchase() == Decimal("3000")

    def test_w16_lease_period_field_does_not_exist(self, db: Path) -> None:
        """임차 기간을 담을 자리가 없다 — 단기/장기를 데이터로 가릴 수 없다."""
        columns = {
            row["name"] for row in PurchaseRepository(db).execute("PRAGMA table_info(purchase)")
        }
        for absent in ("lease_start", "lease_end", "lease_period", "contract_period"):
            assert absent not in columns

    def test_w17_education_rows_are_not_excluded(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        """W-17 미구현 — 고객이 언급한 적요라도 지금은 실적에 그대로 들어간다."""
        for text in (
            "민원 담당자 교육(교육비, 임차료, 다과비)",
            "민원 담당자 교육 2차(강사비, 재료비, 다과비, 임차료)",
        ):
            saved = PurchaseRepository(db).insert(
                Purchase(
                    business_no=_PLAIN,
                    company_name="합성기업 나",
                    contract_date=date(2026, 3, 1),
                    payment_date=date(2026, 3, 1),
                    resolution_date=date(2026, 3, 1),
                    amount=Decimal("1000"),
                    description=text,
                    budget_account="행사운영비",
                )
            )
            assert saved.purchase_id is not None

        assert calculator.calculate_total_purchase() == Decimal("2000")

    def test_w17_no_row_level_exclusion_state_exists(self, db: Path) -> None:
        """⭐ 행 단위 **실적 제외 상태가 없다**(STEP 66 조사 결과를 코드로 재확인).

        ⛔ 이 시험은 "없어야 한다" 가 아니라 "**지금 없다**" 를 적는다. 고객이
        W-17-4 에 ②(원본 보존 + 제외 표시)로 답하면 이 시험이 바뀐다.
        """
        columns = {
            row["name"] for row in PurchaseRepository(db).execute("PRAGMA table_info(purchase)")
        }
        for absent in ("excluded", "is_excluded", "exclusion_reason", "counts_toward_target"):
            assert absent not in columns

    def test_q5_3_no_resolution_document_identifier(self, db: Path) -> None:
        """Q5-3 미구현 — 지출결의서를 식별할 값이 없다."""
        columns = {
            row["name"] for row in PurchaseRepository(db).execute("PRAGMA table_info(purchase)")
        }
        for absent in ("resolution_no", "resolution_number", "document_no", "voucher_no"):
            assert absent not in columns

    def test_description_is_never_a_calculation_condition(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        """⭐ 적요를 바꿔도 계산이 달라지지 않는다.

        W-16·W-17 이 **적요 낱말로 구현되어 버리는 것**을 막는 시험이다.
        §0.9.5 원칙 1(적요 낱말 단독 확정 금지)의 계산 쪽 대응물이다.
        """
        company_id = _certified_company(db)
        _add(db, "300", company_id=company_id)
        _add(db, "700")
        policy_id = _policy_id(db, "SMALL_BUSINESS")
        before = (
            calculator.calculate_total_purchase(),
            calculator.calculate_policy_purchase(policy_id),
        )

        PurchaseRepository(db).execute(
            "UPDATE purchase SET description = ?", ("단기 렌트 · 민원 담당자 교육 · 임차",)
        )

        after = (
            calculator.calculate_total_purchase(),
            calculator.calculate_policy_purchase(policy_id),
        )
        assert after == before

    def test_budget_account_is_never_a_calculation_condition(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        """예산과목을 바꿔도 계산이 달라지지 않는다(§0.9.5 원칙 2 의 계산 쪽 대응)."""
        company_id = _certified_company(db)
        _add(db, "300", company_id=company_id)
        policy_id = _policy_id(db, "SMALL_BUSINESS")
        before = calculator.calculate_policy_purchase(policy_id)

        PurchaseRepository(db).execute("UPDATE purchase SET budget_account = ?", ("각종수수료",))

        assert calculator.calculate_policy_purchase(policy_id) == before


# ======================================================================
# 금액 — 현재 적재 규칙 (구분 B)
# ======================================================================
class TestAmountRule:
    """금액은 **그대로 더한다.** 0 이하는 애초에 적재되지 않는다."""

    def test_amount_is_summed_as_is(
        self, db: Path, calculator: ProcurementAchievementCalculator
    ) -> None:
        _add(db, "1234567.89")
        _add(db, "0.11")
        assert calculator.calculate_total_purchase() == Decimal("1234568.00")

    @pytest.mark.parametrize("amount", ["0", "-1"])
    def test_non_positive_amount_cannot_be_stored(self, db: Path, amount: str) -> None:
        """0 이하 금액은 저장 단계에서 막힌다 — 계산이 보는 일이 없다.

        ⚠️ 이 행들은 "제외" 나 "무효" 가 아니라 **원본에는 있으나 현재 검토
        대상 DB 에 적재되지 않은 행**이다(Q5-8 🔴 미확정). 처리 규칙을 여기서
        정하지 않는다.
        """
        from procurement.database.purchase_repository import PurchaseValidationError

        with pytest.raises(PurchaseValidationError):
            PurchaseRepository(db).insert(
                Purchase(
                    business_no=_PLAIN,
                    company_name="합성기업 나",
                    contract_date=date(2026, 3, 1),
                    payment_date=date(2026, 3, 1),
                    amount=Decimal(amount),
                )
            )


# ======================================================================
# 회귀 — 분모·분자·달성률이 함께 맞는가
# ======================================================================
class TestAchievementRegression:
    """⭐ 분모 · 분자 · 달성률을 **한 시나리오에서 함께** 확인한다."""

    @pytest.fixture
    def scenario(self, db: Path) -> int:
        """인증기업 300 + 미인증 700 = 1,000. 목표율 30%."""
        company_id = _certified_company(db)
        _add(db, "300", company_id=company_id)
        _add(db, "700")
        PolicyRepository(db).update_target_rate("SMALL_BUSINESS", Decimal("30"))
        return _policy_id(db, "SMALL_BUSINESS")

    def test_numbers_hold_together(
        self, db: Path, calculator: ProcurementAchievementCalculator, scenario: int
    ) -> None:
        result = calculator.calculate_achievement(scenario, Decimal("30"))
        assert result.total_purchase_amount == Decimal("1000")
        assert result.purchase_amount == Decimal("300")
        # 구매비율 30% ÷ 목표 30% = 달성률 100%
        assert result.achievement_rate == Decimal("100.00")

    def test_period_scoped_numbers_hold_together(
        self, db: Path, calculator: ProcurementAchievementCalculator, scenario: int
    ) -> None:
        period = PeriodFilter.for_year(2026, PAYMENT_DATE)
        result = calculator.calculate_achievement(scenario, Decimal("30"), period)
        assert result.total_purchase_amount == Decimal("1000")
        assert result.purchase_amount == Decimal("300")
        assert result.achievement_rate == Decimal("100.00")

    def test_contract_date_period_gives_the_same_here(
        self, db: Path, calculator: ProcurementAchievementCalculator, scenario: int
    ) -> None:
        """기준일을 바꿔도 이 시나리오에서는 같다 — 날짜가 모두 같은 해이기 때문."""
        by_payment = calculator.calculate_achievement(
            scenario, Decimal("30"), PeriodFilter.for_year(2026, PAYMENT_DATE)
        )
        by_contract = calculator.calculate_achievement(
            scenario, Decimal("30"), PeriodFilter.for_year(2026, CONTRACT_DATE)
        )
        assert by_payment.achievement_rate == by_contract.achievement_rate
