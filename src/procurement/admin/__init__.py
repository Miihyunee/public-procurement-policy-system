"""
procurement.admin

정책 **설정 관리**(목표율 등록) 계층 패키지.

달성률 계산과는 별개의 설정 경로를 담당합니다::

    FastAPI → PolicyAdminService       → PolicyRepository       → SQLite
    FastAPI → PolicyTargetAdminService → PolicyTargetRepository → SQLite

연도별 목표비율(:class:`PolicyTargetAdminService`)이 **정본**이며, 정책 단위
목표율(:class:`PolicyAdminService`)은 하위호환을 위해 남겨 둔 경로입니다
(``DECISIONS.md`` §0.20).

대시보드 계산 계층(:mod:`procurement.api` · :mod:`procurement.dashboard` ·
:mod:`procurement.calculators`)을 변경하지 않습니다.
"""

from procurement.admin.auth import build_admin_token_guard
from procurement.admin.policy_admin import PolicyAdminService, PolicyNotFoundError
from procurement.admin.policy_company_source_response import (
    NOT_REGISTERED,
    REGISTERED,
    PolicyCompanySourceItemModel,
    PolicyCompanySourceListModel,
)
from procurement.admin.policy_target_admin import PolicyTargetAdminService
from procurement.admin.policy_target_response import (
    PolicyTargetItemModel,
    PolicyTargetListResponseModel,
    PolicyTargetUpdateRequest,
)
from procurement.admin.response import (
    PolicyItemResponseModel,
    PolicyListResponseModel,
    TargetRateUpdateRequest,
)

__all__ = [
    "PolicyAdminService",
    "NOT_REGISTERED",
    "REGISTERED",
    "PolicyCompanySourceItemModel",
    "PolicyCompanySourceListModel",
    "PolicyTargetAdminService",
    "PolicyTargetItemModel",
    "PolicyTargetListResponseModel",
    "PolicyTargetUpdateRequest",
    "PolicyItemResponseModel",
    "PolicyListResponseModel",
    "PolicyNotFoundError",
    "TargetRateUpdateRequest",
    "build_admin_token_guard",
]
