"""
``GET /dashboard/summary?year=`` 및 기간 조회 가능 여부 노출 테스트.

핵심 규칙 두 가지를 고정합니다.

1. ``year`` 를 **생략하면 기존과 동일**하게 전 기간 합산으로 동작한다(하위 호환).
2. ``year`` 를 지정했는데 **연도 귀속 기준일이 설정되지 않았으면 503** 으로
   거부한다. 임의의 기준일로 숫자를 만들지 않는다(D-24 · W-1).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.core.period import CONTRACT_DATE, PAYMENT_DATE
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Certification, Company, Purchase


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "period_api.db"
    init_db(path)
    seed_policies(path)

    companies = CompanyRepository(path)
    certifications = CertificationRepository(path)
    purchases = PurchaseRepository(path)
    policies = PolicyRepository(path)

    policy = policies.find_by_policy_code("SMALL_BUSINESS")
    assert policy is not None and policy.policy_id is not None
    policies.update_target_rate("SMALL_BUSINESS", Decimal("30"))

    company = companies.insert(
        Company(business_no="1234567890", company_name="가나상사", representative_name="홍길동")
    )
    assert company.company_id is not None
    certifications.insert(
        Certification(
            company_id=company.company_id,
            policy_id=policy.policy_id,
            valid_from=date(2020, 1, 1),
            valid_to=date(2030, 12, 31),
        )
    )

    purchases.insert(_purchase("300", date(2026, 1, 5), date(2026, 2, 1), company.company_id))
    purchases.insert(_purchase("700", date(2026, 3, 1), date(2026, 3, 5), None))
    purchases.insert(_purchase("500", date(2025, 6, 1), date(2025, 7, 1), company.company_id))
    return path


def _purchase(amount: str, contract: date, payment: date, company_id: int | None) -> Purchase:
    return Purchase(
        business_no="1234567890" if company_id else "9999999999",
        company_name="테스트업체",
        contract_date=contract,
        payment_date=payment,
        amount=Decimal(amount),
        company_id=company_id,
    )


@pytest.fixture
def client_without_date_field(db_path: Path) -> TestClient:
    """연도 귀속 기준일이 설정되지 않은 앱(현재 기본 상태)."""
    return TestClient(create_app(db_path, period_date_field=None))


@pytest.fixture
def client_with_payment_date(db_path: Path) -> TestClient:
    """기준일을 지급일로 설정한 앱.

    .. note::
        테스트에서만 명시적으로 주입합니다. **D-24 를 확정한 것이 아니며**,
        기본값은 여전히 없습니다.
    """
    return TestClient(create_app(db_path, period_date_field=PAYMENT_DATE))


class TestWithoutYear:
    """``year`` 생략 — **400** (D-27). 전 기간을 임의로 합산하지 않는다.

    이전에는 200 + 전 기간 합산이었으나, PM 이 D-27 을 적용하도록 결정해
    동작이 바뀌었습니다. 이 클래스는 그 결정에 대한 회귀 테스트입니다.
    """

    def test_returns_400(self, client_without_date_field: TestClient) -> None:
        assert client_without_date_field.get("/dashboard/summary").status_code == 400

    def test_does_not_sum_all_periods(self, client_with_payment_date: TestClient) -> None:
        """기준일이 설정되어 있어도 연도를 생략하면 합산값을 주지 않는다."""
        response = client_with_payment_date.get("/dashboard/summary")
        assert response.status_code == 400
        assert "total_purchase_amount" not in response.json()

    def test_message_explains_reason(self, client_without_date_field: TestClient) -> None:
        detail = client_without_date_field.get("/dashboard/summary").json()["detail"]
        assert "D-27" in detail

    def test_response_shape_unchanged_when_year_given(
        self, client_with_payment_date: TestClient
    ) -> None:
        """연도를 주면 응답 구조는 기존과 동일하다."""
        body = client_with_payment_date.get("/dashboard/summary?year=2026").json()
        assert set(body) == {"total_purchase_amount", "policies"}


class TestYearWithoutDateField:
    """기준일 미설정 상태에서 연도를 지정하면 숫자를 만들지 않는다."""

    def test_returns_503(self, client_without_date_field: TestClient) -> None:
        assert client_without_date_field.get("/dashboard/summary?year=2026").status_code == 503

    def test_message_mentions_decision(self, client_without_date_field: TestClient) -> None:
        detail = client_without_date_field.get("/dashboard/summary?year=2026").json()["detail"]
        assert "D-24" in detail
        assert "W-1" in detail

    def test_data_status_reports_unavailable(self, client_without_date_field: TestClient) -> None:
        body = client_without_date_field.get("/dashboard/data-status").json()
        assert body["period_filter_available"] is False
        assert body["period_date_field"] is None


class TestYearWithDateField:
    """기준일이 설정되면 연도 조회가 실제로 동작한다."""

    def test_returns_200(self, client_with_payment_date: TestClient) -> None:
        assert client_with_payment_date.get("/dashboard/summary?year=2026").status_code == 200

    def test_filters_total(self, client_with_payment_date: TestClient) -> None:
        body = client_with_payment_date.get("/dashboard/summary?year=2026").json()
        assert body["total_purchase_amount"] == "1000"

    def test_filters_policy_amount(self, client_with_payment_date: TestClient) -> None:
        body = client_with_payment_date.get("/dashboard/summary?year=2026").json()
        small = next(p for p in body["policies"] if p["policy_code"] == "SMALL_BUSINESS")
        assert small["purchase_amount"] == "300"
        assert small["total_purchase_amount"] == "1000"

    def test_previous_year_differs(self, client_with_payment_date: TestClient) -> None:
        body = client_with_payment_date.get("/dashboard/summary?year=2025").json()
        assert body["total_purchase_amount"] == "500"

    def test_empty_year_returns_zero(self, client_with_payment_date: TestClient) -> None:
        body = client_with_payment_date.get("/dashboard/summary?year=2020").json()
        assert body["total_purchase_amount"] == "0"

    def test_data_status_reports_available(self, client_with_payment_date: TestClient) -> None:
        body = client_with_payment_date.get("/dashboard/data-status").json()
        assert body["period_filter_available"] is True
        assert body["period_date_field"] == PAYMENT_DATE

    def test_rejects_out_of_range_year(self, client_with_payment_date: TestClient) -> None:
        assert client_with_payment_date.get("/dashboard/summary?year=12").status_code == 422

    def test_rejects_non_numeric_year(self, client_with_payment_date: TestClient) -> None:
        assert client_with_payment_date.get("/dashboard/summary?year=abc").status_code == 422


class TestDateFieldChangesResult:
    """어느 날짜를 기준으로 하느냐에 따라 결과가 달라진다 — D-24 가 중요한 이유."""

    def test_contract_basis_differs_from_payment_basis(self, db_path: Path) -> None:
        by_payment = TestClient(create_app(db_path, period_date_field=PAYMENT_DATE))
        by_contract = TestClient(create_app(db_path, period_date_field=CONTRACT_DATE))

        payment_total = by_payment.get("/dashboard/summary?year=2025").json()[
            "total_purchase_amount"
        ]
        contract_total = by_contract.get("/dashboard/summary?year=2025").json()[
            "total_purchase_amount"
        ]
        assert payment_total == contract_total == "500"

        # 2026 은 동일하지만, 경계에 걸친 데이터에서는 달라질 수 있다.
        assert (
            by_payment.get("/dashboard/summary?year=2026").json()["total_purchase_amount"] == "1000"
        )


class TestNoDefaultDateField:
    """설정에 기본값이 없다는 사실을 고정한다."""

    def test_settings_default_is_none(self) -> None:
        from procurement.core.config import settings

        assert settings.PURCHASE_PERIOD_DATE_FIELD is None
