"""
procurement.api.dashboard_api

대시보드 데이터를 **API 응답 모델**로 제공하는 API 서비스 계층입니다.

:class:`DashboardApiService` 는 :class:`DashboardDataService` 만 호출해
:class:`DashboardSummary` 를 얻은 뒤, 이를 API 응답 전용 Pydantic 모델
(:class:`DashboardResponseModel`)로 변환합니다.

계층 원칙(유지)::

    DashboardApiService → DashboardDataService → Calculator → Repository

API 계층은 오직 :class:`DashboardDataService` 만 사용하며, Calculator 나
Repository 에 직접 접근하지 않습니다. 기존 :class:`DashboardDataService` 는
변경하지 않고 응답 변환만 수행합니다.

.. note::
    본 계층은 HTTP 서버·라우터·엔드포인트·인증을 포함하지 않습니다(범위 밖).
    응답 데이터(payload) 생성까지만 담당하며, FastAPI 등 HTTP 바인딩은 후속
    Issue 에서 본 계층을 재사용해 추가합니다.
"""

from __future__ import annotations

from decimal import Decimal

from procurement.api.response import DashboardResponseModel
from procurement.dashboard.data_service import DashboardDataService


class DashboardApiService:
    """대시보드 요약을 API 응답 모델로 변환해 제공합니다."""

    def __init__(self, dashboard_service: DashboardDataService) -> None:
        """서비스를 초기화합니다.

        Args:
            dashboard_service: 요약 데이터를 생성할
                :class:`DashboardDataService`. API 계층은 이 서비스만 호출하며
                Calculator·Repository 에 직접 접근하지 않습니다.
        """
        self._dashboard_service = dashboard_service

    def get_dashboard(self) -> DashboardResponseModel:
        """시스템에 등록된 목표율 기반 대시보드 응답을 반환합니다.

        내부적으로
        :meth:`DashboardDataService.build_summary_from_registered_targets` 를
        호출합니다.

        Returns:
            :class:`DashboardResponseModel`.

        Raises:
            ValueError: 주입된 :class:`DashboardDataService` 에
                ``policy_repository`` 가 설정되지 않은 경우(그대로 전파).
            CalculatorValidationError: 계산기 검증 실패 시(그대로 전파).
        """
        summary = self._dashboard_service.build_summary_from_registered_targets()
        return DashboardResponseModel.from_summary(summary)

    def get_dashboard_with_targets(
        self, target_rates: dict[int, Decimal]
    ) -> DashboardResponseModel:
        """외부에서 입력한 목표율 기반 대시보드 응답을 반환합니다(하위호환).

        내부적으로 :meth:`DashboardDataService.build_summary` 를 호출합니다.

        Args:
            target_rates: ``{policy_id: 목표율}`` 매핑. 비어 있으면 전체
                구매액만 담긴 응답이 반환됩니다.

        Returns:
            :class:`DashboardResponseModel`.

        Raises:
            CalculatorValidationError: 목표율이 0 이하이거나 존재하지 않는
                정책이 포함된 경우(계산기 검증 그대로 전파).
        """
        summary = self._dashboard_service.build_summary(target_rates)
        return DashboardResponseModel.from_summary(summary)
