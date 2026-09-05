"""STEP 39 — 미매칭 구매를 기업정보와 다시 연결하는 운영 진입점.

구매데이터가 먼저 들어오고 기업정보가 나중에 확보되는 것이 이 시스템의 정상
흐름입니다(``PURCHASE_IMPORT_DESIGN.md`` §6.3 "경우 B"). 연결 로직 자체는
``PurchaseImporter.rematch()`` 에 이미 있었지만 **부를 방법이 없었습니다.**

.. warning::
    ⛔ **기업정보를 만들지 않습니다.** 등록된 기업이 있는 건만 연결되고,
    없는 건은 그대로 미매칭으로 남습니다.
    ⛔ **연결 규칙을 바꾸지 않습니다** — 사업자등록번호 완전 일치 그대로입니다.
    ⛔ **이미 연결된 구매를 건드리지 않습니다**(멱등).

.. note::
    ``rematch()`` 는 새로 연결된 건수 하나만 돌려주고 실패 사유를 구분해 주지
    않습니다. 그래서 응답에도 "오류 N건" 이 **없고**, 연결되지 않고 남은 건수만
    사실대로 담습니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from procurement.app import create_app
from procurement.database.bootstrap import init_db
from procurement.database.company_repository import CompanyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.importers.purchase_importer import PurchaseImporter
from procurement.importers.rematch_service import RematchService
from procurement.models.company import Company
from procurement.models.purchase import Purchase

#: 합성 데이터 — 실제 사업자번호·거래처명을 쓰지 않습니다.
_A = "1000000001"
_B = "2000000002"

_DAY = date(2026, 3, 2)


def _purchase(business_no: str, amount: str = "1000") -> Purchase:
    return Purchase(
        business_no=business_no,
        company_name="합성기업",
        contract_date=_DAY,
        payment_date=_DAY,
        amount=Decimal(amount),
    )


def _company(business_no: str) -> Company:
    return Company(
        business_no=business_no,
        company_name="등록된 합성기업",
        representative_name="합성",
    )


@pytest.fixture
def db(tmp_path: Path) -> Path:
    """빈 스키마만 있는 DB."""
    path = tmp_path / "rematch.db"
    init_db(path)
    return path


@pytest.fixture
def seeded(db: Path) -> Path:
    """미매칭 3건 — ``_A`` 2건 · ``_B`` 1건. ⛔ 기업은 아직 하나도 없습니다."""
    purchases = PurchaseRepository(db)
    purchases.insert(_purchase(_A, "100"))
    purchases.insert(_purchase(_A, "200"))
    purchases.insert(_purchase(_B, "300"))
    return db


def _service(db: Path) -> RematchService:
    purchases = PurchaseRepository(db)
    return RematchService(PurchaseImporter(purchases, CompanyRepository(db)), purchases)


def _unmatched(db: Path) -> int:
    return len(PurchaseRepository(db).find_unmatched())


class TestRematchConnects:
    """기업이 등록되어 있으면 연결된다."""

    def test_registered_company_gets_connected(self, seeded: Path) -> None:
        CompanyRepository(seeded).insert(_company(_A))

        result = _service(seeded).rematch()

        assert result.matched == 2  # _A 두 건
        assert _unmatched(seeded) == 1  # _B 한 건은 남는다

    def test_company_id_is_actually_set(self, seeded: Path) -> None:
        company = CompanyRepository(seeded).insert(_company(_A))
        _service(seeded).rematch()

        connected = [
            purchase
            for purchase in PurchaseRepository(seeded).find_all()
            if purchase.business_no == _A
        ]
        assert all(purchase.company_id == company.company_id for purchase in connected)

    def test_counts_describe_the_run(self, seeded: Path) -> None:
        CompanyRepository(seeded).insert(_company(_A))

        result = _service(seeded).rematch()

        assert result.unmatched_before == 3
        assert result.attempted == 3
        assert result.matched == 2
        assert result.still_unmatched == 1


class TestNoCompanyStaysUnmatched:
    """⛔ 기업이 없으면 **만들지 않는다** — 그대로 미매칭으로 둔다."""

    def test_nothing_matches_without_companies(self, seeded: Path) -> None:
        result = _service(seeded).rematch()

        assert result.matched == 0
        assert result.still_unmatched == 3

    def test_no_company_row_is_created(self, seeded: Path) -> None:
        companies = CompanyRepository(seeded)
        assert companies.count() == 0

        _service(seeded).rematch()

        assert companies.count() == 0

    def test_purchase_rows_are_not_lost(self, seeded: Path) -> None:
        """⛔ 연결하지 못했다고 구매를 지우거나 바꾸지 않는다."""
        before = PurchaseRepository(seeded).find_all()

        _service(seeded).rematch()

        after = PurchaseRepository(seeded).find_all()
        assert len(after) == len(before)
        assert [purchase.amount for purchase in after] == [purchase.amount for purchase in before]


class TestMatchedPurchasesAreUntouched:
    """⛔ 이미 연결된 구매는 건드리지 않는다."""

    def test_existing_link_survives(self, seeded: Path) -> None:
        purchases = PurchaseRepository(seeded)
        companies = CompanyRepository(seeded)
        first = companies.insert(_company(_A))
        target = purchases.find_unmatched()[0]
        assert target.purchase_id is not None
        purchases.update_company_id(target.purchase_id, first.company_id or 0)
        before = purchases.find_by_id(target.purchase_id)
        assert before is not None

        _service(seeded).rematch()

        after = purchases.find_by_id(target.purchase_id)
        assert after is not None
        assert after.company_id == before.company_id

    def test_only_unmatched_are_attempted(self, seeded: Path) -> None:
        """시도 건수는 실행 시점의 미매칭 수와 같다 — 전체가 아니다."""
        purchases = PurchaseRepository(seeded)
        company = CompanyRepository(seeded).insert(_company(_A))
        target = purchases.find_unmatched()[0]
        assert target.purchase_id is not None
        purchases.update_company_id(target.purchase_id, company.company_id or 0)

        result = _service(seeded).rematch()

        assert len(purchases.find_all()) == 3
        assert result.attempted == 2  # ⛔ 3 이 아니다


class TestIdempotence:
    """여러 번 실행해도 중복·부작용이 없다."""

    def test_second_run_matches_nothing_new(self, seeded: Path) -> None:
        CompanyRepository(seeded).insert(_company(_A))
        service = _service(seeded)

        first = service.rematch()
        second = service.rematch()

        assert first.matched == 2
        assert second.matched == 0
        assert second.attempted == 1  # 남은 _B 한 건만 다시 본다

    def test_repeated_runs_do_not_change_data(self, seeded: Path) -> None:
        CompanyRepository(seeded).insert(_company(_A))
        service = _service(seeded)
        service.rematch()
        snapshot = [
            (purchase.purchase_id, purchase.company_id, purchase.amount)
            for purchase in PurchaseRepository(seeded).find_all()
        ]

        service.rematch()
        service.rematch()

        after = [
            (purchase.purchase_id, purchase.company_id, purchase.amount)
            for purchase in PurchaseRepository(seeded).find_all()
        ]
        assert after == snapshot

    def test_a_company_registered_later_is_picked_up(self, seeded: Path) -> None:
        """이것이 이 기능의 존재 이유다 — 기업이 나중에 들어와도 연결된다."""
        service = _service(seeded)
        assert service.rematch().matched == 0

        CompanyRepository(seeded).insert(_company(_B))

        assert service.rematch().matched == 1


class TestEmptyDatabase:
    def test_no_purchases_is_not_an_error(self, db: Path) -> None:
        result = _service(db).rematch()

        assert result.unmatched_before == 0
        assert result.attempted == 0
        assert result.matched == 0
        assert result.still_unmatched == 0


class TestHttp:
    """HTTP 계약 — 화면이 실제로 쓰는 모양."""

    def test_returns_the_counts(self, seeded: Path) -> None:
        CompanyRepository(seeded).insert(_company(_A))
        client = TestClient(create_app(seeded))

        response = client.post("/purchases/rematch")

        assert response.status_code == 200
        body = response.json()
        assert body["unmatched_before"] == 3
        assert body["attempted"] == 3
        assert body["matched"] == 2
        assert body["still_unmatched"] == 1

    def test_notice_does_not_call_leftovers_an_error(self, seeded: Path) -> None:
        """⛔ 남은 건을 "오류" · "실패" 로 부르지 않는다 — 기업정보가 없을 뿐이다."""
        client = TestClient(create_app(seeded))

        body = client.post("/purchases/rematch").json()

        assert body["matched"] == 0
        for banned in ("오류", "실패", "에러"):
            assert banned not in body["notice"], banned
        assert "등록" in body["notice"]

    def test_no_error_count_is_invented(self, seeded: Path) -> None:
        """기존 계층이 실패 사유를 주지 않으므로 없는 정보를 만들지 않는다."""
        client = TestClient(create_app(seeded))

        body = client.post("/purchases/rematch").json()

        assert "error_count" not in body
        assert "errors" not in body

    def test_empty_run_says_so(self, db: Path) -> None:
        client = TestClient(create_app(db))

        body = client.post("/purchases/rematch").json()

        assert body["attempted"] == 0
        assert "없" in body["notice"]

    def test_wrong_methods_are_refused(self, seeded: Path) -> None:
        """⛔ 조회로 착각해 GET 으로 부를 수 없게 한다 — 데이터를 바꾸는 조작이다."""
        client = TestClient(create_app(seeded))

        for call in (client.get, client.put, client.delete):
            assert call("/purchases/rematch").status_code == 405

    def test_no_request_body_is_required(self, seeded: Path) -> None:
        """확인창이 보내는 것은 빈 POST 뿐이다."""
        client = TestClient(create_app(seeded))

        assert client.post("/purchases/rematch").status_code == 200


class TestConsistencyWithTheScreens:
    """STEP 38 화면·대시보드와 같은 숫자를 말해야 한다."""

    def test_before_count_matches_data_status(self, seeded: Path) -> None:
        client = TestClient(create_app(seeded))

        status = client.get("/dashboard/data-status").json()
        body = client.post("/purchases/rematch").json()

        assert body["unmatched_before"] == status["unmatched_purchase_count"]

    def test_after_count_matches_the_unmatched_screen(self, seeded: Path) -> None:
        CompanyRepository(seeded).insert(_company(_A))
        client = TestClient(create_app(seeded))

        body = client.post("/purchases/rematch").json()
        screen = client.get("/dashboard/unmatched-companies").json()

        assert body["still_unmatched"] == screen["unmatched_purchase_count"]

    def test_the_screen_shrinks_after_a_run(self, seeded: Path) -> None:
        CompanyRepository(seeded).insert(_company(_A))
        client = TestClient(create_app(seeded))
        before = client.get("/dashboard/unmatched-companies").json()

        client.post("/purchases/rematch")

        after = client.get("/dashboard/unmatched-companies").json()
        assert before["total"] == 2
        assert after["total"] == 1  # _A 가 빠지고 _B 만 남는다
