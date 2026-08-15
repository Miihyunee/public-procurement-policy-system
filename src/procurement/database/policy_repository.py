"""
procurement.database.policy_repository

Policy 엔티티의 영속화(저장/조회)를 담당하는 Repository.

:class:`procurement.database.base.BaseRepository` 를 상속하며, SQLite 표준 SQL
만 사용합니다. 테이블 컬럼은 ``docs/DATABASE_DESIGN.md`` 의 Policy 정의를
그대로 따르고, 설계에 없는 컬럼은 추가하지 않습니다.

.. note::
    본 Repository 는 Foundation 단계 범위로, Insert/조회/집계만 제공합니다.
    Update/Delete 및 비즈니스 로직은 이후 Issue 에서 구현합니다.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal

from procurement.database.base import BaseRepository
from procurement.models.policy import Policy


class PolicyValidationError(ValueError):
    """필수값 누락 등 Policy 데이터 검증 실패 시 발생하는 예외."""


class DuplicatePolicyCodeError(Exception):
    """이미 등록된 정책 코드로 저장을 시도할 때 발생하는 예외."""


# DATABASE_DESIGN.md v1.1 의 Policy 테이블 정의를 그대로 반영한다.
CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS policy (
    policy_id INTEGER PRIMARY KEY,
    policy_code TEXT UNIQUE NOT NULL,
    policy_name TEXT NOT NULL,
    description TEXT,
    is_active BOOLEAN NOT NULL,
    evaluation_basis TEXT NOT NULL,
    target_rate TEXT,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""

# 필수 입력값 (policy_code 는 UNIQUE 제약과 별개로 비어 있지 않아야 함)
_REQUIRED_FIELDS = ("policy_code", "policy_name")

# evaluation_basis 허용 값 (MVP). VENDOR_EXISTENCE 는 이번 범위에 포함하지 않는다.
#
# PAYMENT_OR_CONTRACT_DATE 는 2026-08-14 고객 확정 규칙(창업기업)이며, 구매의 두
# 날짜 중 하나라도 인증 유효기간에 해당하면 인정한다는 뜻이다.
ALLOWED_EVALUATION_BASIS = ("PAYMENT_DATE", "CONTRACT_DATE", "PAYMENT_OR_CONTRACT_DATE")

#: 목표율 상한.
#:
#: .. warning::
#:     이 값은 **정부 정책상 법정 상한이 아니다.** 현재 시스템은 구매비율을
#:     ``정책 인정 구매액 ÷ 전체 구매액 × 100`` 으로 정의하므로 구매비율이 100 을
#:     넘을 수 없고, 목표율이 100 을 초과하면 달성이 구조적으로 불가능해진다.
#:     즉 현재 분모 정의에서 파생되는 **구조적 상한**이다. 분모 구조가 다른 정책을
#:     도입할 때 이 제약을 재검토한다.
TARGET_RATE_MAX = Decimal("100")


def _to_db(value: datetime) -> str:
    """datetime 을 SQLite 저장용 ISO 문자열로 변환합니다."""
    return value.isoformat(sep=" ")


def _from_db(value: str) -> datetime:
    """SQLite 에서 읽은 ISO 문자열을 datetime 으로 변환합니다."""
    return datetime.fromisoformat(value)


def _rate_to_db(value: Decimal | None) -> str | None:
    """목표율(Decimal)을 SQLite 저장용 문자열로 변환합니다 (미설정은 ``None``)."""
    return None if value is None else str(value)


def _rate_from_db(value: str | None) -> Decimal | None:
    """SQLite 에서 읽은 목표율 문자열을 Decimal 로 변환합니다 (NULL 은 ``None``)."""
    return None if value is None else Decimal(value)


def validate_target_rate(target_rate: Decimal | None) -> None:
    """목표율 값이 허용 범위 안인지 검증합니다.

    ``None`` 은 **목표율 미설정**을 뜻하는 정상 값이므로 통과시킵니다.

    등록(:meth:`PolicyRepository.insert`)과 변경
    (:meth:`PolicyRepository.update_target_rate`) 두 경로가 같은 규칙을 쓰도록
    한곳에 모았습니다. 경로마다 규칙이 갈라지면 한쪽으로 우회해 잘못된 목표율을
    저장할 수 있기 때문입니다.

    Args:
        target_rate: 검증할 목표율. ``None`` 이면 미설정으로 간주해 통과합니다.

    Raises:
        PolicyValidationError: 0 이하이거나 :data:`TARGET_RATE_MAX` 를 초과한 경우.
    """
    if target_rate is None:
        return
    if target_rate <= 0:
        raise PolicyValidationError(f"target_rate 는 0 보다 커야 합니다: {target_rate}")
    if target_rate > TARGET_RATE_MAX:
        raise PolicyValidationError(
            f"target_rate 는 {TARGET_RATE_MAX} 이하여야 합니다: {target_rate} "
            "(구매비율이 100 을 넘을 수 없는 현재 계산 구조에서 파생된 상한입니다)"
        )


class PolicyRepository(BaseRepository):
    """Policy 테이블에 대한 데이터 접근 계층."""

    table_name = "policy"

    def create_table(self) -> None:
        """Policy 테이블을 생성합니다 (없을 때만).

        ``CREATE TABLE IF NOT EXISTS`` 를 사용하므로 반복 호출해도 안전합니다.
        """
        with self.connection() as conn:
            conn.execute(CREATE_TABLE_SQL)

    def insert(self, policy: Policy) -> Policy:
        """정책을 등록하고 채번된 ``policy_id`` 와 타임스탬프를 반영해 반환합니다.

        Args:
            policy: 저장할 :class:`Policy`. ``policy_id`` 는 무시되고 자동 채번됩니다.

        Returns:
            ``policy_id`` / ``created_at`` / ``updated_at`` 가 채워진 새 :class:`Policy`.

        Raises:
            PolicyValidationError: 필수값(정책 코드·정책명)이 비어 있거나
                ``is_active`` 가 ``None`` 이거나, ``evaluation_basis`` 가 허용값
                (``PAYMENT_DATE`` / ``CONTRACT_DATE``) 이 아니거나, ``target_rate``
                가 0 이하인 경우.
            DuplicatePolicyCodeError: 동일한 정책 코드가 이미 존재하는 경우.
        """
        self._validate_required(policy)

        now = datetime.now()
        created_at = policy.created_at or now
        updated_at = policy.updated_at or now

        sql = (
            "INSERT INTO policy "
            "(policy_code, policy_name, description, is_active, evaluation_basis, "
            "target_rate, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            policy.policy_code,
            policy.policy_name,
            policy.description,
            int(policy.is_active),
            policy.evaluation_basis,
            _rate_to_db(policy.target_rate),
            _to_db(created_at),
            _to_db(updated_at),
        )

        with self.connection() as conn:
            try:
                cursor = conn.execute(sql, params)
            except sqlite3.IntegrityError as exc:
                raise DuplicatePolicyCodeError(
                    f"이미 등록된 정책 코드입니다: {policy.policy_code}"
                ) from exc
            new_id = cursor.lastrowid

        return Policy(
            policy_id=new_id,
            policy_code=policy.policy_code,
            policy_name=policy.policy_name,
            description=policy.description,
            is_active=policy.is_active,
            evaluation_basis=policy.evaluation_basis,
            target_rate=policy.target_rate,
            created_at=created_at,
            updated_at=updated_at,
        )

    def find_by_policy_code(self, policy_code: str) -> Policy | None:
        """정책 코드로 정책을 조회합니다.

        Args:
            policy_code: 조회할 정책 코드.

        Returns:
            일치하는 :class:`Policy`, 없으면 ``None``.
        """
        rows = self.execute("SELECT * FROM policy WHERE policy_code = ?", (policy_code,))
        return self._row_to_policy(rows[0]) if rows else None

    def find_by_id(self, policy_id: int) -> Policy | None:
        """policy_id 로 정책을 조회합니다.

        Args:
            policy_id: 조회할 내부 고유 ID.

        Returns:
            일치하는 :class:`Policy`, 없으면 ``None``.
        """
        rows = self.execute("SELECT * FROM policy WHERE policy_id = ?", (policy_id,))
        return self._row_to_policy(rows[0]) if rows else None

    def find_active(self) -> list[Policy]:
        """활성 정책을 목표율 설정 여부와 무관하게 모두 조회합니다.

        ``is_active = 1`` 인 정책을 ``policy_id`` 오름차순으로 반환합니다.
        목표율(``target_rate``)이 설정되지 않은 정책도 포함되므로, 대시보드에서
        **"목표율 미설정" 상태를 표시**할 때 사용합니다.

        목표율이 설정된 정책만 필요하면 :meth:`find_active_with_target_rate` 를
        사용합니다.

        Returns:
            활성 :class:`Policy` 목록. 없으면 빈 목록.
        """
        rows = self.execute("SELECT * FROM policy WHERE is_active = 1 ORDER BY policy_id")
        return [self._row_to_policy(row) for row in rows]

    def find_active_with_target_rate(self) -> list[Policy]:
        """목표율이 설정된 활성 정책을 조회합니다.

        ``is_active = 1`` 이고 ``target_rate`` 가 NULL 이 아닌 정책만
        ``policy_id`` 오름차순으로 반환합니다. 목표율 기반 대시보드 계산
        (:class:`~procurement.dashboard.data_service.DashboardDataService`)에서
        외부 입력 없이 목표율을 확보하기 위한 조회입니다.

        Returns:
            목표율이 설정된 활성 :class:`Policy` 목록. 없으면 빈 목록.
        """
        rows = self.execute(
            "SELECT * FROM policy "
            "WHERE is_active = 1 AND target_rate IS NOT NULL "
            "ORDER BY policy_id"
        )
        return [self._row_to_policy(row) for row in rows]

    def find_all(self) -> list[Policy]:
        """모든 정책을 활성 여부와 무관하게 조회합니다.

        비활성 정책의 목표율은 변경할 수 없지만, 조회에서까지 감추면 운영자가
        **왜 변경되지 않는지 확인할 방법이 없습니다.** 따라서 목표율 관리 화면은
        전체를 보여주고 ``is_active`` 로 구분합니다.

        Returns:
            전체 :class:`Policy` 목록(``policy_id`` 오름차순). 없으면 빈 목록.
        """
        rows = self.execute("SELECT * FROM policy ORDER BY policy_id")
        return [self._row_to_policy(row) for row in rows]

    def update_target_rate(self, policy_code: str, target_rate: Decimal | None) -> Policy | None:
        """정책의 목표율을 변경합니다.

        ``target_rate`` 에 ``None`` 을 주면 **목표율을 해제**합니다(미설정 상태로
        되돌림). 목표율 미설정은 시스템에서 정상 상태이며, 대시보드는 이를
        ``TARGET_RATE_NOT_SET`` 으로 표시합니다.

        ``updated_at`` 을 함께 갱신합니다. ``created_at`` 은 변경하지 않습니다.

        Args:
            policy_code: 변경할 정책 코드.
            target_rate: 새 목표율. ``None`` 이면 미설정으로 되돌립니다.

        Returns:
            변경된 :class:`Policy`. 해당 정책 코드가 없으면 ``None``.

        Raises:
            PolicyValidationError: ``target_rate`` 가 허용 범위를 벗어난 경우
                (:func:`validate_target_rate` 와 동일 규칙).
        """
        validate_target_rate(target_rate)

        updated_at = datetime.now()
        with self.connection() as conn:
            cursor = conn.execute(
                "UPDATE policy SET target_rate = ?, updated_at = ? WHERE policy_code = ?",
                (_rate_to_db(target_rate), _to_db(updated_at), policy_code),
            )
            if cursor.rowcount == 0:
                return None

        return self.find_by_policy_code(policy_code)

    def exists(self, policy_code: str) -> bool:
        """해당 정책 코드의 정책이 존재하는지 확인합니다.

        Args:
            policy_code: 확인할 정책 코드.

        Returns:
            존재하면 ``True``, 아니면 ``False``.
        """
        rows = self.execute("SELECT 1 FROM policy WHERE policy_code = ? LIMIT 1", (policy_code,))
        return len(rows) > 0

    def count(self) -> int:
        """등록된 정책 수를 반환합니다.

        Returns:
            policy 테이블의 전체 행 수.
        """
        rows = self.execute("SELECT COUNT(*) AS cnt FROM policy")
        return int(rows[0]["cnt"])

    # ------------------------------------------------------------------
    # 내부 헬퍼
    # ------------------------------------------------------------------
    def _validate_required(self, policy: Policy) -> None:
        """필수 입력값과 evaluation_basis 허용값을 검증합니다."""
        for field in _REQUIRED_FIELDS:
            value = getattr(policy, field)
            if value is None or not str(value).strip():
                raise PolicyValidationError(f"필수값이 누락되었습니다: {field}")
        if policy.is_active is None:
            raise PolicyValidationError("필수값이 누락되었습니다: is_active")
        if policy.evaluation_basis not in ALLOWED_EVALUATION_BASIS:
            raise PolicyValidationError(
                "evaluation_basis 는 "
                f"{', '.join(ALLOWED_EVALUATION_BASIS)} 만 허용됩니다: "
                f"{policy.evaluation_basis!r}"
            )
        # target_rate 는 선택 항목(NULL 허용). 값이 있으면 허용 범위 안이어야 한다.
        validate_target_rate(policy.target_rate)

    @staticmethod
    def _row_to_policy(row: sqlite3.Row) -> Policy:
        """SQLite Row 를 :class:`Policy` 로 변환합니다."""
        return Policy(
            policy_id=row["policy_id"],
            policy_code=row["policy_code"],
            policy_name=row["policy_name"],
            description=row["description"],
            is_active=bool(row["is_active"]),
            evaluation_basis=row["evaluation_basis"],
            target_rate=_rate_from_db(row["target_rate"]),
            created_at=_from_db(row["created_at"]),
            updated_at=_from_db(row["updated_at"]),
        )
