"""
tests.test_company_source_file_and_api

**기업정보 확인 방식 2종**(파일 업로드 · 조회)이 같은 결과를 만드는지 검증합니다
(STEP 91 §20 · §22).

증명하려는 것은 하나입니다 — **가져오는 곳만 다르고, 그 뒤는 전부 같다.**

::

    파일 업로드 ─┐
                 ├→ CompanyRecord → CompanyImporter → Company / Certification
    조회        ─┘                                            ↓
                                            기존 매칭 · 기존 판정 · 기존 계산

.. warning::
    ⛔ **합성 데이터만 사용합니다.** 실제 사업자등록번호·거래처명을 쓰지
    않습니다. 사업자등록번호는 체크섬을 만족하는 형식값입니다.

.. warning::
    ⛔ **실제 API 를 호출하지 않습니다.** 조회 방식은 응답 모양만 흉내 낸
    가짜 클라이언트로 검증합니다 — 키도, 네트워크도 쓰지 않습니다.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from procurement.app import build_company_source_service, create_app
from procurement.calculators import ProcurementAchievementCalculator
from procurement.collectors.client import (
    SOURCE_STARTUP_KISED,
    SOURCE_WOMAN,
    FetchResult,
)
from procurement.collectors.models import CertificationRecord
from procurement.core.period import RESOLUTION_DATE, PeriodFilter
from procurement.database.bootstrap import bootstrap
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.database.purchase_repository import PurchaseRepository
from procurement.importers.company_importer import (
    SOURCE_API,
    SOURCE_FILE,
    CompanyImportStatus,
)
from procurement.matchers.company_matcher import CompanyMatcher
from procurement.models.purchase import Purchase
from procurement.uploads.company_format import company_header_row
from procurement.uploads.company_source_service import API_MISSING_COMPANY_FIELDS
from procurement.uploads.format import header_row

VALIDATE_URL = "/companies/upload/validate"
UPLOAD_URL = "/companies/upload"
SYNC_URL = "/companies/sync"
SOURCES_URL = "/companies/sources"

#: 합성 사업자등록번호(체크섬 만족).
BUSINESS_NO = "220-81-62517"
NORMALIZED = "2208162517"
OTHER_BUSINESS_NO = "104-81-24017"

#: 정상 행 한 건 — 기업 + 중소기업 인증.
GOOD_ROW: list[object] = [
    BUSINESS_NO,
    "가나산업",
    "홍길동",
    "SMALL_BUSINESS",
    date(2026, 1, 1),
    date(2026, 12, 31),
]


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """정책 seed 까지 끝난 빈 DB."""
    path = tmp_path / "company.db"
    bootstrap(path)
    return path


@pytest.fixture
def client(db_path: Path) -> TestClient:
    """격리 DB 를 쓰는 API 클라이언트."""
    return TestClient(create_app(db_path=db_path))


def _excel(path: Path, rows: list[list[object]], headers: list[str] | None = None) -> Path:
    """기업정보 표준 머리글 + 주어진 행으로 엑셀을 만듭니다."""
    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    sheet.append(headers if headers is not None else list(company_header_row()))
    for row in rows:
        sheet.append(row)
    workbook.save(path)
    workbook.close()
    return path


class _FakeApiClient:
    """조회 응답 모양만 흉내 내는 가짜 클라이언트.

    ⛔ 실제 API 를 호출하지 않습니다. 어떤 값을 주느냐에 따라 기업을 만들 수
    있는지/없는지가 갈리는지를 보기 위한 fixture 입니다.
    """

    def __init__(self, records: list[CertificationRecord], *, policy_code: str) -> None:
        self._records = records
        self._policy_code = policy_code
        self.calls: list[tuple[str, str, date | None]] = []

    def fetch(self, source: str, business_no: str, *, stdr_date: date | None = None) -> FetchResult:
        self.calls.append((source, business_no, stdr_date))
        return FetchResult(
            source=source,
            policy_code=self._policy_code,
            business_no=business_no,
            records=tuple(record for record in self._records if record.business_no == business_no),
            attempts=1,
        )


def _api_record(
    business_no: str = NORMALIZED,
    *,
    company_name: str | None = "가나산업",
    representative_name: str | None = "홍길동",
) -> CertificationRecord:
    """조회가 준 확인서 한 건(합성)."""
    return CertificationRecord(
        business_no=business_no,
        valid_from=date(2026, 1, 1),
        valid_to=date(2026, 12, 31),
        company_name=company_name,
        representative_name=representative_name,
    )


# ======================================================================
# §20 ① ~ ⑥  파일 방식 — 검증
# ======================================================================
class TestFileValidation:
    """① ~ ⑤ 파일 방식의 정상·오류 처리."""

    def test_valid_file_is_stored(self, client: TestClient, db_path: Path, tmp_path: Path) -> None:
        """① 정상 파일이 적재된다."""
        path = _excel(tmp_path / "ok.xlsx", [GOOD_ROW])

        body = client.post(UPLOAD_URL, json={"file_path": str(path)}).json()

        assert body["stored"] is True
        assert body["source"] == SOURCE_FILE
        assert body["total"] == 1
        assert body["created"] == 1
        assert body["certifications"] == 1
        assert CompanyRepository(db_path).find_by_business_no(NORMALIZED) is not None

    def test_missing_header_is_rejected(self, client: TestClient, tmp_path: Path) -> None:
        """② 필수 컬럼이 없으면 저장하지 않는다."""
        headers = [header for header in company_header_row() if header != "대표자명"]
        path = _excel(tmp_path / "no-header.xlsx", [], headers=list(headers))

        body = client.post(UPLOAD_URL, json={"file_path": str(path)}).json()

        assert body["stored"] is False
        assert any("대표자명" in line for line in body["file_errors"])

    def test_missing_business_no_is_rejected(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """③ 사업자등록번호가 없는 행은 저장하지 않는다."""
        row: list[object] = [
            None,
            "가나산업",
            "홍길동",
            "SMALL_BUSINESS",
            date(2026, 1, 1),
            date(2026, 12, 31),
        ]
        path = _excel(tmp_path / "no-bizno.xlsx", [row])

        body = client.post(UPLOAD_URL, json={"file_path": str(path)}).json()

        assert body["stored"] is False
        assert body["issues"] != []
        assert CompanyRepository(db_path).count() == 0

    def test_a_missing_representative_is_stored_empty(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """④ 대표자명이 없어도 저장한다 — 그 칸만 비운다.

        .. note::
            분류 A · PM 확정 반영 (2026-09-05): 기업을 식별하는 값은 기업명과
            사업자등록번호 둘이며, 대표자명은 선택값이다. 이 시험은 그전까지
            *"대표자명이 없는 행은 저장하지 않는다"* 였다.

            실제 사회적기업 자료 6,128행 중 1,491행에 대표자명이 없었고, 그
            때문에 등록되지 못한 거래처가 달성/미달 판정을 뒤집었다.

        .. warning::
            ⛔ 기업명·사업자등록번호로 **대신 채우지 않는다.** 지어내지도
            않는다 — 빈 값은 ``None`` 으로 남는다.
        """
        row: list[object] = [
            BUSINESS_NO,
            "가나산업",
            None,
            "SMALL_BUSINESS",
            date(2026, 1, 1),
            date(2026, 12, 31),
        ]
        path = _excel(tmp_path / "no-rep.xlsx", [row])

        body = client.post(UPLOAD_URL, json={"file_path": str(path)}).json()

        assert body["stored"] is True
        assert body["created"] == 1
        stored = CompanyRepository(db_path).find_by_business_no(BUSINESS_NO)
        assert stored is not None
        assert stored.company_name == "가나산업"
        assert stored.representative_name is None

    def test_bad_date_is_rejected(self, client: TestClient, db_path: Path, tmp_path: Path) -> None:
        """⑤ 날짜 형식이 잘못되면 저장하지 않는다."""
        row: list[object] = [
            BUSINESS_NO,
            "가나산업",
            "홍길동",
            "SMALL_BUSINESS",
            "2026.13.45",
            date(2026, 12, 31),
        ]
        path = _excel(tmp_path / "bad-date.xlsx", [row])

        body = client.post(UPLOAD_URL, json={"file_path": str(path)}).json()

        assert body["stored"] is False
        assert CompanyRepository(db_path).count() == 0

    def test_validate_only_never_stores(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """검증만 요청하면 정상 파일이라도 저장하지 않는다."""
        path = _excel(tmp_path / "ok.xlsx", [GOOD_ROW])

        body = client.post(VALIDATE_URL, json={"file_path": str(path)}).json()

        assert body["stored"] is False
        assert CompanyRepository(db_path).count() == 0

    def test_duplicate_business_no_keeps_the_first(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """⑥ 같은 사업자등록번호가 두 번 오면 **덮어쓰지 않는다.**"""
        second: list[object] = [
            BUSINESS_NO,
            "다른이름산업",
            "김철수",
            "SMALL_BUSINESS",
            date(2026, 1, 1),
            date(2026, 12, 31),
        ]
        path = _excel(tmp_path / "dup.xlsx", [GOOD_ROW, second])

        body = client.post(UPLOAD_URL, json={"file_path": str(path)}).json()

        assert body["created"] == 1
        assert body["already_exists"] == 1
        company = CompanyRepository(db_path).find_by_business_no(NORMALIZED)
        assert company is not None
        assert company.company_name == "가나산업"  # 먼저 들어온 값 그대로


# ======================================================================
# §20 ⑦ ~ ⑨  두 방식이 같은 자리에 들어간다
# ======================================================================
class TestBothSourcesLandInTheSamePlace:
    """⑦ ⑧ ⑨ 파일과 조회가 같은 구조를 만든다."""

    def test_file_creates_company(self, client: TestClient, db_path: Path, tmp_path: Path) -> None:
        """⑦ 파일 방식이 ``Company`` 를 만든다."""
        client.post(UPLOAD_URL, json={"file_path": str(_excel(tmp_path / "f.xlsx", [GOOD_ROW]))})

        company = CompanyRepository(db_path).find_by_business_no(NORMALIZED)
        assert company is not None
        assert company.company_name == "가나산업"
        assert company.representative_name == "홍길동"

    def test_api_creates_company(self, db_path: Path) -> None:
        """⑧ 조회 방식도 같은 ``Company`` 를 만든다(가짜 응답)."""
        service = build_company_source_service(
            db_path, _FakeApiClient([_api_record()], policy_code="WOMAN")
        )

        report = service.import_from_api(SOURCE_WOMAN, [NORMALIZED], stdr_date=date(2026, 6, 1))

        assert report.source == SOURCE_API
        assert report.created_count == 1
        company = CompanyRepository(db_path).find_by_business_no(NORMALIZED)
        assert company is not None
        assert company.company_name == "가나산업"
        assert company.representative_name == "홍길동"

    def test_file_and_api_produce_the_same_company_shape(self, tmp_path: Path) -> None:
        """⑨ 두 방식이 만든 기업이 **같은 모양**이다.

        ⛔ 방식별로 다른 저장 구조를 만들지 않는다.
        """
        file_db = tmp_path / "file.db"
        api_db = tmp_path / "api.db"
        bootstrap(file_db)
        bootstrap(api_db)

        TestClient(create_app(db_path=file_db)).post(
            UPLOAD_URL, json={"file_path": str(_excel(tmp_path / "f.xlsx", [GOOD_ROW]))}
        )
        build_company_source_service(
            api_db, _FakeApiClient([_api_record()], policy_code="WOMAN")
        ).import_from_api(SOURCE_WOMAN, [NORMALIZED], stdr_date=date(2026, 6, 1))

        from_file = CompanyRepository(file_db).find_by_business_no(NORMALIZED)
        from_api = CompanyRepository(api_db).find_by_business_no(NORMALIZED)
        assert from_file is not None
        assert from_api is not None
        assert from_file.business_no == from_api.business_no
        assert from_file.company_name == from_api.company_name
        assert from_file.representative_name == from_api.representative_name

    def test_api_without_company_fields_creates_nothing(self, db_path: Path) -> None:
        """조회가 기업명·대표자명을 주지 않으면 **만들지 않고 사유를 남긴다.**

        ⛔ 없는 값을 지어내지 않는다.
        """
        service = build_company_source_service(
            db_path,
            _FakeApiClient(
                [_api_record(company_name=None, representative_name=None)],
                policy_code="WOMAN",
            ),
        )

        report = service.import_from_api(SOURCE_WOMAN, [NORMALIZED], stdr_date=date(2026, 6, 1))

        assert report.created_count == 0
        assert report.failed_count == 1
        assert any(API_MISSING_COMPANY_FIELDS in row.messages for row in report.failed_rows())
        assert CompanyRepository(db_path).count() == 0


# ======================================================================
# §20 ⑩ ~ ⑫  매칭 · 연결
# ======================================================================
class TestMatchingIsUnchanged:
    """⑩ ⑪ ⑫ 저장 뒤 매칭은 **기존 기능** 을 그대로 쓴다."""

    def _purchase(self, db_path: Path, business_no: str = NORMALIZED) -> None:
        PurchaseRepository(db_path).insert(
            Purchase(
                business_no=business_no,
                company_name="가나산업",
                amount=Decimal("1000000"),
                resolution_date=date(2026, 3, 15),
            )
        )

    def test_company_from_file_matches_purchases(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """⑩ 파일로 넣은 기업이 기존 구매와 매칭된다."""
        self._purchase(db_path)
        client.post(UPLOAD_URL, json={"file_path": str(_excel(tmp_path / "f.xlsx", [GOOD_ROW]))})

        CompanyMatcher(CompanyRepository(db_path), PurchaseRepository(db_path)).match_all()

        purchase = PurchaseRepository(db_path).find_all()[0]
        assert purchase.company_id is not None

    def test_rematch_endpoint_is_reused(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """⑪ 재매칭은 **기존** ``POST /purchases/rematch`` 로 한다.

        ⛔ 기업정보 적재용 매칭 기능을 새로 만들지 않았다.
        """
        self._purchase(db_path)
        client.post(UPLOAD_URL, json={"file_path": str(_excel(tmp_path / "f.xlsx", [GOOD_ROW]))})

        response = client.post("/purchases/rematch")

        assert response.status_code == 200
        assert PurchaseRepository(db_path).find_all()[0].company_id is not None

    def test_certification_needs_a_company_first(self, db_path: Path) -> None:
        """⑫ 기업이 만들어지지 않으면 인증도 연결되지 않는다.

        .. note::
            분류 A · PM 확정 반영 (2026-09-05). 이 시험은 «기업을 만들 수 없는
            조회 결과» 로 **대표자명 없음**을 썼는데, 대표자명이 선택값이 되어
            더 이상 기업을 못 만드는 사유가 아니다. 지키려던 것 — *기업이
            없으면 인증도 붙지 않는다* — 은 그대로 두고, 사유만 여전히 필수인
            **기업명 없음**으로 바꾼다.
        """
        service = build_company_source_service(
            db_path,
            _FakeApiClient([_api_record(company_name=None)], policy_code="WOMAN"),
        )

        service.import_from_api(SOURCE_WOMAN, [NORMALIZED], stdr_date=date(2026, 6, 1))

        assert CompanyRepository(db_path).count() == 0
        assert CertificationRepository(db_path).count() == 0

    def test_a_company_without_a_representative_is_still_created(self, db_path: Path) -> None:
        """③ 대표자명이 없어도 기업이 만들어진다 — 그 칸만 비어 있다.

        분류 A · PM 확정 반영 (2026-09-05). 대표자명 없는 조회 결과는 예전에
        기업 자체를 만들지 못했다.
        """
        service = build_company_source_service(
            db_path,
            _FakeApiClient([_api_record(representative_name=None)], policy_code="WOMAN"),
        )

        service.import_from_api(SOURCE_WOMAN, [NORMALIZED], stdr_date=date(2026, 6, 1))

        company = CompanyRepository(db_path).find_by_business_no(NORMALIZED)
        assert company is not None
        assert company.company_name == "가나산업"
        assert company.representative_name is None


# ======================================================================
# §20 ⑬ ~ ⑯  판정은 기존 규칙 그대로
# ======================================================================
class TestJudgementRulesAreUnchanged:
    """⑬ ⑭ ⑮ ⑯ 적재 방법이 판정 규칙을 바꾸지 않는다."""

    def _purchase(
        self,
        db_path: Path,
        *,
        resolution_date: date,
        contract_date: date | None = None,
        issue_date: date | None = None,
    ) -> None:
        PurchaseRepository(db_path).insert(
            Purchase(
                business_no=NORMALIZED,
                company_name="가나산업",
                amount=Decimal("1000000"),
                resolution_date=resolution_date,
                contract_date=contract_date,
                issue_date=issue_date,
            )
        )

    def _calculator(self, db_path: Path) -> ProcurementAchievementCalculator:
        return ProcurementAchievementCalculator(
            PurchaseRepository(db_path),
            CertificationRepository(db_path),
            PolicyRepository(db_path),
        )

    def _policy_id(self, db_path: Path, policy_code: str) -> int:
        policy = PolicyRepository(db_path).find_by_policy_code(policy_code)
        assert policy is not None
        assert policy.policy_id is not None
        return policy.policy_id

    def test_resolution_date_decides_small_business(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """⑬ 중소기업 판정은 **결의일자** 로 한다."""
        self._purchase(db_path, resolution_date=date(2026, 3, 15))
        client.post(UPLOAD_URL, json={"file_path": str(_excel(tmp_path / "f.xlsx", [GOOD_ROW]))})
        client.post("/purchases/rematch")

        period = PeriodFilter.for_year(2026, RESOLUTION_DATE)
        policy_id = self._policy_id(db_path, "SMALL_BUSINESS")
        assert self._calculator(db_path).calculate_policy_purchase(policy_id, period) == Decimal(
            "1000000"
        )

    def test_startup_accepts_resolution_or_contract_date(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """⑭ 창업기업은 결의일자·계약일자 중 **하나만** 인증기간에 들어도 인정한다."""
        # 결의일자는 인증기간 밖(2026-03-15 → 인증은 2026-06-01~12-31),
        # 계약일자는 인증기간 안이다.
        self._purchase(db_path, resolution_date=date(2026, 3, 15), contract_date=date(2026, 7, 1))
        row: list[object] = [
            BUSINESS_NO,
            "가나산업",
            "홍길동",
            "STARTUP",
            date(2026, 6, 1),
            date(2026, 12, 31),
        ]
        client.post(UPLOAD_URL, json={"file_path": str(_excel(tmp_path / "s.xlsx", [row]))})
        client.post("/purchases/rematch")

        period = PeriodFilter.for_year(2026, RESOLUTION_DATE)
        policy_id = self._policy_id(db_path, "STARTUP")
        assert self._calculator(db_path).calculate_policy_purchase(policy_id, period) == Decimal(
            "1000000"
        )

    def test_issue_date_is_never_used(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """⑮ 신고기준일(``issue_date``)은 판정에 쓰이지 않는다.

        신고기준일만 인증기간 안에 있고 결의일자는 밖이면 **인정되지 않는다.**
        """
        self._purchase(db_path, resolution_date=date(2026, 3, 15), issue_date=date(2026, 7, 1))
        row: list[object] = [
            BUSINESS_NO,
            "가나산업",
            "홍길동",
            "SMALL_BUSINESS",
            date(2026, 6, 1),
            date(2026, 12, 31),
        ]
        client.post(UPLOAD_URL, json={"file_path": str(_excel(tmp_path / "i.xlsx", [row]))})
        client.post("/purchases/rematch")

        period = PeriodFilter.for_year(2026, RESOLUTION_DATE)
        policy_id = self._policy_id(db_path, "SMALL_BUSINESS")
        calculated = self._calculator(db_path).calculate_policy_purchase(policy_id, period)
        assert calculated == Decimal("0")

    def test_target_rate_is_still_unset(self, db_path: Path) -> None:
        """⑯ 기업정보를 넣어도 목표율이 생기지 않는다.

        ⛔ 목표율은 고객이 정한다. 코드가 기본값을 채우지 않는다.
        """
        for policy_code in ("SMALL_BUSINESS", "WOMAN", "DISABLED", "STARTUP"):
            policy = PolicyRepository(db_path).find_by_policy_code(policy_code)
            assert policy is not None
            assert policy.target_rate is None


# ======================================================================
# §22  중복 · 갱신
# ======================================================================
class TestReimportIsSafe:
    """같은 파일을 다시 올려도 깨지지 않는다."""

    def test_reimport_does_not_fail(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """① 중복 INSERT 로 실패하지 않는다."""
        path = _excel(tmp_path / "f.xlsx", [GOOD_ROW])

        client.post(UPLOAD_URL, json={"file_path": str(path)})
        body = client.post(UPLOAD_URL, json={"file_path": str(path)}).json()

        assert body["stored"] is True
        assert body["failed"] == 0
        assert body["already_exists"] == 1
        assert CompanyRepository(db_path).count() == 1

    def test_reimport_keeps_purchase_links(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """② 다시 올려도 기존 구매 연결이 유지된다."""
        PurchaseRepository(db_path).insert(
            Purchase(
                business_no=NORMALIZED,
                company_name="가나산업",
                amount=Decimal("1000000"),
                resolution_date=date(2026, 3, 15),
            )
        )
        path = _excel(tmp_path / "f.xlsx", [GOOD_ROW])
        client.post(UPLOAD_URL, json={"file_path": str(path)})
        client.post("/purchases/rematch")
        linked = PurchaseRepository(db_path).find_all()[0].company_id

        client.post(UPLOAD_URL, json={"file_path": str(path)})

        assert PurchaseRepository(db_path).find_all()[0].company_id == linked

    def test_reimport_does_not_duplicate_certifications(
        self, client: TestClient, db_path: Path, tmp_path: Path
    ) -> None:
        """③ 기존 인증이 지워지거나 중복되지 않는다."""
        path = _excel(tmp_path / "f.xlsx", [GOOD_ROW])

        client.post(UPLOAD_URL, json={"file_path": str(path)})
        second = client.post(UPLOAD_URL, json={"file_path": str(path)}).json()

        assert second["certifications"] == 0  # 다시 넣지 않았다
        assert CertificationRepository(db_path).count() == 1


# ======================================================================
# 확인 방식 목록
# ======================================================================
class TestSourceListing:
    """화면이 목록을 들고 있지 않다 — 서버가 준다."""

    def test_both_methods_are_offered(self, client: TestClient) -> None:
        """파일·조회 **두 방법 모두** 제공된다."""
        body = client.get(SOURCES_URL).json()

        assert body["methods"] == [SOURCE_FILE, SOURCE_API]

    def test_api_sources_come_from_existing_constants(self, client: TestClient) -> None:
        """조회 출처는 **기존 상수** 그대로다. ⛔ 새로 만들지 않았다."""
        body = client.get(SOURCES_URL).json()

        sources = {item["source"] for item in body["api_sources"]}
        assert SOURCE_WOMAN in sources
        assert SOURCE_STARTUP_KISED in sources

    def test_policy_name_is_never_invented(self, client: TestClient) -> None:
        """정책 이름은 **등록된 값** 만 쓴다."""
        body = client.get(SOURCES_URL).json()

        by_code = {item["policy_code"]: item["policy_name"] for item in body["api_sources"]}
        assert by_code["WOMAN"] == "여성기업"
        assert by_code["DISABLED"] == "장애인기업"


class TestApiCallShape:
    """조회 호출은 기존 클라이언트를 그대로 쓴다."""

    def test_stdr_date_is_passed_through(self, db_path: Path) -> None:
        """⛔ 기준일자를 코드가 임의로 채우지 않는다 — 호출자 값이 그대로 간다."""
        fake = _FakeApiClient([_api_record()], policy_code="WOMAN")
        service = build_company_source_service(db_path, fake)

        service.import_from_api(SOURCE_WOMAN, [NORMALIZED], stdr_date=date(2026, 6, 1))

        assert fake.calls == [(SOURCE_WOMAN, NORMALIZED, date(2026, 6, 1))]

    def test_sync_without_api_client_is_rejected(self, db_path: Path) -> None:
        """조회 클라이언트가 없으면 조용히 넘어가지 않고 알린다."""
        service = build_company_source_service(db_path)

        with pytest.raises(RuntimeError):
            service.import_from_api(SOURCE_WOMAN, [NORMALIZED], stdr_date=date(2026, 6, 1))


class TestStatusValues:
    """행별 결과 상태값이 기대한 세 가지뿐이다."""

    def test_statuses_are_exactly_three(self) -> None:
        assert {status.value for status in CompanyImportStatus} == {
            "CREATED",
            "ALREADY_EXISTS",
            "FAILED",
        }


class TestUserCanChooseTheMethod:
    """§12 — 화면에서 **사용자가** 확인 방식을 고른다."""

    @pytest.fixture(scope="class")
    def index_html(self) -> str:
        from procurement.web.page import read_index_html

        return read_index_html()

    def test_both_choices_are_on_the_screen(self, index_html: str) -> None:
        """파일 업로드·조회 두 선택지가 모두 있다."""
        assert 'id="company-source-file"' in index_html
        assert 'id="company-source-api"' in index_html

    def test_file_panel_and_api_panel_both_exist(self, index_html: str) -> None:
        assert 'id="company-file-panel"' in index_html
        assert 'id="company-api-panel"' in index_html

    def test_screen_calls_the_shared_endpoints(self, index_html: str) -> None:
        """⛔ 화면이 방식별로 다른 저장 경로를 만들지 않는다."""
        assert '"/companies/upload/validate"' in index_html
        assert '"/companies/upload"' in index_html
        assert '"/companies/sync"' in index_html

    def test_screen_does_not_hold_the_source_list(self, index_html: str) -> None:
        """조회 출처 목록은 서버가 준다 — 화면에 하드코딩하지 않는다."""
        assert '"/companies/sources"' in index_html
        assert SOURCE_WOMAN not in index_html
        assert SOURCE_STARTUP_KISED not in index_html

    def test_screen_never_fills_in_the_reference_date(self, index_html: str) -> None:
        """⛔ 기준일자를 화면이 오늘 날짜 등으로 채우지 않는다."""
        assert "조회 기준일자를 입력해 주세요." in index_html
        # 기준일자 칸에 값을 **써 넣는** 코드가 없다. 읽기만 한다.
        assert 'el("company-api-date").value =' not in index_html
        assert 'el("company-api-date").value' in index_html


def test_purchase_upload_format_is_untouched() -> None:
    """⛔ 기업정보 양식을 만들면서 **구매 표준 양식을 바꾸지 않았다.**"""
    assert "사업자등록번호" in header_row()
    assert "인증종류" not in header_row()
    assert OTHER_BUSINESS_NO not in header_row()
