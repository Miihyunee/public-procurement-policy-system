"""
procurement.database.bootstrap

프로젝트를 처음 받은 사람이 수작업 없이 바로 실행할 수 있도록, 데이터베이스를
초기화하고 기본 정책을 등록하는 **Project Bootstrap** 기능을 제공합니다.

목표 흐름::

    git clone → install → init → run → GET /dashboard/summary

제공 기능:

- :func:`init_db` — 핵심 테이블을 일괄 생성(멱등)
- :func:`migrate_schema` — 이전 버전 DB 에 누락된 컬럼을 보완(멱등)
- :func:`seed_policies` — MVP 정책 5종을 등록(멱등)
- :func:`verify_bootstrap` — DB·테이블·컬럼·seed 상태를 점검
- :func:`bootstrap` — 위 과정을 순서대로 수행하는 오케스트레이터

.. note::
    모든 기능은 **멱등(idempotent)** 합니다. 반복 실행해도 기존 데이터베이스를
    다시 만들거나 정책을 중복 등록하지 않습니다.

    정책의 ``target_rate`` 는 **NULL 로 등록**합니다. 공식 근거가 확인되지 않은
    목표율을 임의의 숫자로 입력하지 않기 위한 PM 결정(D-004)에 따른 것으로,
    목표율이 없는 정책은 대시보드 계산 대상에서 제외됩니다. 목표율을 등록하면
    별도 조치 없이 자동으로 계산에 포함됩니다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from procurement.core.config import settings
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.import_batch_repository import ImportBatchRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models.policy import Policy


@dataclass(frozen=True, kw_only=True)
class PolicySeed:
    """Bootstrap 이 등록하는 기본 정책 정의.

    Attributes:
        policy_code: 정책 코드(Unique). 확정값이며 변경하지 않습니다.
        policy_name: 정책명.
        evaluation_basis: 판정 기준일 유형(``PAYMENT_DATE`` / ``CONTRACT_DATE``).
        description: 정책 설명.
    """

    policy_code: str
    policy_name: str
    evaluation_basis: str
    description: str


#: MVP 대상 정책 5종. 판정 기준일은 ``docs/POLICY_DEFINITION.md`` 를 따릅니다.
#:
#: ``target_rate`` 는 의도적으로 포함하지 않습니다(NULL 등록).
MVP_POLICY_SEEDS: tuple[PolicySeed, ...] = (
    PolicySeed(
        policy_code="SMALL_BUSINESS",
        policy_name="중소기업",
        evaluation_basis="PAYMENT_DATE",
        description="중소기업제품 우선구매",
    ),
    PolicySeed(
        policy_code="WOMAN",
        policy_name="여성기업",
        evaluation_basis="PAYMENT_DATE",
        description="여성기업제품 우선구매",
    ),
    PolicySeed(
        policy_code="DISABLED",
        policy_name="장애인기업",
        evaluation_basis="PAYMENT_DATE",
        description="장애인기업제품 우선구매",
    ),
    PolicySeed(
        policy_code="STARTUP",
        policy_name="창업기업",
        # 2026-08-14 고객 확정: 결의일자와 계약일자 중 하나라도 인증 유효기간에
        # 해당하면 인정한다(OR 조건). 계약일 단독 기준이 아니다.
        evaluation_basis="PAYMENT_OR_CONTRACT_DATE",
        description="창업기업제품 우선구매(두 날짜 중 하나라도 인증기간에 해당하면 인정)",
    ),
    PolicySeed(
        policy_code="GREEN",
        policy_name="녹색제품",
        evaluation_basis="PAYMENT_DATE",
        description="녹색제품 우선구매(판정 단위는 업무 분석 후 확정)",
    ),
)

#: 시스템 동작에 필요한 테이블과, 각 테이블에서 반드시 존재해야 하는 컬럼.
#: 구(舊) 스키마 DB 를 감지하기 위해 컬럼까지 확인합니다.
_REQUIRED_SCHEMA: dict[str, tuple[str, ...]] = {
    "company": ("company_id", "business_no", "company_name", "representative_name"),
    "policy": ("policy_id", "policy_code", "policy_name", "evaluation_basis", "target_rate"),
    "certification": ("certification_id", "company_id", "policy_id", "valid_from", "valid_to"),
    "purchase": (
        "purchase_id",
        "business_no",
        "contract_date",
        "payment_date",
        "amount",
        "batch_id",
    ),
    "import_batch": (
        "batch_id",
        "file_name",
        "period_start",
        "period_end",
        "status",
        "row_count",
        "total_amount",
    ),
}


@dataclass(frozen=True, kw_only=True)
class HealthCheckItem:
    """Health Check 항목 하나의 결과.

    Attributes:
        name: 점검 항목 이름.
        passed: 통과 여부.
        detail: 사람이 읽을 수 있는 설명. 실패 시 원인과 조치를 담습니다.
    """

    name: str
    passed: bool
    detail: str


@dataclass(frozen=True, kw_only=True)
class HealthReport:
    """Health Check 전체 결과.

    Attributes:
        db_path: 점검한 데이터베이스 경로.
        items: 항목별 점검 결과.
    """

    db_path: Path
    items: list[HealthCheckItem] = field(default_factory=list)

    @property
    def healthy(self) -> bool:
        """모든 항목을 통과했는지 여부."""
        return all(item.passed for item in self.items)

    def format_report(self) -> str:
        """콘솔 출력용 문자열로 변환합니다."""
        lines = [f"DB: {self.db_path}"]
        for item in self.items:
            mark = "OK  " if item.passed else "FAIL"
            lines.append(f"  [{mark}] {item.name} — {item.detail}")
        lines.append("결과: 정상" if self.healthy else "결과: 실패 — 위 항목을 확인하세요.")
        return "\n".join(lines)


def resolve_db_path(db_path: str | Path | None = None) -> Path:
    """사용할 DB 경로를 결정합니다.

    Args:
        db_path: 명시적으로 지정한 경로. ``None`` 이면 설정값(``settings.db_file``)을
            사용합니다.

    Returns:
        최종 DB 파일 경로.
    """
    return Path(db_path) if db_path is not None else settings.db_file


def init_db(db_path: str | Path | None = None) -> None:
    """핵심 테이블을 생성합니다.

    각 Repository 의 ``create_table()`` 은 ``CREATE TABLE IF NOT EXISTS`` 를
    사용하므로 **반복 실행해도 안전**하며, 기존 데이터를 삭제하지 않습니다.
    DB 파일과 상위 디렉터리는 연결 시점에 자동 생성됩니다.

    생성 후 :func:`migrate_schema` 를 호출해, 이전 버전에서 만든 DB 에 추가된
    컬럼을 보완합니다.

    Args:
        db_path: 대상 DB 경로. ``None`` 이면 설정값을 사용합니다.
    """
    path = resolve_db_path(db_path)
    CompanyRepository(path).create_table()
    PolicyRepository(path).create_table()
    CertificationRepository(path).create_table()
    PurchaseRepository(path).create_table()
    ImportBatchRepository(path).create_table()
    migrate_schema(path)
    # 인덱스는 컬럼 보완 이후에 만든다(구 스키마 DB 대응).
    PurchaseRepository(path).ensure_indexes()


#: 기존 테이블에 나중에 추가된 컬럼. ``CREATE TABLE IF NOT EXISTS`` 로는 추가되지
#: 않으므로 ``ALTER TABLE`` 로 보완한다. (테이블, 컬럼, 컬럼 정의)
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ("purchase", "batch_id", "INTEGER"),
)


def migrate_schema(db_path: str | Path | None = None) -> list[str]:
    """이전 버전에서 만든 DB 에 누락된 컬럼을 추가합니다(멱등).

    ``CREATE TABLE IF NOT EXISTS`` 는 **기존 테이블에 컬럼을 추가하지 않습니다.**
    따라서 나중에 추가된 컬럼은 ``ALTER TABLE ... ADD COLUMN`` 으로 보완해야
    합니다. 이미 있는 컬럼은 건너뛰므로 반복 실행해도 안전합니다.

    기존 행의 새 컬럼 값은 ``NULL`` 이 됩니다. ``purchase.batch_id`` 가 ``NULL``
    인 행은 **계산에 계속 포함**되므로(배치 도입 이전 데이터 보호), 기존 계산
    결과가 달라지지 않습니다.

    Args:
        db_path: 대상 DB 경로. ``None`` 이면 설정값을 사용합니다.

    Returns:
        이번 호출에서 실제로 추가한 컬럼 목록(``"테이블.컬럼"`` 형식).
    """
    path = resolve_db_path(db_path)
    existing = _read_schema(path)

    added: list[str] = []
    with sqlite3.connect(path) as conn:
        for table, column, definition in _ADDED_COLUMNS:
            if table not in existing or column in existing[table]:
                continue
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            added.append(f"{table}.{column}")
    return added


#: 고객 확정으로 판정 기준이 바뀐 정책. ``(정책코드, 이전 값, 새 값)``.
#:
#: ``seed_policies()`` 는 **이미 존재하는 정책을 건너뛰므로**, 기존 DB 의 값은
#: seed 상수를 고쳐도 그대로 남는다. 확정된 업무규칙이 기존 DB 에서만 적용되지
#: 않는 상태를 막기 위해 명시적으로 갱신한다.
_UPDATED_EVALUATION_BASIS: tuple[tuple[str, str, str], ...] = (
    # 2026-08-14 고객 확정 — 창업기업은 결의일자 OR 계약일자로 판정한다.
    ("STARTUP", "CONTRACT_DATE", "PAYMENT_OR_CONTRACT_DATE"),
)


def migrate_policy_evaluation_basis(db_path: str | Path | None = None) -> list[str]:
    """고객 확정으로 바뀐 정책 판정 기준을 기존 DB 에 반영합니다(멱등).

    **이전 값과 정확히 일치하는 행만** 갱신합니다. 운영자가 다른 값으로 바꿔 둔
    경우에는 건드리지 않습니다.

    .. note::
        판정 기준(``evaluation_basis``)은 시스템이 소유하는 값입니다. 관리 API
        (``PolicyAdminService``)는 ``target_rate`` 만 변경하므로, 이 갱신이
        운영자가 설정한 값을 덮어쓰지 않습니다.

        구매·인증 데이터는 **전혀 건드리지 않습니다.**

    Args:
        db_path: 대상 DB 경로. ``None`` 이면 설정값을 사용합니다.

    Returns:
        이번 호출에서 실제로 갱신한 항목 목록(``"정책코드: 이전→새값"`` 형식).
    """
    path = resolve_db_path(db_path)
    if "policy" not in _read_schema(path):
        return []

    updated: list[str] = []
    with sqlite3.connect(path) as conn:
        for policy_code, old_value, new_value in _UPDATED_EVALUATION_BASIS:
            cursor = conn.execute(
                "UPDATE policy SET evaluation_basis = ? "
                "WHERE policy_code = ? AND evaluation_basis = ?",
                (new_value, policy_code, old_value),
            )
            if cursor.rowcount:
                updated.append(f"{policy_code}: {old_value}→{new_value}")
    return updated


def seed_policies(db_path: str | Path | None = None) -> list[str]:
    """MVP 정책 5종을 등록합니다(멱등).

    이미 같은 ``policy_code`` 가 존재하면 건너뛰므로 반복 실행해도 중복 등록되지
    않으며, 기존 정책의 값(특히 운영자가 설정한 ``target_rate``)을 덮어쓰지
    않습니다.

    ``target_rate`` 는 **NULL 로 등록**합니다(PM 결정 D-004). 목표율이 없는 정책은
    대시보드 계산에서 제외되며, 이후 목표율을 등록하면 자동으로 포함됩니다.

    Args:
        db_path: 대상 DB 경로. ``None`` 이면 설정값을 사용합니다.

    Returns:
        이번 호출에서 **새로 등록한** 정책 코드 목록. 모두 이미 존재하면 빈 목록.
    """
    repository = PolicyRepository(resolve_db_path(db_path))

    created: list[str] = []
    for seed in MVP_POLICY_SEEDS:
        if repository.exists(seed.policy_code):
            continue
        repository.insert(
            Policy(
                policy_code=seed.policy_code,
                policy_name=seed.policy_name,
                description=seed.description,
                evaluation_basis=seed.evaluation_basis,
                target_rate=None,  # D-004: 임의의 목표율을 입력하지 않는다.
            )
        )
        created.append(seed.policy_code)
    return created


def verify_bootstrap(db_path: str | Path | None = None) -> HealthReport:
    """초기화 상태를 점검합니다.

    다음을 확인하고 항목별 결과를 반환합니다.

    1. DB 파일 존재
    2. 필수 테이블 존재
    3. 각 테이블의 필수 컬럼 존재(구 스키마 DB 감지)
    4. MVP 정책 seed 존재

    Args:
        db_path: 대상 DB 경로. ``None`` 이면 설정값을 사용합니다.

    Returns:
        점검 결과 :class:`HealthReport`.
    """
    path = resolve_db_path(db_path)
    items: list[HealthCheckItem] = []

    if not path.exists():
        items.append(
            HealthCheckItem(
                name="DB 파일",
                passed=False,
                detail=f"파일이 없습니다: {path}. 'python -m procurement init' 을 실행하세요.",
            )
        )
        return HealthReport(db_path=path, items=items)

    items.append(HealthCheckItem(name="DB 파일", passed=True, detail=str(path)))

    existing_columns = _read_schema(path)

    missing_tables = [table for table in _REQUIRED_SCHEMA if table not in existing_columns]
    if missing_tables:
        items.append(
            HealthCheckItem(
                name="테이블",
                passed=False,
                detail=(
                    f"누락된 테이블: {', '.join(sorted(missing_tables))}. "
                    "'python -m procurement init' 을 실행하세요."
                ),
            )
        )
    else:
        items.append(
            HealthCheckItem(
                name="테이블",
                passed=True,
                detail=f"{len(_REQUIRED_SCHEMA)}개 테이블 존재",
            )
        )

    missing_columns = _find_missing_columns(existing_columns)
    if missing_columns:
        items.append(
            HealthCheckItem(
                name="스키마(컬럼)",
                passed=False,
                detail=(
                    f"누락된 컬럼: {missing_columns}. 이전 버전에서 만든 DB 로 보입니다. "
                    "'python -m procurement init' 을 실행하면 누락된 컬럼만 추가하며, "
                    "기존 데이터는 삭제하지 않습니다."
                ),
            )
        )
    elif not missing_tables:
        items.append(HealthCheckItem(name="스키마(컬럼)", passed=True, detail="필수 컬럼 확인됨"))

    items.append(_check_policy_seed(path, seeded_tables_ok=not missing_tables))
    return HealthReport(db_path=path, items=items)


def bootstrap(db_path: str | Path | None = None, *, seed: bool = True) -> HealthReport:
    """초기화 → 정책 등록 → 점검을 순서대로 수행합니다.

    Args:
        db_path: 대상 DB 경로. ``None`` 이면 설정값을 사용합니다.
        seed: ``False`` 이면 정책 등록을 건너뛰고 스키마만 초기화합니다.

    Returns:
        초기화 후의 :class:`HealthReport`.
    """
    init_db(db_path)
    if seed:
        seed_policies(db_path)
        # seed 는 기존 정책을 건너뛰므로, 확정으로 바뀐 판정 기준은 따로 반영한다.
        migrate_policy_evaluation_basis(db_path)
    return verify_bootstrap(db_path)


# ----------------------------------------------------------------------
# 내부 헬퍼
# ----------------------------------------------------------------------
def _read_schema(path: Path) -> dict[str, set[str]]:
    """DB 에 존재하는 테이블과 각 테이블의 컬럼명을 조회합니다."""
    schema: dict[str, set[str]] = {}
    with sqlite3.connect(str(path)) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
        for (table_name,) in rows:
            if table_name not in _REQUIRED_SCHEMA:
                continue
            columns = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
            schema[table_name] = {column[1] for column in columns}
    return schema


def _find_missing_columns(existing_columns: dict[str, set[str]]) -> str:
    """필수 컬럼 중 누락된 항목을 ``table.column`` 형태 문자열로 반환합니다."""
    missing: list[str] = []
    for table, required in _REQUIRED_SCHEMA.items():
        actual = existing_columns.get(table)
        if actual is None:
            continue  # 테이블 자체가 없는 경우는 테이블 점검에서 보고한다.
        missing.extend(f"{table}.{column}" for column in required if column not in actual)
    return ", ".join(missing)


def _check_policy_seed(path: Path, *, seeded_tables_ok: bool) -> HealthCheckItem:
    """MVP 정책 seed 가 모두 등록되어 있는지 확인합니다."""
    if not seeded_tables_ok:
        return HealthCheckItem(
            name="정책 seed",
            passed=False,
            detail="테이블이 준비되지 않아 확인할 수 없습니다.",
        )

    repository = PolicyRepository(path)
    missing = [
        seed.policy_code for seed in MVP_POLICY_SEEDS if not repository.exists(seed.policy_code)
    ]
    if missing:
        return HealthCheckItem(
            name="정책 seed",
            passed=False,
            detail=(
                f"누락된 정책: {', '.join(missing)}. "
                "'python -m procurement init' 을 실행하세요."
            ),
        )
    return HealthCheckItem(
        name="정책 seed",
        passed=True,
        detail=f"MVP 정책 {len(MVP_POLICY_SEEDS)}종 등록됨(target_rate 미설정)",
    )
