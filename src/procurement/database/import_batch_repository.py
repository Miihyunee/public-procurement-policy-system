"""
procurement.database.import_batch_repository

``import_batch`` 테이블에 대한 데이터 접근 계층입니다.

배치는 **한 번의 업로드 단위**이며, 같은 기간을 다시 올리면 이전 배치를
``SUPERSEDED`` 로 표시해 계산에서 제외합니다(**D-25 확정 — 대체**).

.. note::
    **행을 물리적으로 삭제하지 않습니다.** 대체된 배치와 그 배치로 들어온
    ``purchase`` 행은 그대로 남으며, 상태로만 구분합니다. 무엇이 언제 대체되었는지
    추적할 수 있어야 하기 때문입니다.

.. note::
    Foreign Key 제약은 걸지 않습니다. ``purchase.company_id`` 등 기존 설계와
    같은 방식(논리 참조)을 유지합니다.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from decimal import Decimal

from procurement.database.base import BaseRepository
from procurement.models.import_batch import (
    ALLOWED_STATUSES,
    STATUS_ACTIVE,
    STATUS_SUPERSEDED,
    ImportBatch,
)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS import_batch (
    batch_id INTEGER PRIMARY KEY,
    file_name TEXT NOT NULL,
    file_hash TEXT,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    uploaded_at DATETIME NOT NULL,
    row_count INTEGER NOT NULL,
    total_amount NUMERIC NOT NULL,
    status TEXT NOT NULL,
    superseded_by INTEGER,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""

#: 같은 기간의 ACTIVE 배치를 찾는 조회가 잦으므로 인덱스를 둔다.
CREATE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_import_batch_period "
    "ON import_batch (period_start, period_end, status)"
)


class ImportBatchValidationError(ValueError):
    """배치 저장 시 필수값 검증에 실패하면 발생합니다."""


def _to_db(value: datetime) -> str:
    """datetime 을 SQLite 저장용 ISO 문자열로 변환합니다."""
    return value.isoformat(sep=" ")


def _from_db(value: str) -> datetime:
    """SQLite 에서 읽은 ISO 문자열을 datetime 으로 변환합니다."""
    return datetime.fromisoformat(value)


def _to_db_date(value: date) -> str:
    """date 를 SQLite 저장용 ISO 문자열(YYYY-MM-DD)로 변환합니다."""
    return value.isoformat()


def _from_db_date(value: str) -> date:
    """SQLite 에서 읽은 ISO 문자열을 date 로 변환합니다."""
    return date.fromisoformat(value)


def _to_db_amount(value: Decimal) -> str:
    """금액을 문자열로 저장합니다(정밀도 보존 — 기존 purchase 와 동일 규약)."""
    return str(value)


def _from_db_amount(value: object) -> Decimal:
    """SQLite 에서 읽은 금액 값을 Decimal 로 변환합니다."""
    return Decimal(str(value))


class ImportBatchRepository(BaseRepository):
    """import_batch 테이블에 대한 데이터 접근 계층."""

    table_name = "import_batch"

    def create_table(self) -> None:
        """import_batch 테이블과 인덱스를 생성합니다 (없을 때만).

        ``CREATE TABLE IF NOT EXISTS`` 를 사용하므로 반복 호출해도 안전합니다.
        """
        with self.connection() as conn:
            conn.execute(CREATE_TABLE_SQL)
            conn.execute(CREATE_INDEX_SQL)

    def insert(self, batch: ImportBatch) -> ImportBatch:
        """배치를 저장하고 채번된 ID 와 타임스탬프를 반영해 반환합니다.

        Args:
            batch: 저장할 :class:`ImportBatch`. ``batch_id`` 는 무시되고 자동
                채번됩니다.

        Returns:
            ``batch_id`` / ``uploaded_at`` / ``created_at`` / ``updated_at`` 가
            채워진 새 :class:`ImportBatch`.

        Raises:
            ImportBatchValidationError: 파일명이 비어 있거나, 기간이 뒤집혔거나,
                허용되지 않은 상태값인 경우.
        """
        self._validate(batch)

        now = datetime.now()
        uploaded_at = batch.uploaded_at or now
        created_at = batch.created_at or now
        updated_at = batch.updated_at or now

        sql = (
            "INSERT INTO import_batch "
            "(file_name, file_hash, period_start, period_end, uploaded_at, "
            "row_count, total_amount, status, superseded_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            batch.file_name,
            batch.file_hash,
            _to_db_date(batch.period_start),
            _to_db_date(batch.period_end),
            _to_db(uploaded_at),
            batch.row_count,
            _to_db_amount(batch.total_amount),
            batch.status,
            batch.superseded_by,
            _to_db(created_at),
            _to_db(updated_at),
        )

        with self.connection() as conn:
            cursor = conn.execute(sql, params)
            new_id = cursor.lastrowid

        return ImportBatch(
            batch_id=new_id,
            file_name=batch.file_name,
            file_hash=batch.file_hash,
            period_start=batch.period_start,
            period_end=batch.period_end,
            uploaded_at=uploaded_at,
            row_count=batch.row_count,
            total_amount=batch.total_amount,
            status=batch.status,
            superseded_by=batch.superseded_by,
            created_at=created_at,
            updated_at=updated_at,
        )

    def find_by_id(self, batch_id: int) -> ImportBatch | None:
        """batch_id 로 배치를 조회합니다.

        Args:
            batch_id: 조회할 배치 ID.

        Returns:
            일치하는 :class:`ImportBatch`, 없으면 ``None``.
        """
        rows = self.execute("SELECT * FROM import_batch WHERE batch_id = ?", (batch_id,))
        return self._row_to_batch(rows[0]) if rows else None

    def find_active_by_period(self, period_start: date, period_end: date) -> ImportBatch | None:
        """같은 대상 기간의 ACTIVE 배치를 조회합니다.

        기간이 **정확히 일치**하는 배치만 찾습니다. 부분 겹침은 대체 대상으로
        보지 않습니다 — 무엇을 대체할지 애매해지기 때문입니다.

        Args:
            period_start: 대상 기간 시작일.
            period_end: 대상 기간 종료일.

        Returns:
            해당 기간의 ACTIVE 배치. 없으면 ``None``. 여러 건이면 가장 최근 것.
        """
        rows = self.execute(
            "SELECT * FROM import_batch "
            "WHERE period_start = ? AND period_end = ? AND status = ? "
            "ORDER BY batch_id DESC",
            (_to_db_date(period_start), _to_db_date(period_end), STATUS_ACTIVE),
        )
        return self._row_to_batch(rows[0]) if rows else None

    def find_active_by_period_all(self, period_start: date, period_end: date) -> list[ImportBatch]:
        """같은 대상 기간의 ACTIVE 배치를 **모두** 조회합니다.

        정상 상태라면 0 건 또는 1 건입니다. 2 건 이상이면 대체 처리가 중간에
        실패한 것이므로, 점검용으로 사용합니다.

        Args:
            period_start: 대상 기간 시작일.
            period_end: 대상 기간 종료일.

        Returns:
            :class:`ImportBatch` 목록.
        """
        rows = self.execute(
            "SELECT * FROM import_batch "
            "WHERE period_start = ? AND period_end = ? AND status = ? "
            "ORDER BY batch_id",
            (_to_db_date(period_start), _to_db_date(period_end), STATUS_ACTIVE),
        )
        return [self._row_to_batch(row) for row in rows]

    def find_by_file_hash(self, file_hash: str) -> list[ImportBatch]:
        """같은 내용 해시를 가진 배치를 조회합니다.

        같은 파일을 그대로 다시 올린 경우를 감지하는 데 사용합니다.

        Args:
            file_hash: 원본 파일 내용 해시.

        Returns:
            :class:`ImportBatch` 목록. 없으면 빈 목록.
        """
        rows = self.execute(
            "SELECT * FROM import_batch WHERE file_hash = ? ORDER BY batch_id",
            (file_hash,),
        )
        return [self._row_to_batch(row) for row in rows]

    def find_all(self) -> list[ImportBatch]:
        """전체 배치 목록을 조회합니다(대체된 배치 포함).

        Returns:
            :class:`ImportBatch` 목록. 없으면 빈 목록.
        """
        rows = self.execute("SELECT * FROM import_batch ORDER BY batch_id")
        return [self._row_to_batch(row) for row in rows]

    def update_totals(self, batch_id: int, row_count: int, total_amount: Decimal) -> bool:
        """적재 결과(행 수·금액 합계)를 배치에 기록합니다.

        Args:
            batch_id: 대상 배치 ID.
            row_count: 실제 적재된 행 수.
            total_amount: 적재된 금액 합계.

        Returns:
            갱신된 행이 있으면 ``True``, 해당 배치가 없으면 ``False``.
        """
        affected = self.execute_write(
            "UPDATE import_batch SET row_count = ?, total_amount = ?, updated_at = ? "
            "WHERE batch_id = ?",
            (row_count, _to_db_amount(total_amount), _to_db(datetime.now()), batch_id),
        )
        return affected > 0

    def supersede(self, batch_id: int, superseded_by: int) -> bool:
        """배치를 대체 처리합니다(계산에서 제외).

        행을 삭제하지 않고 상태만 바꿉니다.

        Args:
            batch_id: 대체될 배치 ID.
            superseded_by: 대체한 새 배치 ID.

        Returns:
            갱신된 행이 있으면 ``True``, 해당 배치가 없거나 이미 대체된
            상태이면 ``False``.
        """
        affected = self.execute_write(
            "UPDATE import_batch SET status = ?, superseded_by = ?, updated_at = ? "
            "WHERE batch_id = ? AND status = ?",
            (
                STATUS_SUPERSEDED,
                superseded_by,
                _to_db(datetime.now()),
                batch_id,
                STATUS_ACTIVE,
            ),
        )
        return affected > 0

    def count(self) -> int:
        """등록된 배치 수를 반환합니다.

        Returns:
            import_batch 테이블의 전체 행 수.
        """
        rows = self.execute("SELECT COUNT(*) AS cnt FROM import_batch")
        return int(rows[0]["cnt"])

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------
    @staticmethod
    def _validate(batch: ImportBatch) -> None:
        """저장 전에 필수값을 검증합니다."""
        if not batch.file_name or not batch.file_name.strip():
            raise ImportBatchValidationError("file_name 은 필수입니다.")
        if batch.period_start > batch.period_end:
            raise ImportBatchValidationError(
                f"대상 기간 시작일이 종료일보다 늦습니다: {batch.period_start} > {batch.period_end}"
            )
        if batch.status not in ALLOWED_STATUSES:
            allowed = ", ".join(sorted(ALLOWED_STATUSES))
            raise ImportBatchValidationError(
                f"status 는 {allowed} 중 하나여야 합니다: {batch.status!r}"
            )

    @staticmethod
    def _row_to_batch(row: sqlite3.Row) -> ImportBatch:
        """DB 행을 :class:`ImportBatch` 로 변환합니다."""
        return ImportBatch(
            batch_id=row["batch_id"],
            file_name=row["file_name"],
            file_hash=row["file_hash"],
            period_start=_from_db_date(row["period_start"]),
            period_end=_from_db_date(row["period_end"]),
            uploaded_at=_from_db(row["uploaded_at"]),
            row_count=int(row["row_count"]),
            total_amount=_from_db_amount(row["total_amount"]),
            status=row["status"],
            superseded_by=row["superseded_by"],
            created_at=_from_db(row["created_at"]),
            updated_at=_from_db(row["updated_at"]),
        )
