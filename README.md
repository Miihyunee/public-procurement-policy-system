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

```bash
python -m procurement
# 또는 (패키지 설치 후)
procurement
```

## 프로젝트 구조

public-procurement-policy-system/
├── src/
│ └── procurement/
│ ├── init.py
│ ├── main.py # CLI 진입점
│ ├── core/ # 공통 기능 (Config, Logging, Exceptions 등) (예정)
│ ├── collectors/ # 정부 Open API 데이터 수집 (예정)
│ ├── matchers/ # 구매실적 ↔ 기업 데이터 매칭 (예정)
│ ├── calculators/ # 우선구매 달성률 계산 (예정)
│ ├── models/ # Pydantic 데이터 모델 (예정)
│ ├── database/ # SQLite 접근 계층 (예정)
│ └── api/ # Web UI / REST API (예정)
├── tests/
├── docs/
├── pyproject.toml
├── requirements.txt
└── requirements-dev.txt


## 개발 원칙

- Python 3.12 이상
- Type Hint 적극 사용
- 함수 및 클래스에 Docstring 작성
- 코드 품질 도구: `ruff` (lint + format), `mypy` (type check), `pytest` (test)

## 라이선스

MIT