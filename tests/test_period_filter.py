"""
기간 조건 값 객체(:class:`PeriodFilter`) 테스트.

특히 **``date_field`` 에 기본값이 없다**는 점을 고정합니다. 연도 귀속에 어느
날짜를 쓸지는 D-24(미확정, W-1 종속)이므로, 기본값이 생기면 사실상 결정된
것처럼 굳어집니다.
"""

from __future__ import annotations

import dataclasses
from datetime import date

import pytest

from procurement.core.period import (
    ALLOWED_DATE_FIELDS,
    CONTRACT_DATE,
    PAYMENT_DATE,
    PeriodFilter,
    PeriodValidationError,
)


class TestConstruction:
    """생성과 검증."""

    def test_creates_with_payment_date(self) -> None:
        period = PeriodFilter(
            start=date(2026, 1, 1), end=date(2026, 12, 31), date_field=PAYMENT_DATE
        )
        assert period.date_field == PAYMENT_DATE

    def test_creates_with_contract_date(self) -> None:
        period = PeriodFilter(
            start=date(2026, 1, 1), end=date(2026, 12, 31), date_field=CONTRACT_DATE
        )
        assert period.date_field == CONTRACT_DATE

    def test_date_field_has_no_default(self) -> None:
        """date_field 를 생략하면 만들 수 없다 (D-24 미확정)."""
        with pytest.raises(TypeError):
            PeriodFilter(start=date(2026, 1, 1), end=date(2026, 12, 31))  # type: ignore[call-arg]

    def test_rejects_unknown_date_field(self) -> None:
        with pytest.raises(PeriodValidationError):
            PeriodFilter(start=date(2026, 1, 1), end=date(2026, 12, 31), date_field="created_at")

    def test_rejects_sql_injection_shaped_field(self) -> None:
        """허용 목록 밖의 값은 SQL 에 닿기 전에 거부된다."""
        with pytest.raises(PeriodValidationError):
            PeriodFilter(
                start=date(2026, 1, 1),
                end=date(2026, 12, 31),
                date_field="payment_date; DROP TABLE purchase",
            )

    def test_rejects_reversed_range(self) -> None:
        with pytest.raises(PeriodValidationError):
            PeriodFilter(start=date(2026, 12, 31), end=date(2026, 1, 1), date_field=PAYMENT_DATE)

    def test_allows_single_day_range(self) -> None:
        period = PeriodFilter(start=date(2026, 5, 1), end=date(2026, 5, 1), date_field=PAYMENT_DATE)
        assert period.start == period.end

    def test_is_frozen(self) -> None:
        period = PeriodFilter(
            start=date(2026, 1, 1), end=date(2026, 12, 31), date_field=PAYMENT_DATE
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            period.start = date(2025, 1, 1)  # type: ignore[misc]


class TestForYear:
    """``for_year`` — D-23(회계연도 = 역년) 구현."""

    def test_uses_calendar_year(self) -> None:
        period = PeriodFilter.for_year(2026, PAYMENT_DATE)
        assert period.start == date(2026, 1, 1)
        assert period.end == date(2026, 12, 31)

    def test_requires_date_field(self) -> None:
        with pytest.raises(TypeError):
            PeriodFilter.for_year(2026)  # type: ignore[call-arg]

    def test_rejects_unknown_date_field(self) -> None:
        with pytest.raises(PeriodValidationError):
            PeriodFilter.for_year(2026, "임의값")

    def test_leap_year_end_is_dec_31(self) -> None:
        assert PeriodFilter.for_year(2024, PAYMENT_DATE).end == date(2024, 12, 31)


class TestAllowedFields:
    """허용 목록."""

    def test_only_two_fields_allowed(self) -> None:
        assert ALLOWED_DATE_FIELDS == frozenset({PAYMENT_DATE, CONTRACT_DATE})

    def test_describe_mentions_date_field(self) -> None:
        period = PeriodFilter.for_year(2026, CONTRACT_DATE)
        assert CONTRACT_DATE in period.describe()
