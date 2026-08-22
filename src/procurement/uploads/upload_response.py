"""
procurement.uploads.upload_response

업로드 검증 결과(:class:`~procurement.uploads.upload_service.UploadResult`)를
**API 응답 전용 Pydantic 모델**로 변환합니다.

응답은 화면이 그대로 그릴 수 있는 형태를 목표로 합니다(지시서 §15).

- 성공: 총 행 수 · 정상 행 수 · 저장 여부
- 실패: 총 행 수 · 정상 행 수 · 오류 행 수 · **행 번호 / 항목명 / 내용** 목록

.. note::
    업무 판단을 하지 않습니다. 값 조합은
    :class:`~procurement.uploads.upload_service.UploadService` 가 하고, 이
    모듈은 직렬화만 담당합니다.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from procurement.importers.trace_response import RejectionReasonResponseModel
from procurement.models.import_rejection import REJECTION_REASON_LABELS
from procurement.uploads.upload_service import UploadResult

#: 응답에 담을 최대 문제 건수. 수천 건을 그대로 내보내면 화면이 무의미해진다.
MAX_ISSUES: int = 200


class UploadIssueResponseModel(BaseModel):
    """문제 한 건.

    Attributes:
        row_number: 엑셀 행 번호(사용자가 화면에서 보는 번호와 같습니다).
        header: 항목명(엑셀 컬럼명). 행 전체 문제이면 ``null``.
        message: 사용자에게 보여줄 설명.
        severity: ``error`` (저장 불가) 또는 ``warning`` (확인 필요).
    """

    model_config = ConfigDict(frozen=True)

    row_number: int
    header: str | None
    message: str
    severity: str


class UploadResponseModel(BaseModel):
    """업로드 검증 결과 응답.

    Attributes:
        file_name: 올린 파일명.
        sheet_name: 읽은 시트 이름.
        ok: 파일·행 모두 정상인지 여부.
        stored: 실제로 저장했는지 여부.
        storage_note: 저장 여부에 대한 설명(저장하지 않았다면 그 이유).
        total_rows: 읽은 데이터 행 수.
        valid_rows: 오류 없이 통과한 행 수.
        error_rows: 오류가 있는 행 수.
        stored_rows: 실제로 DB 에 저장된 행 수. 저장하지 않았으면 0.
        rejected_rows: 원본에는 있었으나 적재되지 않아 **기록만 남은** 행 수.
            ⛔ 제외 확정이 아닙니다 — 처리 방식은 확인 대기입니다(Q5-8).
        rejection_reasons: 사유별 미적재 행 수(코드 · 표시 이름 · 건수).
            ⛔ 표시 이름에 "제외" 같은 확정 표현을 쓰지 않습니다.
        batch_id: 저장된 배치 ID. 저장하지 않았으면 ``null``.
        file_errors: 파일 단위 오류(읽기 실패·머리글 누락 등).
        issues: 행 단위 문제 목록.
        truncated: 문제가 너무 많아 목록을 잘랐는지 여부.
        summary_lines: 화면 상단에 바로 쓸 요약 문장.
    """

    model_config = ConfigDict(frozen=True)

    file_name: str
    sheet_name: str
    ok: bool
    stored: bool
    storage_note: str
    total_rows: int
    valid_rows: int
    error_rows: int
    stored_rows: int
    rejected_rows: int = 0
    rejection_reasons: tuple[RejectionReasonResponseModel, ...] = ()
    batch_id: int | None
    file_errors: tuple[str, ...]
    issues: tuple[UploadIssueResponseModel, ...]
    truncated: bool
    summary_lines: tuple[str, ...]


def build_upload_response(result: UploadResult) -> UploadResponseModel:
    """검증 결과를 응답 모델로 변환합니다.

    Args:
        result: :class:`UploadResult`.

    Returns:
        :class:`UploadResponseModel`.
    """
    report = result.report
    all_issues = (
        sorted(report.issues, key=lambda issue: (issue.row_number, issue.header or ""))
        if report is not None
        else []
    )
    shown = all_issues[:MAX_ISSUES]

    summary = report.summary_lines() if report is not None else ("파일을 읽을 수 없습니다.",)

    return UploadResponseModel(
        file_name=result.file_name,
        sheet_name=result.sheet_name,
        ok=result.ok,
        stored=result.stored,
        storage_note=result.storage_note,
        total_rows=result.total_rows,
        valid_rows=result.valid_rows,
        error_rows=result.error_rows,
        stored_rows=result.stored_rows,
        rejected_rows=result.rejected_rows,
        rejection_reasons=tuple(
            RejectionReasonResponseModel(
                reason=reason,
                label=REJECTION_REASON_LABELS.get(reason, reason),
                count=count,
            )
            for reason, count in sorted(result.rejection_reasons.items())
        ),
        batch_id=result.batch_id,
        file_errors=result.file_errors,
        issues=tuple(
            UploadIssueResponseModel(
                row_number=issue.row_number,
                header=issue.header,
                message=issue.message,
                severity=issue.severity,
            )
            for issue in shown
        ),
        truncated=len(all_issues) > len(shown),
        summary_lines=summary,
    )
