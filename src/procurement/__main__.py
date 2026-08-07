"""
Entry point for ``python -m procurement``.

서브커맨드만 정의하고 실제 동작은 각 모듈에 위임합니다(얇게 유지).

Usage:
    python -m procurement init      # DB 초기화 + 정책 등록 + 상태 점검
    python -m procurement run       # FastAPI 개발 서버 실행
    python -m procurement health    # 초기화 상태만 점검

    # Swagger(OpenAPI) 문서: http://127.0.0.1:8000/docs
"""

from __future__ import annotations

import argparse
from pathlib import Path

from procurement.database.bootstrap import bootstrap, verify_bootstrap

#: 개발 서버 기본 바인딩 주소·포트
_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8000


def _build_parser() -> argparse.ArgumentParser:
    """서브커맨드 파서를 구성합니다."""
    parser = argparse.ArgumentParser(
        prog="procurement",
        description="공공기관 우선구매 정책 달성률 시스템",
    )
    subparsers = parser.add_subparsers(dest="command")

    init_parser = subparsers.add_parser(
        "init", help="DB 초기화, 기본 정책 등록, 상태 점검을 수행합니다."
    )
    init_parser.add_argument("--db", type=Path, default=None, help="DB 파일 경로")
    init_parser.add_argument(
        "--no-seed", action="store_true", help="기본 정책 등록을 건너뜁니다."
    )

    run_parser = subparsers.add_parser("run", help="FastAPI 개발 서버를 실행합니다.")
    run_parser.add_argument("--host", default=_DEFAULT_HOST, help="바인딩 주소")
    run_parser.add_argument("--port", type=int, default=_DEFAULT_PORT, help="포트")

    health_parser = subparsers.add_parser("health", help="초기화 상태를 점검합니다.")
    health_parser.add_argument("--db", type=Path, default=None, help="DB 파일 경로")

    return parser


def _run_init(db_path: Path | None, *, seed: bool) -> int:
    """초기화를 수행하고 점검 결과를 출력합니다."""
    report = bootstrap(db_path, seed=seed)
    print(report.format_report())
    if not report.healthy:
        return 1
    print("\n다음 명령으로 서버를 실행하세요: python -m procurement run")
    return 0


def _run_health(db_path: Path | None) -> int:
    """초기화 상태를 점검하고 결과를 출력합니다."""
    report = verify_bootstrap(db_path)
    print(report.format_report())
    return 0 if report.healthy else 1


def _run_server(host: str, port: int) -> int:
    """FastAPI 개발 서버를 실행합니다."""
    import uvicorn

    uvicorn.run("procurement.app:app", host=host, port=port)
    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점.

    Args:
        argv: 명령행 인자. ``None`` 이면 ``sys.argv`` 를 사용합니다.

    Returns:
        프로세스 종료 코드. 정상은 ``0``, 점검 실패는 ``1``.
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "init":
        return _run_init(args.db, seed=not args.no_seed)
    if args.command == "health":
        return _run_health(args.db)
    if args.command == "run":
        return _run_server(args.host, args.port)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
