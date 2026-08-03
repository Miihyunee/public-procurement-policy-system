"""
procurement.database.company_repository

Company 엔티티의 영속화(저장/조회)를 담당하는 Repository.

:class:`procurement.database.base.BaseRepository` 를 상속하며, SQLite 표준 SQL
만 사용합니다. 테이블 컬럼은 ``docs/DATABASE_DESIGN.md`` 의 Company 정의를
그대로 따르고, 설계에 없는 컬럼은 추가하지 않습니다.

.. note::
    본 Repository 는 Foundation 단계 범위로, Insert/조회/집계만 제공합니다.
    Update/Delete 및 비즈니스 로직은 이후 Issue 에서 구현합니다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from procurement.database.base import BaseRepository
from procurement.models.company import Company


class CompanyValidationError(ValueError):
    """필수값 누락 등 Company 데이터 검증 실패 시 발생하는 예외."""


class DuplicateBusinessNoError(Exception):
    """이미 등록된 사업자등록번호로 저장을 시도할 때 발생하는 예외."""


# DATABASE_DESIGN.md 의 Company 테이블 정의를 그대로 반영한다.
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS company (
    company_id INTEGER PRIMARY KEY,
    business_no TEXT UNIQUE NOT NULL,
    company_name TEXT NOT NULL,
    representative_name TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""

# 필수 입력값 (business_no 는 UNIQUE 제약과 별개로 NOT NULL/비어있지 않아야 함)
_REQUIRED_FIELDS = ("business_no", "company_name", "representative_name")


def _to_db(value: datetime) -> str:
    """datetime 을 SQLite 저장용 ISO 문자열로 변환합니다."""
    return value.isoformat(sep=" ")


def _from_db(value: str) -> datetime:
    """SQLite 에서 읽은 ISO 문자열을 datetime 으로 변환합니다."""
    return datetime.fromisoformat(value)


class CompanyRepository(BaseRepository):
    """Company 테이블에 대한 데이터 접근 계층."""

    table_name = "company"

    def create_table(self) -> None:
        """Company 테이블을 생성합니다 (없을 때만).

        ``CREATE TABLE IF NOT EXISTS`` 를 사용하므로 반복 호출해도 안전합니다.
        """
        with self.connection() as conn:
            conn.execute(CREATE_TABLE_SQL)

    def insert(self, company: Company) -> Company:
        """기업을 등록하고 채번된 ``company_id`` 와 타임스탬프를 반영해 반환합니다.

        Args:
            company: 저장할 :class:`Company`. ``company_id`` 는 무시되고 자동 채번됩니다.

        Returns:
            ``company_id`` / ``created_at`` / ``updated_at`` 가 채워진 새 :class:`Company`.

        Raises:
            CompanyValidationError: 필수값(사업자번호·기업명·대표자명)이 비어 있는 경우.
            DuplicateBusinessNoError: 동일한 사업자등록번호가 이미 존재하는 경우.
        """
        self._validate_required(company)

        now = datetime.now()
        created_at = company.created_at or now
        updated_at = company.updated_at or now

        sql = (
            "INSERT INTO company "
            "(business_no, company_name, representative_name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)"
        )
        params = (
            company.business_no,
            company.company_name,
            company.representative_name,
            _to_db(created_at),
            _to_db(updated_at),
        )

        with self.connection() as conn:
            try:
                cursor = conn.execute(sql, params)
            except sqlite3.IntegrityError as exc:
                raise DuplicateBusinessNoError(
                    f"이미 등록된 사업자등록번호입니다: {company.business_no}"
                ) from exc
            new_id = cursor.lastrowid

        return Company(
            company_id=new_id,
            business_no=company.business_no,
            company_name=company.company_name,
            representative_name=company.representative_name,
            created_at=created_at,
            updated_at=updated_at,
        )

    def find_by_business_no(self, business_no: str) -> Company | None:
        """사업자등록번호로 기업을 조회합니다.

        Args:
            business_no: 조회할 사업자등록번호.

        Returns:
            일치하는 :class:`Company`, 없으면 ``None``.
        """
        rows = self.execute("SELECT * FROM company WHERE business_no = ?", (business_no,))
        return self._row_to_company(rows[0]) if rows else None

    def find_by_id(self, company_id: int) -> Company | None:
        """company_id 로 기업을 조회합니다.

        Args:
            company_id: 조회할 내부 고유 ID.

        Returns:
            일치하는 :class:`Company`, 없으면 ``None``.
        """
        rows = self.execute("SELECT * FROM company WHERE company_id = ?", (company_id,))
        return self._row_to_company(rows[0]) if rows else None

    def exists(self, business_no: str) -> bool:
        """해당 사업자등록번호의 기업이 존재하는지 확인합니다.

        Args:
            business_no: 확인할 사업자등록번호.

        Returns:
            존재하면 ``True``, 아니면 ``False``.
        """
        rows = self.execute("SELECT 1 FROM company WHERE business_no = ? LIMIT 1", (business_no,))
        return len(rows) > 0

    def count(self) -> int:
        """등록된 기업 수를 반환합니다.

        Returns:
            company 테이블의 전체 행 수.
        """
        rows = self.execute("SELECT COUNT(*) AS cnt FROM company")
        return int(rows[0]["cnt"])

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------
    def _validate_required(self, company: Company) -> None:
        """필수 입력값이 비어 있지 않은지 검증합니다."""
        for field in _REQUIRED_FIELDS:
            value = getattr(company, field)
            if value is None or not str(value).strip():
                raise CompanyValidationError(f"필수값이 누락되었습니다: {field}")

    @staticmethod
    def _row_to_company(row: sqlite3.Row) -> Company:
        """SQLite Row 를 :class:`Company` 로 변환합니다."""
        return Company(
            company_id=row["company_id"],
            business_no=row["business_no"],
            company_name=row["company_name"],
            representative_name=row["representative_name"],
            created_at=_from_db(row["created_at"]),
            updated_at=_from_db(row["updated_at"]),
        )
