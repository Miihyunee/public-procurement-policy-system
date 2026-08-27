"""
tests.test_startup_result_code_90

창업기업 확인서 API 의 **결과코드 ``90``** 처리 검증.

배경
====

2026-08-27 실호출에서 ``smppKiCertInfo/getKiCertInfo`` 가 결과코드 ``90``
"매칭데이터가 존재하지 않습니다" 를 돌려주었습니다. 이 코드는 **공식 활용가이드
어디에도 없습니다.** 코드는 명세에 없는 코드를 추측하지 않도록 만들어져 있어
오류로 올렸고, 그 결과 확인서가 없는 기업을 조회할 때마다 실패했습니다.

PM 결정(2026-08-27)에 따라 ``90`` 을 ``03`` 과 같은 **"정상 응답이지만 조회
결과 없음"** 으로 처리합니다.

이 파일이 고정하는 사실
=======================

1. ``90`` 은 오류가 아니라 **빈 결과**다
2. ⛔ ``00``(데이터 있음)으로 바뀐 것이 **아니다** — 확인서를 만들지 않는다
3. ⛔ 창업기업 조회에만 적용된다 — 여성·장애인은 그대로다
4. 인증 오류·한도 초과 등 다른 코드의 처리는 그대로다

.. note::
    실제 API 서버에 접속하지 않습니다. 인증키는 더미, 사업자번호는 합성값입니다.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date

import pytest

from procurement.collectors.client import (
    SOURCE_DISABLED,
    SOURCE_STARTUP_SMPP,
    SOURCE_WOMAN,
    CertificationApiClient,
)
from procurement.collectors.errors import ApiAuthError, ApiQuotaError
from procurement.collectors.models import ApiResponseError
from procurement.collectors.smpp import (
    NO_DATA_CODE,
    STARTUP_NO_DATA_CODES,
    SUCCESS_CODE,
    parse_cert_list,
    parse_startup_cert,
)
from procurement.collectors.transport import HttpResponse

#: 합성 사업자등록번호. 실제 고객 값이 아닙니다.
BUSINESS_NO = "1000000001"

STDR_DATE = date(2026, 8, 27)


def _response(code: str, message: str, body: str = "") -> str:
    """결과코드를 담은 응답을 만듭니다."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<response><header><resultCode>{code}</resultCode>"
        f"<resultMsg>{message}</resultMsg></header>"
        f"<body>{body}</body></response>"
    )


#: 실호출에서 실제로 돌아온 형태(코드와 메시지만 옮김).
CODE_90 = _response("90", "매칭데이터가 존재하지 않습니다.")

#: ``90`` 인데 항목이 섞여 들어온 경우 — 그것도 해석하지 않아야 한다.
CODE_90_WITH_JUNK = _response(
    "90",
    "매칭데이터가 존재하지 않습니다.",
    "<items><item><entrpsNm>부스러기</entrpsNm></item></items>",
)

CODE_03 = _response(NO_DATA_CODE, "NODATA_ERROR")

STARTUP_OK = _response(
    SUCCESS_CODE,
    "NORMAL SERVICE.",
    "<items><item>"
    "<bsnmNo>1000000001</bsnmNo>"
    "<entrpsNm>합성기업</entrpsNm>"
    "<validPdDe>2022.04.07 ~ 2025.04.06</validPdDe>"
    "</item></items>",
)


class StubTransport:
    """네트워크를 쓰지 않는 전송 대역."""

    def __init__(self, body: str) -> None:
        """대역을 초기화합니다."""
        self._body = body
        self.calls = 0

    def get(self, url: str, params: Mapping[str, str], *, timeout: float) -> HttpResponse:
        """준비된 본문을 돌려줍니다."""
        self.calls += 1
        return HttpResponse(status=200, body=self._body)


def _client(body: str) -> tuple[CertificationApiClient, StubTransport]:
    """대역이 끼워진 호출기를 만듭니다."""
    transport = StubTransport(body)
    return (
        CertificationApiClient(smpp_api_key="test-smpp-key", transport=transport),
        transport,
    )


def _fetch_startup(body: str) -> tuple[object, StubTransport]:
    """창업기업 조회를 한 번 수행합니다."""
    client, transport = _client(body)
    return client.fetch(SOURCE_STARTUP_SMPP, BUSINESS_NO, stdr_date=None), transport


# ---------------------------------------------------------------------------
# ① 결과코드 90 — 빈 결과
# ---------------------------------------------------------------------------


class TestCode90IsEmptyNotAnError:
    """``90`` 은 오류가 아니라 "조회 결과 없음" 이다."""

    def test_parser_returns_an_empty_list(self) -> None:
        assert parse_startup_cert(CODE_90) == []

    def test_client_returns_an_empty_result_without_raising(self) -> None:
        result, _ = _fetch_startup(CODE_90)

        assert result.records == ()  # type: ignore[attr-defined]

    def test_the_call_still_reports_which_source_answered(self) -> None:
        """호출은 정상 종료다 — 어느 출처에서 왔는지도 그대로 남는다."""
        result, _ = _fetch_startup(CODE_90)

        assert result.source == SOURCE_STARTUP_SMPP  # type: ignore[attr-defined]
        assert result.policy_code == "STARTUP"  # type: ignore[attr-defined]

    def test_it_is_not_retried(self) -> None:
        """정상 응답이므로 다시 부르지 않는다."""
        _, transport = _fetch_startup(CODE_90)

        assert transport.calls == 1

    def test_no_certification_is_fabricated(self) -> None:
        """⛔ 데이터 없음이 "확인서 한 건" 으로 바뀌지 않는다."""
        result, _ = _fetch_startup(CODE_90)

        assert len(result.records) == 0  # type: ignore[attr-defined]

    def test_ninety_is_not_treated_as_the_success_code(self) -> None:
        """⛔ ``90`` 을 ``00`` 으로 바꾼 것이 아니다.

        ``00`` 은 **항목을 해석**하고, ``90`` 은 **해석하지 않는다.** 둘이 같은
        코드가 되었다면 아래 두 결과가 같아졌을 것이다.
        """
        assert len(parse_startup_cert(STARTUP_OK)) == 1
        assert parse_startup_cert(CODE_90) == []
        assert SUCCESS_CODE not in STARTUP_NO_DATA_CODES


# ---------------------------------------------------------------------------
# ⑦ 90 에 부스러기가 섞여 있어도 해석하지 않는다
# ---------------------------------------------------------------------------


class TestCode90DoesNotParseLeftovers:
    """``90`` 이면 항목을 **보지 않는다**."""

    def test_incomplete_items_are_ignored(self) -> None:
        """필수 필드가 없는 항목이 섞여 있어도 오류가 나지 않는다.

        해석을 시도했다면 ``validPdDe`` 가 없어 :class:`ApiParseError` 가 났을
        것이다. 빈 목록이 나온다는 것은 아예 내려가지 않았다는 뜻이다.
        """
        assert parse_startup_cert(CODE_90_WITH_JUNK) == []

    def test_the_client_path_ignores_them_too(self) -> None:
        result, _ = _fetch_startup(CODE_90_WITH_JUNK)

        assert result.records == ()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# ② 결과코드 03 — 기존 동작 유지
# ---------------------------------------------------------------------------


class TestCode03IsUnchanged:
    """명세에 있던 ``03`` 의 동작은 그대로다."""

    def test_startup_still_returns_empty(self) -> None:
        assert parse_startup_cert(CODE_03) == []

    def test_client_path_still_returns_empty(self) -> None:
        result, _ = _fetch_startup(CODE_03)

        assert result.records == ()  # type: ignore[attr-defined]

    def test_woman_and_disabled_still_return_empty(self) -> None:
        assert parse_cert_list(CODE_03, BUSINESS_NO) == []


# ---------------------------------------------------------------------------
# ③④⑤⑥ 오류 코드 — 기존 처리 유지
# ---------------------------------------------------------------------------


class TestErrorCodesAreUnchanged:
    """인증·한도 오류는 그대로 오류다."""

    @pytest.mark.parametrize("code", ["20", "30", "32"])
    def test_auth_codes_still_raise(self, code: str) -> None:
        """20 미승인 · 30 키 오류 · 32 IP 미등록."""
        client, transport = _client(_response(code, "AUTH"))

        with pytest.raises(ApiAuthError):
            client.fetch(SOURCE_STARTUP_SMPP, BUSINESS_NO, stdr_date=None)
        # ⛔ 인증 오류는 재시도하지 않는다 — 한도만 소모한다
        assert transport.calls == 1

    def test_quota_code_still_raises(self) -> None:
        """22 일일 한도 초과."""
        client, _ = _client(_response("22", "LIMITED NUMBER OF SERVICE REQUESTS"))

        with pytest.raises(ApiQuotaError):
            client.fetch(SOURCE_STARTUP_SMPP, BUSINESS_NO, stdr_date=None)

    @pytest.mark.parametrize("code", ["01", "10", "31", "99"])
    def test_other_unknown_codes_are_still_not_guessed(self, code: str) -> None:
        """⛔ ``90`` 하나만 열었다 — 나머지 모르는 코드는 여전히 오류다.

        "알 수 없는 코드는 전부 데이터 없음" 으로 처리했다면 진짜 오류가 조용히
        묻힌다.
        """
        with pytest.raises(ApiResponseError):
            parse_startup_cert(_response(code, "무언가"))


# ---------------------------------------------------------------------------
# ⑧ 여성·장애인 회귀 — 90 을 넓히지 않았다
# ---------------------------------------------------------------------------


class TestWomanAndDisabledAreUntouched:
    """⛔ 이번 변경은 창업기업 조회에만 적용된다."""

    def test_woman_parser_still_raises_on_90(self) -> None:
        """여성기업 응답의 ``90`` 이 무슨 뜻인지는 **확인된 바가 없다**.

        확인하지 않은 API 까지 넓히면 진짜 오류를 조용히 삼킨다.
        """
        with pytest.raises(ApiResponseError) as caught:
            parse_cert_list(CODE_90, BUSINESS_NO)

        assert caught.value.code == "90"

    @pytest.mark.parametrize("source", [SOURCE_WOMAN, SOURCE_DISABLED])
    def test_client_path_still_raises_on_90(self, source: str) -> None:
        client, _ = _client(CODE_90)

        with pytest.raises(ApiResponseError):
            client.fetch(source, BUSINESS_NO, stdr_date=STDR_DATE)

    def test_the_widened_set_is_only_used_by_the_startup_parser(self) -> None:
        """넓힌 집합이 창업기업 파서에서만 쓰인다."""
        from pathlib import Path

        text = Path("src/procurement/collectors/smpp.py").read_text(encoding="utf-8")
        uses = text.count("STARTUP_NO_DATA_CODES")

        # 정의 1회 + 창업기업 파서 본문 1회 (그 밖의 문서 언급은 :data: 참조)
        assert "_check_result(root, STARTUP_NO_DATA_CODES)" in text
        assert text.count("_check_result(root, STARTUP_NO_DATA_CODES)") == 1
        assert uses >= 2

    def test_the_default_stays_the_documented_code_only(self) -> None:
        """기본값은 명세에 있는 ``03`` 하나뿐이다."""
        assert STARTUP_NO_DATA_CODES == frozenset({"03", "90"})
        assert NO_DATA_CODE == "03"
