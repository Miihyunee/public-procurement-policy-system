"""
procurement.api.status_api

데이터 적재 현황을 **API 응답 모델**로 제공하는 API 서비스 계층입니다.

기존 :class:`procurement.api.dashboard_api.DashboardApiService` 를 수정하지 않고
나란히 두는 별도 서비스입니다. 계층 원칙은 동일합니다::

    DataStatusApiService → DataStatusService → Repository

HTTP 계층은 이 서비스만 호출하며 저장소에 직접 접근하지 않습니다.
"""

from __future__ import annotations

from procurement.api.status_response import DataStatusResponseModel
from procurement.dashboard.status_service import DataStatusService


class DataStatusApiService:
    """데이터 적재 현황을 API 응답 모델로 변환해 제공합니다."""

    def __init__(
        self,
        status_service: DataStatusService,
        data_mode: str,
        period_date_field: str | None = None,
    ) -> None:
        """서비스를 초기화합니다.

        Args:
            status_service: 적재 현황을 집계할 :class:`DataStatusService`.
            data_mode: 현재 데이터 모드(``demo`` / ``operational``). 응답에 그대로
                실려 화면의 ``DEMO / SAMPLE DATA`` 표시를 결정합니다.
            period_date_field: 기간 판정에 사용하도록 설정된 날짜 컬럼.
                ``None`` 이면 기간 조회를 사용할 수 없습니다(D-24 미확정).
        """
        self._status_service = status_service
        self._data_mode = data_mode
        self._period_date_field = period_date_field

    def get_data_status(self, requested_year: int | None = None) -> DataStatusResponseModel:
        """적재 현황 응답을 반환합니다.

        Args:
            requested_year: 화면이 선택한 연도. **조회 조건으로 사용되지 않으며**
                응답에 그대로 되돌려 줍니다(기간 필터는 미구현).

        Returns:
            :class:`DataStatusResponseModel`.
        """
        status = self._status_service.build_status()
        return DataStatusResponseModel.from_status(
            status,
            data_mode=self._data_mode,
            requested_year=requested_year,
            period_date_field=self._period_date_field,
        )
