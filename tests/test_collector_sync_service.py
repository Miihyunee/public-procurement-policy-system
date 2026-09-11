"""
tests.test_collector_sync_service

조회 결과를 기존 ``Certification`` 구조에 연결하는 계층 검증.

.. warning::
    **실제 외부 API 서버에 접속하지 않습니다.** 응답은 명세서 샘플 구조를 따르는
    고정 문자열이며 대역(stub)이 돌려줍니다.

이 파일이 고정하는 것은 "가져온 것을 기존 자리에 넣는다" 뿐입니다. 인증 판정
기준·유효기간 해석·달성률 계산은 이 계층의 관심사가 아니며 검증 대상도 아닙니다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from pathlib import Path

import pytest

from procurement.collectors.client import (
    SOURCE_STARTUP_KISED,
    SOURCE_WOMAN,
    CertificationApiClient,
)
from procurement.collectors.errors import StdrDateRequiredError
from procurement.collectors.sync_service import (
    SKIP_COMPANY_NOT_FOUND,
    CertificationSyncService,
    PolicyNotRegisteredError,
)
from procurement.collectors.transport import HttpResponse
from procurement.database.certification_repository import CertificationRepository
from procurement.database.company_repository import CompanyRepository
from procurement.database.policy_repository import PolicyRepository
from procurement.models import Company, Policy

BUSINESS_NO = "4021497692"
STDR_DATE = date(2026, 8, 14)

WOMAN_OK = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header>
  <body><items><item>
    <certSeCode>03</certSeCode>
    <issuInstt>한국여성경제인협회</issuInstt>
    <validPdBeginDe>20240401</validPdBeginDe>
    <validPdEndDe>20270331</validPdEndDe>
  </item></items></body>
</response>
"""

WOMAN_TWO_CERTS = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header>
  <body><items>
    <item>
      <certSeCode>03</certSeCode>
      <validPdBeginDe>20210401</validPdBeginDe>
      <validPdEndDe>20240331</validPdEndDe>
    </item>
    <item>
      <certSeCode>03</certSeCode>
      <validPdBeginDe>20240401</validPdBeginDe>
      <validPdEndDe>20270331</validPdEndDe>
    </item>
  </items></body>
</response>
"""

NO_DATA = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header><resultCode>03</resultCode><resultMsg>NODATA_ERROR</resultMsg></header>
</response>
"""

KISED_OK = """
{"data": [{"brno": "4021497692", "ntrp_nm": "테스트기업", "repr_nm": "홍길동",
"confmdoc_isu_no": "2024-0001", "confmdoc_isu_dt": "2024-04-01",
"confmdoc_expr_dt": "2027-03-31"}]}
"""


class StubTransport:
    """준비된 응답을 순서대로 돌려주는 전송 대역."""

    def __init__(self, *bodies: str) -> None:
        """대역을 초기화합니다."""
        self._bodies = list(bodies)
        self.call_count = 0

    def get(
        self,
        url: str,
        params: Mapping[str, str],
        *,
        timeout: float,
    ) -> HttpResponse:
        """다음 응답을 반환합니다."""
        del url, params, timeout
        self.call_count += 1
        return HttpResponse(status=200, body=self._bodies.pop(0))


class Fixture:
    """저장소·서비스 묶음."""

    def __init__(self, tmp_path: Path, *bodies: str) -> None:
        """DB 와 서비스를 준비합니다."""
        db = tmp_path / "sync.db"
        self.companies = CompanyRepository(db)
        self.policies = PolicyRepository(db)
        self.certifications = CertificationRepository(db)
        self.companies.create_table()
        self.policies.create_table()
        self.certifications.create_table()
        self.transport = StubTransport(*bodies)
        self.service = CertificationSyncService(
            client=CertificationApiClient(
                smpp_api_key="test-smpp-key",
                startup_api_key="test-startup-key",
                transport=self.transport,
            ),
            company_repository=self.companies,
            policy_repository=self.policies,
            certification_repository=self.certifications,
        )

    def add_policy(self, code: str) -> int:
        """정책을 등록하고 ``policy_id`` 를 반환합니다."""
        policy = self.policies.insert(
            Policy(policy_code=code, policy_name=code, is_active=True)
        )
        assert policy.policy_id is not None
        return policy.policy_id

    def add_company(self, business_no: str = BUSINESS_NO) -> int:
        """기업을 등록하고 ``company_id`` 를 반환합니다."""
        company = self.companies.insert(
            Company(
                business_no=business_no,
                company_name="테스트기업",
                representative_name="홍길동",
            )
        )
        assert company.company_id is not None
        return company.company_id


# ---------------------------------------------------------------------------
# 1. 저장까지 연결되는가
# ---------------------------------------------------------------------------


def test_saves_certification_for_registered_company(tmp_path: Path) -> None:
    """조회 결과가 기존 Certification 테이블에 저장된다."""
    fx = Fixture(tmp_path, WOMAN_OK)
    policy_id = fx.add_policy("WOMAN")
    company_id = fx.add_company()

    result = fx.service.sync_one(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)

    assert result.fetched == 1
    assert result.saved == 1
    assert result.skip_reason is None

    saved = fx.certifications.find_by_company(company_id)
    assert len(saved) == 1
    assert saved[0].policy_id == policy_id
    assert saved[0].valid_from == date(2024, 4, 1)
    assert saved[0].valid_to == date(2027, 3, 31)
    assert saved[0].issuing_agency == "한국여성경제인협회"


def test_saves_certificate_number_when_api_provides_it(tmp_path: Path) -> None:
    """발급번호를 주는 API 는 그 값도 함께 저장한다."""
    fx = Fixture(tmp_path, KISED_OK)
    fx.add_policy("STARTUP")
    company_id = fx.add_company()

    fx.service.sync_one(SOURCE_STARTUP_KISED, BUSINESS_NO, stdr_date=None)

    saved = fx.certifications.find_by_company(company_id)
    assert saved[0].certificate_number == "2024-0001"


def test_saves_multiple_certifications(tmp_path: Path) -> None:
    """확인서가 여러 건이면 모두 저장한다."""
    fx = Fixture(tmp_path, WOMAN_TWO_CERTS)
    fx.add_policy("WOMAN")
    company_id = fx.add_company()

    result = fx.service.sync_one(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)

    assert result.saved == 2
    assert len(fx.certifications.find_by_company(company_id)) == 2


# ---------------------------------------------------------------------------
# 2. 저장하지 않고 건너뛰는 경우
# ---------------------------------------------------------------------------


def test_unregistered_company_is_skipped_not_invented(tmp_path: Path) -> None:
    """기업이 없으면 지어내지 않고 건너뛴다.

    여성·장애인 확인 API 는 기업명·대표자명을 주지 않습니다. 없는 값을 코드가
    채우면 근거 없는 기업 정보가 DB 에 남습니다.
    """
    fx = Fixture(tmp_path, WOMAN_OK)
    fx.add_policy("WOMAN")

    result = fx.service.sync_one(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)

    assert result.fetched == 1
    assert result.saved == 0
    assert result.skip_reason == SKIP_COMPANY_NOT_FOUND
    assert fx.companies.count() == 0
    assert fx.certifications.count() == 0


def test_no_data_response_saves_nothing(tmp_path: Path) -> None:
    """유효한 확인서가 없으면 아무것도 저장하지 않는다(오류도 아니다)."""
    fx = Fixture(tmp_path, NO_DATA)
    fx.add_policy("WOMAN")
    fx.add_company()

    result = fx.service.sync_one(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)

    assert result.fetched == 0
    assert result.saved == 0
    assert result.skip_reason is None
    assert fx.certifications.count() == 0


def test_missing_policy_raises(tmp_path: Path) -> None:
    """대응 정책이 등록되어 있지 않으면 호출 전에 실패한다."""
    fx = Fixture(tmp_path, WOMAN_OK)

    with pytest.raises(PolicyNotRegisteredError):
        fx.service.sync_one(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)

    assert fx.transport.call_count == 0


def test_unknown_source_raises(tmp_path: Path) -> None:
    """정의되지 않은 출처는 거부한다."""
    fx = Fixture(tmp_path, WOMAN_OK)

    with pytest.raises(ValueError, match="조회 출처"):
        fx.service.sync_one("UNKNOWN", BUSINESS_NO, stdr_date=None)


# ---------------------------------------------------------------------------
# 3. 재실행 안전성
# ---------------------------------------------------------------------------


def test_rerun_does_not_duplicate(tmp_path: Path) -> None:
    """같은 인증을 두 번 저장하지 않는다."""
    fx = Fixture(tmp_path, WOMAN_OK, WOMAN_OK)
    fx.add_policy("WOMAN")
    company_id = fx.add_company()

    first = fx.service.sync_one(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)
    second = fx.service.sync_one(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)

    assert first.saved == 1
    assert second.saved == 0
    assert second.skipped_duplicate == 1
    assert len(fx.certifications.find_by_company(company_id)) == 1


def test_same_response_twice_in_one_call_is_deduplicated(tmp_path: Path) -> None:
    """한 응답 안에 같은 유효기간이 두 번 와도 한 건만 저장한다."""
    body = WOMAN_TWO_CERTS.replace("20210401", "20240401").replace("20240331", "20270331")
    fx = Fixture(tmp_path, body)
    fx.add_policy("WOMAN")
    company_id = fx.add_company()

    result = fx.service.sync_one(SOURCE_WOMAN, BUSINESS_NO, stdr_date=STDR_DATE)

    assert result.fetched == 2
    assert result.saved == 1
    assert result.skipped_duplicate == 1
    assert len(fx.certifications.find_by_company(company_id)) == 1


# ---------------------------------------------------------------------------
# 4. stdrDate 는 이 계층에서도 임의로 채우지 않는다
# ---------------------------------------------------------------------------


def test_sync_requires_explicit_stdr_date(tmp_path: Path) -> None:
    """기준일을 주지 않으면 호출하지 않는다."""
    fx = Fixture(tmp_path, WOMAN_OK)
    fx.add_policy("WOMAN")
    fx.add_company()

    with pytest.raises(StdrDateRequiredError):
        fx.service.sync_one(SOURCE_WOMAN, BUSINESS_NO, stdr_date=None)

    assert fx.transport.call_count == 0
    assert fx.certifications.count() == 0


def test_sync_has_no_stdr_date_default(tmp_path: Path) -> None:
    """``sync_one`` 은 ``stdr_date`` 기본값을 갖지 않는다."""
    fx = Fixture(tmp_path, WOMAN_OK)

    with pytest.raises(TypeError):
        fx.service.sync_one(SOURCE_WOMAN, BUSINESS_NO)  # type: ignore[call-arg]
