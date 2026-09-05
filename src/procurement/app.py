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
    설정값 ``PURCHASE_PERIOD_DATE_FIELD`` 를 비우면 503 으로 거부합니다
    (기본값은 결의일자 — 🟢 2026-09-02 PM 확정).
"""

from __future__ import annotations

import calendar
from datetime import date
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from procurement.admin import (
    NOT_REGISTERED,
    REGISTERED,
    PolicyAdminService,
    PolicyCompanySourceItemModel,
    PolicyCompanySourceListModel,
    PolicyItemResponseModel,
    PolicyListResponseModel,
    PolicyNotFoundError,
    PolicyTargetAdminService,
    PolicyTargetItemModel,
    PolicyTargetListResponseModel,
    PolicyTargetUpdateRequest,
    TargetRateUpdateRequest,
    build_admin_token_guard,
)
from procurement.api import (
    DashboardApiService,
    DashboardResponseModel,
    MissingResolutionDateListResponseModel,
)
from procurement.api.rematch_response import RematchResponseModel
from procurement.api.status_api import DataStatusApiService
from procurement.api.status_response import DataStatusResponseModel
from procurement.api.unmatched_response import UnmatchedPageResponseModel
from procurement.calculators import ProcurementAchievementCalculator
from procurement.calculators.procurement_achievement import CalculatorValidationError
from procurement.collectors.client import SOURCE_POLICY_CODES
from procurement.core.config import settings
from procurement.core.performance_exclusion import ExclusionReasonError
from procurement.core.period import PeriodFilter
from procurement.dashboard import DashboardDataService
from procurement.dashboard.missing_resolution_export import (
    export_lines as missing_resolution_export_lines,
)
from procurement.dashboard.status_service import DataStatusService
from procurement.dashboard.unmatched_service import (
    DEFAULT_PAGE_SIZE as UNMATCHED_PAGE_SIZE,
)
from procurement.dashboard.unmatched_service import (
    DESCENDING as UNMATCHED_DESCENDING,
)
from procurement.dashboard.unmatched_service import (
    MAX_PAGE_SIZE as UNMATCHED_MAX_PAGE_SIZE,
)
from procurement.dashboard.unmatched_service import (
    SORT_AMOUNT as UNMATCHED_SORT_AMOUNT,
)
from procurement.dashboard.unmatched_service import (
    UnmatchedCompanyService,
    UnmatchedQuery,
    UnmatchedQueryError,
)
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.import_batch_repository import (
    ImportBatchRepository,
    ImportBatchValidationError,
)
from procurement.database.import_rejection_repository import ImportRejectionRepository
from procurement.database.policy_company_source_repository import (
    PolicyCompanySourceRepository,
)
from procurement.database.policy_repository import PolicyRepository, PolicyValidationError
from procurement.database.policy_target_repository import PolicyTargetRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.database.review_repository import ReviewRepository, ReviewValidationError
from procurement.importers.batch_import_service import BatchImportService
from procurement.importers.company_importer import (
    COMPANY_SOURCES,
    CompanyImporter,
    CompanyImportReport,
)
from procurement.importers.purchase_importer import PurchaseImporter
from procurement.importers.rejection_export import export_lines as rejection_export_lines
from procurement.importers.rejection_query import (
    ANY as REJECTION_ANY,
)
from procurement.importers.rejection_query import (
    ASCENDING as REJECTION_ASCENDING,
)
from procurement.importers.rejection_query import (
    DEFAULT_PAGE_SIZE as REJECTION_PAGE_SIZE,
)
from procurement.importers.rejection_query import (
    MAX_PAGE_SIZE as REJECTION_MAX_PAGE_SIZE,
)
from procurement.importers.rejection_query import (
    RejectionQuery,
    RejectionQueryError,
)
from procurement.importers.rematch_service import RematchService
from procurement.importers.trace_response import (
    BatchHistoryListResponseModel,
    BatchHistoryResponseModel,
    ImportTraceResponseModel,
    PeriodListResponseModel,
    RejectionPageResponseModel,
    build_history_response,
    build_period_response,
    build_trace_response,
)
from procurement.importers.trace_service import ImportTraceService
from procurement.models.policy_company_source import PolicyCompanySource
from procurement.reviews.export import export_lines, history_lines
from procurement.reviews.query import (
    ANY as QUERY_ANY,
)
from procurement.reviews.query import (
    ASCENDING,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    ReviewQuery,
    ReviewQueryError,
    validate_batch_id,
)
from procurement.reviews.response import (
    ConditionProgressResponseModel,
    ConfirmReviewRequest,
    ExcludePerformanceRequest,
    ExclusionReasonsResponseModel,
    IncludePerformanceRequest,
    PageResponseModel,
    PurchaseTypeOptionResponseModel,
    ReopenReviewRequest,
    ReviewHistoryItemResponseModel,
    ReviewHistoryResponseModel,
    ReviewItemResponseModel,
    ReviewListResponseModel,
    ReviewProgressResponseModel,
    build_exclusion_reasons_response,
    purchase_type_options,
)
from procurement.reviews.review_service import (
    FILTER_ALL,
    ReviewFilterError,
    ReviewNotFoundError,
    ReviewService,
    ReviewStateError,
)
from procurement.reviews.rule_classification import RuleClassifier
from procurement.uploads.company_source_service import (
    CompanyApiClient,
    CompanySourceService,
)
from procurement.uploads.template import TEMPLATE_FILE_NAME, build_template_bytes
from procurement.uploads.upload_response import UploadResponseModel, build_upload_response
from procurement.uploads.upload_service import (
    ExistingPeriodBatchError,
    OverlappingPeriodBatchError,
    UploadPeriodMismatchError,
    UploadService,
)
from procurement.uploads.validation import ValidationReport
from procurement.web import (
    AchievementLevelsResponseModel,
    PolicyDisplayResponseModel,
    build_achievement_levels_response,
    build_policy_display_response,
    parse_thresholds,
    read_index_html,
)


def _rejection_query(
    *,
    search: str,
    reason: str,
    batch_id: int | None,
    sort: str,
    direction: str,
    page: int,
    page_size: int,
) -> RejectionQuery:
    """미적재 조회 조건을 만듭니다.

    ⚠️ **목록과 CSV 가 같은 함수를 씁니다.** 조건 해석을 두 곳에 두면 화면에서
    보던 것과 다른 파일이 내려옵니다(STEP 15 지시 §13).

    Raises:
        HTTPException: 허용되지 않는 조건값이면 422.
    """
    try:
        return RejectionQuery(
            search=search,
            reason=reason,
            batch_id=batch_id,
            sort=sort,
            direction=direction,
            page=page,
            page_size=page_size,
        )
    except RejectionQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _unmatched_query(
    *,
    search: str,
    sort: str,
    direction: str,
    page: int,
    page_size: int,
) -> UnmatchedQuery:
    """미매칭 기업 조회 조건을 만듭니다.

    Raises:
        HTTPException: 허용되지 않는 조건값이면 422.
    """
    try:
        return UnmatchedQuery(
            search=search,
            sort=sort,
            direction=direction,
            page=page,
            page_size=page_size,
        )
    except UnmatchedQueryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def build_unmatched_service(db_path: str | Path | None = None) -> UnmatchedCompanyService:
    """미매칭 기업 조회 서비스를 조립합니다(composition root).

    ⛔ **조회 저장소 하나만** 넣습니다. 기업·인증 저장소를 주입하지 않으므로
    이 서비스는 구조적으로 아무것도 만들거나 바꿀 수 없습니다.

    Args:
        db_path: 사용할 DB 경로. ``None`` 이면 설정값을 사용합니다.

    Returns:
        조립된 :class:`UnmatchedCompanyService`.
    """
    path: str | Path = db_path if db_path is not None else settings.db_file
    return UnmatchedCompanyService(PurchaseRepository(path))


def build_rematch_service(db_path: str | Path | None = None) -> RematchService:
    """재매칭 서비스를 조립합니다(composition root).

    ::

        RematchService → PurchaseImporter.rematch() → CompanyMatcher

    ⚠️ **기존 적재 계층을 그대로 재사용합니다.** 재매칭 전용 연결 로직을
    만들지 않으므로, 업로드 시점의 연결과 나중 재매칭의 결과가 갈라지지
    않습니다.

    ⛔ 기업 저장소는 :class:`PurchaseImporter` 안에서 **조회에만** 쓰이며,
    서비스 자신은 주입받지 않으므로 기업을 만들 수 없습니다.

    Args:
        db_path: 사용할 DB 경로. ``None`` 이면 설정값을 사용합니다.

    Returns:
        조립된 :class:`RematchService`.
    """
    path: str | Path = db_path if db_path is not None else settings.db_file
    purchase_repo = PurchaseRepository(path)
    importer = PurchaseImporter(purchase_repo, CompanyRepository(path))
    return RematchService(importer, purchase_repo)


def build_dashboard_api(db_path: str | Path | None = None) -> DashboardApiService:
    """대시보드 API 서비스를 조립합니다(composition root).

    저장소 → 계산기 → :class:`DashboardDataService` → :class:`DashboardApiService`
    순으로 의존성을 생성·주입합니다. 등록 목표율 기반 요약을 사용하기 위해
    ``policy_repository`` 를 함께 주입합니다.

    ``purchase_repository`` 는 **결의일자가 비어 있어 결의일자 기준 집계에
    포함되지 못한 행이 몇 건인지 알려주기 위해서만** 주입합니다. 달성률
    계산에는 관여하지 않습니다 — 계산은 그대로 ``calculator`` 가 합니다.

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
    data_service = DashboardDataService(
        calculator,
        policy_repository=policy_repo,
        purchase_repository=purchase_repo,
        # 목표비율의 정본은 **연도별** 값이다(DECISIONS §0.20).
        policy_target_repository=PolicyTargetRepository(path),
        # 기업정보를 받지 못한 정책은 **조회불가**다(STEP 96 §8).
        policy_company_source_repository=PolicyCompanySourceRepository(path),
        certification_repository=certification_repo,
    )
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


def build_policy_target_admin(db_path: str | Path | None = None) -> PolicyTargetAdminService:
    """연도별 목표비율 관리 서비스를 조립합니다(composition root).

    설정 경로는 계산 경로와 분리되어 있습니다 — 목표비율을 바꾸는 일이 계산
    코드를 건드리지 않습니다::

        PolicyTargetAdminService → PolicyTargetRepository → SQLite
                                 → PolicyRepository (정책 목록)

    Args:
        db_path: 사용할 SQLite DB 경로. ``None`` 이면 설정값을 사용합니다.

    Returns:
        조립된 :class:`PolicyTargetAdminService`.
    """
    path: str | Path = db_path if db_path is not None else settings.db_file
    return PolicyTargetAdminService(PolicyRepository(path), PolicyTargetRepository(path))


def build_upload_service(db_path: str | Path | None = None) -> UploadService:
    """업로드 서비스를 조립합니다(composition root).

    저장은 **기존 적재 계층을 그대로 재사용**합니다. 업로드 전용 저장 로직을
    만들지 않으므로, 업로드 경로와 기존 경로의 결과가 갈라지지 않습니다::

        UploadService → BatchImportService → PurchaseImporter → Repository

    Args:
        db_path: 사용할 SQLite DB 경로. ``None`` 이면 설정값을 사용합니다.

    Returns:
        조립된 :class:`UploadService`.
    """
    path: str | Path = db_path if db_path is not None else settings.db_file
    purchase_repo = PurchaseRepository(path)
    company_repo = CompanyRepository(path)
    batch_repo = ImportBatchRepository(path)
    importer = PurchaseImporter(purchase_repo, company_repo)
    return UploadService(
        BatchImportService(
            importer,
            batch_repo,
            purchase_repo,
            # 적재되지 않은 원본 행을 사유와 함께 남긴다(STEP 12 · Q5-8).
            ImportRejectionRepository(path),
        )
    )


def build_import_trace_service(db_path: str | Path | None = None) -> ImportTraceService:
    """적재 추적 조회 서비스를 조립합니다(composition root).

    ::

        ImportTraceService → PurchaseRepository        (⛔ 읽기 전용)
                           → ImportBatchRepository     (⛔ 읽기 전용)
                           → ImportRejectionRepository (⛔ 읽기 전용)
                           → ReviewRepository          (⛔ 읽기 전용)

    Args:
        db_path: 사용할 SQLite DB 경로. ``None`` 이면 설정값을 사용합니다.

    Returns:
        조립된 :class:`ImportTraceService`.
    """
    path: str | Path = db_path if db_path is not None else settings.db_file
    return ImportTraceService(
        PurchaseRepository(path),
        ImportBatchRepository(path),
        ImportRejectionRepository(path),
        # 기간별 확정 건수를 세기 위해서만 읽습니다. ⛔ 쓰지 않습니다.
        ReviewRepository(path),
    )


def build_review_service(db_path: str | Path | None = None) -> ReviewService:
    """담당자 검토 서비스를 조립합니다(composition root).

    ::

        ReviewService → PurchaseRepository (⛔ 읽기 전용)
                      → ReviewRepository   (DB-2)

    .. warning::
        🔴 **분석기를 주입하지 않습니다.** 적요 분석 방법(BM25 · RAG · FUSE)이
        아직 선택되지 않았기 때문입니다
        (``docs/DESCRIPTION_SIMILARITY_DESIGN.md`` §3.5). 분석기가 없으면
        후보 없이 검토 화면이 동작하며, 담당자는 원본만 보고 판단합니다.
        방법이 정해지면 여기에 구현체를 넣기만 하면 됩니다.

    Args:
        db_path: 사용할 SQLite DB 경로. ``None`` 이면 설정값을 사용합니다.

    Returns:
        조립된 :class:`ReviewService`.
    """
    path: str | Path = db_path if db_path is not None else settings.db_file
    purchases = PurchaseRepository(path)
    policies = PolicyRepository(path)
    # 정책 필터(STEP 123) — 실적 합산이 쓰는 **그 판정**을 그대로 물어본다.
    # ⛔ 사업자번호를 다시 비교하거나 인증 파일을 다시 읽지 않는다.
    calculator = ProcurementAchievementCalculator(
        purchases, CertificationRepository(path), policies
    )

    def match_policy(policy_code: str, period: PeriodFilter | None) -> set[int]:
        policy = policies.find_by_policy_code(policy_code)
        if policy is None or policy.policy_id is None:
            # 없는 정책 코드로는 좁힐 수 없다. ⛔ 조용히 전체를 주지 않는다.
            return set()
        return calculator.find_matching_purchase_ids(policy.policy_id, period)

    return ReviewService(purchases, ReviewRepository(path), policy_matcher=match_policy)


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
        period_date_field if period_date_field is not None else settings.PURCHASE_PERIOD_DATE_FIELD
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
            ``year`` 를 지정했으나 ``date_field`` 가 비어 있는 경우 **503**.

    .. note::
        🟢 **2026-09-02 PM 확정(STEP 86)** — 연도 귀속 기준일은 **결의일자**이며
        설정 기본값이 그렇게 잡혀 있습니다. 그래서 503 은 이제 "확정되지
        않았다" 가 아니라 **"운영자가 설정을 비웠다"** 는 뜻입니다. 그 상태에서
        임의의 날짜로 계산하지 않는다는 원칙은 그대로입니다.
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
                "연도별 조회를 사용할 수 없습니다. 연도 귀속 기준일 설정"
                "(PURCHASE_PERIOD_DATE_FIELD)이 비어 있습니다. 확정된 기준일은 "
                "결의일자(resolution_date)이며 기본값도 그것입니다 — 설정을 "
                "비운 상태에서는 임의의 날짜로 계산하지 않습니다."
            ),
        )
    return PeriodFilter.for_year(year, date_field)


#: ``.xlsx`` 의 MIME 타입.
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class UploadRequestModel(BaseModel):
    """업로드 검증 요청.

    Attributes:
        file_path: 검증할 ``.xlsx`` 파일의 **로컬 경로**. 데스크톱 앱이 파일
            선택 대화상자에서 얻은 경로를 그대로 넘깁니다.
    """

    file_path: str = Field(
        min_length=1,
        description="검증할 .xlsx 파일의 로컬 경로",
    )


class UploadImportRequestModel(UploadRequestModel):
    """업로드 저장 요청.

    Attributes:
        file_path: 저장할 ``.xlsx`` 파일의 로컬 경로.
        year: 대상 회계연도. **필수입니다.**
        month: 대상 월(1~12). **선택입니다.**

            🟢 2026-09-05 고객 확정: 지출 데이터는 **매월 올리고 연간으로
            누적**한다. 그래서 월을 주면 그 달 하루치 범위가 대상 기간이
            되고, 달마다 배치가 따로 생겨 **다음 달을 올려도 지난달이
            남습니다.** 같은 달을 다시 올리면 그 달만 교체됩니다.

            생략하면 예전처럼 ``1/1 ~ 12/31`` 한 덩어리입니다 — 한 해치를
            한 번에 올리던 방식이 그대로 동작합니다.

            ⛔ 파일 내용에서 기간을 유추하지 않습니다. 어느 날짜로 연도를
            나눌지는 운영자 설정 사항이므로(D-24), 파일에서 추론하면 확정되지
            않은 규칙이 생깁니다. **올린 사람이 어느 달인지 지정합니다.**
    """

    year: int = Field(
        ge=1900,
        le=2999,
        description="대상 회계연도. 월을 주지 않으면 1/1 ~ 12/31 로 환산합니다(D-23).",
    )
    month: int | None = Field(
        default=None,
        ge=1,
        le=12,
        description=(
            "대상 월(1~12). 주면 그 달이 대상 기간이 되어 **달마다 누적**됩니다. "
            "생략하면 한 해 전체가 한 덩어리입니다."
        ),
    )
    replace_existing: bool = Field(
        default=False,
        description=(
            "같은 기간의 기존 데이터를 교체해도 좋다는 **사용자의 명시적 확인**. "
            "기본값 false — 확인 없이는 교체하지 않고 409 로 거부합니다(PM-005)."
        ),
    )


class RuleClassificationResponseModel(BaseModel):
    """확정 규칙 적용 결과(STEP 122).

    Attributes:
        examined: 살펴본 거래 수.
        classified: 규칙으로 **새로 확정한** 거래 수.
        already_decided: 이미 유형이 있어 건너뛴 거래 수.
            ⛔ 담당자가 고른 값을 덮어쓰지 않았습니다.
        pending: 규칙에 없어 **담당자 검토로 남은** 거래 수.
        by_type: 유형별 확정 건수.
    """

    model_config = ConfigDict(frozen=True)

    examined: int
    classified: int
    already_decided: int
    pending: int
    by_type: dict[str, int]


class UploadMonthResponseModel(BaseModel):
    """한 달의 적재 여부.

    Attributes:
        month: 달(1~12).
        uploaded: 그 달의 지출 데이터가 들어와 있으면 ``True``.
    """

    model_config = ConfigDict(frozen=True)

    month: int
    uploaded: bool


class UploadMonthStatusResponseModel(BaseModel):
    """그 해 1~12월의 적재 현황(STEP 119 §13).

    ⭐ **월별 달성률이 아닙니다.** 「올해 몇 월 데이터까지 올렸는가」만 답합니다.

    Attributes:
        year: 대상 연도.
        months: 1월부터 12월까지 **열두 칸 모두**. 빠진 달이 없어야 화면이
            「아직 안 올린 달」을 보여 줄 수 있습니다.
    """

    model_config = ConfigDict(frozen=True)

    year: int
    months: list[UploadMonthResponseModel]


class CompanyUploadRequestModel(BaseModel):
    """기업정보 파일 업로드 요청.

    Attributes:
        file_path: 읽을 ``.xlsx`` 파일의 **로컬 경로**. 구매 업로드와 같은
            방식입니다.
    """

    file_path: str = Field(
        min_length=1,
        description="기업정보 .xlsx 파일의 로컬 경로",
    )
    policy_code: str | None = Field(
        default=None,
        description=(
            "사용자가 **화면에서 고른** 정책 코드. 주면 파일 전체를 이 정책의 "
            "기업 목록으로 저장하며, 파일에 `인증종류` 칸이 없어도 됩니다. "
            "⛔ 파일 안에 다른 인증명이 적혀 있어도 다른 정책에 등록하지 "
            "않습니다(STEP 96 §5). 생략하면 기존 통합 양식(인증종류 포함)으로 "
            "처리합니다."
        ),
    )


class CompanySyncRequestModel(BaseModel):
    """기업정보 조회 요청.

    ⛔ **새 조회 기능이 아닙니다.** 기존 조회를 그대로 호출합니다.

    Attributes:
        source: 조회 출처 식별자(기존 값).
        business_numbers: 조회할 사업자등록번호 목록.
        stdr_date: 조회 기준일자. **필수입니다** — 코드가 오늘 날짜 등을
            임의로 채우지 않습니다.
    """

    source: str = Field(min_length=1, description="조회 출처 식별자")
    business_numbers: list[str] = Field(min_length=1, description="조회할 사업자등록번호 목록")
    stdr_date: date = Field(description="조회 기준일자(필수)")


class CompanySourceOptionModel(BaseModel):
    """조회 방식에서 고를 수 있는 **조회 출처** 하나.

    Attributes:
        source: 조회 출처 식별자(:mod:`procurement.collectors.client` 의 기존 값).
        policy_code: 그 출처가 확인해 주는 정책 코드.
        policy_name: 등록된 정책 이름. 등록되어 있지 않으면 ``None`` 입니다 —
            ⛔ 이름을 임의로 지어내지 않습니다.
    """

    model_config = ConfigDict(frozen=True)

    source: str
    policy_code: str
    policy_name: str | None = None


class CompanySourceListModel(BaseModel):
    """기업정보를 확인하는 **방법** 과 조회 출처 목록.

    Attributes:
        methods: 고를 수 있는 방법 — ``FILE`` · ``API``. 둘 다 같은 자리에
            저장되며 이후 판정은 동일합니다.
        api_sources: 조회 방식을 골랐을 때 사용할 수 있는 출처.
    """

    model_config = ConfigDict(frozen=True)

    methods: list[str]
    api_sources: list[CompanySourceOptionModel]


class CompanyRowResultModel(BaseModel):
    """기업정보 한 건의 처리 결과."""

    model_config = ConfigDict(frozen=True)

    source_row: int
    status: str
    business_no: str | None
    company_id: int | None
    certification_saved: bool
    messages: list[str]


class CompanyImportResponseModel(BaseModel):
    """기업정보 적재 결과.

    Attributes:
        source: 어디서 가져왔는지 — ``FILE`` 또는 ``API``. **표시용**이며
            판정에 쓰이지 않습니다.
        stored: 실제로 저장했는가. 검증 오류로 저장하지 않았으면 ``false``.
        total: 처리한 건수.
        created: 새로 등록한 기업 수.
        already_exists: 이미 있어서 그대로 둔 기업 수.
        failed: 넣지 못한 건수.
        certifications: 새로 저장한 인증 수.
        file_errors: 파일 단위 오류(머리글 누락 등).
        issues: 행 단위 검증 문제.
        rows: 행별 결과.
    """

    model_config = ConfigDict(frozen=True)

    source: str
    stored: bool
    total: int
    created: int = 0
    already_exists: int = 0
    failed: int = 0
    certifications: int = 0
    file_errors: list[str] = []
    issues: list[str] = []
    rows: list[CompanyRowResultModel] = []


def _upload_period(year: int, month: int | None) -> tuple[date, date]:
    """업로드 대상 기간을 정합니다.

    🟢 2026-09-05 고객 확정: 지출 데이터는 **매월 올리고 연간으로 누적**한다.
    달마다 기간이 다르면 배치도 따로 생기므로, 다음 달을 올려도 지난달이
    그대로 남습니다. 같은 달을 다시 올릴 때만 그 달이 교체됩니다.

    Args:
        year: 대상 회계연도.
        month: 대상 월(1~12). ``None`` 이면 한 해 전체입니다.

    Returns:
        ``(시작일, 종료일)``. 월을 주면 그 달의 1일 ~ 말일, 아니면 1/1 ~ 12/31.
    """
    if month is None:
        return date(year, 1, 1), date(year, 12, 31)
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


def _company_import_response(
    source: str,
    report: CompanyImportReport | None,
    validation: ValidationReport | None = None,
) -> CompanyImportResponseModel:
    """기업정보 처리 결과를 응답 모델로 만듭니다."""
    if report is None:
        assert validation is not None
        return CompanyImportResponseModel(
            source=source,
            stored=False,
            total=validation.total_rows,
            file_errors=list(validation.file_errors),
            issues=list(validation.issue_lines()),
        )
    return CompanyImportResponseModel(
        source=report.source,
        stored=True,
        total=report.total_count,
        created=report.created_count,
        already_exists=report.existing_count,
        failed=report.failed_count,
        certifications=report.certification_count,
        issues=list(validation.issue_lines()) if validation is not None else [],
        rows=[
            CompanyRowResultModel(
                source_row=row.source_row,
                status=row.status.value,
                business_no=row.business_no,
                company_id=row.company_id,
                certification_saved=row.certification_saved,
                messages=row.messages,
            )
            for row in report.rows
        ],
    )


def build_company_source_service(
    db_path: str | Path | None = None,
    api_client: CompanyApiClient | None = None,
) -> CompanySourceService:
    """기업정보 확보 서비스를 조립합니다(composition root).

    파일과 조회 **두 방법이 같은 저장 계층**으로 모입니다::

        CompanySourceService → CompanyImporter → Company / Certification

    Args:
        db_path: 사용할 SQLite DB 경로. ``None`` 이면 설정값을 사용합니다.
        api_client: 조회에 사용할 기존 클라이언트. ``None`` 이면 파일 방식만
            사용할 수 있습니다.

    Returns:
        조립된 :class:`CompanySourceService`.
    """
    path: str | Path = db_path if db_path is not None else settings.db_file
    return CompanySourceService(
        CompanyImporter(
            CompanyRepository(path),
            CertificationRepository(path),
            PolicyRepository(path),
        ),
        api_client,
    )


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
    upload_service = build_upload_service(db_path)
    policy_admin = build_policy_admin(db_path)
    policy_target_admin = build_policy_target_admin(db_path)
    review_service = build_review_service(db_path)
    import_trace = build_import_trace_service(db_path)
    date_field = (
        period_date_field if period_date_field is not None else settings.PURCHASE_PERIOD_DATE_FIELD
    )
    data_status_api = build_data_status_api(db_path, data_mode, date_field)
    unmatched_service = build_unmatched_service(db_path)
    rematch_service = build_rematch_service(db_path)
    # 기업정보 확보 — 파일·조회 **두 방법이 같은 저장 계층**으로 모인다.
    company_source_service = build_company_source_service(db_path)
    company_policy_repository = PolicyRepository(
        db_path if db_path is not None else settings.db_file
    )
    company_source_registry = PolicyCompanySourceRepository(
        db_path if db_path is not None else settings.db_file
    )
    # 월별 적재 현황 조회용 — 계산과 **같은 저장소**를 본다(STEP 119 §7).
    purchase_repository = PurchaseRepository(db_path if db_path is not None else settings.db_file)
    # 확정 규칙으로만 구매유형을 자동 확정한다(STEP 122). ⛔ 추측하지 않는다.
    rule_classifier = RuleClassifier(
        purchase_repository,
        ReviewRepository(db_path if db_path is not None else settings.db_file),
    )

    def _record_company_source(
        policy_code: str,
        source: str,
        report: CompanyImportReport,
        *,
        source_label: str | None = None,
    ) -> None:
        """정책의 기업정보를 **받았다는 사실**을 기록합니다(STEP 96 §8).

        ⛔ 인증이 0건이어도 기록합니다 — 목록을 받았는데 우리 거래처가 하나도
        없을 수 있고, 그것은 "판단할 수 없다" 가 아니라 **"전부 미해당"** 입니다.
        """
        policy = company_policy_repository.find_by_policy_code(policy_code)
        if policy is None or policy.policy_id is None:
            return
        company_source_registry.record(
            policy.policy_id,
            source=source,
            company_count=report.created_count + report.existing_count,
            certification_count=report.certification_count,
            source_label=source_label,
        )

    thresholds = parse_thresholds(settings.DASHBOARD_ACHIEVEMENT_DISPLAY_THRESHOLDS)
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
        "/dashboard/missing-resolution-date",
        response_model=MissingResolutionDateListResponseModel,
        summary="결의일자 미기재 구매 조회",
        tags=["dashboard"],
    )
    def get_missing_resolution_date(
        year: int | None = Query(
            default=None,
            ge=1900,
            le=2999,
            description=(
                "지금 화면이 보고 있는 회계연도. **범위 조건으로 쓰이지 "
                "않습니다** — 결의일자 기준 조회인지 판단하는 데만 씁니다. "
                "``/dashboard/summary`` 와 같은 규칙으로 필수입니다(D-27)."
            ),
        ),
    ) -> MissingResolutionDateListResponseModel:
        """결의일자가 입력되지 않은 구매를 **행 단위로** 반환합니다.

        ``/dashboard/summary`` 의 ``missing_resolution_date`` 는 "N건(M 원)"
        이라는 숫자만 알려 줍니다. 그것만으로는 **어떤 행인지** 알 수 없어
        담당자가 무엇을 확인해야 할지 판단할 수 없으므로, 같은 모집단을 행으로
        펼쳐 돌려줍니다.

        ⛔ **조회 기능일 뿐입니다.** 결의일자를 채우지 않고, 지급일·계약일로
        대체하지 않으며, 어떤 행도 수정하지 않습니다.

        ⛔ **판정하지 않습니다.** 이 행들은 "오류"·"무효"·"실적 불인정" 이
        아니라 **결의일자가 입력되지 않은 구매**일 뿐이며, 어떻게 처리할지는
        아직 정해지지 않았습니다.

        ⚠️ **결의일자 범위 조건을 걸지 않습니다.** 이 행들은 결의일자가 없어서
        빠진 것이므로 같은 날짜로 기간을 걸면 정의상 하나도 남지 않습니다.
        대신 계산 대상과 **같은 배치 조건**만 적용합니다. 기간 판정 기준일이
        결의일자가 **아닌** 조회에서는 안내 자체가 해당되지 않으므로 **빈
        목록**을 반환합니다(``/dashboard/summary`` 의 ``applies=false`` 와 같은
        판단입니다).

        Raises:
            HTTPException: ``year`` 미지정 시 400(D-27), 기간 판정 기준일
                미설정 시 503(D-24) — ``/dashboard/summary`` 와 동일합니다.
        """
        return dashboard_api.get_missing_resolution_date(_require_period(year, date_field))

    @app.get(
        "/dashboard/missing-resolution-date.csv",
        summary="결의일자 미기재 구매 CSV 내려받기",
        tags=["dashboard"],
        response_class=StreamingResponse,
    )
    def export_missing_resolution_date(
        year: int | None = Query(
            default=None,
            ge=1900,
            le=2999,
            description=(
                "지금 화면이 보고 있는 회계연도. 목록 조회와 **완전히 같은 "
                "규칙**입니다 — 범위 조건이 아니라 결의일자 기준 조회인지 "
                "판단하는 데만 씁니다."
            ),
        ),
    ) -> StreamingResponse:
        """**화면 목록과 같은 대상**을 CSV 로 내려보냅니다.

        담당자가 내려받아 업무 확인·고객 확인에 쓰기 위한 파일입니다. 대상은
        ``GET /dashboard/missing-resolution-date`` 와 **같은 호출**로 얻으므로,
        화면에서 보던 것과 다른 것이 내려오는 일이 없습니다.

        ⛔ **빈 결의일자를 채우지 않습니다.** 값이 없으면 CSV 에서도 **빈 칸**
        입니다. 신고기준일·지급일·계약일 어느 것으로도 대체하지 않습니다 —
        비어 있다는 사실이 이 파일의 존재 이유이기 때문입니다.

        ⛔ **조회 기능일 뿐입니다.** 어떤 행도 만들거나 바꾸거나 지우지 않고,
        계산기를 부르지 않으며, 달성률에 영향을 주지 않습니다.

        ⛔ **판정하지 않습니다.** 이 행들은 "오류"·"무효"·"실적 불인정" 이
        아니라 **결의일자가 입력되지 않은 구매**일 뿐이며, 어떻게 처리할지는
        아직 정해지지 않았습니다.

        ⚠️ **원본 행 번호 · 공급가액 · 세액은 없습니다.** ``purchase`` 에 없는
        값이므로 지어내지 않습니다(``DECISIONS.md`` §0.7.5).

        Raises:
            HTTPException: ``year`` 미지정 시 400(D-27), 기간 판정 기준일
                미설정 시 503(D-24) — 목록 조회와 동일합니다. ⛔ 빈 CSV 를
                성공으로 돌려주지 않습니다.
        """
        rows = dashboard_api.list_missing_resolution_date_rows(_require_period(year, date_field))
        return StreamingResponse(
            missing_resolution_export_lines(rows),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="missing-resolution-date.csv"'},
        )

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
        "/dashboard/unmatched-companies",
        response_model=UnmatchedPageResponseModel,
        summary="미매칭 기업 조회(사업자번호별 집계·검색·정렬·페이지)",
        tags=["dashboard"],
    )
    def search_unmatched_companies(
        search: str = Query("", description="사업자등록번호 · 거래처명"),
        sort: str = Query(UNMATCHED_SORT_AMOUNT, description="amount | count | business_no"),
        # 금액이 큰 사업자번호부터 보여야 "무엇을 먼저 확보할지" 를 알 수 있다.
        direction: str = Query(UNMATCHED_DESCENDING, description="asc | desc"),
        page: int = Query(1, ge=1, description="1부터 시작하는 페이지 번호"),
        page_size: int = Query(
            UNMATCHED_PAGE_SIZE, ge=1, le=UNMATCHED_MAX_PAGE_SIZE, description="한 페이지 건수"
        ),
    ) -> UnmatchedPageResponseModel:
        """기업정보가 없어 연결되지 않은 구매를 사업자번호별로 묶어 반환합니다.

        대시보드는 "기업 미매칭 N건" 총계만 보여 줍니다. 그 숫자만으로는 **어느
        기업정보를 먼저 확보해야 하는지** 알 수 없어, 같은 사실을 사업자번호
        단위로 접어 금액 비중과 함께 돌려줍니다.

        ⛔ **조회 기능일 뿐입니다.** 기업·인증·구매 어느 것도 만들거나 바꾸지
        않으며, 어느 사업자번호를 확보해야 하는지 **판정하지도 않습니다.**

        ⚠️ 모집단은 대시보드의 ``unmatched_purchase_count`` 와 **같은 기준**
        (``company_id IS NULL`` 전체)이라 대체된 배치의 행도 포함합니다. 그
        사실은 응답의 ``includes_superseded`` · ``notice`` 로 알립니다.

        Raises:
            HTTPException: 조건 값이 허용 범위를 벗어나면 422.
        """
        query = _unmatched_query(
            search=search,
            sort=sort,
            direction=direction,
            page=page,
            page_size=page_size,
        )
        return UnmatchedPageResponseModel.from_page(unmatched_service.search(query))

    @app.post(
        "/purchases/rematch",
        response_model=RematchResponseModel,
        summary="미매칭 구매를 기업정보와 다시 연결",
        tags=["purchases"],
    )
    def rematch_purchases() -> RematchResponseModel:
        """기업정보가 없어 연결되지 않았던 구매를 다시 연결합니다.

        구매데이터가 먼저 들어오고 기업정보가 나중에 확보되는 것이 이 시스템의
        정상 흐름입니다(``PURCHASE_IMPORT_DESIGN.md`` §6.3 "경우 B"). 기업정보를
        등록한 뒤 이 기능을 실행하면 사업자등록번호가 같은 구매가 연결됩니다.

        ⛔ **기업정보를 만들지 않습니다.** 등록된 기업이 있는 건만 연결되고,
        없는 건은 그대로 미매칭으로 남습니다.

        ⛔ **연결 규칙을 바꾸지 않습니다.** 사업자등록번호 완전 일치라는 기존
        규칙을 그대로 씁니다.

        ⛔ **이미 연결된 구매를 건드리지 않습니다.** 미매칭 건만 대상이므로 몇
        번을 실행해도 기존 연결이 바뀌거나 끊기지 않습니다(**멱등**).

        ⚠️ 응답에 "오류 건수" 가 없습니다. 기존 연결 계층이 실패 사유를 구분해
        주지 않으므로 **없는 정보를 지어내지 않고**, 연결되지 않고 남은 건수만
        사실대로 돌려줍니다.
        """
        return RematchResponseModel.from_result(rematch_service.rematch())

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
        "/dashboard/achievement-levels",
        response_model=AchievementLevelsResponseModel,
        summary="달성률 표시 구간표 조회(화면 표시 전용)",
        tags=["dashboard"],
    )
    def get_achievement_levels() -> AchievementLevelsResponseModel:
        """달성률을 화면에 어떻게 구분해 보여줄지 정한 구간표를 반환합니다.

        .. danger::
            **법정 기준이 아닙니다.** 화면 UX 확인용 임시 표시 기준이며, 정책
            판정·계산에 사용되지 않습니다. 기존 응답의 ``status`` 와는 별개
            체계입니다(:mod:`procurement.web.achievement_display`).

        경계값은 설정 ``DASHBOARD_ACHIEVEMENT_DISPLAY_THRESHOLDS`` 로 바꿀 수
        있으며, 화면은 이 응답만 보고 그리므로 코드 수정이 필요 없습니다.
        """
        return build_achievement_levels_response(thresholds)

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
        "/uploads/template",
        summary="표준 업로드 양식(.xlsx) 내려받기",
        tags=["uploads"],
        response_class=Response,
    )
    def download_upload_template() -> Response:
        """표준 업로드 양식 엑셀 파일을 반환합니다.

        컬럼 정의는 :mod:`procurement.uploads.format` 하나만 참조하므로, 양식
        파일과 검증 규칙이 서로 어긋날 수 없습니다.

        ⛔ 업무규칙이 확정되지 않은 컬럼(예산과목·구매유형 등)은 양식에 넣지
        않습니다. 칸이 있으면 사용자가 채우고, 그 값을 시스템이 해석하게 되어
        확정되지 않은 규칙이 생기기 때문입니다.
        """
        quoted = quote(TEMPLATE_FILE_NAME)
        return Response(
            content=build_template_bytes(),
            media_type=XLSX_MEDIA_TYPE,
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quoted}"},
        )

    @app.get(
        "/companies/sources",
        response_model=CompanySourceListModel,
        summary="기업정보 확인 방식과 조회 출처 목록",
        tags=["companies"],
    )
    def list_company_sources() -> CompanySourceListModel:
        """기업정보를 확인하는 **방법**과 조회 출처를 반환합니다.

        ⛔ 화면이 목록을 들고 있지 않습니다. 어떤 출처가 있는지는 코드에 이미
        정의된 값이며, 화면은 받아서 보여 줄 뿐입니다.

        ⛔ 어느 방법을 기본으로 삼을지 여기서 정하지 않습니다 — 사용자가
        고릅니다.
        """
        policy_repository = company_policy_repository
        return CompanySourceListModel(
            methods=list(COMPANY_SOURCES),
            api_sources=[
                CompanySourceOptionModel(
                    source=source,
                    policy_code=policy_code,
                    policy_name=(
                        policy.policy_name
                        if (policy := policy_repository.find_by_policy_code(policy_code))
                        is not None
                        else None
                    ),
                )
                for source, policy_code in SOURCE_POLICY_CODES.items()
            ],
        )

    @app.get(
        "/companies/registration",
        response_model=PolicyCompanySourceListModel,
        summary="정책별 기업정보 등록 현황",
        tags=["companies"],
    )
    def list_company_registration() -> PolicyCompanySourceListModel:
        """정책별로 기업정보를 받았는지, 어떤 방법으로 받을 수 있는지 반환합니다.

        **활성 정책 전체**가 담깁니다. 받지 못한 정책도 빼지 않고
        ``registered: false`` · ``status: "NOT_REGISTERED"`` 로 내려갑니다 —
        ⛔ 그것은 **조회불가**이지 "해당 기업이 없다" 가 아닙니다(STEP 96 §8).

        ``available_methods`` 는 그 정책에서 실제로 **고를 수 있는** 방법만
        담습니다. 조회가 구현되지 않은 정책(예: 중소기업)에는 ``API`` 가 들어
        있지 않습니다 — ⛔ 없는 선택지를 화면에 만들지 않기 위해서입니다
        (STEP 96 §3).
        """
        registered = {record.policy_id: record for record in company_source_registry.find_all()}
        # 조회가 가능한 정책 = 기존 상수가 정한 것뿐. ⛔ 임의로 넓히지 않는다.
        api_capable = set(SOURCE_POLICY_CODES.values())

        items: list[PolicyCompanySourceItemModel] = []
        for policy in company_policy_repository.find_active():
            if policy.policy_id is None:  # pragma: no cover - 저장된 정책은 ID 가 있다
                continue
            record = registered.get(policy.policy_id)
            methods = ["FILE"]
            if policy.policy_code in api_capable:
                methods.append("API")
            items.append(
                PolicyCompanySourceItemModel(
                    policy_id=policy.policy_id,
                    policy_code=policy.policy_code,
                    policy_name=policy.policy_name,
                    registered=record is not None,
                    status=REGISTERED if record is not None else NOT_REGISTERED,
                    status_label="등록완료" if record is not None else "미등록",
                    source=record.source if record is not None else None,
                    source_label=record.source_label if record is not None else None,
                    company_count=record.company_count if record is not None else None,
                    certification_count=(
                        record.certification_count if record is not None else None
                    ),
                    updated_at=record.updated_at if record is not None else None,
                    available_methods=methods,
                )
            )
        return PolicyCompanySourceListModel(items=items)

    @app.post(
        "/companies/upload/validate",
        response_model=CompanyImportResponseModel,
        summary="기업정보 파일 검증 (저장하지 않음)",
        tags=["companies"],
    )
    def validate_company_upload(
        payload: CompanyUploadRequestModel,
    ) -> CompanyImportResponseModel:
        """기업정보 파일을 읽어 **모든 행을 검증**하고 결과를 반환합니다.

        ⛔ **저장하지 않습니다.** 구매 업로드와 같은 방식으로, 무엇을 고쳐야
        하는지 먼저 보여 줍니다.
        """
        return _company_import_response(
            "FILE",
            None,
            company_source_service.validate_file(
                payload.file_path, policy_code=payload.policy_code
            ),
        )

    @app.post(
        "/companies/upload",
        response_model=CompanyImportResponseModel,
        summary="기업정보 파일 검증 후 저장",
        tags=["companies"],
    )
    def import_company_upload(
        payload: CompanyUploadRequestModel,
    ) -> CompanyImportResponseModel:
        """기업정보 파일을 검증하고, **오류가 하나도 없을 때만** 저장합니다.

        구매 업로드와 같은 **"전부 검증 → 전부 저장"** 원칙입니다.

        ``policy_code`` 를 주면 **그 정책의 목록**으로 저장하고, 그 정책의
        기업정보를 받았다는 사실을 기록합니다 — 그때부터 그 정책은 조회불가가
        아니게 됩니다(STEP 96 §5 · §8).

        저장한 뒤 구매와 연결하려면 기존 ``POST /purchases/rematch`` 를
        호출합니다 — ⛔ 새 매칭 기능을 만들지 않았습니다.
        """
        # ⭐ 등록 버전은 **검증을 통과한 뒤에** 만들어진다. 그래야 저장할 수
        #    없는 파일이 최신 자료로 바뀌지 않는다. 버전 ID 를 먼저 받아야
        #    저장하는 인증마다 «어느 파일에서 왔는지» 를 달 수 있다.
        recorded: PolicyCompanySource | None = None

        def begin_version(checksum: str) -> int | None:
            nonlocal recorded
            if payload.policy_code is None:
                return None
            policy = company_policy_repository.find_by_policy_code(payload.policy_code)
            if policy is None or policy.policy_id is None:
                return None
            recorded = company_source_registry.record(
                policy.policy_id,
                source="FILE",
                company_count=0,
                certification_count=0,
                source_label=Path(payload.file_path).name,
                file_checksum=checksum,
            )
            return recorded.policy_company_source_id

        validation, report = company_source_service.import_file(
            payload.file_path,
            policy_code=payload.policy_code,
            begin_version=begin_version,
        )
        # 건수는 적재가 끝나야 알 수 있어 여기서 채운다.
        if report is not None and recorded is not None:
            assert recorded.policy_company_source_id is not None
            company_source_registry.update_counts(
                recorded.policy_company_source_id,
                company_count=report.created_count + report.existing_count,
                certification_count=report.certification_count,
            )
        return _company_import_response("FILE", report, validation)

    @app.post(
        "/companies/sync",
        response_model=CompanyImportResponseModel,
        summary="기업정보 조회 후 저장",
        tags=["companies"],
    )
    def sync_companies(payload: CompanySyncRequestModel) -> CompanyImportResponseModel:
        """**기존 조회 기능**으로 기업정보를 확보해 저장합니다.

        ⛔ 새 외부 조회를 만들지 않았습니다. 파일 방식과 **같은 저장 계층**을
        쓰므로, 이후 매칭·인증 판정·계산은 두 방법이 완전히 같습니다.

        ⚠️ 조회 결과가 기업명·대표자명을 주지 않는 경우가 있습니다. 그때는 그
        건을 **넣지 않고** 사유를 그대로 돌려줍니다 — ⛔ 없는 값을 임의로
        채우지 않습니다.
        """
        # 조회 출처가 어느 정책인지는 **기존 상수**가 정한다. ⛔ 추론하지 않는다.
        api_policy_code = SOURCE_POLICY_CODES.get(payload.source)
        report = company_source_service.import_from_api(
            payload.source,
            payload.business_numbers,
            stdr_date=payload.stdr_date,
        )
        if api_policy_code is not None:
            _record_company_source(api_policy_code, "API", report, source_label=payload.source)
        return _company_import_response("API", report)

    @app.post(
        "/uploads/purchases/validate",
        response_model=UploadResponseModel,
        summary="표준 업로드 파일 검증 (저장하지 않음)",
        tags=["uploads"],
    )
    def validate_purchase_upload(payload: UploadRequestModel) -> UploadResponseModel:
        """올린 엑셀 파일을 읽어 **모든 행을 검증**하고 결과를 반환합니다.

        ⛔ **저장하지 않습니다.** 표준 양식에는 지급일 항목이 없는데 적재
        계층은 ``payment_date`` 를 필수로 요구합니다. 이 칸을 무엇으로 채울지는
        업무규칙 결정 사항이므로, 임시로 다른 날짜를 넣어 저장하지 않습니다.

        검증은 "전부 검증 → 전부 저장" 원칙에 따라 **전체 행을 먼저** 수행하며,
        일부 행만 먼저 저장하는 경로를 만들지 않았습니다.

        파일은 **경로로 전달**받습니다. 백엔드는 데스크톱 앱이 띄운
        ``127.0.0.1`` 전용 프로세스이고 파일은 같은 PC 에 있으므로, 파일 본문을
        네트워크로 다시 실어 보내지 않습니다.
        """
        return build_upload_response(upload_service.validate_file(payload.file_path))

    @app.post(
        "/uploads/purchases",
        response_model=UploadResponseModel,
        summary="표준 업로드 파일 검증 후 저장",
        tags=["uploads"],
        responses={
            409: {
                "description": (
                    "같은 기간에 이미 등록된 데이터가 있고 교체 확인이 없는 경우. "
                    "**DB 는 변경되지 않습니다.** 사용자에게 교체 여부를 물은 뒤 "
                    "`replace_existing: true` 로 다시 요청하세요."
                ),
                "content": {
                    "application/json": {
                        "example": {
                            "detail": {
                                "code": "EXISTING_PERIOD",
                                "message": "2026년 데이터가 이미 등록되어 있습니다.",
                                "existing_batch_id": 1,
                                "existing_file_name": "2026년_구매실적.xlsx",
                                "existing_row_count": 1744,
                                "year": 2026,
                            }
                        }
                    }
                },
            }
        },
    )
    def import_purchase_upload(payload: UploadImportRequestModel) -> UploadResponseModel:
        """올린 엑셀 파일을 검증하고, **오류가 하나도 없을 때만** 저장합니다.

        **"전부 검증 → 전부 저장"** 입니다. 한 행이라도 오류가 있으면 적재
        계층을 호출조차 하지 않으므로 DB 에 아무 변화가 없으며, 정상 행도
        저장되지 않습니다. 이때도 200 으로 응답하고 ``stored: false`` 와 오류
        목록을 돌려줍니다 — 사용자가 무엇을 고쳐야 하는지 한 번에 보게 하기
        위함입니다.

        대상 기간은 ``year`` (와 주어졌다면 ``month``)로 정합니다. 월을 주면
        **그 달**이 기간이 되어 달마다 배치가 따로 생기고, 다음 달을 올려도
        지난달 데이터가 남습니다 — 화면의 실적은 그 해의 **누적**입니다
        (🟢 2026-09-05 고객 확정). 월을 생략하면 예전처럼 ``1/1 ~ 12/31``
        한 덩어리입니다(D-23).

        같은 기간의 이전 배치는 기존 규칙대로 대체됩니다(D-25) — 월을 주면
        **그 달만** 교체되고 다른 달은 그대로입니다.

        저장은 기존 :class:`BatchImportService` 가 수행합니다. 업로드 전용
        저장 로직을 만들지 않았습니다.

        ⛔ **같은 기간에 이미 데이터가 있으면 묻지 않고 교체하지 않습니다**
        (PM-005). ``replace_existing`` 없이 요청하면 **409** 로 거부하며, 이때
        **DB 는 전혀 변경되지 않습니다.** 화면은 이 응답을 받아 교체 여부를
        묻고, 사용자가 승인하면 ``replace_existing: true`` 로 다시 요청합니다.

        교체는 **논리 교체**입니다(PM-012). 이전 배치는 물리 삭제하지 않고
        ``SUPERSEDED`` 로 표시되어 계산에서만 빠지므로, 어떤 파일이
        사용되었는지 이력이 남습니다.
        """
        # 배치의 대상 기간은 **단순 날짜 범위**다. 어느 날짜 컬럼으로 연도를
        # 나눌지(D-24)와는 별개이므로 PeriodFilter 를 쓰지 않는다 — 여기서
        # date_field 를 고르면 확정되지 않은 의미가 붙는다.
        period_start, period_end = _upload_period(payload.year, payload.month)
        label = f"{payload.year}년"
        if payload.month is not None:
            label += f" {payload.month}월"
        try:
            result = upload_service.import_file(
                payload.file_path,
                period_start=period_start,
                period_end=period_end,
                replace_existing=payload.replace_existing,
            )
        except ExistingPeriodBatchError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "EXISTING_PERIOD",
                    "message": f"{label} 데이터가 이미 등록되어 있습니다.",
                    "existing_batch_id": exc.existing.batch_id,
                    "existing_file_name": exc.existing.file_name,
                    "existing_row_count": exc.existing.row_count,
                    "year": payload.year,
                    "month": payload.month,
                },
            ) from exc
        except UploadPeriodMismatchError as exc:
            # 🟢 고객 확정(STEP 121) — 고른 기간과 결의일자가 다른 행이 하나라도
            #    있으면 **파일 전체를 거절**한다. ⛔ 일부만 적재하지 않는다.
            #    교체 확인보다 먼저 걸리므로 기존 그 달 데이터는 그대로 남는다.
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "UPLOAD_PERIOD_MISMATCH",
                    "message": (
                        f"고르신 업로드 기간은 {label}입니다. 파일에 {label}이 아닌 "
                        f"결의일자가 {exc.mismatch_count}건 있어 **파일 전체**를 "
                        "등록하지 않았습니다."
                    ),
                    "year": payload.year,
                    "month": payload.month,
                    "mismatch_count": exc.mismatch_count,
                    "total_rows": exc.total_rows,
                    # ⛔ 거래 원본을 늘어놓지 않는다 — 어느 연월이 몇 건인지만 준다.
                    "found_periods": [
                        {"period": period, "count": count}
                        for period, count in sorted(exc.mismatched.items())
                    ],
                },
            ) from exc
        except OverlappingPeriodBatchError as exc:
            # ⛔ 교체 확인으로 넘어갈 수 없는 자리다. 겹친 배치를 대체하면 그
            #    안의 다른 달까지 사라지고(§9 금지), 두면 그 달이 이중 집계된다
            #    (§8 금지). 어느 쪽도 안전하지 않아 **적재하지 않는다.**
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "OVERLAPPING_PERIOD",
                    "message": (
                        f"{label} 자료와 **기간이 겹치는** 데이터가 이미 등록되어 "
                        "있습니다. 겹치는 기간이 서로 달라 그 달만 바꿀 수 없습니다."
                    ),
                    "existing": [
                        {
                            "batch_id": row.batch_id,
                            "file_name": row.file_name,
                            "row_count": row.row_count,
                            "period_start": str(row.period_start),
                            "period_end": str(row.period_end),
                        }
                        for row in exc.existing
                    ],
                    "year": payload.year,
                    "month": payload.month,
                    "hint": (
                        "한 해치를 통짜로 올려 두셨다면, 같은 범위(연도 전체)로 "
                        "다시 올리시거나 달 단위로 나누어 올려 주십시오."
                    ),
                },
            ) from exc
        except ImportBatchValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        if result.stored and date_field is not None:
            # 🟢 적재 직후 **고객이 확정한 규칙**만 적용한다(STEP 122 §5).
            # ⛔ 추측하지 않는다 — 예산과목 완전 일치 2건뿐이며, 나머지는
            #    담당자 검토 대상으로 남는다.
            # ⛔ 기준일 설정이 비어 있으면 돌리지 않는다 — 어느 기간을 볼지
            #    임의로 정하지 않는다(D-24).
            rule_classifier.apply(
                PeriodFilter(start=period_start, end=period_end, date_field=date_field)
            )
        return build_upload_response(result)

    @app.post(
        "/reviews/apply-rules",
        response_model=RuleClassificationResponseModel,
        summary="확정 규칙으로 구매유형 자동 확정",
        tags=["reviews"],
    )
    def apply_purchase_type_rules(year: int | None = None) -> RuleClassificationResponseModel:
        """**고객이 확정한 규칙**에 해당하는 거래의 구매유형을 확정합니다.

        업로드할 때 자동으로 한 번 돌지만, **이미 올라와 있는 데이터**에도
        적용할 수 있도록 열어 둔 입구입니다.

        .. warning::
            ⛔ **추측하지 않습니다.** 예산과목이 ``도서인쇄비`` ·
            ``소모성물품구입비`` 와 **정확히 같은** 거래만 물품으로 확정합니다.
            적요·거래처명·금액을 보지 않으며, 그 밖의 거래는 손대지 않고
            **담당자 검토 대상**으로 남깁니다.

        .. warning::
            ⛔ **담당자가 고른 값을 덮어쓰지 않습니다.** 이미 유형이 정해진
            거래는 건너뜁니다.

        자동 확정도 이력에 남고, 담당자가 검토 화면에서 **다시 바꿀 수
        있습니다**(``PUT /reviews/{purchase_id}``).

        Args:
            year: 좁힐 연도. 생략하면 전체입니다.
        """
        period = _require_period(year, date_field) if year is not None else None
        outcome = rule_classifier.apply(period)
        return RuleClassificationResponseModel(
            examined=outcome.examined,
            classified=outcome.classified,
            already_decided=outcome.already_decided,
            pending=outcome.pending,
            by_type=dict(outcome.by_type or {}),
        )

    @app.get(
        "/uploads/purchases/months",
        response_model=UploadMonthStatusResponseModel,
        summary="월별 지출 데이터 적재 현황",
        tags=["uploads"],
    )
    def purchase_upload_months(year: int) -> UploadMonthStatusResponseModel:
        """그 해 1~12월 중 **어느 달까지 올라와 있는지** 돌려줍니다(STEP 119 §5).

        ⭐ **월별 달성률이 아닙니다.** 「올해 몇 월 데이터까지 올렸는가」 하나만
        답합니다.

        판정 기준은 대시보드의 누적 실적과 **같은 데이터**입니다 — 활성 배치의
        행을 **결의일자**로 가릅니다. 그래서 「업로드 완료」로 보이는 달은 반드시
        누적 실적에도 들어가 있습니다(§7).

        .. warning::
            ⛔ **새 업무규칙을 만들지 않았습니다.** 그 달의 계산 대상 행이
            하나라도 있으면 완료이고, 없으면 미업로드입니다. 「몇 건 이상」 같은
            기준도, 신고기준일·계약일자로 달을 가르는 일도 없습니다.

        Args:
            year: 대상 연도. ⛔ 다른 연도와 섞이지 않습니다.
        """
        uploaded = purchase_repository.months_with_purchases(year)
        return UploadMonthStatusResponseModel(
            year=year,
            months=[
                UploadMonthResponseModel(month=month, uploaded=month in uploaded)
                for month in range(1, 13)
            ],
        )

    # ------------------------------------------------------------------
    # 담당자 검토 (DB-2) — 구매유형 확정
    #
    # ⛔ 원본(DB-1)을 수정하지 않습니다. ⛔ 자동 확정하지 않습니다.
    # 🔴 적요 분석 방법은 아직 선택되지 않았습니다(결정 대기).
    # ------------------------------------------------------------------
    @app.get(
        "/reviews",
        response_model=ReviewListResponseModel,
        summary="구매유형 검토 목록",
        tags=["reviews"],
    )
    def list_reviews(
        review_filter: str = FILTER_ALL,
        limit: int | None = None,
        offset: int = 0,
        search: str = "",
        status: str = QUERY_ANY,
        decision: str = QUERY_ANY,
        history: str = QUERY_ANY,
        candidates: str = QUERY_ANY,
        policy: str | None = Query(
            None,
            description=(
                "이 정책의 인증기업과 한 거래만 (예: WOMAN). 생략하면 전체. "
                "실적 합산이 쓰는 매칭 판정을 그대로 씁니다 — 다시 매칭하지 않습니다."
            ),
        ),
        batch_id: int | None = Query(
            None, description="이 업로드 배치로 들어온 행만 (기간 필터). 생략하면 전체"
        ),
        ambiguous_only: bool = False,
        sort: str = "purchase_id",
        direction: str = ASCENDING,
        page: int | None = None,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> ReviewListResponseModel:
        """검토 대상 목록과 진행 상황을 반환합니다.

        각 항목은 ``source``(원본) · ``analysis``(자동 분석) ·
        ``review``(담당자 확정) · ``past_labels``(과거 이력)로 **분리**되어
        있습니다.

        검색 · 필터 · 정렬 · 페이지는 **서버에서** 처리하고 **해당 페이지만**
        내려보냅니다. 전체를 브라우저로 보내 거르는 구조는 건수가 늘수록
        첫 화면이 느려지기 때문입니다.

        ``page`` 를 주지 않으면 기존 방식(``limit`` · ``offset``)으로 동작해
        이전 호출부가 그대로 동작합니다.

        Raises:
            HTTPException: 허용되지 않는 조건값이면 422.
        """
        if page is None:
            # ⚠️ 이 경로도 **같은 기간 조건**을 적용한다. 예전에는 여기서만
            #    ``batch_id`` 를 보지 않아, 조건을 줘도 전체가 내려왔다 — 담당자는
            #    거르지 않은 목록을 걸러진 것으로 읽게 된다(STEP 20 발견).
            #    ⛔ 값이 잘못됐다고 전체 조회로 되돌리지 않는다. 거부한다.
            try:
                validate_batch_id(batch_id)
                targets = review_service.list_targets(
                    review_filter=review_filter,
                    batch_id=batch_id,
                    policy_code=policy,
                    limit=limit,
                    offset=offset,
                )
            except (ReviewFilterError, ReviewQueryError) as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return ReviewListResponseModel(
                items=[ReviewItemResponseModel.from_target(target) for target in targets],
                progress=ReviewProgressResponseModel.from_progress(review_service.progress()),
            )

        try:
            query = ReviewQuery(
                search=search,
                status=status,
                decision=decision,
                history=history,
                candidates=candidates,
                policy_code=policy,
                batch_id=batch_id,
                ambiguous_only=ambiguous_only,
                sort=sort,
                direction=direction,
                page=page,
                page_size=page_size,
            )
        except ReviewQueryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        result = review_service.search(query)
        return ReviewListResponseModel(
            items=[ReviewItemResponseModel.from_target(target) for target in result.items],
            # 전체 진행률과 현재 조건 진행률은 **다른 값**이며, 둘 다 보여준다.
            progress=ReviewProgressResponseModel.from_progress(review_service.progress()),
            page=PageResponseModel.from_page(result.page),
            condition=ConditionProgressResponseModel.from_progress(result.condition),
        )

    @app.get(
        "/reviews/export.csv",
        summary="확정 이력 CSV 내려받기",
        tags=["reviews"],
        response_class=StreamingResponse,
    )
    def export_reviews(
        search: str = "",
        status: str = QUERY_ANY,
        decision: str = QUERY_ANY,
        history: str = QUERY_ANY,
        candidates: str = QUERY_ANY,
        policy: str | None = Query(
            None,
            description=(
                "이 정책의 인증기업과 한 거래만 (예: WOMAN). 생략하면 전체. "
                "실적 합산이 쓰는 매칭 판정을 그대로 씁니다 — 다시 매칭하지 않습니다."
            ),
        ),
        batch_id: int | None = Query(
            None, description="이 업로드 배치로 들어온 행만 (기간 필터). 생략하면 전체"
        ),
        ambiguous_only: bool = False,
        sort: str = "purchase_id",
        direction: str = ASCENDING,
    ) -> StreamingResponse:
        """화면과 **같은 조건**의 검토 내역을 CSV 로 내려줍니다.

        담당자가 엑셀에서 직접 검증하기 위한 것입니다. ⛔ 자동 확정이나 분석기
        평가와는 무관하며, ``최종 유형`` 열에는 **담당자 확정값만** 들어갑니다.

        페이지 조건은 받지 않습니다 — 내보내기는 **조건에 맞는 전부**입니다.

        Raises:
            HTTPException: 허용되지 않는 조건값이면 422.
        """
        try:
            query = ReviewQuery(
                search=search,
                status=status,
                decision=decision,
                history=history,
                candidates=candidates,
                policy_code=policy,
                batch_id=batch_id,
                ambiguous_only=ambiguous_only,
                sort=sort,
                direction=direction,
                page=1,
                page_size=MAX_PAGE_SIZE,
            )
        except ReviewQueryError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        targets = review_service.search_all(query)
        return StreamingResponse(
            export_lines(targets),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="reviews.csv"'},
        )

    @app.get(
        "/reviews/history.csv",
        summary="검토 변경 이력 CSV 내려받기",
        tags=["reviews"],
        response_class=StreamingResponse,
    )
    def export_review_history(
        batch_id: int | None = Query(
            None, description="이 업로드 배치로 들어온 행만 (기간 필터). 생략하면 전체"
        ),
    ) -> StreamingResponse:
        """검토 **변경 이력**을 CSV 로 내려줍니다.

        ``/reviews/export.csv`` 와 다른 표입니다 — 저쪽은 구매 한 건의 **현재
        상태**가 한 줄이고, 여기서는 그 구매에 있었던 **변경 한 번**이 한 줄입니다.
        확정 → 취소 → 재확정이면 세 줄이 그대로 나옵니다.

        기간은 화면·목록·CSV 와 **같은 뜻**입니다 — 현재 배치(``batch_id``)로만
        좁히며, 대체된 배치의 이력은 나오지 않습니다. ⛔ 날짜로 기간을 다시
        계산하지 않습니다(어느 날짜로 나눌지는 아직 확정되지 않은 업무규칙).

        ⛔ 이력을 고르거나 줄이지 않습니다 — 최신만 남기거나 취소 기록을 빼지
        않습니다. 기록이 곧 근거이기 때문입니다.

        Raises:
            HTTPException: ``batch_id`` 가 1 보다 작으면 422.
        """
        if batch_id is not None and batch_id < 1:
            raise HTTPException(status_code=422, detail="batch_id 는 1 이상이어야 합니다")

        pairs = review_service.history_of_batch(batch_id)
        return StreamingResponse(
            history_lines(pairs),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="review-history.csv"'},
        )

    @app.get(
        "/imports/trace",
        response_model=ImportTraceResponseModel,
        summary="원본 → 적재 → 미적재 대조",
        tags=["imports"],
    )
    def get_import_trace() -> ImportTraceResponseModel:
        """원본 파일의 행이 지금 어디에 있는지 보여줍니다.

        담당자가 "전체를 검토했다" 고 판단하기 전에 확인해야 할 숫자입니다.
        원본에는 있었지만 적재되지 않아 **검토 화면에 보이지 않는 행**이 있으면
        여기서 건수와 사유가 드러납니다.

        ⛔ **업무 판단을 하지 않습니다.** 미적재 행을 실적에서 뺄지, 검토
        대상에 넣을지는 고객 확인 사항입니다(``Q5-8``).
        """
        return build_trace_response(import_trace.overview(), import_trace.rejections())

    @app.get(
        "/imports/periods",
        response_model=PeriodListResponseModel,
        summary="조회에 쓸 수 있는 기간 목록",
        tags=["imports"],
    )
    def list_import_periods() -> PeriodListResponseModel:
        """검토·미적재 조회에 쓸 수 있는 기간을 최근 순으로 반환합니다.

        ⛔ **화면이 기간을 만들지 않습니다.** 업로드된 배치의 기간을 그대로
        내려보내고, 화면은 담당자가 고른 기간의 ``batch_id`` 를 조회 조건에
        넣기만 합니다.

        ⛔ **현재 배치가 있는 기간만** 나옵니다. 같은 기간을 다시 올려 이전
        배치가 대체되었다면 기간은 하나이고 ``batch_id`` 는 새 배치입니다.
        대체된 배치는 업로드 이력(``GET /imports/batches``)에서 봅니다.
        """
        return build_period_response(import_trace.periods())

    @app.get(
        "/imports/batches",
        response_model=BatchHistoryListResponseModel,
        summary="업로드 이력",
        tags=["imports"],
    )
    def list_import_batches(
        # ``Annotated`` 로 적는다 — 기본값 자리에서 ``Query(...)`` 를 부르면
        # 날짜 타입에서는 ruff(B008)가 걸린다.
        period_start: Annotated[date | None, Query(description="이 기간의 업로드만")] = None,
        period_end: Annotated[
            date | None, Query(description="``period_start`` 와 함께 지정")
        ] = None,
    ) -> BatchHistoryListResponseModel:
        """업로드 이력을 최근 순으로 반환합니다.

        매월 원본 파일을 올리는 운영을 전제로, **어느 달 파일이 언제 올라왔고
        지금 무엇이 쓰이고 있는지**를 한눈에 보기 위한 목록입니다. 대체된
        배치도 함께 나옵니다 — 무엇이 무엇으로 바뀌었는지 알아야 하기 때문
        입니다.

        ⛔ 새 상태값을 만들지 않았습니다. ``status`` 는 기존 배치 lifecycle 의
        ``ACTIVE`` / ``SUPERSEDED`` 그대로입니다.

        기간을 주면 그 기간의 업로드만 봅니다. 두 값은 **함께** 주어야 하며,
        화면은 ``GET /imports/periods`` 가 준 값을 그대로 돌려보냅니다.

        Raises:
            HTTPException: 기간을 한쪽만 주면 422.
        """
        if (period_start is None) != (period_end is None):
            raise HTTPException(status_code=422, detail="기간은 시작과 끝을 함께 지정해야 합니다.")
        return build_history_response(
            import_trace.history(period_start=period_start, period_end=period_end)
        )

    @app.get(
        "/imports/batches/{batch_id}",
        response_model=BatchHistoryResponseModel,
        summary="업로드 한 건 상세",
        tags=["imports"],
    )
    def get_import_batch(batch_id: int) -> BatchHistoryResponseModel:
        """업로드 한 건의 원본·적재·미적재와 사유별 건수를 반환합니다.

        Raises:
            HTTPException: 해당 배치가 없으면 404.
        """
        entry = import_trace.batch(batch_id)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"업로드를 찾을 수 없습니다: {batch_id}")
        return BatchHistoryResponseModel.from_entry(entry)

    @app.get(
        "/imports/rejections",
        response_model=RejectionPageResponseModel,
        summary="미적재 원본 행 조회(검색·필터·정렬·페이지)",
        tags=["imports"],
    )
    def search_import_rejections(  # noqa: PLR0913 — 조회 조건이 그만큼 있다
        search: str = Query("", description="적요·거래처명·사업자번호·원본 행 번호"),
        reason: str = Query(REJECTION_ANY, description="미적재 사유 코드 또는 ALL"),
        batch_id: int | None = Query(
            None, description="이 업로드 배치의 기록만 (기간 필터). 생략하면 전체"
        ),
        sort: str = Query("row_number", description="정렬 기준"),
        direction: str = Query(REJECTION_ASCENDING, description="asc | desc"),
        page: int = Query(1, ge=1, description="1부터 시작하는 페이지 번호"),
        page_size: int = Query(
            REJECTION_PAGE_SIZE, ge=1, le=REJECTION_MAX_PAGE_SIZE, description="한 페이지 건수"
        ),
    ) -> RejectionPageResponseModel:
        """미적재 원본 행을 조건에 맞춰 한 페이지씩 반환합니다.

        ⛔ **조회 기능일 뿐입니다.** 걸러 본다고 해서 그 행이 실적에 포함되거나
        빠지지 않습니다. 처리 방식은 고객 확인 사항입니다(``Q5-8``).

        Raises:
            HTTPException: 조건 값이 허용 범위를 벗어나면 422.
        """
        query = _rejection_query(
            search=search,
            reason=reason,
            batch_id=batch_id,
            sort=sort,
            direction=direction,
            page=page,
            page_size=page_size,
        )
        return RejectionPageResponseModel.from_page(import_trace.search_rejections(query))

    @app.get(
        "/imports/trace.csv",
        summary="미적재 원본 행 CSV 내려받기",
        tags=["imports"],
        response_class=StreamingResponse,
    )
    def export_import_trace(
        search: str = Query("", description="적요·거래처명·사업자번호·원본 행 번호"),
        reason: str = Query(REJECTION_ANY, description="미적재 사유 코드 또는 ALL"),
        batch_id: int | None = Query(None, description="이 업로드 배치의 기록만 (기간 필터)"),
        sort: str = Query("row_number", description="정렬 기준"),
        direction: str = Query(REJECTION_ASCENDING, description="asc | desc"),
    ) -> StreamingResponse:
        """**화면과 같은 조건**의 미적재 원본 행을 CSV 로 내려보냅니다.

        담당자가 **원본 엑셀과 나란히 놓고 대조**하기 위한 파일입니다. 원본 행
        번호와 원본 값(음수 금액 포함)을 그대로 싣습니다.

        조건은 목록(``GET /imports/rejections``)과 **같은 규칙**으로 해석합니다
        — 화면에서 보던 것과 다른 것이 내려오면 안 되기 때문입니다.

        페이지 조건은 받지 않습니다 — 내보내기는 **조건에 맞는 전부**입니다
        (검토 이력 CSV 와 같은 계약).

        ⛔ **"제외 목록" 이 아닙니다.** 처리 방식은 고객 확인 사항입니다(Q5-8).

        Raises:
            HTTPException: 허용되지 않는 조건값이면 422.
        """
        query = _rejection_query(
            search=search,
            reason=reason,
            batch_id=batch_id,
            sort=sort,
            direction=direction,
            page=1,
            page_size=REJECTION_MAX_PAGE_SIZE,
        )
        return StreamingResponse(
            rejection_export_lines(import_trace.search_rejections_all(query)),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="import-rejections.csv"'},
        )

    @app.get(
        "/reviews/options",
        response_model=list[PurchaseTypeOptionResponseModel],
        summary="담당자가 고를 수 있는 구매유형 선택지",
        tags=["reviews"],
    )
    def list_review_options() -> list[PurchaseTypeOptionResponseModel]:
        """공사 · 용역 · 물품 · 판단 보류를 반환합니다.

        ⛔ 화면이 선택지를 직접 만들지 않도록 **백엔드가 목록을 소유**합니다.
        """
        return purchase_type_options()

    @app.get(
        "/reviews/progress",
        response_model=ReviewProgressResponseModel,
        summary="검토 진행 상황",
        tags=["reviews"],
    )
    def get_review_progress() -> ReviewProgressResponseModel:
        """확정 / 미확정 / 확인 권장 건수를 반환합니다."""
        return ReviewProgressResponseModel.from_progress(review_service.progress())

    # ⚠️ 정적 경로는 ``/reviews/{purchase_id}`` **앞에** 둔다. 뒤에 두면
    #    "exclusion-reasons" 가 purchase_id 로 해석되어 422 가 된다.
    @app.get(
        "/reviews/exclusion-reasons",
        response_model=ExclusionReasonsResponseModel,
        summary="실적 제외 사유 선택지",
        tags=["reviews"],
    )
    def list_exclusion_reasons() -> ExclusionReasonsResponseModel:
        """담당자가 고를 수 있는 **실적 제외 사유**를 반환합니다.

        화면이 선택지를 하드코딩하지 않도록 서버가 내려줍니다. 단기 차량 임차를
        어떻게 판단하는지에 대한 안내 문구도 함께 담습니다.

        ⛔ 이 안내는 **사람에게 보여 주는 말**이며 자동 판정 기준이 아닙니다.
        """
        return build_exclusion_reasons_response()

    @app.get(
        "/reviews/{purchase_id}",
        response_model=ReviewItemResponseModel,
        summary="검토 대상 1건",
        tags=["reviews"],
    )
    def get_review(purchase_id: int) -> ReviewItemResponseModel:
        """원본 · 분석 결과 · 확정 상태를 함께 반환합니다."""
        try:
            return ReviewItemResponseModel.from_target(review_service.get_target(purchase_id))
        except ReviewNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get(
        "/reviews/{purchase_id}/history",
        response_model=ReviewHistoryResponseModel,
        summary="검토 변경 이력",
        tags=["reviews"],
    )
    def get_review_history(purchase_id: int) -> ReviewHistoryResponseModel:
        """확정·재검토·분석 이력을 시간순으로 반환합니다."""
        entries = review_service.history(purchase_id)
        return ReviewHistoryResponseModel(
            purchase_id=purchase_id,
            items=[ReviewHistoryItemResponseModel.from_entry(entry) for entry in entries],
        )

    @app.put(
        "/reviews/{purchase_id}",
        response_model=ReviewItemResponseModel,
        summary="구매유형 확정",
        tags=["reviews"],
    )
    def confirm_review(purchase_id: int, payload: ConfirmReviewRequest) -> ReviewItemResponseModel:
        """담당자가 고른 구매유형을 확정합니다.

        ``{"final_purchase_type": null}`` 은 **판단 보류**를 뜻합니다.
        키가 아예 없으면 422 로 거부해 "바꾸지 않음" 과 구분합니다.

        ⛔ 원본(DB-1)은 수정되지 않습니다.
        """
        try:
            target = review_service.confirm(
                purchase_id,
                final_purchase_type=payload.final_purchase_type,
                reviewed_by=payload.reviewed_by,
                review_note=payload.review_note,
            )
        except ReviewNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ReviewValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return ReviewItemResponseModel.from_target(target)

    @app.post(
        "/reviews/{purchase_id}/reopen",
        response_model=ReviewItemResponseModel,
        summary="확정 되돌리기(재검토)",
        tags=["reviews"],
    )
    def reopen_review(purchase_id: int, payload: ReopenReviewRequest) -> ReviewItemResponseModel:
        """확정을 되돌립니다 — 화면의 **"확정 취소"**.

        ⛔ **지우지 않습니다.** 이전 확정값·확정자·확정 시각·메모를 그대로 두고
        상태만 ``REOPENED`` 로 바꾸며, 되돌린 사실 자체도 이력에 남습니다.

        **확정된 건만** 되돌릴 수 있습니다. 이미 되돌렸거나 아직 확정하지 않은
        건에 다시 요청하면 409 로 거부하므로, 버튼을 여러 번 눌러도 상태가
        어긋나지 않습니다.

        Raises:
            HTTPException: 구매가 없으면 404, 확정된 건이 아니면 409.
        """
        try:
            target = review_service.reopen(
                purchase_id, reopened_by=payload.reopened_by, note=payload.note
            )
        except ReviewNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ReviewStateError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return ReviewItemResponseModel.from_target(target)

    @app.put(
        "/reviews/{purchase_id}/performance-exclusion",
        response_model=ReviewItemResponseModel,
        summary="실적 제외 확정",
        tags=["reviews"],
    )
    def exclude_from_performance(
        purchase_id: int, payload: ExcludePerformanceRequest
    ) -> ReviewItemResponseModel:
        """이 구매를 **달성률 계산에서 뺍니다**(2026-08-31 고객 확정 · §0.10).

        고객이 지출결의서·세금계산서·품의서를 확인한 뒤 내리는 판단을 담당자가
        여기서 확정합니다.

        ⛔ **적요로 자동 판정하지 않습니다.** `교육`·`강사`·`임차`·`렌트` 같은
        낱말이 있다는 이유만으로 시스템이 이 요청을 보내지 않습니다. 사람이
        확인하고 누를 때만 동작합니다.

        ⛔ **행을 지우지 않습니다.** 원본은 그대로 남고 계산에서만 빠지며,
        누가·언제·왜 뺐는지가 검토 이력에 남습니다. 검토 화면에도 계속 보입니다
        (그래야 되돌릴 수 있습니다).

        ⛔ **구매유형을 건드리지 않습니다.** 유형과 실적 산입 여부는 다른
        개념이라 다른 필드에 남습니다 — 한 건이 **용역이면서 실적 제외**일 수
        있습니다.

        사유는 `교육비` · `강사료` · `단기 차량 임차` · `기타` 중 하나입니다.

        Raises:
            HTTPException: 구매가 없으면 404, 허용되지 않는 사유면 422.
        """
        try:
            target = review_service.exclude_from_performance(
                purchase_id,
                reason=payload.reason,
                excluded_by=payload.excluded_by,
                note=payload.note,
            )
        except ReviewNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ExclusionReasonError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return ReviewItemResponseModel.from_target(target)

    @app.delete(
        "/reviews/{purchase_id}/performance-exclusion",
        response_model=ReviewItemResponseModel,
        summary="실적 제외 되돌리기",
        tags=["reviews"],
    )
    def include_in_performance(
        purchase_id: int, payload: IncludePerformanceRequest | None = None
    ) -> ReviewItemResponseModel:
        """실적 제외를 되돌려 다시 계산에 넣습니다.

        ⛔ 제외했던 이력을 지우지 않습니다 — 되돌렸다는 사실도 함께 남습니다.

        ⚠️ **예산과목 규칙으로 빠진 건은 이 요청으로 돌아오지 않습니다.**
        그것은 담당자의 판단이 아니라 고객이 확정한 규칙이기 때문입니다
        (응답의 ``performance.by_budget_account_rule`` 이 ``true``).

        Raises:
            HTTPException: 구매가 없으면 404.
        """
        body = payload or IncludePerformanceRequest()
        try:
            target = review_service.include_in_performance(
                purchase_id, changed_by=body.changed_by, note=body.note
            )
        except ReviewNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return ReviewItemResponseModel.from_target(target)

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

    @app.get(
        "/policy-targets",
        response_model=PolicyTargetListResponseModel,
        summary="연도별 정책 목표비율 조회",
        tags=["policy-targets"],
    )
    def list_policy_targets(
        year: int = Query(
            ge=1900,
            le=2999,
            description=(
                "대상 회계연도. **필수입니다.** 구매의 결의일자 연도와 맞춥니다 — "
                "⛔ 다른 연도의 목표비율을 끌어와 채우지 않습니다."
            ),
        ),
    ) -> PolicyTargetListResponseModel:
        """한 연도의 정책별 목표비율을 반환합니다.

        **활성 정책 전체**가 담깁니다. 목표비율이 없는 정책도 빼지 않고
        ``target_rate: null`` · ``target_rate_status: "NOT_SET"`` 으로
        내려갑니다 — 화면이 입력칸을 그리려면 정책 목록 자체가 필요하고,
        "정책이 없다" 와 "목표비율이 아직 없다" 는 다른 상태이기 때문입니다.

        정책 코드와 정책명을 함께 주므로 화면이 정책명을 들고 있을 필요가
        없습니다.

        ⛔ 목표비율의 축은 **연도 × 정책** 뿐입니다. 구매처별 목표비율은
        제공하지 않습니다(DECISIONS §0.20).
        """
        try:
            return policy_target_admin.list_targets(year)
        except PolicyValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put(
        "/policy-targets/{year}/{policy_code}",
        response_model=PolicyTargetItemModel,
        summary="연도별 정책 목표비율 설정·해제",
        tags=["policy-targets"],
        dependencies=[Depends(require_admin_token)],
    )
    def set_policy_target(
        year: int, policy_code: str, payload: PolicyTargetUpdateRequest
    ) -> PolicyTargetItemModel:
        """한 연도 · 한 정책의 목표비율을 저장합니다.

        같은 ``(연도, 정책)`` 으로 몇 번을 호출해도 결과가 같습니다(**멱등**) —
        있으면 값을 바꾸고 없으면 만듭니다.

        ``{"target_rate": null}`` 은 **해제**를 뜻하며 행을 지웁니다.
        ⛔ 0 으로 저장하지 않습니다 — 0% 는 "미설정" 이 아닙니다.
        ``target_rate`` 키가 아예 없으면 422 로 거부해 "변경하지 않음" 과
        "해제" 를 구분합니다.

        목표비율은 ``0`` 초과 ``100`` 이하의 **임의의 값**입니다(예: ``"37.5"``).
        ⛔ 화면의 달성률 표시 구간(20/40/60/80/100)으로 제한하지 않습니다.

        ⚠️ 이 연도의 값은 **다른 연도에 영향을 주지 않습니다.**
        """
        try:
            return policy_target_admin.set_target(year, policy_code, payload.target_rate)
        except PolicyNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PolicyValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put(
        "/policy-targets/{year}/{policy_code}/{scope}",
        response_model=PolicyTargetItemModel,
        summary="연도별 정책 목표비율 설정·해제 (분모 기준별)",
        tags=["policy-targets"],
        dependencies=[Depends(require_admin_token)],
    )
    def set_scoped_policy_target(
        year: int, policy_code: str, scope: str, payload: PolicyTargetUpdateRequest
    ) -> PolicyTargetItemModel:
        """한 연도 · 한 정책 · **한 분모 기준**의 목표비율을 저장합니다.

        위의 `PUT /policy-targets/{year}/{policy_code}` 와 같은 일을 하되,
        어느 분모를 기준으로 재는 목표인지까지 지정합니다. 목표가 하나뿐인
        정책은 위의 경로로 충분하고, 여성기업처럼 **구매유형마다 목표가 다른**
        정책은 이 경로로 각 값을 따로 고칩니다(DECISIONS §0.25 · §0.26).

        ``scope`` 는 ``TOTAL`` · ``CONSTRUCTION`` · ``SERVICE`` · ``GOODS`` ·
        ``PRODUCIBLE_ITEMS`` 중 하나입니다. ``scope=TOTAL`` 은 위 경로와
        완전히 같은 동작입니다.

        ⛔ 유형별 목표를 하나로 합치거나 평균 내지 않습니다 — 그렇게 하면
        고객이 준 값이 아닌 숫자로 달성률을 재게 됩니다.

        ⚠️ 저장할 수 있다고 계산까지 되는 것은 아닙니다. ``PRODUCIBLE_ITEMS``
        처럼 분모를 구할 방법이 아직 없는 기준은 값이 그대로 남되 달성률은
        «계산 보류» 입니다.
        """
        try:
            return policy_target_admin.set_target(year, policy_code, payload.target_rate, scope)
        except PolicyNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PolicyValidationError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

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
