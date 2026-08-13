"""
procurement.app

FastAPI 애플리케이션과 **의존성 조립(composition root)** 을 정의합니다.

이 모듈은 저장소·계산기·서비스 계층을 한곳에서 조립해
:class:`DashboardApiService` 를 만들고, 이를 호출하는 HTTP 엔드포인트를
제공합니다. 계층 원칙은 다음과 같이 유지됩니다::

    FastAPI → DashboardApiService → DashboardDataService → Calculator → Repository

- HTTP 계층(엔드포인트)은 **오직 :class:`DashboardApiService` 만** 호출합니다.
- Calculator·Repository 에 직접 접근하지 않습니다(조립은 :func:`build_dashboard_api`
  한 곳에서만 수행).

.. note::
    JSON API 외에 브라우저용 Dashboard 화면(``GET /``)을 제공합니다. 화면은
    서버에서 값을 렌더링하지 않고 JSON API 를 호출해 그리므로 계층 구조에
    영향을 주지 않습니다. Swagger(OpenAPI) 문서는 ``/docs`` 에서 확인할 수
    있습니다.

.. warning::
    ``GET /dashboard/summary`` 는 **대상 연도(``year``)가 필수**입니다(**D-27**).
    연도를 생략하면 400 으로 거부하며, 전 기간을 임의로 합산한 값을 돌려주지
    않습니다. 어느 날짜로 연도를 나눌지(**D-24**)는 아직 확정되지 않았으므로,
    설정값 ``PURCHASE_PERIOD_DATE_FIELD`` 가 없으면 503 으로 거부합니다.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from procurement.admin import (
    PolicyAdminService,
    PolicyItemResponseModel,
    PolicyListResponseModel,
    PolicyNotFoundError,
    TargetRateUpdateRequest,
    build_admin_token_guard,
)
from procurement.api import DashboardApiService, DashboardResponseModel
from procurement.api.status_api import DataStatusApiService
from procurement.api.status_response import DataStatusResponseModel
from procurement.calculators import ProcurementAchievementCalculator
from procurement.calculators.procurement_achievement import CalculatorValidationError
from procurement.core.config import settings
from procurement.core.period import PeriodFilter
from procurement.dashboard import DashboardDataService
from procurement.dashboard.status_service import DataStatusService
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.import_batch_repository import ImportBatchRepository
from procurement.database.policy_repository import PolicyRepository, PolicyValidationError
from procurement.database.purchase_repository import PurchaseRepository
from procurement.web import (
    PolicyDisplayResponseModel,
    build_policy_display_response,
    read_index_html,
)


def build_dashboard_api(db_path: str | Path | None = None) -> DashboardApiService:
    """대시보드 API 서비스를 조립합니다(composition root).

    저장소 → 계산기 → :class:`DashboardDataService` → :class:`DashboardApiService`
    순으로 의존성을 생성·주입합니다. 등록 목표율 기반 요약을 사용하기 위해
    ``policy_repository`` 를 함께 주입합니다.

    Args:
        db_path: 사용할 SQLite DB 경로. ``None`` 이면 설정값(``settings.db_file``)을
            사용합니다.

    Returns:
        조립된 :class:`DashboardApiService`.
    """
    path: str | Path = db_path if db_path is not None else settings.db_file
    purchase_repo = PurchaseRepository(path)
    certification_repo = CertificationRepository(path)
    policy_repo = PolicyRepository(path)
    calculator = ProcurementAchievementCalculator(purchase_repo, certification_repo, policy_repo)
    data_service = DashboardDataService(calculator, policy_repository=policy_repo)
    return DashboardApiService(data_service)


def build_policy_admin(db_path: str | Path | None = None) -> PolicyAdminService:
    """정책 목표율 관리 서비스를 조립합니다(composition root).

    설정 경로는 계산 경로와 분리되어 있으므로 계산기·대시보드 서비스를
    주입하지 않습니다::

        PolicyAdminService → PolicyRepository → SQLite

    Args:
        db_path: 사용할 SQLite DB 경로. ``None`` 이면 설정값(``settings.db_file``)을
            사용합니다.

    Returns:
        조립된 :class:`PolicyAdminService`.
    """
    path: str | Path = db_path if db_path is not None else settings.db_file
    return PolicyAdminService(PolicyRepository(path))


def build_data_status_api(
    db_path: str | Path | None = None,
    data_mode: str | None = None,
    period_date_field: str | None = None,
) -> DataStatusApiService:
    """데이터 적재 현황 API 서비스를 조립합니다(composition root).

    계산 경로와 분리된 조회 전용 경로입니다::

        DataStatusApiService → DataStatusService → Repository → SQLite

    Args:
        db_path: 사용할 SQLite DB 경로. ``None`` 이면 설정값을 사용합니다.
        data_mode: 데이터 모드(``demo`` / ``operational``). ``None`` 이면
            설정값(``settings.DATA_MODE``)을 사용합니다.
        period_date_field: 기간 판정 날짜 컬럼. ``None`` 이면 설정값
            (``settings.PURCHASE_PERIOD_DATE_FIELD``)을 사용합니다.

    Returns:
        조립된 :class:`DataStatusApiService`.
    """
    path: str | Path = db_path if db_path is not None else settings.db_file
    status_service = DataStatusService(
        PurchaseRepository(path),
        CompanyRepository(path),
        CertificationRepository(path),
        PolicyRepository(path),
        ImportBatchRepository(path),
    )
    mode = data_mode if data_mode is not None else settings.DATA_MODE
    date_field = (
        period_date_field
        if period_date_field is not None
        else settings.PURCHASE_PERIOD_DATE_FIELD
    )
    return DataStatusApiService(status_service, mode, date_field)


def _require_period(year: int | None, date_field: str | None) -> PeriodFilter:
    """연도 요청을 기간 조건으로 변환합니다(연도 **필수**).

    Args:
        year: 요청된 연도. ``None`` 이면 400 으로 거부합니다.
        date_field: 기간 판정에 사용할 날짜 컬럼. ``None`` 이면 사용할 수 없습니다.

    Returns:
        :class:`PeriodFilter`.

    Raises:
        HTTPException: ``year`` 미지정 시 **400**(D-27 — 전 기간 합산 금지).
            ``year`` 를 지정했으나 ``date_field`` 가 설정되지 않은 경우 **503**
            (D-24 미확정 — 임의의 기준일로 계산하지 않음).
    """
    if year is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "대상 연도(year)를 지정해야 합니다. 전 기간을 합산한 값은 "
                "어느 기간의 실적인지 알 수 없으므로 제공하지 않습니다(D-27)."
            ),
        )
    if date_field is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "연도별 조회를 사용할 수 없습니다. 연도 귀속 기준일이 확정되지 "
                "않았습니다(D-24 · 고객 확인 항목 W-1). 확정 후 설정값 "
                "PURCHASE_PERIOD_DATE_FIELD 를 지정하면 활성화됩니다."
            ),
        )
    return PeriodFilter.for_year(year, date_field)


def create_app(
    db_path: str | Path | None = None,
    admin_token: str | None = None,
    data_mode: str | None = None,
    period_date_field: str | None = None,
) -> FastAPI:
    """FastAPI 애플리케이션을 생성합니다.

    Args:
        db_path: 조립에 사용할 DB 경로. ``None`` 이면 설정값을 사용합니다.
            테스트에서 격리 DB 를 주입할 때 사용합니다.
        admin_token: 설정 변경(쓰기) API 에 사용할 관리자 토큰. ``None`` 이면
            설정값(``settings.ADMIN_API_TOKEN``)을 사용합니다. 최종적으로 값이
            없으면 쓰기 API 는 503 으로 비활성화됩니다.
        data_mode: Dashboard 화면에 표시할 데이터 모드. ``None`` 이면 설정값
            (``settings.DATA_MODE``)을 사용합니다.
        period_date_field: 기간(연도) 판정에 사용할 날짜 컬럼. ``None`` 이면
            설정값(``settings.PURCHASE_PERIOD_DATE_FIELD``)을 사용합니다.
            최종적으로 값이 없으면 연도 지정 조회가 503 으로 거부됩니다
            (D-24 미확정 — 임의의 기준일을 쓰지 않기 위함).

    Returns:
        엔드포인트가 등록된 :class:`fastapi.FastAPI` 인스턴스.
    """
    dashboard_api = build_dashboard_api(db_path)
    policy_admin = build_policy_admin(db_path)
    date_field = (
        period_date_field
        if period_date_field is not None
        else settings.PURCHASE_PERIOD_DATE_FIELD
    )
    data_status_api = build_data_status_api(db_path, data_mode, date_field)
    token = admin_token if admin_token is not None else settings.ADMIN_API_TOKEN
    require_admin_token = build_admin_token_guard(token)

    app = FastAPI(
        title="Public Procurement Policy System API",
        version=settings.APP_VERSION,
        description="공공기관 우선구매 정책 달성률 대시보드 API",
    )

    @app.get(
        "/dashboard/summary",
        response_model=DashboardResponseModel,
        summary="등록 목표율 기반 대시보드 요약 조회",
        tags=["dashboard"],
    )
    def get_dashboard_summary(
        year: int | None = Query(
            default=None,
            ge=1900,
            le=2999,
            description=(
                "대상 회계연도(1/1 ~ 12/31, D-23). **필수입니다.** 생략하면 400 으로 "
                "거부합니다(D-27) — 전 기간을 임의로 합산하지 않습니다."
            ),
        ),
    ) -> DashboardResponseModel:
        """시스템에 등록된 목표율 기반 대시보드 요약을 반환합니다.

        해당 회계연도(1/1 ~ 12/31, **D-23**)의 구매만 집계합니다. 기간 조건은
        **분모(전체 구매액)와 분자(정책 구매액)에 동일하게** 적용되며, 계산 공식
        자체는 변경되지 않습니다.

        오류 응답은 두 가지입니다.

        - ``year`` **미지정 → 400** (**D-27**). 전 기간을 임의로 합산해
          돌려주지 않습니다. 어느 기간의 숫자인지 모호한 값을 만들지 않기 위함입니다.
        - ``year`` 지정 + 기간 판정 기준일 미설정 → **503**. 어느 날짜로 연도를
          나눌지는 **D-24(미확정)** 이며, 임의의 기준일로 숫자를 만들지 않습니다.
        """
        return dashboard_api.get_dashboard(_require_period(year, date_field))

    @app.get(
        "/dashboard/data-status",
        response_model=DataStatusResponseModel,
        summary="데이터 적재 현황 조회",
        tags=["dashboard"],
    )
    def get_data_status(
        year: int | None = Query(
            default=None,
            ge=1900,
            le=2999,
            description=(
                "화면이 선택한 연도. **현재는 조회 조건으로 사용되지 않고** "
                "응답에 그대로 되돌려 줍니다(기간 필터 미구현)."
            ),
        ),
    ) -> DataStatusResponseModel:
        """저장소에 적재된 데이터 현황을 반환합니다.

        달성률을 계산하지 않고 건수·금액 합계·일자 범위만 집계합니다.
        연도별 집계는 D-23 ~ D-27 확정 후 별도 Issue 에서 구현하므로, 응답의
        ``period_filter_applied`` 는 항상 ``false`` 입니다.
        """
        return data_status_api.get_data_status(year)

    @app.get(
        "/dashboard/policy-display",
        response_model=PolicyDisplayResponseModel,
        summary="정책별 개발 진행 상태(화면 표시용) 조회",
        tags=["dashboard"],
    )
    def get_policy_display() -> PolicyDisplayResponseModel:
        """정책별 화면 표시 정보를 반환합니다.

        값은 ``docs/DECISIONS.md`` 의 결정을 옮긴 것이며 계산에 사용되지
        않습니다. 화면이 "계산 가능"과 "계산 보류"를 구분하기 위해 사용합니다.
        """
        return build_policy_display_response()

    @app.get(
        "/",
        response_class=HTMLResponse,
        summary="Dashboard 화면",
        tags=["dashboard"],
        include_in_schema=False,
    )
    def get_dashboard_page() -> HTMLResponse:
        """Dashboard 화면(정적 HTML)을 반환합니다.

        페이지는 값을 서버에서 렌더링하지 않고, 브라우저에서 위 JSON API 를
        호출해 채웁니다.
        """
        return HTMLResponse(read_index_html())

    @app.get(
        "/policies",
        response_model=PolicyListResponseModel,
        summary="정책 목록 및 현재 목표율 조회",
        tags=["policies"],
    )
    def list_policies() -> PolicyListResponseModel:
        """등록된 정책과 현재 목표율을 반환합니다.

        비활성 정책도 포함하며 ``is_active`` 로 구분합니다. 목표율이 설정되지
        않은 정책은 ``target_rate`` 가 ``null`` 이고 ``target_rate_status`` 가
        ``NOT_SET`` 입니다.
        """
        return policy_admin.list_policies()

    @app.put(
        "/policies/{policy_code}/target-rate",
        response_model=PolicyItemResponseModel,
        summary="정책 목표율 설정·해제",
        tags=["policies"],
        dependencies=[Depends(require_admin_token)],
    )
    def update_target_rate(
        policy_code: str, payload: TargetRateUpdateRequest
    ) -> PolicyItemResponseModel:
        """정책의 목표율을 설정하거나 해제합니다.

        ``{"target_rate": null}`` 은 목표율 **해제**를 뜻합니다. ``target_rate``
        키가 아예 없으면 422 로 거부해 "변경하지 않음"과 "해제"를 구분합니다.

        예외는 이 엔드포인트 안에서만 HTTP 응답으로 변환합니다(전역 예외 처리
        방식을 변경하지 않기 위함).
        """
        try:
            return policy_admin.set_target_rate(policy_code, payload.target_rate)
        except PolicyNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PolicyValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.exception_handler(CalculatorValidationError)
    async def _handle_calculator_validation_error(
        request: Request, exc: CalculatorValidationError
    ) -> JSONResponse:
        """계산기 검증 오류를 422 응답으로 변환합니다."""
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    return app


#: 모듈 수준 ASGI 앱 — ``uvicorn procurement.app:app`` 으로 실행할 수 있습니다.
app = create_app()
