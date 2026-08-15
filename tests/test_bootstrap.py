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
    bootstrap,
    init_db,
    migrate_policy_evaluation_basis,
    seed_policies,
    verify_bootstrap,
)
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.models import Purchase

#: 확정된 MVP 정책 코드 (PM 확정 — 변경하지 않음)
EXPECTED_CODES = {"SMALL_BUSINESS", "WOMAN", "DISABLED", "STARTUP", "GREEN"}


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
        """창업기업만 두 날짜 OR 기준, 나머지는 지급일 기준입니다.

        .. note::
            **기대값이 바뀐 이유** — 2026-08-14 고객 확정.

                창업기업은 결의일자와 계약일자가 기업 인증 유효기간에 해당할
                경우 모두 실적으로 인정한다.

            이전에는 ``CONTRACT_DATE``(계약일 **단독**) 였으나, 확정 규칙은 두
            날짜에 대한 **OR 조건**이므로 계약일 단독으로는 표현할 수 없습니다.
            (계약일이 기간 밖이고 다른 날짜만 안에 있는 구매를 놓칩니다.)

            테스트를 통과시키려고 바꾼 것이 아니라, **업무규칙 자체가 바뀌어**
            기대값이 달라진 경우입니다. 근거: ``DECISIONS.md`` §0.6.

            나머지 정책의 기준은 **변경되지 않았습니다.**
        """
        init_db(db_path)
        seed_policies(db_path)
        repository = PolicyRepository(db_path)
        startup = repository.find_by_policy_code("STARTUP")
        assert startup is not None
        assert startup.evaluation_basis == "PAYMENT_OR_CONTRACT_DATE"
        for code in EXPECTED_CODES - {"STARTUP"}:
            policy = repository.find_by_policy_code(code)
            assert policy is not None
            assert policy.evaluation_basis == "PAYMENT_DATE"

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

        assert updated == ["STARTUP: CONTRACT_DATE→PAYMENT_OR_CONTRACT_DATE"]
        assert self._basis(db_path, "STARTUP") == "PAYMENT_OR_CONTRACT_DATE"

    def test_migration_is_idempotent(self, db_path: Path) -> None:
        bootstrap(db_path)
        self._set_basis(db_path, "STARTUP", "CONTRACT_DATE")

        assert migrate_policy_evaluation_basis(db_path)
        assert migrate_policy_evaluation_basis(db_path) == []

    def test_other_policies_are_untouched(self, db_path: Path) -> None:
        """STARTUP 외 정책의 기준은 건드리지 않는다."""
        bootstrap(db_path)
        self._set_basis(db_path, "STARTUP", "CONTRACT_DATE")

        migrate_policy_evaluation_basis(db_path)

        for code in EXPECTED_CODES - {"STARTUP"}:
            assert self._basis(db_path, code) == "PAYMENT_DATE"

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

        assert self._basis(db_path, "STARTUP") == "PAYMENT_OR_CONTRACT_DATE"

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
        """초기화 직후 5종이 모두 '목표율 미설정'으로 표시됩니다(0% 처리 아님)."""
        bootstrap(db_path)
        payload = (
            TestClient(create_app(db_path, period_date_field="payment_date"))
            .get("/dashboard/summary?year=2026")
            .json()
        )
        assert payload["total_purchase_amount"] == "0"
        assert {item["policy_code"] for item in payload["policies"]} == EXPECTED_CODES
        for item in payload["policies"]:
            assert item["target_rate"] is None
            assert item["achievement_rate"] is None
            assert item["shortage_rate"] is None
            assert item["status"] == "TARGET_RATE_NOT_SET"
            assert item["status_label"] == "목표율 미설정"

    def test_policy_is_calculated_once_target_rate_is_set(self, db_path: Path) -> None:
        """목표율을 등록하면 별도 조치 없이 계산 대상이 됩니다."""
        bootstrap(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "UPDATE policy SET target_rate = ? WHERE policy_code = ?",
                ("50", "SMALL_BUSINESS"),
            )
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
        for code in EXPECTED_CODES - {"SMALL_BUSINESS"}:
            assert by_code[code]["status"] == "TARGET_RATE_NOT_SET"
