"""
STEP 126-2 — 한글 Windows 에서 **시작조차 못 하던** 문제.

무슨 일이 있었나
=================
Windows 에서 만든 실제 EXE 를 처음 띄우자 이렇게 죽었다::

    File "__main__.py", line 63, in _run_init
    UnicodeEncodeError: 'cp949' codec can't encode character '\\u2014'

한글 Windows 의 기본 출력 인코딩은 ``cp949`` 다. 초기화 보고문에 쓰는
줄표(``—`` U+2014)를 이 인코딩으로 쓸 수 없어서, 그 줄을 찍는 순간
프로그램이 통째로 멈췄다.

⚠️ **Windows 환경 차이가 아니라 제품 결함이다.** 고객 PC 도 한글
Windows 이므로 똑같이 시작 단계에서 죽는다.

왜 글자를 지우지 않았나
=======================
``bootstrap.py`` 한 파일에만 줄표가 29개 있고, 안내문 전체가 한글이다.
글자를 골라내는 방식은 다음에 누가 기호 하나만 써도 같은 일이 다시
난다. **출력 인코딩 자체를 UTF-8 로 고정**했다.

⛔ 업무 로직을 건드리지 않았다 — 무엇을 계산하고 무엇을 안내할지는
그대로이고, 그것을 **어떤 인코딩으로 내보내는가**만 정했다.
"""

from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path

import pytest

from procurement.__main__ import _use_utf8_output

ROOT = Path(__file__).resolve().parents[1]

#: 사고를 낸 바로 그 글자.
EM_DASH = "—"


# ======================================================================
# 사고 재현
# ======================================================================
class TestTheCharacterThatBrokeIt:
    def test_1_cp949_really_cannot_hold_it(self) -> None:
        """전제 확인 — 이 글자는 cp949 로 쓸 수 없다."""
        with pytest.raises(UnicodeEncodeError):
            EM_DASH.encode("cp949")

    def test_2_the_startup_report_contains_it(self) -> None:
        """⭐ 그 글자가 **시작 안내문에 실제로** 들어 있다.

        이 시험이 사고를 재현한다 — 이 글자가 안내문에 있는 한, 출력
        인코딩을 고정하지 않으면 한글 Windows 에서 죽는다.
        """
        source = (ROOT / "src" / "procurement" / "database" / "bootstrap.py").read_text(
            encoding="utf-8"
        )

        assert EM_DASH in source


# ======================================================================
# 고친 것
# ======================================================================
class TestTheOutputEncodingIsPinned:
    def test_3_it_switches_a_cp949_stream_to_utf8(self) -> None:
        """⭐ cp949 로 열린 출력을 UTF-8 로 바꾼다."""
        original = sys.stdout
        sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp949")
        try:
            _use_utf8_output()
            chosen = sys.stdout.encoding
        finally:
            sys.stdout = original

        assert chosen == "utf-8"

    def test_4_a_stream_that_cannot_be_reconfigured_is_left_alone(self) -> None:
        """⛔ 재설정을 못 하는 출력이라고 해서 죽지 않는다."""
        original = sys.stdout
        sys.stdout = io.StringIO()  # reconfigure 가 없다
        try:
            _use_utf8_output()
        finally:
            sys.stdout = original

    def test_5_both_streams_are_covered(self) -> None:
        """오류 출력도 함께 고정한다 — 예외 메시지에도 한글이 들어간다."""
        source = (ROOT / "src" / "procurement" / "__main__.py").read_text(encoding="utf-8")

        assert "sys.stdout, sys.stderr" in source

    def test_6_unencodable_characters_never_stop_the_program(self) -> None:
        """⛔ 어떤 글자도 프로그램을 멈추게 하지 않는다(``errors="replace"``)."""
        original = sys.stdout
        sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp949")
        try:
            _use_utf8_output()
            print(f"초기화 완료 {EM_DASH} 정상")  # 예전이라면 여기서 죽었다
        finally:
            sys.stdout = original


# ======================================================================
# ⭐ 진짜 확인 — 실제로 cp949 환경에서 켜 본다
# ======================================================================
class TestTheProgramStartsUnderKoreanWindowsEncoding:
    def test_7_init_survives_a_cp949_console(self, tmp_path: Path) -> None:
        """⭐ 한글 Windows 의 출력 환경을 흉내 내어 ``init`` 을 돌린다.

        ``PYTHONIOENCODING=cp949`` 는 Windows 가 아닌 곳에서도 같은 조건을
        만든다. 고친 것이 없다면 이 시험은 사고와 **똑같은**
        :class:`UnicodeEncodeError` 로 실패한다.

        ⛔ 고객 데이터를 쓰지 않는다 — 빈 임시 DB 하나를 만들 뿐이다.
        """
        environment = {
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(ROOT / "src"),
            "PYTHONIOENCODING": "cp949",
        }

        completed = subprocess.run(
            [sys.executable, "-m", "procurement", "init", "--db", str(tmp_path / "t.db")],
            capture_output=True,
            text=True,
            env=environment,
            cwd=tmp_path,
        )

        assert "UnicodeEncodeError" not in completed.stderr, completed.stderr
        assert completed.returncode == 0, completed.stderr
