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

from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from procurement.importers.trace_service import ImportTraceOverview
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
        reasons=tuple(
            RejectionReasonResponseModel(
                reason=reason,
                label=REJECTION_REASON_LABELS.get(reason, reason),
                count=count,
            )
            for reason, count in sorted((overview.reasons or {}).items())
        ),
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
