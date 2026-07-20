"""
Configuration 시스템 테스트.

Settings 기본값, 환경변수 오버라이드, 경로 파생 동작을 검증합니다.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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


class TestEnvExampleFile:
    """.env.example 파일이 존재하고 필수 키를 포함하는지 검증합니다."""

    def test_env_example_exists(self) -> None:
        env_example = settings.project_root / ".env.example"
        assert env_example.exists(), ".env.example 파일이 프로젝트 루트에 있어야 합니다."

    def test_env_example_contains_required_keys(self) -> None:
        env_example = settings.project_root / ".env.example"
        content = env_example.read_text(encoding="utf-8")
        required_keys = ["APP_NAME", "APP_VERSION", "ENVIRONMENT", "DEBUG",
                         "DATA_PATH", "DATABASE_PATH", "LOG_PATH"]
        for key in required_keys:
            assert key in content, f".env.example에 {key} 항목이 없습니다."