"""
결의일자 미기재 구매를 **행 단위로 확인**하는 조회 기능을 고정합니다(STEP 60).

무엇을 지키는 시험인가
======================

STEP 59 는 "결의일자가 비어 있는 구매 N건(M 원)" 이라는 **숫자**를 화면에
올렸습니다. 그 숫자만으로는 *어떤* 행인지 알 수 없어, 담당자가 무엇을
확인해야 할지 판단할 수 없습니다. STEP 60 은 같은 모집단을 **행으로 펼쳐
보여 주는 것**이 전부입니다.

.. warning::
    ⛔ **조회 전용입니다.** 결의일자를 채우지 않고, 지급일·계약일로 대체하지
    않으며, 어떤 행도 수정하지 않습니다.

.. warning::
    ⛔ **판정하지 않습니다.** 이 행들은 "오류"·"무효"·"실적 불인정" 이 아니라
    **결의일자가 입력되지 않은 구매**일 뿐입니다. 어떻게 처리할지는 아직
    정해지지 않았으며, 이 STEP 에서 정하지도 않았습니다.

.. warning::
    ⛔ **달성률을 바꾸지 않습니다.** :class:`TestAchievementUnchangedByListing`
    이 STEP 59 의 원칙을 이어서 잠급니다.

기간 조건 — STEP 59 와 같은 원칙
================================

``resolution_date`` 가 ``NULL`` 인 행에 결의일자 **범위 조건**을 걸면 정의상
하나도 남지 않습니다. 따라서 범위 조건은 걸지 않고, 계산 경로와 **같은 배치
조건**만 적용합니다. 조회 기준일이 결의일자가 아닌 경우에는 안내 자체가
해당되지 않으므로 **빈 목록**입니다(요약의 ``applies=false`` 와 같은 판단).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procurement.api.response import MissingResolutionDateListResponseModel
from procurement.app import create_app
from procurement.calculators.procurement_achievement import ProcurementAchievementCalculator
from procurement.core.period import CONTRACT_DATE, PAYMENT_DATE, RESOLUTION_DATE, PeriodFilter
from procurement.dashboard.data_service import DashboardDataService
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.import_batch_repository import ImportBatchRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Certification, Company, Purchase
from procurement.models.import_batch import STATUS_ACTIVE, STATUS_SUPERSEDED, ImportBatch

# 합성 사업자번호입니다 — 실제 고객 데이터가 아닙니다.
CERTIFIED_NO = "1000000001"
PLAIN_NO = "1000000002"

#: 목록 조회 URL. 요약(``/dashboard/summary``)과 같은 연도 규칙을 씁니다.
LIST_URL = "/dashboard/missing-resolution-date?year=2026"


def _purchase(
    business_no: str,
    amount: str,
    *,
    resolution: date | None,
    company_id: int | None = None,
    batch_id: int | None = None,
    description: str | None = "사무용품 구입",
    budget_account: str | None = "201-01",
) -> Purchase:
    return Purchase(
        business_no=business_no,
        company_name="테스트업체",
        contract_date=date(2026, 1, 10),
        payment_date=date(2026, 2, 10),
        resolution_date=resolution,
        issue_date=date(2026, 2, 5),
        description=description,
        budget_account=budget_account,
        amount=Decimal(amount),
        company_id=company_id,
        batch_id=batch_id,
    )


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """정책·인증기업만 준비된 빈 DB. 구매 행은 각 시험이 직접 넣습니다."""
    path = tmp_path / "missing_resolution_list.db"
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
    purchases = PurchaseRepository(db_path)
    policies = PolicyRepository(db_path)
    calculator = ProcurementAchievementCalculator(
        purchases, CertificationRepository(db_path), policies
    )
    return DashboardDataService(
        calculator, policy_repository=policies, purchase_repository=purchases
    )


def _client(db_path: Path) -> TestClient:
    return TestClient(create_app(db_path, period_date_field=RESOLUTION_DATE))


# ----------------------------------------------------------------------
# Repository
# ----------------------------------------------------------------------
class TestRepositoryList:
    """저장소 조회 — 집계와 **같은 모집단**을 행으로 돌려준다."""

    def test_empty(self, db_path: Path) -> None:
        assert PurchaseRepository(db_path).find_missing_resolution_date() == []

    def test_no_null_rows(self, db_path: Path) -> None:
        purchases = PurchaseRepository(db_path)
        purchases.insert(_purchase(PLAIN_NO, "100", resolution=date(2026, 3, 1)))
        assert purchases.find_missing_resolution_date() == []

    def test_single_row(self, db_path: Path) -> None:
        purchases = PurchaseRepository(db_path)
        purchases.insert(_purchase(PLAIN_NO, "250", resolution=None))
        rows = purchases.find_missing_resolution_date()
        assert len(rows) == 1
        assert rows[0].resolution_date is None

    def test_multiple_rows(self, db_path: Path) -> None:
        purchases = PurchaseRepository(db_path)
        purchases.insert(_purchase(PLAIN_NO, "250", resolution=None))
        purchases.insert(_purchase(PLAIN_NO, "150.50", resolution=None))
        purchases.insert(_purchase(PLAIN_NO, "999", resolution=date(2026, 3, 1)))
        rows = purchases.find_missing_resolution_date()
        assert len(rows) == 2
        assert all(row.resolution_date is None for row in rows)

    def test_normal_rows_never_mixed_in(self, db_path: Path) -> None:
        """결의일자가 있는 행은 절대 섞이지 않는다."""
        purchases = PurchaseRepository(db_path)
        purchases.insert(_purchase(PLAIN_NO, "999", resolution=date(2026, 3, 1)))
        purchases.insert(_purchase(PLAIN_NO, "250", resolution=None))
        amounts = [row.amount for row in purchases.find_missing_resolution_date()]
        assert amounts == [Decimal("250")]

    def test_amount_preserved_exactly(self, db_path: Path) -> None:
        """금액은 소수점까지 그대로 보존된다(부동소수 오차 없음)."""
        purchases = PurchaseRepository(db_path)
        purchases.insert(_purchase(PLAIN_NO, "1234567.89", resolution=None))
        assert purchases.find_missing_resolution_date()[0].amount == Decimal("1234567.89")

    def test_other_fields_preserved(self, db_path: Path) -> None:
        """적요·거래처명·사업자번호·예산과목이 원본 그대로 실린다."""
        purchases = PurchaseRepository(db_path)
        purchases.insert(
            _purchase(
                PLAIN_NO, "250", resolution=None, description="복사용지", budget_account="401"
            )
        )
        row = purchases.find_missing_resolution_date()[0]
        assert row.description == "복사용지"
        assert row.company_name == "테스트업체"
        assert row.business_no == PLAIN_NO
        assert row.budget_account == "401"

    def test_ordered_by_purchase_id(self, db_path: Path) -> None:
        purchases = PurchaseRepository(db_path)
        for amount in ("10", "20", "30"):
            purchases.insert(_purchase(PLAIN_NO, amount, resolution=None))
        rows = purchases.find_missing_resolution_date()
        assert all(row.purchase_id is not None for row in rows)  # 저장된 행만 나온다
        ids = [row.purchase_id for row in rows if row.purchase_id is not None]
        assert ids == sorted(ids)

    def test_superseded_batch_rows_excluded(self, db_path: Path) -> None:
        """대체된 배치의 행은 빠진다 — 계산 대상과 **같은 배치 조건**이다.

        ⛔ 새 배치 판정 규칙을 만들지 않았다. ``find_for_calculation`` 과 같은
        조건을 그대로 쓴다.
        """
        batches = ImportBatchRepository(db_path)
        active = batches.insert(_batch(STATUS_ACTIVE))
        superseded = batches.insert(_batch(STATUS_SUPERSEDED))
        assert active.batch_id is not None and superseded.batch_id is not None

        purchases = PurchaseRepository(db_path)
        purchases.insert(_purchase(PLAIN_NO, "100", resolution=None, batch_id=active.batch_id))
        purchases.insert(_purchase(PLAIN_NO, "900", resolution=None, batch_id=superseded.batch_id))

        rows = purchases.find_missing_resolution_date()
        assert [row.amount for row in rows] == [Decimal("100")]

    def test_batchless_rows_included(self, db_path: Path) -> None:
        """배치 이전에 적재된 행(batch_id NULL)은 계속 보인다."""
        purchases = PurchaseRepository(db_path)
        purchases.insert(_purchase(PLAIN_NO, "100", resolution=None, batch_id=None))
        assert len(purchases.find_missing_resolution_date()) == 1

    def test_list_matches_count(self, db_path: Path) -> None:
        """⭐ 목록의 길이·합계가 STEP 59 집계와 **정확히 일치**한다.

        두 조회가 어긋나면 화면은 "3건" 이라 적고 2줄만 보여 주는데, 담당자는
        그 어긋남을 알아챌 방법이 없다.
        """
        batches = ImportBatchRepository(db_path)
        superseded = batches.insert(_batch(STATUS_SUPERSEDED))
        assert superseded.batch_id is not None

        purchases = PurchaseRepository(db_path)
        purchases.insert(_purchase(PLAIN_NO, "250", resolution=None))
        purchases.insert(_purchase(PLAIN_NO, "150.50", resolution=None))
        purchases.insert(_purchase(PLAIN_NO, "999", resolution=date(2026, 3, 1)))
        purchases.insert(_purchase(PLAIN_NO, "900", resolution=None, batch_id=superseded.batch_id))

        count, amount = purchases.count_missing_resolution_date()
        rows = purchases.find_missing_resolution_date()
        assert count == len(rows)
        assert amount == sum((row.amount for row in rows), Decimal("0"))

    def test_no_period_argument(self, db_path: Path) -> None:
        """⛔ 기간 조건을 **받지 않는다** — 걸면 정의상 0건이 되기 때문이다."""
        import inspect

        signature = inspect.signature(PurchaseRepository.find_missing_resolution_date)
        assert list(signature.parameters) == ["self"]

    def test_does_not_modify_rows(self, db_path: Path) -> None:
        """⛔ 조회가 원본을 바꾸지 않는다."""
        purchases = PurchaseRepository(db_path)
        stored = purchases.insert(_purchase(PLAIN_NO, "250", resolution=None))
        assert stored.purchase_id is not None
        purchases.find_missing_resolution_date()
        again = purchases.find_by_id(stored.purchase_id)
        assert again is not None
        assert again.resolution_date is None
        assert again.payment_date == stored.payment_date
        assert again.amount == stored.amount


def _batch(status: str) -> ImportBatch:
    return ImportBatch(
        period_start=date(2026, 1, 1),
        period_end=date(2026, 12, 31),
        file_name="synthetic.xlsx",
        row_count=1,
        status=status,
    )


# ----------------------------------------------------------------------
# 서비스 계층 — 기간 기준 판단
# ----------------------------------------------------------------------
class TestServiceListing:
    """기간 기준에 따라 목록을 줄지 말지가 갈린다(요약의 ``applies`` 와 동일)."""

    def test_lists_on_resolution_date_basis(self, db_path: Path) -> None:
        PurchaseRepository(db_path).insert(_purchase(PLAIN_NO, "250", resolution=None))
        rows = _service(db_path).list_missing_resolution_date(
            PeriodFilter.for_year(2026, RESOLUTION_DATE)
        )
        assert len(rows) == 1

    @pytest.mark.parametrize("date_field", [PAYMENT_DATE, CONTRACT_DATE])
    def test_empty_on_other_date_basis(self, db_path: Path, date_field: str) -> None:
        """결의일자 기준이 아니면 안내 자체가 해당되지 않으므로 빈 목록이다."""
        PurchaseRepository(db_path).insert(_purchase(PLAIN_NO, "250", resolution=None))
        rows = _service(db_path).list_missing_resolution_date(
            PeriodFilter.for_year(2026, date_field)
        )
        assert rows == []

    def test_empty_without_period(self, db_path: Path) -> None:
        PurchaseRepository(db_path).insert(_purchase(PLAIN_NO, "250", resolution=None))
        assert _service(db_path).list_missing_resolution_date(None) == []

    def test_empty_without_purchase_repository(self, db_path: Path) -> None:
        """저장소를 주입하지 않은 조립에서는 조용히 비활성이다(하위호환)."""
        policies = PolicyRepository(db_path)
        calculator = ProcurementAchievementCalculator(
            PurchaseRepository(db_path), CertificationRepository(db_path), policies
        )
        service = DashboardDataService(calculator, policy_repository=policies)
        rows = service.list_missing_resolution_date(PeriodFilter.for_year(2026, RESOLUTION_DATE))
        assert rows == []

    def test_period_year_does_not_filter_rows(self, db_path: Path) -> None:
        """⛔ 어느 연도로 조회하든 대상이 줄지 않는다 — 범위 조건을 걸지 않는다."""
        PurchaseRepository(db_path).insert(_purchase(PLAIN_NO, "250", resolution=None))
        service = _service(db_path)
        for year in (2020, 2026, 2030):
            rows = service.list_missing_resolution_date(
                PeriodFilter.for_year(year, RESOLUTION_DATE)
            )
            assert len(rows) == 1, f"{year} 년 조회에서 대상이 달라졌습니다"


# ----------------------------------------------------------------------
# 응답 모델
# ----------------------------------------------------------------------
class TestResponseModel:
    """건수·합계는 목록에서 직접 센다 — 따로 세어 넣지 않는다."""

    def test_empty(self) -> None:
        model = MissingResolutionDateListResponseModel.from_purchases([])
        assert model.items == []
        assert model.count == 0
        assert model.amount == Decimal("0")

    def test_count_always_matches_items(self, db_path: Path) -> None:
        purchases = PurchaseRepository(db_path)
        for amount in ("10", "20.5", "30"):
            purchases.insert(_purchase(PLAIN_NO, amount, resolution=None))
        model = MissingResolutionDateListResponseModel.from_purchases(
            purchases.find_missing_resolution_date()
        )
        assert model.count == len(model.items) == 3
        assert model.amount == Decimal("60.5")

    def test_missing_resolution_date_stays_null(self, db_path: Path) -> None:
        """⛔ 비어 있는 결의일자를 다른 날짜로 채우지 않는다."""
        purchases = PurchaseRepository(db_path)
        purchases.insert(_purchase(PLAIN_NO, "250", resolution=None))
        model = MissingResolutionDateListResponseModel.from_purchases(
            purchases.find_missing_resolution_date()
        )
        assert model.items[0].resolution_date is None


# ----------------------------------------------------------------------
# HTTP API
# ----------------------------------------------------------------------
class TestListApi:
    """``GET /dashboard/missing-resolution-date``."""

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
        return _client(db_path)

    def test_response_shape(self, client: TestClient) -> None:
        body = client.get(LIST_URL).json()
        assert set(body) == {"items", "count", "amount"}

    def test_item_shape(self, client: TestClient) -> None:
        """검토 화면의 원본 블록과 **같은 필드**를 쓴다(새 공개 범위 없음)."""
        item = client.get(LIST_URL).json()["items"][0]
        assert set(item) == {
            "purchase_id",
            "description",
            "company_name",
            "business_no",
            "amount",
            "resolution_date",
            "issue_date",
            "budget_account",
        }

    def test_only_missing_rows(self, client: TestClient) -> None:
        body = client.get(LIST_URL).json()
        assert body["count"] == 1
        assert Decimal(body["items"][0]["amount"]) == Decimal("5000")
        assert body["items"][0]["resolution_date"] is None

    def test_count_matches_summary(self, client: TestClient) -> None:
        """⭐ 목록과 대시보드 안내의 건수·금액이 어긋나지 않는다."""
        listing = client.get(LIST_URL).json()
        summary = client.get("/dashboard/summary?year=2026").json()["missing_resolution_date"]
        assert listing["count"] == summary["count"] == len(listing["items"])
        assert Decimal(listing["amount"]) == Decimal(summary["amount"])

    def test_amount_serialized_as_string(self, client: TestClient) -> None:
        body = client.get(LIST_URL).json()
        assert isinstance(body["amount"], str)
        assert isinstance(body["items"][0]["amount"], str)

    def test_empty_list(self, db_path: Path) -> None:
        """0건이면 빈 목록을 준다 — 오류가 아니다."""
        PurchaseRepository(db_path).insert(_purchase(PLAIN_NO, "700", resolution=date(2026, 3, 1)))
        body = _client(db_path).get(LIST_URL).json()
        assert body == {"items": [], "count": 0, "amount": "0"}

    def test_empty_on_payment_date_basis(self, db_path: Path) -> None:
        PurchaseRepository(db_path).insert(_purchase(PLAIN_NO, "5000", resolution=None))
        client = TestClient(create_app(db_path, period_date_field=PAYMENT_DATE))
        body = client.get(LIST_URL).json()
        assert body["count"] == 0
        assert body["items"] == []

    def test_year_required(self, client: TestClient) -> None:
        """요약과 **같은 규칙**이다 — 연도를 생략하면 400 (D-27)."""
        assert client.get("/dashboard/missing-resolution-date").status_code == 400

    def test_503_without_date_field(self, db_path: Path) -> None:
        """기간 판정 기준일이 없으면 503 — 요약과 동일(D-24)."""
        client = TestClient(create_app(db_path, period_date_field=None))
        assert client.get(LIST_URL).status_code == 503

    def test_read_only_no_write_route(self, db_path: Path) -> None:
        """⛔ 이 경로에 쓰기(수정) 메서드를 만들지 않았다."""
        client = _client(db_path)
        for method in ("post", "put", "patch", "delete"):
            response = getattr(client, method)("/dashboard/missing-resolution-date")
            assert response.status_code == 405, f"{method.upper()} 가 열려 있습니다"

    def test_dashboard_summary_still_compatible(self, client: TestClient) -> None:
        """기존 대시보드 응답이 그대로 동작한다."""
        body = client.get("/dashboard/summary?year=2026").json()
        assert {"total_purchase_amount", "policies", "missing_resolution_date"} == set(body)


# ----------------------------------------------------------------------
# 달성률 불변 (STEP 59 원칙 유지)
# ----------------------------------------------------------------------
class TestAchievementUnchangedByListing:
    """⭐ 목록을 조회해도 분모·분자·달성률이 달라지지 않는다."""

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
        return _client(db_path)

    def test_summary_identical_before_and_after_listing(self, client: TestClient) -> None:
        before = client.get("/dashboard/summary?year=2026").json()
        client.get(LIST_URL)
        after = client.get("/dashboard/summary?year=2026").json()
        assert after == before

    def test_denominator_excludes_missing_rows(self, client: TestClient) -> None:
        body = client.get("/dashboard/summary?year=2026").json()
        assert Decimal(body["total_purchase_amount"]) == Decimal("1000")

    def test_listed_amount_is_not_in_totals(self, client: TestClient) -> None:
        """목록의 금액(5,000)이 분모(1,000)에 섞이지 않았다."""
        listing = client.get(LIST_URL).json()
        body = client.get("/dashboard/summary?year=2026").json()
        assert Decimal(listing["amount"]) == Decimal("5000")
        assert Decimal(body["total_purchase_amount"]) == Decimal("1000")

    def test_achievement_rates_unchanged_by_listing(self, client: TestClient) -> None:
        before = [
            p["achievement_rate"]
            for p in client.get("/dashboard/summary?year=2026").json()["policies"]
        ]
        client.get(LIST_URL)
        after = [
            p["achievement_rate"]
            for p in client.get("/dashboard/summary?year=2026").json()["policies"]
        ]
        assert after == before


# ----------------------------------------------------------------------
# 화면
# ----------------------------------------------------------------------
class TestScreen:
    """화면 — 안내 문구에서 목록을 펼쳐 볼 수 있다."""

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

    def test_fold_element_exists(self, page: str) -> None:
        assert 'id="missing-resolution-fold"' in page
        assert 'id="missing-resolution-rows"' in page

    def test_uses_details_element(self, page: str) -> None:
        """기존 ``<details>`` 접기 관용구를 그대로 쓴다(키보드·스크린리더 지원)."""
        start = page.index('id="missing-resolution-fold"')
        assert "<details" in page[start - 60 : start]

    def test_calls_list_endpoint(self, page: str) -> None:
        assert "/dashboard/missing-resolution-date?year=" in page

    def test_hidden_when_zero(self, page: str) -> None:
        """0건이면 STEP 59 원칙대로 안내·목록 모두 표시하지 않는다."""
        body = self._render_body(page)
        assert "fold.hidden = true" in body

    def test_shows_count_and_amount(self, page: str) -> None:
        rows = self._rows_body(page)
        assert "numberFormat(page.count)" in rows
        assert "numberFormat(page.amount)" in rows

    def test_lists_required_columns(self, page: str) -> None:
        rows = self._rows_body(page)
        for column in ("적요", "거래처명", "사업자등록번호", "금액", "결의일자", "예산과목"):
            assert column in rows, f"{column} 열이 없습니다"

    def test_missing_date_shown_as_blank_not_substituted(self, page: str) -> None:
        """⛔ 비어 있는 결의일자를 지급일·계약일로 채우지 않는다."""
        rows = self._rows_body(page)
        assert "item.resolution_date" in rows
        assert "payment_date" not in rows
        assert "contract_date" not in rows

    def test_no_edit_controls(self, page: str) -> None:
        """⛔ 수정·자동 보정 기능을 두지 않는다 — 조회만 한다."""
        rows = self._rows_body(page)
        assert "<button" not in rows
        assert "<input" not in rows

    @pytest.mark.parametrize(
        "banned",
        ["오류", "잘못된 데이터", "무효", "실적 불인정", "자동 제외", "부적합", "삭제"],
    )
    def test_neutral_wording(self, page: str, banned: str) -> None:
        """⛔ 판정 표현을 쓰지 않는다."""
        assert banned not in self._rows_body(page)

    def test_uses_neutral_label(self, page: str) -> None:
        assert "결의일자 미기재 구매" in page

    # -- helpers -------------------------------------------------------
    def _render_body(self, page: str) -> str:
        start = page.index("function renderMissingResolutionDate")
        return page[start : page.index("function loadMissingResolutionRows", start)]

    def _rows_body(self, page: str) -> str:
        start = page.index("function loadMissingResolutionRows")
        return page[start : page.index("function draw(", start)]
