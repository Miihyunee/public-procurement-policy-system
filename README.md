# Public Procurement Policy System

공공기관 구매 담당자가 **정부 우선구매 정책의 달성률을 자동으로 계산**할 수 있는 시스템입니다.

정부기관이 제공하는 기업 데이터를 자동 수집하고, 기관에서 업로드한 구매실적 Excel과 매칭하여
중소기업, 여성기업, 장애인기업, 창업기업, 녹색제품 등의 우선구매 실적을 계산합니다.

---

## 요구 사항

- Python 3.12 이상

## 개발 환경 설정

```bash
git clone <repository-url>
cd public-procurement-policy-system

python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

pip install -r requirements-dev.txt
pip install -e .
```

## 실행

처음 받은 저장소도 아래 두 명령이면 바로 동작합니다.

```bash
python -m procurement init     # 1) DB 생성 + 테이블 + 기본 정책 등록 + 상태 점검
python -m procurement run      # 2) FastAPI 개발 서버 실행
```

- **Dashboard 화면(브라우저): `http://127.0.0.1:8000/`**
- 대시보드 요약 API: `GET http://127.0.0.1:8000/dashboard/summary`
- 데이터 적재 현황 API: `GET http://127.0.0.1:8000/dashboard/data-status`
- Swagger(OpenAPI) 문서: `http://127.0.0.1:8000/docs`

> 화면 우측 상단의 `DEMO / SAMPLE DATA` 배지는 설정값 `DATA_MODE` 를 그대로
> 표시합니다. 기본값은 `demo` 이며, 실제 운영 데이터를 적재한 뒤에만
> `DATA_MODE=operational` 로 바꿉니다.
>
> ⚠️ **연도 선택은 아직 조회 조건이 아닙니다.** 기간 필터·연도별 집계는 미구현
> 상태이며(D-23 ~ D-27 확정 대기), 화면의 모든 수치는 **전체 데이터 기준**입니다.

### CLI 명령

| 명령 | 설명 |
|---|---|
| `python -m procurement init` | DB·테이블 생성, 기본 정책 등록, 상태 점검 |
| `python -m procurement run` | FastAPI 개발 서버 실행 |
| `python -m procurement health` | 초기화 상태만 점검 |

주요 옵션 — `init`/`health`: `--db PATH`, `init`: `--no-seed`,
`run`: `--host`, `--port`.
`init` 은 **몇 번을 실행해도 안전**하며 기존 데이터를 지우지 않습니다.

### 초기 등록되는 정책

`init` 은 MVP 정책 5종을 등록합니다.

| 정책 코드 | 정책명 | 판정 기준일 |
|---|---|---|
| `SMALL_BUSINESS` | 중소기업 | 지급일 |
| `WOMAN` | 여성기업 | 지급일 |
| `DISABLED` | 장애인기업 | 지급일 |
| `STARTUP` | 창업기업 | **계약일** |
| `GREEN` | 녹색제품 | 지급일 |

> **목표율(`target_rate`)은 등록하지 않습니다(NULL).**
> 공식 근거가 확인되지 않은 목표율을 임의로 넣지 않기 위한 결정입니다.
> 목표율이 없는 정책은 대시보드 계산에서 제외되므로, 초기 `/dashboard/summary`
> 응답의 `policies` 는 빈 목록입니다. 목표율을 등록하면 자동으로 포함됩니다.

### DB 경로

기본값은 `database/procurement.db` 이며, `.env` 또는 환경변수
`DATABASE_PATH` 로 변경할 수 있습니다.

## 프로젝트 구조

```text
public-procurement-policy-system/
├── src/
│   └── procurement/
│       ├── __init__.py
│       ├── __main__.py       # CLI 진입점 (init / run / health)
│       ├── app.py            # FastAPI 앱 + 의존성 조립 — 구현 완료
│       ├── core/             # 공통 기능 (Config 등) — 구현 완료
│       ├── collectors/       # 정부 Open API 데이터 수집 (예정)
│       ├── matchers/         # 구매실적 ↔ 기업 데이터 매칭 — 구현 완료
│       ├── calculators/      # 우선구매 달성률 계산 + Rule Engine — 구현 완료
│       ├── dashboard/        # 대시보드 요약 데이터 서비스 — 구현 완료
│       ├── models/           # 도메인 데이터 모델 (dataclass) — 구현 완료
│       ├── database/         # SQLite 접근 계층 + Bootstrap — 구현 완료
│       └── api/              # REST API 응답 계층 — 구현 완료
├── tests/
├── docs/
├── pyproject.toml
├── requirements.txt
└── requirements-dev.txt
```

## 개발 원칙

- Python 3.12 이상
- Type Hint 적극 사용
- 함수 및 클래스에 Docstring 작성
- 코드 품질 도구: `ruff` (lint + format), `mypy` (type check), `pytest` (test)

## 라이선스

MIT