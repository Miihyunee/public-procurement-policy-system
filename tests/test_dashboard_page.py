"""
Dashboard 화면 및 화면용 API 테스트.

``GET /`` (HTML), ``GET /dashboard/data-status``,
``GET /dashboard/policy-display`` 를 검증합니다. 기존 엔드포인트
(``/dashboard/summary`` · ``/policies``)의 동작이 바뀌지 않았는지도 함께
확인합니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procurement.api.status_response import PERIOD_NOTICE
from procurement.app import create_app
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Purchase
from procurement.web.policy_display import ON_HOLD, POLICY_DISPLAY, READY, get_display_info

#: 정본 정책 코드 개수(bootstrap seed 기준).
SEED_POLICY_COUNT = 5


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "dashboard_page.db"
    init_db(path)
    seed_policies(path)
    return path


@pytest.fixture
def client(db_path: Path) -> TestClient:
    return TestClient(create_app(db_path))


class TestDashboardPage:
    """``GET /`` — 브라우저 화면."""

    def test_returns_200(self, client: TestClient) -> None:
        assert client.get("/").status_code == 200

    def test_content_type_is_html(self, client: TestClient) -> None:
        assert client.get("/").headers["content-type"].startswith("text/html")

    def test_contains_year_selector(self, client: TestClient) -> None:
        assert 'id="year-select"' in client.get("/").text

    def test_contains_chart_containers(self, client: TestClient) -> None:
        """최소 2개 시각화 영역이 존재한다."""
        body = client.get("/").text
        assert 'id="chart-achievement"' in body
        assert 'id="chart-volume"' in body
        assert 'id="chart-gauge"' in body

    def test_contains_theme_toggle(self, client: TestClient) -> None:
        """라이트/다크 전환 버튼이 존재한다."""
        assert 'id="theme-toggle"' in client.get("/").text

    def test_defines_both_themes(self, client: TestClient) -> None:
        """두 테마의 색 토큰이 모두 정의되어 있다."""
        body = client.get("/").text
        assert ':root[data-theme="dark"]' in body
        assert ':root[data-theme="light"]' in body

    def test_contains_upload_status_area(self, client: TestClient) -> None:
        assert 'id="status-table"' in client.get("/").text

    def test_does_not_load_external_resources(self, client: TestClient) -> None:
        """CDN·외부 라이브러리를 사용하지 않는다(오프라인에서도 동작)."""
        body = client.get("/").text
        assert "http://" not in body.replace("http://www.w3.org/2000/svg", "")
        assert "https://" not in body

    def test_page_is_not_in_openapi_schema(self, client: TestClient) -> None:
        assert "/" not in client.get("/openapi.json").json()["paths"]


class TestDataStatusEndpoint:
    """``GET /dashboard/data-status``."""

    def test_returns_200(self, client: TestClient) -> None:
        assert client.get("/dashboard/data-status").status_code == 200

    def test_empty_db_reports_zero(self, client: TestClient) -> None:
        body = client.get("/dashboard/data-status").json()
        assert body["purchase_count"] == 0
        assert body["purchase_total_amount"] == "0"
        assert body["earliest_payment_date"] is None

    def test_policy_count_matches_seed(self, client: TestClient) -> None:
        assert client.get("/dashboard/data-status").json()["policy_count"] == SEED_POLICY_COUNT

    def test_amount_is_serialized_as_string(self, client: TestClient, db_path: Path) -> None:
        PurchaseRepository(db_path).insert(
            Purchase(
                business_no="1234567890",
                company_name="테스트업체",
                contract_date=date(2026, 1, 1),
                payment_date=date(2026, 1, 31),
                amount=Decimal("1234.56"),
            )
        )
        body = client.get("/dashboard/data-status").json()
        assert body["purchase_total_amount"] == "1234.56"

    def test_period_filter_is_never_applied(self, client: TestClient) -> None:
        body = client.get("/dashboard/data-status?year=2026").json()
        assert body["period_filter_applied"] is False
        assert body["period_notice"] == PERIOD_NOTICE

    def test_year_is_echoed_back(self, client: TestClient) -> None:
        assert client.get("/dashboard/data-status?year=2026").json()["requested_year"] == 2026

    def test_year_is_null_when_omitted(self, client: TestClient) -> None:
        assert client.get("/dashboard/data-status").json()["requested_year"] is None

    def test_out_of_range_year_is_rejected(self, client: TestClient) -> None:
        assert client.get("/dashboard/data-status?year=12").status_code == 422

    def test_non_numeric_year_is_rejected(self, client: TestClient) -> None:
        assert client.get("/dashboard/data-status?year=abc").status_code == 422

    def test_data_mode_defaults_to_demo(self, db_path: Path) -> None:
        client = TestClient(create_app(db_path, data_mode="demo"))
        assert client.get("/dashboard/data-status").json()["data_mode"] == "demo"

    def test_data_mode_can_be_operational(self, db_path: Path) -> None:
        client = TestClient(create_app(db_path, data_mode="operational"))
        assert client.get("/dashboard/data-status").json()["data_mode"] == "operational"

    def test_year_does_not_change_counts(self, client: TestClient, db_path: Path) -> None:
        """기간 필터가 없으므로 연도를 바꿔도 결과가 같아야 한다(현재 단계 사실)."""
        PurchaseRepository(db_path).insert(
            Purchase(
                business_no="1234567890",
                company_name="테스트업체",
                contract_date=date(2020, 1, 1),
                payment_date=date(2020, 1, 31),
                amount=Decimal("100"),
            )
        )
        a = client.get("/dashboard/data-status?year=2026").json()["purchase_count"]
        b = client.get("/dashboard/data-status?year=2020").json()["purchase_count"]
        assert a == b == 1


def _display_map(client: TestClient) -> dict[str, dict[str, str]]:
    """정책 코드 → 표시 정보 매핑으로 응답을 변환합니다."""
    items: list[dict[str, str]] = client.get("/dashboard/policy-display").json()["items"]
    return {item["policy_code"]: item for item in items}


class TestPolicyDisplayEndpoint:
    """``GET /dashboard/policy-display``."""

    def test_returns_200(self, client: TestClient) -> None:
        assert client.get("/dashboard/policy-display").status_code == 200

    def test_covers_all_seed_policy_codes(self, client: TestClient) -> None:
        items = client.get("/dashboard/policy-display").json()["items"]
        codes = {item["policy_code"] for item in items}
        seed_codes = {
            policy["policy_code"] for policy in client.get("/policies").json()["policies"]
        }
        assert seed_codes <= codes

    def test_green_is_on_hold(self, client: TestClient) -> None:
        """D-3 — 녹색제품은 유지하되 계산 보류."""
        items = _display_map(client)
        assert items["GREEN"]["development_status"] == ON_HOLD

    def test_woman_is_on_hold(self, client: TestClient) -> None:
        """D-1 — 여성기업은 개발 중단(D-2 선행)."""
        items = _display_map(client)
        assert items["WOMAN"]["development_status"] == ON_HOLD

    def test_on_hold_policies_have_a_reason(self, client: TestClient) -> None:
        for item in client.get("/dashboard/policy-display").json()["items"]:
            if item["development_status"] == ON_HOLD:
                assert item["note"]

    def test_small_business_is_ready(self, client: TestClient) -> None:
        items = _display_map(client)
        assert items["SMALL_BUSINESS"]["development_status"] == READY


class TestPolicyDisplayMap:
    """표시 정보 매핑 자체."""

    def test_unknown_code_falls_back(self) -> None:
        info = get_display_info("NO_SUCH_POLICY")
        assert info.development_status == "UNKNOWN"

    def test_every_entry_has_a_label(self) -> None:
        for info in POLICY_DISPLAY.values():
            assert info.development_label


class TestExistingEndpointsUnchanged:
    """기존 API 가 그대로 동작한다."""

    def test_summary_still_returns_200(self, client: TestClient) -> None:
        assert client.get("/dashboard/summary").status_code == 200

    def test_summary_shape_unchanged(self, client: TestClient) -> None:
        body = client.get("/dashboard/summary").json()
        assert set(body) == {"total_purchase_amount", "policies"}

    def test_summary_has_no_period_fields(self, client: TestClient) -> None:
        """요약 API 에는 기간 관련 필드를 추가하지 않았다."""
        body = client.get("/dashboard/summary").json()
        assert "requested_year" not in body

    def test_policies_still_returns_200(self, client: TestClient) -> None:
        assert client.get("/policies").status_code == 200

    def test_target_rate_write_is_still_protected(self, client: TestClient) -> None:
        """관리자 토큰 미설정 앱에서는 쓰기 API 가 503 이다(D-14)."""
        response = client.put("/policies/SMALL_BUSINESS/target-rate", json={"target_rate": "50"})
        assert response.status_code == 503
