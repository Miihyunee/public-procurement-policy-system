"""
tests.test_upload_importer_seam

**표준 업로드 검증 계층과 기존 적재 계층 사이의 이음매(seam)** 를 고정합니다.

목표 구조에서 두 계층은 아직 연결되어 있지 않습니다::

    표준 Excel → Validation → [ Mapping — 미구현 ] → PurchaseImporter → DB
                  ↑ 구현됨                              ↑ 구현됨(기존)

이 파일은 **Mapping 계층을 만들기 전에** 다음을 검증합니다.

1. 두 계층이 실제로 맞물리는가 (키 이름·타입이 통하는가)
2. 맞물리지 **않는** 지점이 정확히 어디인가 (= PM 결정이 필요한 곳)

.. note::
    **구현이 아니라 계약 검증입니다.** 업무규칙·계산·DB 스키마를 건드리지 않고,
    기존 코드가 이미 가진 성질만 확인합니다. 나중에 어느 한쪽이 바뀌면 여기서
    먼저 깨지므로, 실제 연결 작업 때 어긋남을 미리 잡을 수 있습니다.

.. warning::
    ⛔ **결의일자를 어느 물리 필드에 넣을지 결정하지 않습니다.**
    아래 :class:`TestTheOnlyMissingLink` 가 그 결정이 필요한 지점을 **실행 가능한
    형태로** 드러낼 뿐입니다.
"""

from __future__ import annotations

import ast
import inspect
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from procurement.database.purchase_repository import PurchaseRepository
from procurement.importers.batch_import_service import BatchImportService
from procurement.importers.purchase_importer import PurchaseImporter
from procurement.uploads import header_row, validate_rows

#: 표준 양식 정상 행 한 건.
GOOD_ROW: dict[str, object] = {
    "결의일자": "2026-03-15",
    "계약일자": "2026-02-20",
    "기업명": "한빛산업개발",
    "사업자등록번호": "220-81-62517",
    "계": "54,648,000",
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

        .. note::
            **기대값이 바뀐 이유** — 2026-08-15 PM 최종 결정(B안)으로
            ``resolution_date`` 가 신설되어 적재 계층이 이 키를 읽습니다.
        """
        assert _importer_row_keys() == {
            "business_no",
            "company_name",
            "contract_date",
            "payment_date",
            "resolution_date",
            "amount",
        }

    def test_all_validated_keys_now_reach_storage(self) -> None:
        """검증 결과의 키 5개가 **모두** 이름 그대로 적재 계층에 통한다.

        .. note::
            **기대값이 바뀐 이유** — 이전에는 4개만 통하고 ``resolution_date``
            가 갈 곳이 없었습니다(= PM 결정 지점). 결정이 내려져 필드가
            생겼으므로 이제 5개 모두 통합니다.
        """
        assert _validated_keys() <= _importer_row_keys()
        assert _validated_keys() == {
            "business_no",
            "company_name",
            "contract_date",
            "resolution_date",
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


class TestTheRemainingOpenPoint:
    """🔴 아직 값을 채워 줄 수 없는 항목은 **정확히 하나**다 — PM 결정 사항이다."""

    def test_no_validated_key_is_orphaned_anymore(self) -> None:
        """검증이 만드는 키 중 갈 곳이 없는 것은 **더 이상 없다**."""
        assert _validated_keys() - _importer_row_keys() == set()

    def test_payment_date_still_has_no_source_in_the_standard_form(self) -> None:
        """표준 양식에는 **지급일 컬럼이 없다.**

        그런데 적재 계층은 ``payment_date`` 를 **필수**로 요구합니다. 즉 업로드
        경로를 실제로 연결하려면 다음 중 하나를 PM 이 정해야 합니다.

        1. 표준 양식에 지급일 컬럼을 추가한다
        2. ``payment_date`` 를 선택 항목으로 바꾼다(NULL 허용)
        3. 업로드 경로에서만 다른 날짜로 채운다

        ⛔ **이 테스트는 셋 중 어느 것도 고르지 않는다.** 결정이 필요하다는
        사실만 고정한다. ``payment_date`` 를 결의일자로 대체하는 선택지는
        2026-08-15 PM 결정으로 이미 배제되었다.
        """
        assert _importer_row_keys() - _validated_keys() == {"payment_date"}

    def test_payment_date_is_still_required_by_storage(self) -> None:
        """지급일이 비면 행이 실패한다(= 위 결정 없이는 업로드가 통하지 않는다)."""
        from procurement.importers.purchase_importer import _parse_date

        _, error = _parse_date(None, "지급일")
        assert error is not None

    def test_mapping_layer_does_not_exist_yet(self) -> None:
        """Mapping 계층을 아직 만들지 않았다.

        승인 후 만들면 이 테스트를 삭제하고 실제 매핑 테스트로 대체합니다.
        """
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("procurement.uploads.mapping")


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

    def test_standard_form_still_has_only_confirmed_columns(self) -> None:
        """표준 양식 컬럼이 늘어나지 않았다(미확정 컬럼 유입 방지)."""
        assert header_row() == ("결의일자", "계약일자", "기업명", "사업자등록번호", "계")
