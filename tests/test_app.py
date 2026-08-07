"""
FastAPI Dashboard API 테스트.

:func:`procurement.app.create_app` 로 격리 DB 를 주입한 앱을 만들고,
``GET /dashboard/summary`` 응답과 OpenAPI(Swagger) 문서 동작을 검증합니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procurement.api import DashboardApiService
from procurement.app import build_dashboard_api, create_app
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Certification, Company, Policy, Purchase


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    CompanyRepository(path).create_table()
    PolicyRepository(path).create_table()
    CertificationRepository(path).create_table()
    PurchaseRepository(path).create_table()
    return path


def _seed(db_path: Path, target_rate: Decimal | None) -> None:
    company_repo = CompanyRepository(db_path)
    policy_repo = PolicyRepository(db_path)
    cert_repo = CertificationRepository(db_path)
    purchase_repo = PurchaseRepository(db_path)

    company = company_repo.insert(
        Company(
            business_no="1000000001",
            company_name="기업",
            representative_name="홍길동",
        )
    )
    assert company.company_id is not None
    policy = policy_repo.insert(
        Policy(policy_code="SMALL_BUSINESS", policy_name="중소기업", target_rate=target_rate)
    )
    assert policy.policy_id is not None
    cert_repo.insert(
        Certification(
            company_id=company.company_id,
            policy_id=policy.policy_id,
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
        )
    )
    purchase_repo.insert(
        Purchase(
            business_no="1000000001",
            company_id=company.company_id,
            company_name="기업",
            contract_date=date(2026, 3, 1),
            payment_date=date(2026, 3, 15),
            amount=Decimal("3000000"),
        )
    )
    purchase_repo.insert(
        Purchase(
            business_no="0000000000",
            company_id=None,
            company_name="기타",
            contract_date=date(2026, 3, 1),
            payment_date=date(2026, 3, 15),
            amount=Decimal("7000000"),
        )
    )


@pytest.fixture
def client(db_path: Path) -> TestClient:
    return TestClient(create_app(db_path))


class TestCompositionRoot:
    """조립 함수(build_dashboard_api)를 검증합니다."""

    def test_returns_dashboard_api_service(self, db_path: Path) -> None:
        assert isinstance(build_dashboard_api(db_path), DashboardApiService)


class TestDashboardSummaryEndpoint:
    """GET /dashboard/summary 동작을 검증합니다."""

    def test_registered_target_rate(self, client: TestClient, db_path: Path) -> None:
        """등록 목표율 50% 기준으로 요약 JSON 이 반환됩니다."""
        _seed(db_path, target_rate=Decimal("50"))
        response = client.get("/dashboard/summary")
        assert response.status_code == 200
        payload = response.json()
        assert payload["total_purchase_amount"] == "10000000"
        assert len(payload["policies"]) == 1
        item = payload["policies"][0]
        assert item["policy_code"] == "SMALL_BUSINESS"
        assert item["target_rate"] == "50"
        assert item["achievement_rate"] == "60.00"
        assert item["shortage_rate"] == "40.00"
        assert item["status"] == "SHORTAGE"
        assert item["status_label"] == "부족"

    def test_decimal_fields_are_strings(self, client: TestClient, db_path: Path) -> None:
        _seed(db_path, target_rate=Decimal("50"))
        item = client.get("/dashboard/summary").json()["policies"][0]
        for key in ("purchase_amount", "target_rate", "achievement_rate", "shortage_rate"):
            assert isinstance(item[key], str)

    def test_excludes_policy_without_target_rate(
        self, client: TestClient, db_path: Path
    ) -> None:
        """목표율이 없는 정책도 응답에 포함되며 '목표율 미설정'으로 표시됩니다."""
        _seed(db_path, target_rate=None)
        payload = client.get("/dashboard/summary").json()
        assert payload["total_purchase_amount"] == "10000000"
        assert len(payload["policies"]) == 1

        item = payload["policies"][0]
        assert item["policy_code"] == "SMALL_BUSINESS"
        assert item["target_rate"] is None
        assert item["achievement_rate"] is None
        assert item["shortage_rate"] is None
        assert item["status"] == "TARGET_RATE_NOT_SET"
        assert item["status_label"] == "목표율 미설정"

    def test_empty_database(self, client: TestClient) -> None:
        """데이터가 없으면 전체 구매액 0, 정책 요약은 빈 목록입니다."""
        payload = client.get("/dashboard/summary").json()
        assert payload["total_purchase_amount"] == "0"
        assert payload["policies"] == []


class TestOpenApi:
    """Swagger(OpenAPI) 문서 동작을 검증합니다(성공 기준)."""

    def test_openapi_schema_lists_endpoint(self, client: TestClient) -> None:
        schema = client.get("/openapi.json")
        assert schema.status_code == 200
        body = schema.json()
        assert "/dashboard/summary" in body["paths"]
        assert "get" in body["paths"]["/dashboard/summary"]

    def test_swagger_ui_served(self, client: TestClient) -> None:
        response = client.get("/docs")
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_response_model_in_schema(self, client: TestClient) -> None:
        body = client.get("/openapi.json").json()
        assert "DashboardResponseModel" in body["components"]["schemas"]
