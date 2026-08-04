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
    valid_from DATE NOT NULL,
    valid_to DATE NOT NULL,
    issuing_agency TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""

# 필수 입력값 (None 허용 금지)
_REQUIRED_FIELDS = ("company_id", "policy_id", "valid_from", "valid_to")


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


class CertificationRepository(BaseRepository):
    """Certification 테이블에 대한 데이터 접근 계층."""

    table_name = "certification"

    def create_table(self) -> None:
        """Certification 테이블을 생성합니다 (없을 때만).

        ``CREATE TABLE IF NOT EXISTS`` 를 사용하므로 반복 호출해도 안전합니다.
        """
        with self.connection() as conn:
            conn.execute(CREATE_TABLE_SQL)

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
            "(company_id, policy_id, certificate_number, valid_from, valid_to, "
            "issuing_agency, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            certification.company_id,
            certification.policy_id,
            certification.certificate_number,
            _to_db_date(certification.valid_from),
            _to_db_date(certification.valid_to),
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
            valid_from=certification.valid_from,
            valid_to=certification.valid_to,
            issuing_agency=certification.issuing_agency,
            created_at=created_at,
            updated_at=updated_at,
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
        """해당 정책에 속한 인증 목록을 반환합니다.

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

        if certification.valid_to < certification.valid_from:
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
            valid_from=_from_db_date(row["valid_from"]),
            valid_to=_from_db_date(row["valid_to"]),
            issuing_agency=row["issuing_agency"],
            created_at=_from_db(row["created_at"]),
            updated_at=_from_db(row["updated_at"]),
        )
