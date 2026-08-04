"""
procurement.database.purchase_repository

Purchase 엔티티의 영속화(저장/조회)를 담당하는 Repository.

:class:`procurement.database.base.BaseRepository` 를 상속하며, SQLite 표준 SQL
만 사용합니다. 테이블 컬럼은 ``docs/DATABASE_DESIGN.md`` 의 Purchase 정의를
그대로 따르고, 설계에 없는 컬럼은 추가하지 않습니다.

.. note::
    본 Repository 는 단순 저장/조회만 담당합니다. Company 자동 매칭, 정책 계산,
    Certification 연계, Update/Delete, Foreign Key 제약은 이번 범위에 포함하지
    않습니다.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime
from decimal import Decimal

from procurement.database.base import BaseRepository
from procurement.models.purchase import Purchase


class PurchaseValidationError(ValueError):
    """필수값 누락·금액 오류 등 Purchase 데이터 검증 실패 시 발생하는 예외."""


# DATABASE_DESIGN.md v1.1 의 Purchase 테이블 정의를 그대로 반영한다.
# 판정 기준일을 계약일(창업기업)/지급일(일반 정책)로 이원화하기 위해
# contract_date 와 payment_date 를 사용한다.
# company_id 는 매칭 후 저장되므로 NULL 을 허용하고, Foreign Key 제약은
# 이번 Issue 범위에서 제외한다.
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS purchase (
    purchase_id INTEGER PRIMARY KEY,
    business_no TEXT NOT NULL,
    company_id INTEGER,
    company_name TEXT NOT NULL,
    contract_date DATE NOT NULL,
    payment_date DATE NOT NULL,
    amount NUMERIC NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""

# 공백만 있는 값도 허용하지 않는 문자열 필수값
_REQUIRED_TEXT_FIELDS = ("business_no", "company_name")


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
    """Decimal 을 SQLite 바인딩용 문자열로 변환합니다.

    sqlite3 는 :class:`~decimal.Decimal` 을 직접 바인딩하지 못하므로 문자열로
    전달합니다. NUMERIC 컬럼이므로 SQLite 가 수치형으로 저장합니다.
    """
    return str(value)


def _from_db_amount(value: object) -> Decimal:
    """SQLite 에서 읽은 금액 값을 Decimal 로 변환합니다."""
    return Decimal(str(value))


class PurchaseRepository(BaseRepository):
    """Purchase 테이블에 대한 데이터 접근 계층."""

    table_name = "purchase"

    def create_table(self) -> None:
        """Purchase 테이블을 생성합니다 (없을 때만).

        ``CREATE TABLE IF NOT EXISTS`` 를 사용하므로 반복 호출해도 안전합니다.
        """
        with self.connection() as conn:
            conn.execute(CREATE_TABLE_SQL)

    def insert(self, purchase: Purchase) -> Purchase:
        """구매실적을 저장하고 채번된 ID 와 타임스탬프를 반영해 반환합니다.

        Args:
            purchase: 저장할 :class:`Purchase`.
                ``purchase_id`` 는 무시되고 자동 채번됩니다.

        Returns:
            ``purchase_id`` / ``created_at`` / ``updated_at`` 가 채워진
            새 :class:`Purchase`.

        Raises:
            PurchaseValidationError: 필수값이 비어 있거나 ``amount`` 가 0 이하인 경우.
        """
        self._validate(purchase)

        now = datetime.now()
        created_at = purchase.created_at or now
        updated_at = purchase.updated_at or now

        sql = (
            "INSERT INTO purchase "
            "(business_no, company_id, company_name, contract_date, payment_date, "
            "amount, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            purchase.business_no,
            purchase.company_id,
            purchase.company_name,
            _to_db_date(purchase.contract_date),
            _to_db_date(purchase.payment_date),
            _to_db_amount(purchase.amount),
            _to_db(created_at),
            _to_db(updated_at),
        )

        with self.connection() as conn:
            cursor = conn.execute(sql, params)
            new_id = cursor.lastrowid

        return Purchase(
            purchase_id=new_id,
            business_no=purchase.business_no,
            company_id=purchase.company_id,
            company_name=purchase.company_name,
            contract_date=purchase.contract_date,
            payment_date=purchase.payment_date,
            amount=purchase.amount,
            created_at=created_at,
            updated_at=updated_at,
        )

    def find_by_id(self, purchase_id: int) -> Purchase | None:
        """purchase_id 로 구매실적을 조회합니다.

        Args:
            purchase_id: 조회할 내부 고유 ID.

        Returns:
            일치하는 :class:`Purchase`, 없으면 ``None``.
        """
        rows = self.execute("SELECT * FROM purchase WHERE purchase_id = ?", (purchase_id,))
        return self._row_to_purchase(rows[0]) if rows else None

    def find_by_business_no(self, business_no: str) -> list[Purchase]:
        """사업자등록번호로 구매실적 목록을 조회합니다.

        하나의 사업자등록번호에 여러 건의 구매실적이 존재할 수 있으므로
        목록을 반환합니다.

        Args:
            business_no: 조회할 사업자등록번호.

        Returns:
            :class:`Purchase` 목록. 없으면 빈 목록.
        """
        rows = self.execute(
            "SELECT * FROM purchase WHERE business_no = ? ORDER BY purchase_id",
            (business_no,),
        )
        return [self._row_to_purchase(row) for row in rows]

    def find_all(self) -> list[Purchase]:
        """전체 구매실적 목록을 조회합니다.

        전체 구매금액 집계 등 기관 단위 계산에 사용됩니다.

        Returns:
            :class:`Purchase` 목록. 없으면 빈 목록.
        """
        rows = self.execute("SELECT * FROM purchase ORDER BY purchase_id")
        return [self._row_to_purchase(row) for row in rows]

    def find_unmatched(self) -> list[Purchase]:
        """기업 매칭이 되지 않은 구매실적 목록을 조회합니다.

        ``company_id`` 가 ``NULL`` 인 행을 대상으로 합니다.

        Returns:
            :class:`Purchase` 목록. 없으면 빈 목록.
        """
        rows = self.execute("SELECT * FROM purchase WHERE company_id IS NULL ORDER BY purchase_id")
        return [self._row_to_purchase(row) for row in rows]

    def update_company_id(self, purchase_id: int, company_id: int) -> bool:
        """구매실적의 ``company_id`` 를 갱신합니다.

        ``company_id`` 만 변경합니다. ``updated_at`` 관리는 향후 Update 기능에서
        일괄 처리합니다.

        Args:
            purchase_id: 갱신할 구매실적의 내부 고유 ID.
            company_id: 연결할 Company 참조 ID.

        Returns:
            갱신된 행이 있으면 ``True``, 해당 ``purchase_id`` 가 없으면 ``False``.
        """
        affected = self.execute_write(
            "UPDATE purchase SET company_id = ? WHERE purchase_id = ?",
            (company_id, purchase_id),
        )
        return affected > 0

    def count(self) -> int:
        """등록된 구매실적 수를 반환합니다.

        Returns:
            purchase 테이블의 전체 행 수.
        """
        rows = self.execute("SELECT COUNT(*) AS cnt FROM purchase")
        return int(rows[0]["cnt"])

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------
    def _validate(self, purchase: Purchase) -> None:
        """필수값과 구매금액을 검증합니다 (DB 접근 전 수행)."""
        for field in _REQUIRED_TEXT_FIELDS:
            value = getattr(purchase, field)
            if value is None or not str(value).strip():
                raise PurchaseValidationError(f"필수값이 누락되었습니다: {field}")

        if purchase.contract_date is None:
            raise PurchaseValidationError("필수값이 누락되었습니다: contract_date")

        if purchase.payment_date is None:
            raise PurchaseValidationError("필수값이 누락되었습니다: payment_date")

        if purchase.amount is None:
            raise PurchaseValidationError("필수값이 누락되었습니다: amount")

        if purchase.amount <= 0:
            raise PurchaseValidationError(
                f"구매금액은 0 보다 커야 합니다: amount={purchase.amount}"
            )

    @staticmethod
    def _row_to_purchase(row: sqlite3.Row) -> Purchase:
        """SQLite Row 를 :class:`Purchase` 로 변환합니다."""
        return Purchase(
            purchase_id=row["purchase_id"],
            business_no=row["business_no"],
            company_id=row["company_id"],
            company_name=row["company_name"],
            contract_date=_from_db_date(row["contract_date"]),
            payment_date=_from_db_date(row["payment_date"]),
            amount=_from_db_amount(row["amount"]),
            created_at=_from_db(row["created_at"]),
            updated_at=_from_db(row["updated_at"]),
        )
