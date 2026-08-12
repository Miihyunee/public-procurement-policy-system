"""
procurement.collectors.models

외부 인증 API 응답을 담는 중간 DTO 를 정의합니다.

API 마다 응답 필드명·날짜 형식·제공 항목이 모두 다르므로, 각 파서가 이 공통
DTO 로 변환한 뒤 저장 계층으로 넘깁니다.

.. warning::
    이 DTO 는 **API 가 실제로 주는 것만** 담습니다. API 가 주지 않는 값을
    기본값으로 채우지 않습니다. 예를 들어 여성기업·장애인기업 확인 API 는
    **기업명과 대표자명을 제공하지 않으므로** 해당 필드가 ``None`` 입니다.
    ``Company`` 저장에 필요한 값을 어디서 채울지는 별도 결정 사항입니다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date


class ApiResponseError(RuntimeError):
    """외부 API 가 오류 코드를 반환했을 때 발생합니다.

    Attributes:
        code: API 가 반환한 결과 코드.
        message: API 가 반환한 결과 메시지.
    """

    def __init__(self, code: str, message: str) -> None:
        """오류를 초기화합니다."""
        super().__init__(f"API 오류 (code={code}): {message}")
        self.code = code
        self.message = message


class ApiParseError(ValueError):
    """응답 구조가 명세와 다를 때 발생합니다."""


@dataclass(frozen=True, kw_only=True)
class CertificationRecord:
    """인증 확인서 한 건(파싱 결과).

    Attributes:
        business_no: 사업자등록번호(하이픈 없는 형태). API 가 응답에 담지 않는
            경우(여성·장애인 확인)에는 **요청에 사용한 값**을 그대로 넣습니다.
        valid_from: 확인서 유효기간 시작일.
        valid_to: 확인서 유효기간 만료일.
        cert_code: API 가 준 확인서 구분 코드. 없으면 ``None``.
        certificate_number: 확인서 발급번호. 제공하지 않으면 ``None``.
        issuing_agency: 인증기관. 제공하지 않으면 ``None``.
        company_name: 기업명. **제공하지 않는 API 가 있습니다**(``None``).
        representative_name: 대표자명. **제공하지 않는 API 가 있습니다**(``None``).
        address: 소재지. 제공하지 않으면 ``None``.
    """

    business_no: str
    valid_from: date
    valid_to: date
    cert_code: str | None = None
    certificate_number: str | None = None
    issuing_agency: str | None = None
    company_name: str | None = None
    representative_name: str | None = None
    address: str | None = None
