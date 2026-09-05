"""
procurement.uploads.company_format

**기업정보 표준 양식**의 컬럼을 정의합니다.

기업정보를 확인하는 방법은 두 가지입니다 — **파일 업로드**와 **조회**. 이
모듈은 그중 파일 쪽 양식만 정의하며, 두 방법이 만들어 내는 결과
(:class:`~procurement.models.company.Company` ·
:class:`~procurement.models.certification.Certification`)는 **같습니다.**

.. warning::
    ⛔ **고객이 확정하지 않은 컬럼을 넣지 않았습니다.** 여기 있는 것은
    *"지금 구조가 저장하려면 반드시 있어야 하는 값"* 뿐입니다.

    - 사업자등록번호 — 기업을 식별하는 유일한 키
    - 기업명 — :class:`Company` 의 **필수** 항목 (대표자명은 선택)
    - 인증 종류 · 유효 시작일 · 유효 종료일 — :class:`Certification` 의 필수 항목

    ⛔ 업체 규모 · 등급 같은 값은 **넣지 않았습니다.** 중소기업 여부를 규모로
    해석하는 규칙이 없기 때문입니다(`CERTIFICATION_SOURCE_ANALYSIS.md` §16).

.. warning::
    ⛔ **대표자명을 다른 값으로 대신 채우지 않습니다.** 기업명·사업자등록번호로
    대체하거나 "미상" 을 넣지 않습니다 — 그렇게 하면 근거 없는 기업 정보가
    남습니다. 값이 없으면 **비운 채로** 저장합니다(🟢 2026-09-05 PM 확정:
    대표자명은 선택값). 필수인 것은 기업명과 사업자등록번호 둘뿐입니다.

.. note::
    이 모듈은 **엑셀 파일을 읽거나 쓰지 않습니다.** 컬럼 정의만 담당합니다.
"""

from __future__ import annotations

from dataclasses import replace
from types import MappingProxyType
from typing import Final

from procurement.core.open_ended_certification import allows_open_ended
from procurement.uploads.format import StandardColumn

#: 인증 없이 **기업만** 올릴 때 필요한 컬럼.
COMPANY_COLUMNS: Final[tuple[StandardColumn, ...]] = (
    StandardColumn(
        key="business_no",
        header="사업자등록번호",
        required=True,
        description="기업을 식별하는 핵심 키입니다. 하이픈은 있어도 됩니다.",
        example="220-81-62517",
    ),
    StandardColumn(
        key="company_name",
        header="기업명",
        required=True,
        description="기업 이름입니다.",
        example="한빛산업개발",
    ),
    StandardColumn(
        key="representative_name",
        header="대표자명",
        required=False,
        description=(
            "대표자 이름입니다. **비워 두어도 됩니다**(2026-09-05 PM 확정) — "
            "기업을 식별하는 값은 기업명과 사업자등록번호입니다."
        ),
        example="홍길동",
    ),
)

#: 인증까지 함께 올릴 때 추가로 필요한 컬럼.
CERTIFICATION_COLUMNS: Final[tuple[StandardColumn, ...]] = (
    StandardColumn(
        key="policy_code",
        header="인증종류",
        required=True,
        description=(
            "등록된 정책 코드입니다. 등록되지 않은 인증은 받지 않습니다 — "
            "어느 정책의 실적으로 셀지 정해져 있지 않기 때문입니다."
        ),
        example="SMALL_BUSINESS",
    ),
    StandardColumn(
        key="valid_from",
        header="유효시작일",
        required=True,
        description="인증 유효기간의 시작일입니다.",
        example="2026-01-01",
    ),
    StandardColumn(
        key="valid_to",
        header="유효종료일",
        required=True,
        description=(
            "인증 유효기간의 종료일입니다. **비워 둘 수 없습니다** — 단, "
            "사회적기업·사회적협동조합만 예외이며 비우면 계속 유효한 인증이 "
            "됩니다(2026-09-04 고객 확정)."
        ),
        example="2026-12-31",
    ),
)

#: 기업정보 파일의 전체 컬럼(기업 + 인증).
STANDARD_COMPANY_COLUMNS: Final[tuple[StandardColumn, ...]] = (
    COMPANY_COLUMNS + CERTIFICATION_COLUMNS
)

#: 내부 키 → 컬럼 정의 (조회용).
COMPANY_COLUMNS_BY_KEY: Final[MappingProxyType[str, StandardColumn]] = MappingProxyType(
    {column.key: column for column in STANDARD_COMPANY_COLUMNS}
)

#: 반드시 있어야 하는 **머리글**.
COMPANY_REQUIRED_HEADERS: Final[tuple[str, ...]] = tuple(
    column.header for column in STANDARD_COMPANY_COLUMNS
)

#: 🔴 **아직 양식에 넣지 않은 컬럼과 그 이유.**
#:
#: 넣어 두면 사용자가 채우고, 그 값을 시스템이 해석하게 되어 확정되지 않은
#: 규칙이 생깁니다.
COMPANY_PENDING_COLUMNS: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "업체규모": "중소기업 여부를 규모로 판정하는 규칙이 없다",
        "소재지": "판정에 쓰지 않으며 필요 여부 미정",
        "인증서번호": "저장할 자리는 있으나 파일로 받아야 하는지 미정",
    }
)


#: **정책을 사용자가 고른 뒤** 올리는 파일의 컬럼(STEP 96 §5).
#:
#: ``인증종류`` 가 **빠져 있습니다.** 어느 정책의 목록인지는 화면에서 사용자가
#: 고른 값으로 정해지며, ⛔ 파일 내용을 보고 시스템이 추론하지 않습니다.
#:
#: 정책별 원본(여성기업 명단 · 자활용사촌 명단 …)에는 "인증종류" 같은 칸이
#: 없는 것이 보통입니다. 그 칸을 요구하면 사용자가 원본을 고쳐야 하고, 고치는
#: 순간 원본이 아니게 됩니다.
POLICY_SCOPED_COMPANY_COLUMNS: Final[tuple[StandardColumn, ...]] = COMPANY_COLUMNS + tuple(
    column for column in CERTIFICATION_COLUMNS if column.key != "policy_code"
)

#: 정책을 고르고 올릴 때 반드시 있어야 하는 머리글.
POLICY_SCOPED_REQUIRED_HEADERS: Final[tuple[str, ...]] = tuple(
    column.header for column in POLICY_SCOPED_COMPANY_COLUMNS
)


def policy_scoped_columns(policy_code: str | None) -> tuple[StandardColumn, ...]:
    """정책을 고르고 올릴 때 검증에 쓸 컬럼 정의.

    🟢 2026-09-04 고객 확정(STEP 108): *"사회적기업과 사회적협동조합은
    종료일이 없으며 계속 유효한 것으로 판단한다."* 그 두 정책에서만
    ``유효종료일`` 이 **선택 항목**이 됩니다.

    Args:
        policy_code: 사용자가 화면에서 고른 정책 코드.

    Returns:
        검증에 쓸 컬럼 정의. 그 외 정책은 기존과 완전히 같습니다 —
        ⛔ 종료일이 비어 있으면 여전히 오류입니다.
    """
    if not allows_open_ended(policy_code):
        return POLICY_SCOPED_COMPANY_COLUMNS
    return tuple(
        replace(column, required=False) if column.key == "valid_to" else column
        for column in POLICY_SCOPED_COMPANY_COLUMNS
    )


def company_header_row() -> tuple[str, ...]:
    """엑셀 1행에 넣을 머리글을 순서대로 반환합니다."""
    return tuple(column.header for column in STANDARD_COMPANY_COLUMNS)


def company_example_row() -> tuple[str, ...]:
    """양식에 넣을 입력 예시 한 줄을 반환합니다."""
    return tuple(column.example for column in STANDARD_COMPANY_COLUMNS)


def policy_scoped_header_row() -> tuple[str, ...]:
    """정책을 고르고 올릴 때의 머리글(⛔ 인증종류 없음)."""
    return tuple(column.header for column in POLICY_SCOPED_COMPANY_COLUMNS)


def policy_scoped_example_row() -> tuple[str, ...]:
    """정책을 고르고 올릴 때의 입력 예시 한 줄."""
    return tuple(column.example for column in POLICY_SCOPED_COMPANY_COLUMNS)
