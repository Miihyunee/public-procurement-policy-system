"""
정책 목표율 관리 API 테스트.

``GET /policies`` 와 ``PUT /policies/{policy_code}/target-rate`` 의 응답,
검증 규칙, 관리자 인증, 기존 대시보드 API 와의 관계를 검증합니다.

.. note::
    관리자 토큰은 테스트 전용 값을 :func:`create_app` 에 직접 주입합니다.
    실제 운영 토큰을 코드·테스트에 기록하지 않습니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from procurement.app import create_app
from procurement.database.bootstrap import init_db, seed_policies
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Certification, Company, Policy, Purchase

#: 테스트 전용 관리자 토큰(운영 값 아님).
TEST_TOKEN = "test-admin-token"
#: 정본 정책 코드 개수(bootstrap seed 기준).
SEED_POLICY_COUNT = 5

AUTH = {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "admin_api.db"
    init_db(path)
    seed_policies(path)
    return path


@pytest.fixture
def client(db_path: Path) -> TestClient:
    """관리자 토큰이 설정된 앱의 테스트 클라이언트."""
    return TestClient(create_app(db_path, admin_token=TEST_TOKEN))


def _put(client: TestClient, code: str, payload: object) -> Response:
    response: Response = client.put(f"/policies/{code}/target-rate", json=payload, headers=AUTH)
    return response


class TestListPolicies:
    """``GET /policies``."""

    def test_returns_200(self, client: TestClient) -> None:
        assert client.get("/policies").status_code == 200

    def test_returns_seed_policies(self, client: TestClient) -> None:
        payload = client.get("/policies").json()
        assert len(payload["policies"]) == SEED_POLICY_COUNT

    def test_seed_policy_codes(self, client: TestClient) -> None:
        """정본 코드가 그대로 노출됩니다(GREEN 포함)."""
        codes = {item["policy_code"] for item in client.get("/policies").json()["policies"]}
        assert codes == {"SMALL_BUSINESS", "WOMAN", "DISABLED", "STARTUP", "GREEN"}

    def test_unset_target_rate_is_json_null(self, client: TestClient) -> None:
        """미설정 목표율은 문자열이 아니라 JSON null 입니다."""
        payload = client.get("/policies").json()
        assert all(item["target_rate"] is None for item in payload["policies"])
        assert all(item["target_rate_status"] == "NOT_SET" for item in payload["policies"])

    def test_seed_target_rates_are_all_null(self, client: TestClient) -> None:
        """seed 단계에서 어떤 목표율도 입력되어 있지 않습니다."""
        payload = client.get("/policies").json()
        assert [item["target_rate"] for item in payload["policies"]] == [None] * SEED_POLICY_COUNT

    def test_includes_is_active(self, client: TestClient) -> None:
        payload = client.get("/policies").json()
        assert all(item["is_active"] is True for item in payload["policies"])

    def test_does_not_require_admin_token(self, db_path: Path) -> None:
        """조회는 관리자 인증 대상이 아닙니다(토큰 미설정 환경에서도 동작)."""
        response = TestClient(create_app(db_path)).get("/policies")
        assert response.status_code == 200


class TestUpdateTargetRate:
    """``PUT /policies/{policy_code}/target-rate`` 정상 경로."""

    def test_sets_value(self, client: TestClient) -> None:
        response = _put(client, "SMALL_BUSINESS", {"target_rate": "50"})
        assert response.status_code == 200
        assert response.json()["target_rate"] == "50"
        assert response.json()["target_rate_status"] == "SET"

    def test_persisted(self, client: TestClient) -> None:
        _put(client, "SMALL_BUSINESS", {"target_rate": "50"})

        payload = client.get("/policies").json()
        item = {p["policy_code"]: p for p in payload["policies"]}["SMALL_BUSINESS"]
        assert item["target_rate"] == "50"

    def test_decimal_precision_preserved(self, client: TestClient) -> None:
        response = _put(client, "WOMAN", {"target_rate": "8.05"})
        assert response.json()["target_rate"] == "8.05"

    def test_null_resets_target_rate(self, client: TestClient) -> None:
        """명시적 null 은 목표율 해제입니다."""
        _put(client, "STARTUP", {"target_rate": "20"})

        response = _put(client, "STARTUP", {"target_rate": None})

        assert response.status_code == 200
        assert response.json()["target_rate"] is None
        assert response.json()["target_rate_status"] == "NOT_SET"

    def test_max_boundary_allowed(self, client: TestClient) -> None:
        assert _put(client, "DISABLED", {"target_rate": "100"}).status_code == 200

    def test_is_idempotent(self, client: TestClient) -> None:
        first = _put(client, "SMALL_BUSINESS", {"target_rate": "50"}).json()
        second = _put(client, "SMALL_BUSINESS", {"target_rate": "50"}).json()
        assert first["target_rate"] == second["target_rate"] == "50"

    def test_green_policy_is_updatable(self, client: TestClient) -> None:
        """녹색제품 정책도 등록 수단은 동일합니다(값은 넣어두지 않습니다)."""
        assert _put(client, "GREEN", {"target_rate": "10"}).status_code == 200


class TestUpdateValidation:
    """검증 규칙 (전부 422)."""

    def test_missing_key_is_422(self, client: TestClient) -> None:
        """target_rate 키가 없으면 '변경 안 함'이 아니라 잘못된 요청입니다."""
        assert _put(client, "SMALL_BUSINESS", {}).status_code == 422

    def test_json_number_is_422(self, client: TestClient) -> None:
        """JSON number 는 Decimal 정밀도 손상 위험이 있어 거부합니다."""
        assert _put(client, "SMALL_BUSINESS", {"target_rate": 8.0}).status_code == 422

    def test_json_integer_is_422(self, client: TestClient) -> None:
        assert _put(client, "SMALL_BUSINESS", {"target_rate": 8}).status_code == 422

    def test_non_numeric_string_is_422(self, client: TestClient) -> None:
        assert _put(client, "SMALL_BUSINESS", {"target_rate": "abc"}).status_code == 422

    def test_zero_is_422(self, client: TestClient) -> None:
        assert _put(client, "SMALL_BUSINESS", {"target_rate": "0"}).status_code == 422

    def test_negative_is_422(self, client: TestClient) -> None:
        assert _put(client, "SMALL_BUSINESS", {"target_rate": "-1"}).status_code == 422

    def test_over_max_is_422(self, client: TestClient) -> None:
        assert _put(client, "SMALL_BUSINESS", {"target_rate": "100.01"}).status_code == 422

    def test_unknown_policy_code_is_404(self, client: TestClient) -> None:
        assert _put(client, "NOT_EXIST", {"target_rate": "10"}).status_code == 404

    def test_inactive_policy_is_422(self, db_path: Path) -> None:
        PolicyRepository(db_path).insert(
            Policy(policy_code="OFF", policy_name="폐지", is_active=False)
        )
        client = TestClient(create_app(db_path, admin_token=TEST_TOKEN))

        assert _put(client, "OFF", {"target_rate": "10"}).status_code == 422

    def test_rejected_request_does_not_change_value(self, client: TestClient) -> None:
        _put(client, "SMALL_BUSINESS", {"target_rate": "50"})
        _put(client, "SMALL_BUSINESS", {"target_rate": "0"})

        payload = client.get("/policies").json()
        item = {p["policy_code"]: p for p in payload["policies"]}["SMALL_BUSINESS"]
        assert item["target_rate"] == "50"


class TestAdminAuth:
    """관리자 토큰 검증."""

    def test_missing_header_is_401(self, client: TestClient) -> None:
        response = client.put("/policies/SMALL_BUSINESS/target-rate", json={"target_rate": "50"})
        assert response.status_code == 401

    def test_wrong_token_is_401(self, client: TestClient) -> None:
        response = client.put(
            "/policies/SMALL_BUSINESS/target-rate",
            json={"target_rate": "50"},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert response.status_code == 401

    def test_non_bearer_scheme_is_401(self, client: TestClient) -> None:
        response = client.put(
            "/policies/SMALL_BUSINESS/target-rate",
            json={"target_rate": "50"},
            headers={"Authorization": TEST_TOKEN},
        )
        assert response.status_code == 401

    def test_write_disabled_without_token_is_503(self, db_path: Path) -> None:
        """토큰이 설정되지 않으면 쓰기 API 는 비활성입니다."""
        client = TestClient(create_app(db_path))

        response = client.put(
            "/policies/SMALL_BUSINESS/target-rate",
            json={"target_rate": "50"},
            headers=AUTH,
        )

        assert response.status_code == 503

    def test_read_apis_work_without_token(self, db_path: Path) -> None:
        """토큰 미설정 환경에서도 조회 API 2종은 정상 동작합니다."""
        client = TestClient(create_app(db_path))

        assert client.get("/policies").status_code == 200
        assert client.get("/dashboard/summary").status_code == 200


class TestDashboardIntegration:
    """목표율 등록이 대시보드에 반영되는지 확인합니다."""

    @pytest.fixture
    def seeded(self, db_path: Path) -> Path:
        """구매·인증 데이터를 최소한으로 구성합니다."""
        company_repo = CompanyRepository(db_path)
        cert_repo = CertificationRepository(db_path)
        purchase_repo = PurchaseRepository(db_path)
        policy_repo = PolicyRepository(db_path)

        company = company_repo.insert(
            Company(business_no="1018116293", company_name="가기업", representative_name="김대표")
        )
        policy = policy_repo.find_by_policy_code("SMALL_BUSINESS")
        assert company.company_id is not None
        assert policy is not None
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
                business_no="1018116293",
                company_name="가기업",
                contract_date=date(2026, 3, 1),
                payment_date=date(2026, 3, 15),
                amount=Decimal("1000000"),
                company_id=company.company_id,
            )
        )
        return db_path

    def test_status_changes_after_target_rate_is_registered(self, seeded: Path) -> None:
        """목표율 등록 전에는 TARGET_RATE_NOT_SET, 등록 후에는 계산 상태입니다."""
        client = TestClient(create_app(seeded, admin_token=TEST_TOKEN))

        before = client.get("/dashboard/summary").json()
        item = {p["policy_code"]: p for p in before["policies"]}["SMALL_BUSINESS"]
        assert item["status"] == "TARGET_RATE_NOT_SET"

        _put(client, "SMALL_BUSINESS", {"target_rate": "50"})

        after = client.get("/dashboard/summary").json()
        item = {p["policy_code"]: p for p in after["policies"]}["SMALL_BUSINESS"]
        assert item["status"] != "TARGET_RATE_NOT_SET"
        assert item["target_rate"] == "50"
        assert Decimal(item["achievement_rate"]) == Decimal("200")

    def test_reset_returns_to_not_set(self, seeded: Path) -> None:
        """해제하면 다시 목표율 미설정 상태로 돌아갑니다."""
        client = TestClient(create_app(seeded, admin_token=TEST_TOKEN))
        _put(client, "SMALL_BUSINESS", {"target_rate": "50"})

        _put(client, "SMALL_BUSINESS", {"target_rate": None})

        payload = client.get("/dashboard/summary").json()
        item = {p["policy_code"]: p for p in payload["policies"]}["SMALL_BUSINESS"]
        assert item["status"] == "TARGET_RATE_NOT_SET"
        assert item["target_rate"] is None


class TestExistingBehaviourPreserved:
    """기존 API·예외 처리에 영향이 없는지 확인합니다."""

    def test_dashboard_endpoint_still_works(self, client: TestClient) -> None:
        assert client.get("/dashboard/summary").status_code == 200

    def test_no_new_global_exception_handlers(self, db_path: Path) -> None:
        """전역 예외 처리 방식을 바꾸지 않았습니다.

        목표율 관리 예외는 **엔드포인트 내부에서만** HTTP 로 변환합니다.
        전역 핸들러를 추가하면 기존 ``insert`` 경로에서 같은 예외가 발생할 때의
        응답까지 조용히 바뀌므로 등록하지 않습니다.
        """
        from procurement.admin import PolicyNotFoundError
        from procurement.calculators.procurement_achievement import CalculatorValidationError
        from procurement.database.policy_repository import PolicyValidationError

        app = create_app(db_path, admin_token=TEST_TOKEN)
        registered = set(app.exception_handlers)

        assert CalculatorValidationError in registered
        assert PolicyValidationError not in registered
        assert PolicyNotFoundError not in registered

    def test_new_endpoints_in_openapi(self, client: TestClient) -> None:
        paths = client.get("/openapi.json").json()["paths"]
        assert "/policies" in paths
        assert "/policies/{policy_code}/target-rate" in paths
