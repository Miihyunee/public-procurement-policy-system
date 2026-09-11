"""
tests.test_api_key_is_never_exposed

**인증키가 코드 밖으로 새지 않는다**는 사실을 고정합니다.

인증키는 질의문자열(``serviceKey``)로 전송됩니다. 그래서 요청 URL 이 오류
메시지·로그·예외에 한 번이라도 섞여 나가면 **키가 그대로 노출**됩니다. 이 파일은
그 경로가 없다는 것을 검사합니다.

검사하는 사실
=============

1. 전송 계층이 오류를 낼 때 요청 URL·질의문자열을 메시지에 담지 않는다
2. 호출 계층이 오류를 낼 때도 마찬가지다
3. ``collectors`` 패키지는 로깅·표준출력을 **아예 쓰지 않는다** —
   키를 흘릴 코드가 존재하지 않는다
4. 저장소 파일 어디에도 실제 키처럼 보이는 값이 없다

.. note::
    이 파일에 쓰는 값은 ``"secret-key-value"`` 같은 명백한 더미이며 실제 키가
    아닙니다.
"""

from __future__ import annotations

import urllib.error
from collections.abc import Mapping
from pathlib import Path

import pytest

from procurement.collectors import client as client_module
from procurement.collectors import errors as errors_module
from procurement.collectors import kised as kised_module
from procurement.collectors import models as models_module
from procurement.collectors import smpp as smpp_module
from procurement.collectors import sync_service as sync_module
from procurement.collectors import transport as transport_module
from procurement.collectors.client import (
    SOURCE_STARTUP_SMPP,
    ApiKeyNotConfiguredError,
    CertificationApiClient,
)
from procurement.collectors.errors import ApiNetworkError, ApiTransportError
from procurement.collectors.models import ApiParseError
from procurement.collectors.transport import HttpResponse, UrllibTransport

#: 명백한 더미 값. 실제 인증키가 아닙니다.
FAKE_KEY = "secret-key-value-not-a-real-key"

#: 합성 사업자등록번호.
BUSINESS_NO = "1000000001"

URL = "http://apis.data.go.kr/B550598/smppKiCertInfo/getKiCertInfo"

COLLECTOR_MODULES = (
    client_module,
    errors_module,
    kised_module,
    models_module,
    smpp_module,
    sync_module,
    transport_module,
)


class _FailingTransport:
    """항상 같은 예외를 내는 전송 대역."""

    def __init__(self, error: Exception) -> None:
        """대역을 초기화합니다."""
        self._error = error

    def get(self, url: str, params: Mapping[str, str], *, timeout: float) -> HttpResponse:
        """준비된 예외를 냅니다."""
        raise self._error


def _module_path(module: object) -> Path:
    """모듈의 소스 파일 경로."""
    name = getattr(module, "__name__", "")
    return Path("src") / Path(name.replace(".", "/")).with_suffix(".py")


# ---------------------------------------------------------------------------
# 1. 전송 계층 오류 메시지
# ---------------------------------------------------------------------------


class TestTransportErrorsDoNotCarryTheKey:
    """전송 계층은 오류를 낼 때 요청 URL 을 말하지 않는다."""

    @pytest.mark.parametrize(
        "raised",
        [
            TimeoutError("timed out"),
            urllib.error.URLError("connection refused"),
            OSError("network unreachable"),
        ],
    )
    def test_error_message_has_no_key(
        self, raised: Exception, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """timeout · 네트워크 오류 메시지에 키가 들어가지 않는다."""

        def _boom(*args: object, **kwargs: object) -> None:
            raise raised

        monkeypatch.setattr(urllib.request, "urlopen", _boom)

        with pytest.raises(ApiTransportError) as caught:
            UrllibTransport().get(URL, {"serviceKey": FAKE_KEY}, timeout=1.0)

        assert FAKE_KEY not in str(caught.value)
        assert "serviceKey" not in str(caught.value)

    def test_http_error_message_has_no_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """서버가 준 본문만 담고, 요청 URL 은 담지 않는다."""

        def _boom(*args: object, **kwargs: object) -> None:
            raise urllib.error.HTTPError(
                url=f"{URL}?serviceKey={FAKE_KEY}",
                code=500,
                msg="Server Error",
                hdrs=None,  # type: ignore[arg-type]
                fp=None,
            )

        monkeypatch.setattr(urllib.request, "urlopen", _boom)

        with pytest.raises(ApiTransportError) as caught:
            UrllibTransport().get(URL, {"serviceKey": FAKE_KEY}, timeout=1.0)

        assert FAKE_KEY not in str(caught.value)


# ---------------------------------------------------------------------------
# 2. 호출 계층 오류 메시지
# ---------------------------------------------------------------------------


class TestClientErrorsDoNotCarryTheKey:
    """호출 계층도 키를 되풀이하지 않는다."""

    def test_parse_error_has_no_key(self) -> None:
        """응답이 깨졌을 때의 메시지에 키가 없다."""

        class _Broken:
            def get(self, url: str, params: Mapping[str, str], *, timeout: float) -> HttpResponse:
                return HttpResponse(status=200, body="<response><unclosed>")

        client = CertificationApiClient(smpp_api_key=FAKE_KEY, transport=_Broken())

        with pytest.raises(ApiParseError) as caught:
            client.fetch(SOURCE_STARTUP_SMPP, BUSINESS_NO, stdr_date=None)

        assert FAKE_KEY not in str(caught.value)

    def test_transport_error_has_no_key(self) -> None:
        client = CertificationApiClient(
            smpp_api_key=FAKE_KEY,
            transport=_FailingTransport(ApiNetworkError("외부 API 에 연결할 수 없습니다")),
            max_attempts=1,
        )

        with pytest.raises(ApiTransportError) as caught:
            client.fetch(SOURCE_STARTUP_SMPP, BUSINESS_NO, stdr_date=None)

        assert FAKE_KEY not in str(caught.value)

    def test_missing_key_message_names_the_variable_not_a_value(self) -> None:
        """키가 없다는 안내는 **환경변수 이름만** 말한다."""
        client = CertificationApiClient(smpp_api_key=None)

        with pytest.raises(ApiKeyNotConfiguredError) as caught:
            client.fetch(SOURCE_STARTUP_SMPP, BUSINESS_NO, stdr_date=None)

        message = str(caught.value)
        assert "SMPP_API_KEY" in message
        assert FAKE_KEY not in message


# ---------------------------------------------------------------------------
# 3. 로깅 경로 자체가 없다
# ---------------------------------------------------------------------------


class TestNoLoggingPathExists:
    """``collectors`` 는 로깅·표준출력을 쓰지 않는다.

    "키를 로그에서 지운다" 보다 확실한 방법은 **로그를 남기는 코드가 없는
    것**이다. 나중에 로깅을 넣게 되면 이 테스트가 먼저 실패해서, 키를 어떻게
    가릴지 결정한 뒤에 넣도록 강제한다.
    """

    @pytest.mark.parametrize("module", COLLECTOR_MODULES, ids=lambda m: m.__name__)
    def test_module_does_not_log_or_print(self, module: object) -> None:
        text = _module_path(module).read_text(encoding="utf-8")

        assert "import logging" not in text
        assert "getLogger" not in text
        assert "print(" not in text


# ---------------------------------------------------------------------------
# 4. 저장소에 실제 키처럼 보이는 값이 없다
# ---------------------------------------------------------------------------


class TestRepositoryHasNoKeyLikeValue:
    """소스·테스트·문서에 키처럼 보이는 값이 없다."""

    @pytest.mark.parametrize("directory", ["src", "tests", "docs"])
    def test_no_long_literal_next_to_a_key_name(self, directory: str) -> None:
        """``serviceKey = "긴문자열"`` 형태가 없다.

        공공데이터포털 인증키는 수십 자의 무작위 문자열이다. 키 이름 바로 뒤에
        긴 리터럴이 붙어 있으면 실제 값일 가능성이 높다.
        """
        import re

        pattern = re.compile(
            r"(SMPP_API_KEY|STARTUP_API_KEY|serviceKey|service_key|api_?key)"
            r"\s*[=:]\s*[\"'][A-Za-z0-9%+/=_-]{20,}[\"']",
            re.IGNORECASE,
        )

        offenders = [
            str(path)
            for path in Path(directory).rglob("*")
            if path.is_file()
            and path.suffix in {".py", ".md", ".json", ".yaml", ".yml", ".toml"}
            and pattern.search(path.read_text(encoding="utf-8", errors="ignore"))
        ]

        assert offenders == []
