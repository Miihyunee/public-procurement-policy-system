"""
procurement.collectors

외부 인증 API 응답을 시스템이 쓰는 형태로 변환하는 계층입니다.

현재는 **응답 파싱만** 제공합니다. HTTP 호출·저장은 포함하지 않습니다::

    from procurement.collectors import parse_cert_list

    records = parse_cert_list(xml_text, business_no="4021497692")

파싱과 네트워크를 분리해 두면, 실제 호출 없이 명세서의 응답 샘플만으로
검증할 수 있습니다.

.. note::
    각 파서는 해당 API 의 공식 명세서(공공데이터 오픈API 활용가이드 /
    서비스설계서)를 근거로 작성했습니다. 명세에 없는 필드나 형식을 추측해서
    받아들이지 않으며, 알 수 없는 형태는 오류로 처리합니다.
"""

from procurement.collectors.dates import parse_day, parse_range
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

__all__ = [
    "ApiParseError",
    "ApiResponseError",
    "CertificationRecord",
    "parse_cert_list",
    "parse_corporate_information_json",
    "parse_corporate_information_xml",
    "parse_day",
    "parse_range",
    "parse_startup_cert",
]
