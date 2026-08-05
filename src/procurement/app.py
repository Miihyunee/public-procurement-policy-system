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
    이번 범위는 **등록 목표율 기반 대시보드 요약**(``GET /dashboard/summary``)
    뿐입니다. 외부 목표율 입력 방식은 후속 Issue 로 분리합니다. 응답은 JSON 전용이며,
    Swagger(OpenAPI) 문서는 ``/docs`` 에서 확인할 수 있습니다.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from procurement.api import DashboardApiService, DashboardResponseModel
from procurement.calculators import ProcurementAchievementCalculator
from procurement.calculators.procurement_achievement import CalculatorValidationError
from procurement.core.config import settings
from procurement.dashboard import DashboardDataService
from procurement.database.certification_repository import CertificationRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository


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


def create_app(db_path: str | Path | None = None) -> FastAPI:
    """FastAPI 애플리케이션을 생성합니다.

    Args:
        db_path: 조립에 사용할 DB 경로. ``None`` 이면 설정값을 사용합니다.
            테스트에서 격리 DB 를 주입할 때 사용합니다.

    Returns:
        엔드포인트가 등록된 :class:`fastapi.FastAPI` 인스턴스.
    """
    dashboard_api = build_dashboard_api(db_path)

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
    def get_dashboard_summary() -> DashboardResponseModel:
        """시스템에 등록된 목표율 기반 대시보드 요약을 반환합니다."""
        return dashboard_api.get_dashboard()

    @app.exception_handler(CalculatorValidationError)
    async def _handle_calculator_validation_error(
        request: Request, exc: CalculatorValidationError
    ) -> JSONResponse:
        """계산기 검증 오류를 422 응답으로 변환합니다."""
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    return app


#: 모듈 수준 ASGI 앱 — ``uvicorn procurement.app:app`` 으로 실행할 수 있습니다.
app = create_app()
