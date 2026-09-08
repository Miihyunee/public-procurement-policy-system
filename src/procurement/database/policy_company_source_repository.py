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
from procurement.models.policy_company_source import (
    IMPORT_COMPLETED,
    IMPORT_IN_PROGRESS,
    PolicyCompanySource,
)

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
    import_status TEXT NOT NULL DEFAULT 'COMPLETED',
    completed_at DATETIME,
    processed_count INTEGER NOT NULL DEFAULT 0,
    total_count INTEGER NOT NULL DEFAULT 0,
    registered_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE (policy_id, version),
    FOREIGN KEY (policy_id) REFERENCES policy (policy_id)
)
"""

#: 활성이면서 **끝까지 간** 버전만 고르는 조건 (STEP 154).
#:
#: ⛔ ``is_active`` 만으로는 부족합니다. 예전에는 버전 행이 만들어지는 순간
#: 활성이 되어, 적재가 중간에 끊겨도 화면이 「등록완료」라고 말했습니다.
_ACTIVE_AND_COMPLETE = f"is_active = 1 AND import_status = '{IMPORT_COMPLETED}'"


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
            self._add_import_status(conn)

    @staticmethod
    def _add_import_status(conn: sqlite3.Connection) -> None:
        """적재 완료 여부 칸을 더합니다 (STEP 154).

        .. warning::
            ⛔ **기존 행을 모두 ``COMPLETED`` 로 둡니다.** 보수적인 선택입니다.

            지금 스키마에는 «그 등록이 끝까지 갔는가» 를 되짚을 근거가 없습니다.
            그렇다고 전부 미완료로 두면, 이미 잘 쓰고 있던 정책이 하루아침에
            **조회불가**가 되어 달성률이 사라집니다. 지금까지의 동작을 그대로
            두는 쪽이 안전합니다(DECISIONS §0.58).

            ⛔ 기존 기업·인증 데이터는 손대지 않습니다.
        """
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(policy_company_source)")}
        if "import_status" not in columns:
            conn.execute(
                "ALTER TABLE policy_company_source "
                f"ADD COLUMN import_status TEXT NOT NULL DEFAULT '{IMPORT_COMPLETED}'"
            )
        if "completed_at" not in columns:
            conn.execute("ALTER TABLE policy_company_source ADD COLUMN completed_at DATETIME")
        # 진행률 칸 (STEP 156). 예전 행은 0 이며, 끝난 등록의 진행률은 응답
        # 계층이 100% 로 읽습니다 — ⛔ 옛 행을 되짚어 채우지 않습니다.
        for column in ("processed_count", "total_count"):
            if column not in columns:
                conn.execute(
                    "ALTER TABLE policy_company_source "
                    f"ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
                )

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

        .. note::
            끝까지 가지 않은 등록(:data:`IMPORT_IN_PROGRESS`)은 여기서 나오지
            않습니다 — 그 자료로 계산하면 안 되기 때문입니다(STEP 154).
        """
        rows = self.execute(
            f"SELECT * FROM policy_company_source WHERE policy_id = ? AND {_ACTIVE_AND_COMPLETE}",
            (policy_id,),
        )
        return self._row_to_source(rows[0]) if rows else None

    def find_in_progress(self, policy_id: int) -> PolicyCompanySource | None:
        """끝나지 않은 등록이 있으면 **가장 최근 것**을 돌려줍니다(STEP 154).

        화면이 「미등록」과 「등록 진행 중」을 가르는 데 씁니다. ⛔ 계산에는
        쓰지 않습니다.

        Args:
            policy_id: 대상 정책 ID.

        Returns:
            진행 중인 등록. 없으면 ``None``.
        """
        rows = self.execute(
            "SELECT * FROM policy_company_source WHERE policy_id = ? AND import_status = ? "
            "ORDER BY version DESC",
            (policy_id, IMPORT_IN_PROGRESS),
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

        .. note::
            끝까지 가지 않은 등록은 **세지 않습니다**(STEP 154). 적재가 끊긴
            자료로 「미해당」을 말할 수 없고, 그 상태는 여전히 **조회불가**
            입니다.
        """
        rows = self.execute(
            f"SELECT DISTINCT policy_id FROM policy_company_source WHERE {_ACTIVE_AND_COMPLETE}"
        )
        return {int(row["policy_id"]) for row in rows}

    def find_all(self) -> list[PolicyCompanySource]:
        """등록 기록 전체를 반환합니다."""
        rows = self.execute(
            f"SELECT * FROM policy_company_source WHERE {_ACTIVE_AND_COMPLETE} ORDER BY policy_id"
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

        .. note::
            적재가 **이미 끝난 뒤** 한 번에 기록하는 경로입니다(조회 방식 등).
            파일 업로드처럼 적재가 오래 걸리는 경로는 :meth:`begin` 으로 열고
            :meth:`complete` 로 닫습니다(STEP 154).
        """
        started = self.begin(
            policy_id, source=source, source_label=source_label, file_checksum=file_checksum
        )
        assert started.policy_company_source_id is not None  # 방금 만들었다
        self.complete(
            started.policy_company_source_id,
            company_count=company_count,
            certification_count=certification_count,
        )
        saved = self.get(policy_id)
        assert saved is not None  # 방금 완료 처리했다
        return saved

    def begin(
        self,
        policy_id: int,
        *,
        source: str,
        source_label: str | None = None,
        file_checksum: str | None = None,
    ) -> PolicyCompanySource:
        """등록을 **시작**합니다 — ⛔ 활성으로 만들지 않습니다 (STEP 154).

        왜 나눴나
        =========
        예전에는 버전 행을 만드는 순간 바로 활성이 되고 이전 버전이 비활성이
        되었습니다. 그래서 98,832행 적재가 중간에 끊기면

        - 새 버전은 **비어 있는데 활성**이고
        - 멀쩡하던 이전 버전은 **이미 비활성**이며
        - 화면은 「등록완료」라고 말했습니다.

        이제 시작 시점에는 :data:`IMPORT_IN_PROGRESS` · ``is_active = 0`` 으로
        두고, :meth:`complete` 가 불릴 때만 활성으로 바꿉니다. 적재가 끊기면
        **이전 활성 버전이 그대로 남습니다.**

        Args:
            policy_id: 대상 정책 ID.
            source: ``FILE`` 또는 ``API``.
            source_label: 사용자가 알아볼 출처 표시(파일명 등).
            file_checksum: 올린 파일 내용의 지문. 이미 **끝까지 간** 같은 지문이
                있으면 새 버전을 만들지 않고 그 행을 그대로 씁니다.

        Returns:
            이번 등록이 쓸 :class:`PolicyCompanySource`.
        """
        now = datetime.now()
        current = self.get(policy_id)

        if (
            current is not None
            and file_checksum is not None
            and current.file_checksum == file_checksum
        ):
            # 같은 자료를 다시 올렸다 — ⛔ 버전을 늘리지 않는다(멱등).
            # ⛔ 상태도 되돌리지 않는다. 이번 재적재가 끊기더라도 이미 끝나 있던
            #    이 버전이 그대로 살아 있어야 한다. 진행률도 그대로 둔다 —
            #    끝난 등록을 다시 「43%」로 되돌려 보이면 안 된다(STEP 156).
            return current

        rows = self.execute(
            "SELECT MAX(version) AS latest FROM policy_company_source WHERE policy_id = ?",
            (policy_id,),
        )
        latest = rows[0]["latest"]
        next_version = 1 if latest is None else int(latest) + 1

        self.execute_write(
            "INSERT INTO policy_company_source "
            "(policy_id, source, company_count, certification_count, source_label, "
            "version, file_checksum, is_active, import_status, processed_count, "
            "total_count, registered_at, updated_at) "
            "VALUES (?, ?, 0, 0, ?, ?, ?, 0, ?, 0, 0, ?, ?)",
            (
                policy_id,
                source,
                source_label,
                next_version,
                file_checksum,
                IMPORT_IN_PROGRESS,
                _to_db(now),
                _to_db(now),
            ),
        )
        started = self.find_in_progress(policy_id)
        assert started is not None  # 방금 만들었다
        return started

    def update_progress(
        self, policy_company_source_id: int, *, processed_count: int, total_count: int
    ) -> None:
        """지금까지 **실제로 처리한** 행 수를 기록합니다 (STEP 156).

        ⛔ 짐작하지 않습니다. 적재 루프가 센 값만 적습니다.
        ⛔ 행마다 부르지 않습니다 — 부르는 쪽이 간격을 둡니다. 98,832행에
           행마다 UPDATE 를 걸면 이미 느린 적재가 더 느려집니다.

        Args:
            policy_company_source_id: :meth:`begin` 이 돌려준 버전 ID.
            processed_count: 지금까지 처리한 행 수.
            total_count: 이번 등록의 전체 행 수.
        """
        self.execute_write(
            "UPDATE policy_company_source SET processed_count = ?, total_count = ?, "
            "updated_at = ? WHERE policy_company_source_id = ?",
            (processed_count, total_count, _to_db(datetime.now()), policy_company_source_id),
        )

    def complete(
        self,
        policy_company_source_id: int,
        *,
        company_count: int,
        certification_count: int,
        total_count: int | None = None,
    ) -> None:
        """적재가 **끝났음**을 기록하고 그 버전을 활성으로 올립니다(STEP 154).

        ⭐ 이전 버전을 비활성으로 내리는 것도 **여기서** 합니다. 적재가 끝나기
        전에 내리면, 끊겼을 때 멀쩡하던 자료까지 계산에서 빠집니다.

        ⛔ 예전 버전을 지우지 않습니다. 계산에서만 빠집니다.

        Args:
            policy_company_source_id: :meth:`begin` 이 돌려준 버전 ID.
            company_count: 그 버전에 매인 기업 수.
            certification_count: 그 버전에 매인 인증 수.
            total_count: 이번 등록의 전체 행 수. 주면 진행률을 그 값으로
                마무리합니다. ⛔ 주지 않으면 기존 ``total_count`` 를 그대로
                두고 ``processed_count`` 만 거기에 맞춥니다 — 끝난 등록의
                진행률이 100% 보다 작게 남으면 안 됩니다.
        """
        now = datetime.now()
        rows = self.execute(
            "SELECT policy_id FROM policy_company_source WHERE policy_company_source_id = ?",
            (policy_company_source_id,),
        )
        if not rows:
            return
        policy_id = int(rows[0]["policy_id"])

        with self.connection() as conn:
            conn.execute(
                "UPDATE policy_company_source SET is_active = 0, updated_at = ? "
                "WHERE policy_id = ? AND is_active = 1 AND policy_company_source_id != ?",
                (_to_db(now), policy_id, policy_company_source_id),
            )
            if total_count is not None:
                conn.execute(
                    "UPDATE policy_company_source SET total_count = ? "
                    "WHERE policy_company_source_id = ?",
                    (total_count, policy_company_source_id),
                )
            conn.execute(
                "UPDATE policy_company_source SET company_count = ?, certification_count = ?, "
                "is_active = 1, import_status = ?, completed_at = ?, updated_at = ?, "
                "processed_count = total_count "
                "WHERE policy_company_source_id = ?",
                (
                    company_count,
                    certification_count,
                    IMPORT_COMPLETED,
                    _to_db(now),
                    _to_db(now),
                    policy_company_source_id,
                ),
            )

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
            import_status=row["import_status"],
            completed_at=(
                _from_db(row["completed_at"]) if row["completed_at"] is not None else None
            ),
            processed_count=int(row["processed_count"]),
            total_count=int(row["total_count"]),
            registered_at=_from_db(row["registered_at"]),
            updated_at=_from_db(row["updated_at"]),
        )
