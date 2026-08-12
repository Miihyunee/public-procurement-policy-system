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

from procurement.matchers.business_no import normalize_business_no


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
        business_no: 사업자등록번호. :func:`normalize_business_no` 로 **기존 규칙과
            동일하게 정규화**한 10자리 문자열입니다. API 가 응답에 담지 않는
            경우(여성·장애인 확인)에는 **요청에 사용한 값**을 정규화해 넣습니다.
        business_no_original: 정규화 전 원본 값(추적용).
        business_no_warnings: 정규화 경고(체크섬 불일치 등). 경고가 있어도
            데이터를 버리지 않습니다(D-002).
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
    business_no_original: str = ""
    business_no_warnings: tuple[str, ...] = ()
    valid_from: date
    valid_to: date
    cert_code: str | None = None
    certificate_number: str | None = None
    issuing_agency: str | None = None
    company_name: str | None = None
    representative_name: str | None = None
    address: str | None = None


def resolve_business_no(value: object) -> tuple[str, str, tuple[str, ...]]:
    """사업자등록번호를 **기존 규칙과 동일하게** 정규화합니다.

    외부 API 응답도 구매데이터와 같은 규칙(``docs/DECISIONS.md`` §3.2)을 거쳐야
    합니다. 그렇지 않으면 하이픈 유무 같은 사소한 차이로 매칭이 조용히 실패합니다.

    - 하이픈·공백 제거 후 10자리
    - **9자리 자동 0 보정 금지**
    - 체크섬 오류는 경고만 (D-002) — 데이터를 버리지 않습니다

    Args:
        value: API 응답 또는 요청에 사용한 사업자등록번호.

    Returns:
        ``(정규화값, 원본, 경고 목록)``.

    Raises:
        ApiParseError: 형식이 맞지 않아 결합 키로 쓸 수 없는 경우.
    """
    normalized = normalize_business_no(value)
    if not normalized.is_valid or normalized.value is None:
        raise ApiParseError(
            f"사업자등록번호를 결합 키로 쓸 수 없습니다: {normalized.original!r} "
            f"({normalized.status.value})"
        )
    return normalized.value, normalized.original, tuple(normalized.warnings)
