"""
procurement.database.policy_company_source_repository

**정책별 기업정보 등록 여부**의 저장/조회를 담당하는 Repository.

.. warning::
    ⛔ 이 저장소가 답하는 질문은 하나입니다 — *"이 정책의 기업 목록을 받은 적이
    있는가?"* 받은 적이 없으면 그 정책은 **조회불가**이며, ⛔ 미해당이나 0원으로
    처리하지 않습니다(STEP 96 §8).

.. note::
    정책당 **한 행**입니다. 같은 정책을 다시 등록하면 건수와 시각만 갱신됩니다 —
    "언제 마지막으로 받았는가" 를 보기 위해서입니다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from procurement.database.base import BaseRepository
from procurement.models.policy_company_source import PolicyCompanySource

#: 정책별 기업정보 등록 기록.
#:
#: ``UNIQUE (policy_id)`` — 정책당 현재 등록 상태는 하나입니다.
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS policy_company_source (
    policy_company_source_id INTEGER PRIMARY KEY,
    policy_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    company_count INTEGER NOT NULL,
    certification_count INTEGER NOT NULL,
    source_label TEXT,
    registered_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE (policy_id),
    FOREIGN KEY (policy_id) REFERENCES policy (policy_id)
)
"""


def _to_db(value: datetime) -> str:
    """datetime 을 SQLite 저장용 ISO 문자열로 변환합니다."""
    return value.isoformat(sep=" ")


def _from_db(value: str) -> datetime:
    """SQLite 에서 읽은 ISO 문자열을 datetime 으로 변환합니다."""
    return datetime.fromisoformat(value)


class PolicyCompanySourceRepository(BaseRepository):
    """``policy_company_source`` 테이블에 대한 데이터 접근 계층."""

    table_name = "policy_company_source"

    def create_table(self) -> None:
        """등록 기록 테이블을 생성합니다 (없을 때만)."""
        with self.connection() as conn:
            conn.execute(CREATE_TABLE_SQL)

    def get(self, policy_id: int) -> PolicyCompanySource | None:
        """한 정책의 등록 기록을 조회합니다.

        Args:
            policy_id: 대상 정책 ID.

        Returns:
            :class:`PolicyCompanySource`. **등록된 적이 없으면 ``None``** 이며,
            그것이 곧 **조회불가**를 뜻합니다.
        """
        rows = self.execute("SELECT * FROM policy_company_source WHERE policy_id = ?", (policy_id,))
        return self._row_to_source(rows[0]) if rows else None

    def registered_policy_ids(self) -> set[int]:
        """기업정보를 받은 적이 있는 정책 ID 집합.

        Returns:
            등록된 정책 ID. 비어 있으면 **어느 정책도 판정할 수 없습니다.**
        """
        rows = self.execute("SELECT policy_id FROM policy_company_source")
        return {int(row["policy_id"]) for row in rows}

    def find_all(self) -> list[PolicyCompanySource]:
        """등록 기록 전체를 반환합니다."""
        rows = self.execute("SELECT * FROM policy_company_source ORDER BY policy_id")
        return [self._row_to_source(row) for row in rows]

    def record(
        self,
        policy_id: int,
        *,
        source: str,
        company_count: int,
        certification_count: int,
        source_label: str | None = None,
    ) -> PolicyCompanySource:
        """정책의 기업정보를 **받았다는 사실**을 기록합니다.

        같은 정책을 다시 등록하면 건수와 시각만 갱신됩니다(멱등).

        .. note::
            ``certification_count`` 가 0 이어도 기록합니다. 목록을 받았는데 우리
            거래처가 하나도 없을 수 있고, 그것은 "판단할 수 없다" 가 아니라
            **"전부 미해당"** 이기 때문입니다.

        Args:
            policy_id: 대상 정책 ID.
            source: ``FILE`` 또는 ``API``.
            company_count: 확인한 기업 수.
            certification_count: 새로 저장한 인증 수.
            source_label: 사용자가 알아볼 출처 표시(파일명 등).

        Returns:
            저장된 :class:`PolicyCompanySource`.
        """
        now = datetime.now()
        sql = (
            "INSERT INTO policy_company_source "
            "(policy_id, source, company_count, certification_count, source_label, "
            "registered_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (policy_id) DO UPDATE SET "
            "source = excluded.source, "
            "company_count = excluded.company_count, "
            "certification_count = excluded.certification_count, "
            "source_label = excluded.source_label, "
            "updated_at = excluded.updated_at"
        )
        params = (
            policy_id,
            source,
            company_count,
            certification_count,
            source_label,
            _to_db(now),
            _to_db(now),
        )
        with self.connection() as conn:
            conn.execute(sql, params)

        saved = self.get(policy_id)
        assert saved is not None  # 방금 저장했다
        return saved

    def delete(self, policy_id: int) -> bool:
        """등록 기록을 지웁니다 — 그 정책은 다시 **조회불가**가 됩니다.

        ⛔ 저장된 기업·인증 자체를 지우지 않습니다. 이 기록만 지웁니다.

        Args:
            policy_id: 대상 정책 ID.

        Returns:
            지운 기록이 있으면 ``True``.
        """
        deleted = self.execute_write(
            "DELETE FROM policy_company_source WHERE policy_id = ?", (policy_id,)
        )
        return deleted > 0

    @staticmethod
    def _row_to_source(row: sqlite3.Row) -> PolicyCompanySource:
        """조회 행을 :class:`PolicyCompanySource` 로 변환합니다."""
        return PolicyCompanySource(
            policy_company_source_id=row["policy_company_source_id"],
            policy_id=row["policy_id"],
            source=row["source"],
            company_count=row["company_count"],
            certification_count=row["certification_count"],
            source_label=row["source_label"],
            registered_at=_from_db(row["registered_at"]),
            updated_at=_from_db(row["updated_at"]),
        )
