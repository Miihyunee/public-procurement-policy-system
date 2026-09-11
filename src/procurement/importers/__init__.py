"""
procurement.importers

고객이 제공한 구매(지출) 데이터를 시스템에 적재하는 계층입니다.

컬럼 매핑이 끝난 행(dict)을 입력으로 받아 정규화·검증·기업 연결을 수행하고,
정상/경고/실패를 구분한 리포트를 반환합니다::

    from procurement.importers import PurchaseImporter

    importer = PurchaseImporter(purchase_repository, company_repository)
    report = importer.import_rows(rows)
    print(report.format_report())

매월 누적 적재는 배치 단위로 수행합니다. 같은 기간을 다시 올리면 이전 배치를
대체합니다(D-25)::

    from procurement.importers import BatchImportService

    service = BatchImportService(importer, batch_repository, purchase_repository)
    result = service.import_batch(
        rows,
        file_name="2026-07.xlsx",
        period_start=date(2026, 7, 1),
        period_end=date(2026, 7, 31),
    )

.. note::
    파일(Excel/CSV) 파싱은 포함하지 않습니다. 실제 고객 파일의 형식을 확인한
    뒤 별도로 붙입니다. 설계는 ``docs/PURCHASE_IMPORT_DESIGN.md`` 를 따릅니다.
"""

from procurement.importers.batch_import_service import BatchImportResult, BatchImportService
from procurement.importers.purchase_importer import (
    ImportReport,
    ImportRowResult,
    ImportStatus,
    PurchaseImporter,
)

__all__ = [
    "BatchImportResult",
    "BatchImportService",
    "ImportReport",
    "ImportRowResult",
    "ImportStatus",
    "PurchaseImporter",
]
