"""
tests.test_review_regression

🔴 **회귀 방지** — 검토 기능(DB-2)이 기존 것을 바꾸지 않았는가.

STEP 1~3 은 **새 테이블과 새 화면만 추가**하는 작업입니다. 다음 중 하나라도
바뀌면 이 파일이 먼저 깨져야 합니다.

1. Excel → DB-1 업로드 흐름
2. Calculator 계산 결과
3. Dashboard 응답
4. 원본 데이터(``purchase`` 테이블)
5. 기존 DB 의 스키마

설계 근거: ``docs/NEW_ARCHITECTURE.md`` §7 — "1~3 은 기존 계산 결과를 전혀
바꾸지 않는다"
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from procurement.app import create_app
from procurement.calculators import ProcurementAchievementCalculator
from procurement.core.purchase_type import CONSTRUCTION, GOODS, SERVICE
from procurement.database.bootstrap import bootstrap
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.database.review_repository import ReviewRepository
from procurement.models.certification import Certification
from procurement.models.company import Company
from procurement.models.purchase import Purchase
from procurement.uploads.format import header_row

FIXED = date(2026, 3, 15)

#: 정상 행 1건 (9컬럼 표준 양식).
ROW: list[object] = [
    date(2026, 3, 15),  # 결의일자
    date(2026, 2, 20),  # 계약일자
    date(2026, 4, 1),  # 지급일
    "한빛산업개발",
    "220-81-62517",
    54648000,
    date(2026, 3, 10),  # 신고기준일
    "시설물 유지관리",
    "외주용역비",
]


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "regression.db"
    bootstrap(path)
    return path


@pytest.fixture
def client(db_path: Path) -> TestClient:
    return TestClient(create_app(db_path, period_date_field="payment_date"))


def _excel(path: Path) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(list(header_row()))
    sheet.append(ROW)
    workbook.save(path)
    workbook.close()
    return path


def _seed_certified_purchase(db_path: Path) -> int:
    """중소기업 인증 기업의 구매 1건을 심습니다."""
    company = CompanyRepository(db_path).insert(
        Company(business_no="2208162517", company_name="한빛산업개발", representative_name="홍길동")
    )
    policy = PolicyRepository(db_path).find_by_policy_code("SMALL_BUSINESS")
    assert policy is not None and policy.policy_id is not None
    assert company.company_id is not None
    CertificationRepository(db_path).insert(
        Certification(
            company_id=company.company_id,
            policy_id=policy.policy_id,
            valid_from=date(2026, 1, 1),
            valid_to=date(2026, 12, 31),
        )
    )
    saved = PurchaseRepository(db_path).insert(
        Purchase(
            business_no="2208162517",
            company_name="한빛산업개발",
            contract_date=FIXED,
            payment_date=FIXED,
            resolution_date=FIXED,
            issue_date=FIXED,
            description="시설물 유지관리",
            budget_account="외주용역비",
            amount=Decimal("1000000"),
            company_id=company.company_id,
        )
    )
    assert saved.purchase_id is not None
    return saved.purchase_id


class TestUploadFlowStillWorks:
    """① Excel → DB-1 기존 흐름이 그대로 동작한다."""

    def test_upload_is_stored(self, client: TestClient, db_path: Path, tmp_path: Path) -> None:
        path = _excel(tmp_path / "good.xlsx")

        body = client.post("/uploads/purchases", json={"file_path": str(path), "year": 2026}).json()

        assert body["ok"] is True
        assert body["stored"] is True
        assert PurchaseRepository(db_path).count() == 1

    def test_uploaded_values_are_intact(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """업로드가 검토 기능의 영향을 받지 않는다."""
        client.post(
            "/uploads/purchases",
            json={"file_path": str(_excel(tmp_path / "good.xlsx")), "year": 2026},
        )

        stored = PurchaseRepository(db_path).find_all()[0]
        assert stored.description == "시설물 유지관리"
        assert stored.budget_account == "외주용역비"
        assert stored.amount == Decimal("54648000")

    def test_upload_does_not_create_review_rows(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """업로드가 DB-2 에 쓰지 않는다 — 두 흐름은 아직 분리되어 있다."""
        client.post(
            "/uploads/purchases",
            json={"file_path": str(_excel(tmp_path / "good.xlsx")), "year": 2026},
        )

        assert ReviewRepository(db_path).count() == 0


class TestCalculatorIsUnchanged:
    """② Calculator 숫자가 변하지 않는다."""

    def _calculator(self, db_path: Path) -> ProcurementAchievementCalculator:
        return ProcurementAchievementCalculator(
            PurchaseRepository(db_path),
            CertificationRepository(db_path),
            PolicyRepository(db_path),
        )

    def test_confirmation_does_not_change_the_total(
        self, client: TestClient, db_path: Path
    ) -> None:
        purchase_id = _seed_certified_purchase(db_path)
        calculator = self._calculator(db_path)
        before = calculator.calculate_total_purchase()

        client.put(f"/reviews/{purchase_id}", json={"final_purchase_type": CONSTRUCTION})

        assert calculator.calculate_total_purchase() == before

    def test_confirmation_does_not_change_policy_amount(
        self, client: TestClient, db_path: Path
    ) -> None:
        purchase_id = _seed_certified_purchase(db_path)
        policy = PolicyRepository(db_path).find_by_policy_code("SMALL_BUSINESS")
        assert policy is not None and policy.policy_id is not None
        calculator = self._calculator(db_path)
        before = calculator.calculate_policy_purchase(policy.policy_id)

        client.put(f"/reviews/{purchase_id}", json={"final_purchase_type": SERVICE})
        client.post(f"/reviews/{purchase_id}/reopen", json={})
        client.put(f"/reviews/{purchase_id}", json={"final_purchase_type": GOODS})

        assert calculator.calculate_policy_purchase(policy.policy_id) == before

    def test_calculator_does_not_read_db2(self) -> None:
        """⛔ 계산기가 검토 테이블·모듈을 참조하지 않는다.

        .. note::
            **기대값이 바뀐 이유** — 2026-09-03 STEP 103 §9. 여성기업 목표가
            구매유형별(공사 3% · 용역·물품 5%)이라 유형별 분모가 필요해졌고,
            계산기가 ``core.purchase_type`` 의 **낱말**을 알게 되었습니다.

            ⛔ 느슨해진 것이 아닙니다. 계산기는 여전히 검토 테이블도, 분류
            모듈도 건드리지 않습니다 — ``review`` · ``classification`` 금지는
            그대로입니다. 유형을 **고르는** 일은 담당자가 하고 계산기는 받은
            값을 Repository 에 넘길 뿐이며, 그 점은 아래 시험이 못박습니다.
        """
        from pathlib import Path as FilePath

        import procurement.calculators.procurement_achievement as module

        source = FilePath(module.__file__).read_text(encoding="utf-8")
        for forbidden in ("review", "classification"):
            assert forbidden not in source, forbidden

    def test_calculator_does_not_decide_a_purchase_type(self) -> None:
        """⛔ 계산기가 구매유형을 **정하지** 않는다 — 받은 값을 넘길 뿐이다.

        적요·예산과목·거래처명을 보고 유형을 고르는 코드가 생기면 실패합니다.
        """
        from pathlib import Path as FilePath

        import procurement.calculators.procurement_achievement as module

        source = FilePath(module.__file__).read_text(encoding="utf-8")
        for forbidden in ("description", "budget_account", "company_name"):
            assert forbidden not in source, forbidden


class TestDashboardIsUnchanged:
    """③ Dashboard 응답이 변하지 않는다."""

    def test_summary_is_identical_after_review(self, client: TestClient, db_path: Path) -> None:
        purchase_id = _seed_certified_purchase(db_path)
        before = client.get("/dashboard/summary?year=2026").json()

        client.put(f"/reviews/{purchase_id}", json={"final_purchase_type": CONSTRUCTION})

        assert client.get("/dashboard/summary?year=2026").json() == before

    def test_summary_has_no_review_field(self, client: TestClient, db_path: Path) -> None:
        """⛔ 대시보드 응답에 검토 정보가 새어 들어가지 않는다."""
        _seed_certified_purchase(db_path)

        body = client.get("/dashboard/summary?year=2026").json()

        assert "review" not in body
        for item in body["policies"]:
            assert "purchase_type" not in item
            assert "review_status" not in item

    def test_data_status_is_identical_after_review(self, client: TestClient, db_path: Path) -> None:
        purchase_id = _seed_certified_purchase(db_path)
        before = client.get("/dashboard/data-status?year=2026").json()

        client.put(f"/reviews/{purchase_id}", json={"final_purchase_type": GOODS})

        assert client.get("/dashboard/data-status?year=2026").json() == before


class TestOriginalDataIsImmutable:
    """④ 원본 데이터가 불변이다."""

    def test_every_review_action_leaves_db1_alone(self, client: TestClient, db_path: Path) -> None:
        purchase_id = _seed_certified_purchase(db_path)
        before = PurchaseRepository(db_path).find_all()

        client.get("/reviews")
        client.get(f"/reviews/{purchase_id}")
        client.put(
            f"/reviews/{purchase_id}",
            json={"final_purchase_type": CONSTRUCTION, "review_note": "메모"},
        )
        client.post(f"/reviews/{purchase_id}/reopen", json={})
        client.put(f"/reviews/{purchase_id}", json={"final_purchase_type": None})

        assert PurchaseRepository(db_path).find_all() == before

    def test_review_layer_never_writes_to_purchase(self) -> None:
        """⛔ 검토 계층 소스에 원본 쓰기 호출이 없다."""
        from pathlib import Path as FilePath

        root = FilePath(__file__).resolve().parents[1] / "src" / "procurement"
        targets = [
            root / "reviews" / "review_service.py",
            root / "reviews" / "response.py",
            root / "database" / "review_repository.py",
        ]
        for path in targets:
            source = path.read_text(encoding="utf-8")
            for forbidden in (
                "purchase_repository.insert",
                "purchase_repository.update_",
                "_purchase_repository.insert",
                "_purchase_repository.update_",
            ):
                assert forbidden not in source, (path.name, forbidden)


class TestSchemaIsAdditiveOnly:
    """⑤ 기존 스키마를 바꾸지 않고 테이블만 추가했다."""

    def test_purchase_columns_are_unchanged(self, db_path: Path) -> None:
        with sqlite3.connect(str(db_path)) as conn:
            names = [row[1] for row in conn.execute("PRAGMA table_info(purchase)")]

        assert names == [
            "purchase_id",
            "business_no",
            "company_id",
            "company_name",
            "contract_date",
            "payment_date",
            "resolution_date",
            "issue_date",
            "description",
            "budget_account",
            "amount",
            "batch_id",
            "created_at",
            "updated_at",
        ]

    def test_new_tables_exist(self, db_path: Path) -> None:
        with sqlite3.connect(str(db_path)) as conn:
            names = {
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }

        assert {"purchase_review", "purchase_review_history"} <= names

    def test_bootstrap_is_idempotent(self, db_path: Path) -> None:
        bootstrap(db_path)
        bootstrap(db_path)

        assert ReviewRepository(db_path).count() == 0

    def test_legacy_db_gets_the_new_tables(self, tmp_path: Path) -> None:
        """검토 테이블이 없던 DB 도 bootstrap 한 번으로 준비된다."""
        legacy = tmp_path / "legacy.db"
        PurchaseRepository(legacy).create_table()

        bootstrap(legacy)

        with sqlite3.connect(str(legacy)) as conn:
            names = {
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        assert "purchase_review" in names


class TestDb3IsNotBuiltYet:
    """⛔ DB-3 는 아직 만들지 않았다 (지시 9번)."""

    def test_no_final_dataset_module(self) -> None:
        from pathlib import Path as FilePath

        root = FilePath(__file__).resolve().parents[1] / "src" / "procurement"
        names = {path.stem for path in root.rglob("*.py")}

        assert "final_dataset_repository" not in names
        assert "final_dataset_builder" not in names

    def test_no_final_dataset_table(self, db_path: Path) -> None:
        with sqlite3.connect(str(db_path)) as conn:
            names = {
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }

        assert "final_dataset" not in names
        assert "final_purchase" not in names


class TestReviewScreenHasNoBusinessLogic:
    """⛔ 화면에 업무 판정을 두지 않는다 (UI 프레임워크 독립성)."""

    def _html(self) -> str:
        from pathlib import Path as FilePath

        path = (
            FilePath(__file__).resolve().parents[1]
            / "src"
            / "procurement"
            / "web"
            / "static"
            / "index.html"
        )
        return path.read_text(encoding="utf-8")

    def test_options_come_from_the_backend(self) -> None:
        """선택지 목록을 화면이 만들지 않는다."""
        html = self._html()

        assert "/reviews/options" in html
        assert "CONSTRUCTION" not in html, "유형 코드를 화면에 박아 두지 않는다"

    def test_no_threshold_in_the_screen(self) -> None:
        """⛔ 이중 매칭 판정을 화면에서 하지 않는다."""
        html = self._html()

        assert "is_ambiguous" in html, "백엔드가 준 값을 그대로 쓴다"
        assert "0.9" not in html
        assert "> 0.5" not in html

    def test_screen_uses_the_review_api(self) -> None:
        html = self._html()

        assert '"/reviews?' in html or "/reviews?review_filter=" in html
        assert "/reopen" in html

    def test_screen_states_the_original_is_untouched(self) -> None:
        """담당자가 화면에서 그 사실을 알 수 있어야 한다."""
        html = self._html()

        assert "원본 데이터는 수정되지 않습니다" in html
