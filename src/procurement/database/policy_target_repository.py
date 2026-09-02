"""
procurement.database.policy_target_repository

**연도별 · 정책별 목표비율**의 저장/조회를 담당하는 Repository.

.. warning::
    ⛔ **구매처(``Company``)별 목표비율을 저장하지 않습니다.** 이 테이블의 키는
    ``(year, policy_id)`` 뿐이며, 사업자등록번호로 목표비율을 나누지 않습니다
    (``DECISIONS.md`` §0.20).

.. warning::
    ⛔ **연도끼리 값을 빌려오지 않습니다.** 2026년 목표가 없으면 2025년 값을
    끌어다 쓰지 않고 **미설정**입니다. 없는 목표를 다른 값으로 메우면 "설정하지
    않았다" 와 "설정했다" 를 구분할 수 없게 됩니다.

.. note::
    목표비율 값 검증은 :func:`~procurement.database.policy_repository.validate_target_rate`
    를 **재사용**합니다. 규칙을 두 벌로 두면 한쪽으로 우회해 잘못된 값이 들어갑니다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal

from procurement.database.base import BaseRepository
from procurement.database.policy_repository import (
    PolicyValidationError,
    validate_target_rate,
)
from procurement.models.policy_target import PolicyTarget

#: 목표비율 테이블.
#:
#: ``UNIQUE (year, policy_id)`` 가 이 테이블의 핵심 제약입니다 — 한 연도의 한
#: 정책에는 목표비율이 **하나만** 존재합니다. 중복 INSERT 는 DB 가 막습니다.
#:
#: ``target_rate`` 를 TEXT 로 두는 것은 기존 ``policy.target_rate`` 와 같은
#: 방식입니다. REAL 로 두면 부동소수 오차가 생겨 ``37.5`` 가 그대로 돌아오지
#: 않습니다.
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS policy_target (
    policy_target_id INTEGER PRIMARY KEY,
    year INTEGER NOT NULL,
    policy_id INTEGER NOT NULL,
    target_rate TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE (year, policy_id),
    FOREIGN KEY (policy_id) REFERENCES policy (policy_id)
)
"""

#: 허용 연도 범위. 기존 대시보드 API 의 연도 검증(``ge=1900, le=2999``)과 맞춥니다.
YEAR_MIN = 1900
YEAR_MAX = 2999


def _to_db(value: datetime) -> str:
    """datetime 을 SQLite 저장용 ISO 문자열로 변환합니다."""
    return value.isoformat(sep=" ")


def _from_db(value: str) -> datetime:
    """SQLite 에서 읽은 ISO 문자열을 datetime 으로 변환합니다."""
    return datetime.fromisoformat(value)


def validate_year(year: int) -> None:
    """연도가 허용 범위 안인지 검증합니다.

    Args:
        year: 검증할 연도.

    Raises:
        PolicyValidationError: 범위를 벗어난 경우.
    """
    if not YEAR_MIN <= year <= YEAR_MAX:
        raise PolicyValidationError(f"연도는 {YEAR_MIN} ~ {YEAR_MAX} 사이여야 합니다: {year}")


class PolicyTargetRepository(BaseRepository):
    """``policy_target`` 테이블에 대한 데이터 접근 계층."""

    table_name = "policy_target"

    def create_table(self) -> None:
        """목표비율 테이블을 생성합니다 (없을 때만).

        ``CREATE TABLE IF NOT EXISTS`` 이므로 반복 호출해도 안전하며, 이미
        저장된 목표비율을 건드리지 않습니다.
        """
        with self.connection() as conn:
            conn.execute(CREATE_TABLE_SQL)

    def get(self, year: int, policy_id: int) -> PolicyTarget | None:
        """한 연도 · 한 정책의 목표비율을 조회합니다.

        Args:
            year: 대상 회계연도.
            policy_id: 대상 정책 ID.

        Returns:
            :class:`PolicyTarget`. **설정되지 않았으면 ``None``** 입니다 —
            ⛔ 다른 연도 값이나 0 으로 대체하지 않습니다.
        """
        rows = self.execute(
            "SELECT * FROM policy_target WHERE year = ? AND policy_id = ?",
            (year, policy_id),
        )
        return self._row_to_target(rows[0]) if rows else None

    def list_by_year(self, year: int) -> list[PolicyTarget]:
        """한 연도에 설정된 목표비율을 모두 조회합니다.

        Args:
            year: 대상 회계연도.

        Returns:
            :class:`PolicyTarget` 목록. **설정된 것만** 담기므로, 목표비율이
            없는 정책은 이 목록에 나타나지 않습니다(= 미설정).
        """
        rows = self.execute(
            "SELECT * FROM policy_target WHERE year = ? ORDER BY policy_id",
            (year,),
        )
        return [self._row_to_target(row) for row in rows]

    def rates_by_policy_id(self, year: int) -> dict[int, Decimal]:
        """한 연도의 목표비율을 ``{policy_id: 목표비율}`` 로 반환합니다.

        기존 계산기(:meth:`~procurement.calculators.ProcurementAchievementCalculator.calculate_all`)
        가 받는 모양 그대로입니다 — ⛔ 계산기 시그니처를 바꾸지 않기 위해 여기서
        모양을 맞춥니다.

        Args:
            year: 대상 회계연도.

        Returns:
            목표비율이 **설정된 정책만** 담긴 매핑. 비어 있을 수 있습니다.
        """
        return {target.policy_id: target.target_rate for target in self.list_by_year(year)}

    def upsert(self, year: int, policy_id: int, target_rate: Decimal) -> PolicyTarget:
        """목표비율을 저장합니다. 이미 있으면 **값만 바꿉니다.**

        같은 ``(year, policy_id)`` 로 몇 번을 호출해도 행이 하나만 남습니다
        (``UNIQUE (year, policy_id)`` + ``ON CONFLICT ... DO UPDATE``). PUT 이
        멱등하게 동작해야 하기 때문입니다.

        Args:
            year: 대상 회계연도.
            policy_id: 대상 정책 ID.
            target_rate: 목표 구매비율(%). ``0`` 초과 ``100`` 이하.

        Returns:
            저장된 :class:`PolicyTarget`.

        Raises:
            PolicyValidationError: 연도가 범위를 벗어났거나, 목표비율이 허용
                범위를 벗어났거나, 존재하지 않는 정책 ID 인 경우.
        """
        validate_year(year)
        # ⛔ 검증 규칙을 새로 만들지 않는다 — 기존 정책 목표율과 같은 함수를 쓴다.
        validate_target_rate(target_rate)
        if target_rate is None:  # pragma: no cover - 타입상 도달하지 않는다
            raise PolicyValidationError("target_rate 는 필수입니다.")

        now = datetime.now()
        sql = (
            "INSERT INTO policy_target "
            "(year, policy_id, target_rate, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (year, policy_id) DO UPDATE SET "
            "target_rate = excluded.target_rate, updated_at = excluded.updated_at"
        )
        params = (year, policy_id, str(target_rate), _to_db(now), _to_db(now))

        with self.connection() as conn:
            try:
                conn.execute(sql, params)
            except sqlite3.IntegrityError as exc:
                raise PolicyValidationError(
                    f"등록되지 않은 정책입니다: policy_id={policy_id}"
                ) from exc

        saved = self.get(year, policy_id)
        assert saved is not None  # 방금 저장했다
        return saved

    def delete(self, year: int, policy_id: int) -> bool:
        """목표비율을 **해제**합니다(행을 지웁니다).

        해제는 "값이 0" 이 아니라 **"설정하지 않음"** 입니다. 그래서 0 을 넣지
        않고 행을 지웁니다 — 그래야 조회 결과가 ``None`` 이 되어 기존 미설정
        처리 경로를 그대로 탑니다.

        Args:
            year: 대상 회계연도.
            policy_id: 대상 정책 ID.

        Returns:
            지운 행이 있으면 ``True``, 이미 없었으면 ``False``.
        """
        deleted = self.execute_write(
            "DELETE FROM policy_target WHERE year = ? AND policy_id = ?",
            (year, policy_id),
        )
        return deleted > 0

    def count(self) -> int:
        """저장된 목표비율 건수."""
        rows = self.execute("SELECT COUNT(*) AS cnt FROM policy_target")
        return int(rows[0]["cnt"])

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_target(row: sqlite3.Row) -> PolicyTarget:
        """조회 행을 :class:`PolicyTarget` 으로 변환합니다."""
        return PolicyTarget(
            policy_target_id=row["policy_target_id"],
            year=row["year"],
            policy_id=row["policy_id"],
            target_rate=Decimal(row["target_rate"]),
            created_at=_from_db(row["created_at"]),
            updated_at=_from_db(row["updated_at"]),
        )
