"""
Entry point for ``python -m procurement``.

FastAPI 개발 서버를 실행합니다. 애플리케이션 조립과 엔드포인트 정의는
:mod:`procurement.app` 에 있으며, 본 모듈은 서버 기동만 담당합니다(얇게 유지).

Usage:
    python -m procurement          # 개발 서버 실행 (http://127.0.0.1:8000)
    procurement                    # 설치된 경우 동일

    # Swagger(OpenAPI) 문서: http://127.0.0.1:8000/docs
"""

from __future__ import annotations


def main() -> None:
    """FastAPI 개발 서버를 실행합니다."""
    import uvicorn

    uvicorn.run("procurement.app:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
