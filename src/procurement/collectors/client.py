"""
procurement.collectors.client

외부 인증 API 를 호출해 **기존 파서**에 넘기는 계층입니다.

이 계층의 책임은 다음으로 **한정**합니다.

- 엔드포인트 결정
- 인증키(``serviceKey``) 전달
- 필수 파라미터 전달
- 응답 대기 시간(timeout)
- 문서화된 오류 응답의 범주 분류
- 일시적 장애에 한한 최소 재시도
- 응답을 기존 파서로 전달

다음은 **하지 않습니다.**

- 인증 여부 판정 (여성기업인지 · 창업기업인지 등)
- 유효기간 해석 규칙 변경
- ``stdrDate`` 값 선택
- 응답 필드 의미의 재해석

.. warning::
    ``stdrDate``(기준일자)는 **호출자가 반드시 전달**해야 합니다. 오늘 날짜나
    연도 말일 등을 코드가 임의로 고르지 않습니다. 어떤 날짜를 기준으로 유효성을
    볼지는 업무 결정 사항이며 아직 확정되지 않았습니다(D-24 관련).

.. note::
    엔드포인트·파라미터명·결과코드는 모두 공식 명세서를 근거로 하며
    (``docs/DATA_ACQUISITION_PLAN.md`` §2.2.0 · §3.2), 추측해서 채운 항목은
    없습니다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date

from procurement.collectors.errors import (
    RETRYABLE_ERRORS,
    ApiRequestError,
    StdrDateRequiredError,
    classify_result_code,
)
from procurement.collectors.kised import parse_corporate_information_json
from procurement.collectors.models import ApiResponseError, CertificationRecord
from procurement.collectors.smpp import (
    DISABLED_NO_DATA_CODES,
    NO_DATA_CODE,
    WOMAN_NO_DATA_CODES,
    parse_cert_list,
    parse_startup_cert,
)
from procurement.collectors.transport import (
    DEFAULT_TIMEOUT_SECONDS,
    Transport,
    UrllibTransport,
)

#: 엔드포인트 (명세서 기재값)
URL_WOMAN = "http://apis.data.go.kr/B550598/smppCertInfo/getFnrssList"
URL_DISABLED = "http://apis.data.go.kr/B550598/smppCertInfo/getDspsnList"
URL_STARTUP_SMPP = "http://apis.data.go.kr/B550598/smppKiCertInfo/getKiCertInfo"
URL_STARTUP_KISED = "https://apis.data.go.kr/B552735/kisedCertService/getCorporateInformation"

#: 조회 출처 식별자.
#:
#: 창업기업은 확보한 API 가 **2종**(SMPP · 창업진흥원)이므로 하나로 합치지 않고
#: 호출자가 명시적으로 고르게 합니다. 어느 쪽을 정식 출처로 삼을지는 업무 결정
#: 사항이며, 코드가 임의로 정하지 않습니다.
SOURCE_WOMAN = "WOMAN_SMPP"
SOURCE_DISABLED = "DISABLED_SMPP"
SOURCE_STARTUP_SMPP = "STARTUP_SMPP"
SOURCE_STARTUP_KISED = "STARTUP_KISED"

#: 조회 출처 → 정책 코드
SOURCE_POLICY_CODES: Mapping[str, str] = {
    SOURCE_WOMAN: "WOMAN",
    SOURCE_DISABLED: "DISABLED",
    SOURCE_STARTUP_SMPP: "STARTUP",
    SOURCE_STARTUP_KISED: "STARTUP",
}

#: 출처별 "데이터 없음" 결과코드 (``smppCertInfo`` 계열).
#:
#: 여성기업·장애인기업은 **같은 파서**
#: (:func:`~procurement.collectors.smpp.parse_cert_list`)를 쓰지만, 어느 코드를
#: "데이터 없음" 으로 볼지는 **출처마다 실호출로 따로 확인**했습니다. 파서가
#: 아니라 여기서 가르는 이유입니다 — 파서 안에서 넓히면 확인하지 않은 API 까지
#: 함께 넓어집니다.
#:
#: 여기에 없는 출처는 명세에 기재된 ``03`` 하나만 씁니다.
SMPP_CERT_NO_DATA_CODES: Mapping[str, frozenset[str]] = {
    SOURCE_WOMAN: WOMAN_NO_DATA_CODES,
    SOURCE_DISABLED: DISABLED_NO_DATA_CODES,
}

#: ``stdrDate`` 를 **필수**로 요구하는 출처 (명세서 기재).
SOURCES_REQUIRING_STDR_DATE: frozenset[str] = frozenset({SOURCE_WOMAN, SOURCE_DISABLED})

#: 기본 시도 횟수(최초 1회 + 재시도 1회).
#:
#: 명세서에는 재시도 정책이 기재되어 있지 않습니다. 근거 없이 여러 번 재시도하면
#: 일일 호출 한도만 소모하므로 **최소값**을 기본으로 둡니다. 재시도 대상도
#: 일시적 장애(timeout · 네트워크 · 5xx)로 한정합니다.
DEFAULT_MAX_ATTEMPTS = 2


class ApiKeyNotConfiguredError(RuntimeError):
    """인증키가 설정되지 않았습니다.

    키는 ``.env`` 로만 주입합니다. 코드·테스트·문서·저장소에 실제 값을 두지
    않습니다.
    """


@dataclass(frozen=True, kw_only=True)
class FetchResult:
    """조회 결과 한 건.

    Attributes:
        source: 조회 출처 식별자.
        policy_code: 이 출처가 대응하는 정책 코드.
        business_no: 조회에 사용한 사업자등록번호(요청값 그대로).
        records: 파서가 해석한 확인서 목록. 유효한 확인서가 없으면 빈 목록입니다.
        attempts: 실제로 시도한 횟수.
    """

    source: str
    policy_code: str
    business_no: str
    records: tuple[CertificationRecord, ...]
    attempts: int


def _format_stdr_date(value: date) -> str:
    """``stdrDate`` 를 명세 형식(``YYYYMMDD``)으로 변환합니다."""
    return value.strftime("%Y%m%d")


class CertificationApiClient:
    """인증 확인 API 호출기.

    Args:
        smpp_api_key: 공공구매종합정보망(SMPP) 인증키. 여성·장애인·창업(SMPP)에
            사용합니다.
        startup_api_key: 창업진흥원 인증키.
        transport: HTTP 전송 구현. 생략하면 ``urllib`` 기반 기본 구현을 씁니다.
            테스트에서는 네트워크를 쓰지 않는 대역을 넘깁니다.
        timeout: 응답 대기 시간(초).
        max_attempts: 최대 시도 횟수. 1 이면 재시도하지 않습니다.

    .. note::
        키를 넘기지 않아도 객체는 만들어집니다. 키가 필요한 호출을 실제로 할 때
        :class:`ApiKeyNotConfiguredError` 가 발생합니다. 키가 없는 환경에서도
        구성(composition)이 실패하지 않도록 하기 위함입니다.
    """

    def __init__(
        self,
        *,
        smpp_api_key: str | None = None,
        startup_api_key: str | None = None,
        transport: Transport | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        """호출기를 초기화합니다."""
        if max_attempts < 1:
            raise ValueError("max_attempts 는 1 이상이어야 합니다.")
        self._smpp_api_key = smpp_api_key
        self._startup_api_key = startup_api_key
        self._transport: Transport = transport if transport is not None else UrllibTransport()
        self._timeout = timeout
        self._max_attempts = max_attempts

    # ------------------------------------------------------------------
    # 공개 API
    # ------------------------------------------------------------------
    def fetch(
        self,
        source: str,
        business_no: str,
        *,
        stdr_date: date | None,
    ) -> FetchResult:
        """출처를 지정해 확인서를 조회합니다.

        Args:
            source: :data:`SOURCE_WOMAN` 등 조회 출처 식별자.
            business_no: 조회할 사업자등록번호.
            stdr_date: 기준일자. **기본값이 없습니다.** 여성·장애인 조회에서는
                필수이며, ``None`` 이면 :class:`StdrDateRequiredError` 가
                발생합니다. 그 밖의 출처에서는 명세상 파라미터가 없으므로
                ``None`` 을 넘깁니다.

        Returns:
            :class:`FetchResult`.

        Raises:
            StdrDateRequiredError: 기준일이 필요한 조회인데 전달되지 않은 경우.
            ApiKeyNotConfiguredError: 해당 출처의 인증키가 설정되지 않은 경우.
            ApiAuthError · ApiQuotaError · ApiResponseError: API 오류 응답.
            ApiTimeoutError · ApiNetworkError · ApiServerError · ApiRequestError:
                전송 계층 오류.
            ApiParseError: 응답 형식이 명세와 다른 경우.
        """
        if source not in SOURCE_POLICY_CODES:
            raise ValueError(f"알 수 없는 조회 출처입니다: {source!r}")
        if source in SOURCES_REQUIRING_STDR_DATE and stdr_date is None:
            raise StdrDateRequiredError(
                f"{source} 조회에는 stdrDate(기준일자)가 필수입니다. "
                "어느 날짜를 기준으로 볼지는 업무 결정 사항(D-24 관련)이므로 "
                "코드가 임의로 정하지 않습니다. 호출자가 명시적으로 전달하세요."
            )

        if source == SOURCE_WOMAN:
            return self._fetch_smpp_cert_list(SOURCE_WOMAN, URL_WOMAN, business_no, stdr_date)
        if source == SOURCE_DISABLED:
            return self._fetch_smpp_cert_list(SOURCE_DISABLED, URL_DISABLED, business_no, stdr_date)
        if source == SOURCE_STARTUP_SMPP:
            return self._fetch_startup_smpp(business_no)
        return self._fetch_startup_kised(business_no)

    # ------------------------------------------------------------------
    # 출처별 호출
    # ------------------------------------------------------------------
    def _fetch_smpp_cert_list(
        self,
        source: str,
        url: str,
        business_no: str,
        stdr_date: date | None,
    ) -> FetchResult:
        """여성기업·장애인기업 확인 조회 (응답 구조 동일).

        .. note::
            응답 구조는 같지만 **"데이터 없음" 으로 볼 코드는 출처마다 따로
            확인했습니다.** 여성기업(2026-08-27)에 이어 장애인기업도 실호출에서
            ``90`` 이 확인되어(2026-08-27, STEP 49) 각각 넓혔습니다. 값이 같아진
            지금도 :data:`SMPP_CERT_NO_DATA_CODES` 에서 **출처별로** 가릅니다 —
            공용 파서의 기본값을 넓히면 확인하지 않은 API 까지 함께 넓어지기
            때문입니다.
        """
        if stdr_date is None:  # pragma: no cover - fetch() 에서 이미 막힘
            raise StdrDateRequiredError("stdrDate 가 필요합니다.")
        params = {
            "serviceKey": self._require_key(self._smpp_api_key, "SMPP_API_KEY"),
            "bsnmNo": business_no,
            "stdrDate": _format_stdr_date(stdr_date),
        }
        no_data_codes = SMPP_CERT_NO_DATA_CODES.get(source, frozenset({NO_DATA_CODE}))
        body, attempts = self._request(url, params)
        records = self._parse(
            lambda: parse_cert_list(body, business_no, no_data_codes=no_data_codes)
        )
        return self._result(source, business_no, records, attempts)

    def _fetch_startup_smpp(self, business_no: str) -> FetchResult:
        """창업기업 확인서 조회 (SMPP)."""
        params = {
            "serviceKey": self._require_key(self._smpp_api_key, "SMPP_API_KEY"),
            "bsnmNo": business_no,
        }
        body, attempts = self._request(URL_STARTUP_SMPP, params)
        records = self._parse(lambda: parse_startup_cert(body))
        return self._result(SOURCE_STARTUP_SMPP, business_no, records, attempts)

    def _fetch_startup_kised(self, business_no: str) -> FetchResult:
        """창업기업 확인서 조회 (창업진흥원).

        이 API 는 JSON 과 XML 을 모두 제공하며 기본값이 JSON 입니다. 명시적으로
        ``returnType=JSON`` 을 보내 응답 형식이 바뀌어 파서가 깨지는 일을 막습니다.
        """
        params = {
            "serviceKey": self._require_key(self._startup_api_key, "STARTUP_API_KEY"),
            "brno": business_no,
            "returnType": "JSON",
        }
        body, attempts = self._request(URL_STARTUP_KISED, params)
        records = self._parse(lambda: parse_corporate_information_json(body))
        return self._result(SOURCE_STARTUP_KISED, business_no, records, attempts)

    # ------------------------------------------------------------------
    # 내부 도우미
    # ------------------------------------------------------------------
    def _result(
        self,
        source: str,
        business_no: str,
        records: list[CertificationRecord],
        attempts: int,
    ) -> FetchResult:
        """조회 결과를 조립합니다."""
        return FetchResult(
            source=source,
            policy_code=SOURCE_POLICY_CODES[source],
            business_no=business_no,
            records=tuple(records),
            attempts=attempts,
        )

    @staticmethod
    def _require_key(value: str | None, env_name: str) -> str:
        """인증키를 반환합니다.

        Raises:
            ApiKeyNotConfiguredError: 키가 비어 있는 경우.
        """
        if value is None or not value.strip():
            raise ApiKeyNotConfiguredError(
                f"{env_name} 가 설정되지 않았습니다. .env 에 값을 넣으세요. "
                "실제 키 값은 코드·문서·저장소에 기록하지 않습니다."
            )
        return value

    def _request(self, url: str, params: Mapping[str, str]) -> tuple[str, int]:
        """재시도를 포함해 요청을 보냅니다.

        일시적 장애(:data:`~procurement.collectors.errors.RETRYABLE_ERRORS`)에만
        재시도합니다. 인증 실패·잘못된 요청·한도 초과는 다시 보내도 결과가 같으므로
        **즉시 실패**시킵니다.

        Returns:
            ``(응답 본문, 시도 횟수)``.
        """
        last_error: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._transport.get(url, params, timeout=self._timeout)
            except RETRYABLE_ERRORS as exc:
                last_error = exc
                continue
            if response.status >= 400:
                # 전송 구현이 상태 코드를 예외로 올리지 않은 경우의 방어선.
                raise ApiRequestError(response.status, response.body)
            return response.body, attempt

        assert last_error is not None  # noqa: S101 - 루프 구조상 항상 설정됨
        raise last_error

    @staticmethod
    def _parse(parse: Callable[[], list[CertificationRecord]]) -> list[CertificationRecord]:
        """파서를 호출하고, 문서화된 결과코드를 범주별 오류로 재분류합니다."""
        try:
            return parse()
        except ApiResponseError as exc:
            raise classify_result_code(exc) from exc
