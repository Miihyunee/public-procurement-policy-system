"""
Entry point for ``python -m procurement``.

서브커맨드만 정의하고 실제 동작은 각 모듈에 위임합니다(얇게 유지).

Usage:
    python -m procurement init      # DB 초기화 + 정책 등록 + 상태 점검
    python -m procurement run       # DB 스키마 점검 후 FastAPI 개발 서버 실행
    python -m procurement health    # 초기화 상태만 점검
    python -m procurement targets --year 2026   # 확정 목표비율을 해당 연도에 등록

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
    init_parser.add_argument("--no-seed", action="store_true", help="기본 정책 등록을 건너뜁니다.")

    run_parser = subparsers.add_parser("run", help="FastAPI 개발 서버를 실행합니다.")
    run_parser.add_argument("--host", default=_DEFAULT_HOST, help="바인딩 주소")
    run_parser.add_argument("--port", type=int, default=_DEFAULT_PORT, help="포트")
    run_parser.add_argument("--db", type=Path, default=None, help="DB 파일 경로")

    health_parser = subparsers.add_parser("health", help="초기화 상태를 점검합니다.")
    health_parser.add_argument("--db", type=Path, default=None, help="DB 파일 경로")

    targets_parser = subparsers.add_parser(
        "targets", help="고객이 확정한 목표비율을 해당 연도에 등록합니다."
    )
    targets_parser.add_argument(
        "--year", type=int, required=True, help="목표비율을 등록할 연도 (예: 2026)"
    )
    targets_parser.add_argument("--db", type=Path, default=None, help="DB 파일 경로")

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


def _run_targets(db_path: Path | None, year: int) -> int:
    """고객이 확정한 목표비율을 지정한 연도에 등록합니다.

    ⛔ **저장 가능한 것만 등록합니다.** 여성기업(구매유형별 두 목표)과
    국가유공자자활용사촌(분모가 생산가능품목 구매액)은 지금 구조로 담으면 틀린
    달성률이 나오므로 넣지 않고, 왜 넣지 않았는지를 출력합니다
    (:data:`~procurement.policy.BLOCKED_TARGETS`).

    이미 등록된 값이 있으면 덮어씁니다(연도 × 정책 단위 upsert). 목표비율은
    운영자가 고치는 값이므로 **여러 번 실행해도 결과가 같습니다.**

    Args:
        db_path: DB 경로. ``None`` 이면 설정값을 사용합니다.
        year: 목표비율을 등록할 연도.

    Returns:
        정상은 ``0``. 정책이 등록되어 있지 않아 붙일 곳이 없으면 ``1``.
    """
    from procurement.database.policy_repository import PolicyRepository
    from procurement.database.policy_target_repository import PolicyTargetRepository
    from procurement.policy import BLOCKED_TARGETS, STORABLE_TARGET_RATES

    # ⚠️ 초기화되지 않은 DB 를 그냥 읽으면 sqlite 의 "no such table" 이 그대로
    #    올라와 운영자가 무엇을 해야 할지 알 수 없습니다. ``run`` 과 같은 방식으로
    #    **먼저 점검하고** 조치를 안내합니다.
    report = verify_bootstrap(db_path)
    if not report.healthy:
        print(report.format_report())
        print(
            "\n목표비율을 등록하지 않았습니다. DB 를 먼저 초기화하세요."
            "\n  python -m procurement init"
        )
        return 1

    policies = {policy.policy_code: policy for policy in PolicyRepository(db_path).find_all()}

    repository = PolicyTargetRepository(db_path)
    registered: list[str] = []
    missing: list[str] = []
    for code, rate in STORABLE_TARGET_RATES.items():
        policy = policies.get(code)
        if policy is None or policy.policy_id is None:
            missing.append(code)
            continue
        repository.upsert(year, policy.policy_id, rate)
        registered.append(f"  {policy.policy_name} ({code}) — {rate}%")

    print(f"[{year}년] 목표비율 {len(registered)}건을 등록했습니다.")
    print("\n".join(registered))

    if missing:
        print("\n정책을 찾지 못해 건너뛴 코드: " + ", ".join(sorted(missing)))

    print("\n등록하지 않은 정책 — 확정은 받았으나 지금 구조로 담을 수 없습니다:")
    for code, reason in BLOCKED_TARGETS.items():
        print(f"  {code}\n    {reason}")
    print("\n⛔ 위 두 정책은 숫자를 지어내지 않고 '계산 보류' 로 둡니다.")
    return 0


def _run_server(host: str, port: int, db_path: Path | None = None) -> int:
    """FastAPI 개발 서버를 실행합니다.

    **서버를 띄우기 전에 DB 스키마를 먼저 점검합니다.** 구(舊) 버전에서 만든 DB 를
    그대로 두고 실행하면, 조회 시점에 ``purchase.batch_id`` 컬럼이 없어
    ``IndexError`` 가 나고 대시보드가 **HTTP 500** 으로 실패합니다. 그 시점에는
    원인이 화면에 드러나지 않아 운영자가 무엇을 해야 할지 알 수 없습니다.

    따라서 점검에 실패하면 **서버를 시작하지 않고** 원인과 조치를 출력합니다.

    .. note::
        이 함수는 **DB 를 변경하지 않습니다.** 점검만 하고, 마이그레이션이
        필요하면 ``python -m procurement init`` 을 안내합니다. 서버 기동 과정에서
        스키마를 자동으로 바꾸면 운영자가 모르는 사이에 DB 가 변경됩니다.

    Args:
        host: 바인딩 주소.
        port: 포트.
        db_path: 점검할 DB 경로. ``None`` 이면 설정값을 사용합니다.

    Returns:
        정상 기동 후 종료는 ``0``, 점검 실패로 기동하지 않으면 ``1``.
    """
    report = verify_bootstrap(db_path)
    if not report.healthy:
        print(report.format_report())
        print(
            "\n서버를 시작하지 않았습니다. DB 상태를 먼저 정리하세요."
            "\n  python -m procurement init"
            "\n\n위 명령은 누락된 테이블·컬럼만 보완하며, 기존 데이터를 삭제하지 않습니다."
        )
        return 1

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
    if args.command == "targets":
        return _run_targets(args.db, args.year)
    if args.command == "run":
        return _run_server(args.host, args.port, args.db)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
