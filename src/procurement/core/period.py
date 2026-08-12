"""
procurement.core.period

구매실적 조회에 사용할 **기간 조건**을 표현하는 값 객체를 정의합니다.

기간 조건은 세 가지 정보로 이루어집니다.

- ``start`` / ``end`` — 조회 구간(경계 포함)
- ``date_field`` — **어느 날짜 컬럼으로 기간을 판정할 것인가**

.. warning::
    ``date_field`` 에는 **기본값이 없습니다.** 반드시 호출자가 지정해야 합니다.

    연도 귀속에 어느 날짜를 쓸 것인지는 **D-24 (미확정)** 이며, 이는 고객 확인
    항목 **W-1 (대금 지급 완료일이 무엇인가)** 에 종속됩니다. 기본값을 두면
    개발·테스트에서 그 값이 계속 사용되어 사실상 확정된 것처럼 굳어지므로,
    값을 지정하지 않으면 **동작하지 않고 오류**가 나도록 설계했습니다.

    "어느 날짜로 연도를 나누는지 아무도 결정하지 않은 채 숫자가 나오는 상황"
    자체를 만들지 않기 위한 조치입니다.

회계연도가 역년(1/1 ~ 12/31)이라는 점은 **D-23 으로 확정**되었으므로
:meth:`PeriodFilter.for_year` 가 이를 구현합니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

#: 대금 지급일 기준
PAYMENT_DATE = "payment_date"

#: 계약일 기준
CONTRACT_DATE = "contract_date"

#: 기간 판정에 사용할 수 있는 날짜 컬럼. ``purchase`` 테이블의 실제 컬럼명이며,
#: SQL 에 직접 끼워 넣기 전에 반드시 이 집합으로 검증합니다.
ALLOWED_DATE_FIELDS: frozenset[str] = frozenset({PAYMENT_DATE, CONTRACT_DATE})


class PeriodValidationError(ValueError):
    """기간 조건이 올바르지 않을 때 발생합니다."""


@dataclass(frozen=True, kw_only=True)
class PeriodFilter:
    """구매실적 조회 기간 조건(값 객체).

    Attributes:
        start: 조회 시작일(포함).
        end: 조회 종료일(포함).
        date_field: 기간 판정에 사용할 날짜 컬럼.
            :data:`PAYMENT_DATE` 또는 :data:`CONTRACT_DATE`. **기본값 없음.**

    Raises:
        PeriodValidationError: ``start`` 가 ``end`` 보다 늦거나, ``date_field``
            가 허용 목록에 없는 경우.
    """

    start: date
    end: date
    date_field: str

    def __post_init__(self) -> None:
        """값을 검증합니다."""
        if self.date_field not in ALLOWED_DATE_FIELDS:
            allowed = ", ".join(sorted(ALLOWED_DATE_FIELDS))
            raise PeriodValidationError(
                f"date_field 는 {allowed} 중 하나여야 합니다: {self.date_field!r}"
            )
        if self.start > self.end:
            raise PeriodValidationError(
                f"기간 시작일이 종료일보다 늦습니다: {self.start} > {self.end}"
            )

    @classmethod
    def for_year(cls, year: int, date_field: str) -> PeriodFilter:
        """회계연도 한 해에 해당하는 기간 조건을 만듭니다.

        회계연도는 **역년(1/1 ~ 12/31)** 입니다(**D-23 확정**).

        Args:
            year: 대상 연도.
            date_field: 기간 판정에 사용할 날짜 컬럼. **생략할 수 없습니다**(D-24).

        Returns:
            ``year-01-01 ~ year-12-31`` 구간의 :class:`PeriodFilter`.

        Raises:
            PeriodValidationError: ``date_field`` 가 허용 목록에 없는 경우.
        """
        return cls(start=date(year, 1, 1), end=date(year, 12, 31), date_field=date_field)

    def describe(self) -> str:
        """사람이 읽을 수 있는 설명을 반환합니다."""
        return f"{self.start} ~ {self.end} ({self.date_field} 기준)"
