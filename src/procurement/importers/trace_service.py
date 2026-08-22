"""
procurement.importers.trace_service

**원본 → 적재 → 미적재** 를 맞대어 보는 조회 전용 서비스.

담당자가 답할 수 있어야 하는 질문 하나를 위해 존재합니다.

    "원본 파일에 있던 행은 지금 어디에 있는가?"

.. warning::
    ⛔ **업무 판단을 하지 않습니다.** 어떤 행을 실적으로 인정할지, 미적재 행을
    제외할지는 고객 확인 사항입니다(``CUSTOMER_DATA_QUESTIONS.md`` Q5-8).
    이 서비스는 숫자를 세어 보여줄 뿐입니다.

.. note::
    쓰기 경로가 없습니다. 조회만 합니다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from procurement.database.import_batch_repository import ImportBatchRepository
from procurement.database.import_rejection_repository import ImportRejectionRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models.import_rejection import ImportRejection, ImportTrace


@dataclass(frozen=True, kw_only=True)
class ImportTraceOverview:
    """전체 대조 결과.

    Attributes:
        stored: 현재 DB-1 에 있는 구매 행 수 — **검토 화면에 보이는 수**.
        rejected: 적재되지 않아 기록만 남은 행 수 — 화면에 보이지 않는 수.
        reasons: 사유별 미적재 행 수.
        batches: 배치별 대조표(최근 업로드 순).
    """

    stored: int = 0
    rejected: int = 0
    reasons: dict[str, int] | None = None
    batches: tuple[ImportTrace, ...] = ()

    @property
    def source_rows(self) -> int:
        """설명 가능한 원본 행 수 = 적재 + 미적재."""
        return self.stored + self.rejected

    @property
    def all_visible(self) -> bool:
        """원본 행이 모두 검토 화면에 보이는가."""
        return self.rejected == 0


def _count_reasons(items: Iterable[ImportRejection]) -> dict[str, int]:
    """사유별 건수를 셉니다."""
    counts: dict[str, int] = {}
    for item in items:
        counts[item.reason] = counts.get(item.reason, 0) + 1
    return counts


class ImportTraceService:
    """적재 추적 조회 서비스."""

    def __init__(
        self,
        purchase_repository: PurchaseRepository,
        batch_repository: ImportBatchRepository,
        rejection_repository: ImportRejectionRepository,
    ) -> None:
        """서비스를 초기화합니다.

        Args:
            purchase_repository: 적재된 행을 세는 데 사용합니다(⛔ 읽기 전용).
            batch_repository: 배치별 대조에 사용합니다.
            rejection_repository: 미적재 기록 저장소.
        """
        self._purchase_repository = purchase_repository
        self._batch_repository = batch_repository
        self._rejection_repository = rejection_repository

    def overview(self) -> ImportTraceOverview:
        """전체 대조 결과를 만듭니다.

        Returns:
            :class:`ImportTraceOverview`.
        """
        # ⚠️ 두 쪽 다 **대체된 배치를 뺀** 같은 기준으로 읽는다. 한쪽만 전체를
        #    읽으면 같은 기간을 다시 올렸을 때 미적재 건수만 계속 불어난다.
        stored_rows = self._purchase_repository.find_for_calculation(None)
        rejections = self._rejection_repository.find_current()

        stored_by_batch: dict[int | None, int] = {}
        for purchase in stored_rows:
            stored_by_batch[purchase.batch_id] = stored_by_batch.get(purchase.batch_id, 0) + 1

        rejected_by_batch: dict[int | None, int] = {}
        reasons: dict[str, int] = {}
        for item in rejections:
            rejected_by_batch[item.batch_id] = rejected_by_batch.get(item.batch_id, 0) + 1
            reasons[item.reason] = reasons.get(item.reason, 0) + 1

        batches: list[ImportTrace] = []
        for batch_id in sorted(
            set(stored_by_batch) | set(rejected_by_batch),
            key=lambda value: (value is None, value),
        ):
            batch = None if batch_id is None else self._batch_repository.find_by_id(batch_id)
            stored = stored_by_batch.get(batch_id, 0)
            rejected = rejected_by_batch.get(batch_id, 0)
            batches.append(
                ImportTrace(
                    # 배치 단위 원본 행 수는 다시 세지 않고 두 값의 합으로 둔다
                    # — 업로드 시점의 행 수를 따로 저장하고 있지 않기 때문이다.
                    source_rows=stored + rejected,
                    batch_id=batch_id,
                    file_name=batch.file_name if batch is not None else "(배치 없음)",
                    stored=stored,
                    rejected=rejected,
                    reasons=_count_reasons(
                        item for item in rejections if item.batch_id == batch_id
                    ),
                )
            )

        return ImportTraceOverview(
            stored=len(stored_rows),
            rejected=len(rejections),
            reasons=reasons,
            batches=tuple(batches),
        )

    def rejections(self, *, limit: int | None = None) -> list[ImportRejection]:
        """미적재 행 목록을 반환합니다.

        Args:
            limit: 최대 건수. ``None`` 이면 전부. ⚠️ 잘라서 보여줄 때는 화면이
                **몇 건을 잘랐는지** 함께 알려야 합니다 — 여기서 조용히 줄이면
                "다 봤다" 는 오해가 다시 생깁니다.
        """
        items = self._rejection_repository.find_current()
        return items if limit is None else items[:limit]
