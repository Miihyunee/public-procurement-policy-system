"""
procurement.admin.auth

설정 변경(쓰기) API 를 위한 **최소 접근 통제**입니다.

목표율 변경은 이 시스템에서 처음으로 외부에서 데이터를 바꿀 수 있는 경로이므로,
관리자 토큰을 확인한 뒤에만 허용합니다.

동작::

    토큰 미설정  → 503 (쓰기 API 비활성)
    토큰 불일치  → 401
    토큰 일치    → 통과

.. note::
    본 모듈은 사용자 인증 시스템이 아닙니다. 단일 관리자 토큰을 확인할 뿐이며,
    사용자 단위 식별은 제공하지 않습니다.

.. warning::
    실제 토큰 값은 **환경변수로만** 주입합니다. 코드·문서·테스트·저장소에
    실제 값을 기록하지 않습니다.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable

from fastapi import Header, HTTPException

#: 토큰이 설정되지 않아 쓰기 API 를 제공할 수 없을 때의 응답 메시지.
TOKEN_NOT_CONFIGURED_MESSAGE = "관리자 토큰이 설정되지 않아 설정 변경 API 가 비활성화되어 있습니다."
#: 토큰이 없거나 일치하지 않을 때의 응답 메시지.
TOKEN_INVALID_MESSAGE = "관리자 인증에 실패했습니다."

_BEARER_PREFIX = "Bearer "


def build_admin_token_guard(admin_token: str | None) -> Callable[[str | None], None]:
    """관리자 토큰 검증 의존성을 생성합니다.

    반환된 함수를 FastAPI ``Depends`` 로 **쓰기 엔드포인트에만** 연결합니다.
    전역 미들웨어를 사용하지 않으므로 기존 조회 API 는 영향을 받지 않습니다.

    Args:
        admin_token: 설정된 관리자 토큰. ``None`` 이거나 빈 문자열이면 쓰기 API 를
            비활성 상태로 취급합니다.

    Returns:
        ``Authorization`` 헤더를 검증하는 의존성 함수.
    """

    def require_admin_token(authorization: str | None = Header(default=None)) -> None:
        """``Authorization: Bearer <토큰>`` 헤더를 검증합니다.

        Raises:
            HTTPException: 토큰 미설정 시 503, 인증 실패 시 401.
        """
        if not admin_token:
            raise HTTPException(status_code=503, detail=TOKEN_NOT_CONFIGURED_MESSAGE)
        if authorization is None or not authorization.startswith(_BEARER_PREFIX):
            raise HTTPException(status_code=401, detail=TOKEN_INVALID_MESSAGE)
        presented = authorization[len(_BEARER_PREFIX) :].strip()
        # 타이밍 공격을 피하기 위해 상수 시간 비교를 사용한다.
        if not secrets.compare_digest(presented, admin_token):
            raise HTTPException(status_code=401, detail=TOKEN_INVALID_MESSAGE)

    return require_admin_token
