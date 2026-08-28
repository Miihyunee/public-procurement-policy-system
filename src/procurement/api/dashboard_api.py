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

from procurement.api.response import (
    DashboardResponseModel,
    MissingResolutionDateListResponseModel,
)
from procurement.core.period import PeriodFilter
from procurement.dashboard.data_service import DashboardDataService
from procurement.models.purchase import Purchase


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

    def get_dashboard(self, period: PeriodFilter | None = None) -> DashboardResponseModel:
        """시스템에 등록된 목표율 기반 대시보드 응답을 반환합니다.

        내부적으로
        :meth:`DashboardDataService.build_summary_from_registered_targets` 를
        호출합니다.

        Args:
            period: 적용할 기간 조건. ``None`` 이면 기간 제한 없음(기존 동작).

        Returns:
            :class:`DashboardResponseModel`.

        Raises:
            ValueError: 주입된 :class:`DashboardDataService` 에
                ``policy_repository`` 가 설정되지 않은 경우(그대로 전파).
            CalculatorValidationError: 계산기 검증 실패 시(그대로 전파).
        """
        summary = self._dashboard_service.build_summary_from_registered_targets(period)
        return DashboardResponseModel.from_summary(summary)

    def get_missing_resolution_date(
        self, period: PeriodFilter | None = None
    ) -> MissingResolutionDateListResponseModel:
        """결의일자가 없어 기간 산정에서 빠진 구매를 **행 단위로** 반환합니다.

        :meth:`get_dashboard` 응답의 ``missing_resolution_date`` 가 알려 주는
        건수·금액과 **같은 모집단**을 행으로 펼친 것입니다. 담당자가 어떤
        행인지 직접 확인할 수 있게 하는 것이 목적입니다.

        .. warning::
            ⛔ **조회 전용입니다.** 결의일자를 채우거나 다른 날짜로 대체하지
            않고, 어떤 행도 수정하지 않으며, 달성률에 영향을 주지 않습니다.

        .. warning::
            ⛔ **판정하지 않습니다.** 이 행들을 "오류"·"무효" 로 분류하지
            않으며, 어떻게 처리할지는 아직 정해지지 않았습니다.

        Args:
            period: 지금 화면이 보고 있는 기간 조건. **범위 조건으로 쓰지
                않습니다** — 결의일자 기준 조회인지 판단하는 데만 씁니다.
                결의일자 기준이 아니면 빈 목록을 반환합니다.

        Returns:
            :class:`MissingResolutionDateListResponseModel`.
        """
        rows = self._dashboard_service.list_missing_resolution_date(period)
        return MissingResolutionDateListResponseModel.from_purchases(rows)

    def list_missing_resolution_date_rows(
        self, period: PeriodFilter | None = None
    ) -> list[Purchase]:
        """CSV 내보내기용으로 **같은 대상**을 행 그대로 돌려줍니다.

        :meth:`get_missing_resolution_date` 와 **완전히 같은 호출**을 씁니다 —
        화면에서 보던 것과 다른 파일이 내려오면 안 되기 때문입니다. 응답 모델로
        감싸지 않는 이유는, CSV 를 한 줄씩 흘려보내려면 도메인 행이 필요하기
        때문입니다(기존 검토·미적재 CSV 와 같은 방식).

        .. warning::
            ⛔ **조회 전용입니다.** 계산기를 부르지 않고, 어떤 행도 만들거나
            바꾸지 않습니다.

        Args:
            period: 지금 화면이 보고 있는 기간 조건. 범위 조건으로 쓰지 않으며,
                결의일자 기준 조회인지 판단하는 데만 씁니다.

        Returns:
            :class:`Purchase` 목록(``purchase_id`` 오름차순). 없으면 빈 목록.
        """
        return self._dashboard_service.list_missing_resolution_date(period)

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
