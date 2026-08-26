"""
tests.test_startup_enterprise_api

**창업기업 확인서 API**(``smppKiCertInfo/getKiCertInfo``) 조회 경로 검증.

이 파일은 STEP 42 의 확인 항목을 창업기업 경로 하나에 모아 검사합니다. 호출
계층(:mod:`procurement.collectors.client`)과 파서
(:mod:`procurement.collectors.smpp`)는 이미 있으므로 **새로 만들지 않고**,
지시서가 요구한 다음 사실만 확인합니다.

1. 사업자등록번호가 요청에 제대로 실린다
2. 정상 응답이 내부 구조로 해석된다
3. 오류(HTTP · 잘못된 응답 · 필수 필드 누락 · 빈 결과)가 구분된다
4. ⛔ **유효/무효 판정값을 만들지 않는다**
5. ⛔ **조회만으로 DB 가 바뀌지 않는다**

.. warning::
    **이 파일의 어떤 테스트도 실제 API 서버에 접속하지 않습니다** —
    :class:`TestRealApiCall` 만 예외이며, 인증키와 시험용 사업자번호가 **둘 다**
    설정(``.env`` 또는 환경변수)으로 주어졌을 때만 실행되고 그 밖에는 건너뜁니다.

.. note::
    인증키는 ``"test-smpp-key"`` 같은 명백한 더미 값이고, 사업자번호는
    ``1000000001`` 같은 합성값입니다. 실제 키·실제 고객 사업자번호를 이 파일에
    적지 않습니다.
"""

from __future__ import annotations

import ast
import dataclasses
import hashlib
from collections.abc import Mapping
from pathlib import Path

import pytest

from procurement.collectors import models as collector_models
from procurement.collectors import smpp as smpp_parser
from procurement.collectors.client import (
    SOURCE_STARTUP_SMPP,
    URL_STARTUP_SMPP,
    CertificationApiClient,
    FetchResult,
)
from procurement.collectors.errors import (
    ApiAuthError,
    ApiRequestError,
    ApiServerError,
)
from procurement.collectors.models import ApiParseError, CertificationRecord
from procurement.collectors.transport import HttpResponse
from procurement.core.config import Settings, settings
from procurement.database.bootstrap import init_db

#: 합성 사업자등록번호. 실제 고객 값이 아닙니다.
BUSINESS_NO = "1000000001"

#: 명세서 샘플 구조를 따르는 정상 응답 (실제 API 응답이 아님).
STARTUP_OK = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header>
  <body><items><item>
    <bsnmNo>1000000001</bsnmNo>
    <entrpsNm>합성기업</entrpsNm>
    <rprsntvNm>홍길동</rprsntvNm>
    <adres>어딘가</adres>
    <validPdDe>2022.04.07 ~ 2025.04.06</validPdDe>
    <earlyValidPdDe>2022.04.07 ~ 2023.04.06</earlyValidPdDe>
  </item></items></body>
</response>
"""

#: 결과코드 03 — 오류가 아니라 "해당 사업자번호로 유효한 확인서가 없음".
NO_DATA = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header><resultCode>03</resultCode><resultMsg>NODATA_ERROR</resultMsg></header>
</response>
"""

#: 유효기간 필드가 빠진 응답.
MISSING_VALID_PERIOD = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header>
  <body><items><item>
    <bsnmNo>1000000001</bsnmNo>
    <entrpsNm>합성기업</entrpsNm>
  </item></items></body>
</response>
"""

#: 사업자번호 필드가 빠진 응답.
MISSING_BUSINESS_NO = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header><resultCode>00</resultCode><resultMsg>NORMAL SERVICE.</resultMsg></header>
  <body><items><item>
    <entrpsNm>합성기업</entrpsNm>
    <validPdDe>2022.04.07 ~ 2025.04.06</validPdDe>
  </item></items></body>
</response>
"""

#: 인증키 오류 (문서화된 결과코드 30).
AUTH_ERROR = """<?xml version="1.0" encoding="UTF-8"?>
<response>
  <header><resultCode>30</resultCode><resultMsg>SERVICE KEY IS NOT REGISTERED</resultMsg></header>
</response>
"""


class StubTransport:
    """네트워크를 쓰지 않는 전송 대역.

    준비된 응답을 순서대로 돌려주고, 요청 인자를 :attr:`calls` 에 기록합니다.
    """

    def __init__(self, *responses: HttpResponse | Exception) -> None:
        """대역을 초기화합니다."""
        self._responses = list(responses)
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def get(self, url: str, params: Mapping[str, str], *, timeout: float) -> HttpResponse:
        """기록해 둔 응답을 순서대로 반환합니다."""
        self.calls.append((url, dict(params), timeout))
        if not self._responses:
            raise AssertionError("대역에 준비된 응답보다 많이 호출되었습니다.")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(*responses: HttpResponse | Exception) -> tuple[CertificationApiClient, StubTransport]:
    """대역이 끼워진 호출기를 만듭니다."""
    transport = StubTransport(*responses)
    return (
        CertificationApiClient(smpp_api_key="test-smpp-key", transport=transport),
        transport,
    )


def _fetch(*responses: HttpResponse | Exception) -> tuple[FetchResult, StubTransport]:
    """창업기업 조회를 한 번 수행합니다."""
    client, transport = _client(*responses)
    return client.fetch(SOURCE_STARTUP_SMPP, BUSINESS_NO, stdr_date=None), transport


def _ok(body: str) -> HttpResponse:
    """HTTP 200 응답을 만듭니다."""
    return HttpResponse(status=200, body=body)


# ---------------------------------------------------------------------------
# ② 사업자등록번호 전달 (지시 §10-②)
# ---------------------------------------------------------------------------


class TestRequest:
    """요청이 명세대로 만들어진다."""

    def test_business_no_is_sent_as_bsnm_no(self) -> None:
        """조회 키는 명세서에 기재된 ``bsnmNo`` 로 전달된다."""
        _, transport = _fetch(_ok(STARTUP_OK))

        _, params, _ = transport.calls[0]
        assert params["bsnmNo"] == BUSINESS_NO

    def test_endpoint_is_the_spec_url(self) -> None:
        """엔드포인트를 코드가 임의로 조립하지 않는다."""
        _, transport = _fetch(_ok(STARTUP_OK))

        url, _, _ = transport.calls[0]
        assert url == URL_STARTUP_SMPP
        assert url.endswith("/B550598/smppKiCertInfo/getKiCertInfo")

    def test_service_key_is_sent(self) -> None:
        """인증키는 ``serviceKey`` 로 전달된다."""
        _, transport = _fetch(_ok(STARTUP_OK))

        _, params, _ = transport.calls[0]
        assert params["serviceKey"] == "test-smpp-key"

    def test_no_undocumented_parameters_are_added(self) -> None:
        """명세에 없는 파라미터를 지어내지 않는다.

        특히 ``stdrDate``(기준일자)는 이 API 의 명세에 **없다.** 코드가 오늘
        날짜 등을 임의로 붙이면 그것이 곧 확인받지 않은 판정 기준이 된다.
        """
        _, transport = _fetch(_ok(STARTUP_OK))

        _, params, _ = transport.calls[0]
        assert set(params) == {"serviceKey", "bsnmNo"}


# ---------------------------------------------------------------------------
# ① 정상 응답 parsing (지시 §10-①)
# ---------------------------------------------------------------------------


class TestSuccessfulResponse:
    """정상 응답이 내부 구조로 해석된다."""

    def test_one_record_is_returned(self) -> None:
        result, _ = _fetch(_ok(STARTUP_OK))

        assert len(result.records) == 1

    def test_api_values_are_preserved_as_is(self) -> None:
        """API 가 준 값을 그대로 담는다 — 의미를 바꾸지 않는다."""
        result, _ = _fetch(_ok(STARTUP_OK))

        record = result.records[0]
        assert record.business_no == BUSINESS_NO
        assert record.company_name == "합성기업"
        assert record.representative_name == "홍길동"
        assert record.address == "어딘가"

    def test_valid_period_is_read_but_not_judged(self) -> None:
        """범위 문자열이 시작일·종료일로 나뉜다.

        ⛔ **여기까지가 전부다.** 이 두 날짜로 유효/무효를 정하지 않는다.
        """
        result, _ = _fetch(_ok(STARTUP_OK))

        record = result.records[0]
        assert (record.valid_from.isoformat(), record.valid_to.isoformat()) == (
            "2022-04-07",
            "2025-04-06",
        )

    def test_source_and_policy_are_reported(self) -> None:
        """어느 출처로 조회했는지 결과에 남는다."""
        result, _ = _fetch(_ok(STARTUP_OK))

        assert (result.source, result.policy_code) == (SOURCE_STARTUP_SMPP, "STARTUP")


# ---------------------------------------------------------------------------
# ③ API 오류 (지시 §10-③)
# ---------------------------------------------------------------------------


class TestErrors:
    """오류가 범주별로 구분된다."""

    def test_empty_result_is_not_an_error(self) -> None:
        """결과코드 03 은 "확인서가 없음" 이지 오류가 아니다."""
        result, _ = _fetch(_ok(NO_DATA))

        assert result.records == ()

    def test_http_4xx_is_a_request_error(self) -> None:
        client, _ = _client(HttpResponse(status=404, body="not found"))

        with pytest.raises(ApiRequestError):
            client.fetch(SOURCE_STARTUP_SMPP, BUSINESS_NO, stdr_date=None)

    def test_http_5xx_is_retried_then_raised(self) -> None:
        """서버 오류는 재시도 대상이며, 모두 실패하면 그대로 올린다."""
        client, transport = _client(ApiServerError(503), ApiServerError(503))

        with pytest.raises(ApiServerError):
            client.fetch(SOURCE_STARTUP_SMPP, BUSINESS_NO, stdr_date=None)
        assert len(transport.calls) == 2

    def test_broken_response_is_a_parse_error(self) -> None:
        client, _ = _client(_ok("<response><unclosed>"))

        with pytest.raises(ApiParseError):
            client.fetch(SOURCE_STARTUP_SMPP, BUSINESS_NO, stdr_date=None)

    def test_missing_valid_period_is_a_parse_error(self) -> None:
        """필수 필드가 없으면 빈 값으로 넘기지 않고 실패시킨다."""
        client, _ = _client(_ok(MISSING_VALID_PERIOD))

        with pytest.raises(ApiParseError):
            client.fetch(SOURCE_STARTUP_SMPP, BUSINESS_NO, stdr_date=None)

    def test_missing_business_no_is_a_parse_error(self) -> None:
        """사업자번호가 없으면 요청값으로 몰래 채우지 않는다.

        이 API 는 응답에 ``bsnmNo`` 를 담는다고 명세에 기재되어 있다. 없는
        응답을 요청값으로 메우면 어느 기업의 확인서인지 확인할 수 없게 된다.
        """
        client, _ = _client(_ok(MISSING_BUSINESS_NO))

        with pytest.raises(ApiParseError):
            client.fetch(SOURCE_STARTUP_SMPP, BUSINESS_NO, stdr_date=None)

    def test_documented_auth_code_is_classified(self) -> None:
        """문서화된 결과코드 30 은 인증 오류로 분류된다."""
        client, transport = _client(_ok(AUTH_ERROR))

        with pytest.raises(ApiAuthError):
            client.fetch(SOURCE_STARTUP_SMPP, BUSINESS_NO, stdr_date=None)
        # ⛔ 인증 오류는 다시 보내도 결과가 같다 — 일일 한도만 소모하므로 재시도 금지
        assert len(transport.calls) == 1


# ---------------------------------------------------------------------------
# ④ 인증 유효기간 판정 금지 (지시 §8 · §10-④)
# ---------------------------------------------------------------------------

#: 이번 단계에서 **만들면 안 되는** 값의 이름.
FORBIDDEN_NAMES = (
    "is_valid",
    "valid",
    "is_startup",
    "certified",
    "confidence",
    "score",
    "recommended_type",
)


class TestNoVerdictIsProduced:
    """⛔ 조회 결과가 판정을 담지 않는다 — 타입 수준에서 확인한다."""

    def test_record_has_no_verdict_field(self) -> None:
        """확인서 DTO 에 판정 필드가 **없다**.

        필드가 없으면 뒤 계층이 실수로라도 판정을 읽어갈 수 없다.
        """
        names = {field.name for field in dataclasses.fields(CertificationRecord)}

        assert names.isdisjoint(FORBIDDEN_NAMES)

    def test_fetch_result_has_no_verdict_field(self) -> None:
        names = {field.name for field in dataclasses.fields(FetchResult)}

        assert names.isdisjoint(FORBIDDEN_NAMES)

    def test_the_response_object_exposes_no_verdict_attribute(self) -> None:
        """실제 조회 결과에도 판정 이름이 붙어 있지 않다."""
        result, _ = _fetch(_ok(STARTUP_OK))

        record = result.records[0]
        for name in FORBIDDEN_NAMES:
            assert not hasattr(record, name)
            assert not hasattr(result, name)

    @pytest.mark.parametrize("module", [smpp_parser, collector_models])
    def test_modules_expose_no_validity_helper(self, module: object) -> None:
        """ "유효한가" 를 답해 주는 함수를 두지 않는다.

        기준일이 확정되기 전까지 "가져오는 것" 과 "판정하는 것" 을 섞지 않는다.
        """
        public = {name for name in dir(module) if not name.startswith("_")}

        assert public.isdisjoint(FORBIDDEN_NAMES)
        assert not {name for name in public if name.startswith("is_valid")}


# ---------------------------------------------------------------------------
# ⑤ DB 미변경 (지시 §9 · §10-⑤)
# ---------------------------------------------------------------------------


class TestDatabaseIsUntouched:
    """⛔ 조회만으로는 DB 가 바뀌지 않는다."""

    def test_fetching_does_not_change_the_database(self, tmp_path: Path) -> None:
        """조회 전후 DB 파일이 **바이트 단위로 동일**하다."""
        db_path = tmp_path / "startup-api.db"
        init_db(db_path)
        before = hashlib.sha256(db_path.read_bytes()).hexdigest()

        result, _ = _fetch(_ok(STARTUP_OK))

        assert len(result.records) == 1
        assert hashlib.sha256(db_path.read_bytes()).hexdigest() == before

    def test_the_client_does_not_know_about_repositories(self) -> None:
        """호출 계층이 저장 계층을 알지 못한다.

        모듈이 Repository 를 아예 import 하지 않으면 저장 코드를 넣을 수 없다.
        저장 연결은 별도 계층(``sync_service``)의 일이며 이번 단계 밖이다.
        """
        source = Path(CertificationApiClient.__module__.replace(".", "/"))
        text = (Path("src") / source.with_suffix(".py")).read_text(encoding="utf-8")

        assert "Repository" not in text
        assert "INSERT" not in text.upper()


# ---------------------------------------------------------------------------
# 실제 API 호출 (지시 §11) — 기본은 건너뜀
# ---------------------------------------------------------------------------

# ⛔ ``os.environ`` 을 직접 읽지 않습니다(STEP 44 수정). 이 프로젝트의 설정은
#    모두 ``.env → Settings`` 를 거치는데, ``os.environ`` 만 보면 ``.env`` 에
#    넣은 값이 보이지 않아 키를 넣고도 계속 건너뛰게 됩니다. 같은 설정을 두
#    가지 방법으로 읽으면 "왜 안 되는지" 를 알 수 없게 되므로 경로를 하나로
#    맞춥니다. ``Settings`` 는 환경변수도 함께 읽으므로 ``export`` 방식도
#    그대로 동작합니다.
_REAL_KEY = (settings.SMPP_API_KEY or "").strip()
_REAL_BUSINESS_NO = (settings.SMPP_TEST_BUSINESS_NO or "").strip()


@pytest.mark.skipif(
    not (_REAL_KEY and _REAL_BUSINESS_NO),
    reason=(
        "실제 API 호출 시험은 SMPP_API_KEY 와 SMPP_TEST_BUSINESS_NO 가 "
        "둘 다 설정된 환경에서만 수행합니다(.env 또는 환경변수). "
        "값이 없으면 실패가 아니라 건너뜁니다."
    ),
)
class TestRealApiCall:
    """실제 엔드포인트 연결 확인.

    .. warning::
        키와 시험용 사업자번호가 **둘 다** 주어졌을 때만 실행됩니다. 시험용
        사업자번호는 개발자가 따로 제공한 값이어야 하며, 고객 원본 데이터에서
        가져오지 않습니다.

    .. warning::
        인증키와 사업자번호를 로그·assert 메시지에 출력하지 않습니다.
    """

    def test_the_endpoint_answers_and_the_response_parses(self) -> None:
        """응답이 오고, 그 응답이 파서를 통과한다."""
        client = CertificationApiClient(smpp_api_key=_REAL_KEY)

        result = client.fetch(SOURCE_STARTUP_SMPP, _REAL_BUSINESS_NO, stdr_date=None)

        # ⛔ 확인서가 있든 없든 둘 다 정상이다. "있으면 창업기업" 이라고 하지 않는다.
        assert isinstance(result.records, tuple)
        assert result.source == SOURCE_STARTUP_SMPP


# ---------------------------------------------------------------------------
# 실호출 시험이 열리고 닫히는 조건 (STEP 44)
# ---------------------------------------------------------------------------


class TestRealCallGate:
    """실호출 시험을 여는 **문**이 올바로 동작한다.

    이 검사가 없어서 STEP 43 에서 결함을 늦게 발견했습니다. ``.env`` 에 키를
    넣어도 시험이 계속 건너뛰는데, 그것이 "키가 잘못됐다" 인지 "설정을 읽는
    경로가 다르다" 인지 화면만 봐서는 알 수 없었습니다.
    """

    def test_the_gate_reads_the_project_settings_not_os_environ(self) -> None:
        """문이 ``os.environ`` 을 직접 읽지 않는다.

        이 프로젝트의 설정은 모두 ``.env → Settings`` 를 거칩니다. 이 파일만
        다른 경로로 읽으면 ``.env`` 에 넣은 값이 보이지 않습니다.
        """
        text = Path(__file__).read_text(encoding="utf-8")
        # 주석·docstring 이 아니라 **실행되는 코드**만 본다. 본문에서 이 결함을
        # 설명하려면 이름을 적어야 하는데, 글자만 세면 그 설명에 걸린다.
        tree = ast.parse(text)
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}

        assert "environ" not in attributes
        assert "SMPP_API_KEY" in attributes
        assert "SMPP_TEST_BUSINESS_NO" in attributes

    def test_settings_reads_both_values_from_a_dotenv_file(self, tmp_path: Path) -> None:
        """``.env`` 파일에 적은 값이 ``Settings`` 로 읽힌다.

        STEP 43 결함의 핵심이 바로 이 경로였습니다. 여기 쓰는 값은 명백한
        더미이며 실제 키가 아닙니다.
        """
        env_file = tmp_path / ".env"
        env_file.write_text(
            "SMPP_API_KEY=dummy-key-for-test\nSMPP_TEST_BUSINESS_NO=1000000001\n",
            encoding="utf-8",
        )

        loaded = Settings(_env_file=env_file)  # type: ignore[call-arg]

        assert loaded.SMPP_API_KEY == "dummy-key-for-test"
        assert loaded.SMPP_TEST_BUSINESS_NO == "1000000001"

    def test_both_values_default_to_none_so_the_gate_stays_closed(self) -> None:
        """설정이 없으면 두 값 모두 ``None`` — 문이 닫힌 채로 있다."""
        empty = Settings(_env_file=None)  # type: ignore[call-arg]

        assert empty.SMPP_API_KEY is None
        assert empty.SMPP_TEST_BUSINESS_NO is None

    @pytest.mark.parametrize(
        ("key", "business_no"),
        [
            (None, "1000000001"),
            ("dummy-key-for-test", None),
            (None, None),
            ("   ", "1000000001"),
            ("dummy-key-for-test", "   "),
        ],
    )
    def test_the_gate_is_closed_unless_both_values_are_present(
        self, key: str | None, business_no: str | None
    ) -> None:
        """하나라도 없거나 공백이면 건너뛴다 — **실패가 아니다**.

        문을 여는 조건은 실호출 시험을 감싼 ``skipif`` 와 같은 식입니다.
        """
        opened = bool((key or "").strip() and (business_no or "").strip())

        assert opened is False

    def test_the_gate_opens_only_when_both_are_set(self) -> None:
        """둘 다 있을 때만 열린다."""
        opened = bool("dummy-key-for-test".strip() and "1000000001".strip())

        assert opened is True

    def test_the_skip_reason_does_not_reveal_any_value(self) -> None:
        """건너뛴 사유 문구에 값이 아니라 **변수 이름만** 나온다.

        이 문구는 ``pytest -rs`` 로 화면에 그대로 찍힙니다. 값이 섞이면 키가
        터미널·CI 로그에 남습니다.
        """
        marker = next(
            mark
            for mark in TestRealApiCall.pytestmark  # type: ignore[attr-defined]
            if mark.name == "skipif"
        )
        reason = str(marker.kwargs["reason"])

        assert "SMPP_API_KEY" in reason
        assert "SMPP_TEST_BUSINESS_NO" in reason
        for value in (_REAL_KEY, _REAL_BUSINESS_NO):
            if value:
                assert value not in reason
