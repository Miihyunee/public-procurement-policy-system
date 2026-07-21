"""
procurement.core.config.settings

애플리케이션 설정을 관리합니다.

설정 우선순위 (높은 순):
    1. 환경변수 (export KEY=value)
    2. .env 파일
    3. 필드 기본값

사용 예:
    from procurement.core.config import settings

    print(settings.APP_NAME)
    print(settings.DATABASE_PATH)
    print(settings.db_file)
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


# 프로젝트 루트: 이 파일 기준 5단계 상위 (src/procurement/core/config/settings.py)
_PROJECT_ROOT = Path(__file__).resolve().parents[4]


class Settings(BaseSettings):
    """Public Procurement Policy System 전역 설정.

    환경변수 또는 .env 파일로 값을 재정의할 수 있습니다.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------
    APP_NAME: str = Field(
        default="Public Procurement Policy System",
        description="애플리케이션 이름",
    )
    APP_VERSION: str = Field(
        default="0.1.0",
        description="애플리케이션 버전",
    )
    ENVIRONMENT: Literal["development", "staging", "production"] = Field(
        default="development",
        description="실행 환경 (development | staging | production)",
    )
    DEBUG: bool = Field(
        default=True,
        description="디버그 모드 활성화 여부",
    )

    # ------------------------------------------------------------------
    # Path
    # ------------------------------------------------------------------
    DATA_PATH: Path = Field(
        default=_PROJECT_ROOT / "data",
        description="데이터 파일 루트 디렉터리",
    )
    DATABASE_PATH: Path = Field(
        default=_PROJECT_ROOT / "database",
        description="SQLite DB 파일 저장 디렉터리",
    )
    LOG_PATH: Path = Field(
        default=_PROJECT_ROOT / "logs",
        description="로그 파일 저장 디렉터리",
    )
    DATABASE_FILENAME: str = Field(
        default="procurement.db",
        description="SQLite 데이터베이스 파일명",
    )

    # ------------------------------------------------------------------
    # Computed fields (읽기 전용 파생 경로)
    # ------------------------------------------------------------------
    @computed_field  # type: ignore[prop-decorator]
    @property
    def project_root(self) -> Path:
        """프로젝트 루트 디렉터리 경로."""
        return _PROJECT_ROOT

    @computed_field  # type: ignore[prop-decorator]
    @property
    def db_file(self) -> Path:
        """SQLite 데이터베이스 파일 전체 경로.

        DATABASE_PATH / DATABASE_FILENAME 으로 결정됩니다.

        예: /project/database/procurement.db
        """
        return self.DATABASE_PATH / self.DATABASE_FILENAME

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        """운영 환경 여부."""
        return self.ENVIRONMENT == "production"


# 모듈 수준 싱글턴
settings = Settings()