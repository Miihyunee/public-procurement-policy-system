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
    # Admin API
    # ------------------------------------------------------------------
    ADMIN_API_TOKEN: str | None = Field(
        default=None,
        description=(
            "설정 변경(쓰기) API 인증 토큰. 환경변수 또는 .env 로만 주입한다. "
            "미설정이면 쓰기 API 가 비활성화된다(503). 실제 값을 코드·문서·저장소에 "
            "기록하지 않는다."
        ),
    )

    # ------------------------------------------------------------------
    # Dashboard
    # ------------------------------------------------------------------
    DATA_MODE: Literal["demo", "operational"] = Field(
        default="demo",
        description=(
            "현재 DB 에 담긴 데이터의 성격. demo 이면 Dashboard 에 "
            "'DEMO / SAMPLE DATA' 를 표시한다. 실제 운영 데이터가 적재된 뒤에만 "
            "operational 로 바꾼다(기본값은 안전한 쪽인 demo)."
        ),
    )

    DASHBOARD_ACHIEVEMENT_DISPLAY_THRESHOLDS: str | None = Field(
        default=None,
        description=(
            "Dashboard 달성률 표시 구간 경계(%). 쉼표로 구분한 5개 값. "
            "예: '20,40,60,80,100'. 미설정이면 기본값을 사용한다. "
            "⚠️ 법정 기준이 아니라 화면 표시용 임시 기준이며, 계산·판정에 "
            "사용되지 않는다."
        ),
    )

    PURCHASE_PERIOD_DATE_FIELD: (
        Literal["payment_date", "contract_date", "resolution_date"] | None
    ) = Field(
        default=None,
        description=(
            "연도(기간) 귀속 판정에 사용할 날짜 컬럼. **기본값을 두지 않는다.** "
            "2026-08-14 고객 확정에 따르면 일반 정책은 결의일자(resolution_date) "
            "기준이나, 기존 데이터에는 그 값이 없을 수 있어 운영자가 명시한다. "
            "어느 날짜로 연도를 나눌지는 D-24(미확정)이며 고객 확인 항목 W-1 에 "
            "종속된다. 미설정 상태에서 기간 조회를 요청하면 숫자를 내지 않고 "
            "오류로 응답한다."
        ),
    )

    # ------------------------------------------------------------------
    # 외부 인증 API
    #
    #   ⚠️ 실제 키 값은 .env 로만 주입한다. 코드·테스트·문서·저장소·.env.example
    #      어디에도 실제 값을 기록하지 않는다.
    # ------------------------------------------------------------------
    SMPP_API_KEY: str | None = Field(
        default=None,
        description=(
            "공공구매종합정보망(SMPP) 인증키. 여성기업·장애인기업·창업기업(SMPP) "
            "조회에 사용한다. 미설정이면 해당 조회가 ApiKeyNotConfiguredError 로 "
            "실패한다. 공공데이터포털의 **Decoding** 키를 넣는다."
        ),
    )

    STARTUP_API_KEY: str | None = Field(
        default=None,
        description=(
            "창업진흥원 창업기업확인서 조회 인증키. 미설정이면 해당 조회가 "
            "ApiKeyNotConfiguredError 로 실패한다. 공공데이터포털의 **Decoding** "
            "키를 넣는다."
        ),
    )

    SMPP_TEST_BUSINESS_NO: str | None = Field(
        default=None,
        description=(
            "실호출 시험용 사업자등록번호. SMPP_API_KEY 와 **둘 다** 설정되어 "
            "있을 때만 실제 API 호출 시험이 수행되고, 하나라도 없으면 건너뜁니다"
            "(실패가 아닙니다). ⛔ 고객 원본 데이터에서 가져오지 않습니다 — "
            "개발자가 따로 확보한 시험용 값만 넣으며, 실제 값을 코드·테스트·"
            "문서·저장소에 기록하지 않습니다."
        ),
    )

    EXTERNAL_API_TIMEOUT_SECONDS: float = Field(
        default=10.0,
        description=(
            "외부 인증 API 응답 대기 시간(초). 명세서 기재 성능은 평균 500ms 이며, "
            "그보다 충분히 큰 값을 기본으로 둔다."
        ),
    )

    EXTERNAL_API_MAX_ATTEMPTS: int = Field(
        default=2,
        ge=1,
        description=(
            "외부 인증 API 최대 시도 횟수(최초 1회 포함). 명세서에 재시도 정책이 "
            "없으므로 최소값을 기본으로 둔다. 재시도는 timeout·네트워크·5xx 에만 "
            "적용되며, 인증 실패·잘못된 요청·한도 초과는 재시도하지 않는다."
        ),
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
