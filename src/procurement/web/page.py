"""
procurement.web.page

Dashboard 정적 페이지(``static/index.html``)를 읽어 오는 헬퍼입니다.

템플릿 엔진을 도입하지 않기 위해 서버는 파일을 **그대로** 읽어 반환합니다.
페이지에 들어갈 값은 모두 브라우저가 JSON API 를 호출해 채웁니다.
"""

from __future__ import annotations

from pathlib import Path

#: Dashboard 페이지 파일 경로
INDEX_HTML_PATH = Path(__file__).resolve().parent / "static" / "index.html"


def read_index_html() -> str:
    """Dashboard 페이지 HTML 을 읽어 반환합니다.

    Returns:
        ``static/index.html`` 의 내용.

    Raises:
        FileNotFoundError: 페이지 파일이 없는 경우(패키징 누락).
    """
    return INDEX_HTML_PATH.read_text(encoding="utf-8")
