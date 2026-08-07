"""
Project Bootstrap 테스트.

DB 초기화·정책 seed·Health Check·CLI 동작과 **멱등성**을 검증하고,
초기화 후 ``GET /dashboard/summary`` 가 500 없이 응답하는지 확인합니다.
"""

from __future__ import annotations

import sqlite3
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
    seed_policies,
    verify_bootstrap,
)
from procurement.database.policy_repository import PolicyRepository

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
        """창업기업만 계약일 기준, 나머지는 지급일 기준입니다."""
        init_db(db_path)
        seed_policies(db_path)
        repository = PolicyRepository(db_path)
        startup = repository.find_by_policy_code("STARTUP")
        assert startup is not None
        assert startup.evaluation_basis == "CONTRACT_DATE"
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


class TestDashboardAfterBootstrap:
    """초기화 후 대시보드 API 가 500 없이 응답해야 합니다."""

    def test_summary_returns_200(self, db_path: Path) -> None:
        bootstrap(db_path)
        response = TestClient(create_app(db_path)).get("/dashboard/summary")
        assert response.status_code == 200

    def test_null_target_rate_policies_are_excluded(self, db_path: Path) -> None:
        """목표율이 NULL 인 정책은 계산 대상에서 제외됩니다(0% 처리 아님)."""
        bootstrap(db_path)
        payload = TestClient(create_app(db_path)).get("/dashboard/summary").json()
        assert payload["total_purchase_amount"] == "0"
        assert payload["policies"] == []

    def test_policy_appears_once_target_rate_is_set(self, db_path: Path) -> None:
        """목표율을 등록하면 별도 조치 없이 계산에 포함됩니다."""
        bootstrap(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "UPDATE policy SET target_rate = ? WHERE policy_code = ?",
                ("50", "SMALL_BUSINESS"),
            )
        payload = TestClient(create_app(db_path)).get("/dashboard/summary").json()
        codes = [item["policy_code"] for item in payload["policies"]]
        assert codes == ["SMALL_BUSINESS"]
