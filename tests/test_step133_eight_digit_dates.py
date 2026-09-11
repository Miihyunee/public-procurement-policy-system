"""
STEP 133 — 고객 원본의 **여덟 자리 날짜**를 그대로 읽는다.

무엇이 막혀 있었나
===================
고객 기관 명단의 날짜가 ``20241023`` 형태다. 시스템은 ``2026-03-15`` 만
읽을 수 있어, 원본을 올리면 **전 행이 날짜 오류로 거절**됐다.

그래서 예전에는 누군가 원본을 고쳐 만든 파일을 썼고, 그 과정에서 취소일자가
있는 행이 함께 사라졌다(STEP 132 에서 발견).

🟢 2026-09-06 PM 확정(STEP 133 §2)
    여덟 자리는 ``YYYYMMDD`` 로만 읽는다.

⛔ **날짜를 읽는 표기를 넓힌 것뿐이다.** 유효기간 판정도, 실적 기준일도
바뀌지 않았다.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from procurement.uploads.validation import _parse_date


# ======================================================================
# §4  CASE 1 ~ 5
# ======================================================================
class TestTheDateFormatsWeAccept:
    @pytest.mark.parametrize("raw", ["20241023", 20241023, 20241023.0])
    def test_case_1_eight_digits_are_read(self, raw: object) -> None:
        """CASE 1 — ``20241023`` → 2024-10-23.

        엑셀은 서식에 따라 글자로도 숫자로도 넘겨준다. 어느 쪽이든 같게
        읽어야 한다.
        """
        assert _parse_date(raw) == date(2024, 10, 23)

    def test_case_2_the_existing_format_still_works(self) -> None:
        """CASE 2 — ⛔ 예전 표기를 잃지 않았다."""
        assert _parse_date("2026-03-15") == date(2026, 3, 15)
        assert _parse_date("2026/03/15") == date(2026, 3, 15)

    def test_case_3_another_eight_digit_value(self) -> None:
        """CASE 3 — ``20260114`` → 2026-01-14 (실제 명단의 취소일자 표기)."""
        assert _parse_date("20260114") == date(2026, 1, 14)

    def test_case_4_an_impossible_date_is_not_bent_into_shape(self) -> None:
        """CASE 4 — ⛔ ``20241301`` 을 그럴듯한 날짜로 바꾸지 않는다.

        13월은 없다. 12월이나 이듬해 1월로 고쳐 읽으면, 담당자가 확인하지
        않은 날짜가 실적 숫자를 만들게 된다.
        """
        assert _parse_date("20241301") is None
        assert _parse_date("20240230") is None

    @pytest.mark.parametrize(
        "raw",
        ["2024102", "202410233", "abc", "", "   ", "2024-1", 20241023.5, True],
    )
    def test_case_5_everything_else_stays_an_error(self, raw: object) -> None:
        """CASE 5 — 나머지는 예전처럼 오류다."""
        assert _parse_date(raw) is None

    def test_seven_digits_are_not_quietly_padded(self) -> None:
        """⭐ 일곱 자리를 몰래 채워 읽지 않는다.

        ``strptime`` 은 ``2024102`` 를 「2024-10-02」로 받아들인다. 사용자가
        무엇을 적으려 했는지 알 수 없는 값이므로 오류로 두어야 한다.
        """
        assert _parse_date("2024102") is None

    def test_a_real_date_object_passes_through(self) -> None:
        """이미 날짜인 값은 그대로 둔다."""
        assert _parse_date(date(2026, 3, 15)) == date(2026, 3, 15)
        assert _parse_date(datetime(2026, 3, 15, 9, 30)) == date(2026, 3, 15)


# ======================================================================
# §3  세 날짜 칸 모두에 같은 규칙이 적용되는가
# ======================================================================
class TestTheSameRuleAppliesToEveryDateColumn:
    def test_all_three_certification_dates_read_eight_digits(self) -> None:
        """시작일자 · 만료일자 · 취소일자 — 셋 다 같은 파서를 쓴다."""
        from procurement.uploads.company_format import policy_scoped_columns, row_columns
        from procurement.uploads.validation import validate_rows

        report = validate_rows(
            [
                {
                    "사업자등록번호": "1000000009",
                    "기업명": "합성기업",
                    "유효시작일": 20230425,
                    "유효종료일": 20260424,
                    "취소일자": "20260114",
                }
            ],
            columns=row_columns(policy_scoped_columns("WOMAN")),
        )

        assert report.rows, report.issues
        values = report.rows[0].values

        assert values["valid_from"] == date(2023, 4, 25)
        assert values["valid_to"] == date(2026, 4, 24)
        assert values["cancelled_on"] == date(2026, 1, 14)

    def test_a_purchase_row_reads_them_too(self) -> None:
        """구매 양식의 날짜도 같은 규칙을 쓴다 — 파서가 하나뿐이다."""
        from procurement.uploads.validation import validate_rows

        report = validate_rows(
            [
                {
                    "결의일자": 20260301,
                    "기업명": "합성기업",
                    "사업자등록번호": "1000000009",
                    "계": 1000000,
                    "신고기준일": "2026-03-01",
                }
            ]
        )

        assert report.rows, report.issues
        assert report.rows[0].values["resolution_date"] == date(2026, 3, 1)


# ======================================================================
# ⛔ 판정 규칙은 그대로다
# ======================================================================
class TestOnlyTheReadingChanged:
    def test_no_new_judgement_was_added(self) -> None:
        """⛔ 유효기간 판정도 실적 기준일도 건드리지 않았다."""
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "src" / "procurement"
        for relative in (
            "calculators/procurement_achievement.py",
            "calculators/rules/date_rules.py",
        ):
            source = (root / relative).read_text(encoding="utf-8")
            assert "%Y%m%d" not in source, relative
            assert "cancelled_on" not in source, relative

    def test_the_accepted_formats_are_written_down(self) -> None:
        """받아들이는 표기가 한 곳에 적혀 있다 — 흩어지면 갈린다."""
        from procurement.uploads.validation import _DATE_FORMATS

        assert _DATE_FORMATS == ("%Y-%m-%d", "%Y/%m/%d")
