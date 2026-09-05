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

import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

#: 사용자 데이터 폴더 이름. `package.json` 의 앱 이름과 **같게** 둔다 —
#: Electron 이 만드는 `userData` 폴더와 같은 자리를 가리켜야 하기 때문이다.
_APP_DIR_NAME = "procurement-desktop"


def is_frozen() -> bool:
    """PyInstaller 등으로 **묶여서** 실행 중인가.

    묶인 실행파일에서는 :data:`sys.frozen` 이 붙습니다. 이 값으로 「소스가 있는
    개발 환경」과 「배포본」을 가릅니다.
    """
    return bool(getattr(sys, "frozen", False))


def user_data_root() -> Path:
    """고객 데이터를 두는 폴더 (STEP 125 §6).

    프로그램이 설치되는 자리(`C:\\Program Files\\...`)는 일반 사용자 권한으로
    **쓸 수 없고**, 프로그램을 새 버전으로 덮으면 **함께 지워질 수** 있습니다.
    그래서 데이터는 설치 자리 밖에 둡니다.

    ============  ================================================
    Windows       ``%APPDATA%\\procurement-desktop``
    그 밖         ``$XDG_DATA_HOME`` 또는 ``~/.local/share`` 아래
    ============  ================================================

    .. note::
        Electron 이 넘겨 주는 ``userData`` 폴더와 **같은 이름**을 씁니다.
        백엔드를 단독 실행하든 Electron 이 띄우든 같은 자리를 보게 됩니다.
    """
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / _APP_DIR_NAME
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        return Path(xdg) / _APP_DIR_NAME
    return Path.home() / ".local" / "share" / _APP_DIR_NAME


def default_root() -> Path:
    """기본 경로들의 **기준 폴더**.

    .. warning::
        ⛔ **묶인 실행파일에서 소스 위치를 기준으로 삼지 않습니다.**

        예전에는 언제나 ``Path(__file__).resolve().parents[4]`` 였습니다. 개발
        환경에서는 프로젝트 루트가 정확히 잡히지만, PyInstaller 로 묶으면 이
        파일이 **임시로 풀린 폴더** 안에 놓여 그 위 어딘가를 가리킵니다. 거기에
        DB 를 만들면 프로그램을 끌 때 **고객 데이터가 사라집니다**(STEP 124 §4.1).

    ⭐ **환경변수가 언제나 이깁니다.** 여기서 정하는 것은 «아무것도 지정하지
    않았을 때의 기본값» 뿐이며, ``DATABASE_PATH`` 를 주면 그 값이 쓰입니다
    (Electron 은 이미 그렇게 넘깁니다).
    """
    if is_frozen():
        return user_data_root()
    # 프로젝트 루트: 이 파일 기준 5단계 상위 (src/procurement/core/config/settings.py)
    return Path(__file__).resolve().parents[4]


#: 기본 경로의 기준 폴더. 실행 형태는 도중에 바뀌지 않으므로 한 번만 정합니다.
_PROJECT_ROOT = default_root()


def _env_files() -> tuple[str, ...]:
    """설정 파일을 찾을 자리 (STEP 125 §5).

    ⛔ 예전에는 ``".env"`` 하나였고, 그것은 **현재 작업 디렉터리 기준**입니다.
    바탕화면 바로가기로 켜면 작업 디렉터리가 임의라 찾지 못합니다.

    그래서 **사용자 데이터 폴더의 ``.env`` 를 함께** 봅니다. 순서는

    1. ``<사용자 데이터 폴더>/.env`` — 배포본에서 고객이 API 키를 두는 자리
    2. ``.env`` — 개발 환경에서 쓰던 자리. **뒤에 두어 이깁니다.**

    ⭐ 어느 쪽이든 **환경변수가 그보다 먼저**입니다. ``DATABASE_PATH`` 우선순위는
    그대로입니다.

    ⛔ API 키를 실행파일에 넣지 않습니다. 고객 기관이 발급받는 값이며, 넣으면
    꺼내 볼 수 있습니다.
    """
    return (str(user_data_root() / ".env"), ".env")


class Settings(BaseSettings):
    """Public Procurement Policy System 전역 설정.

    환경변수 또는 .env 파일로 값을 재정의할 수 있습니다.
    """

    model_config = SettingsConfigDict(
        env_file=_env_files(),
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
        default="resolution_date",
        description=(
            "연도(기간) 귀속 판정에 사용할 날짜 컬럼. 기본값은 결의일자"
            "(resolution_date)다. 🟢 2026-09-02 PM 확정(STEP 86) — "
            "'실적 산정 및 연도 귀속의 기준일은 원본파일의 결의일자다.' "
            "⛔ 신고기준일(issue_date)은 기간 축에 넣지 않는다. "
            "운영자가 다른 값으로 바꿀 수는 있으나, 바꾸면 확정 규칙과 달라진다. "
            "명시적으로 비우면 기간 조회는 숫자를 내지 않고 오류로 응답한다."
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
