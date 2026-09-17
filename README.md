# 공공기관 우선구매 정책 달성률 자동 계산 시스템

공공기관 구매 담당자가 **정부 우선구매 정책의 달성률을 자동으로 계산**하는 데스크톱
프로그램입니다.

정책별 기업 명단을 정부 Open API 또는 파일로 등록하고, 기관 회계시스템에서 나온
구매실적 엑셀을 그대로 올리면, 사업자등록번호로 맞춰 정책별 실적과 달성률을
산출합니다. **폐쇄망 환경에서 담당자 PC 단독으로 동작**하며 데이터는 PC 밖으로
나가지 않습니다.

---

## 한눈에 보기

| 항목 | 값 |
|---|---|
| 대상 정책 | **9종** (중소·여성·장애인·창업·사회적기업·사회적협동조합·장애인표준사업장·자활용사촌·녹색제품) |
| 백엔드 | Python 3.12 · FastAPI · SQLite (API 43개) |
| 화면 | HTML/CSS/JS 단일 파일 — **외부 라이브러리·CDN 0** |
| 배포 | Electron + PyInstaller → Windows 설치파일 1개 |
| 테스트 | pytest **4,807건** 전량 통과 |
| 정적 분석 | ruff 0건 · mypy strict 0건 (130개 파일) |
| 설치본 통합 검증 | 15개 항목 전량 통과 |

---

## 이 시스템이 지키는 원칙

업무 시스템이라 **추측하지 않는 것**을 설계 원칙으로 두었습니다.

- **「확인하지 못함」과 「없음」을 구분합니다.** 명단 조회에 실패한 정책은
  「조회불가」로 표시하고 0건으로 바꿔 적지 않습니다.
- **근거 없는 값을 채우지 않습니다.** 목표비율은 담당자가 등록하며, 기준이 확정되지
  않은 정책은 「산출 기준 미정」으로 두고 0% 로 계산하지 않습니다.
- **구매유형(물품·용역·공사)을 자동 판정하지 않습니다.** 적요 키워드나 거래처명으로
  추정하면 근거 없는 숫자가 되므로 담당자가 확정합니다.
- **지우지 않고 대체합니다.** 같은 기간을 다시 올리면 이전 자료는 삭제되지 않고
  이력에 「대체됨」으로 남습니다.
- **모든 숫자를 되짚을 수 있습니다.** 업로드·교체·확정·미적재 이력이 전부 남아
  「어느 파일의 몇 번째 행에서 왔는가」를 추적할 수 있습니다.

---

## 주요 기능

### 1. 기업 명단 등록

- 공공구매종합정보망(SMPP) · 창업기업 정보 Open API 연동 조회
- API 를 쓸 수 없는 정책은 엑셀 업로드로 등록 — 방식은 정책마다 선택
- 조회 실패 시 「조회불가」 표시

### 2. 구매실적 업로드

- 회계시스템 **원본 엑셀을 가공 없이** 업로드 (머리글 별칭 호환)
- 자동 제외 — 집계(소계·합계) 행 · 앞선 해 결의 건 · 대상 기간 외 건
- 제외 사유를 행 단위로 화면·CSV 확인
- 연 단위 기본, 월 단위 병행
- 재업로드 시 확인 후 논리 교체(이전 배치는 `SUPERSEDED` 이력으로 보존)

### 3. 매칭 · 달성률 계산

- 사업자등록번호 기준 일괄 매칭
- 정책별로 다른 판정 기준일(결의일자·계약일 등)을 **규칙 엔진**으로 분리
- 연도별·정책별 목표비율 관리, 목표 대비 달성률 산출

### 4. 구매유형 검토

- 담당자 확정 방식, 검토 진행률·잔여 건수 관리
- 확정 / 확정취소 이력과 확정자·시각·메모 보존

### 5. 대시보드

- 「지금 하실 일」을 먼저 제시 — 미등록·보류 정책을 상단에 모아 해당 화면으로 연결
- 정책별 목표·실적·달성률·상태 일괄 조회
- 색상 단독 표기 배제(기호·문자 병기)로 접근성 확보

---

## 실행 방법

### 담당자용 (설치형)

Windows 설치파일을 실행하면 별도 준비 없이 바로 쓸 수 있습니다. Python·Node·서버·
계정이 필요 없습니다. 자세한 사용법은 [`docs/USER_MANUAL.md`](docs/USER_MANUAL.md)
(40쪽, 프로그램 안에서도 PDF 로 내려받을 수 있습니다)를 보십시오.

### 개발자용

```bash
git clone https://github.com/Miihyunee/public-procurement-policy-system.git
cd public-procurement-policy-system

python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate

pip install -r requirements-dev.txt
pip install -e .
```

```bash
python -m procurement init     # DB 생성 + 테이블 + 기본 정책 등록 + 상태 점검
python -m procurement run      # FastAPI 개발 서버 실행
```

- 화면 — <http://127.0.0.1:8000/>
- API 문서(Swagger) — <http://127.0.0.1:8000/docs>

`init` 은 **몇 번을 실행해도 안전**하며 기존 데이터를 지우지 않습니다.

| 명령 | 설명 |
|---|---|
| `python -m procurement init` | DB·테이블 생성, 기본 정책 등록, 상태 점검 |
| `python -m procurement run` | FastAPI 개발 서버 실행 |
| `python -m procurement health` | 초기화 상태만 점검 |

### 데스크톱 앱 빌드 (Windows 전용)

```powershell
python -m PyInstaller packaging/procurement-backend.spec   # 백엔드 → dist/procurement
npx electron-builder --win                                  # 설치파일 → release/
```

PyInstaller 는 크로스 컴파일을 하지 않으므로 두 빌드 모두 Windows 에서 실행해야
합니다. 자세한 절차는 [`docs/STEP166_UPLOAD_VERIFICATION.md`](docs/STEP166_UPLOAD_VERIFICATION.md)
와 [`docs/WINDOWS_BUILD_SETUP.md`](docs/WINDOWS_BUILD_SETUP.md) 에 있습니다.

---

## 검증

```bash
pytest -q                       # 4,807건
ruff check src tests scripts
cd src && mypy -p procurement
node scripts/verify-backend.js  # 설치본 생명주기 15개 항목 (외부 리소스 미사용 포함)
```

테스트 코드가 제품 코드의 2배 이상입니다(제품 약 2.9만 줄 / 테스트 약 6.3만 줄).
업무규칙 하나하나를 테스트로 고정했고, **테스트를 지우거나 건너뛰어 통과시키지
않았습니다.**

---

## 구조

```text
Electron 화면 (index.html)
      │  fetch (127.0.0.1 전용)
      ▼
   FastAPI
      ▼
ApiService → DataService → Calculator → Rule Engine → Repository → SQLite
```

```text
public-procurement-policy-system/
├── src/procurement/
│   ├── app.py            # FastAPI 앱 + 의존성 조립(합성 루트)
│   ├── core/             # 설정·공통 업무 로직
│   ├── collectors/       # 정부 Open API 수집 (SMPP · 창업기업)
│   ├── uploads/          # 엑셀 검증·매핑·표준 양식
│   ├── importers/        # 배치 적재와 논리 교체
│   ├── matchers/         # 구매실적 ↔ 기업 명단 매칭
│   ├── calculators/      # 달성률 계산 + Rule Engine
│   ├── reviews/          # 구매유형 검토
│   ├── dashboard/        # 대시보드 요약
│   ├── database/         # SQLite 접근 계층 + Bootstrap
│   └── web/static/       # 화면 (단일 파일)
├── electron/             # 데스크톱 셸
├── packaging/            # PyInstaller 스펙
├── scripts/              # 검증·빌드 보조 스크립트
├── tests/                # 4,807건
└── docs/                 # 설계·업무규칙·의사결정 기록
```

---

## 문서

| 문서 | 내용 |
|---|---|
| [`docs/USER_MANUAL.md`](docs/USER_MANUAL.md) | 담당자용 사용 설명서 (40쪽) |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | 업무규칙 의사결정 기록 — 무엇을 왜 그렇게 정했는가 |
| [`docs/SYSTEM_ARCHITECTURE.md`](docs/SYSTEM_ARCHITECTURE.md) | 시스템 구조 |
| [`docs/POLICY_DEFINITION.md`](docs/POLICY_DEFINITION.md) | 정책별 정의와 판정 기준 |
| [`docs/INSTALL_GUIDE.md`](docs/INSTALL_GUIDE.md) | 설치 안내 |
| [`docs/UI_REDESIGN_STEP163.md`](docs/UI_REDESIGN_STEP163.md) | 화면 개편 As-Is / To-Be 분석 |
| [`docs/THIRD_PARTY_LICENSES.md`](docs/THIRD_PARTY_LICENSES.md) | 사용 라이브러리 라이선스 |

---

## 보안 · 데이터 취급

- 구매실적과 기업 명단은 **담당자 PC 안에만** 저장되며 외부로 전송되지 않습니다.
- **API 인증키는 저장소·설치파일에 들어 있지 않습니다.** 기관이 직접 발급받아
  사용자 데이터 폴더의 `.env` 에 넣습니다.
- 화면은 외부 리소스(CDN·웹폰트)를 전혀 쓰지 않아 인터넷이 끊긴 폐쇄망에서도
  동작합니다. 글꼴도 설치본에 동봉되어 있습니다.

---

## 개발 원칙

- Python 3.12 이상, Type Hint 와 Docstring 필수
- 품질 도구 — `ruff`(lint + format) · `mypy`(strict) · `pytest`
- 확정되지 않은 업무규칙은 코드로 확정하지 않고 [`docs/DECISIONS.md`](docs/DECISIONS.md)
  에 미확정으로 남깁니다.

## 라이선스

MIT
