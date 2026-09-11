"""
STEP 125 — **묶여서 실행될 때** 설정 경로가 어디를 가리키는가.

무엇을 고쳤는가
===============
STEP 124 가 찾아낸 두 곳이다. 둘 다 **배포본에서만** 문제가 되고 개발
환경에서는 예전 그대로다.

============================  ==================================================
① 기본 경로의 기준 폴더        예전엔 언제나 소스 위치(``parents[4]``).
                              묶으면 **임시로 풀린 폴더** 위를 가리켜 DB 가
                              프로그램을 끌 때 사라진다.
② ``.env`` 를 찾는 자리        예전엔 ``".env"`` 하나 — **현재 작업 디렉터리**
                              기준. 바로가기로 켜면 찾지 못한다.
============================  ==================================================

⭐ **우선순위는 그대로다**
==========================
::

    환경변수  >  .env  >  기본값

Electron 은 이미 ``DATABASE_PATH`` 를 사용자 데이터 폴더로 넘긴다. 그 값이
언제나 이기므로, 이번 수정은 **아무것도 지정하지 않았을 때**만 달라진다.

⛔ 업무 로직을 건드리지 않았다 — 계산·매칭·인증기간·월별 누적·구매유형 어느
것도 이 파일의 관심사가 아니다.

.. note::
    묶인 실행(:data:`sys.frozen`)은 이 환경에서 만들 수 없으므로 **그 표시만
    흉내 내어** 확인한다. 실제 PyInstaller 빌드는 Windows 에서 해야 한다.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from procurement.core.config.settings import (
    Settings,
    _env_files,
    default_root,
    is_frozen,
    user_data_root,
)


@pytest.fixture
def frozen(monkeypatch: pytest.MonkeyPatch) -> None:
    """PyInstaller 번들 실행을 흉내 낸다 — ``sys.frozen`` 표시만 붙인다."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)


@pytest.fixture
def appdata(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Windows 의 ``%APPDATA%`` 를 흉내 낸다."""
    root = tmp_path / "AppData" / "Roaming"
    root.mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(root))
    return root


def _settings(**env: str) -> Settings:
    """주어진 환경변수만 놓고 설정을 새로 만든다."""
    return Settings(**env)  # type: ignore[arg-type]


def _in_fresh_process(
    statement: str,
    *,
    appdata: Path,
    frozen: bool = False,
    extra: dict[str, str] | None = None,
) -> str:
    """새 프로세스에서 설정을 읽어 한 줄을 돌려준다.

    필드 기본값과 ``.env`` 자리는 **모듈을 읽을 때** 정해지므로 같은 프로세스
    안에서 바꿔치기할 수 없다. 실제 배포와 같은 조건을 만들려면 프로세스를
    새로 띄우는 수밖에 없다.

    ⛔ 작업 디렉터리를 **저장소 밖**으로 두고 실행한다 — 바탕화면 바로가기로
    켠 상황과 같게 하려는 것이다.
    """
    import subprocess
    import tempfile

    source = Path("src").resolve()
    preamble = "import sys\n"
    if frozen:
        preamble += "sys.frozen = True\n"
    preamble += f"sys.path.insert(0, {str(source)!r})\n"
    preamble += "from procurement.core.config.settings import Settings\n"

    environment = dict(os.environ)
    environment.pop("XDG_DATA_HOME", None)
    environment["APPDATA"] = str(appdata)
    environment.update(extra or {})

    with tempfile.TemporaryDirectory() as elsewhere:
        completed = subprocess.run(
            [sys.executable, "-c", preamble + statement],
            capture_output=True,
            text=True,
            cwd=elsewhere,
            env=environment,
            check=True,
        )
    return completed.stdout.strip()


# ======================================================================
# §4  묶였을 때의 기준 폴더
# ======================================================================
class TestTheBundledRootIsTheUserDataFolder:
    def test_1_it_is_not_frozen_in_development(self) -> None:
        """개발 환경에서는 묶이지 않은 상태다 — 예전 그대로."""
        assert is_frozen() is False

    def test_2_development_still_points_at_the_project_root(self) -> None:
        """⭐ 개발 환경의 기준 폴더가 바뀌지 않았다.

        ``src/procurement/core/config/settings.py`` 기준 5단계 위 —
        즉 저장소 루트다.
        """
        expected = Path(__file__).resolve().parents[1]

        assert default_root() == expected

    def test_3_bundled_points_at_the_user_data_folder(self, frozen: None, appdata: Path) -> None:
        """⭐ 묶이면 사용자 데이터 폴더를 가리킨다 — 임시 폴더가 아니다."""
        assert default_root() == appdata / "procurement-desktop"

    def test_4_the_bundled_database_lands_outside_the_program(self, tmp_path: Path) -> None:
        """묶인 상태의 기본 DB 경로가 사용자 데이터 폴더 안이다.

        ⛔ 프로그램을 새 버전으로 덮어도 여기 있는 데이터는 지워지지 않는다.

        .. note::
            필드 기본값은 **모듈을 읽을 때** 정해지므로 같은 프로세스 안에서
            바꿔치기할 수 없다. 그래서 실제 배포와 같은 조건 — 새 프로세스 ·
            ``%APPDATA%`` 지정 · ``sys.frozen`` — 으로 확인한다.
        """
        appdata = tmp_path / "AppData" / "Roaming"
        appdata.mkdir(parents=True)

        printed = _in_fresh_process("print(Settings().db_file)", appdata=appdata, frozen=True)

        assert Path(printed) == appdata / "procurement-desktop" / "database" / "procurement.db"

    def test_5_the_folder_name_matches_the_desktop_app(self) -> None:
        """Electron 의 ``userData`` 와 **같은 이름**이라야 같은 자리를 본다."""
        import json

        package = json.loads(Path("package.json").read_text(encoding="utf-8"))

        assert user_data_root().name == package["name"]

    @pytest.mark.skipif(os.name == "nt", reason="POSIX 환경의 대체 경로 규칙")
    def test_6_without_appdata_it_falls_back_to_xdg(
        self, frozen: None, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Windows 가 아니면 XDG 규칙을 따른다 — 개발자 PC 에서도 확인 가능하다."""
        monkeypatch.delenv("APPDATA", raising=False)
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))

        assert user_data_root() == tmp_path / "share" / "procurement-desktop"


# ======================================================================
# §4  ⭐ 환경변수가 언제나 이긴다
# ======================================================================
class TestTheEnvironmentVariableAlwaysWins:
    def test_7_database_path_beats_the_bundled_default(
        self, frozen: None, appdata: Path, tmp_path: Path
    ) -> None:
        """⭐ 묶인 상태에서도 ``DATABASE_PATH`` 가 이긴다.

        Electron 이 넘기는 값이 그대로 쓰인다는 뜻이다 — 이번 수정이 기존
        동작을 가리지 않는다.
        """
        chosen = tmp_path / "chosen"

        database = _settings(DATABASE_PATH=str(chosen)).db_file

        assert database == chosen / "procurement.db"

    def test_8_database_path_beats_the_development_default(self, tmp_path: Path) -> None:
        """개발 환경에서도 마찬가지다 — 우선순위가 예전과 같다."""
        chosen = tmp_path / "chosen"

        assert _settings(DATABASE_PATH=str(chosen)).db_file == chosen / "procurement.db"

    def test_9_the_filename_is_still_configurable(self, tmp_path: Path) -> None:
        """``DATABASE_FILENAME`` 도 예전처럼 동작한다."""
        settings = _settings(DATABASE_PATH=str(tmp_path), DATABASE_FILENAME="other.db")

        assert settings.db_file == tmp_path / "other.db"


# ======================================================================
# §5  .env 를 찾는 자리
# ======================================================================
class TestTheSettingsFileIsFoundOutsideTheWorkingDirectory:
    def test_10_the_user_data_env_is_one_of_the_places(self, appdata: Path) -> None:
        """사용자 데이터 폴더의 ``.env`` 가 후보에 들어 있다."""
        assert str(appdata / "procurement-desktop" / ".env") in _env_files()

    def test_11_the_working_directory_env_is_still_there(self, appdata: Path) -> None:
        """⛔ 예전 자리를 빼지 않았다 — 개발 환경이 그대로 동작해야 한다."""
        assert ".env" in _env_files()

    def test_12_the_working_directory_env_wins(self, appdata: Path) -> None:
        """둘 다 있으면 작업 디렉터리 쪽이 이긴다 — 개발 동작을 바꾸지 않으려고."""
        places = _env_files()

        assert places.index(".env") > places.index(str(appdata / "procurement-desktop" / ".env"))

    def test_12b_a_settings_file_outside_the_working_directory_is_read(
        self, tmp_path: Path
    ) -> None:
        """⭐ 작업 디렉터리가 딴 곳이어도 사용자 데이터 폴더의 ``.env`` 를 읽는다."""
        appdata = tmp_path / "AppData" / "Roaming"
        (appdata / "procurement-desktop").mkdir(parents=True)
        (appdata / "procurement-desktop" / ".env").write_text(
            "ADMIN_API_TOKEN=from-user-data\n", encoding="utf-8"
        )

        printed = _in_fresh_process("print(Settings().ADMIN_API_TOKEN)", appdata=appdata)

        assert printed == "from-user-data"

    def test_12c_the_environment_variable_still_beats_the_file(self, tmp_path: Path) -> None:
        """⭐ 그 파일이 있어도 환경변수가 이긴다 — 우선순위가 그대로다."""
        appdata = tmp_path / "AppData" / "Roaming"
        (appdata / "procurement-desktop").mkdir(parents=True)
        (appdata / "procurement-desktop" / ".env").write_text(
            "ADMIN_API_TOKEN=from-user-data\n", encoding="utf-8"
        )

        printed = _in_fresh_process(
            "print(Settings().ADMIN_API_TOKEN)",
            appdata=appdata,
            extra={"ADMIN_API_TOKEN": "from-environment"},
        )

        assert printed == "from-environment"

    def test_13_no_api_key_is_baked_in(self) -> None:
        """⛔ API 키가 코드에 들어 있지 않다 — 고객 기관이 발급받는 값이다."""
        settings = _settings()

        assert settings.SMPP_API_KEY is None
        assert settings.STARTUP_API_KEY is None


# ======================================================================
# 업무 로직을 건드리지 않았다
# ======================================================================
class TestNothingElseChanged:
    def test_14_the_year_axis_is_untouched(self) -> None:
        """연도 귀속 기준일이 그대로다."""
        assert _settings().PURCHASE_PERIOD_DATE_FIELD == "resolution_date"

    def test_15_the_settings_module_holds_no_business_rule(self) -> None:
        """⛔ 이번 수정이 업무 규칙을 들여오지 않았다.

        설명글에 정책 이름이 나오는 것은 「이 API 가 무엇을 조회하는가」를 적은
        것이므로 문제가 아니다. 무서운 것은 설정 모듈이 **계산·판정 코드를
        끌어오는 것**이다 — 그것을 본다.
        """
        import ast

        tree = ast.parse(
            (Path("src") / "procurement" / "core" / "config" / "settings.py").read_text(
                encoding="utf-8"
            )
        )
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)
            elif isinstance(node, ast.Import):
                imported += [alias.name for alias in node.names]

        for module in imported:
            assert not module.startswith("procurement."), module
