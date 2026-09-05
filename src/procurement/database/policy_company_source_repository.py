"""
procurement.database.policy_company_source_repository

**정책별 기업정보 등록 여부**의 저장/조회를 담당하는 Repository.

.. warning::
    ⛔ 이 저장소가 답하는 질문은 하나입니다 — *"이 정책의 기업 목록을 받은 적이
    있는가?"* 받은 적이 없으면 그 정책은 **조회불가**이며, ⛔ 미해당이나 0원으로
    처리하지 않습니다(STEP 96 §8).

.. note::
    정책당 **여러 행**입니다 — 등록할 때마다 버전이 하나씩 늘고, 그중 하나만
    ``is_active`` 입니다.

    🟢 2026-09-05 고객 확정: *"기존 인증기업 데이터는 이력으로 보관한다. 새
    인증기업 파일이 올라오면 그 파일을 그 정책의 최신 데이터로 선택하고, 현재
    실적 계산은 최신 버전을 기준으로 한다."*

    ⛔ 예전 버전을 지우지 않습니다. **보관 범위와 계산 범위를 나눌 뿐**입니다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from procurement.database.base import BaseRepository
from procurement.models.policy_company_source import PolicyCompanySource

#: 정책별 기업정보 등록 기록 — **버전마다 한 행**.
#:
#: ``UNIQUE (policy_id, version)`` — 같은 정책에 같은 버전 번호가 둘일 수 없습니다.
#: ⛔ ``UNIQUE (policy_id)`` 는 뗐습니다. 정책당 한 행이면 이력이 남지 않습니다.
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS policy_company_source (
    policy_company_source_id INTEGER PRIMARY KEY,
    policy_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    company_count INTEGER NOT NULL,
    certification_count INTEGER NOT NULL,
    source_label TEXT,
    version INTEGER NOT NULL DEFAULT 1,
    file_checksum TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    registered_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE (policy_id, version),
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
        """등록 기록 테이블을 생성합니다 (없을 때만).

        예전 스키마는 ``UNIQUE (policy_id)`` 라 정책당 한 행뿐이었습니다. 그런
        DB 는 테이블을 다시 만들고 기존 행을 **버전 1 · 활성**으로 옮깁니다 —
        지금까지 정책마다 한 번씩만 등록했으므로 그 행이 곧 현재 버전입니다.
        ⛔ 옮기는 동안 어떤 값도 바꾸지 않습니다.
        """
        with self.connection() as conn:
            conn.execute(CREATE_TABLE_SQL)
            self._migrate_to_versioned(conn)

    @staticmethod
    def _migrate_to_versioned(conn: sqlite3.Connection) -> None:
        """정책당 한 행이던 구 스키마를 버전 구조로 바꿉니다."""
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(policy_company_source)")}
        if {"version", "file_checksum", "is_active"} <= columns:
            return  # 이미 버전 구조입니다.

        conn.execute(
            "ALTER TABLE policy_company_source RENAME TO policy_company_source_pre_version"
        )
        conn.execute(CREATE_TABLE_SQL)
        conn.execute(
            "INSERT INTO policy_company_source "
            "(policy_company_source_id, policy_id, source, company_count, "
            "certification_count, source_label, version, file_checksum, is_active, "
            "registered_at, updated_at) "
            "SELECT policy_company_source_id, policy_id, source, company_count, "
            "certification_count, source_label, 1, NULL, 1, registered_at, updated_at "
            "FROM policy_company_source_pre_version"
        )
        conn.execute("DROP TABLE policy_company_source_pre_version")

    def get(self, policy_id: int) -> PolicyCompanySource | None:
        """한 정책의 등록 기록을 조회합니다.

        Args:
            policy_id: 대상 정책 ID.

        Returns:
            :class:`PolicyCompanySource`. **등록된 적이 없으면 ``None``** 이며,
            그것이 곧 **조회불가**를 뜻합니다.
        """
        rows = self.execute(
            "SELECT * FROM policy_company_source WHERE policy_id = ? AND is_active = 1",
            (policy_id,),
        )
        return self._row_to_source(rows[0]) if rows else None

    def find_versions(self, policy_id: int) -> list[PolicyCompanySource]:
        """한 정책의 등록 이력을 **버전 순서대로** 반환합니다.

        ⛔ 예전 버전은 지워지지 않습니다. 계산에 쓰이지 않을 뿐입니다.

        Args:
            policy_id: 대상 정책 ID.

        Returns:
            버전 오름차순 :class:`PolicyCompanySource` 목록.
        """
        rows = self.execute(
            "SELECT * FROM policy_company_source WHERE policy_id = ? ORDER BY version",
            (policy_id,),
        )
        return [self._row_to_source(row) for row in rows]

    def registered_policy_ids(self) -> set[int]:
        """기업정보를 받은 적이 있는 정책 ID 집합.

        Returns:
            등록된 정책 ID. 비어 있으면 **어느 정책도 판정할 수 없습니다.**
        """
        rows = self.execute("SELECT DISTINCT policy_id FROM policy_company_source")
        return {int(row["policy_id"]) for row in rows}

    def find_all(self) -> list[PolicyCompanySource]:
        """등록 기록 전체를 반환합니다."""
        rows = self.execute(
            "SELECT * FROM policy_company_source WHERE is_active = 1 ORDER BY policy_id"
        )
        return [self._row_to_source(row) for row in rows]

    def record(
        self,
        policy_id: int,
        *,
        source: str,
        company_count: int,
        certification_count: int,
        source_label: str | None = None,
        file_checksum: str | None = None,
    ) -> PolicyCompanySource:
        """정책의 기업정보를 **받았다는 사실**을 새 버전으로 기록합니다.

        🟢 2026-09-05 고객 확정: 새 파일이 올라오면 그 파일이 최신이 되고,
        예전 버전은 **이력으로 남습니다.**

        ==============================  ====================================
        올린 파일                        결과
        ==============================  ====================================
        내용이 **같다**(같은 지문)        ⛔ 새 버전을 만들지 않는다. 건수·시각만 갱신
        내용이 **다르다**                 새 버전이 활성이 되고, 이전 버전은 비활성
        지문이 없다(조회 방식 등)          내용을 비교할 수 없으므로 **새 버전**
        ==============================  ====================================

        ⛔ 파일명으로 판단하지 않습니다 — 이름이 같아도 내용이 다르면 다른
        자료이고, 이름이 달라도 내용이 같으면 같은 자료입니다.

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
            file_checksum: 올린 파일 내용의 지문. 같은 지문이면 새 버전을
                만들지 않습니다.

        Returns:
            지금 **활성**인 :class:`PolicyCompanySource`.
        """
        now = datetime.now()
        current = self.get(policy_id)

        if (
            current is not None
            and file_checksum is not None
            and current.file_checksum == file_checksum
        ):
            # 같은 자료를 다시 올렸다 — ⛔ 버전을 늘리지 않는다(멱등).
            self.execute_write(
                "UPDATE policy_company_source SET company_count = ?, "
                "certification_count = ?, source_label = ?, updated_at = ? "
                "WHERE policy_company_source_id = ?",
                (
                    company_count,
                    certification_count,
                    source_label,
                    _to_db(now),
                    current.policy_company_source_id,
                ),
            )
            refreshed = self.get(policy_id)
            assert refreshed is not None  # 방금 갱신했다
            return refreshed

        rows = self.execute(
            "SELECT MAX(version) AS latest FROM policy_company_source WHERE policy_id = ?",
            (policy_id,),
        )
        latest = rows[0]["latest"]
        next_version = 1 if latest is None else int(latest) + 1

        with self.connection() as conn:
            # ⛔ 예전 버전을 지우지 않는다. 계산에서만 빠진다.
            conn.execute(
                "UPDATE policy_company_source SET is_active = 0, updated_at = ? "
                "WHERE policy_id = ? AND is_active = 1",
                (_to_db(now), policy_id),
            )
            conn.execute(
                "INSERT INTO policy_company_source "
                "(policy_id, source, company_count, certification_count, source_label, "
                "version, file_checksum, is_active, registered_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)",
                (
                    policy_id,
                    source,
                    company_count,
                    certification_count,
                    source_label,
                    next_version,
                    file_checksum,
                    _to_db(now),
                    _to_db(now),
                ),
            )

        saved = self.get(policy_id)
        assert saved is not None  # 방금 저장했다
        return saved

    def update_counts(
        self, policy_company_source_id: int, *, company_count: int, certification_count: int
    ) -> None:
        """이미 만든 버전에 집계만 채웁니다.

        버전은 **적재 전에** 정해야 인증을 그 버전으로 표시할 수 있는데, 건수는
        **적재가 끝나야** 알 수 있어서 두 번에 나눠 씁니다.

        Args:
            policy_company_source_id: 대상 버전 ID.
            company_count: 확인한 기업 수.
            certification_count: 새로 저장한 인증 수.
        """
        self.execute_write(
            "UPDATE policy_company_source SET company_count = ?, certification_count = ?, "
            "updated_at = ? WHERE policy_company_source_id = ?",
            (company_count, certification_count, _to_db(datetime.now()), policy_company_source_id),
        )

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
            version=int(row["version"]),
            file_checksum=row["file_checksum"],
            is_active=bool(row["is_active"]),
            registered_at=_from_db(row["registered_at"]),
            updated_at=_from_db(row["updated_at"]),
        )
