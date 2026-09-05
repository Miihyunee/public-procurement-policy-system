"""
procurement.uploads.validation

표준 업로드 양식의 **행 단위 검증**을 수행합니다.

목적은 "업로드 실패" 라는 한 줄이 아니라, 사용자가 **어느 행 · 어느 항목 · 왜**
잘못됐는지 알고 엑셀을 고칠 수 있게 하는 것입니다::

    총 1,250건
    정상 1,230건
    오류    20건

    12행 | 사업자등록번호 | 값이 없습니다.
    18행 | 결의일자       | 날짜 형식이 잘못되었습니다.

.. note::
    사업자등록번호 정규화는 **기존 규칙을 그대로 재사용**합니다
    (:mod:`procurement.matchers.business_no`). 새 규칙을 만들지 않습니다.

.. warning::
    **이 모듈은 저장하지 않습니다.** 검증 결과만 돌려주며, ``Purchase`` 모델로
    옮기는 Mapping 계층은 아직 만들지 않았습니다. 결의일자를 어느 물리 필드에
    담을지가 확정되지 않았기 때문입니다(PM 결정 대기).

.. warning::
    **엑셀 파일을 읽지 않습니다.** 이미 행 목록으로 풀어 놓은 값을 검증합니다.
    엑셀 파싱(``openpyxl``)은 의존성 추가 승인 후 별도 어댑터로 붙입니다.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from procurement.matchers.business_no import normalize_business_no
from procurement.uploads.format import (
    STANDARD_COLUMNS,
    StandardColumn,
)

#: 허용하는 날짜 문자열 형식.
#:
#: 샘플 데이터에서 관찰된 두 표기를 받습니다. 그 밖의 표기는 사용자가 의도한
#: 날짜를 확정할 수 없으므로 오류로 처리합니다(예: ``03/04/2026`` 은 3월 4일인지
#: 4월 3일인지 알 수 없습니다).
_DATE_FORMATS: tuple[str, ...] = ("%Y-%m-%d", "%Y/%m/%d")

#: 금액에서 제거할 문자(천 단위 구분자·공백·원화 기호).
_AMOUNT_NOISE = re.compile(r"[,\s₩]")


@dataclass(frozen=True, kw_only=True)
class RowIssue:
    """행 하나에서 발견된 문제.

    Attributes:
        row_number: 사용자가 보는 엑셀 행 번호(머리글 다음 행이 2).
        header: 문제가 있는 컬럼의 엑셀 머리글. 행 전체 문제면 ``None``.
        message: 사용자에게 보여줄 설명.
        severity: ``"error"`` 는 저장 불가, ``"warning"`` 은 저장 가능하나 확인 필요.
    """

    row_number: int
    header: str | None
    message: str
    severity: str = "error"

    def format_line(self) -> str:
        """한 줄 표시 형식으로 변환합니다."""
        column = self.header or "-"
        return f"{self.row_number}행 | {column} | {self.message}"


@dataclass(frozen=True, kw_only=True)
class ValidatedRow:
    """검증을 통과한 행 하나.

    Attributes:
        row_number: 엑셀 행 번호.
        values: 정규화된 값. 키는 :class:`~procurement.uploads.format.StandardColumn`
            의 ``key`` 입니다.
        warnings: 저장을 막지는 않지만 확인이 필요한 사항.
    """

    row_number: int
    values: dict[str, object]
    warnings: tuple[RowIssue, ...] = ()


@dataclass(frozen=True, kw_only=True)
class ValidationReport:
    """업로드 검증 결과.

    Attributes:
        rows: 검증을 통과한 행 목록.
        issues: 발견된 모든 문제(오류 + 경고).
        file_errors: 파일 단위 문제(머리글 누락 등). 있으면 행 검증을 하지 않습니다.
        total_rows: 검사한 전체 행 수.
    """

    rows: list[ValidatedRow] = field(default_factory=list)
    issues: list[RowIssue] = field(default_factory=list)
    file_errors: list[str] = field(default_factory=list)
    total_rows: int = 0

    @property
    def errors(self) -> list[RowIssue]:
        """저장을 막는 문제만 반환합니다."""
        return [issue for issue in self.issues if issue.severity == "error"]

    @property
    def warnings(self) -> list[RowIssue]:
        """저장 가능하나 확인이 필요한 문제만 반환합니다."""
        return [issue for issue in self.issues if issue.severity == "warning"]

    @property
    def error_row_count(self) -> int:
        """오류가 있는 **행**의 수(한 행에 문제가 여러 개여도 1건)."""
        return len({issue.row_number for issue in self.errors})

    @property
    def ok(self) -> bool:
        """파일 오류도 행 오류도 없으면 ``True``."""
        return not self.file_errors and not self.errors

    def summary_lines(self) -> tuple[str, ...]:
        """사용자에게 보여줄 요약을 반환합니다."""
        if self.file_errors:
            return ("파일을 읽을 수 없습니다.", *(f"· {text}" for text in self.file_errors))

        lines = [
            f"총 {self.total_rows:,}건",
            f"정상 {len(self.rows):,}건",
            f"오류 {self.error_row_count:,}건",
        ]
        if self.warnings:
            lines.append(f"확인 필요 {len(self.warnings):,}건")
        return tuple(lines)

    def issue_lines(self, limit: int = 100) -> tuple[str, ...]:
        """문제 목록을 행 순서대로 반환합니다.

        Args:
            limit: 최대 표시 건수. 너무 많으면 화면이 무의미해집니다.
        """
        ordered = sorted(self.issues, key=lambda issue: (issue.row_number, issue.header or ""))
        lines = [issue.format_line() for issue in ordered[:limit]]
        if len(ordered) > limit:
            lines.append(f"... 외 {len(ordered) - limit:,}건")
        return tuple(lines)


def validate_headers(
    headers: Sequence[str],
    *,
    columns: Sequence[StandardColumn] = STANDARD_COLUMNS,
) -> list[str]:
    """머리글 행을 검증합니다.

    Args:
        headers: 엑셀 1행의 값.
        columns: 검사 기준이 되는 양식 정의. 기본은 **구매 표준 양식**입니다.
            기업정보 양식처럼 다른 양식을 검사할 때만 넘깁니다.

            .. note::
                양식마다 검증기를 따로 만들면 사업자등록번호·날짜 규칙이 두 벌이
                되어 한쪽만 고치는 일이 생깁니다. **규칙은 하나**로 두고 컬럼
                정의만 갈아 끼웁니다.

    Returns:
        파일 단위 오류 메시지 목록. 정상이면 빈 목록.
    """
    required = tuple(column.header for column in columns)
    present = {str(header).strip() for header in headers if str(header).strip()}
    missing = [header for header in required if header not in present]

    errors: list[str] = []
    if missing:
        errors.append(
            f"필수 항목이 없습니다: {', '.join(missing)}. "
            "표준 양식을 내려받아 머리글을 그대로 사용하세요."
        )
    return errors


def validate_rows(
    rows: Iterable[Mapping[str, object]],
    *,
    first_row_number: int = 2,
    columns: Sequence[StandardColumn] = STANDARD_COLUMNS,
) -> ValidationReport:
    """행 목록을 검증합니다.

    Args:
        rows: 머리글 → 값 매핑의 목록. 엑셀에서 읽어 온 그대로를 넣습니다.
        first_row_number: 첫 행의 엑셀 행 번호. 머리글이 1행이므로 기본 2입니다.
        columns: 검사 기준이 되는 양식 정의. 기본은 **구매 표준 양식**입니다.

    Returns:
        :class:`ValidationReport`.
    """
    validated: list[ValidatedRow] = []
    issues: list[RowIssue] = []
    total = 0

    for offset, row in enumerate(rows):
        row_number = first_row_number + offset
        total += 1
        values, row_issues = _validate_row(row, row_number, columns)
        issues.extend(row_issues)
        if any(issue.severity == "error" for issue in row_issues):
            continue
        validated.append(
            ValidatedRow(
                row_number=row_number,
                values=values,
                warnings=tuple(issue for issue in row_issues if issue.severity == "warning"),
            )
        )

    return ValidationReport(rows=validated, issues=issues, total_rows=total)


def _validate_row(
    row: Mapping[str, object],
    row_number: int,
    columns: Sequence[StandardColumn] = STANDARD_COLUMNS,
) -> tuple[dict[str, object], list[RowIssue]]:
    """행 하나를 검증하고 정규화합니다."""
    values: dict[str, object] = {}
    issues: list[RowIssue] = []

    for column in columns:
        raw = row.get(column.header)
        if _is_blank(raw):
            if column.required:
                issues.append(
                    RowIssue(row_number=row_number, header=column.header, message="값이 없습니다.")
                )
            continue

        parsed, problem = _parse_value(column, raw, row_number)
        if problem is not None:
            issues.append(problem)
            if problem.severity == "error":
                continue
        values[column.key] = parsed

    return values, issues


def _parse_value(
    column: StandardColumn, raw: object, row_number: int
) -> tuple[object, RowIssue | None]:
    """컬럼 종류에 맞게 값을 해석합니다."""
    if column.key in (
        "resolution_date",
        "contract_date",
        "payment_date",
        "issue_date",
        # 기업정보 양식의 인증 유효기간 — **같은 날짜 규칙**을 씁니다.
        "valid_from",
        "valid_to",
    ):
        parsed_date = _parse_date(raw)
        if parsed_date is None:
            return None, RowIssue(
                row_number=row_number,
                header=column.header,
                message=f"날짜 형식이 잘못되었습니다: {raw!r} (예: 2026-03-15)",
            )
        return parsed_date, None

    if column.key == "business_no":
        return _parse_business_no(raw, column, row_number)

    if column.key == "amount":
        return _parse_amount(raw, column, row_number)

    return str(raw).strip(), None


def _parse_business_no(
    raw: object, column: StandardColumn, row_number: int
) -> tuple[object, RowIssue | None]:
    """사업자등록번호를 **기존 규칙 그대로** 정규화합니다.

    하이픈 제거 후 10자리, 9자리 자동 보정 금지, 체크섬 오류는 경고(D-002).
    """
    normalized = normalize_business_no(raw)
    if not normalized.is_valid or normalized.value is None:
        return None, RowIssue(
            row_number=row_number,
            header=column.header,
            message=f"사업자등록번호를 사용할 수 없습니다: {raw!r} (하이픈 제외 10자리)",
        )
    if normalized.warnings:
        return normalized.value, RowIssue(
            row_number=row_number,
            header=column.header,
            message="; ".join(normalized.warnings),
            severity="warning",
        )
    return normalized.value, None


def _parse_amount(
    raw: object, column: StandardColumn, row_number: int
) -> tuple[object, RowIssue | None]:
    """금액을 :class:`~decimal.Decimal` 로 해석합니다."""
    if isinstance(raw, bool):
        return None, RowIssue(
            row_number=row_number, header=column.header, message="숫자가 아닙니다."
        )
    if isinstance(raw, int | float | Decimal):
        amount = Decimal(str(raw))
    else:
        text = _AMOUNT_NOISE.sub("", str(raw).strip())
        try:
            amount = Decimal(text)
        except (InvalidOperation, ValueError):
            return None, RowIssue(
                row_number=row_number,
                header=column.header,
                message=f"숫자가 아닙니다: {raw!r}",
            )

    if amount <= 0:
        # ⛔ 0 이하 금액의 처리 방식은 **확정되지 않았다.**
        #
        # 음수 상계 규칙은 확정됐지만(DECISIONS §0.6.3), 현재 Repository 가
        # amount <= 0 저장을 거부한다. 여기서 오류로 단정하면 확정되지 않은
        # 규칙을 만드는 셈이고, 정상으로 넘기면 저장 단계에서 실패한다.
        # 따라서 **경고로 표시만 하고 판단은 호출자에게 남긴다.**
        return amount, RowIssue(
            row_number=row_number,
            header=column.header,
            message=(
                f"0 이하 금액입니다: {amount}. "
                "현재 시스템은 0 이하 금액을 저장하지 않습니다(처리 방식 확정 대기)."
            ),
            severity="warning",
        )
    return amount, None


def _parse_date(raw: object) -> date | None:
    """날짜를 해석합니다. 해석할 수 없으면 ``None``."""
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw

    text = str(raw).strip()
    for pattern in _DATE_FORMATS:
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _is_blank(value: object) -> bool:
    """값이 비어 있는지 판단합니다."""
    return value is None or (isinstance(value, str) and not value.strip())
