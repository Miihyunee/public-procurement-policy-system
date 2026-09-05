"""
procurement.api

대시보드 데이터를 API 응답 형태로 제공하는 API 계층 패키지.

:class:`DashboardDataService` 가 생성한 요약을 JSON 직렬화 가능한 Pydantic 응답
모델로 변환합니다::

    from procurement.api import DashboardApiService

    api = DashboardApiService(dashboard_service)
    response = api.get_dashboard()          # 등록된 목표율 기반
    payload = response.model_dump()          # 직렬화(Decimal→문자열)

.. note::
    본 패키지는 응답 데이터(payload) 생성까지만 담당합니다. HTTP 서버·라우터·
    엔드포인트·인증·UI·차트는 포함하지 않으며(범위 밖), FastAPI 등 HTTP 바인딩은
    후속 Issue 에서 본 계층을 재사용해 추가합니다.
"""

from procurement.api.dashboard_api import DashboardApiService
from procurement.api.response import (
    DashboardResponseModel,
    MissingResolutionDateListResponseModel,
    PolicySummaryResponseModel,
)

__all__ = [
    "DashboardApiService",
    "DashboardResponseModel",
    "MissingResolutionDateListResponseModel",
    "PolicySummaryResponseModel",
]
