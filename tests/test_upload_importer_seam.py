"""
tests.test_upload_importer_seam

**표준 업로드 검증 계층과 기존 적재 계층 사이의 이음매(seam)** 를 고정합니다.

두 계층은 이제 Mapping 계층으로 연결되어 있습니다::

    표준 Excel → Validation → Mapping → PurchaseImporter → DB
                  ↑ 구현       ↑ 구현    ↑ 기존 재사용

이 파일은 **이음매의 계약**을 고정합니다.

1. 두 계층이 실제로 맞물리는가 (키 이름·타입이 통하는가)
2. 맞물리지 **않는** 지점이 남아 있지 않은가

.. note::
    **계약 검증입니다.** 업무규칙·계산·DB 스키마를 건드리지 않고, 기존 코드가
    이미 가진 성질만 확인합니다. 어느 한쪽이 바뀌면 여기서 먼저 깨지므로,
    두 계층이 조용히 어긋나는 일을 막습니다.

.. note::
    **이력** — 2026-08-17 PM 결정 전까지 이 파일은 "결의일자를 어느 물리 필드에
    넣을지" 와 "지급일을 무엇으로 채울지" 라는 **미해결 결정 지점**을 실행 가능한
    형태로 고정하고 있었습니다. 두 결정이 모두 내려져(``resolution_date`` 신설 ·
    표준 양식에 ``지급일`` 추가) 해당 단언은 실제 연결 검증으로 대체했습니다.
"""

from __future__ import annotations

import ast
import inspect
from datetime import date
from decimal import Decimal
from pathlib import Path

from procurement.database.purchase_repository import PurchaseRepository
from procurement.importers.batch_import_service import BatchImportService
from procurement.importers.purchase_importer import PurchaseImporter
from procurement.uploads import header_row, validate_rows

#: 표준 양식 정상 행 한 건.
GOOD_ROW: dict[str, object] = {
    "결의일자": "2026-03-15",
    "지급일": "2026-04-01",
    "계약일자": "2026-02-20",
    "기업명": "한빛산업개발",
    "사업자등록번호": "220-81-62517",
    "계": "54,648,000",
    # 2026-08-20 음수 상계 업무규칙 확정으로 추가된 3컬럼.
    "신고기준일": "2026-03-10",
    "적요": "사무용품 구매",
    "예산과목": "소모성물품구입비",
}


def _importer_row_keys() -> set[str]:
    """``PurchaseImporter`` 가 실제로 읽는 행 키를 소스에서 추출합니다.

    문서가 아니라 **코드**를 근거로 삼습니다. 시그니처가 ``Mapping[str, Any]``
    라 타입만으로는 알 수 없으므로 ``row.get("...")`` 호출을 찾습니다.
    """
    source = Path(inspect.getfile(PurchaseImporter)).read_text(encoding="utf-8")
    keys: set[str] = set()
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
            keys.add(node.args[0].value)
    return keys


def _validated_keys() -> set[str]:
    """검증 계층이 만들어 내는 값의 키를 반환합니다."""
    report = validate_rows([GOOD_ROW])
    assert report.ok
    return set(report.rows[0].values)


class TestTheTwoLayersFit:
    """두 계층이 실제로 맞물린다."""

    def test_importer_reads_the_keys_we_expect(self) -> None:
        """``PurchaseImporter`` 가 읽는 키 (코드에서 추출).

        이 목록이 바뀌면 Mapping 계층도 바뀌어야 하므로 여기서 먼저 깨집니다.
        """
        assert _importer_row_keys() == {
            "business_no",
            "company_name",
            "contract_date",
            "payment_date",
            "resolution_date",
            "issue_date",
            "description",
            "budget_account",
            "amount",
        }

    def test_all_keys_match_by_name(self) -> None:
        """검증 결과의 키가 **이름까지 그대로** 적재 계층에 통한다.

        .. note::
            **기대값이 바뀐 이유**

            - 2026-08-17 PM 결정으로 ``지급일`` 이 추가되면서 마지막 빈칸이
              채워졌습니다. Mapping 계층은 새 변환기가 아니라 **얇은 연결자**
              입니다.
            - 2026-08-20 음수 상계 업무규칙 확정으로 ``신고기준일`` ·
              ``적요`` · ``예산과목`` 이 추가되었습니다(6 → 9).
        """
        assert _validated_keys() == _importer_row_keys()
        assert _validated_keys() == {
            "business_no",
            "company_name",
            "contract_date",
            "payment_date",
            "resolution_date",
            "issue_date",
            "description",
            "budget_account",
            "amount",
        }

    def test_validated_types_are_what_the_importer_accepts(self) -> None:
        """검증이 만든 값의 타입이 적재 계층이 받는 타입과 맞는다."""
        values = validate_rows([GOOD_ROW]).rows[0].values

        assert isinstance(values["contract_date"], date)
        assert isinstance(values["resolution_date"], date)
        assert isinstance(values["amount"], Decimal)
        assert isinstance(values["business_no"], str)
        assert isinstance(values["company_name"], str)

    def test_business_no_is_already_normalized(self) -> None:
        """검증 계층이 이미 정규화했으므로 적재 계층이 다시 해도 값이 같다.

        두 계층이 **같은 규칙**(`matchers.business_no`)을 쓴다는 뜻입니다.
        """
        from procurement.matchers.business_no import normalize_business_no

        validated = validate_rows([GOOD_ROW]).rows[0].values["business_no"]
        again = normalize_business_no(validated)

        assert again.is_valid
        assert again.value == validated == "2208162517"


class TestMappingIsAThinConnector:
    """✅ 남은 결정 지점이 없다 — Mapping 은 값을 바꾸지 않는다."""

    def test_no_key_is_orphaned(self) -> None:
        """양쪽 키 집합이 정확히 일치한다(고아 키·빈칸 없음)."""
        assert _validated_keys() - _importer_row_keys() == set()
        assert _importer_row_keys() - _validated_keys() == set()

    def test_mapping_passes_values_through_unchanged(self) -> None:
        """⛔ Mapping 은 값을 **그대로** 넘긴다.

        여기서 값을 고치면 검증 규칙이 두 곳에 생기고, 어느 쪽이 진짜인지 알
        수 없게 됩니다.
        """
        from procurement.uploads.mapping import to_import_row

        row = validate_rows([GOOD_ROW]).rows[0]
        mapped = to_import_row(row)

        assert mapped == {key: row.values[key] for key in mapped}

    def test_mapping_invents_nothing(self) -> None:
        """⛔ 검증이 만들지 않은 키를 Mapping 이 새로 만들지 않는다."""
        from procurement.uploads.mapping import to_import_row

        row = validate_rows([GOOD_ROW]).rows[0]

        assert set(to_import_row(row)) <= set(row.values)

    def test_importer_accepts_the_mapped_row(self, tmp_path: Path) -> None:
        """매핑 결과를 기존 Importer 가 그대로 받아 저장한다."""
        from procurement.database.company_repository import CompanyRepository
        from procurement.uploads.mapping import to_import_rows

        db_path = tmp_path / "seam-import.db"
        purchase_repo = PurchaseRepository(db_path)
        purchase_repo.create_table()
        company_repo = CompanyRepository(db_path)
        company_repo.create_table()

        report = validate_rows([GOOD_ROW])
        result = PurchaseImporter(purchase_repo, company_repo).import_rows(
            to_import_rows(report.rows)
        )

        assert result.stored_count == 1
        assert purchase_repo.count() == 1


class TestExistingStorageIsReusable:
    """저장 엔진을 새로 만들 필요가 없다 — 기존 것을 그대로 쓴다."""

    def test_batch_import_service_signature_is_stable(self) -> None:
        """업로드 API 가 호출할 진입점의 인자 구성을 고정한다."""
        signature = inspect.signature(BatchImportService.import_batch)
        assert list(signature.parameters) == [
            "self",
            "rows",
            "file_name",
            "period_start",
            "period_end",
            "file_hash",
        ]

    def test_period_is_supplied_by_the_caller(self) -> None:
        """⛔ 대상 기간은 **호출자가 지정**한다. 파일에서 유추하지 않는다.

        어느 날짜로 연도를 나눌지가 확정되지 않았으므로(D-24 계열), 기간을
        파일 내용에서 추론하면 확정되지 않은 규칙이 생깁니다.
        """
        signature = inspect.signature(BatchImportService.import_batch)
        for name in ("period_start", "period_end"):
            assert signature.parameters[name].default is inspect.Parameter.empty

    def test_importer_accepts_a_batch_id(self, tmp_path: Path) -> None:
        """행 적재와 배치를 이미 연결할 수 있다."""
        signature = inspect.signature(PurchaseImporter.import_rows)
        assert "batch_id" in signature.parameters

        repository = PurchaseRepository(tmp_path / "seam.db")
        repository.create_table()
        assert repository.count() == 0


class TestNoBusinessRuleChange:
    """이 파일은 계약만 확인한다 — 업무규칙을 바꾸지 않는다."""

    def test_validation_layer_still_has_no_storage_dependency(self) -> None:
        """검증 계층이 저장 계층을 import 하지 않는다(계층 분리 유지)."""
        import procurement.uploads.validation as module

        assert module.__file__ is not None
        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.append(node.module)

        assert not [name for name in imported if "database" in name]

    def test_standard_form_has_only_confirmed_columns(self) -> None:
        """표준 양식에 **확정 컬럼만** 있다(미확정 컬럼 유입 방지).

        .. note::
            **기대값이 바뀐 이유**

            - 2026-08-17 PM 결정으로 ``지급일`` 추가.
            - 2026-08-20 음수 상계 업무규칙 확정으로 ``신고기준일`` ·
              ``적요`` · ``예산과목`` 추가. 확정되지 않은 컬럼(구매유형 ·
              대표자명 · 거래구분)은 여전히 들어 있지 않습니다.
        """
        from procurement.uploads import PENDING_COLUMNS

        assert header_row() == (
            "결의일자",
            "계약일자",
            "지급일",
            "기업명",
            "사업자등록번호",
            "계",
            "신고기준일",
            "적요",
            "예산과목",
        )
        assert not set(header_row()) & set(PENDING_COLUMNS)
