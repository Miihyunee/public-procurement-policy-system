"""
procurement.database.import_rejection_repository

``import_rejection`` 테이블에 대한 데이터 접근 계층.

**원본에는 있었지만 DB-1 에 적재되지 않은 행**을 기록합니다.

.. warning::
    ⛔ **업무 판단을 저장하지 않습니다.** "제외 확정" 이 아니라 "이 행은 이런
    사유로 적재되지 않았다" 는 사실만 남깁니다. 실적 인정 여부는 고객 확인
    사항입니다(``CUSTOMER_DATA_QUESTIONS.md`` Q5-8).

.. note::
    ``purchase`` 테이블은 **건드리지 않습니다.** 적재되지 않은 행을 거기에
    넣으면 곧바로 계산 대상이 되어 버리기 때문입니다. 기존 스키마·기존 계산에
    영향이 없도록 신규 테이블만 추가합니다.

.. note::
    Foreign Key 제약은 걸지 않습니다 — ``import_batch`` · ``purchase`` 와 같은
    방식(논리 참조)을 유지합니다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import date, datetime
from decimal import Decimal

from procurement.database.base import BaseRepository
from procurement.models.import_rejection import (
    REJECTION_REASONS,
    ImportRejection,
)

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS import_rejection (
    rejection_id INTEGER PRIMARY KEY,
    batch_id INTEGER,
    row_number INTEGER NOT NULL,
    reason TEXT NOT NULL,
    message TEXT NOT NULL,
    business_no TEXT,
    company_name TEXT,
    description TEXT,
    budget_account TEXT,
    amount NUMERIC,
    resolution_date DATE,
    issue_date DATE,
    created_at DATETIME NOT NULL
)
"""

#: 배치별 조회가 대부분이다.
CREATE_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_import_rejection_batch "
    "ON import_rejection (batch_id, row_number)"
)


class ImportRejectionValidationError(ValueError):
    """기록 값이 올바르지 않을 때 발생합니다."""


def _to_db(value: datetime) -> str:
    return value.isoformat(sep=" ")


def _from_db(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _optional_date(value: object) -> date | None:
    if value is None or str(value).strip() == "":
        return None
    return date.fromisoformat(str(value))


def _optional_amount(value: object) -> Decimal | None:
    if value is None or str(value).strip() == "":
        return None
    return Decimal(str(value))


class ImportRejectionRepository(BaseRepository):
    """import_rejection 테이블에 대한 데이터 접근 계층."""

    table_name = "import_rejection"

    def create_table(self) -> None:
        """테이블과 인덱스를 생성합니다 (없을 때만)."""
        with self.connection() as conn:
            conn.execute(CREATE_TABLE_SQL)
            conn.execute(CREATE_INDEX_SQL)

    def record_many(self, rejections: Iterable[ImportRejection]) -> int:
        """적재되지 않은 행들을 한 번에 기록합니다.

        Args:
            rejections: 기록할 :class:`ImportRejection` 목록.

        Returns:
            기록한 행 수.

        Raises:
            ImportRejectionValidationError: 사유 코드가 허용값이 아닌 경우.
        """
        items = list(rejections)
        for item in items:
            if item.reason not in REJECTION_REASONS:
                raise ImportRejectionValidationError(f"허용되지 않은 사유입니다: {item.reason}")
        if not items:
            return 0

        now = _to_db(datetime.now())
        with self.connection() as conn:
            conn.executemany(
                "INSERT INTO import_rejection ("
                " batch_id, row_number, reason, message, business_no, company_name,"
                " description, budget_account, amount, resolution_date, issue_date, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        item.batch_id,
                        item.row_number,
                        item.reason,
                        item.message,
                        item.business_no,
                        item.company_name,
                        item.description,
                        item.budget_account,
                        None if item.amount is None else str(item.amount),
                        None if item.resolution_date is None else item.resolution_date.isoformat(),
                        None if item.issue_date is None else item.issue_date.isoformat(),
                        now,
                    )
                    for item in items
                ],
            )
        return len(items)

    def find_by_batch(self, batch_id: int | None) -> list[ImportRejection]:
        """배치 하나의 미적재 행을 원본 행 번호 순서로 반환합니다.

        Args:
            batch_id: 대상 배치 ID. ``None`` 이면 배치 없이 적재한 기록을
                찾습니다(하위 호환 경로).
        """
        if batch_id is None:
            rows = self.execute(
                "SELECT * FROM import_rejection WHERE batch_id IS NULL ORDER BY row_number"
            )
        else:
            rows = self.execute(
                "SELECT * FROM import_rejection WHERE batch_id = ? ORDER BY row_number",
                (batch_id,),
            )
        return [self._to_model(row) for row in rows]

    def find_all(self) -> list[ImportRejection]:
        """모든 기록을 반환합니다(배치 → 행 번호 순)."""
        rows = self.execute(
            "SELECT * FROM import_rejection ORDER BY batch_id IS NULL, batch_id, row_number"
        )
        return [self._to_model(row) for row in rows]

    def count_by_batch(self, batch_id: int | None) -> int:
        """배치 하나의 미적재 행 수."""
        if batch_id is None:
            rows = self.execute("SELECT COUNT(*) AS n FROM import_rejection WHERE batch_id IS NULL")
        else:
            rows = self.execute(
                "SELECT COUNT(*) AS n FROM import_rejection WHERE batch_id = ?", (batch_id,)
            )
        return int(rows[0]["n"])

    def count_by_reason(self, batch_id: int | None = None) -> dict[str, int]:
        """사유별 미적재 행 수.

        Args:
            batch_id: 대상 배치. ``None`` 이면 **전체**를 셉니다(배치 없는
                기록만 세는 :meth:`count_by_batch` 와 다릅니다).
        """
        if batch_id is None:
            rows = self.execute(
                "SELECT reason, COUNT(*) AS n FROM import_rejection GROUP BY reason"
            )
        else:
            rows = self.execute(
                "SELECT reason, COUNT(*) AS n FROM import_rejection "
                "WHERE batch_id = ? GROUP BY reason",
                (batch_id,),
            )
        return {str(row["reason"]): int(row["n"]) for row in rows}

    @staticmethod
    def _to_model(row: sqlite3.Row) -> ImportRejection:
        return ImportRejection(
            rejection_id=row["rejection_id"],
            batch_id=row["batch_id"],
            row_number=row["row_number"],
            reason=row["reason"],
            message=row["message"],
            business_no=row["business_no"],
            company_name=row["company_name"],
            description=row["description"],
            budget_account=row["budget_account"],
            amount=_optional_amount(row["amount"]),
            resolution_date=_optional_date(row["resolution_date"]),
            issue_date=_optional_date(row["issue_date"]),
            created_at=_from_db(row["created_at"]),
        )
