"""
procurement.uploads.format

**표준 업로드 양식**의 컬럼을 정의합니다.

기관마다 다른 원본 엑셀 컬럼명을 시스템이 추측해서 해석하지 않습니다. 대신
우리가 지정한 양식을 사용자가 내려받아 채워 올립니다.

::

    표준 Excel  →  Validation  →  Mapping  →  Purchase  →  Repository  →  Calculator
     (이 모듈)    (validation)    (미구현)

.. warning::
    **확정된 컬럼만 정의합니다.** 예산과목 · 구매유형 · 적요 · 대표자명 ·
    거래구분은 아직 확정되지 않아 :data:`PENDING_COLUMNS` 에 이유와 함께 따로
    두었고, 양식에 넣지 않았습니다.

.. note::
    이 모듈은 **엑셀 파일을 읽거나 쓰지 않습니다.** 컬럼 정의만 담당하므로
    외부 라이브러리에 의존하지 않습니다. 엑셀 입출력(``openpyxl``)은 의존성
    추가 승인 후 별도 어댑터로 붙입니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Final


@dataclass(frozen=True, kw_only=True)
class StandardColumn:
    """표준 양식의 컬럼 하나.

    Attributes:
        key: 시스템 내부에서 쓰는 식별자. 엑셀 컬럼명이 바뀌어도 이 값은 유지된다.
        header: 사용자가 보는 엑셀 컬럼명(1행 머리글).
        required: 값이 반드시 있어야 하는지 여부.
        description: 양식 안내에 쓸 설명.
        example: 사용자에게 보여줄 입력 예시.
    """

    key: str
    header: str
    required: bool
    description: str
    example: str


#: 🟢 **고객 확정 컬럼** (2026-08-14 회신 · `DECISIONS.md` §0.6).
#:
#: 순서가 곧 엑셀 열 순서입니다.
STANDARD_COLUMNS: Final[tuple[StandardColumn, ...]] = (
    StandardColumn(
        key="resolution_date",
        header="결의일자",
        required=True,
        description="구매실적의 연도 귀속 기준일입니다.",
        example="2026-03-15",
    ),
    StandardColumn(
        key="contract_date",
        header="계약일자",
        required=True,
        description="창업기업 판정에 함께 사용합니다.",
        example="2026-02-20",
    ),
    StandardColumn(
        key="company_name",
        header="기업명",
        required=True,
        description="거래처명입니다. 음수 거래 상계 판정에도 사용합니다.",
        example="한빛산업개발",
    ),
    StandardColumn(
        key="business_no",
        header="사업자등록번호",
        required=True,
        description="기업을 식별하는 핵심 키입니다. 하이픈은 있어도 됩니다.",
        example="220-81-62517",
    ),
    StandardColumn(
        key="amount",
        header="계",
        required=True,
        description="부가가치세가 포함된 총 구매금액입니다. 공급가액이 아닙니다.",
        example="54648000",
    ),
)

#: 헤더 → 컬럼 정의 (조회용).
COLUMNS_BY_HEADER: Final[MappingProxyType[str, StandardColumn]] = MappingProxyType(
    {column.header: column for column in STANDARD_COLUMNS}
)

#: 내부 키 → 컬럼 정의 (조회용).
COLUMNS_BY_KEY: Final[MappingProxyType[str, StandardColumn]] = MappingProxyType(
    {column.key: column for column in STANDARD_COLUMNS}
)

#: 반드시 있어야 하는 헤더.
REQUIRED_HEADERS: Final[tuple[str, ...]] = tuple(
    column.header for column in STANDARD_COLUMNS if column.required
)

#: 🔴 **아직 양식에 넣지 않은 컬럼과 그 이유.**
#:
#: 고객이 확정하지 않았으므로 양식에 넣지 않습니다. 넣어 두면 사용자가 채우고,
#: 그 값을 시스템이 해석하게 되어 확정되지 않은 규칙이 생깁니다.
PENDING_COLUMNS: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "예산과목": "구매유형 분류 근거. 확정 3건 외 자동 분류 금지(DECISIONS §0.5.3)",
        "구매유형": "사용자가 직접 고를지, 예산과목에서 유도할지 미정",
        "적요": "저장 필요 여부 미정",
        "대표자명": "Company 필수 제약 관련. 스키마 변경 승인 대상",
        "거래구분": "표준 양식은 구매만 담으므로 필요 여부 미정",
    }
)


def header_row() -> tuple[str, ...]:
    """엑셀 1행에 넣을 머리글을 순서대로 반환합니다."""
    return tuple(column.header for column in STANDARD_COLUMNS)


def example_row() -> tuple[str, ...]:
    """양식에 넣을 입력 예시 한 줄을 반환합니다."""
    return tuple(column.example for column in STANDARD_COLUMNS)


def guide_lines() -> tuple[str, ...]:
    """양식 안내 문구를 반환합니다(엑셀 안내 시트·화면 공용).

    Returns:
        각 줄이 한 항목인 안내 문구.
    """
    lines = [
        "■ 작성 안내",
        "",
        "· 1행의 머리글은 지우거나 바꾸지 마세요.",
        "· 2행의 예시는 지우고 실제 데이터를 입력하세요.",
        "· 모든 항목은 필수입니다.",
        "",
        "■ 항목 설명",
        "",
    ]
    lines.extend(f"· {column.header} — {column.description}" for column in STANDARD_COLUMNS)
    lines.extend(
        [
            "",
            "■ 입력 형식",
            "",
            "· 날짜 — 2026-03-15 또는 2026/03/15",
            "· 사업자등록번호 — 10자리. 하이픈은 있어도 됩니다",
            "· 금액 — 숫자만. 쉼표는 있어도 됩니다",
        ]
    )
    return tuple(lines)
