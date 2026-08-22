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
from datetime import date, datetime, timedelta
from decimal import Decimal

from procurement.core.description_key import normalize_description
from procurement.database.import_batch_repository import ImportBatchRepository
from procurement.database.import_rejection_repository import ImportRejectionRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.importers.rejection_query import ANY, RejectionQuery
from procurement.models.import_batch import ImportBatch
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


@dataclass(frozen=True, kw_only=True)
class BatchHistoryEntry:
    """업로드 한 번의 기록.

    ⛔ 새 상태값을 만들지 않습니다 — ``status`` 는 기존 배치 lifecycle 의
    ``ACTIVE`` / ``SUPERSEDED`` 그대로입니다.

    Attributes:
        batch: 배치 원본(기간 · 업로드 시각 · 상태 · 파일명).
        stored: 이 배치로 적재된 행 수.
        rejected: 이 배치에서 적재되지 않아 기록된 행 수.
        reasons: 사유별 건수.
    """

    batch: ImportBatch
    stored: int = 0
    rejected: int = 0
    reasons: dict[str, int] | None = None

    @property
    def batch_id(self) -> int | None:
        """배치 ID."""
        return self.batch.batch_id

    @property
    def source_rows(self) -> int | None:
        """원본 행 수. **기록된 값**이며, 모르면 ``None`` 입니다.

        ⚠️ 적재 + 미적재로 되계산하지 않습니다 — 그러면 :attr:`unexplained` 가
        늘 0 이 되어 아무것도 검증하지 못합니다.
        """
        return self.batch.source_row_count

    @property
    def unexplained(self) -> int | None:
        """설명되지 않은 행 수. 원본 행 수를 모르면 ``None``.

        ⚠️ 0 이 아니면 **어딘가에서 행이 사라진 것**입니다.
        """
        if self.batch.source_row_count is None:
            return None
        return self.batch.source_row_count - self.stored - self.rejected

    @property
    def is_current(self) -> bool:
        """지금 검토·계산에 쓰이는 배치인가(기존 lifecycle 기준)."""
        return self.batch.is_active


@dataclass(frozen=True, kw_only=True)
class PeriodOption:
    """검토·조회에 쓸 수 있는 **기간 하나**.

    ⚠️ 기간은 화면이 만들지 않습니다. 업로드된 배치의 ``period_start`` /
    ``period_end`` 를 그대로 씁니다.

    ⛔ **대체된 배치의 기간은 여기 오지 않습니다** — 운영 조회는 현재 배치만
    봅니다. 과거 배치는 업로드 이력에서 봅니다.

    Attributes:
        period_start: 대상 기간 시작일.
        period_end: 대상 기간 종료일.
        batch_id: 이 기간의 **현재 배치** ID.
        stored: 그 배치로 적재된 행 수.
        rejected: 그 배치의 미적재 행 수.
        current_batch_count: 이 기간에 현재 상태인 배치 수.

            정상이면 1 입니다. 2 이상이면 대체 처리가 중간에 멈춘 것이므로
            화면이 그 사실을 드러낼 수 있게 함께 내려보냅니다
            (:meth:`~procurement.database.import_batch_repository.ImportBatchRepository.find_active_by_period_all`
            의 점검 목적과 같습니다). ⛔ 여기서 고치지 않습니다.
    """

    period_start: date
    period_end: date
    batch_id: int
    stored: int = 0
    rejected: int = 0
    current_batch_count: int = 1

    @property
    def label(self) -> str:
        """사람이 읽는 기간 이름.

        한 달에 정확히 맞아떨어지면 ``2026-03``, 아니면 ``시작 ~ 끝`` 입니다.
        ⛔ 달을 만들어 내는 것이 아니라 **배치의 기간을 읽어 적는 것**입니다.
        """
        if self.period_start.day == 1 and self.period_end == _month_end(self.period_start):
            return f"{self.period_start.year}-{self.period_start.month:02d}"
        return f"{self.period_start.isoformat()} ~ {self.period_end.isoformat()}"


@dataclass(frozen=True, kw_only=True)
class RejectionPage:
    """조건에 맞는 미적재 행 한 페이지.

    Attributes:
        items: 이 페이지의 행.
        total: 조건에 맞는 전체 건수.
        page: 현재 페이지 번호.
        page_size: 한 페이지 건수.
    """

    items: list[ImportRejection]
    total: int = 0
    page: int = 1
    page_size: int = 0

    @property
    def total_pages(self) -> int:
        """전체 쪽 수. 결과가 없으면 1쪽(빈 화면)."""
        if self.page_size <= 0:
            return 1
        return max(1, -(-self.total // self.page_size))

    @property
    def has_previous(self) -> bool:
        """이전 쪽이 있는가."""
        return self.page > 1

    @property
    def has_next(self) -> bool:
        """다음 쪽이 있는가."""
        return self.page < self.total_pages


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

    def periods(self) -> list[PeriodOption]:
        """운영 조회에 쓸 수 있는 기간 목록을 **최근 순**으로 반환합니다.

        ⛔ **현재 배치가 있는 기간만** 나옵니다. 같은 기간을 다시 올려 이전
        배치가 대체되었다면 기간은 하나만 나오고, 그 기간의 ``batch_id`` 는
        **새 배치**를 가리킵니다.

        Returns:
            :class:`PeriodOption` 목록. 업로드가 없으면 빈 목록.
        """
        rejections = self._rejection_repository.find_current()
        rejected_by_batch: dict[int | None, int] = {}
        for item in rejections:
            rejected_by_batch[item.batch_id] = rejected_by_batch.get(item.batch_id, 0) + 1

        by_period: dict[tuple[date, date], list[ImportBatch]] = {}
        for batch in self._batch_repository.find_all():
            if not batch.is_active or batch.batch_id is None:
                continue
            by_period.setdefault((batch.period_start, batch.period_end), []).append(batch)

        options: list[PeriodOption] = []
        for (start, end), batches in by_period.items():
            # 정상이라면 1건이다. 여러 건이면 가장 최근 것을 쓰고 건수를 알린다.
            latest = max(batches, key=lambda item: item.batch_id or 0)
            assert latest.batch_id is not None
            options.append(
                PeriodOption(
                    period_start=start,
                    period_end=end,
                    batch_id=latest.batch_id,
                    stored=latest.row_count,
                    rejected=rejected_by_batch.get(latest.batch_id, 0),
                    current_batch_count=len(batches),
                )
            )
        options.sort(key=lambda option: (option.period_start, option.period_end), reverse=True)
        return options

    def history(
        self, *, period_start: date | None = None, period_end: date | None = None
    ) -> list[BatchHistoryEntry]:
        """업로드 이력을 **최근 순**으로 반환합니다.

        ⛔ 대체된 배치도 함께 보여 줍니다 — 무엇이 무엇으로 바뀌었는지
        담당자가 알 수 있어야 하기 때문입니다.

        ⚠️ 적재 행 수는 배치에 기록된 ``row_count`` 를 씁니다. 대체된 배치의
        구매 행은 현재 조회에서 빠지므로, 지금 다시 세면 0 이 나옵니다.

        Args:
            period_start: 이 기간의 업로드만. ``None`` 이면 전체.
            period_end: 〃. ``period_start`` 와 **함께** 주어야 합니다.
        """
        batches = self._batch_repository.find_all()
        if period_start is not None and period_end is not None:
            batches = [
                batch
                for batch in batches
                if batch.period_start == period_start and batch.period_end == period_end
            ]
        rejections = self._rejection_repository.find_all()

        by_batch: dict[int | None, list[ImportRejection]] = {}
        for item in rejections:
            by_batch.setdefault(item.batch_id, []).append(item)

        entries = [
            BatchHistoryEntry(
                batch=batch,
                stored=batch.row_count,
                rejected=len(by_batch.get(batch.batch_id, [])),
                reasons=_count_reasons(by_batch.get(batch.batch_id, [])),
            )
            for batch in batches
        ]
        entries.sort(key=_history_order, reverse=True)
        return entries

    def batch(self, batch_id: int) -> BatchHistoryEntry | None:
        """업로드 한 건의 상세를 반환합니다. 없으면 ``None``."""
        found = self._batch_repository.find_by_id(batch_id)
        if found is None:
            return None
        rejections = self._rejection_repository.find_by_batch(batch_id)
        return BatchHistoryEntry(
            batch=found,
            stored=found.row_count,
            rejected=len(rejections),
            reasons=_count_reasons(rejections),
        )

    def search_rejections(self, query: RejectionQuery) -> RejectionPage:
        """조건에 맞는 미적재 행 한 페이지를 반환합니다.

        ⛔ **조회일 뿐입니다.** 걸러 본다고 그 행의 처리 방식이 정해지지
        않습니다(Q5-8).

        ⚠️ 대상은 :meth:`rejections` 와 같이 **지금 유효한** 기록입니다 —
        대체된 배치의 기록은 들어오지 않습니다.
        """
        items = self._rejection_repository.find_current()
        items = _sorted([item for item in items if _keeps(item, query)], query)
        start = query.offset
        return RejectionPage(
            items=items[start : start + query.page_size],
            total=len(items),
            page=query.page,
            page_size=query.page_size,
        )

    def search_rejections_all(self, query: RejectionQuery) -> list[ImportRejection]:
        """조건에 맞는 미적재 행 **전부**를 반환합니다(페이지 무시).

        CSV 내보내기처럼 "지금 보고 있는 조건의 전체" 가 필요한 경우에만
        씁니다. 화면 목록은 :meth:`search_rejections` 를 써서 한 페이지만
        가져갑니다.

        ⚠️ **거르고 줄 세우는 규칙은 화면 목록과 같은 것을 씁니다** — 두 곳에
        따로 두면 화면과 CSV 의 결과가 갈라집니다.
        """
        items = self._rejection_repository.find_current()
        return _sorted([item for item in items if _keeps(item, query)], query)

    def rejections(self, *, limit: int | None = None) -> list[ImportRejection]:
        """미적재 행 목록을 반환합니다.

        Args:
            limit: 최대 건수. ``None`` 이면 전부. ⚠️ 잘라서 보여줄 때는 화면이
                **몇 건을 잘랐는지** 함께 알려야 합니다 — 여기서 조용히 줄이면
                "다 봤다" 는 오해가 다시 생깁니다.
        """
        items = self._rejection_repository.find_current()
        return items if limit is None else items[:limit]


def _month_end(start: date) -> date:
    """그 달의 마지막 날."""
    if start.month == 12:
        return date(start.year, 12, 31)
    return date(start.year, start.month + 1, 1) - timedelta(days=1)


def _history_order(entry: BatchHistoryEntry) -> tuple[datetime, int]:
    """최근 업로드가 먼저. 시각이 같으면 배치 ID 순."""
    uploaded = entry.batch.uploaded_at or datetime.min
    return uploaded, entry.batch_id or 0


def _keeps(item: ImportRejection, query: RejectionQuery) -> bool:
    """조건에 맞는 행인가."""
    if query.batch_id is not None and item.batch_id != query.batch_id:
        return False
    if query.reason != ANY and item.reason != query.reason:
        return False
    if not query.search:
        return True
    needle = normalize_description(query.search)
    haystacks = (
        normalize_description(item.description),
        normalize_description(item.company_name),
        str(item.row_number),
        normalize_description(item.business_no),
    )
    return any(needle in value for value in haystacks)


def _sorted(items: list[ImportRejection], query: RejectionQuery) -> list[ImportRejection]:
    """정렬합니다. **값이 없는 행은 오름차순·내림차순 모두에서 마지막**입니다.

    ⚠️ ``reverse=True`` 하나로 처리하면 "값 없음" 표시까지 뒤집혀, 내림차순일
    때 빈 값이 맨 앞으로 올라옵니다 — 검토 목록에서 실제로 겪은 문제라
    (``REVIEW_INTERFACE_DESIGN.md`` §9.1) 여기서도 있는 것과 없는 것을 나눠
    정렬합니다.
    """
    present = [item for item in items if _value_of(item, query.sort) is not None]
    missing = [item for item in items if _value_of(item, query.sort) is None]
    present.sort(
        key=lambda item: _sort_key(item, query.sort),
        reverse=query.descending,
    )
    # 값이 없는 행끼리는 배치 · 원본 행 번호 순으로 — 순서가 흔들리지 않도록.
    missing.sort(key=lambda item: (item.batch_id or 0, item.row_number))
    return present + missing


def _value_of(item: ImportRejection, key: str) -> object | None:
    """정렬 기준 값. 없으면 ``None``."""
    if key == "amount":
        return item.amount
    if key == "description":
        return item.description
    if key == "company_name":
        return item.company_name
    if key == "reason":
        return item.reason
    return item.row_number


def _sort_key(item: ImportRejection, key: str) -> tuple[Decimal, str]:
    """값이 있는 행의 정렬 키.

    숫자 칸과 글자 칸을 **한 가지 모양**(숫자, 글자)으로 맞춰 돌려줍니다 —
    정렬 기준이 무엇이든 같은 방식으로 비교되도록.
    """
    if key == "amount":
        return (item.amount if item.amount is not None else Decimal(0), "")
    if key == "description":
        return (Decimal(0), normalize_description(item.description))
    if key == "company_name":
        return (Decimal(0), normalize_description(item.company_name))
    if key == "reason":
        return (Decimal(0), item.reason)
    # 원본 행 번호는 **파일마다 1부터** 다시 시작한다. 여러 달을 올려 두면 번호만
    # 으로는 순서가 정해지지 않으므로 배치를 앞세운다 — CSV 와 같은 순서가 되어
    # 두 결과를 나란히 대조할 수 있다.
    return (Decimal((item.batch_id or 0) * 1_000_000 + item.row_number), "")
