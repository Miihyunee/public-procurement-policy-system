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

from procurement.core.performance_exclusion import EXCLUDED, EXCLUDED_BUDGET_ACCOUNTS
from procurement.core.period import PeriodFilter
from procurement.database.base import BaseRepository
from procurement.models.import_batch import STATUS_ACTIVE
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
    -- 🟢 2026-09-02 PM 확정(STEP 87) — 실적 산정 기준일이 아니므로 NULL 을
    -- 허용한다. 고객 원본에 이 두 컬럼이 없어도 결의일자가 있는 거래는
    -- 적재되어야 한다. ⛔ 없는 값을 다른 날짜로 채우지 않는다.
    contract_date DATE,
    payment_date DATE,
    resolution_date DATE,
    issue_date DATE,
    description TEXT,
    budget_account TEXT,
    amount NUMERIC NOT NULL,
    batch_id INTEGER,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""

#: 배치 단위 조회·대체 처리에 필요한 인덱스.
CREATE_BATCH_INDEX_SQL = "CREATE INDEX IF NOT EXISTS idx_purchase_batch ON purchase (batch_id)"

#: 실적에서 빼는 예산과목 — SQL 파라미터 순서를 고정하기 위해 정렬해 둡니다.
#: (frozenset 은 순서가 보장되지 않아 SQL 이 실행마다 달라져 보일 수 있습니다.)
SORTED_EXCLUDED_BUDGET_ACCOUNTS: tuple[str, ...] = tuple(sorted(EXCLUDED_BUDGET_ACCOUNTS))

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


def _optional(row: sqlite3.Row, column: str) -> str | None:
    """구(舊) 스키마 DB 에서도 안전하게 컬럼 값을 읽습니다.

    ``ALTER TABLE`` 마이그레이션 전의 DB 를 그대로 열었을 때 컬럼이 없으면
    :class:`IndexError` 가 납니다. 없는 컬럼은 **값이 없는 것**으로 봅니다.
    """
    try:
        value = row[column]
    except IndexError:
        return None
    return str(value) if value is not None else None


class PurchaseRepository(BaseRepository):
    """Purchase 테이블에 대한 데이터 접근 계층."""

    table_name = "purchase"

    def create_table(self) -> None:
        """Purchase 테이블을 생성합니다 (없을 때만).

        ``CREATE TABLE IF NOT EXISTS`` 를 사용하므로 반복 호출해도 안전합니다.
        """
        with self.connection() as conn:
            conn.execute(CREATE_TABLE_SQL)

    def ensure_indexes(self) -> None:
        """조회 인덱스를 생성합니다 (없을 때만).

        ``batch_id`` 컬럼이 있어야 하므로, 구 스키마 DB 에서는 컬럼 추가
        (:func:`procurement.database.bootstrap.migrate_schema`) **이후에**
        호출해야 합니다. 컬럼이 없으면 아무 것도 하지 않습니다.
        """
        columns = {row["name"] for row in self.execute("PRAGMA table_info(purchase)")}
        if "batch_id" not in columns:
            return
        with self.connection() as conn:
            conn.execute(CREATE_BATCH_INDEX_SQL)

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
            "resolution_date, issue_date, description, budget_account, amount, "
            "batch_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            purchase.business_no,
            purchase.company_id,
            purchase.company_name,
            _to_db_date(purchase.contract_date) if purchase.contract_date else None,
            _to_db_date(purchase.payment_date) if purchase.payment_date else None,
            _to_db_date(purchase.resolution_date) if purchase.resolution_date else None,
            _to_db_date(purchase.issue_date) if purchase.issue_date else None,
            purchase.description,
            purchase.budget_account,
            _to_db_amount(purchase.amount),
            purchase.batch_id,
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
            resolution_date=purchase.resolution_date,
            issue_date=purchase.issue_date,
            description=purchase.description,
            budget_account=purchase.budget_account,
            amount=purchase.amount,
            batch_id=purchase.batch_id,
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

    def find_for_review(self, period: PeriodFilter | None = None) -> list[Purchase]:
        """**검토 대상** 구매실적을 조회합니다.

        ``find_all()`` 과 두 가지가 다릅니다.

        1. **대체된 배치의 행을 제외**합니다::

               batch_id 가 NULL 이거나
               batch_id 가 status='ACTIVE' 인 배치를 가리키는 행

           ``batch_id`` 가 ``NULL`` 인 행을 포함하는 이유는, 배치 도입 이전에
           적재된 데이터를 갑자기 사라지게 만들지 않기 위함입니다.

        2. ``period`` 를 주면 **기간 조건**을 적용합니다.

        .. important::
            **실적에서 빠진 행도 그대로 나옵니다.** 담당자가 화면에서 그 행을
            보고 사유를 확인하거나 **제외를 되돌릴** 수 있어야 하기 때문입니다.
            빠진 행이 목록에서 사라지면 되돌릴 방법이 없습니다.

            계산 모집단은 :meth:`find_for_calculation` 입니다 — 여기서 실적
            제외 조건이 **한 겹 더** 붙습니다.

        ``import_batch`` 테이블이 아직 없는 DB(구 스키마)에서는 배치 조건을
        건너뛰고 기존과 동일하게 동작합니다.

        Args:
            period: 적용할 기간 조건. ``None`` 이면 기간 제한 없이 전체.

        Returns:
            :class:`Purchase` 목록. 없으면 빈 목록.
        """
        conditions, params = self._review_scope_conditions(period)
        return self._select_purchases(conditions, params)

    def find_for_calculation(
        self, period: PeriodFilter | None = None, purchase_type: str | None = None
    ) -> list[Purchase]:
        """**계산 대상** 구매실적을 조회합니다 — 달성률 분모·분자의 모집단.

        :meth:`find_for_review` 의 조건에 **실적 제외**가 한 겹 더 붙습니다
        (2026-08-31 고객 확정 · ``DECISIONS.md`` §0.10)::

            검토 대상
              − 예산과목이 고객 지목 6종인 행
              − 담당자가 실적 제외로 확정한 행

        .. warning::
            ⛔ **행을 지우지 않습니다.** 계산에서만 뺍니다. 그 행은 검토
            화면에 그대로 남고, 누가·언제·왜 뺐는지는 검토 이력에 남습니다.

        .. warning::
            ⛔ **적요를 보지 않습니다.** `교육`·`강사`·`임차`·`렌트` 같은 낱말로
            빼지 않습니다 — 고객이 지출결의서·품의서를 확인해 판단한다고 했고
            그 자료는 시스템에 없습니다.

        .. note::
            **구매유형 좁히기(STEP 103 §10).** ``purchase_type`` 을 주면 담당자가
            검토 화면에서 **확정한** 유형이 그 값인 행만 남깁니다
            (``purchase_review.final_purchase_type``). 여성기업 목표가 구매유형별
            (공사 3% · 용역·물품 5%)이라 유형별 분모·분자가 필요하기 때문입니다.

            ⛔ **유형을 추정하지 않습니다.** 확정되지 않은 행
            (``final_purchase_type IS NULL`` 이거나 검토 행 자체가 없는 경우)은
            어느 유형에도 넣지 않습니다 — 담당자가 나중에 확정하면 그때 자연히
            들어옵니다. ⛔ 적요·예산과목·거래처명으로 유추하지 않습니다.

            ``None`` 이면 유형을 보지 않으므로 **기존 동작 그대로**입니다.

        Args:
            period: 적용할 기간 조건. ``None`` 이면 기간 제한 없이 전체.
            purchase_type: 좁힐 구매유형(``CONSTRUCTION``/``SERVICE``/``GOODS``).
                ``None`` 이면 좁히지 않습니다.

        Returns:
            :class:`Purchase` 목록. 없으면 빈 목록.
        """
        conditions, params = self._review_scope_conditions(period)
        exclusion_conditions, exclusion_params = self._performance_exclusion_conditions()
        conditions.extend(exclusion_conditions)
        params.extend(exclusion_params)

        if purchase_type is not None:
            type_condition, type_params = self._confirmed_purchase_type_condition(purchase_type)
            conditions.append(type_condition)
            params.extend(type_params)

        return self._select_purchases(conditions, params)

    def _confirmed_purchase_type_condition(self, purchase_type: str) -> tuple[str, list[object]]:
        """담당자가 **확정한** 구매유형이 주어진 값인 행만 남기는 조건.

        ⛔ 확정되지 않은 행은 남지 않습니다. 검토 행이 아예 없는 구매도,
        ``final_purchase_type`` 이 ``NULL`` 인 구매도 제외됩니다 — 둘 다
        «담당자가 아직 정하지 않았다» 는 같은 뜻이기 때문입니다.

        구 스키마 DB(검토 테이블이 없는 경우)에서는 확정값이 존재할 수 없으므로
        **아무 행도 남기지 않는** 조건을 돌려줍니다. ⛔ 반대로 «전부 남긴다» 로
        열어 두면 유형이 확정된 적 없는 데이터가 통째로 공사·용역·물품 분모에
        들어가 달성률이 틀립니다.
        """
        if not self._has_review_table():
            return "1 = 0", []
        return (
            "EXISTS (SELECT 1 FROM purchase_review r "
            "WHERE r.purchase_id = purchase.purchase_id "
            "AND r.final_purchase_type = ?)",
            [purchase_type],
        )

    def _has_review_table(self) -> bool:
        """검토 테이블이 있는지 확인합니다(구 스키마 DB 보호)."""
        return bool(
            self.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'purchase_review'"
            )
        )

    def _review_scope_conditions(
        self, period: PeriodFilter | None
    ) -> tuple[list[str], list[object]]:
        """배치·기간 조건 — 검토 대상과 계산 대상이 **함께** 쓰는 부분."""
        conditions: list[str] = []
        params: list[object] = []

        if self._has_import_batch_table():
            conditions.append(
                "(batch_id IS NULL OR batch_id IN "
                "(SELECT batch_id FROM import_batch WHERE status = ?))"
            )
            params.append(STATUS_ACTIVE)

        if period is not None:
            # date_field 는 PeriodFilter 가 허용 목록으로 검증하므로 SQL 에
            # 직접 넣어도 안전하다(사용자 입력이 그대로 들어오지 않는다).
            conditions.append(f"{period.date_field} BETWEEN ? AND ?")
            params.append(_to_db_date(period.start))
            params.append(_to_db_date(period.end))

        return conditions, params

    def _select_purchases(self, conditions: list[str], params: list[object]) -> list[Purchase]:
        """조건을 붙여 구매 행을 읽습니다(정렬은 항상 ``purchase_id``)."""
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        rows = self.execute(f"SELECT * FROM purchase{where} ORDER BY purchase_id", tuple(params))
        return [self._row_to_purchase(row) for row in rows]

    def count_missing_resolution_date(self) -> tuple[int, Decimal]:
        """**결의일자가 없는** 계산 대상 구매의 건수와 금액 합계를 셉니다.

        결의일자 기준으로 연도를 나누면 ``resolution_date`` 가 ``NULL`` 인 행은
        조회에서 빠집니다. 분모·분자 양쪽에서 함께 빠지므로 달성률은 왜곡되지
        않지만, **전체 구매액이 조용히 줄어드는데 화면에 아무 표시가 없습니다**
        (``docs/DECISIONS.md`` §0.8.4). 그 숫자를 화면에 알려 주기 위한 조회입니다.

        .. warning::
            ⛔ **계산에 쓰이지 않습니다.** 분모·분자 어느 쪽에도 들어가지 않으며,
            :class:`~procurement.calculators.procurement_achievement.ProcurementAchievementCalculator`
            는 이 값을 보지 않습니다. **표시 전용**입니다.

        .. note::
            **기간 조건을 받지 않습니다.** 이 행들은 ``resolution_date`` 가 없어서
            빠진 것이므로, 같은 날짜로 기간을 걸면 정의상 하나도 남지 않습니다.
            대신 :meth:`find_for_calculation` 과 **같은 배치 조건**(대체된 배치
            제외)을 적용해, 계산 대상과 같은 모집단에서 셉니다.

        Returns:
            ``(건수, 금액 합계)``. 없으면 ``(0, Decimal("0"))``.
        """
        where, params = self._missing_resolution_date_where()
        rows = self.execute(f"SELECT amount FROM purchase WHERE {where}", params)
        total = Decimal("0")
        for row in rows:
            total += _from_db_amount(row["amount"])
        return len(rows), total

    def find_missing_resolution_date(self) -> list[Purchase]:
        """**결의일자가 없는** 계산 대상 구매를 행 단위로 조회합니다.

        :meth:`count_missing_resolution_date` 가 세는 것과 **완전히 같은 모집단**
        입니다. 화면이 "N건" 만 보여 주면 담당자는 *어떤* 행인지 알 수 없어
        무엇을 확인해야 할지 판단할 수 없으므로, 같은 조건으로 행을 돌려줍니다.

        .. warning::
            ⛔ **조회만 합니다.** 결의일자를 채우지 않고, 지급일·계약일로
            대체하지도 않으며, 어떤 행도 수정하지 않습니다.

        .. warning::
            ⛔ **판정하지 않습니다.** 이 행들은 "오류"·"무효"·"실적 불인정" 이
            아니라 **결의일자가 입력되지 않은 구매**일 뿐입니다.

        .. note::
            :meth:`count_missing_resolution_date` 와 마찬가지로 **기간 조건을
            받지 않습니다.** 사유는 그 메서드의 설명과 같습니다.

        정렬은 :meth:`find_for_calculation` · :meth:`find_by_batch` 와 같은
        ``purchase_id`` 오름차순입니다 — 적재된 순서대로 보이므로 원본과
        맞대어 보기 쉽습니다.

        Returns:
            :class:`Purchase` 목록. 없으면 빈 목록.
        """
        where, params = self._missing_resolution_date_where()
        rows = self.execute(
            f"SELECT * FROM purchase WHERE {where} ORDER BY purchase_id",
            params,
        )
        return [self._row_to_purchase(row) for row in rows]

    def _missing_resolution_date_where(self) -> tuple[str, tuple[object, ...]]:
        """결의일자 미기재 조회의 **WHERE 절과 파라미터**를 만듭니다.

        건수 집계와 행 조회가 **같은 모집단**을 보도록 조건을 한 곳에서만
        만듭니다. 두 곳에 같은 SQL 을 적어 두면 한쪽만 고쳐졌을 때 "N건" 과
        목록의 길이가 어긋나는데, 화면에서는 그 어긋남이 보이지 않습니다.

        배치 조건은 :meth:`find_for_calculation` 과 동일합니다(새 판정 규칙을
        만들지 않습니다). ``import_batch`` 테이블이 없는 구 스키마에서는 배치
        조건을 건너뜁니다.
        """
        conditions = ["resolution_date IS NULL"]
        params: list[object] = []

        if self._has_import_batch_table():
            conditions.append(
                "(batch_id IS NULL OR batch_id IN "
                "(SELECT batch_id FROM import_batch WHERE status = ?))"
            )
            params.append(STATUS_ACTIVE)

        return " AND ".join(conditions), tuple(params)

    def find_by_batch(self, batch_id: int) -> list[Purchase]:
        """특정 배치로 적재된 구매실적을 조회합니다.

        Args:
            batch_id: 조회할 배치 ID.

        Returns:
            :class:`Purchase` 목록. 없으면 빈 목록.
        """
        rows = self.execute(
            "SELECT * FROM purchase WHERE batch_id = ? ORDER BY purchase_id", (batch_id,)
        )
        return [self._row_to_purchase(row) for row in rows]

    def _performance_exclusion_conditions(self) -> tuple[list[str], list[object]]:
        """**실적에서 빠지는 행**을 걸러 내는 조건을 만듭니다.

        2026-08-31 고객 확정(``DECISIONS.md`` §0.10). 빼는 경로는 둘뿐입니다.

        1. **예산과목 규칙** — 고객이 지목한 6종은 내용과 관계없이 뺍니다.
           ⛔ 부분 문자열이 아니라 **정확히 같은 값**만 봅니다.
        2. **담당자 확정** — 검토 화면에서 사람이 사유와 함께 확정한 건.

        .. warning::
            ⛔ **적요를 보지 않습니다.** `교육`·`강사`·`임차`·`렌트` 같은 낱말로
            빼지 않습니다 — 고객이 지출결의서·품의서를 확인해서 판단한다고 했고,
            그 자료는 시스템에 없습니다(§0.9.5 원칙 1).

        구 스키마 DB(검토 테이블이나 새 컬럼이 없는 경우)에서는 담당자 확정
        조건을 건너뛰고 **기존과 동일하게** 동작합니다.
        """
        conditions: list[str] = []
        params: list[object] = []

        # ① 예산과목 규칙. 앞뒤 공백만 떼고 정확히 비교한다(TRIM).
        placeholders = ", ".join("?" for _ in SORTED_EXCLUDED_BUDGET_ACCOUNTS)
        conditions.append(
            f"(budget_account IS NULL OR TRIM(budget_account) NOT IN ({placeholders}))"
        )
        params.extend(SORTED_EXCLUDED_BUDGET_ACCOUNTS)

        # ② 담당자가 확정한 제외.
        if self._has_performance_status_column():
            conditions.append(
                "NOT EXISTS (SELECT 1 FROM purchase_review r "
                "WHERE r.purchase_id = purchase.purchase_id "
                "AND r.performance_status = ?)"
            )
            params.append(EXCLUDED)

        return conditions, params

    def _has_performance_status_column(self) -> bool:
        """검토 테이블에 ``performance_status`` 컬럼이 있는지 확인합니다.

        구 스키마 DB 에서도 계산이 동작해야 하므로, 조건을 붙이기 전에 확인
        합니다. 없으면 담당자 확정 제외가 없다는 뜻이므로 조건을 붙이지
        않습니다 — 기존 계산 결과가 그대로 유지됩니다.
        """
        tables = self.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'purchase_review'"
        )
        if not tables:
            return False
        columns = {row["name"] for row in self.execute("PRAGMA table_info(purchase_review)")}
        return "performance_status" in columns

    def _has_import_batch_table(self) -> bool:
        """``import_batch`` 테이블이 존재하는지 확인합니다.

        구 스키마 DB 에서도 계산이 동작해야 하므로, 배치 조건을 붙이기 전에
        테이블 존재 여부를 먼저 확인합니다.
        """
        rows = self.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'import_batch'"
        )
        return bool(rows)

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

        # ⛔ contract_date · payment_date 는 **필수가 아니다**(🟢 2026-09-02 PM
        #    확정 · STEP 87). 실적 산정 기준일은 resolution_date 이며, 원본에
        #    없는 날짜 때문에 정상 거래를 미적재시키지 않는다. 값이 없으면
        #    NULL 로 저장하고, 다른 날짜로 채우지 않는다.

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
            contract_date=(_from_db_date(row["contract_date"]) if row["contract_date"] else None),
            payment_date=_from_db_date(row["payment_date"]) if row["payment_date"] else None,
            resolution_date=(
                _from_db_date(row["resolution_date"]) if row["resolution_date"] else None
            ),
            issue_date=_from_db_date(row["issue_date"]) if _optional(row, "issue_date") else None,
            description=_optional(row, "description"),
            budget_account=_optional(row, "budget_account"),
            amount=_from_db_amount(row["amount"]),
            batch_id=row["batch_id"],
            created_at=_from_db(row["created_at"]),
            updated_at=_from_db(row["updated_at"]),
        )
