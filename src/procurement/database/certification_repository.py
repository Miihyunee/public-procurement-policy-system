"""
procurement.database.certification_repository

Certification 엔티티의 영속화(저장/조회)를 담당하는 Repository.

:class:`procurement.database.base.BaseRepository` 를 상속하며, SQLite 표준 SQL
만 사용합니다. 테이블 컬럼은 ``docs/DATABASE_DESIGN.md`` 의 Certification 정의를
그대로 따르고, 설계에 없는 컬럼은 추가하지 않습니다.

.. note::
    본 Repository 는 데이터 접근만 담당합니다. Foreign Key 제약, Company/Policy
    존재 여부 검증, 비즈니스 로직은 이번 범위에 포함하지 않습니다.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from procurement.database.base import BaseRepository
from procurement.models.certification import Certification


class CertificationValidationError(ValueError):
    """필수값 누락·유효기간 오류 등 Certification 데이터 검증 실패 시 발생하는 예외."""


# DATABASE_DESIGN.md 의 Certification 테이블 정의를 그대로 반영한다.
# Foreign Key 제약은 이번 Issue 범위에서 제외한다.
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS certification (
    certification_id INTEGER PRIMARY KEY,
    company_id INTEGER NOT NULL,
    policy_id INTEGER NOT NULL,
    certificate_number TEXT,
    policy_company_source_id INTEGER,
    valid_from DATE NOT NULL,
    valid_to DATE,
    issuing_agency TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""

# 필수 입력값 (None 허용 금지)
#
# 🟢 2026-09-04 고객 확정(STEP 108): 사회적기업·사회적협동조합은 종료일이
#    없으며 계속 유효하다. 따라서 ``valid_to`` 는 필수값이 아닙니다.
#    ⛔ 없는 종료일을 지어내어 채우지 않습니다.
_REQUIRED_FIELDS = ("company_id", "policy_id", "valid_from")


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


def _to_db_date_optional(value: date | None) -> str | None:
    """종료일이 없는 인증을 위해 ``None`` 을 그대로 통과시킵니다."""
    return None if value is None else _to_db_date(value)


def _from_db_date_optional(value: str | None) -> date | None:
    """NULL 종료일을 ``None`` 으로 읽습니다 (= 계속 유효)."""
    return None if value is None else _from_db_date(value)


class CertificationRepository(BaseRepository):
    """Certification 테이블에 대한 데이터 접근 계층."""

    table_name = "certification"

    def create_table(self) -> None:
        """Certification 테이블을 생성합니다 (없을 때만).

        ``CREATE TABLE IF NOT EXISTS`` 를 사용하므로 반복 호출해도 안전합니다.

        이미 만들어진 DB 는 ``valid_to`` 가 ``NOT NULL`` 이라 종료일 없는
        인증을 넣을 수 없습니다. 그런 DB 만 골라 테이블을 다시 만들고 기존
        행을 **값 그대로** 옮깁니다. 옮기는 동안 어떤 날짜도 바꾸지 않습니다.
        """
        with self.connection() as conn:
            conn.execute(CREATE_TABLE_SQL)
            self._migrate_valid_to_nullable(conn)
            self._migrate_source_column(conn)

    @staticmethod
    def _migrate_valid_to_nullable(conn: sqlite3.Connection) -> None:
        """구 스키마(``valid_to NOT NULL``)를 종료일 없는 인증도 담도록 바꿉니다."""
        columns = conn.execute("PRAGMA table_info(certification)").fetchall()
        valid_to = next((column for column in columns if column["name"] == "valid_to"), None)
        if valid_to is None or not valid_to["notnull"]:
            return  # 이미 종료일 없는 인증을 담을 수 있습니다.

        conn.execute("ALTER TABLE certification RENAME TO certification_pre_open_ended")
        conn.execute(CREATE_TABLE_SQL)
        conn.execute(
            "INSERT INTO certification "
            "(certification_id, company_id, policy_id, certificate_number, "
            "valid_from, valid_to, issuing_agency, created_at, updated_at) "
            "SELECT certification_id, company_id, policy_id, certificate_number, "
            "valid_from, valid_to, issuing_agency, created_at, updated_at "
            "FROM certification_pre_open_ended"
        )
        conn.execute("DROP TABLE certification_pre_open_ended")

    @staticmethod
    def _migrate_source_column(conn: sqlite3.Connection) -> None:
        """인증에 «어느 등록 버전에서 왔는지» 칸을 더합니다.

        이미 저장된 인증은 그 정책의 **하나뿐인 등록 버전**을 가리키게 채웁니다
        — 지금까지 정책마다 한 번씩만 등록했으므로 그 버전이 곧 현재 자료입니다.
        ⛔ 인증 자체는 하나도 건드리지 않습니다.
        """
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(certification)")}
        if "policy_company_source_id" in columns:
            return
        conn.execute("ALTER TABLE certification ADD COLUMN policy_company_source_id INTEGER")

        tables = {
            row["name"]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        if "policy_company_source" not in tables:
            return
        # 등록표가 아직 버전 구조가 아닐 수 있습니다(표를 만드는 순서 때문에).
        # 그때는 정책당 한 행뿐이라 ``is_active`` 를 물을 필요가 없습니다.
        source_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(policy_company_source)")
        }
        active_clause = " AND s.is_active = 1" if "is_active" in source_columns else ""
        conn.execute(
            "UPDATE certification SET policy_company_source_id = ("
            "  SELECT s.policy_company_source_id FROM policy_company_source s"
            "  WHERE s.policy_id = certification.policy_id" + active_clause + ") "
            "WHERE policy_company_source_id IS NULL"
        )

    def insert(self, certification: Certification) -> Certification:
        """인증 정보를 저장하고 채번된 ID 와 타임스탬프를 반영해 반환합니다.

        Args:
            certification: 저장할 :class:`Certification`.
                ``certification_id`` 는 무시되고 자동 채번됩니다.

        Returns:
            ``certification_id`` / ``created_at`` / ``updated_at`` 가 채워진
            새 :class:`Certification`.

        Raises:
            CertificationValidationError: 필수값이 ``None`` 이거나
                ``valid_to`` 가 ``valid_from`` 보다 이전인 경우.
        """
        self._validate(certification)

        now = datetime.now()
        created_at = certification.created_at or now
        updated_at = certification.updated_at or now

        sql = (
            "INSERT INTO certification "
            "(company_id, policy_id, certificate_number, policy_company_source_id, "
            "valid_from, valid_to, issuing_agency, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            certification.company_id,
            certification.policy_id,
            certification.certificate_number,
            certification.policy_company_source_id,
            _to_db_date(certification.valid_from),
            _to_db_date_optional(certification.valid_to),
            certification.issuing_agency,
            _to_db(created_at),
            _to_db(updated_at),
        )

        with self.connection() as conn:
            cursor = conn.execute(sql, params)
            new_id = cursor.lastrowid

        return Certification(
            certification_id=new_id,
            company_id=certification.company_id,
            policy_id=certification.policy_id,
            certificate_number=certification.certificate_number,
            policy_company_source_id=certification.policy_company_source_id,
            valid_from=certification.valid_from,
            valid_to=certification.valid_to,
            issuing_agency=certification.issuing_agency,
            created_at=created_at,
            updated_at=updated_at,
        )

    def assign_source(self, certification_id: int, policy_company_source_id: int | None) -> None:
        """이미 있는 인증을 **지금 등록 버전**의 것으로 다시 표시합니다.

        ⭐ 최신 목록에 그대로 들어 있는 기업은 인증 내용이 같아 새로 저장되지
        않습니다. 그때 표시를 옮겨 주지 않으면 «예전 버전에서 온 인증» 으로
        남아 계산에서 빠집니다 — 목록에 멀쩡히 있는 기업이 조용히 실적에서
        사라지는 셈입니다.

        ⛔ 날짜·기업·정책은 건드리지 않습니다. **어느 자료에서 확인되었는가**
        만 갱신합니다.

        Args:
            certification_id: 대상 인증 ID.
            policy_company_source_id: 지금 등록 버전 ID.
        """
        self.execute_write(
            "UPDATE certification SET policy_company_source_id = ?, updated_at = ? "
            "WHERE certification_id = ?",
            (policy_company_source_id, _to_db(datetime.now()), certification_id),
        )

    def find_by_id(self, certification_id: int) -> Certification | None:
        """certification_id 로 인증 정보를 조회합니다.

        Args:
            certification_id: 조회할 내부 고유 ID.

        Returns:
            일치하는 :class:`Certification`, 없으면 ``None``.
        """
        rows = self.execute(
            "SELECT * FROM certification WHERE certification_id = ?", (certification_id,)
        )
        return self._row_to_certification(rows[0]) if rows else None

    def find_by_company(self, company_id: int) -> list[Certification]:
        """해당 기업이 보유한 인증 목록을 반환합니다.

        Args:
            company_id: 조회할 Company 참조 ID.

        Returns:
            :class:`Certification` 목록. 없으면 빈 목록.
        """
        rows = self.execute(
            "SELECT * FROM certification WHERE company_id = ? ORDER BY certification_id",
            (company_id,),
        )
        return [self._row_to_certification(row) for row in rows]

    def find_by_policy(self, policy_id: int) -> list[Certification]:
        """해당 정책에 속한 인증 목록을 반환합니다 — **이력 포함 전부**.

        ⚠️ 계산에는 :meth:`find_active_by_policy` 를 씁니다. 이 메서드는
        «지금까지 받은 자료 전부» 를 봅니다.

        Args:
            policy_id: 조회할 Policy 참조 ID.

        Returns:
            :class:`Certification` 목록. 없으면 빈 목록.
        """
        rows = self.execute(
            "SELECT * FROM certification WHERE policy_id = ? ORDER BY certification_id",
            (policy_id,),
        )
        return [self._row_to_certification(row) for row in rows]

    def find_active_by_policy(self, policy_id: int) -> list[Certification]:
        """**지금 계산에 쓰는** 인증만 반환합니다.

        🟢 2026-09-05 고객 확정: *"기존 인증기업 데이터는 이력으로 보관하고, 새
        파일이 올라오면 그 파일을 최신으로 선택한다. 현재 실적 계산은 최신으로
        선택된 파일을 기준으로 한다."*

        그래서 **활성 등록 버전에서 온 인증**만 봅니다. 예전 버전의 인증은
        ⛔ 지워지지 않고 남되 계산에서 빠집니다.

        어느 버전에도 매이지 않은 인증(``policy_company_source_id`` 가 ``None``)
        은 **그대로 셉니다.** 직접 넣은 인증이 등록 이력이 없다는 이유로 조용히
        사라지면, 저장한 사람이 모르는 사이에 실적이 줄어듭니다.

        Args:
            policy_id: 조회할 Policy 참조 ID.

        Returns:
            계산 대상 :class:`Certification` 목록.
        """
        if not self._has_source_table():
            return self.find_by_policy(policy_id)
        rows = self.execute(
            "SELECT c.* FROM certification c "
            "LEFT JOIN policy_company_source s "
            "  ON s.policy_company_source_id = c.policy_company_source_id "
            "WHERE c.policy_id = ? "
            "  AND (c.policy_company_source_id IS NULL OR s.is_active = 1) "
            "ORDER BY c.certification_id",
            (policy_id,),
        )
        return [self._row_to_certification(row) for row in rows]

    def _has_source_table(self) -> bool:
        """등록 버전 표가 있는가 — 없으면 버전 구분 없이 전부 봅니다."""
        rows = self.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            ("policy_company_source",),
        )
        return bool(rows)

    def policy_ids_with_certifications(self) -> set[int]:
        """인증이 **한 건이라도 있는** 정책 ID 집합.

        "이 정책을 판정할 근거가 있는가" 를 묻는 데 씁니다. 인증이 저장되어
        있다는 것은 그 정책의 기업 목록을 **어떤 경로로든 받았다**는 뜻입니다.

        Returns:
            인증을 가진 정책 ID. 비어 있으면 그 정책들은 판정할 수 없습니다.
        """
        rows = self.execute("SELECT DISTINCT policy_id FROM certification")
        return {int(row["policy_id"]) for row in rows}

    def count(self) -> int:
        """등록된 인증 수를 반환합니다.

        Returns:
            certification 테이블의 전체 행 수.
        """
        rows = self.execute("SELECT COUNT(*) AS cnt FROM certification")
        return int(rows[0]["cnt"])

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------
    def _validate(self, certification: Certification) -> None:
        """필수값과 유효기간을 검증합니다."""
        for field in _REQUIRED_FIELDS:
            if getattr(certification, field) is None:
                raise CertificationValidationError(f"필수값이 누락되었습니다: {field}")

        # 종료일이 없는 인증(= 계속 유효)은 순서를 따질 대상이 아닙니다.
        if certification.valid_to is not None and certification.valid_to < certification.valid_from:
            raise CertificationValidationError(
                "valid_to 는 valid_from 보다 이전일 수 없습니다: "
                f"valid_from={certification.valid_from}, valid_to={certification.valid_to}"
            )

    @staticmethod
    def _row_to_certification(row: sqlite3.Row) -> Certification:
        """SQLite Row 를 :class:`Certification` 으로 변환합니다."""
        return Certification(
            certification_id=row["certification_id"],
            company_id=row["company_id"],
            policy_id=row["policy_id"],
            certificate_number=row["certificate_number"],
            policy_company_source_id=row["policy_company_source_id"],
            valid_from=_from_db_date(row["valid_from"]),
            valid_to=_from_db_date_optional(row["valid_to"]),
            issuing_agency=row["issuing_agency"],
            created_at=_from_db(row["created_at"]),
            updated_at=_from_db(row["updated_at"]),
        )
