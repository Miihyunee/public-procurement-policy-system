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
from collections.abc import Mapping, Sequence
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
    "오류가 있어 저장하지 않았습니다. 한 행이라도 오류가 있으면 정상 행도 저장하지 않습니다."
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

    @property
    def rejected_rows(self) -> int:
        """원본에는 있었으나 **적재되지 않은** 행 수.

        ⛔ "제외 확정" 이 아닙니다. 사유와 함께 기록만 남긴 행입니다(Q5-8).
        """
        return len(self.batch.rejections) if self.batch is not None else 0

    @property
    def rejection_reasons(self) -> dict[str, int]:
        """사유별 미적재 행 수."""
        return dict(self.batch.trace.reasons or {}) if self.batch is not None else {}

    @property
    def unexplained_rows(self) -> int:
        """**설명되지 않은** 행 수 = 원본 − 적재 − 미적재.

        ⚠️ 0 이 아니면 어딘가에서 행이 사라진 것입니다. 저장하지 않았으면 0
        입니다(적재/미적재를 말할 수 없으므로).
        """
        return self.batch.trace.unexplained if self.batch is not None else 0


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

        처리 순서(PM-006 · PM-007 · STEP 119 · STEP 121)::

            1. 파일 읽기 · 전체 행 검증
            2. 오류가 하나라도 있으면 → 저장하지 않고 반환 (기존 데이터 그대로)
            3. 고른 기간 밖의 결의일자 확인 → 하나라도 있으면 UploadPeriodMismatchError
            4. 기간이 겹치는 다른 배치 확인 → 있으면 OverlappingPeriodBatchError
            5. 같은 기간 ACTIVE 배치 확인
            6. 있는데 replace_existing 이 False → ExistingPeriodBatchError
            7. 새 배치 저장 → **저장에 성공한 뒤에야** 이전 배치를 SUPERSEDED

        ⭐ **거절은 전부 교체보다 앞에 있습니다.** 올릴 수 없는 파일로 기존
        데이터를 지울지 물을 이유가 없기 때문입니다(STEP 121 §4 · §11).

        **검증 실패 때문에 기존 정상 데이터가 사라지는 상황은 발생하지
        않습니다.** 2~6단계에서 이미 반환·예외가 나므로 적재 계층을 호출조차
        하지 않고, 7단계의 무효화는 기존 :class:`BatchImportService` 안에서
        새 배치 적재가 끝난 뒤에 일어납니다.

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
            UploadPeriodMismatchError: 고른 기간 밖의 결의일자가 하나라도 있는
                경우. **파일 전체를 거절**하며 DB 는 변경되지 않습니다.
            OverlappingPeriodBatchError: 기간이 겹치는 다른 배치가 있는 경우.
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

        # 🟢 고른 기간과 결의일자가 다른 행이 하나라도 있으면 **파일 전체를
        #    거절한다**(STEP 121 · 고객 확정). ⛔ 교체 확인보다 **먼저** 본다 —
        #    올릴 수 없는 파일로 기존 데이터를 지울지 물을 이유가 없다.
        mismatched = _rows_outside_period(result, period_start, period_end)
        if mismatched:
            raise UploadPeriodMismatchError(
                mismatched=mismatched,
                total_rows=result.valid_rows,
                period_start=period_start,
                period_end=period_end,
            )

        # ⛔ 기간이 **겹치기만 하는** 기존 데이터가 있으면 적재하지 않는다.
        #    교체하면 다른 달까지 사라지고, 두면 그 달이 이중 집계된다(STEP 119).
        overlapping = self._batch_import_service.find_overlapping_batches(period_start, period_end)
        if overlapping:
            raise OverlappingPeriodBatchError(
                existing=overlapping,
                period_start=period_start,
                period_end=period_end,
            )

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
            f"{period_start.year}년 데이터가 이미 등록되어 있습니다(배치 #{existing.batch_id})."
        )


class OverlappingPeriodBatchError(RuntimeError):
    """대상 기간과 **겹치는** 기존 데이터가 있어 적재를 거절할 때 발생합니다.

    같은 기간이 아니라 **다른 기간인데 겹치는** 경우입니다. 한 해치를 통짜로
    (``1/1~12/31``) 올려 둔 상태에서 한 달치를 올리는 것이 대표적입니다.

    .. warning::
        ⛔ **교체 확인으로 해결되지 않습니다.** 겹친 배치를 대체해 버리면
        그 안의 **다른 달까지 함께 사라집니다**(STEP 119 §9 금지). 그렇다고
        그대로 두면 그 달 행이 두 배치에 남아 **이중으로 집계**됩니다(§8 금지).

        어느 쪽도 안전하지 않으므로 **적재하지 않고 그대로 둡니다.** 「한 해
        통짜 데이터가 있을 때 한 달만 바꾸려면 어떻게 하는가」는 아직 정해지지
        않은 업무규칙입니다 — ⛔ 여기서 임의로 정하지 않습니다.

    **DB 는 전혀 변경되지 않은 상태**에서 발생합니다.

    Attributes:
        existing: 겹치는 ACTIVE 배치들.
        period_start: 올리려던 기간 시작일.
        period_end: 올리려던 기간 종료일.
    """

    def __init__(
        self,
        *,
        existing: Sequence[ImportBatch],
        period_start: date,
        period_end: date,
    ) -> None:
        """오류를 만듭니다."""
        self.existing = list(existing)
        self.period_start = period_start
        self.period_end = period_end
        periods = ", ".join(f"{row.period_start}~{row.period_end}" for row in self.existing)
        super().__init__(
            f"{period_start}~{period_end} 와 겹치는 데이터가 이미 등록되어 있습니다"
            f"({periods}). 겹치는 기간이 서로 다르므로 그 달만 바꿀 수 없습니다."
        )


class UploadPeriodMismatchError(RuntimeError):
    """고른 기간 밖의 결의일자가 있어 **파일 전체를 거절**할 때 발생합니다.

    🟢 **2026-09-05 고객 확정 (STEP 121)**

        올릴 때 고른 연도·월과 거래의 **결의일자**가 일치해야 한다. 하나라도
        다르면 **파일 전체를 거절**한다. ⛔ 일부 행만 빼고 나머지를 적재하지
        않는다.

    왜 전체를 거절하는가
    ====================
    일부만 적재하면 담당자가 「몇 건이 빠졌는지」를 매번 확인해야 하고, 빠진
    행은 어느 달에도 올라가지 않은 채 조용히 사라진다. 파일을 통째로 돌려주면
    담당자가 원본을 고쳐 다시 올리므로 빠지는 행이 없다.

    .. warning::
        ⛔ **결의일자를 고쳐 맞추지 않습니다.** 신고기준일·계약일자·지급일로
        대신하지도, 고른 달로 옮기지도 않습니다.

    **DB 는 전혀 변경되지 않은 상태**에서 발생합니다 — 교체 확인보다 먼저
    검사하므로, 기존 그 달 데이터도 그대로 남습니다.

    Attributes:
        mismatched: 어긋난 결의일자의 **연월별 건수**. ⛔ 거래 원본을 담지
            않습니다 — 오류 메시지에 실제 거래를 늘어놓지 않기 위함입니다.
        total_rows: 검증을 통과한 전체 행 수.
        period_start: 고른 기간 시작일.
        period_end: 고른 기간 종료일.
    """

    def __init__(
        self,
        *,
        mismatched: Mapping[str, int],
        total_rows: int,
        period_start: date,
        period_end: date,
    ) -> None:
        """오류를 만듭니다."""
        self.mismatched = dict(mismatched)
        self.total_rows = total_rows
        self.period_start = period_start
        self.period_end = period_end
        found = ", ".join(f"{month}({count}건)" for month, count in sorted(self.mismatched.items()))
        super().__init__(
            f"고른 기간({period_start}~{period_end}) 밖의 결의일자가 "
            f"{self.mismatch_count}건 있어 파일 전체를 등록하지 않았습니다: {found}"
        )

    @property
    def mismatch_count(self) -> int:
        """어긋난 행 수."""
        return sum(self.mismatched.values())


def _rows_outside_period(
    result: UploadResult, period_start: date, period_end: date
) -> dict[str, int]:
    """고른 기간 밖의 결의일자를 **연월별로** 셉니다.

    ⛔ 판정 기준은 ``resolution_date`` 하나뿐입니다 — 신고기준일·계약일자·
    지급일은 보지 않습니다(🟢 §0.10 · STEP 86).

    .. note::
        결의일자가 비어 있는 행은 여기까지 오지 않습니다. 표준 양식에서
        **필수 컬럼**이라 검증 단계에서 이미 오류로 걸리고, 그러면 파일 전체가
        저장되지 않습니다. ⛔ 그래서 여기서 빈 값에 대한 규칙을 새로 만들지
        않습니다.

    Args:
        result: 검증을 통과한 업로드 결과.
        period_start: 고른 기간 시작일.
        period_end: 고른 기간 종료일.

    Returns:
        ``{"2026-07": 2, "2025-12": 1}`` 처럼 연월 → 건수. 모두 맞으면 빈 사전.
    """
    if result.report is None:
        return {}
    outside: dict[str, int] = {}
    for row in result.report.rows:
        resolution_date = row.values.get("resolution_date")
        if not isinstance(resolution_date, date):
            continue  # 필수 컬럼이라 여기 올 일이 없다 — 와도 판정하지 않는다
        if period_start <= resolution_date <= period_end:
            continue
        label = f"{resolution_date.year:04d}-{resolution_date.month:02d}"
        outside[label] = outside.get(label, 0) + 1
    return outside


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
            f"같은 기간의 이전 배치 #{batch.superseded_batch.batch_id} 는 계산에서 제외됩니다."
        )
    if batch.duplicate_of is not None:
        lines.append(
            f"⚠️ 내용이 같은 파일이 배치 #{batch.duplicate_of.batch_id} 로 이미 올라와 있습니다."
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
