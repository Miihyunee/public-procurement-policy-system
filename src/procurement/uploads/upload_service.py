"""
procurement.uploads.upload_service

**표준 Excel 업로드 흐름**을 조립하는 서비스입니다.

::

    .xlsx  →  excel_adapter  →  머리글 검증  →  행 검증  →  mapping  →  BatchImportService
                                                                            ↓
                                                          PurchaseImporter → Repository

이 서비스는 각 단계를 **새로 만들지 않고 호출만** 합니다. 특히 저장은 기존
:class:`~procurement.importers.batch_import_service.BatchImportService` 를 그대로
사용하므로, 업로드 경로와 기존 적재 경로가 **같은 저장 로직**을 공유합니다.

.. note::
    **"전부 검증 → 전부 저장"** 원칙(지시서 §14)을 구조로 강제합니다.

    - :meth:`UploadService.validate_file` — 검증만. DB 를 건드리지 않습니다.
    - :meth:`UploadService.import_file` — 검증 후 **오류가 하나도 없을 때만**
      저장합니다. 오류가 있으면 :class:`BatchImportService` 를 **호출조차 하지
      않으므로** DB 에 아무 변화가 없습니다.

    일부 행만 먼저 저장하는 경로는 만들지 않았습니다.

.. warning::
    ⛔ **같은 기간에 이미 등록된 데이터가 있으면 묻지 않고 교체하지 않습니다**
    (PM-005). 호출자가 ``replace_existing=True`` 로 **명시적으로 확인**해야만
    교체합니다. 확인이 없으면 :class:`ExistingPeriodBatchError` 를 냅니다.

    확인 여부를 묻는 시점은 **검증을 통과한 뒤**입니다. 오류가 있는 파일로는
    교체 여부를 물을 이유가 없기 때문입니다.

.. warning::
    ⛔ **대상 기간은 호출자가 지정합니다.** 파일 내용에서 유추하지 않습니다.
    어느 날짜로 연도를 나눌지는 운영자 설정 사항이며(D-24), 파일에서 추론하면
    확정되지 않은 규칙이 생깁니다.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from procurement.importers.batch_import_service import BatchImportResult, BatchImportService
from procurement.models.import_batch import ImportBatch
from procurement.uploads.excel_adapter import ExcelReadError, WorkbookRead, read_standard_workbook
from procurement.uploads.mapping import to_import_rows
from procurement.uploads.validation import ValidationReport, validate_headers, validate_rows

#: 검증만 수행했을 때의 설명. 화면·API 응답에 그대로 노출한다.
VALIDATION_ONLY_NOTE: str = "검증만 수행했습니다. 저장하지 않았습니다."

#: 오류 때문에 저장하지 않았을 때의 설명.
NOT_STORED_NOTE: str = (
    "오류가 있어 저장하지 않았습니다. 한 행이라도 오류가 있으면 정상 행도 "
    "저장하지 않습니다."
)


@dataclass(frozen=True, kw_only=True)
class UploadResult:
    """업로드 처리 결과.

    Attributes:
        file_name: 올린 파일명.
        file_errors: 파일 단위 오류(읽기 실패·머리글 누락 등). 비어 있으면 정상.
        report: 행 단위 검증 결과. 파일 단위 오류가 있으면 ``None``.
        sheet_name: 읽은 시트 이름.
        stored: 실제로 저장했는지 여부.
        storage_note: 저장 여부에 대한 설명.
        batch: 저장에 성공했을 때의 배치 적재 결과. 저장하지 않았으면 ``None``.
    """

    file_name: str
    file_errors: tuple[str, ...] = ()
    report: ValidationReport | None = None
    sheet_name: str = ""
    stored: bool = False
    storage_note: str = VALIDATION_ONLY_NOTE
    batch: BatchImportResult | None = None

    @property
    def ok(self) -> bool:
        """파일·행 모두 오류가 없는지 여부."""
        return not self.file_errors and self.report is not None and self.report.ok

    @property
    def storable(self) -> bool:
        """저장 단계로 넘어갈 수 있는 상태인지 여부.

        "전부 검증 → 전부 저장" 원칙에 따라, 오류가 하나라도 있으면 ``False``.
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

    @property
    def stored_rows(self) -> int:
        """실제로 DB 에 저장된 행 수. 저장하지 않았으면 0."""
        return self.batch.report.stored_count if self.batch is not None else 0

    @property
    def batch_id(self) -> int | None:
        """저장된 배치 ID. 저장하지 않았으면 ``None``."""
        return self.batch.batch.batch_id if self.batch is not None else None


class UploadService:
    """표준 Excel 업로드를 읽고 검증하며, 요청 시 기존 적재 계층으로 저장합니다.

    Args:
        batch_import_service: 저장에 사용할 기존
            :class:`BatchImportService`. ``None`` 이면 :meth:`import_file` 을
            쓸 수 없고 검증만 가능합니다(저장 계층 없이 검증만 하는 용도).
    """

    def __init__(self, batch_import_service: BatchImportService | None = None) -> None:
        """서비스를 초기화합니다."""
        self._batch_import_service = batch_import_service

    # ------------------------------------------------------------------
    # 검증 전용
    # ------------------------------------------------------------------
    def validate_file(self, source: str | Path) -> UploadResult:
        """파일을 읽어 머리글과 모든 행을 검증합니다. **저장하지 않습니다.**

        오류가 있어도 예외를 던지지 않고 **결과로 돌려줍니다.** 사용자에게
        무엇이 잘못되었는지 한 번에 보여주기 위함입니다.

        Args:
            source: 읽을 ``.xlsx`` 파일 경로.

        Returns:
            :class:`UploadResult`. ``stored`` 는 항상 ``False`` 입니다.
        """
        return self._read_and_validate(source)

    # ------------------------------------------------------------------
    # 검증 + 저장
    # ------------------------------------------------------------------
    def import_file(
        self,
        source: str | Path,
        *,
        period_start: date,
        period_end: date,
        replace_existing: bool = False,
    ) -> UploadResult:
        """파일을 검증하고, **오류가 하나도 없을 때만** 저장합니다.

        처리 순서(PM-006 · PM-007)::

            1. 파일 읽기 · 전체 행 검증
            2. 오류가 하나라도 있으면 → 저장하지 않고 반환 (기존 데이터 그대로)
            3. 같은 기간 ACTIVE 배치 확인
            4. 있는데 replace_existing 이 False → ExistingPeriodBatchError
            5. 새 배치 저장 → **저장에 성공한 뒤에야** 이전 배치를 SUPERSEDED

        **검증 실패 때문에 기존 정상 데이터가 사라지는 상황은 발생하지
        않습니다.** 2단계에서 이미 반환하므로 적재 계층을 호출조차 하지 않고,
        5단계의 무효화는 기존 :class:`BatchImportService` 안에서 새 배치 적재가
        끝난 뒤에 일어납니다.

        Args:
            source: 읽을 ``.xlsx`` 파일 경로.
            period_start: 대상 기간 시작일. **호출자가 지정합니다.**
            period_end: 대상 기간 종료일. **호출자가 지정합니다.**
            replace_existing: 같은 기간의 기존 데이터를 교체해도 좋다는
                **사용자의 명시적 확인**. 기본값 ``False`` — 확인 없이는
                교체하지 않습니다.

        Returns:
            :class:`UploadResult`. 저장했으면 ``stored`` 가 ``True`` 이고
            ``batch`` 에 적재 결과가 담깁니다.

        Raises:
            UploadStorageUnavailableError: 저장 계층 없이 생성된 경우.
            ExistingPeriodBatchError: 같은 기간에 이미 데이터가 있는데
                ``replace_existing`` 이 ``False`` 인 경우. **DB 는 변경되지
                않습니다.**
        """
        if self._batch_import_service is None:
            raise UploadStorageUnavailableError(
                "저장 계층이 연결되지 않아 업로드를 저장할 수 없습니다."
            )

        result = self._read_and_validate(source)
        if not result.storable:
            # ⛔ 오류가 있으면 적재 계층을 **호출하지 않는다.** DB 는 그대로다.
            #    교체 여부도 묻지 않는다 — 저장할 수 없는 파일이기 때문이다.
            return _with_note(result, NOT_STORED_NOTE)

        existing = self._batch_import_service.find_active_batch(period_start, period_end)
        if existing is not None and not replace_existing:
            # ⛔ 묻지 않고 교체하지 않는다. 여기서 멈추므로 DB 는 그대로다.
            raise ExistingPeriodBatchError(
                existing=existing,
                period_start=period_start,
                period_end=period_end,
            )

        assert result.report is not None  # storable 이 보장
        batch = self._batch_import_service.import_batch(
            to_import_rows(result.report.rows),
            file_name=result.file_name,
            period_start=period_start,
            period_end=period_end,
            file_hash=_file_hash(source),
        )
        return _stored(result, batch)

    # ------------------------------------------------------------------
    # 내부
    # ------------------------------------------------------------------
    def _read_and_validate(self, source: str | Path) -> UploadResult:
        """읽기 → 머리글 검증 → 행 검증을 수행합니다."""
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


class UploadStorageUnavailableError(RuntimeError):
    """저장 계층 없이 저장을 시도했을 때 발생합니다."""


class ExistingPeriodBatchError(RuntimeError):
    """같은 기간에 이미 등록된 데이터가 있는데 교체 확인이 없을 때 발생합니다.

    **DB 는 전혀 변경되지 않은 상태**에서 발생합니다. 호출자는 사용자에게
    교체 여부를 물은 뒤, 승인받으면 ``replace_existing=True`` 로 다시
    호출하면 됩니다.

    Attributes:
        existing: 이미 등록되어 있는 ACTIVE 배치.
        period_start: 대상 기간 시작일.
        period_end: 대상 기간 종료일.
    """

    def __init__(
        self,
        *,
        existing: ImportBatch,
        period_start: date,
        period_end: date,
    ) -> None:
        """오류를 만듭니다."""
        self.existing = existing
        self.period_start = period_start
        self.period_end = period_end
        super().__init__(
            f"{period_start.year}년 데이터가 이미 등록되어 있습니다"
            f"(배치 #{existing.batch_id})."
        )


def _with_note(result: UploadResult, note: str) -> UploadResult:
    """저장 설명만 바꾼 결과를 만듭니다."""
    return UploadResult(
        file_name=result.file_name,
        file_errors=result.file_errors,
        report=result.report,
        sheet_name=result.sheet_name,
        stored=False,
        storage_note=note,
    )


def _stored(result: UploadResult, batch: BatchImportResult) -> UploadResult:
    """저장 결과를 반영한 결과를 만듭니다."""
    lines = [f"배치 #{batch.batch.batch_id} 로 {batch.report.stored_count:,}건을 저장했습니다."]
    if batch.replaced and batch.superseded_batch is not None:
        lines.append(
            f"같은 기간의 이전 배치 #{batch.superseded_batch.batch_id} 는 "
            "계산에서 제외됩니다."
        )
    if batch.duplicate_of is not None:
        lines.append(
            f"⚠️ 내용이 같은 파일이 배치 #{batch.duplicate_of.batch_id} 로 "
            "이미 올라와 있습니다."
        )
    return UploadResult(
        file_name=result.file_name,
        file_errors=result.file_errors,
        report=result.report,
        sheet_name=result.sheet_name,
        stored=True,
        storage_note=" ".join(lines),
        batch=batch,
    )


def _to_validation_rows(workbook: WorkbookRead) -> list[dict[str, object]]:
    """읽은 행을 검증 계층이 받는 형태로 넘깁니다.

    검증 계층은 **머리글 이름을 키로** 받으므로 변환이 필요 없습니다. 값에도
    손대지 않습니다 — 여기서 값을 고치면 검증 규칙이 두 곳에 생깁니다.
    """
    return workbook.rows


def _file_hash(source: str | Path) -> str:
    """파일 내용 해시를 만듭니다(같은 파일 재업로드 감지용).

    감지되어도 **적재를 막지 않고 경고만** 남깁니다(기존 규칙 유지).
    """
    digest = hashlib.sha256()
    with Path(source).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
