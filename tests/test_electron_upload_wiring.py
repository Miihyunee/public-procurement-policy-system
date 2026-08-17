"""
tests.test_electron_upload_wiring

**Electron 업로드 연결의 정적 구조** 검증.

헤드리스 환경에서는 Electron GUI 를 실제로 띄울 수 없으므로, 창을 띄우지 않고
확인할 수 있는 성질만 고정합니다. 여기서 잡으려는 것은 "화면이 예쁜가" 가 아니라
**업무 로직이 JavaScript 로 새어 나가지 않았는가** 입니다(지시서 §9 · §36).

.. note::
    백엔드 생명주기는 ``node scripts/verify-backend.js`` 가 별도로 검증합니다
    (실제 프로세스 기동 포함).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ELECTRON_DIR = PROJECT_ROOT / "electron"
INDEX_HTML = PROJECT_ROOT / "src" / "procurement" / "web" / "static" / "index.html"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TestNoBusinessLogicInJavaScript:
    """⛔ 업무규칙을 JavaScript 로 재작성하지 않았다."""

    #: 업무 판정을 JS 에서 했다면 반드시 등장할 낱말들.
    FORBIDDEN = (
        "valid_from",
        "validity",
        "achievement_rate =",
        "인증 유효기간",
        "상계",
        "달성률 계산",
    )

    @pytest.mark.parametrize("name", ["main.js", "preload.js", "uploads.js", "backend.js"])
    def test_electron_files_have_no_business_terms(self, name: str) -> None:
        source = _read(ELECTRON_DIR / name)
        for term in self.FORBIDDEN:
            assert term not in source, f"{name}: {term}"

    def test_electron_does_not_touch_sqlite(self) -> None:
        """Electron 이 DB 를 직접 열지 않는다.

        주석의 구조 설명에는 SQLite 가 등장할 수 있으므로, **실제 모듈을
        불러오는지**로 판정합니다.
        """
        for path in ELECTRON_DIR.glob("*.js"):
            source = _read(path)
            required = set(re.findall(r"require\(\s*[\"']([^\"']+)[\"']\s*\)", source))
            assert not [name for name in required if "sqlite" in name.lower()], path.name

    def test_upload_module_only_does_dialogs_and_transfer(self) -> None:
        """``uploads.js`` 는 대화상자와 파일 전달만 한다."""
        source = _read(ELECTRON_DIR / "uploads.js")

        assert "showOpenDialog" in source
        assert "showSaveDialog" in source
        # 엑셀 해석을 시도하지 않는다.
        assert "xlsx" not in source.lower().replace('"xlsx"', "").replace(".xlsx", "")


class TestPreloadSurfaceIsMinimal:
    """preload 가 여는 문은 최소한이다."""

    def test_exposes_only_expected_keys(self) -> None:
        source = _read(ELECTRON_DIR / "preload.js")
        keys = set(re.findall(r"^\s{2}(\w+):", source, flags=re.MULTILINE))

        assert keys == {"isDesktop", "saveTemplate", "selectUploadFile", "versions"}

    def test_does_not_expose_ipc_renderer_itself(self) -> None:
        """⛔ ``ipcRenderer`` 를 통째로 노출하지 않는다(임의 채널 호출 방지)."""
        source = _read(ELECTRON_DIR / "preload.js")

        assert "exposeInMainWorld" in source
        assert "ipcRenderer," not in source
        assert ": ipcRenderer" not in source

    def test_does_not_expose_node_modules(self) -> None:
        source = _read(ELECTRON_DIR / "preload.js")
        for forbidden in ("node:fs", "node:child_process", "require(\"fs\")"):
            assert forbidden not in source


class TestSecuritySettingsPreserved:
    """기존 보안 설정을 유지한다."""

    def test_context_isolation_and_sandbox(self) -> None:
        source = _read(ELECTRON_DIR / "main.js")

        assert "contextIsolation: true" in source
        assert "nodeIntegration: false" in source
        assert "sandbox: true" in source

    def test_handlers_are_registered_before_window(self) -> None:
        """창을 띄우기 전에 IPC 핸들러가 준비된다(첫 클릭이 실패하지 않도록)."""
        source = _read(ELECTRON_DIR / "main.js")

        assert source.index("registerUploadHandlers(backend.port)") < source.index(
            "createWindow(backend.port)"
        )

    def test_backend_binds_loopback_only(self) -> None:
        """백엔드는 ``127.0.0.1`` 로만 바인딩한다(파일 경로 전달 방식의 전제)."""
        source = _read(ELECTRON_DIR / "backend.js")

        assert "127.0.0.1" in source
        assert "0.0.0.0" not in source


class TestRendererUsesBackendOnly:
    """화면은 백엔드 API 로만 일한다."""

    def test_upload_calls_the_backend_endpoints(self) -> None:
        """화면은 검증·저장 모두 백엔드 API 를 호출한다."""
        source = _read(INDEX_HTML)
        assert "/uploads/purchases/validate" in source
        assert '"/uploads/purchases"' in source

    def test_year_is_sent_from_the_screen(self) -> None:
        """⛔ 대상 기간은 **화면이 지정**한다. 파일에서 유추하지 않는다."""
        source = _read(INDEX_HTML)
        assert "upload-year" in source
        assert "year: Number(" in source

    def test_screen_does_not_compute_the_period(self) -> None:
        """연도 → 기간 환산은 백엔드가 한다(화면에 날짜 계산이 없다)."""
        source = _read(INDEX_HTML)
        assert "period_start" not in source
        assert "period_end" not in source

    def test_upload_ui_elements_exist(self) -> None:
        source = _read(INDEX_HTML)
        for element_id in (
            "upload-template",
            "upload-pick",
            "upload-run",
            "upload-save",
            "upload-year",
            "upload-summary",
            "upload-issues",
        ):
            assert f'id="{element_id}"' in source

    def test_renderer_does_not_parse_excel(self) -> None:
        """화면이 엑셀을 직접 해석하지 않는다."""
        source = _read(INDEX_HTML)

        assert "openpyxl" not in source
        assert "SheetJS" not in source
        assert "XLSX." not in source

    def test_renderer_sends_only_the_path(self) -> None:
        """파일 내용이 아니라 **경로만** 백엔드로 보낸다."""
        source = _read(INDEX_HTML)

        assert "file_path: uploadPath" in source
        assert "FileReader" not in source

    def test_browser_mode_degrades_gracefully(self) -> None:
        """데스크톱이 아니면 버튼을 막고 안내한다(브라우저에서 열었을 때)."""
        source = _read(INDEX_HTML)

        assert "데스크톱 앱에서 사용할 수 있습니다" in source
