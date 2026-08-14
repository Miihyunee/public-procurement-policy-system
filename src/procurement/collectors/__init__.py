"""
procurement.collectors

외부 인증 API 응답을 시스템이 쓰는 형태로 변환하는 계층입니다.

계층은 아래와 같이 분리되어 있습니다::

    Transport → ApiClient → Parser → Certification

파싱만 필요하면 파서를 직접 쓸 수 있습니다::

    from procurement.collectors import parse_cert_list

    records = parse_cert_list(xml_text, business_no="4021497692")

파싱과 네트워크를 분리해 두면, 실제 호출 없이 명세서의 응답 샘플만으로
검증할 수 있습니다.

.. warning::
    ``stdrDate``(기준일자)는 호출자가 **명시적으로** 전달해야 합니다. 코드가
    오늘 날짜·연도 말일·지급일·계약일 중 어느 것도 임의로 고르지 않습니다.
    업무 결정 전이기 때문입니다(D-24 관련).

.. note::
    각 파서는 해당 API 의 공식 명세서(공공데이터 오픈API 활용가이드 /
    서비스설계서)를 근거로 작성했습니다. 명세에 없는 필드나 형식을 추측해서
    받아들이지 않으며, 알 수 없는 형태는 오류로 처리합니다.
"""

from procurement.collectors.client import (
    SOURCE_DISABLED,
    SOURCE_POLICY_CODES,
    SOURCE_STARTUP_KISED,
    SOURCE_STARTUP_SMPP,
    SOURCE_WOMAN,
    ApiKeyNotConfiguredError,
    CertificationApiClient,
    FetchResult,
)
from procurement.collectors.dates import parse_day, parse_range
from procurement.collectors.errors import (
    ApiAuthError,
    ApiNetworkError,
    ApiQuotaError,
    ApiRequestError,
    ApiServerError,
    ApiTimeoutError,
    ApiTransportError,
    StdrDateRequiredError,
)
from procurement.collectors.kised import (
    parse_corporate_information_json,
    parse_corporate_information_xml,
)
from procurement.collectors.models import (
    ApiParseError,
    ApiResponseError,
    CertificationRecord,
)
from procurement.collectors.smpp import parse_cert_list, parse_startup_cert
from procurement.collectors.sync_service import (
    SKIP_COMPANY_NOT_FOUND,
    CertificationSyncService,
    PolicyNotRegisteredError,
    SyncResult,
)
from procurement.collectors.transport import HttpResponse, Transport, UrllibTransport

__all__ = [
    "SKIP_COMPANY_NOT_FOUND",
    "SOURCE_DISABLED",
    "SOURCE_POLICY_CODES",
    "SOURCE_STARTUP_KISED",
    "SOURCE_STARTUP_SMPP",
    "SOURCE_WOMAN",
    "ApiAuthError",
    "ApiKeyNotConfiguredError",
    "ApiNetworkError",
    "ApiParseError",
    "ApiQuotaError",
    "ApiRequestError",
    "ApiResponseError",
    "ApiServerError",
    "ApiTimeoutError",
    "ApiTransportError",
    "CertificationApiClient",
    "CertificationRecord",
    "CertificationSyncService",
    "FetchResult",
    "HttpResponse",
    "PolicyNotRegisteredError",
    "StdrDateRequiredError",
    "SyncResult",
    "Transport",
    "UrllibTransport",
    "parse_cert_list",
    "parse_corporate_information_json",
    "parse_corporate_information_xml",
    "parse_day",
    "parse_range",
    "parse_startup_cert",
]
