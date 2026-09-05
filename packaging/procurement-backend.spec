# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 사양 — Python 백엔드를 실행파일 하나로 묶는다 (STEP 125).

    pyinstaller packaging/procurement-backend.spec

만들어지는 것::

    dist/procurement/procurement.exe      (Windows)
    dist/procurement/procurement          (그 밖)

Electron 이 그 파일을 `resources/backend/` 에 두고 실행한다
(`electron/main.js` 의 `backendConfig()`).

.. warning::
    ⛔ **이 파일은 이 저장소에서 빌드해 본 적이 없다.** 개발 환경이
    Linux 이고 PyInstaller 는 **크로스 컴파일을 하지 않는다** — Windows
    실행파일은 Windows 에서 빌드해야 한다. 여기 적힌 것은 STEP 124 의
    의존성 조사에 근거한 **초안**이며, 실제 빌드에서 조정될 수 있다.

.. note::
    **onedir 로 둔다**(`--onefile` 아님). 한 파일로 묶으면 실행할 때마다
    임시 폴더에 압축을 풀어 **시작이 느리고**, 백신이 그 동작을 의심하는 일이
    잦다. 폴더째 두면 Electron 이 `resources/` 안에 그대로 담기므로 고객에게는
    어차피 보이지 않는다.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

#: 저장소 루트. `.spec` 은 `packaging/` 안에 있다.
#: ⛔ 절대경로를 적지 않는다 — 빌드하는 사람의 PC 마다 다르다.
ROOT = Path(SPECPATH).resolve().parent

#: 진입점. ⛔ 새 main 파일을 만들지 않고 기존 CLI 를 그대로 쓴다.
#: `procurement.__main__:main` 이 `run` 하위 명령을 갖고 있다.
ENTRY = ROOT / "src" / "procurement" / "__main__.py"

#: 화면 파일. `procurement/web/page.py` 가 `__file__` 기준으로 읽으므로
#: 패키지 안의 **같은 자리**에 넣어야 한다.
STATIC = ROOT / "src" / "procurement" / "web" / "static" / "index.html"

# ----------------------------------------------------------------------
# 숨은 import
#
# ⛔ 되는대로 다 넣지 않는다(지시서 §9). 아래는 **정적 분석으로 찾기 어려운
#    것**만 적은 것이며, 실제 빌드에서 빠지는 것이 나오면 그때 더한다.
# ----------------------------------------------------------------------
hiddenimports = [
    # uvicorn 은 프로토콜·루프 구현을 **문자열 이름으로** 늦게 불러온다.
    *collect_submodules("uvicorn"),
    # pydantic v2 의 native 코어. 보통 자동으로 잡히지만 명시해 둔다.
    "pydantic_core",
]

a = Analysis(
    [str(ENTRY)],
    pathex=[str(ROOT / "src")],
    binaries=[],
    # 화면 파일을 패키지 안 제자리에 넣는다.
    datas=[(str(STATIC), "procurement/web/static")],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # 배포본에 필요 없는 것들. ⛔ 런타임 의존성을 줄이는 것이 아니라
    #    **개발 도구**를 빼는 것이다.
    excludes=["pytest", "mypy", "ruff", "coverage"],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="procurement",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # ⛔ UPX 압축은 백신 오탐을 늘린다.
    # ⚠️ 콘솔을 남긴다. 로깅 체계가 아직 없어서(STEP 124 §10) 표준출력이
    #    유일한 오류 단서이고, Electron 이 그것을 받아 준다.
    #    창은 Electron 이 띄우므로 고객에게 이 콘솔이 보이지는 않는다.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="procurement",
)
