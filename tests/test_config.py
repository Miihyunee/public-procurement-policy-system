"""
Configuration 시스템 테스트.

Settings 기본값, 환경변수 오버라이드, 경로 파생 동작을 검증합니다.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from procurement.core.config import Settings, settings


class TestDefaultSettings:
    """기본값이 올바르게 설정되는지 검증합니다."""

    def test_app_name(self) -> None:
        assert settings.APP_NAME == "Public Procurement Policy System"

    def test_app_version(self) -> None:
        assert settings.APP_VERSION == "0.1.0"

    def test_environment_default(self) -> None:
        assert settings.ENVIRONMENT == "development"

    def test_debug_default(self) -> None:
        assert settings.DEBUG is True

    def test_project_root_is_path(self) -> None:
        assert isinstance(settings.project_root, Path)
        assert settings.project_root.exists()

    def test_data_path_is_path(self) -> None:
        assert isinstance(settings.DATA_PATH, Path)

    def test_database_path_is_path(self) -> None:
        assert isinstance(settings.DATABASE_PATH, Path)

    def test_log_path_is_path(self) -> None:
        assert isinstance(settings.LOG_PATH, Path)

    def test_database_filename_default(self) -> None:
        assert settings.DATABASE_FILENAME == "procurement.db"

    def test_db_file_is_path(self) -> None:
        assert isinstance(settings.db_file, Path)

    def test_db_file_composed_correctly(self) -> None:
        """db_file = DATABASE_PATH / DATABASE_FILENAME 인지 확인합니다."""
        assert settings.db_file == settings.DATABASE_PATH / settings.DATABASE_FILENAME

    def test_db_file_has_correct_suffix(self) -> None:
        assert settings.db_file.suffix == ".db"


class TestEnvironmentOverride:
    """환경변수로 설정값을 변경할 수 있는지 검증합니다."""

    def test_debug_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DEBUG", "False")
        s = Settings()
        assert s.DEBUG is False

    def test_environment_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENVIRONMENT", "production")
        s = Settings()
        assert s.ENVIRONMENT == "production"

    def test_is_production_flag(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("ENVIRONMENT", "production")
        s = Settings()
        assert s.is_production is True

    def test_is_not_production_by_default(self) -> None:
        assert settings.is_production is False

    def test_data_path_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("DATA_PATH", str(tmp_path))
        s = Settings()
        assert s.DATA_PATH == tmp_path

    def test_database_path_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("DATABASE_PATH", str(tmp_path))
        s = Settings()
        assert s.DATABASE_PATH == tmp_path

    def test_log_path_override(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("LOG_PATH", str(tmp_path))
        s = Settings()
        assert s.LOG_PATH == tmp_path

    def test_database_filename_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DATABASE_FILENAME", "custom.db")
        s = Settings()
        assert s.DATABASE_FILENAME == "custom.db"

    def test_db_file_reflects_overrides(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """DATABASE_PATH, DATABASE_FILENAME 변경 시 db_file도 반영되어야 합니다."""
        monkeypatch.setenv("DATABASE_PATH", str(tmp_path))
        monkeypatch.setenv("DATABASE_FILENAME", "custom.db")
        s = Settings()
        assert s.db_file == tmp_path / "custom.db"


class TestEnvExampleFile:
    """.env.example 파일이 존재하고 필수 키를 포함하는지 검증합니다."""

    def test_env_example_exists(self) -> None:
        env_example = settings.project_root / ".env.example"
        assert env_example.exists(), ".env.example 파일이 프로젝트 루트에 있어야 합니다."

    def test_env_example_contains_required_keys(self) -> None:
        env_example = settings.project_root / ".env.example"
        content = env_example.read_text(encoding="utf-8")
        required_keys = [
            "APP_NAME",
            "APP_VERSION",
            "ENVIRONMENT",
            "DEBUG",
            "DATA_PATH",
            "DATABASE_PATH",
            "DATABASE_FILENAME",
            "LOG_PATH",
        ]
        for key in required_keys:
            assert key in content, f".env.example에 {key} 항목이 없습니다."


class TestExternalApiSettings:
    """외부 인증 API 설정을 검증합니다.

    실제 키 값은 이 파일 어디에도 넣지 않습니다. 검증하는 것은 "어디서 어떻게
    읽히는가" 뿐입니다.
    """

    def test_api_keys_default_to_none(self) -> None:
        """키는 기본값이 없다. 설정하지 않으면 해당 조회가 실패해야 한다."""
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.SMPP_API_KEY is None
        assert s.STARTUP_API_KEY is None

    def test_smpp_api_key_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SMPP_API_KEY", "dummy-value-for-test")
        s = Settings()
        assert s.SMPP_API_KEY == "dummy-value-for-test"

    def test_startup_api_key_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("STARTUP_API_KEY", "dummy-value-for-test")
        s = Settings()
        assert s.STARTUP_API_KEY == "dummy-value-for-test"

    def test_timeout_default(self) -> None:
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.EXTERNAL_API_TIMEOUT_SECONDS == 10.0

    def test_max_attempts_default_is_minimal(self) -> None:
        """명세에 재시도 정책이 없으므로 기본값은 최소(최초 1회 + 재시도 1회)."""
        s = Settings(_env_file=None)  # type: ignore[call-arg]
        assert s.EXTERNAL_API_MAX_ATTEMPTS == 2

    def test_max_attempts_must_be_at_least_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("EXTERNAL_API_MAX_ATTEMPTS", "0")
        with pytest.raises(ValidationError):
            Settings()

    def test_env_example_has_key_names_but_no_values(self) -> None:
        """.env.example 에는 변수명만 있고 실제 값이 없어야 한다.

        변경 사유(STEP 43): 실호출 시험용 사업자등록번호
        ``SMPP_TEST_BUSINESS_NO`` 를 검사 대상에 **더했다.** 이 값도 실제
        사업자번호이므로 키와 같은 취급을 받아야 하며, ``.env.example`` 에
        값이 적히면 저장소에 남는다. 검사 범위를 넓힌 것이며 기존에 지키던
        사실은 그대로다.
        """
        env_example = settings.project_root / ".env.example"
        content = env_example.read_text(encoding="utf-8")
        for key in ("SMPP_API_KEY", "STARTUP_API_KEY", "SMPP_TEST_BUSINESS_NO"):
            assert key in content, f".env.example 에 {key} 항목이 없습니다."
            for line in content.splitlines():
                stripped = line.strip().lstrip("#").strip()
                if stripped.startswith(f"{key}="):
                    assert stripped == f"{key}=", (
                        f".env.example 의 {key} 에 값이 적혀 있습니다. 실제 키는 .env 에만 둡니다."
                    )
