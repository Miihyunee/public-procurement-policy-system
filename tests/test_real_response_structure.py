"""
tests.test_real_response_structure

**실제 ``00`` 응답의 구조**를 네 API 에서 한 번에 확인합니다.

왜 필요한가
===========

지금까지 확인된 실제 응답은 "확인서 없음"(``03``/``90``) 뿐입니다. 정상 응답이
어떤 모양인지는 **활용가이드로만** 알고 있고, 실제로 받아본 적이 없습니다.
파서가 명세를 옳게 읽었는지는 실제 ``00`` 응답을 봐야 확정됩니다.

이 파일은 그 확인을 **한 번의 실행으로** 끝내기 위한 것입니다.

.. warning::
    ⛔ **값을 출력하지 않습니다.** 인증키·사업자번호는 물론, 확인서의 유효기간·
    기업명·품명번호 같은 값도 담지 않습니다. 화면에 나가는 것은 **결과코드 ·
    페이지 정보 · 필드 이름 · 항목 수** 뿐입니다.

.. warning::
    ⛔ **실제 응답 XML 을 파일로 저장하지 않습니다.** 메모리에서만 봅니다.

.. note::
    키가 없으면 **skip**, 네트워크에 닿지 못해도 **skip** 입니다. "연결이 안
    된다" 와 "응답이 명세와 다르다" 는 다른 사실이므로 섞지 않습니다.

.. note::
    ⛔ 유효기간을 어떤 날짜와도 비교하지 않습니다. 이 파일은 **API 가 무엇을
    돌려주는가**만 봅니다.
"""

from __future__ import annotations

from datetime import date
from xml.etree import ElementTree

import pytest

from procurement.collectors.client import (
    DEFAULT_NUM_OF_ROWS,
    DEFAULT_PAGE_NO,
    URL_DIRECT_PRODUCTION,
    URL_DISABLED,
    URL_STARTUP_SMPP,
    URL_WOMAN,
)
from procurement.collectors.errors import ApiTransportError
from procurement.collectors.smpp import NO_DATA_CODE, SUCCESS_CODE
from procurement.collectors.transport import UrllibTransport
from procurement.core.config import settings

#: 시험용 기준일자.
#:
#: ⛔ **업무 판정 기준일이 아닙니다.** ``stdrDate`` 는 "유효확인서를 확인할
#: 기준일자" 라는 **조회 조건**이며, 달성률 계산의 판정 기준일
#: (``Policy.evaluation_basis``)과 다릅니다. 어느 날짜를 조회에 쓸지는 아직
#: 확정되지 않았습니다(``DECISIONS.md`` §0.4).
TEST_STDR_DATE = date(2026, 8, 1)

#: 페이지 관련 응답 필드 — 값 자체가 구조 정보이므로 그대로 봅니다.
PAGE_FIELDS = ("totalCount", "numOfRows", "pageNo")

#: API 별 명세상 **항목 필수 필드** (활용가이드 "c) 응답 메시지 명세").
WOMAN_DISABLED_FIELDS = ("certSeCode", "issuInstt", "validPdBeginDe", "validPdEndDe", "certfcDe")
STARTUP_FIELDS = ("entrpsNm", "bsnmNo", "minduty", "rprsntvNm", "adres", "validPdDe")
DIRECT_FIELDS = ("certSeCode", "validPdBeginDe", "validPdEndDe", "certfcDe", "detailPrdnmNo")

_REAL_KEY = (settings.SMPP_API_KEY or "").strip()
_REAL_BUSINESS_NO = (settings.SMPP_TEST_BUSINESS_NO or "").strip()


def _describe(xml_text: str, expected: tuple[str, ...]) -> str:
    """응답의 **구조만** 요약합니다 — 값은 담지 않습니다."""
    root = ElementTree.fromstring(xml_text)

    code = next((n.text for n in root.iter("resultCode") if n.text), "(없음)")
    message = next((n.text for n in root.iter("resultMsg") if n.text), "(없음)")
    lines = [f"resultCode={code}", f"resultMsg={message}"]

    for tag in PAGE_FIELDS:
        node = next((n for n in root.iter(tag)), None)
        lines.append(f"{tag}={node.text if node is not None else '(없음)'}")

    items = list(root.iter("item"))
    lines.append(f"item 수={len(items)}")
    if items:
        first = items[0]
        lines.append(f"있는 명세 필드={[t for t in expected if first.find(t) is not None]}")
        lines.append(f"없는 명세 필드={[t for t in expected if first.find(t) is None]}")
        lines.append(f"명세에 없는 필드={sorted({c.tag for c in first} - set(expected))}")
    return " | ".join(lines)


def _fetch_raw(url: str, *, with_stdr_date: bool) -> str:
    """실제 응답 본문을 받아옵니다.

    ``totalCount`` 같은 구조 정보를 보려면 파서를 거치기 전의 원문이 필요합니다.
    요청 파라미터는 호출 계층과 **같은 이름·같은 형식**으로 만듭니다.
    """
    params = {
        "serviceKey": _REAL_KEY,
        "bsnmNo": _REAL_BUSINESS_NO,
        "pageNo": str(DEFAULT_PAGE_NO),
        "numOfRows": str(DEFAULT_NUM_OF_ROWS),
    }
    if with_stdr_date:
        params["stdrDate"] = TEST_STDR_DATE.strftime("%Y%m%d")

    try:
        response = UrllibTransport().get(url, params, timeout=15.0)
    except ApiTransportError as exc:
        pytest.skip(f"네트워크로 API 에 닿지 못했습니다: {type(exc).__name__}")
    if response.status != 200:
        pytest.fail(f"HTTP status={response.status} (본문은 출력하지 않습니다)")
    return response.body


def _check(label: str, url: str, expected: tuple[str, ...], *, with_stdr_date: bool) -> None:
    """한 API 의 실제 응답 구조를 확인합니다."""
    body = _fetch_raw(url, with_stdr_date=with_stdr_date)
    summary = _describe(body, expected)
    root = ElementTree.fromstring(body)
    code = next((n.text for n in root.iter("resultCode") if n.text), None)

    if code != SUCCESS_CODE:
        # ⛔ "확인서 없음" 을 "구조 확인함" 으로 바꿔 읽지 않는다.
        if code in {NO_DATA_CODE, "90"}:
            pytest.skip(f"[{label}] 확인서 없음 — 정상 구조 미확인 | {summary}")
        pytest.fail(f"[{label}] 문서화되지 않은 결과코드 | {summary}")

    items = list(root.iter("item"))
    assert items, f"[{label}] resultCode 는 00 인데 항목이 없습니다 | {summary}"
    for index, item in enumerate(items):
        missing = [tag for tag in expected if item.find(tag) is None]
        assert not missing, f"[{label}] {index}번째 항목에 필수 필드 없음: {missing} | {summary}"

    # 확인에 성공해도 구조 요약은 남긴다 — 이것이 이 시험의 산출물이다.
    pytest.fail(f"[{label}] ✅ 정상 00 응답 구조 확인 (아래 요약을 보고하세요) | {summary}")


@pytest.mark.skipif(
    not (_REAL_KEY and _REAL_BUSINESS_NO),
    reason=(
        "실제 API 호출 시험은 SMPP_API_KEY 와 SMPP_TEST_BUSINESS_NO 가 "
        "둘 다 설정된 환경에서만 수행합니다(.env 또는 환경변수). "
        "값이 없으면 실패가 아니라 건너뜁니다."
    ),
)
class TestRealResponseStructure:
    """네 API 의 실제 응답 구조를 확인합니다.

    .. note::
        구조를 확인한 경우에도 **일부러 실패로 끝납니다.** pytest 는 통과한
        시험의 메시지를 보여주지 않는데, 이 시험의 목적은 바로 그 요약을
        사람이 읽는 것이기 때문입니다. "실패" 는 결함이 아니라 **보고서**입니다.
    """

    def test_woman(self) -> None:
        _check("여성기업", URL_WOMAN, WOMAN_DISABLED_FIELDS, with_stdr_date=True)

    def test_disabled(self) -> None:
        _check("장애인기업", URL_DISABLED, WOMAN_DISABLED_FIELDS, with_stdr_date=True)

    def test_startup(self) -> None:
        # 이 API 는 명세상 stdrDate 가 없다.
        _check("창업기업", URL_STARTUP_SMPP, STARTUP_FIELDS, with_stdr_date=False)

    def test_direct_production(self) -> None:
        _check("직접생산", URL_DIRECT_PRODUCTION, DIRECT_FIELDS, with_stdr_date=True)
