"""
procurement.uploads.upload_service

**표준 Excel 업로드 흐름**을 조립하는 서비스입니다.

::

    .xlsx  →  excel_adapter  →  머리글 검증  →  행 검증  →  [ Mapping — 미구현 ]  →  적재

이 서비스는 위 흐름에서 **검증까지**를 담당하며, 각 단계를 새로 만들지 않고
기존 모듈을 호출만 합니다.

.. warning::
    ⛔ **아직 저장하지 않습니다.**

    표준 양식에는 지급일 컬럼이 없는데 :class:`PurchaseImporter` 는
    ``payment_date`` 를 **필수**로 요구합니다. 이 칸을 무엇으로 채울지는
    PM 결정 사항이므로, 임시로 다른 날짜를 넣어 저장하지 않습니다.

    저장이 승인되면 :meth:`UploadService.validate_file` 결과의
    ``report.rows`` 를 그대로 기존
    :class:`~procurement.importers.batch_import_service.BatchImportService`
    에 넘기면 됩니다. **새 적재 로직을 만들 필요가 없습니다.**

.. note::
    "전부 검증 → 전부 저장" 원칙(지시서 §14)에 따라, 검증과 저장을 한 단계씩
    분리해 두었습니다. 오류가 하나라도 있으면 저장 단계로 넘어가지 않습니다
    (:attr:`UploadResult.storable`).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from procurement.uploads.excel_adapter import ExcelReadError, WorkbookRead, read_standard_workbook
from procurement.uploads.validation import ValidationReport, validate_headers, validate_rows

#: 저장이 막혀 있는 이유. 화면·API 응답에 그대로 노출해 "왜 저장되지 않았는가" 를
#: 사용자와 PM 이 같은 문장으로 보게 한다.
STORAGE_PENDING_REASON: str = (
    "검증만 수행했습니다. 표준 양식에 지급일 항목이 없어 저장 단계는 "
    "업무규칙 확정 후 연결됩니다."
)


@dataclass(frozen=True, kw_only=True)
class UploadResult:
    """업로드 검증 결과.

    Attributes:
        file_name: 올린 파일명.
        file_errors: 파일 단위 오류(읽기 실패·머리글 누락 등). 비어 있으면 정상.
        report: 행 단위 검증 결과. 파일 단위 오류가 있으면 ``None``.
        sheet_name: 읽은 시트 이름.
        stored: 실제로 저장했는지 여부. **현재는 항상 ``False``** 입니다.
        storage_note: 저장하지 않은 이유(또는 저장 결과 설명).
    """

    file_name: str
    file_errors: tuple[str, ...] = ()
    report: ValidationReport | None = None
    sheet_name: str = ""
    stored: bool = False
    storage_note: str = STORAGE_PENDING_REASON

    @property
    def ok(self) -> bool:
        """파일·행 모두 오류가 없는지 여부."""
        return not self.file_errors and self.report is not None and self.report.ok

    @property
    def storable(self) -> bool:
        """저장 단계로 넘어갈 수 있는 상태인지 여부.

        "전부 검증 → 전부 저장" 원칙에 따라, 오류가 하나라도 있으면 ``False``
        입니다. ``True`` 라고 해서 지금 저장되는 것은 아닙니다(승인 대기).
        """
        return self.ok

    @property
    def total_rows(self) -> int:
        """읽은 데이터 행 수."""
        return self.report.total_rows if self.report is not None else 0

    @property
    def valid_rows(self) -> int:
        """오류 없이 통과한 행 수."""
        return len(self.report.rows) if self.report is not None else 0

    @property
    def error_rows(self) -> int:
        """오류가 있는 행 수."""
        return self.report.error_row_count if self.report is not None else 0


class UploadService:
    """표준 Excel 업로드를 읽고 검증합니다.

    상태를 갖지 않으므로 요청마다 새로 만들거나 재사용해도 무방합니다.
    """

    def validate_file(self, source: str | Path) -> UploadResult:
        """파일을 읽어 머리글과 모든 행을 검증합니다.

        오류가 있어도 예외를 던지지 않고 **결과로 돌려줍니다.** 사용자에게
        무엇이 잘못되었는지 한 번에 보여주기 위함입니다.

        Args:
            source: 읽을 ``.xlsx`` 파일 경로.

        Returns:
            :class:`UploadResult`.
        """
        file_name = Path(source).name

        try:
            workbook = read_standard_workbook(source)
        except ExcelReadError as exc:
            return UploadResult(file_name=file_name, file_errors=(str(exc),))

        header_errors = validate_headers(workbook.headers)
        if header_errors:
            return UploadResult(
                file_name=file_name,
                file_errors=tuple(header_errors),
                sheet_name=workbook.sheet_name,
            )

        report = validate_rows(
            _to_validation_rows(workbook),
            first_row_number=workbook.first_row_number,
        )
        return UploadResult(
            file_name=file_name,
            report=report,
            sheet_name=workbook.sheet_name,
        )


def _to_validation_rows(workbook: WorkbookRead) -> list[dict[str, object]]:
    """읽은 행을 검증 계층이 받는 형태로 넘깁니다.

    검증 계층은 **머리글 이름을 키로** 받으므로 변환이 필요 없습니다. 값에도
    손대지 않습니다 — 여기서 값을 고치면 검증 규칙이 두 곳에 생깁니다.
    """
    return workbook.rows
