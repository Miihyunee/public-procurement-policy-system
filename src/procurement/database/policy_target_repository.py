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

from procurement.core.target_scope import TARGET_SCOPES, TOTAL, is_calculable
from procurement.database.base import BaseRepository
from procurement.database.policy_repository import (
    PolicyValidationError,
    validate_target_rate,
)
from procurement.models.policy_target import PolicyTarget

#: 목표비율 테이블.
#:
#: ``UNIQUE (year, policy_id, scope)`` 가 이 테이블의 핵심 제약입니다 — 한 연도의
#: 한 정책에는 **분모 기준마다** 목표비율이 하나씩 존재합니다. 중복 INSERT 는 DB
#: 가 막습니다.
#:
#: .. note::
#:     **``scope`` 가 왜 키에 들어가는가(2026-09-03 · STEP 99 §2).** 원래 키는
#:     ``(year, policy_id)`` 였습니다. 여성기업 목표가 «공사 3% / 용역·물품 5%»
#:     로 **둘**이라는 것이 확정되면서, 정책당 하나만 담는 키로는 표현할 수 없게
#:     되었습니다. ⛔ 둘 중 하나를 고르거나 평균 내지 않고 **키를 넓혔습니다.**
#:     기존 정책은 모두 ``scope='TOTAL'`` 한 행이므로 동작이 달라지지 않습니다.
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
    scope TEXT NOT NULL DEFAULT 'TOTAL',
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    UNIQUE (year, policy_id, scope),
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


def validate_scope(scope: str) -> None:
    """분모 기준이 허용값인지 검증합니다.

    Args:
        scope: 검증할 분모 기준.

    Raises:
        PolicyValidationError: 허용되지 않는 값인 경우.
    """
    if scope not in TARGET_SCOPES:
        allowed = " · ".join(sorted(TARGET_SCOPES))
        raise PolicyValidationError(f"허용되지 않는 분모 기준입니다: {scope!r} (허용: {allowed})")


class PolicyTargetRepository(BaseRepository):
    """``policy_target`` 테이블에 대한 데이터 접근 계층."""

    table_name = "policy_target"

    def create_table(self) -> None:
        """목표비율 테이블을 생성합니다 (없을 때만).

        ``CREATE TABLE IF NOT EXISTS`` 이므로 반복 호출해도 안전하며, 이미
        저장된 목표비율을 건드리지 않습니다.

        ⚠️ STEP 99 이전에 만들어진 DB 에는 ``scope`` 컬럼이 없습니다. 그런 DB 는
        컬럼을 **덧붙이고** 기존 행을 모두 ``TOTAL`` 로 채웁니다 — 그때 저장된
        목표는 전부 기관 전체 구매금액 기준이었기 때문입니다. ⛔ 행을 지우거나
        값을 바꾸지 않습니다.
        """
        with self.connection() as conn:
            conn.execute(CREATE_TABLE_SQL)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(policy_target)")}
            if "scope" in columns:
                return

            # 옛 테이블에는 ``UNIQUE (year, policy_id)`` 가 걸려 있어 한 정책에
            # 분모 기준을 둘 이상 넣을 수 없습니다. 그 제약의 인덱스는 SQLite 가
            # DROP INDEX 로 지우지 못하므로 **테이블을 다시 만들어 옮깁니다.**
            # ⛔ 행을 지우거나 값을 바꾸지 않습니다 — 그때 저장된 목표는 전부
            #    기관 전체 구매금액 기준이었으므로 ``TOTAL`` 로 옮깁니다.
            conn.execute("ALTER TABLE policy_target RENAME TO policy_target_pre_scope")
            conn.execute(CREATE_TABLE_SQL)
            conn.execute(
                "INSERT INTO policy_target "
                "(policy_target_id, year, policy_id, target_rate, scope, created_at, updated_at) "
                "SELECT policy_target_id, year, policy_id, target_rate, ?, created_at, updated_at "
                "FROM policy_target_pre_scope",
                (TOTAL,),
            )
            conn.execute("DROP TABLE policy_target_pre_scope")

    def get(self, year: int, policy_id: int, scope: str = TOTAL) -> PolicyTarget | None:
        """한 연도 · 한 정책 · 한 분모 기준의 목표비율을 조회합니다.

        Args:
            year: 대상 회계연도.
            policy_id: 대상 정책 ID.
            scope: 분모 기준. 기본값은 기관 전체 구매금액(``TOTAL``)이며, 기존
                호출부는 인자를 주지 않아도 예전과 같은 행을 받습니다.

        Returns:
            :class:`PolicyTarget`. **설정되지 않았으면 ``None``** 입니다 —
            ⛔ 다른 연도 값이나 0 으로 대체하지 않습니다.
        """
        rows = self.execute(
            "SELECT * FROM policy_target WHERE year = ? AND policy_id = ? AND scope = ?",
            (year, policy_id, scope),
        )
        return self._row_to_target(rows[0]) if rows else None

    def list_for_policy(self, year: int, policy_id: int) -> list[PolicyTarget]:
        """한 연도 · 한 정책의 목표비율을 **분모 기준별로 모두** 조회합니다.

        여성기업처럼 목표가 여럿인 정책을 화면에 보여 줄 때 씁니다.

        Args:
            year: 대상 회계연도.
            policy_id: 대상 정책 ID.

        Returns:
            :class:`PolicyTarget` 목록. 설정된 것이 없으면 빈 목록입니다.
        """
        rows = self.execute(
            "SELECT * FROM policy_target WHERE year = ? AND policy_id = ? ORDER BY scope",
            (year, policy_id),
        )
        return [self._row_to_target(row) for row in rows]

    def list_by_year(self, year: int) -> list[PolicyTarget]:
        """한 연도에 설정된 목표비율을 **분모 기준 구분 없이 모두** 조회합니다.

        Args:
            year: 대상 회계연도.

        Returns:
            :class:`PolicyTarget` 목록. **설정된 것만** 담기므로, 목표비율이
            없는 정책은 이 목록에 나타나지 않습니다(= 미설정). 여성기업처럼
            목표가 여럿인 정책은 **행이 여럿** 담깁니다.
        """
        rows = self.execute(
            "SELECT * FROM policy_target WHERE year = ? ORDER BY policy_id, scope",
            (year,),
        )
        return [self._row_to_target(row) for row in rows]

    def rates_by_policy_id(self, year: int) -> dict[int, Decimal]:
        """한 연도의 목표비율 중 **계산기가 쓸 수 있는 것만** 반환합니다.

        기존 계산기(:meth:`~procurement.calculators.ProcurementAchievementCalculator.calculate_all`)
        가 받는 모양 ``{policy_id: 목표비율}`` 그대로입니다 — ⛔ 계산기
        시그니처를 바꾸지 않기 위해 여기서 모양을 맞춥니다.

        .. warning::
            ⭐ **분모를 구할 수 없는 목표는 여기에 담지 않습니다.** 계산기는 분모로
            언제나 기관 전체 구매금액을 쓰므로, 여성기업(구매유형별)이나
            자활용사촌(생산가능품목)의 목표를 그대로 넘기면 **틀린 달성률**이
            나옵니다. 그래서 :func:`~procurement.core.target_scope.is_calculable`
            이 참인 분모 기준만 통과시킵니다.

            빠진 정책은 «목표율 미설정» 이 아니라 «계산 보류» 입니다 — 목표는
            저장되어 있고 :meth:`list_for_policy` 로 볼 수 있습니다.

        Args:
            year: 대상 회계연도.

        Returns:
            달성률을 낼 수 있는 정책만 담긴 매핑. 비어 있을 수 있습니다.
        """
        return {
            target.policy_id: target.target_rate
            for target in self.list_by_year(year)
            if is_calculable(target.scope)
        }

    def on_hold_policy_ids(self, year: int) -> set[int]:
        """목표는 **저장되어 있으나 달성률을 낼 수 없는** 정책 ID.

        분모를 구하는 방법이 아직 없는 목표(여성기업 · 자활용사촌)를 화면이
        «계산 보류» 로 구분해 표시하기 위해 씁니다. ⛔ «목표율 미설정» 과 다른
        상태입니다 — 목표는 받았고, 못 내는 것은 분모입니다.

        Args:
            year: 대상 회계연도.

        Returns:
            해당 정책 ID 집합. 계산 가능한 목표도 함께 가진 정책은 제외합니다.
        """
        calculable = set(self.rates_by_policy_id(year))
        return {
            target.policy_id
            for target in self.list_by_year(year)
            if not is_calculable(target.scope) and target.policy_id not in calculable
        }

    def upsert(
        self, year: int, policy_id: int, target_rate: Decimal, scope: str = TOTAL
    ) -> PolicyTarget:
        """목표비율을 저장합니다. 이미 있으면 **값만 바꿉니다.**

        같은 ``(year, policy_id, scope)`` 로 몇 번을 호출해도 행이 하나만 남습니다
        (``UNIQUE (year, policy_id, scope)`` + ``ON CONFLICT ... DO UPDATE``).
        PUT 이 멱등하게 동작해야 하기 때문입니다.

        .. note::
            ``scope`` 를 주지 않으면 기관 전체 구매금액(``TOTAL``) 기준입니다 —
            기존 호출부의 동작이 달라지지 않습니다. 여성기업처럼 목표가 여럿인
            정책은 분모 기준마다 한 번씩 호출해 **각 값을 그대로** 저장합니다.
            ⛔ 둘을 하나로 합치거나 평균 내지 않습니다.

        Args:
            year: 대상 회계연도.
            policy_id: 대상 정책 ID.
            target_rate: 목표 구매비율(%). ``0`` 초과 ``100`` 이하.
            scope: 이 비율을 재는 분모 기준.

        Returns:
            저장된 :class:`PolicyTarget`.

        Raises:
            PolicyValidationError: 연도·분모 기준·목표비율이 허용 범위를
                벗어났거나, 존재하지 않는 정책 ID 인 경우.
        """
        validate_year(year)
        validate_scope(scope)
        # ⛔ 검증 규칙을 새로 만들지 않는다 — 기존 정책 목표율과 같은 함수를 쓴다.
        validate_target_rate(target_rate)
        if target_rate is None:  # pragma: no cover - 타입상 도달하지 않는다
            raise PolicyValidationError("target_rate 는 필수입니다.")

        now = datetime.now()
        sql = (
            "INSERT INTO policy_target "
            "(year, policy_id, target_rate, scope, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (year, policy_id, scope) DO UPDATE SET "
            "target_rate = excluded.target_rate, updated_at = excluded.updated_at"
        )
        params = (year, policy_id, str(target_rate), scope, _to_db(now), _to_db(now))

        with self.connection() as conn:
            try:
                conn.execute(sql, params)
            except sqlite3.IntegrityError as exc:
                raise PolicyValidationError(
                    f"등록되지 않은 정책입니다: policy_id={policy_id}"
                ) from exc

        saved = self.get(year, policy_id, scope)
        assert saved is not None  # 방금 저장했다
        return saved

    def delete(self, year: int, policy_id: int, scope: str = TOTAL) -> bool:
        """목표비율을 **해제**합니다(행을 지웁니다).

        해제는 "값이 0" 이 아니라 **"설정하지 않음"** 입니다. 그래서 0 을 넣지
        않고 행을 지웁니다 — 그래야 조회 결과가 ``None`` 이 되어 기존 미설정
        처리 경로를 그대로 탑니다.

        Args:
            year: 대상 회계연도.
            policy_id: 대상 정책 ID.
            scope: 해제할 분모 기준. 기본값은 ``TOTAL`` 이며, 다른 분모 기준의
                목표는 그대로 남습니다.

        Returns:
            지운 행이 있으면 ``True``, 이미 없었으면 ``False``.
        """
        deleted = self.execute_write(
            "DELETE FROM policy_target WHERE year = ? AND policy_id = ? AND scope = ?",
            (year, policy_id, scope),
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
            scope=row["scope"],
            created_at=_from_db(row["created_at"]),
            updated_at=_from_db(row["updated_at"]),
        )
