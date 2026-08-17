"""
tests.test_upload_mapping

**Mapping 계층** 검증 — 검증 결과를 적재 계층 형태로 넘기는 얇은 연결자.

이 계층에서 가장 중요한 성질은 "무엇을 하는가" 가 아니라 **"무엇을 하지
않는가"** 입니다.

- 값을 바꾸지 않는다
- 없는 값을 채우지 않는다
- 새 키를 만들지 않는다
- 업무 판정을 하지 않는다

여기서 값을 손대면 검증 규칙이 두 곳에 생기고, 어느 쪽이 진짜인지 알 수 없게
됩니다.
"""

from __future__ import annotations

import ast
import inspect
from datetime import date
from decimal import Decimal
from pathlib import Path

from procurement.importers.purchase_importer import PurchaseImporter
from procurement.uploads import validate_rows
from procurement.uploads.mapping import MAPPED_KEYS, mapped_keys, to_import_row, to_import_rows

GOOD_ROW: dict[str, object] = {
    "결의일자": "2026-03-15",
    "계약일자": "2026-02-20",
    "지급일": "2026-04-01",
    "기업명": "한빛산업개발",
    "사업자등록번호": "220-81-62517",
    "계": "54,648,000",
}


def _validated(row: dict[str, object] | None = None) -> object:
    """검증을 통과한 행 하나를 만듭니다."""
    report = validate_rows([row if row is not None else GOOD_ROW])
    assert report.ok, report.issue_lines()
    return report.rows[0]


class TestPassThrough:
    """값을 **그대로** 넘긴다."""

    def test_all_six_keys_are_mapped(self) -> None:
        mapped = to_import_row(_validated())  # type: ignore[arg-type]
        assert set(mapped) == set(MAPPED_KEYS)

    def test_values_are_identical_to_validation_output(self) -> None:
        """⛔ 값 변환이 **일어나지 않는다.**"""
        row = _validated()
        mapped = to_import_row(row)  # type: ignore[arg-type]

        for key, value in mapped.items():
            assert value is row.values[key]  # type: ignore[attr-defined]

    def test_types_are_what_the_importer_accepts(self) -> None:
        mapped = to_import_row(_validated())  # type: ignore[arg-type]

        assert isinstance(mapped["resolution_date"], date)
        assert isinstance(mapped["contract_date"], date)
        assert isinstance(mapped["payment_date"], date)
        assert isinstance(mapped["amount"], Decimal)
        assert isinstance(mapped["business_no"], str)
        assert isinstance(mapped["company_name"], str)

    def test_three_dates_stay_distinct(self) -> None:
        """⛔ 세 날짜가 서로 대체되지 않는다."""
        mapped = to_import_row(_validated())  # type: ignore[arg-type]

        assert mapped["resolution_date"] == date(2026, 3, 15)
        assert mapped["contract_date"] == date(2026, 2, 20)
        assert mapped["payment_date"] == date(2026, 4, 1)

    def test_row_order_is_preserved(self) -> None:
        """행 순서를 유지한다(오류 행 번호와 저장 순서가 어긋나지 않도록)."""
        second = dict(GOOD_ROW)
        second["기업명"] = "가나전자"
        report = validate_rows([GOOD_ROW, second])

        mapped = to_import_rows(report.rows)

        assert [row["company_name"] for row in mapped] == ["한빛산업개발", "가나전자"]


class TestInventsNothing:
    """⛔ 없는 값을 만들어 내지 않는다."""

    def test_no_extra_keys(self) -> None:
        row = _validated()
        mapped = to_import_row(row)  # type: ignore[arg-type]

        assert set(mapped) <= set(row.values)  # type: ignore[attr-defined]

    def test_empty_input_gives_empty_output(self) -> None:
        assert to_import_rows([]) == []

    def test_mapping_module_has_no_business_terms(self) -> None:
        """⛔ 업무 판정 낱말이 이 모듈에 등장하지 않는다."""
        import procurement.uploads.mapping as module

        assert module.__file__ is not None
        source = Path(module.__file__).read_text(encoding="utf-8")

        for term in ("valid_from", "valid_to", "인증", "달성률", "상계", "GOODS"):
            assert term not in source, term

    def test_mapping_does_not_import_storage(self) -> None:
        """계층 분리 유지 — Mapping 이 DB 를 알지 못한다."""
        import procurement.uploads.mapping as module

        assert module.__file__ is not None
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        assert not [name for name in imported if "database" in name]


class TestContractWithImporter:
    """적재 계층과의 계약을 고정한다."""

    def test_mapped_keys_match_importer_keys(self) -> None:
        """Mapping 이 넘기는 키가 Importer 가 읽는 키와 **정확히 일치**한다.

        어느 한쪽이 바뀌면 여기서 먼저 깨집니다.
        """
        source = Path(inspect.getfile(PurchaseImporter)).read_text(encoding="utf-8")
        importer_keys: set[str] = set()
        for node in ast.walk(ast.parse(source)):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "get"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "row"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                importer_keys.add(node.args[0].value)

        assert set(MAPPED_KEYS) == importer_keys

    def test_mapped_keys_is_public(self) -> None:
        assert tuple(mapped_keys()) == MAPPED_KEYS
