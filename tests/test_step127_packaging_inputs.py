"""
STEP 125 — 배포 준비물이 **서로 맞물려 있는가**.

무엇을 확인하는가
=================
Windows 실행파일은 이 환경에서 만들 수 없다(Linux · PyInstaller 는 크로스
컴파일을 하지 않는다). 그래서 **빌드 없이도 확인할 수 있는 것**을 고정한다 —
빌드 준비물끼리 가리키는 자리가 어긋나지 않는지.

::

    packaging/procurement-backend.spec   →  dist/procurement/
    package.json  extraResources          →  resources/backend/
    electron/main.js  backendConfig()     →  resources/backend/procurement(.exe)

가운데 한 곳만 이름이 달라도 **설치는 되는데 프로그램이 안 뜬다.** 그런
어긋남은 Windows 에서야 드러나므로 여기서 미리 묶어 둔다.

⛔ 이 시험은 빌드를 하지 않는다. 파일이 서로를 제대로 가리키는지만 본다.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_JSON = ROOT / "package.json"
SPEC = ROOT / "packaging" / "procurement-backend.spec"
MAIN_JS = ROOT / "electron" / "main.js"


@pytest.fixture
def package() -> dict[str, object]:
    return dict(json.loads(PACKAGE_JSON.read_text(encoding="utf-8")))


@pytest.fixture
def spec() -> str:
    return SPEC.read_text(encoding="utf-8")


@pytest.fixture
def main_js() -> str:
    return MAIN_JS.read_text(encoding="utf-8")


# ======================================================================
# 준비물이 서로를 가리키는가
# ======================================================================
class TestTheBuildInputsAgree:
    def test_1_the_spec_exists_and_names_the_backend(self, spec: str) -> None:
        """PyInstaller 사양이 만드는 이름이 ``procurement`` 다."""
        assert 'name="procurement"' in spec

    def test_2_the_installer_picks_up_what_the_spec_produces(
        self, package: dict[str, object]
    ) -> None:
        """⭐ 설치본이 담는 폴더가 PyInstaller 결과물 자리와 같다."""
        build = package["build"]
        assert isinstance(build, dict)
        resources = build["extraResources"]
        assert isinstance(resources, list)

        assert resources[0]["from"] == "dist/procurement"
        assert resources[0]["to"] == "backend"

    def test_3_electron_looks_where_the_installer_put_it(self, main_js: str) -> None:
        """⭐ Electron 이 찾는 자리가 설치본이 넣은 자리와 같다."""
        assert 'path.join(process.resourcesPath, "backend", executable)' in main_js

    def test_4_windows_gets_the_exe_suffix(self, main_js: str) -> None:
        """⚠️ Windows 에서는 확장자가 있어야 한다 — 없으면 ENOENT 로 못 찾는다."""
        assert '"procurement.exe"' in main_js
        assert 'process.platform === "win32"' in main_js

    def test_5_no_absolute_path_is_baked_in(self, spec: str, main_js: str) -> None:
        """⛔ 빌드하는 사람의 PC 경로를 넣지 않았다."""
        for source in (spec, main_js):
            assert "C:\\Users" not in source
            assert "/home/" not in source


# ======================================================================
# 화면 파일이 실행파일 안에 들어가는가
# ======================================================================
class TestTheScreenTravelsWithTheBackend:
    def test_6_the_spec_bundles_the_page(self, spec: str) -> None:
        """``index.html`` 을 패키지 안 **제자리**에 넣는다."""
        assert '"procurement/web/static"' in spec

    def test_7_that_is_where_the_code_reads_it_from(self) -> None:
        """⭐ 코드가 읽는 자리와 같은 자리다.

        ``page.py`` 가 ``__file__`` 기준으로 읽으므로, 번들 안에서도 패키지
        옆에 있어야 한다.
        """
        from procurement.web.page import INDEX_HTML_PATH

        tail = INDEX_HTML_PATH.parts[-4:]

        assert tail == ("procurement", "web", "static", "index.html")

    def test_8_the_page_is_a_single_file(self) -> None:
        """딸린 JS·CSS 파일이 없다 — 챙길 정적 파일이 하나뿐이다."""
        static = ROOT / "src" / "procurement" / "web" / "static"

        assert sorted(item.name for item in static.iterdir()) == ["index.html"]


# ======================================================================
# 고객 데이터를 지우지 않는가
# ======================================================================
class TestTheCustomerDataSurvives:
    def test_9_uninstall_keeps_the_data_folder(self, package: dict[str, object]) -> None:
        """⭐ 프로그램을 지워도 ``%APPDATA%`` 의 고객 데이터는 남긴다."""
        build = package["build"]
        assert isinstance(build, dict)
        nsis = build["nsis"]
        assert isinstance(nsis, dict)

        assert nsis["deleteAppDataOnUninstall"] is False

    def test_10_the_data_folder_name_matches_the_app(self, package: dict[str, object]) -> None:
        """설정이 쓰는 폴더 이름과 앱 이름이 같아야 같은 자리를 본다."""
        from procurement.core.config.settings import user_data_root

        assert user_data_root().name == package["name"]

    def test_11_the_installer_output_is_not_committed(self) -> None:
        """⛔ 빌드 결과물을 저장소에 넣지 않는다(지시서 §37)."""
        ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

        for folder in ("build/", "dist/", "release/"):
            assert folder in ignored

    def test_11b_the_build_spec_itself_is_committed(self) -> None:
        """⭐ 반대로 **빌드 사양은 반드시 남아야** 한다.

        ``.gitignore`` 의 ``*.spec`` 은 PyInstaller 가 자동 생성한 파일을
        겨냥한 규칙이라, 손으로 쓴 이 사양까지 함께 빨아들인다. 그러면
        Windows 담당자가 저장소를 받아도 **빌드할 방법이 없다.** 조용히
        일어나는 일이라 여기서 붙잡는다.
        """
        import subprocess

        completed = subprocess.run(
            ["git", "check-ignore", str(SPEC.relative_to(ROOT))],
            cwd=ROOT,
            capture_output=True,
            text=True,
        )

        assert completed.returncode != 0, "빌드 사양이 .gitignore 에 걸려 있다"


# ======================================================================
# 빌드 방법이 적혀 있는가
# ======================================================================
class TestTheBuildIsDocumented:
    def test_12_both_build_steps_have_a_command(self, package: dict[str, object]) -> None:
        scripts = package["scripts"]
        assert isinstance(scripts, dict)

        assert scripts["build:backend"] == "pyinstaller packaging/procurement-backend.spec"
        assert "electron-builder" in scripts["build:desktop"]

    def test_13_the_spec_says_it_was_not_built_here(self, spec: str) -> None:
        """⚠️ 빌드해 본 적이 없다는 사실을 파일 자체에 적어 둔다.

        읽는 사람이 「검증된 설정」으로 오해하면 안 된다.
        """
        assert "빌드해 본 적이 없다" in spec

    def test_14_upx_is_off(self, spec: str) -> None:
        """⛔ UPX 압축을 쓰지 않는다 — 백신 오탐을 늘린다."""
        assert "upx=False" in spec
        assert "upx=True" not in spec
