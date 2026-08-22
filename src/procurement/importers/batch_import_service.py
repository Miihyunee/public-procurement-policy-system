"""
procurement.importers.batch_import_service

**월별 누적 적재**를 배치 단위로 수행하는 서비스입니다.

매월 데이터를 올리면 그 달의 배치가 만들어지고, 이전 달 데이터는 그대로 남아
누적됩니다. 같은 달을 다시 올리면 **이전 배치를 대체**합니다(**D-25 확정**).

흐름::

    ① 같은 기간의 ACTIVE 배치 조회 (있으면 대체 대상)
    ② 새 배치 생성 (status=ACTIVE)
    ③ 행 적재 (purchase.batch_id = 새 배치)
    ④ 적재 결과(행 수·금액 합계)를 배치에 기록
    ⑤ 기존 배치를 SUPERSEDED 로 표시

**③ 이 성공한 뒤에 ⑤ 를 수행**합니다. 중간에 실패하면 기존 배치가 ACTIVE 로
남으므로, 최악의 경우에도 "이전 달 실적이 사라지는" 일은 발생하지 않습니다.

.. note::
    현재 :class:`~procurement.database.base.BaseRepository` 는 호출 단위로 연결을
    열고 닫으므로 ①~⑤ 가 하나의 트랜잭션이 아닙니다. 그래서 대체를 **마지막
    단일 UPDATE** 로 두었습니다. 그래도 ⑤ 직전에 중단되면 같은 기간에 ACTIVE
    배치가 2 개 남을 수 있어, :meth:`BatchImportService.find_conflicts` 로 검출할
    수 있게 했습니다.

.. warning::
    **대상 기간(``period_start`` / ``period_end``)은 호출자가 반드시 지정합니다.**
    파일 내용에서 자동으로 유추하지 않습니다 — 어느 날짜 컬럼으로 기간을 잡을지가
    **D-24 (미확정)** 에 종속되기 때문입니다. 자동 도출은 결정 이후에 다룹니다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from procurement.database.import_batch_repository import ImportBatchRepository
from procurement.database.import_rejection_repository import ImportRejectionRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.importers.purchase_importer import ImportReport, PurchaseImporter
from procurement.importers.rejection_trace import build_rejections
from procurement.models.import_batch import ImportBatch
from procurement.models.import_rejection import ImportRejection, ImportTrace


@dataclass(frozen=True, kw_only=True)
class BatchImportResult:
    """배치 적재 결과.

    Attributes:
        batch: 이번에 만들어진 배치(적재 결과가 반영된 상태).
        report: 행별 적재 결과.
        superseded_batch: 이번 업로드로 대체된 이전 배치. 없으면 ``None``.
        duplicate_of: 내용이 같은 파일(해시 일치)로 이미 적재된 ACTIVE 배치.
            없으면 ``None``. **적재를 막지는 않으며 경고 목적**입니다.
        rejections: 원본에는 있었으나 적재되지 않은 행의 기록.
            ⛔ "제외 확정" 이 아니라 **추적 기록**입니다(Q5-8).
    """

    batch: ImportBatch
    report: ImportReport
    superseded_batch: ImportBatch | None = None
    duplicate_of: ImportBatch | None = None
    rejections: tuple[ImportRejection, ...] = ()

    @property
    def trace(self) -> ImportTrace:
        """원본 → 적재 → 미적재 대조표.

        ``trace.unexplained`` 가 0 이 아니면 어딘가에서 행이 사라진 것입니다.
        """
        reasons: dict[str, int] = {}
        for item in self.rejections:
            reasons[item.reason] = reasons.get(item.reason, 0) + 1
        return ImportTrace(
            source_rows=self.report.total_count,
            batch_id=self.batch.batch_id,
            file_name=self.batch.file_name,
            stored=self.report.stored_count,
            rejected=len(self.rejections),
            reasons=reasons,
        )

    @property
    def replaced(self) -> bool:
        """이전 배치를 대체했는지 여부."""
        return self.superseded_batch is not None

    def format_report(self) -> str:
        """사람이 읽을 수 있는 요약을 만듭니다."""
        lines = [
            f"배치 #{self.batch.batch_id} — {self.batch.file_name}",
            f"대상 기간: {self.batch.period_start} ~ {self.batch.period_end}",
            f"적재: {self.batch.row_count}건 / 합계 {self.batch.total_amount}",
        ]
        if self.superseded_batch is not None:
            previous = self.superseded_batch
            lines.append(
                f"대체: 배치 #{previous.batch_id} "
                f"({previous.row_count}건 / 합계 {previous.total_amount}) → 계산에서 제외"
            )
        if self.rejections:
            trace = self.trace
            lines.append(
                f"원본 {trace.source_rows}행 중 {trace.rejected}행이 적재되지 않았습니다"
                " — 사유와 함께 기록해 두었습니다(업무 처리 방식 확인 필요)."
            )
        if self.duplicate_of is not None:
            lines.append(
                f"⚠️ 내용이 같은 파일이 배치 #{self.duplicate_of.batch_id} 로 이미 적재되어 "
                "있습니다. 의도한 재업로드인지 확인하세요."
            )
        return "\n".join(lines)


class BatchImportService:
    """업로드 배치를 만들고 구매데이터를 적재합니다."""

    def __init__(
        self,
        importer: PurchaseImporter,
        batch_repository: ImportBatchRepository,
        purchase_repository: PurchaseRepository,
        rejection_repository: ImportRejectionRepository | None = None,
    ) -> None:
        """서비스를 초기화합니다.

        Args:
            importer: 행 적재에 사용할 :class:`PurchaseImporter`.
            batch_repository: 배치 저장소.
            purchase_repository: 적재 결과 집계에 사용할 구매 저장소.
            rejection_repository: 적재되지 않은 행을 기록할 저장소.
                ``None`` 이면 기록을 남기지 않고 **기존과 동일하게 동작**합니다
                (하위 호환). 운영 조립(``app.py``)에서는 항상 넘깁니다.
        """
        self._importer = importer
        self._batch_repository = batch_repository
        self._purchase_repository = purchase_repository
        self._rejection_repository = rejection_repository

    def import_batch(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        file_name: str,
        period_start: date,
        period_end: date,
        file_hash: str | None = None,
    ) -> BatchImportResult:
        """행들을 새 배치로 적재하고, 같은 기간의 이전 배치를 대체합니다.

        Args:
            rows: 컬럼 매핑이 끝난 행 목록.
            file_name: 원본 파일명.
            period_start: 대상 기간 시작일. **호출자가 지정합니다**(자동 유추 없음).
            period_end: 대상 기간 종료일. **호출자가 지정합니다**.
            file_hash: 원본 파일 내용 해시(선택). 같은 파일 재업로드 감지에
                사용하며, 감지되어도 **적재를 막지 않고 경고만** 남깁니다.

        Returns:
            :class:`BatchImportResult`.

        Raises:
            ImportBatchValidationError: 파일명이 비었거나 기간이 뒤집힌 경우.
        """
        previous = self._batch_repository.find_active_by_period(period_start, period_end)
        duplicate = self._find_duplicate(file_hash)

        batch = self._batch_repository.insert(
            ImportBatch(
                file_name=file_name,
                file_hash=file_hash,
                period_start=period_start,
                period_end=period_end,
            )
        )
        assert batch.batch_id is not None  # insert 가 채번을 보장

        # ⚠️ Importer 에 넘긴 행을 그대로 붙잡아 둔다 — 실패한 행의 원본 값을
        #    되짚어 기록해야 하고, Iterable 은 한 번만 읽힐 수 있기 때문이다.
        row_list = list(rows)
        report = self._importer.import_rows(row_list, batch_id=batch.batch_id)

        # 적재되지 않은 행을 사유와 함께 남긴다. ⛔ 업무 판단이 아니라 기록이다.
        rejections = tuple(build_rejections(row_list, report, batch_id=batch.batch_id))
        if self._rejection_repository is not None and rejections:
            self._rejection_repository.record_many(rejections)

        stored = self._purchase_repository.find_by_batch(batch.batch_id)
        total_amount = sum((purchase.amount for purchase in stored), Decimal("0"))
        self._batch_repository.update_totals(batch.batch_id, len(stored), total_amount)

        superseded: ImportBatch | None = None
        if previous is not None and previous.batch_id is not None:
            # 적재가 끝난 뒤에만 이전 배치를 무효화한다.
            if self._batch_repository.supersede(previous.batch_id, batch.batch_id):
                superseded = self._batch_repository.find_by_id(previous.batch_id)

        saved = self._batch_repository.find_by_id(batch.batch_id)
        assert saved is not None  # 방금 저장했다

        return BatchImportResult(
            batch=saved,
            report=report,
            superseded_batch=superseded,
            duplicate_of=duplicate,
            rejections=rejections,
        )

    def find_active_batch(self, period_start: date, period_end: date) -> ImportBatch | None:
        """같은 대상 기간의 ACTIVE 배치를 조회합니다(읽기 전용).

        **교체 전 사용자 확인**(PM-005)을 위해 "이 기간에 이미 등록된 데이터가
        있는가" 를 묻는 용도입니다. 저장소를 그대로 호출하기만 하며, 아무것도
        바꾸지 않습니다.

        Args:
            period_start: 대상 기간 시작일.
            period_end: 대상 기간 종료일.

        Returns:
            해당 기간의 ACTIVE 배치. 없으면 ``None``.
        """
        return self._batch_repository.find_active_by_period(period_start, period_end)

    def find_conflicts(self, period_start: date, period_end: date) -> list[ImportBatch]:
        """같은 기간에 ACTIVE 배치가 2 개 이상 남아 있는지 점검합니다.

        대체 처리가 중간에 실패하면 발생할 수 있는 상태입니다. 정상이라면
        0 건 또는 1 건입니다.

        Args:
            period_start: 점검할 기간 시작일.
            period_end: 점검할 기간 종료일.

        Returns:
            ACTIVE 배치가 2 개 이상이면 그 목록, 아니면 빈 목록.
        """
        active = self._batch_repository.find_active_by_period_all(period_start, period_end)
        return active if len(active) > 1 else []

    def _find_duplicate(self, file_hash: str | None) -> ImportBatch | None:
        """같은 내용 해시를 가진 ACTIVE 배치를 찾습니다."""
        if file_hash is None:
            return None
        for batch in self._batch_repository.find_by_file_hash(file_hash):
            if batch.is_active:
                return batch
        return None
