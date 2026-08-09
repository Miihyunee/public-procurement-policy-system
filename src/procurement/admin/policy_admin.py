"""
procurement.admin.policy_admin

정책 **목표율 설정**을 담당하는 관리 서비스 계층입니다.

목표율 설정은 달성률 계산이 아니라 **설정 변경**이므로, 계산 경로와 분리된
경로를 사용합니다::

    설정 경로   FastAPI → PolicyAdminService → PolicyRepository → SQLite
    계산 경로   FastAPI → DashboardApiService → DashboardDataService
                        → Calculator → Repository

:class:`PolicyAdminService` 는 Calculator 를 사용하지 않으며, 대시보드 계층
(:mod:`procurement.dashboard` · :mod:`procurement.api`)을 변경하지 않습니다.

.. note::
    본 서비스는 목표율 **값** 을 알지 못합니다. 공식 근거가 확인된 목표율을
    운영자가 등록하기 위한 통로일 뿐이며, 어떤 정책의 목표율도 코드에
    기본값으로 넣지 않습니다.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from procurement.admin.response import PolicyItemResponseModel, PolicyListResponseModel
from procurement.database.policy_repository import PolicyRepository, PolicyValidationError


class PolicyNotFoundError(LookupError):
    """요청한 정책 코드가 존재하지 않을 때 발생하는 예외."""


class PolicyAdminService:
    """정책 목표율 조회·변경 유스케이스를 제공합니다."""

    def __init__(self, policy_repository: PolicyRepository) -> None:
        """서비스를 초기화합니다.

        Args:
            policy_repository: 정책 저장소. 본 서비스는 이 저장소만 사용하며
                계산기·대시보드 서비스에 의존하지 않습니다.
        """
        self._policy_repository = policy_repository

    def list_policies(self) -> PolicyListResponseModel:
        """등록된 정책과 현재 목표율을 조회합니다.

        비활성 정책도 포함해 반환합니다. 비활성 정책은 목표율을 변경할 수
        없지만, 목록에서까지 감추면 변경되지 않는 이유를 확인할 수 없기
        때문입니다. 활성 여부는 ``is_active`` 로 구분합니다.

        Returns:
            :class:`PolicyListResponseModel`.
        """
        return PolicyListResponseModel.from_policies(self._policy_repository.find_all())

    def set_target_rate(self, policy_code: str, target_rate: str | None) -> PolicyItemResponseModel:
        """정책의 목표율을 설정하거나 해제합니다.

        Args:
            policy_code: 대상 정책 코드.
            target_rate: 새 목표율 문자열(예: ``"8.0"``). ``None`` 이면 목표율을
                **해제**합니다(미설정으로 되돌림).

        Returns:
            변경된 정책의 :class:`PolicyItemResponseModel`.

        Raises:
            PolicyNotFoundError: 해당 정책 코드가 없는 경우.
            PolicyValidationError: 목표율이 숫자 형식이 아니거나 허용 범위를
                벗어난 경우, 또는 **비활성 정책**의 목표율을 변경하려는 경우.
        """
        policy = self._policy_repository.find_by_policy_code(policy_code)
        if policy is None:
            raise PolicyNotFoundError(f"존재하지 않는 정책 코드입니다: {policy_code}")
        if not policy.is_active:
            raise PolicyValidationError(f"비활성 정책의 목표율은 변경할 수 없습니다: {policy_code}")

        updated = self._policy_repository.update_target_rate(
            policy_code, self._parse_target_rate(target_rate)
        )
        if updated is None:  # pragma: no cover - 조회 직후 삭제된 경우에만 도달
            raise PolicyNotFoundError(f"존재하지 않는 정책 코드입니다: {policy_code}")
        return PolicyItemResponseModel.from_policy(updated)

    @staticmethod
    def _parse_target_rate(value: str | None) -> Decimal | None:
        """목표율 문자열을 ``Decimal`` 로 변환합니다 (``None`` 은 해제).

        Raises:
            PolicyValidationError: 숫자로 해석할 수 없는 문자열인 경우.
        """
        if value is None:
            return None
        try:
            parsed = Decimal(value.strip())
        except (InvalidOperation, ValueError) as exc:
            raise PolicyValidationError(f"목표율은 숫자 형식이어야 합니다: {value!r}") from exc
        if not parsed.is_finite():
            raise PolicyValidationError(f"목표율은 숫자 형식이어야 합니다: {value!r}")
        return parsed
