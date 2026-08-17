"""
tests.test_upload_format

표준 업로드 양식 정의와 **행 단위 검증**을 검증합니다.

두 가지를 고정합니다.

1. 양식에 **고객이 확정한 컬럼만** 들어간다 (미확정 컬럼을 넣지 않는다)
2. 오류를 **행 · 컬럼 · 사유** 로 알려준다 (PM 지시서 §16 · §43)
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from procurement.uploads import (
    COLUMNS_BY_HEADER,
    PENDING_COLUMNS,
    REQUIRED_HEADERS,
    STANDARD_COLUMNS,
    example_row,
    guide_lines,
    header_row,
    validate_headers,
    validate_rows,
)

#: 정상 행 한 건.
GOOD_ROW: dict[str, object] = {
    "결의일자": "2026-03-15",
    # 2026-08-17 PM 결정 — 표준 양식에 지급일이 추가되었다(6컬럼).
    "지급일": "2026-04-01",
    "계약일자": "2026-02-20",
    "기업명": "한빛산업개발",
    "사업자등록번호": "220-81-62517",
    "계": "54,648,000",
}


def _row(**overrides: object) -> dict[str, object]:
    """정상 행에서 일부 값만 바꾼 행을 만듭니다."""
    row = dict(GOOD_ROW)
    row.update(overrides)
    return row


class TestStandardColumns:
    """양식에는 확정된 컬럼만 들어간다."""

    def test_confirmed_columns_only(self) -> None:
        """2026-08-14 고객 확정 5개."""
        assert header_row() == (
            "결의일자",
            "계약일자",
            "지급일",
            "기업명",
            "사업자등록번호",
            "계",
        )

    def test_amount_column_is_the_vat_included_total(self) -> None:
        """금액 컬럼은 `계`(VAT 포함 총액)다. 공급가액이 아니다."""
        column = COLUMNS_BY_HEADER["계"]
        assert column.key == "amount"
        assert "부가가치세" in column.description
        assert "공급가액이 아닙니다" in column.description

    def test_all_confirmed_columns_are_required(self) -> None:
        assert REQUIRED_HEADERS == header_row()

    @pytest.mark.parametrize(
        "header", ["예산과목", "구매유형", "적요", "대표자명", "거래구분"]
    )
    def test_unconfirmed_columns_are_not_in_the_form(self, header: str) -> None:
        """⛔ 확정되지 않은 컬럼을 양식에 넣지 않는다.

        넣어 두면 사용자가 값을 채우고, 시스템이 그 값을 해석하게 되어
        **확정되지 않은 업무규칙이 생긴다.**
        """
        assert header not in header_row()
        assert header in PENDING_COLUMNS, "미확정 컬럼은 사유와 함께 기록되어야 합니다."

    def test_every_pending_column_has_a_reason(self) -> None:
        for header, reason in PENDING_COLUMNS.items():
            assert reason.strip(), f"{header} 의 보류 사유가 비어 있습니다."

    def test_keys_are_unique(self) -> None:
        keys = [column.key for column in STANDARD_COLUMNS]
        assert len(keys) == len(set(keys))

    def test_example_row_matches_header_count(self) -> None:
        assert len(example_row()) == len(header_row())

    def test_guide_mentions_every_column(self) -> None:
        text = "\n".join(guide_lines())
        for header in header_row():
            assert header in text


class TestHeaderValidation:
    """머리글 검증."""

    def test_correct_headers_pass(self) -> None:
        assert validate_headers(list(header_row())) == []

    def test_missing_header_is_reported_by_name(self) -> None:
        headers = [h for h in header_row() if h != "사업자등록번호"]
        errors = validate_headers(headers)
        assert len(errors) == 1
        assert "사업자등록번호" in errors[0]

    def test_extra_headers_are_allowed(self) -> None:
        """모르는 컬럼이 있어도 파일을 거부하지 않는다(해석하지도 않는다)."""
        assert validate_headers([*header_row(), "비고", "메모"]) == []

    def test_whitespace_in_headers_is_tolerated(self) -> None:
        assert validate_headers([f" {h} " for h in header_row()]) == []


class TestRowValidation:
    """행 단위 검증 — 정상 경로."""

    def test_good_row_passes(self) -> None:
        report = validate_rows([GOOD_ROW])
        assert report.ok
        assert report.total_rows == 1
        assert len(report.rows) == 1

    def test_values_are_normalized(self) -> None:
        values = validate_rows([GOOD_ROW]).rows[0].values
        assert values["resolution_date"] == date(2026, 3, 15)
        assert values["contract_date"] == date(2026, 2, 20)
        assert values["company_name"] == "한빛산업개발"
        assert values["business_no"] == "2208162517"
        assert values["amount"] == Decimal("54648000")

    def test_row_numbers_start_at_two(self) -> None:
        """머리글이 1행이므로 첫 데이터는 2행이다."""
        report = validate_rows([_row(기업명=""), GOOD_ROW])
        assert report.issues[0].row_number == 2

    @pytest.mark.parametrize("raw", ["2026-03-15", "2026/03/15"])
    def test_accepted_date_formats(self, raw: str) -> None:
        report = validate_rows([_row(결의일자=raw)])
        assert report.rows[0].values["resolution_date"] == date(2026, 3, 15)

    def test_excel_datetime_is_accepted(self) -> None:
        """엑셀에서 날짜 셀은 datetime 으로 넘어온다."""
        report = validate_rows([_row(결의일자=datetime(2026, 3, 15, 9, 30))])
        assert report.rows[0].values["resolution_date"] == date(2026, 3, 15)

    @pytest.mark.parametrize("raw", [54648000, 54648000.0, Decimal("54648000")])
    def test_numeric_amount_types_are_accepted(self, raw: object) -> None:
        report = validate_rows([_row(계=raw)])
        assert report.rows[0].values["amount"] == Decimal("54648000")


class TestRowErrors:
    """오류는 행 · 컬럼 · 사유로 알려준다 (PM 지시서 §16 · §43)."""

    def test_missing_value_reports_column(self) -> None:
        report = validate_rows([_row(기업명="")])
        assert not report.ok
        issue = report.errors[0]
        assert issue.row_number == 2
        assert issue.header == "기업명"
        assert issue.message == "값이 없습니다."

    def test_bad_date_reports_column(self) -> None:
        report = validate_rows([_row(결의일자="2026.13.45")])
        issue = report.errors[0]
        assert issue.header == "결의일자"
        assert "날짜 형식" in issue.message

    def test_ambiguous_date_is_rejected(self) -> None:
        """``03/04/2026`` 은 3월 4일인지 4월 3일인지 알 수 없으므로 받지 않는다."""
        report = validate_rows([_row(결의일자="03/04/2026")])
        assert not report.ok

    def test_non_numeric_amount_reports_column(self) -> None:
        report = validate_rows([_row(계="abc")])
        issue = report.errors[0]
        assert issue.header == "계"
        assert "숫자가 아닙니다" in issue.message

    def test_boolean_is_not_a_number(self) -> None:
        report = validate_rows([_row(계=True)])
        assert not report.ok

    def test_short_business_no_is_rejected(self) -> None:
        """9자리를 0으로 자동 보정하지 않는다(기존 규칙 그대로)."""
        report = validate_rows([_row(사업자등록번호="123456789")])
        assert not report.ok
        assert report.errors[0].header == "사업자등록번호"

    def test_multiple_problems_in_one_row(self) -> None:
        report = validate_rows([_row(기업명="", 계="abc", 결의일자="???")])
        assert len({issue.header for issue in report.errors}) == 3
        assert report.error_row_count == 1, "한 행의 문제 여러 개는 1행으로 센다."

    def test_valid_rows_survive_alongside_bad_rows(self) -> None:
        """오류 행 때문에 정상 행이 버려지지 않는다."""
        report = validate_rows([GOOD_ROW, _row(기업명=""), GOOD_ROW])
        assert report.total_rows == 3
        assert len(report.rows) == 2
        assert report.error_row_count == 1

    def test_issue_line_format(self) -> None:
        report = validate_rows([_row(기업명="")])
        assert report.issue_lines()[0] == "2행 | 기업명 | 값이 없습니다."

    def test_issue_lines_are_capped(self) -> None:
        report = validate_rows([_row(기업명="") for _ in range(50)])
        lines = report.issue_lines(limit=10)
        assert len(lines) == 11
        assert "외 40건" in lines[-1]


class TestWarnings:
    """경고는 저장을 막지 않는다."""

    def test_checksum_warning_does_not_block(self) -> None:
        """체크섬 오류는 경고만 남긴다(D-002). 데이터를 버리지 않는다."""
        report = validate_rows([_row(사업자등록번호="1234567890")])
        assert report.ok
        assert len(report.rows) == 1
        assert report.warnings
        assert report.warnings[0].header == "사업자등록번호"

    def test_negative_amount_is_a_warning_not_a_decision(self) -> None:
        """⛔ 0 이하 금액의 처리 방식은 **확정되지 않았다.**

        음수 상계 규칙은 확정됐지만 Repository 가 아직 저장을 거부한다.
        오류로 단정하면 확정되지 않은 규칙을 만드는 셈이므로, **경고로 표시만**
        하고 판단은 호출자에게 남긴다.
        """
        report = validate_rows([_row(계="-100000")])

        assert report.ok, "음수를 오류로 단정하지 않는다."
        assert report.warnings[0].header == "계"
        assert "확정 대기" in report.warnings[0].message
        assert report.rows[0].values["amount"] == Decimal("-100000")

    def test_zero_amount_is_also_a_warning(self) -> None:
        report = validate_rows([_row(계="0")])
        assert report.ok
        assert report.warnings


class TestSummary:
    """요약은 PM 지시서 §43 형식을 따른다."""

    def test_summary_counts(self) -> None:
        rows = [GOOD_ROW] * 3 + [_row(기업명="")] * 2
        lines = validate_rows(rows).summary_lines()
        assert lines[0] == "총 5건"
        assert lines[1] == "정상 3건"
        assert lines[2] == "오류 2건"

    def test_thousands_separator(self) -> None:
        lines = validate_rows([GOOD_ROW] * 1250).summary_lines()
        assert lines[0] == "총 1,250건"

    def test_empty_upload(self) -> None:
        report = validate_rows([])
        assert report.total_rows == 0
        assert report.ok

    def test_file_error_short_circuits_summary(self) -> None:
        from procurement.uploads.validation import ValidationReport

        report = ValidationReport(file_errors=["필수 항목이 없습니다: 계"])
        assert not report.ok
        assert "파일을 읽을 수 없습니다." in report.summary_lines()[0]


class TestNotWiredIntoStorage:
    """이 계층은 아직 저장에 연결되지 않았다.

    결의일자를 어느 물리 필드에 담을지가 확정되지 않아 Mapping 계층을 만들지
    않았습니다. 승인 후 이 테스트를 삭제하고 통합 테스트로 대체합니다.
    """

    def test_validation_does_not_touch_the_database(self) -> None:
        """검증 모듈이 저장 계층을 import 하지 않는다.

        주석에 언급하는 것은 무방하므로 **실제 import 문만** 검사합니다.
        """
        import ast
        from pathlib import Path

        import procurement.uploads.validation as module

        assert module.__file__ is not None
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        assert not [name for name in imported if "database" in name], imported

    def test_uploads_package_has_no_external_dependency(self) -> None:
        """엑셀 라이브러리에 의존하지 않는다(``openpyxl`` 승인 전).

        의존성 없이 동작해야 승인 없이도 검증 계층을 쓸 수 있습니다.
        """
        import ast
        from pathlib import Path

        import procurement.uploads.format as format_module
        import procurement.uploads.validation as validation_module

        allowed_roots = {"__future__", "procurement", "dataclasses", "typing", "types"}
        allowed_roots |= {"re", "collections", "datetime", "decimal"}

        for module in (format_module, validation_module):
            assert module.__file__ is not None
            tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    assert name.split(".")[0] in allowed_roots, f"{module.__name__}: {name}"

    def test_resolution_date_now_has_a_model_field(self) -> None:
        """``Purchase.resolution_date`` 가 존재하고 **선택 항목**이다.

        .. note::
            **기대값이 바뀐 이유** — 2026-08-15 PM 최종 결정(B안)으로 결의일자
            전용 필드가 신설되었습니다. 이전에는 어느 필드에 넣을지 미확정이라
            "필드를 만들지 않았다" 를 고정하고 있었습니다.

            기본값이 ``None`` 이어야 필드 도입 이전 데이터가 보호됩니다.
        """
        import dataclasses

        from procurement.models import Purchase

        fields = {f.name: f for f in dataclasses.fields(Purchase)}
        assert "resolution_date" in fields
        assert fields["resolution_date"].default is None
