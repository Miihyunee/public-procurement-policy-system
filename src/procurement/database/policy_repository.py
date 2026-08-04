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
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL
)
"""

# 필수 입력값 (policy_code 는 UNIQUE 제약과 별개로 비어 있지 않아야 함)
_REQUIRED_FIELDS = ("policy_code", "policy_name")

# evaluation_basis 허용 값 (MVP). VENDOR_EXISTENCE 는 이번 범위에 포함하지 않는다.
ALLOWED_EVALUATION_BASIS = ("PAYMENT_DATE", "CONTRACT_DATE")


def _to_db(value: datetime) -> str:
    """datetime 을 SQLite 저장용 ISO 문자열로 변환합니다."""
    return value.isoformat(sep=" ")


def _from_db(value: str) -> datetime:
    """SQLite 에서 읽은 ISO 문자열을 datetime 으로 변환합니다."""
    return datetime.fromisoformat(value)


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
                (``PAYMENT_DATE`` / ``CONTRACT_DATE``) 이 아닌 경우.
            DuplicatePolicyCodeError: 동일한 정책 코드가 이미 존재하는 경우.
        """
        self._validate_required(policy)

        now = datetime.now()
        created_at = policy.created_at or now
        updated_at = policy.updated_at or now

        sql = (
            "INSERT INTO policy "
            "(policy_code, policy_name, description, is_active, evaluation_basis, "
            "created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            policy.policy_code,
            policy.policy_name,
            policy.description,
            int(policy.is_active),
            policy.evaluation_basis,
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
            created_at=_from_db(row["created_at"]),
            updated_at=_from_db(row["updated_at"]),
        )
