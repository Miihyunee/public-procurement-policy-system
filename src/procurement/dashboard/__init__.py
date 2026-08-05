"""
procurement.dashboard

기관 구매실적 대시보드를 위한 데이터 계층 패키지.

Calculator 계산 결과를 화면이 바로 사용할 수 있는 요약 DTO 로 변환합니다::

    from procurement.dashboard import DashboardDataService

    service = DashboardDataService(calculator)
    summary = service.build_summary({small_biz_policy_id: Decimal("50")})

.. note::
    UI·API·차트는 이후 Issue(#20 API / #21 UI / #22 상세화면)에서 다룹니다.
    본 패키지는 데이터 생성 계층만 제공합니다.
"""

from procurement.dashboard.data_service import DashboardDataService
from procurement.dashboard.models import (
    DashboardStatus,
    DashboardSummary,
    PolicySummary,
)

__all__ = [
    "DashboardDataService",
    "DashboardStatus",
    "DashboardSummary",
    "PolicySummary",
]
