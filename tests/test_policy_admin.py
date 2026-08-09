"""
PolicyAdminService 테스트.

정책 목표율 **설정 경로**(Repository → Admin Service)를 검증합니다.
계산 경로(Calculator·Dashboard)는 사용하지 않습니다.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from procurement.admin import PolicyAdminService, PolicyNotFoundError
from procurement.admin.response import TARGET_RATE_NOT_SET, TARGET_RATE_SET
from procurement.database.policy_repository import PolicyRepository, PolicyValidationError
from procurement.models import Policy

#: 정본 정책 코드(현재 ``main`` 의 seed 기준).
SEED_CODES = ("SMALL_BUSINESS", "WOMAN", "DISABLED", "STARTUP", "GREEN")


@pytest.fixture
def repo(tmp_path: Path) -> PolicyRepository:
    repository = PolicyRepository(tmp_path / "admin.db")
    repository.create_table()
    return repository


@pytest.fixture
def service(repo: PolicyRepository) -> PolicyAdminService:
    """정본 5종이 목표율 미설정(NULL) 상태로 등록된 서비스를 반환합니다."""
    for code in SEED_CODES:
        repo.insert(Policy(policy_code=code, policy_name=code))
    return PolicyAdminService(repo)


class TestListPolicies:
    """정책 목록 조회를 검증합니다."""

    def test_returns_all_seed_policies(self, service: PolicyAdminService) -> None:
        result = service.list_policies()
        assert [item.policy_code for item in result.policies] == list(SEED_CODES)

    def test_null_target_rate_status(self, service: PolicyAdminService) -> None:
        """목표율 미설정 정책은 NOT_SET 으로 표시됩니다."""
        result = service.list_policies()
        assert all(item.target_rate is None for item in result.policies)
        assert all(item.target_rate_status == TARGET_RATE_NOT_SET for item in result.policies)

    def test_status_becomes_set_after_update(self, service: PolicyAdminService) -> None:
        service.set_target_rate("SMALL_BUSINESS", "50")

        result = service.list_policies()
        item = {p.policy_code: p for p in result.policies}["SMALL_BUSINESS"]
        assert item.target_rate == Decimal("50")
        assert item.target_rate_status == TARGET_RATE_SET

    def test_includes_inactive_policy(self, repo: PolicyRepository) -> None:
        """비활성 정책도 목록에 포함되고 is_active 로 구분됩니다."""
        repo.insert(Policy(policy_code="OFF", policy_name="폐지", is_active=False))

        result = PolicyAdminService(repo).list_policies()

        assert [item.policy_code for item in result.policies] == ["OFF"]
        assert result.policies[0].is_active is False


class TestSetTargetRate:
    """목표율 설정·해제를 검증합니다."""

    def test_sets_value(self, service: PolicyAdminService) -> None:
        item = service.set_target_rate("SMALL_BUSINESS", "50")
        assert item.target_rate == Decimal("50")
        assert item.target_rate_status == TARGET_RATE_SET

    def test_preserves_decimal_precision(self, service: PolicyAdminService) -> None:
        item = service.set_target_rate("WOMAN", "8.05")
        assert item.target_rate == Decimal("8.05")

    def test_resets_to_none(self, service: PolicyAdminService) -> None:
        """명시적 None 은 목표율 해제입니다."""
        service.set_target_rate("STARTUP", "20")

        item = service.set_target_rate("STARTUP", None)

        assert item.target_rate is None
        assert item.target_rate_status == TARGET_RATE_NOT_SET

    def test_unknown_policy_code_raises(self, service: PolicyAdminService) -> None:
        with pytest.raises(PolicyNotFoundError):
            service.set_target_rate("NOT_EXIST", "10")

    def test_non_numeric_raises(self, service: PolicyAdminService) -> None:
        with pytest.raises(PolicyValidationError, match="숫자 형식"):
            service.set_target_rate("DISABLED", "abc")

    def test_empty_string_raises(self, service: PolicyAdminService) -> None:
        with pytest.raises(PolicyValidationError, match="숫자 형식"):
            service.set_target_rate("DISABLED", "")

    def test_nan_raises(self, service: PolicyAdminService) -> None:
        """Decimal 이 받아들이는 NaN·Infinity 도 거부합니다."""
        with pytest.raises(PolicyValidationError, match="숫자 형식"):
            service.set_target_rate("DISABLED", "NaN")

    def test_zero_raises(self, service: PolicyAdminService) -> None:
        with pytest.raises(PolicyValidationError, match="0 보다 커야"):
            service.set_target_rate("DISABLED", "0")

    def test_over_max_raises(self, service: PolicyAdminService) -> None:
        with pytest.raises(PolicyValidationError, match="이하여야"):
            service.set_target_rate("DISABLED", "100.01")

    def test_inactive_policy_raises(self, repo: PolicyRepository) -> None:
        """비활성 정책의 목표율은 변경할 수 없습니다."""
        repo.insert(Policy(policy_code="OFF", policy_name="폐지", is_active=False))

        with pytest.raises(PolicyValidationError, match="비활성"):
            PolicyAdminService(repo).set_target_rate("OFF", "10")

    def test_inactive_policy_value_unchanged(self, repo: PolicyRepository) -> None:
        """거부된 요청은 값을 바꾸지 않습니다."""
        repo.insert(
            Policy(
                policy_code="OFF",
                policy_name="폐지",
                is_active=False,
                target_rate=Decimal("30"),
            )
        )

        with pytest.raises(PolicyValidationError):
            PolicyAdminService(repo).set_target_rate("OFF", "10")

        found = repo.find_by_policy_code("OFF")
        assert found is not None
        assert found.target_rate == Decimal("30")

    def test_idempotent(self, service: PolicyAdminService) -> None:
        first = service.set_target_rate("SMALL_BUSINESS", "50")
        second = service.set_target_rate("SMALL_BUSINESS", "50")
        assert first.target_rate == second.target_rate == Decimal("50")


class TestLayering:
    """설정 경로가 계산 경로와 분리되어 있는지 확인합니다."""

    def test_service_needs_only_policy_repository(self, repo: PolicyRepository) -> None:
        """저장소 하나만으로 생성됩니다(계산기·대시보드 의존성 없음)."""
        assert PolicyAdminService(repo) is not None
