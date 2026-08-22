"""
procurement.importers.trace_response

적재 추적 결과를 **API 응답 전용 Pydantic 모델**로 변환합니다.

.. warning::
    ⛔ **표현에 주의합니다.** "제외되었습니다" · "검토할 필요가 없습니다" 같은
    확정 표현을 쓰지 않습니다. 고객이 Q5-8 에 답하기 전이므로, 사실만 적습니다
    — "원본에는 있으나 현재 검토 대상에 포함되지 않은 행".

.. note::
    직렬화만 합니다. 값 계산은
    :class:`~procurement.importers.trace_service.ImportTraceService` 가 합니다.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from procurement.importers.trace_service import (
    BatchHistoryEntry,
    ImportTraceOverview,
    RejectionPage,
)
from procurement.models.import_rejection import (
    REJECTION_REASON_LABELS,
    ImportRejection,
    ImportTrace,
)

#: 응답에 담을 최대 미적재 행 수. 잘랐다면 ``truncated`` 로 반드시 알립니다.
MAX_ROWS: int = 500


class RejectionReasonResponseModel(BaseModel):
    """사유별 건수.

    Attributes:
        reason: 사유 코드.
        label: 사람이 읽는 사유.
        count: 해당 사유의 행 수.
    """

    model_config = ConfigDict(frozen=True)

    reason: str
    label: str
    count: int


class RejectedRowResponseModel(BaseModel):
    """적재되지 않은 원본 행 하나.

    Attributes:
        row_number: 원본 파일의 행 번호. 담당자가 원본을 열어 찾을 수 있습니다.
        batch_id: 이 행이 들어 있던 업로드 배치 ID.
        reason: 사유 코드.
        reason_label: 사람이 읽는 사유.
        message: Importer 가 남긴 원문 사유.
        description: 원본 적요.
        company_name: 원본 거래처명.
        budget_account: 원본 예산과목.
        amount: 원본 금액. **음수도 그대로** 보여 줍니다.
    """

    model_config = ConfigDict(frozen=True)

    row_number: int
    batch_id: int | None
    reason: str
    reason_label: str
    message: str
    description: str | None
    company_name: str | None
    budget_account: str | None
    amount: Decimal | None

    @classmethod
    def from_rejection(cls, rejection: ImportRejection) -> RejectedRowResponseModel:
        """기록을 응답 모델로 변환합니다."""
        return cls(
            row_number=rejection.row_number,
            batch_id=rejection.batch_id,
            reason=rejection.reason,
            reason_label=rejection.reason_label,
            message=rejection.message,
            description=rejection.description,
            company_name=rejection.company_name,
            budget_account=rejection.budget_account,
            amount=rejection.amount,
        )


class BatchTraceResponseModel(BaseModel):
    """배치 하나의 대조표.

    Attributes:
        batch_id: 배치 ID. 배치 없이 적재된 행이면 ``null``.
        file_name: 원본 파일명.
        source_rows: 설명 가능한 원본 행 수(적재 + 미적재).
        stored: 적재된 행 수.
        rejected: 적재되지 않아 기록만 남은 행 수.
    """

    model_config = ConfigDict(frozen=True)

    batch_id: int | None
    file_name: str
    source_rows: int
    stored: int
    rejected: int

    @classmethod
    def from_trace(cls, trace: ImportTrace) -> BatchTraceResponseModel:
        """대조표를 응답 모델로 변환합니다."""
        return cls(
            batch_id=trace.batch_id,
            file_name=trace.file_name,
            source_rows=trace.source_rows,
            stored=trace.stored,
            rejected=trace.rejected,
        )


class ImportTraceResponseModel(BaseModel):
    """적재 추적 응답.

    Attributes:
        source_rows: 설명 가능한 원본 행 수 = ``stored + rejected``.
        stored: 현재 검토 화면에 보이는 행 수.
        rejected: 원본에는 있으나 현재 검토 대상에 포함되지 않은 행 수.
        all_visible: 원본 행이 모두 화면에 보이는지 여부.
        notice: 화면에 그대로 쓸 수 있는 안내 문장. ⛔ 확정 표현을 쓰지 않습니다.
        reasons: 사유별 건수.
        batches: 배치별 대조표.
        rows: 미적재 행 목록.
        truncated: 목록을 잘랐는지 여부.
    """

    model_config = ConfigDict(frozen=True)

    source_rows: int
    stored: int
    rejected: int
    all_visible: bool
    notice: str
    reasons: tuple[RejectionReasonResponseModel, ...]
    batches: tuple[BatchTraceResponseModel, ...]
    rows: tuple[RejectedRowResponseModel, ...]
    truncated: bool


def build_trace_response(
    overview: ImportTraceOverview, rejections: list[ImportRejection]
) -> ImportTraceResponseModel:
    """추적 결과를 응답 모델로 변환합니다.

    Args:
        overview: 집계 결과.
        rejections: 미적재 행 목록(전체).

    Returns:
        :class:`ImportTraceResponseModel`.
    """
    shown = rejections[:MAX_ROWS]
    return ImportTraceResponseModel(
        source_rows=overview.source_rows,
        stored=overview.stored,
        rejected=overview.rejected,
        all_visible=overview.all_visible,
        notice=build_notice(overview),
        reasons=_reason_models(overview.reasons),
        batches=tuple(BatchTraceResponseModel.from_trace(trace) for trace in overview.batches),
        rows=tuple(RejectedRowResponseModel.from_rejection(item) for item in shown),
        truncated=len(rejections) > len(shown),
    )


def build_notice(overview: ImportTraceOverview) -> str:
    """담당자에게 보여줄 한 문장을 만듭니다.

    ⛔ **쓰지 않는 표현** — "제외되었습니다" · "실적에서 제외합니다" ·
    "검토할 필요가 없습니다". 고객이 Q5-8 에 답하기 전이므로 어느 것도 사실이
    아닙니다.
    """
    if overview.all_visible:
        return f"원본 {overview.source_rows:,}행이 모두 검토 대상에 있습니다."
    return (
        f"원본 {overview.source_rows:,}행 중 {overview.stored:,}행이 검토 대상입니다. "
        f"나머지 {overview.rejected:,}행은 원본에는 있으나 현재 검토 대상에 "
        "포함되지 않았습니다 — 처리 방식 확인이 필요합니다."
    )


class BatchHistoryResponseModel(BaseModel):
    """업로드 한 번의 기록.

    Attributes:
        batch_id: 배치 ID.
        file_name: 원본 파일명.
        period_start: 대상 기간 시작일.
        period_end: 대상 기간 종료일.
        uploaded_at: 업로드 시각.
        status: 배치 상태. ⛔ 기존 lifecycle 값 그대로 (``ACTIVE`` / ``SUPERSEDED``).
        is_current: 지금 검토·계산에 쓰이는 배치인가.
        superseded_by: 이 배치를 대체한 배치 ID.
        source_rows: 기록된 원본 행 수. **모르면 ``null``** — 0 이 아닙니다.
        stored: 적재된 행 수.
        rejected: 적재되지 않아 기록된 행 수.
        unexplained: 설명되지 않은 행 수. 원본 행 수를 모르면 ``null``.
        reasons: 사유별 건수.
    """

    model_config = ConfigDict(frozen=True)

    batch_id: int | None
    file_name: str
    period_start: date
    period_end: date
    uploaded_at: datetime | None
    status: str
    is_current: bool
    superseded_by: int | None
    source_rows: int | None
    stored: int
    rejected: int
    unexplained: int | None
    reasons: tuple[RejectionReasonResponseModel, ...]

    @classmethod
    def from_entry(cls, entry: BatchHistoryEntry) -> BatchHistoryResponseModel:
        """이력 항목을 응답 모델로 변환합니다."""
        return cls(
            batch_id=entry.batch_id,
            file_name=entry.batch.file_name,
            period_start=entry.batch.period_start,
            period_end=entry.batch.period_end,
            uploaded_at=entry.batch.uploaded_at,
            status=entry.batch.status,
            is_current=entry.is_current,
            superseded_by=entry.batch.superseded_by,
            source_rows=entry.source_rows,
            stored=entry.stored,
            rejected=entry.rejected,
            unexplained=entry.unexplained,
            reasons=_reason_models(entry.reasons),
        )


class BatchHistoryListResponseModel(BaseModel):
    """업로드 이력 목록.

    Attributes:
        items: 최근 업로드 순.
        total: 전체 업로드 수(대체된 것 포함).
        current: 지금 유효한 배치 수.
    """

    model_config = ConfigDict(frozen=True)

    items: tuple[BatchHistoryResponseModel, ...]
    total: int
    current: int


class RejectionPageResponseModel(BaseModel):
    """조건에 맞는 미적재 행 한 페이지.

    Attributes:
        items: 이 페이지의 행.
        total: 조건에 맞는 전체 건수.
        page: 현재 페이지.
        page_size: 한 페이지 건수.
        total_pages: 전체 쪽 수.
        has_previous: 이전 쪽이 있는가.
        has_next: 다음 쪽이 있는가.
    """

    model_config = ConfigDict(frozen=True)

    items: tuple[RejectedRowResponseModel, ...]
    total: int
    page: int
    page_size: int
    total_pages: int
    has_previous: bool
    has_next: bool

    @classmethod
    def from_page(cls, page: RejectionPage) -> RejectionPageResponseModel:
        """조회 결과를 응답 모델로 변환합니다."""
        return cls(
            items=tuple(RejectedRowResponseModel.from_rejection(item) for item in page.items),
            total=page.total,
            page=page.page,
            page_size=page.page_size,
            total_pages=page.total_pages,
            has_previous=page.has_previous,
            has_next=page.has_next,
        )


def build_history_response(
    entries: list[BatchHistoryEntry],
) -> BatchHistoryListResponseModel:
    """업로드 이력을 응답 모델로 변환합니다."""
    return BatchHistoryListResponseModel(
        items=tuple(BatchHistoryResponseModel.from_entry(entry) for entry in entries),
        total=len(entries),
        current=sum(1 for entry in entries if entry.is_current),
    )


def _reason_models(
    reasons: dict[str, int] | None,
) -> tuple[RejectionReasonResponseModel, ...]:
    """사유별 건수를 응답 모델 묶음으로 만듭니다."""
    return tuple(
        RejectionReasonResponseModel(
            reason=reason,
            label=REJECTION_REASON_LABELS.get(reason, reason),
            count=count,
        )
        for reason, count in sorted((reasons or {}).items())
    )
