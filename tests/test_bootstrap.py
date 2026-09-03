"""
Project Bootstrap 테스트.

DB 초기화·정책 seed·Health Check·CLI 동작과 **멱등성**을 검증하고,
초기화 후 ``GET /dashboard/summary`` 가 500 없이 응답하는지 확인합니다.
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procurement.__main__ import main
from procurement.app import create_app
from procurement.database.bootstrap import (
    MVP_POLICY_SEEDS,
    OUT_OF_SCOPE_POLICY_CODES,
    bootstrap,
    deactivate_out_of_scope_policies,
    init_db,
    migrate_policy_evaluation_basis,
    seed_policies,
    verify_bootstrap,
)
from procurement.database.policy_repository import PolicyRepository
from procurement.database.policy_target_repository import PolicyTargetRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Purchase
from procurement.models.policy import Policy

#: 확정된 MVP 정책 코드 (PM 확정 — 변경하지 않음)
EXPECTED_CODES = {"SMALL_BUSINESS", "WOMAN", "DISABLED", "STARTUP", "GREEN"}

#: 대시보드가 계산 대상으로 보는 코드 — ``GREEN`` 은 제외된다.
#:
#: 2026-08-14 고객 결정(DECISIONS §0.5.1)으로 녹색제품은 이번 MVP 계산 대상이
#: 아니므로 ``is_active=False`` 로 seed 되며, ``find_active()`` 에 잡히지 않는다.
#: 정책 행 자체는 남아 있으므로 ``EXPECTED_CODES`` 는 그대로다.
ACTIVE_CODES = EXPECTED_CODES - {"GREEN"}


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "bootstrap.db"


def _table_names(path: Path) -> set[str]:
    with sqlite3.connect(str(path)) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row[0] for row in rows}


class TestInitDb:
    """init_db 는 4개 테이블을 만들고 반복 실행해도 안전해야 합니다."""

    def test_creates_core_tables(self, db_path: Path) -> None:
        init_db(db_path)
        assert {"company", "policy", "certification", "purchase"} <= _table_names(db_path)

    def test_creates_db_file_and_parent_directory(self, tmp_path: Path) -> None:
        """상위 디렉터리가 없어도 자동 생성됩니다."""
        nested = tmp_path / "no" / "such" / "dir" / "app.db"
        init_db(nested)
        assert nested.exists()

    def test_is_idempotent(self, db_path: Path) -> None:
        init_db(db_path)
        init_db(db_path)
        assert {"company", "policy", "certification", "purchase"} <= _table_names(db_path)

    def test_does_not_drop_existing_data(self, db_path: Path) -> None:
        """재실행이 기존 데이터를 지우지 않습니다."""
        init_db(db_path)
        seed_policies(db_path)
        init_db(db_path)
        assert PolicyRepository(db_path).count() == len(MVP_POLICY_SEEDS)


class TestSeedPolicies:
    """MVP 정책 5종 등록과 멱등성을 검증합니다."""

    def test_seeds_five_mvp_policies(self, db_path: Path) -> None:
        init_db(db_path)
        created = seed_policies(db_path)
        assert set(created) == EXPECTED_CODES
        assert PolicyRepository(db_path).count() == 5

    def test_target_rate_is_null(self, db_path: Path) -> None:
        """D-004: 목표율은 임의 값 없이 NULL 로 등록됩니다."""
        init_db(db_path)
        seed_policies(db_path)
        repository = PolicyRepository(db_path)
        for code in EXPECTED_CODES:
            policy = repository.find_by_policy_code(code)
            assert policy is not None
            assert policy.target_rate is None

    def test_evaluation_basis_matches_policy_definition(self, db_path: Path) -> None:
        """정책별 인증 유효기간 판정 기준일이 확정 규칙과 같은가.

        .. note::
            **기대값이 바뀐 이유 ①** — 2026-08-14 고객 확정(창업기업).

                창업기업은 결의일자와 계약일자가 기업 인증 유효기간에 해당할
                경우 모두 실적으로 인정한다.

            이전에는 ``CONTRACT_DATE``(계약일 **단독**) 였으나, 확정 규칙은 두
            날짜에 대한 **OR 조건**이므로 계약일 단독으로는 표현할 수 없습니다.

            **기대값이 바뀐 이유 ②** — 2026-08-31 고객 최종 회신
            (``DECISIONS.md`` §0.12.1 · STEP 84).

                중소기업 — 결의일자 / 여성기업 — 결의일자 / 장애인기업 — 결의일자

            일반 3개 정책이 ``PAYMENT_DATE`` 에서 ``RESOLUTION_DATE`` 로
            바뀌었습니다. 테스트를 통과시키려고 바꾼 것이 아니라 **업무규칙이
            바뀌어** 기대값이 달라진 경우입니다.

            ⛔ 녹색제품(``GREEN``)은 이번 답변에 없으므로 **그대로 둡니다.**
        """
        init_db(db_path)
        seed_policies(db_path)
        repository = PolicyRepository(db_path)
        expected = {
            "SMALL_BUSINESS": "RESOLUTION_DATE",
            "WOMAN": "RESOLUTION_DATE",
            "DISABLED": "RESOLUTION_DATE",
            "STARTUP": "RESOLUTION_OR_CONTRACT_DATE",
            "GREEN": "PAYMENT_DATE",
        }
        assert set(expected) == EXPECTED_CODES
        for code, basis in expected.items():
            policy = repository.find_by_policy_code(code)
            assert policy is not None
            assert policy.evaluation_basis == basis, code

    def test_second_run_creates_nothing(self, db_path: Path) -> None:
        """두 번째 실행은 아무것도 새로 만들지 않습니다(멱등)."""
        init_db(db_path)
        seed_policies(db_path)
        assert seed_policies(db_path) == []
        assert PolicyRepository(db_path).count() == 5

    def test_does_not_overwrite_existing_target_rate(self, db_path: Path) -> None:
        """운영자가 설정한 목표율을 재실행이 덮어쓰지 않습니다."""
        init_db(db_path)
        seed_policies(db_path)
        repository = PolicyRepository(db_path)
        policy = repository.find_by_policy_code("SMALL_BUSINESS")
        assert policy is not None
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "UPDATE policy SET target_rate = ? WHERE policy_code = ?",
                ("50", "SMALL_BUSINESS"),
            )

        seed_policies(db_path)

        updated = repository.find_by_policy_code("SMALL_BUSINESS")
        assert updated is not None
        assert updated.target_rate == Decimal("50")


class TestVerifyBootstrap:
    """Health Check 결과를 검증합니다."""

    def test_healthy_after_bootstrap(self, db_path: Path) -> None:
        bootstrap(db_path)
        report = verify_bootstrap(db_path)
        assert report.healthy
        assert all(item.passed for item in report.items)

    def test_fails_when_db_missing(self, db_path: Path) -> None:
        report = verify_bootstrap(db_path)
        assert not report.healthy
        assert "init" in report.format_report()

    def test_fails_when_seed_missing(self, db_path: Path) -> None:
        """스키마만 만들고 정책을 등록하지 않으면 실패로 보고합니다."""
        bootstrap(db_path, seed=False)
        report = verify_bootstrap(db_path)
        assert not report.healthy
        assert any("정책 seed" == item.name and not item.passed for item in report.items)

    def test_detects_outdated_schema(self, db_path: Path) -> None:
        """구 스키마(target_rate 컬럼 없음) DB 를 감지합니다."""
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                """CREATE TABLE policy (
                    policy_id INTEGER PRIMARY KEY,
                    policy_code TEXT UNIQUE NOT NULL,
                    policy_name TEXT NOT NULL,
                    description TEXT,
                    is_active BOOLEAN NOT NULL,
                    evaluation_basis TEXT NOT NULL,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL
                )"""
            )
        init_db(db_path)  # IF NOT EXISTS 이므로 기존 policy 테이블은 그대로 남는다
        report = verify_bootstrap(db_path)
        assert not report.healthy
        assert "policy.target_rate" in report.format_report()

    def test_report_is_readable(self, db_path: Path) -> None:
        bootstrap(db_path)
        text = verify_bootstrap(db_path).format_report()
        assert "결과: 정상" in text
        assert str(db_path) in text


class TestBootstrapOrchestrator:
    """bootstrap() 오케스트레이터 동작을 검증합니다."""

    def test_returns_healthy_report(self, db_path: Path) -> None:
        assert bootstrap(db_path).healthy

    def test_no_seed_option(self, db_path: Path) -> None:
        bootstrap(db_path, seed=False)
        assert PolicyRepository(db_path).count() == 0

    def test_repeated_bootstrap_is_stable(self, db_path: Path) -> None:
        bootstrap(db_path)
        report = bootstrap(db_path)
        assert report.healthy
        assert PolicyRepository(db_path).count() == 5


class TestCli:
    """CLI 서브커맨드 동작과 종료 코드를 검증합니다."""

    def test_init_returns_zero(self, db_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert main(["init", "--db", str(db_path)]) == 0
        assert "결과: 정상" in capsys.readouterr().out

    def test_init_twice_is_idempotent(self, db_path: Path) -> None:
        """PM 지정 확인 항목 D — init 을 두 번 실행해도 정상이어야 합니다."""
        assert main(["init", "--db", str(db_path)]) == 0
        assert main(["init", "--db", str(db_path)]) == 0
        assert PolicyRepository(db_path).count() == 5

    def test_init_no_seed(self, db_path: Path) -> None:
        """--no-seed 는 정책을 등록하지 않으므로 점검에 실패합니다."""
        assert main(["init", "--db", str(db_path), "--no-seed"]) == 1
        assert PolicyRepository(db_path).count() == 0

    def test_health_fails_before_init(self, db_path: Path) -> None:
        assert main(["health", "--db", str(db_path)]) == 1

    def test_health_passes_after_init(self, db_path: Path) -> None:
        main(["init", "--db", str(db_path)])
        assert main(["health", "--db", str(db_path)]) == 0

    def test_no_command_prints_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        assert main([]) == 0
        assert "init" in capsys.readouterr().out


class TestEvaluationBasisMigration:
    """확정으로 바뀐 판정 기준이 **기존 DB 에도** 반영되는지 검증합니다.

    ``seed_policies()`` 는 이미 존재하는 정책을 건너뛰므로, seed 상수만 고치면
    기존 DB 는 옛 기준값(``CONTRACT_DATE``)에 머뭅니다. 그러면 같은 코드가
    DB 에 따라 다른 결과를 내므로 명시적으로 갱신합니다.
    """

    @staticmethod
    def _set_basis(path: Path, policy_code: str, value: str) -> None:
        with sqlite3.connect(path) as conn:
            conn.execute(
                "UPDATE policy SET evaluation_basis = ? WHERE policy_code = ?",
                (value, policy_code),
            )

    @staticmethod
    def _basis(path: Path, policy_code: str) -> str:
        policy = PolicyRepository(path).find_by_policy_code(policy_code)
        assert policy is not None
        return policy.evaluation_basis

    def test_legacy_startup_row_is_updated(self, db_path: Path) -> None:
        """구 DB 의 STARTUP 이 OR 기준으로 갱신된다."""
        bootstrap(db_path)
        self._set_basis(db_path, "STARTUP", "CONTRACT_DATE")

        updated = migrate_policy_evaluation_basis(db_path)

        assert updated == ["STARTUP: CONTRACT_DATE→RESOLUTION_OR_CONTRACT_DATE"]
        assert self._basis(db_path, "STARTUP") == "RESOLUTION_OR_CONTRACT_DATE"

    def test_migration_is_idempotent(self, db_path: Path) -> None:
        bootstrap(db_path)
        self._set_basis(db_path, "STARTUP", "CONTRACT_DATE")

        assert migrate_policy_evaluation_basis(db_path)
        assert migrate_policy_evaluation_basis(db_path) == []

    def test_the_general_policies_are_migrated_to_the_resolution_date(self, db_path: Path) -> None:
        """구 DB 의 일반 3개 정책이 결의일자 기준으로 갱신된다(§0.12.1 · STEP 84)."""
        bootstrap(db_path)
        for code in ("SMALL_BUSINESS", "WOMAN", "DISABLED"):
            self._set_basis(db_path, code, "PAYMENT_DATE")

        updated = migrate_policy_evaluation_basis(db_path)

        assert sorted(updated) == [
            "DISABLED: PAYMENT_DATE→RESOLUTION_DATE",
            "SMALL_BUSINESS: PAYMENT_DATE→RESOLUTION_DATE",
            "WOMAN: PAYMENT_DATE→RESOLUTION_DATE",
        ]
        for code in ("SMALL_BUSINESS", "WOMAN", "DISABLED"):
            assert self._basis(db_path, code) == "RESOLUTION_DATE"

    def test_the_green_policy_is_untouched(self, db_path: Path) -> None:
        """⛔ 녹색제품은 이번 답변에 없다 — 갱신 대상이 아니다."""
        bootstrap(db_path)
        self._set_basis(db_path, "STARTUP", "CONTRACT_DATE")

        migrate_policy_evaluation_basis(db_path)

        assert self._basis(db_path, "GREEN") == "PAYMENT_DATE"

    def test_the_startup_policy_is_untouched_by_the_general_change(self, db_path: Path) -> None:
        """⛔ 창업기업은 결의일자 OR 계약일자 그대로다."""
        bootstrap(db_path)

        migrate_policy_evaluation_basis(db_path)

        assert self._basis(db_path, "STARTUP") == "RESOLUTION_OR_CONTRACT_DATE"

    def test_unexpected_value_is_not_overwritten(self, db_path: Path) -> None:
        """이전 값과 다르면 건드리지 않는다(운영자 설정 보호)."""
        bootstrap(db_path)
        self._set_basis(db_path, "STARTUP", "PAYMENT_DATE")

        assert migrate_policy_evaluation_basis(db_path) == []
        assert self._basis(db_path, "STARTUP") == "PAYMENT_DATE"

    def test_bootstrap_applies_the_migration(self, db_path: Path) -> None:
        """``init`` 경로에서 자동으로 반영된다."""
        bootstrap(db_path)
        self._set_basis(db_path, "STARTUP", "CONTRACT_DATE")

        bootstrap(db_path)

        assert self._basis(db_path, "STARTUP") == "RESOLUTION_OR_CONTRACT_DATE"

    def test_missing_policy_table_is_safe(self, tmp_path: Path) -> None:
        """정책 테이블이 없어도 예외 없이 빈 목록을 반환한다."""
        assert migrate_policy_evaluation_basis(tmp_path / "empty.db") == []

    def test_purchase_and_certification_data_are_untouched(self, db_path: Path) -> None:
        """구매·인증 데이터는 전혀 건드리지 않는다."""
        bootstrap(db_path)
        PurchaseRepository(db_path).insert(
            Purchase(
                business_no="1234567890",
                company_name="테스트업체",
                contract_date=date(2026, 1, 1),
                payment_date=date(2026, 1, 31),
                amount=Decimal("1000"),
            )
        )
        self._set_basis(db_path, "STARTUP", "CONTRACT_DATE")

        migrate_policy_evaluation_basis(db_path)

        rows = PurchaseRepository(db_path).find_all()
        assert len(rows) == 1
        assert rows[0].amount == Decimal("1000")


class TestRunRefusesStaleSchema:
    """``run`` 은 DB 스키마를 먼저 점검하고, 문제가 있으면 서버를 띄우지 않습니다.

    **배경**: ``run`` 은 ``migrate_schema()`` 를 호출하지 않습니다(``init`` 만 합니다).
    구 버전에서 만든 DB 를 그대로 두고 실행하면 조회 시점에 ``purchase.batch_id``
    컬럼이 없어 ``IndexError`` 가 나고, 대시보드가 **HTTP 500** 으로 실패합니다.
    그 시점에는 원인이 화면에 드러나지 않아 운영자가 조치를 알 수 없습니다.

    따라서 기동 전에 점검해 **500 대신 안내 메시지**를 내도록 했습니다.
    ``run`` 이 DB 를 자동으로 바꾸지는 않습니다.
    """

    @staticmethod
    def _make_legacy_db(path: Path) -> None:
        """``purchase.batch_id`` 가 없는 구 스키마 DB 를 만듭니다."""
        bootstrap(path)
        with sqlite3.connect(path) as conn:
            conn.execute("DROP INDEX IF EXISTS idx_purchase_batch")
            conn.execute("ALTER TABLE purchase DROP COLUMN batch_id")

    def test_run_refuses_when_db_is_missing(
        self, db_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """DB 가 없으면 서버를 띄우지 않고 조치를 안내한다."""
        assert main(["run", "--db", str(db_path)]) == 1
        assert "python -m procurement init" in capsys.readouterr().out

    def test_run_refuses_on_stale_schema(
        self, db_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """구 스키마이면 500 대신 사유와 조치를 출력하고 기동하지 않는다."""
        self._make_legacy_db(db_path)

        assert main(["run", "--db", str(db_path)]) == 1

        out = capsys.readouterr().out
        assert "batch_id" in out
        assert "python -m procurement init" in out
        assert "서버를 시작하지 않았습니다" in out

    def test_run_does_not_modify_the_database(self, db_path: Path) -> None:
        """점검만 하고 DB 를 바꾸지 않는다(자동 마이그레이션 금지)."""
        self._make_legacy_db(db_path)
        before = _purchase_columns(db_path)

        main(["run", "--db", str(db_path)])

        assert _purchase_columns(db_path) == before
        assert "batch_id" not in _purchase_columns(db_path)

    def test_run_does_not_delete_existing_rows(self, db_path: Path) -> None:
        """기존 데이터를 삭제하지 않는다."""
        bootstrap(db_path)
        PurchaseRepository(db_path).insert(
            Purchase(
                business_no="1234567890",
                company_name="테스트업체",
                contract_date=date(2026, 1, 1),
                payment_date=date(2026, 1, 31),
                amount=Decimal("1000"),
            )
        )
        with sqlite3.connect(db_path) as conn:
            conn.execute("DROP INDEX IF EXISTS idx_purchase_batch")
            conn.execute("ALTER TABLE purchase DROP COLUMN batch_id")

        main(["run", "--db", str(db_path)])

        with sqlite3.connect(db_path) as conn:
            assert conn.execute("SELECT COUNT(*) FROM purchase").fetchone()[0] == 1

    def test_init_recovers_and_then_run_passes_the_check(
        self, db_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``init`` 으로 복구하면 ``run`` 의 점검을 통과한다(health/init 과 일관).

        서버가 실제로 뜨는지는 확인 대상이 아니므로 ``uvicorn.run`` 을 대역으로
        바꿔 기동 자체는 건너뜁니다.
        """
        self._make_legacy_db(db_path)
        assert main(["run", "--db", str(db_path)]) == 1

        assert main(["init", "--db", str(db_path)]) == 0
        assert main(["health", "--db", str(db_path)]) == 0

        started: list[str] = []
        import uvicorn

        monkeypatch.setattr(uvicorn, "run", lambda *a, **k: started.append("ran"))
        assert main(["run", "--db", str(db_path)]) == 0
        assert started == ["ran"]


def _purchase_columns(path: Path) -> set[str]:
    """purchase 테이블의 컬럼 이름 집합을 반환합니다."""
    with sqlite3.connect(path) as conn:
        return {row[1] for row in conn.execute("PRAGMA table_info(purchase)")}


class TestDashboardAfterBootstrap:
    """초기화 후 대시보드 API 가 500 없이 응답해야 합니다."""

    def test_summary_returns_200(self, db_path: Path) -> None:
        bootstrap(db_path)
        response = TestClient(create_app(db_path, period_date_field="payment_date")).get(
            "/dashboard/summary?year=2026"
        )
        assert response.status_code == 200

    def test_seeded_policies_are_shown_as_target_rate_not_set(self, db_path: Path) -> None:
        """초기화 직후 **활성 4종**이 모두 '목표율 미설정'으로 표시됩니다.

        .. note::
            **기대값이 바뀐 이유** — 2026-08-14 고객 결정(DECISIONS §0.5.1)으로
            녹색제품이 이번 MVP 계산 대상에서 제외되어 ``is_active=False`` 로
            seed 됩니다. 대시보드는 활성 정책만 보므로 5종 → 4종이 됩니다.
            정책 행은 삭제하지 않았으므로 ``GET /policies`` 에는 그대로 나옵니다.
        """
        bootstrap(db_path)
        _register_company_data(db_path)
        payload = (
            TestClient(create_app(db_path, period_date_field="payment_date"))
            .get("/dashboard/summary?year=2026")
            .json()
        )
        assert payload["total_purchase_amount"] == "0"
        assert {item["policy_code"] for item in payload["policies"]} == ACTIVE_CODES
        for item in payload["policies"]:
            assert item["target_rate"] is None
            assert item["achievement_rate"] is None
            assert item["shortage_rate"] is None
            assert item["status"] == "TARGET_RATE_NOT_SET"
            assert item["status_label"] == "목표율 미설정"

    def test_policy_is_calculated_once_target_rate_is_set(self, db_path: Path) -> None:
        """목표비율을 등록하면 별도 조치 없이 계산 대상이 됩니다.

        ⚠️ STEP 93 — 목표비율의 정본이 **연도별** 값으로 바뀌었다
        (DECISIONS §0.20). 그래서 등록하는 자리가 ``policy.target_rate`` 에서
        ``policy_target`` 으로 옮겨졌다.
        ⛔ 기대값은 그대로다 — "등록하면 계산된다" 를 여전히 검증한다.
        """
        bootstrap(db_path)
        policy = PolicyRepository(db_path).find_by_policy_code("SMALL_BUSINESS")
        assert policy is not None
        assert policy.policy_id is not None
        PolicyTargetRepository(db_path).upsert(2026, policy.policy_id, Decimal("50"))
        _register_company_data(db_path)
        payload = (
            TestClient(create_app(db_path, period_date_field="payment_date"))
            .get("/dashboard/summary?year=2026")
            .json()
        )
        by_code = {item["policy_code"]: item for item in payload["policies"]}

        # 목표율을 등록한 정책만 계산 상태로 바뀐다.
        assert by_code["SMALL_BUSINESS"]["target_rate"] == "50"
        assert by_code["SMALL_BUSINESS"]["status"] != "TARGET_RATE_NOT_SET"

        # 나머지는 여전히 목표율 미설정으로 남는다.
        for code in ACTIVE_CODES - {"SMALL_BUSINESS"}:
            assert by_code[code]["status"] == "TARGET_RATE_NOT_SET"


class TestGreenIsOutOfScope:
    """🟢 ``GREEN`` 은 이번 MVP 계산 대상에서 제외된다 (DECISIONS §0.5.1).

    확정된 것은 "계산 대상에서 제외" 이고, 처리 방식은 **비활성화**입니다
    (2026-08-20 PM 결정). ⛔ 행을 삭제하지 않으므로 이력은 그대로 남습니다.
    """

    def test_green_is_seeded_inactive(self, db_path: Path) -> None:
        bootstrap(db_path)
        policy = PolicyRepository(db_path).find_by_policy_code("GREEN")

        assert policy is not None, "행을 지우지 않는다"
        assert policy.is_active is False

    def test_green_is_not_in_find_active(self, db_path: Path) -> None:
        """⛔ 계산 대상 조회에 잡히지 않는다."""
        bootstrap(db_path)
        codes = {policy.policy_code for policy in PolicyRepository(db_path).find_active()}

        assert "GREEN" not in codes
        assert codes == ACTIVE_CODES

    def test_green_is_not_in_find_active_with_target_rate(self, db_path: Path) -> None:
        """목표율을 직접 넣어도 계산 대상 조회에 잡히지 않는다."""
        bootstrap(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute("UPDATE policy SET target_rate = '10' WHERE policy_code = 'GREEN'")

        repository = PolicyRepository(db_path)
        assert "GREEN" not in {
            policy.policy_code for policy in repository.find_active_with_target_rate()
        }

    def test_green_stays_in_find_all(self, db_path: Path) -> None:
        """이력 보존 — 전체 조회에는 그대로 나온다."""
        bootstrap(db_path)
        codes = {policy.policy_code for policy in PolicyRepository(db_path).find_all()}

        assert codes == EXPECTED_CODES

    def test_green_is_absent_from_the_dashboard(self, db_path: Path) -> None:
        """⛔ 대시보드 요약에 나타나지 않는다."""
        bootstrap(db_path)
        _register_company_data(db_path)
        payload = (
            TestClient(create_app(db_path, period_date_field="payment_date"))
            .get("/dashboard/summary?year=2026")
            .json()
        )

        assert "GREEN" not in {item["policy_code"] for item in payload["policies"]}

    def test_target_rate_api_rejects_green(self, db_path: Path) -> None:
        """⛔ 목표율 설정 경로가 닫혀 있다 — '목표율만 넣으면 계산되던' 경로."""
        bootstrap(db_path)
        from procurement.admin.policy_admin import PolicyAdminService
        from procurement.database.policy_repository import PolicyValidationError

        service = PolicyAdminService(PolicyRepository(db_path))
        with pytest.raises(PolicyValidationError, match="비활성"):
            service.set_target_rate("GREEN", "10")

    def test_existing_db_is_migrated(self, db_path: Path) -> None:
        """⛔ seed 에서만 빼면 **기존 DB 의 행은 계속 계산된다.**

        이미 활성으로 등록되어 있던 DB 도 비활성으로 바뀌어야 합니다.
        """
        init_db(db_path)
        repository = PolicyRepository(db_path)
        repository.insert(
            Policy(
                policy_code="GREEN",
                policy_name="녹색제품",
                evaluation_basis="PAYMENT_DATE",
                is_active=True,
            )
        )
        assert "GREEN" in {policy.policy_code for policy in repository.find_active()}

        changed = deactivate_out_of_scope_policies(db_path)

        assert changed == ["GREEN"]
        assert "GREEN" not in {policy.policy_code for policy in repository.find_active()}

    def test_migration_is_idempotent(self, db_path: Path) -> None:
        bootstrap(db_path)
        assert deactivate_out_of_scope_policies(db_path) == []

    def test_migration_keeps_the_row_and_its_values(self, db_path: Path) -> None:
        """⛔ 삭제하지 않고, 목표율 등 다른 값도 건드리지 않는다."""
        init_db(db_path)
        repository = PolicyRepository(db_path)
        repository.insert(
            Policy(
                policy_code="GREEN",
                policy_name="녹색제품",
                evaluation_basis="PAYMENT_DATE",
                target_rate=Decimal("10"),
                is_active=True,
            )
        )

        deactivate_out_of_scope_policies(db_path)
        policy = repository.find_by_policy_code("GREEN")

        assert policy is not None
        assert policy.policy_name == "녹색제품"
        assert policy.target_rate == Decimal("10")
        assert policy.is_active is False

    def test_bootstrap_runs_the_migration(self, db_path: Path) -> None:
        """``bootstrap()`` 한 번으로 기존 DB 도 정리된다."""
        init_db(db_path)
        PolicyRepository(db_path).insert(
            Policy(policy_code="GREEN", policy_name="녹색제품", evaluation_basis="PAYMENT_DATE")
        )

        bootstrap(db_path)

        policy = PolicyRepository(db_path).find_by_policy_code("GREEN")
        assert policy is not None and policy.is_active is False

    def test_only_green_is_out_of_scope(self) -> None:
        """⛔ 다른 정책을 임의로 제외 목록에 넣지 않는다.

        최종 정책 목록(8개 vs 9개)의 불일치는 **아직 미확정**입니다
        (DECISIONS §0.5.5). 확정 전에는 GREEN 외에 아무것도 비활성화하지
        않습니다.
        """
        assert OUT_OF_SCOPE_POLICY_CODES == ("GREEN",)

    def test_other_seeds_stay_active(self) -> None:
        inactive = {seed.policy_code for seed in MVP_POLICY_SEEDS if not seed.is_active}
        assert inactive == {"GREEN"}


def _register_company_data(db_path: Path, *policy_codes: str) -> None:
    """정책의 기업 목록을 **받았다는 사실**만 기록합니다(STEP 96 §8).

    ⚠️ 기업정보를 받지 못한 정책은 이제 **조회불가**이므로 목표율 상태까지
    가지 못합니다. 목표율 쪽을 보는 시험이라 앞단을 열어 두는 것입니다.
    ⛔ 기업·인증을 만들지 않습니다 — 목록은 받았으나 우리 거래처가 없는 상태이며,
    그것은 "모른다" 가 아니라 **"전부 미해당"** 입니다.
    ⛔ 기대값은 바뀌지 않았습니다.
    """
    from procurement.database.policy_company_source_repository import (
        PolicyCompanySourceRepository,
    )

    registry = PolicyCompanySourceRepository(db_path)
    repository = PolicyRepository(db_path)
    codes = policy_codes or tuple(policy.policy_code for policy in repository.find_active())
    for code in codes:
        policy = repository.find_by_policy_code(code)
        if policy is None or policy.policy_id is None:
            continue
        registry.record(policy.policy_id, source="FILE", company_count=0, certification_count=0)
