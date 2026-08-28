"""
결의일자가 비어 있는 구매 건을 **보여주기만** 하는 기능을 고정합니다(STEP 59).

무엇을 지키는 시험인가
======================

결의일자(``resolution_date``)는 구매실적의 연도 귀속 기준일입니다. 값이 비어
있는 행은 결의일자 기준 집계에 들어갈 수 없습니다 — 어느 해에 넣어야 할지
알 수 없기 때문입니다. 그런 행이 **몇 건 · 얼마인지 화면에 알리는 것**이
이번 기능의 전부입니다.

.. warning::
    ⛔ **판정하지 않습니다.** 이 건들을 "제외"·"무효"·"오류 데이터" 로
    분류하지 않고, 어떤 날짜로도 대체하지 않습니다. 비어 있다는 사실만
    셉니다.

.. warning::
    ⛔ **달성률을 바꾸지 않습니다.** 이 파일에서 가장 중요한 시험은
    :class:`TestAchievementUnchanged` 입니다. 분모·분자·달성률이 이 기능이
    있기 전과 **완전히 동일**해야 합니다.

기간 조건에 대해
================

집계에는 **기간을 적용하지 않습니다.** 결의일자가 비어 있는 행에 결의일자
범위 조건을 걸면 정의상 항상 0 건이 되어 알림 자체가 성립하지 않기
때문입니다. 대신 계산 경로(``find_for_calculation``)와 **같은 배치 조건**
(ACTIVE 배치 또는 배치 없음)만 적용합니다.

조회 기준일이 결의일자가 **아닌** 경우(지급일·계약일 기준)에는 애초에
결의일자 공란이 집계에서 빠지는 원인이 아니므로 알리지 않습니다
(``applies=False``).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.calculators.procurement_achievement import ProcurementAchievementCalculator
from procurement.core.period import CONTRACT_DATE, PAYMENT_DATE, RESOLUTION_DATE, PeriodFilter
from procurement.dashboard.data_service import DashboardDataService
from procurement.dashboard.models import DashboardSummary, MissingResolutionDate
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Certification, Company, Purchase

# 인증기업 / 미인증기업 — 합성 사업자번호입니다(실제 고객 데이터가 아닙니다).
CERTIFIED_NO = "1000000001"
PLAIN_NO = "1000000002"


def _purchase(
    business_no: str,
    amount: str,
    *,
    resolution: date | None,
    company_id: int | None = None,
) -> Purchase:
    """시험용 구매 행. 결의일자만 바꿔가며 넣습니다."""
    return Purchase(
        business_no=business_no,
        company_name="테스트업체",
        contract_date=date(2026, 1, 10),
        payment_date=date(2026, 2, 10),
        resolution_date=resolution,
        amount=Decimal(amount),
        company_id=company_id,
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """정책·인증기업만 준비된 빈 DB. 구매 행은 각 시험이 직접 넣습니다."""
    path = tmp_path / "missing_resolution.db"
    init_db(path)
    seed_policies(path)

    policies = PolicyRepository(path)
    policy = policies.find_by_policy_code("SMALL_BUSINESS")
    assert policy is not None and policy.policy_id is not None
    policies.update_target_rate("SMALL_BUSINESS", Decimal("30"))

    company = CompanyRepository(path).insert(
        Company(business_no=CERTIFIED_NO, company_name="가나상사", representative_name="홍길동")
    )
    assert company.company_id is not None
    CertificationRepository(path).insert(
        Certification(
            company_id=company.company_id,
            policy_id=policy.policy_id,
            valid_from=date(2020, 1, 1),
            valid_to=date(2030, 12, 31),
        )
    )
    return path


@pytest.fixture
def certified_company_id(db_path: Path) -> int:
    company = CompanyRepository(db_path).find_by_business_no(CERTIFIED_NO)
    assert company is not None and company.company_id is not None
    return company.company_id


def _service(db_path: Path) -> DashboardDataService:
    """조립 순서는 운영과 동일하게 유지합니다(app.py 의 composition root)."""
    purchases = PurchaseRepository(db_path)
    policies = PolicyRepository(db_path)
    calculator = ProcurementAchievementCalculator(
        purchases, CertificationRepository(db_path), policies
    )
    return DashboardDataService(
        calculator, policy_repository=policies, purchase_repository=purchases
    )


def _summary(db_path: Path, period: PeriodFilter | None) -> DashboardSummary:
    return _service(db_path).build_summary_from_registered_targets(period)


def _missing(db_path: Path, date_field: str) -> MissingResolutionDate:
    """해당 기준일로 2026년을 조회했을 때의 알림 값."""
    return _summary(db_path, PeriodFilter.for_year(2026, date_field)).missing_resolution_date


class TestRepositoryCount:
    """저장소 집계 — ``resolution_date IS NULL`` 인 행을 센다."""

    def test_no_null_rows_returns_zero(self, db_path: Path) -> None:
        purchases = PurchaseRepository(db_path)
        purchases.insert(_purchase(PLAIN_NO, "100", resolution=date(2026, 3, 1)))
        assert purchases.count_missing_resolution_date() == (0, Decimal("0"))

    def test_empty_table_returns_zero(self, db_path: Path) -> None:
        assert PurchaseRepository(db_path).count_missing_resolution_date() == (0, Decimal("0"))

    def test_single_null_row(self, db_path: Path) -> None:
        purchases = PurchaseRepository(db_path)
        purchases.insert(_purchase(PLAIN_NO, "250", resolution=None))
        assert purchases.count_missing_resolution_date() == (1, Decimal("250"))

    def test_multiple_null_rows_sum_amounts(self, db_path: Path) -> None:
        purchases = PurchaseRepository(db_path)
        purchases.insert(_purchase(PLAIN_NO, "250", resolution=None))
        purchases.insert(_purchase(PLAIN_NO, "150.50", resolution=None))
        purchases.insert(_purchase(PLAIN_NO, "999", resolution=date(2026, 3, 1)))
        assert purchases.count_missing_resolution_date() == (2, Decimal("400.50"))

    def test_counts_only_null_not_other_dates(self, db_path: Path) -> None:
        """지급일·계약일이 있어도 결의일자가 없으면 센다 — 대체하지 않는다."""
        purchases = PurchaseRepository(db_path)
        row = purchases.insert(_purchase(PLAIN_NO, "700", resolution=None))
        assert row.payment_date is not None and row.contract_date is not None
        assert purchases.count_missing_resolution_date()[0] == 1


class TestSummaryReporting:
    """대시보드 요약에 실린 값 — 기간 기준에 따라 알릴지 말지가 갈린다."""

    def test_reports_when_resolution_date_basis(self, db_path: Path) -> None:
        PurchaseRepository(db_path).insert(_purchase(PLAIN_NO, "250", resolution=None))
        missing = _missing(db_path, RESOLUTION_DATE)
        assert missing.applies is True
        assert missing.count == 1
        assert missing.amount == Decimal("250")

    def test_zero_when_nothing_missing(self, db_path: Path) -> None:
        PurchaseRepository(db_path).insert(_purchase(PLAIN_NO, "250", resolution=date(2026, 3, 1)))
        missing = _missing(db_path, RESOLUTION_DATE)
        assert missing.applies is True
        assert missing.count == 0
        assert missing.amount == Decimal("0")

    @pytest.mark.parametrize("date_field", [PAYMENT_DATE, CONTRACT_DATE])
    def test_not_applicable_for_other_date_basis(self, db_path: Path, date_field: str) -> None:
        """결의일자 기준이 아니면 결의일자 공란은 누락 사유가 아니다."""
        PurchaseRepository(db_path).insert(_purchase(PLAIN_NO, "250", resolution=None))
        missing = _missing(db_path, date_field)
        assert missing.applies is False
        assert missing.count == 0

    def test_not_applicable_without_period(self, db_path: Path) -> None:
        PurchaseRepository(db_path).insert(_purchase(PLAIN_NO, "250", resolution=None))
        assert _summary(db_path, None).missing_resolution_date.applies is False

    def test_not_applicable_without_purchase_repository(self, db_path: Path) -> None:
        """저장소를 주입하지 않은 조립에서는 조용히 비활성이다(하위호환)."""
        policies = PolicyRepository(db_path)
        calculator = ProcurementAchievementCalculator(
            PurchaseRepository(db_path), CertificationRepository(db_path), policies
        )
        service = DashboardDataService(calculator, policy_repository=policies)
        summary = service.build_summary_from_registered_targets(
            PeriodFilter.for_year(2026, RESOLUTION_DATE)
        )
        assert summary.missing_resolution_date.applies is False


class TestAchievementUnchanged:
    """⭐ **가장 중요한 시험** — 이 기능이 달성률을 건드리지 않는다.

    결의일자가 비어 있는 행을 더 넣어도 분모·분자·달성률이 그대로여야 합니다.
    표시 계층만 넓혔을 뿐 계산 로직에는 손대지 않았다는 사실을 여기서
    잠급니다.
    """

    def _seed_calculable(self, db_path: Path, certified_company_id: int) -> None:
        purchases = PurchaseRepository(db_path)
        purchases.insert(
            _purchase(
                CERTIFIED_NO, "300", resolution=date(2026, 1, 5), company_id=certified_company_id
            )
        )
        purchases.insert(_purchase(PLAIN_NO, "700", resolution=date(2026, 3, 1)))

    def test_totals_identical_before_and_after_null_rows(
        self, db_path: Path, certified_company_id: int
    ) -> None:
        self._seed_calculable(db_path, certified_company_id)
        period = PeriodFilter.for_year(2026, RESOLUTION_DATE)
        before = _summary(db_path, period)

        PurchaseRepository(db_path).insert(_purchase(PLAIN_NO, "5000", resolution=None))
        after = _summary(db_path, period)

        # 분모
        assert after.total_purchase_amount == before.total_purchase_amount
        # 분자 · 달성률
        assert [(p.policy_code, p.purchase_amount) for p in after.policy_summaries] == [
            (p.policy_code, p.purchase_amount) for p in before.policy_summaries
        ]
        assert [p.achievement_rate for p in after.policy_summaries] == [
            p.achievement_rate for p in before.policy_summaries
        ]
        # 알림만 늘어난다.
        assert before.missing_resolution_date.count == 0
        assert after.missing_resolution_date.count == 1

    def test_null_row_amount_is_not_in_denominator(
        self, db_path: Path, certified_company_id: int
    ) -> None:
        """공란 행의 금액이 분모에 섞여 들어가지 않는다."""
        self._seed_calculable(db_path, certified_company_id)
        PurchaseRepository(db_path).insert(_purchase(PLAIN_NO, "5000", resolution=None))
        summary = _summary(db_path, PeriodFilter.for_year(2026, RESOLUTION_DATE))
        assert summary.total_purchase_amount == Decimal("1000")
        assert summary.missing_resolution_date.amount == Decimal("5000")

    def test_calculator_is_not_asked_about_missing_rows(
        self, db_path: Path, certified_company_id: int
    ) -> None:
        """계산기 결과 자체가 알림 유무와 무관하다."""
        self._seed_calculable(db_path, certified_company_id)
        PurchaseRepository(db_path).insert(_purchase(PLAIN_NO, "5000", resolution=None))
        calculator = ProcurementAchievementCalculator(
            PurchaseRepository(db_path),
            CertificationRepository(db_path),
            PolicyRepository(db_path),
        )
        period = PeriodFilter.for_year(2026, RESOLUTION_DATE)
        assert calculator.calculate_total_purchase(period) == Decimal("1000")


class TestDashboardApiResponse:
    """HTTP 응답에 사실이 그대로 실린다."""

    @pytest.fixture
    def client(self, db_path: Path, certified_company_id: int) -> TestClient:
        purchases = PurchaseRepository(db_path)
        purchases.insert(
            _purchase(
                CERTIFIED_NO, "300", resolution=date(2026, 1, 5), company_id=certified_company_id
            )
        )
        purchases.insert(_purchase(PLAIN_NO, "700", resolution=date(2026, 3, 1)))
        purchases.insert(_purchase(PLAIN_NO, "5000", resolution=None))
        return TestClient(create_app(db_path, period_date_field=RESOLUTION_DATE))

    def test_field_present(self, client: TestClient) -> None:
        body = client.get("/dashboard/summary?year=2026").json()
        assert "missing_resolution_date" in body

    def test_field_values(self, client: TestClient) -> None:
        missing = client.get("/dashboard/summary?year=2026").json()["missing_resolution_date"]
        assert missing["applies"] is True
        assert missing["count"] == 1
        assert Decimal(missing["amount"]) == Decimal("5000")

    def test_existing_fields_still_present(self, client: TestClient) -> None:
        """기존 필드가 사라지거나 이름이 바뀌지 않았다."""
        body = client.get("/dashboard/summary?year=2026").json()
        assert {"total_purchase_amount", "policies"} <= set(body)
        assert Decimal(body["total_purchase_amount"]) == Decimal("1000")

    def test_amount_is_serialized_as_string(self, client: TestClient) -> None:
        """금액은 Decimal 손실을 피하려고 문자열로 나간다(기존 규칙과 동일)."""
        missing = client.get("/dashboard/summary?year=2026").json()["missing_resolution_date"]
        assert isinstance(missing["amount"], str)

    def test_not_applicable_on_payment_date_basis(
        self, db_path: Path, certified_company_id: int
    ) -> None:
        PurchaseRepository(db_path).insert(_purchase(PLAIN_NO, "5000", resolution=None))
        client = TestClient(create_app(db_path, period_date_field=PAYMENT_DATE))
        missing = client.get("/dashboard/summary?year=2026").json()["missing_resolution_date"]
        assert missing["applies"] is False
        assert missing["count"] == 0


class TestScreenWording:
    """화면 문구 — 사실만 적고 판정하지 않는다."""

    @pytest.fixture
    def page(self) -> str:
        path = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "procurement"
            / "web"
            / "static"
            / "index.html"
        )
        return path.read_text(encoding="utf-8")

    def test_has_notice_element(self, page: str) -> None:
        assert 'id="missing-resolution-note"' in page

    def test_reads_api_field(self, page: str) -> None:
        assert "missing_resolution_date" in page

    def test_hidden_when_zero(self, page: str) -> None:
        """0 건이면 표시하지 않는다 — 없는 문제를 만들지 않는다."""
        assert "info.count === 0" in page

    def test_reuses_number_format(self, page: str) -> None:
        assert "numberFormat(info.count)" in page
        assert "numberFormat(info.amount)" in page

    @pytest.mark.parametrize(
        "banned", ["오류", "무효", "부적합", "검토 불필요", "삭제", "실적 불인정"]
    )
    def test_no_verdict_wording(self, page: str, banned: str) -> None:
        """⛔ 판정 표현을 쓰지 않는다 — 알림을 만드는 함수 본문만 본다."""
        start = page.index("function renderMissingResolutionDate")
        body = page[start : page.index("function draw(", start)]
        assert banned not in body
