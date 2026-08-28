"""
Dashboard API 계층 테스트.

두 가지를 검증합니다.

1. 응답 모델(:class:`DashboardResponseModel` / :class:`PolicySummaryResponseModel`)
   의 변환·직렬화 규칙(Decimal→문자열, status 두 필드).
2. :class:`DashboardApiService` 가 :class:`DashboardDataService` 만 호출해 응답을
   생성하고, 검증 예외를 그대로 전파하는지.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from procurement.api import (
    DashboardApiService,
    DashboardResponseModel,
    PolicySummaryResponseModel,
)
from procurement.calculators import ProcurementAchievementCalculator
from procurement.calculators.procurement_achievement import CalculatorValidationError
from procurement.dashboard import DashboardDataService
from procurement.dashboard.models import (
    DashboardStatus,
    DashboardSummary,
    PolicySummary,
)
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Certification, Company, Policy, Purchase


# ----------------------------------------------------------------------
# 응답 모델 변환/직렬화 (순수 DTO 기반)
# ----------------------------------------------------------------------
def _policy_summary(status: DashboardStatus = DashboardStatus.SHORTAGE) -> PolicySummary:
    return PolicySummary(
        policy_id=1,
        policy_code="SMALL_BUSINESS",
        policy_name="중소기업",
        purchase_amount=Decimal("3000000"),
        total_purchase_amount=Decimal("10000000"),
        target_rate=Decimal("50"),
        achievement_rate=Decimal("60.00"),
        shortage_rate=Decimal("40.00"),
        status=status,
    )


class TestPolicySummaryResponseModel:
    """정책 요약 응답 모델의 필드·직렬화를 검증합니다."""

    def test_from_policy_summary_copies_fields(self) -> None:
        model = PolicySummaryResponseModel.from_policy_summary(_policy_summary())
        assert model.policy_id == 1
        assert model.policy_code == "SMALL_BUSINESS"
        assert model.policy_name == "중소기업"

    def test_status_split_into_code_and_label(self) -> None:
        """status 는 코드, status_label 은 한글 라벨로 분리됩니다."""
        model = PolicySummaryResponseModel.from_policy_summary(_policy_summary())
        assert model.status == "SHORTAGE"
        assert model.status_label == "부족"

    @pytest.mark.parametrize(
        ("status", "code", "label"),
        [
            (DashboardStatus.NORMAL, "NORMAL", "정상"),
            (DashboardStatus.WARNING, "WARNING", "주의"),
            (DashboardStatus.SHORTAGE, "SHORTAGE", "부족"),
        ],
    )
    def test_status_labels(
        self, status: DashboardStatus, code: str, label: str
    ) -> None:
        model = PolicySummaryResponseModel.from_policy_summary(_policy_summary(status))
        assert model.status == code
        assert model.status_label == label

    def test_decimal_serialized_as_string(self) -> None:
        """금액·비율 필드는 문자열로 직렬화됩니다(python 모드 포함)."""
        model = PolicySummaryResponseModel.from_policy_summary(_policy_summary())
        dumped = model.model_dump()
        assert dumped["purchase_amount"] == "3000000"
        assert dumped["total_purchase_amount"] == "10000000"
        assert dumped["target_rate"] == "50"
        assert dumped["achievement_rate"] == "60.00"
        assert dumped["shortage_rate"] == "40.00"
        assert all(isinstance(dumped[k], str) for k in ("purchase_amount", "target_rate"))

    def test_json_roundtrip_matches_dump(self) -> None:
        model = PolicySummaryResponseModel.from_policy_summary(_policy_summary())
        assert json.loads(model.model_dump_json()) == model.model_dump()


class TestDashboardResponseModel:
    """대시보드 전체 응답 모델의 구조·직렬화를 검증합니다."""

    def test_from_summary_structure(self) -> None:
        summary = DashboardSummary(
            total_purchase_amount=Decimal("10000000"),
            policy_summaries=[_policy_summary()],
        )
        model = DashboardResponseModel.from_summary(summary)
        assert model.total_purchase_amount == Decimal("10000000")
        assert len(model.policies) == 1
        assert model.policies[0].policy_code == "SMALL_BUSINESS"

    def test_total_serialized_as_string(self) -> None:
        summary = DashboardSummary(
            total_purchase_amount=Decimal("10000000"), policy_summaries=[]
        )
        dumped = DashboardResponseModel.from_summary(summary).model_dump()
        assert dumped["total_purchase_amount"] == "10000000"
        assert dumped["policies"] == []

    def test_empty_policies(self) -> None:
        summary = DashboardSummary(
            total_purchase_amount=Decimal("0"), policy_summaries=[]
        )
        model = DashboardResponseModel.from_summary(summary)
        assert model.policies == []

    def test_full_payload_shape(self) -> None:
        """스펙 예시와 동일한 응답 구조를 확인합니다."""
        summary = DashboardSummary(
            total_purchase_amount=Decimal("10000000"),
            policy_summaries=[_policy_summary()],
        )
        payload = DashboardResponseModel.from_summary(summary).model_dump()
        # 변경 사유(STEP 59): 결의일자 공란 알림 필드가 추가되었습니다. 이
        # 시험이 지키던 것은 "응답 구조가 정확히 이것뿐" 이라는 사실이므로,
        # 비교를 느슨하게 하지 않고 **새 필드를 기대값에 함께 적습니다.**
        # 기본값은 "해당 없음" 이며, 계산 결과와 무관합니다.
        assert payload == {
            "total_purchase_amount": "10000000",
            "missing_resolution_date": {"applies": False, "count": 0, "amount": "0"},
            "policies": [
                {
                    "policy_id": 1,
                    "policy_code": "SMALL_BUSINESS",
                    "policy_name": "중소기업",
                    "purchase_amount": "3000000",
                    "total_purchase_amount": "10000000",
                    "target_rate": "50",
                    "achievement_rate": "60.00",
                    "shortage_rate": "40.00",
                    "status": "SHORTAGE",
                    "status_label": "부족",
                }
            ],
        }


# ----------------------------------------------------------------------
# DashboardApiService 통합 (실제 Repository + 격리 DB)
# ----------------------------------------------------------------------
@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "test.db"
    CompanyRepository(path).create_table()
    PolicyRepository(path).create_table()
    CertificationRepository(path).create_table()
    PurchaseRepository(path).create_table()
    return path


@pytest.fixture
def policy_repo(db_path: Path) -> PolicyRepository:
    return PolicyRepository(db_path)


@pytest.fixture
def data_service(db_path: Path, policy_repo: PolicyRepository) -> DashboardDataService:
    calculator = ProcurementAchievementCalculator(
        PurchaseRepository(db_path), CertificationRepository(db_path), policy_repo
    )
    return DashboardDataService(calculator, policy_repository=policy_repo)


@pytest.fixture
def data_service_no_repo(db_path: Path) -> DashboardDataService:
    """policy_repository 미주입 서비스(등록 목표율 조회 불가)."""
    calculator = ProcurementAchievementCalculator(
        PurchaseRepository(db_path),
        CertificationRepository(db_path),
        PolicyRepository(db_path),
    )
    return DashboardDataService(calculator)


@pytest.fixture
def api(data_service: DashboardDataService) -> DashboardApiService:
    return DashboardApiService(data_service)


def _seed_policy_with_purchase(db_path: Path, target_rate: Decimal | None) -> int:
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
    return policy.policy_id


class TestDashboardApiServiceWithTargets:
    """외부 목표율 입력 방식(get_dashboard_with_targets)을 검증합니다."""

    def test_returns_response_model(self, api: DashboardApiService, db_path: Path) -> None:
        policy_id = _seed_policy_with_purchase(db_path, target_rate=None)
        response = api.get_dashboard_with_targets({policy_id: Decimal("50")})
        assert isinstance(response, DashboardResponseModel)
        assert response.total_purchase_amount == Decimal("10000000")
        assert len(response.policies) == 1
        item = response.policies[0]
        assert item.achievement_rate == Decimal("60.00")
        assert item.status == "SHORTAGE"
        assert item.status_label == "부족"

    def test_empty_targets_total_only(self, api: DashboardApiService, db_path: Path) -> None:
        _seed_policy_with_purchase(db_path, target_rate=None)
        response = api.get_dashboard_with_targets({})
        assert response.total_purchase_amount == Decimal("10000000")
        assert response.policies == []

    def test_unknown_policy_propagates_validation_error(
        self, api: DashboardApiService
    ) -> None:
        with pytest.raises(CalculatorValidationError):
            api.get_dashboard_with_targets({99999: Decimal("50")})


class TestDashboardApiServiceRegisteredTargets:
    """등록된 목표율 방식(get_dashboard)을 검증합니다."""

    def test_uses_registered_target_rate(
        self, api: DashboardApiService, db_path: Path
    ) -> None:
        _seed_policy_with_purchase(db_path, target_rate=Decimal("50"))
        response = api.get_dashboard()
        assert len(response.policies) == 1
        item = response.policies[0]
        assert item.target_rate == Decimal("50")
        assert item.achievement_rate == Decimal("60.00")
        assert item.status == "SHORTAGE"

    def test_serialized_payload(self, api: DashboardApiService, db_path: Path) -> None:
        _seed_policy_with_purchase(db_path, target_rate=Decimal("50"))
        payload = api.get_dashboard().model_dump()
        assert payload["total_purchase_amount"] == "10000000"
        assert payload["policies"][0]["target_rate"] == "50"
        assert payload["policies"][0]["status_label"] == "부족"

    def test_without_policy_repository_propagates_value_error(
        self, data_service_no_repo: DashboardDataService
    ) -> None:
        api = DashboardApiService(data_service_no_repo)
        with pytest.raises(ValueError):
            api.get_dashboard()


# ----------------------------------------------------------------------
# 목표율 미설정(TARGET_RATE_NOT_SET) 처리 — PM 지정 Test 1~6
# ----------------------------------------------------------------------
class TestTargetRateNotSet:
    """목표율이 등록되지 않은 정책의 표현 방식을 검증합니다.

    핵심 목적은 화면이 **"정책 없음"과 "목표율 미설정"을 구분**할 수 있게
    하는 것입니다.
    """

    def test_1_policy_is_included_in_response(
        self, api: DashboardApiService, db_path: Path
    ) -> None:
        """Test 1 — 목표율 NULL 정책이 응답에 포함된다(제거되지 않음)."""
        _seed_policy_with_purchase(db_path, target_rate=None)
        response = api.get_dashboard()
        assert [item.policy_code for item in response.policies] == ["SMALL_BUSINESS"]

    def test_2_achievement_rate_is_null(
        self, api: DashboardApiService, db_path: Path
    ) -> None:
        """Test 2 — 달성률은 계산하지 않고 NULL 이다(0 이 아님)."""
        _seed_policy_with_purchase(db_path, target_rate=None)
        item = api.get_dashboard().policies[0]
        assert item.achievement_rate is None

    def test_3_shortage_rate_is_null(self, api: DashboardApiService, db_path: Path) -> None:
        """Test 3 — 부족률도 계산하지 않고 NULL 이다."""
        _seed_policy_with_purchase(db_path, target_rate=None)
        item = api.get_dashboard().policies[0]
        assert item.shortage_rate is None

    def test_4_status_is_target_rate_not_set(
        self, api: DashboardApiService, db_path: Path
    ) -> None:
        """Test 4 — status 는 TARGET_RATE_NOT_SET 이다."""
        _seed_policy_with_purchase(db_path, target_rate=None)
        assert api.get_dashboard().policies[0].status == "TARGET_RATE_NOT_SET"

    def test_5_status_label_is_korean(self, api: DashboardApiService, db_path: Path) -> None:
        """Test 5 — status_label 은 '목표율 미설정' 이다."""
        _seed_policy_with_purchase(db_path, target_rate=None)
        assert api.get_dashboard().policies[0].status_label == "목표율 미설정"

    def test_6_existing_calculation_is_unchanged(
        self, api: DashboardApiService, db_path: Path
    ) -> None:
        """Test 6 — 목표율이 있는 정책의 계산 결과는 기존과 동일하다.

        정책비율 30%, 목표 50% → 달성률 60%, 부족률 40%, 상태 '부족'.
        """
        _seed_policy_with_purchase(db_path, target_rate=Decimal("50"))
        item = api.get_dashboard().policies[0]
        assert item.target_rate == Decimal("50")
        assert item.purchase_amount == Decimal("3000000")
        assert item.achievement_rate == Decimal("60.00")
        assert item.shortage_rate == Decimal("40.00")
        assert item.status == "SHORTAGE"
        assert item.status_label == "부족"

    def test_target_rate_and_purchase_amount_are_null(
        self, api: DashboardApiService, db_path: Path
    ) -> None:
        """목표율·정책 구매액도 NULL 이다(계산하지 않았음을 의미)."""
        _seed_policy_with_purchase(db_path, target_rate=None)
        item = api.get_dashboard().policies[0]
        assert item.target_rate is None
        assert item.purchase_amount is None

    def test_total_purchase_amount_is_still_aggregated(
        self, api: DashboardApiService, db_path: Path
    ) -> None:
        """전체 구매액은 목표율과 무관하게 집계된다."""
        _seed_policy_with_purchase(db_path, target_rate=None)
        assert api.get_dashboard().total_purchase_amount == Decimal("10000000")

    def test_serialized_payload_uses_json_null(
        self, api: DashboardApiService, db_path: Path
    ) -> None:
        """직렬화 시 문자열 'None' 이 아니라 JSON null 로 표현된다."""
        _seed_policy_with_purchase(db_path, target_rate=None)
        payload = json.loads(api.get_dashboard().model_dump_json())
        item = payload["policies"][0]
        assert item["target_rate"] is None
        assert item["achievement_rate"] is None
        assert item["shortage_rate"] is None
        assert item["purchase_amount"] is None
        assert item["status"] == "TARGET_RATE_NOT_SET"
        assert item["status_label"] == "목표율 미설정"

    def test_mixed_policies_are_both_represented(
        self, api: DashboardApiService, db_path: Path, policy_repo: PolicyRepository
    ) -> None:
        """목표율이 있는 정책과 없는 정책이 한 응답에 함께 표현된다."""
        _seed_policy_with_purchase(db_path, target_rate=Decimal("50"))
        policy_repo.insert(Policy(policy_code="WOMAN", policy_name="여성기업", target_rate=None))

        by_code = {item.policy_code: item for item in api.get_dashboard().policies}
        assert set(by_code) == {"SMALL_BUSINESS", "WOMAN"}
        assert by_code["SMALL_BUSINESS"].status == "SHORTAGE"
        assert by_code["WOMAN"].status == "TARGET_RATE_NOT_SET"


class TestTargetRateNotSetStatusModel:
    """DashboardStatus 열거형에 추가된 상태를 검증합니다."""

    def test_status_value_and_label(self) -> None:
        assert DashboardStatus.TARGET_RATE_NOT_SET.value == "TARGET_RATE_NOT_SET"
        assert DashboardStatus.TARGET_RATE_NOT_SET.label == "목표율 미설정"

    def test_never_returned_by_rate_judgement(self) -> None:
        """달성률 판정은 이 상태를 반환하지 않는다(계산 불가 상태이므로)."""
        for rate in (Decimal("0"), Decimal("79.99"), Decimal("80"), Decimal("100")):
            assert (
                DashboardStatus.from_achievement_rate(rate)
                is not DashboardStatus.TARGET_RATE_NOT_SET
            )
