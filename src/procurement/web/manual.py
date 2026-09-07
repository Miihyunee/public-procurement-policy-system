"""
procurement.web.manual

프로그램 안에서 내려주는 **사용자 매뉴얼 PDF** 를 읽어 오는 헬퍼입니다.

고객이 설치파일과 매뉴얼을 따로 보관하다 보면 어느 것이 지금 쓰는 버전인지
알기 어렵습니다. 그래서 매뉴얼을 프로그램에 함께 담고, 화면에서 바로 받을 수
있게 합니다 — 지금 쓰는 프로그램에 담긴 매뉴얼이 언제나 그 프로그램의
매뉴얼입니다.

파일은 :mod:`scripts.build_manual_pdf` 가 ``docs/USER_MANUAL.md`` 로부터
만들어 이 자리에 둡니다.

.. warning::
    ⛔ **여기서 매뉴얼을 만들지 않습니다.** 마크다운을 실행 중에 변환하지
    않으며(런타임 의존성이 늘어납니다), 완성된 PDF 를 그대로 읽어 보낼
    뿐입니다.
"""

from __future__ import annotations

from pathlib import Path

#: 프로그램에 담긴 매뉴얼 PDF 경로.
#:
#: ⚠️ 파일명은 **ASCII** 입니다. PyInstaller·electron-builder 가 다루는
#:    경로여서, 한글 파일명은 도구·인코딩에 따라 어긋날 수 있습니다.
MANUAL_PDF_PATH = Path(__file__).resolve().parent / "manual" / "user_manual.pdf"

#: 고객이 받을 때 보이는 파일명. 내려받기 응답 헤더에서만 씁니다.
MANUAL_FILE_NAME = "공공구매정책관리시스템_사용자매뉴얼.pdf"

#: PDF media type.
PDF_MEDIA_TYPE = "application/pdf"


def manual_exists() -> bool:
    """매뉴얼 PDF 가 프로그램에 담겨 있는지 확인합니다.

    Returns:
        파일이 있으면 ``True``.

    Note:
        화면은 이 결과(응답 상태)를 보고 내려받기 단추를 보여 줄지 정합니다.
        ⛔ 없는데 있는 것처럼 단추를 두지 않습니다.
    """
    return MANUAL_PDF_PATH.is_file()


def manual_size() -> int:
    """매뉴얼 PDF 의 크기(바이트)를 반환합니다.

    Returns:
        파일 크기.

    Raises:
        OSError: 파일이 없는 경우.

    Note:
        「있는지」만 묻는 요청(``HEAD``)에 2 MB 를 읽지 않기 위한 것입니다.
    """
    return MANUAL_PDF_PATH.stat().st_size


def read_manual_pdf() -> bytes:
    """매뉴얼 PDF 를 읽어 반환합니다.

    Returns:
        PDF 파일 내용.

    Raises:
        FileNotFoundError: 파일이 없는 경우(패키징 누락).
    """
    return MANUAL_PDF_PATH.read_bytes()
