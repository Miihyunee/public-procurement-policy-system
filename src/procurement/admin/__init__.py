"""
procurement.admin

정책 **설정 관리**(목표율 등록) 계층 패키지.

달성률 계산과는 별개의 설정 경로를 담당합니다::

    FastAPI → PolicyAdminService → PolicyRepository → SQLite

대시보드 계산 계층(:mod:`procurement.api` · :mod:`procurement.dashboard` ·
:mod:`procurement.calculators`)을 변경하지 않습니다.
"""

from procurement.admin.auth import build_admin_token_guard
from procurement.admin.policy_admin import PolicyAdminService, PolicyNotFoundError
from procurement.admin.response import (
    PolicyItemResponseModel,
    PolicyListResponseModel,
    TargetRateUpdateRequest,
)

__all__ = [
    "PolicyAdminService",
    "PolicyItemResponseModel",
    "PolicyListResponseModel",
    "PolicyNotFoundError",
    "TargetRateUpdateRequest",
    "build_admin_token_guard",
]
