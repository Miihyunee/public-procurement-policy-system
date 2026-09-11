"""
procurement.uploads.company_header_aliases

고객 기관이 내려받은 **원본 명단**의 머리글을 표준 항목명으로 옮깁니다.

왜 필요한가
===========
기관이 받는 명단의 칸 이름과 이 시스템의 표준 항목명이 다릅니다. 대응이
없으면 담당자가 **원본을 열어 머리글을 고쳐야** 하고, 고치는 순간 그것은
기관이 내려받은 원본이 아니게 됩니다. 무엇을 어떻게 고쳤는지 기록도 남지
않습니다.

⛔ 짐작하지 않습니다
====================
**실제로 받아 본 파일에서 확인한 이름만** 적습니다. 아래 표는
2026-09-06 여성기업 명단에서 확인한 것입니다.

다음은 하지 않습니다.

- 글자가 비슷하다고 짝지어 주기
- 뜻을 미루어 짐작하기
- 동의어 사전을 임의로 넓히기
- 사용자가 예상하지 못한 칸을 조용히 바꾸기

다른 기관·다른 정책의 파일을 실제로 확보하면, 그 파일을 확인한 뒤 여기에
**따로** 더합니다.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import Final

#: 고객 원본 머리글 → 표준 항목명.
#:
#: 🟢 2026-09-06 PM 확정(STEP 129 §6) — 실제 여성기업 명단에서 확인한 대응.
#:
#: ``대표자명`` 은 이름이 **같아서** 여기 없습니다. 대응이 필요 없습니다.
COMPANY_HEADER_ALIASES: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "업체명": "기업명",
        "사업자번호": "사업자등록번호",
        "시작일자": "유효시작일",
        "만료일자": "유효종료일",
    }
)


def canonical_headers(
    headers: tuple[str, ...],
    aliases: MappingProxyType[str, str] | dict[str, str],
) -> tuple[str, ...]:
    """머리글을 표준 항목명으로 옮깁니다.

    ⛔ **표준 이름이 이미 있으면 바꾸지 않습니다.** 한 파일에 ``기업명`` 과
    ``업체명`` 이 함께 있을 때 둘을 같은 칸으로 합치면, 어느 쪽 값이 남는지
    사용자가 알 수 없습니다. 그런 파일은 원래 이름 그대로 두어 검증 계층이
    판단하게 합니다.

    Args:
        headers: 엑셀 1행에서 읽은 머리글.
        aliases: 원본 이름 → 표준 이름 대응표.

    Returns:
        옮겨진 머리글. 순서는 그대로입니다.
    """
    present = set(headers)
    return tuple(
        aliases[header]
        if header in aliases and aliases[header] not in present
        else header
        for header in headers
    )
