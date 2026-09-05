# Windows 배포 구조 조사 및 패키징 계획 (STEP 124)

| 항목 | 값 |
|---|---|
| 작성 | 2026-09-05 · STEP 124 |
| 상태 | **조사 완료 · 코드 변경 없음** |
| 목적 | 고객 PC 에 설치해 쓰는 Windows 프로그램으로 내보내기 위한 **사전 조사** |
| ⛔ 아닌 것 | EXE 를 완성하는 단계가 아니다. 패키징 설정을 확정 반영하지 않았다. |

> 이 문서는 **추측이 아니라 실제 코드·실행 결과**를 적은 것이다. 확인하지 못한
> 것은 「확인 못 함」으로 적었다.

---

## 1. 현재 실행 구조 — 이미 데스크톱 앱 뼈대가 있다

조사해 보니 **Electron 껍데기가 이미 들어와 있었다.** 새로 설계할 것이 아니라
이어 붙이면 되는 상태다.

```
Electron main process (electron/main.js)
   ├─ (spawn) Python 백엔드 ── FastAPI ── SQLite
   │            electron/backend.js
   └─ BrowserWindow → http://127.0.0.1:<빈 포트>/
```

| 무엇 | 어디 |
|---|---|
| 데스크톱 진입점 | `package.json` `main` → `electron/main.js` |
| 백엔드 수명 관리 | `electron/backend.js` |
| 백엔드 진입점 | `procurement.__main__:main` (`[project.scripts] procurement`) |
| 서버 실행 | `python -m procurement run --host 127.0.0.1 --port <n>` |
| FastAPI 앱 | `procurement.app:create_app()` |
| 화면 | `procurement/web/static/index.html` (한 파일 · 서버가 그대로 읽어 반환) |

**개발환경 실행 방법**

```
1. python -m procurement init          # DB 생성 + 정책 seed
2. python -m procurement run           # 127.0.0.1:8000
3. (데스크톱) npm start                # electron . → 창이 뜨고 백엔드를 직접 띄운다
```

### 1.1 구조 판정 — 지시서 §5 의 A 와 B 사이

```
EXE → Electron 창 → (내부에서 띄운) FastAPI → 창 안에 화면
```

**A(브라우저 필요)가 아니다.** 화면은 Electron 창 안에서 뜨므로 고객이 브라우저
주소창에 무엇을 칠 필요가 없다. 포트도 **OS 에게 빈 포트를 받아** 쓰므로 8000 이
막혀 있어도 충돌하지 않는다(`findFreePort()`).

⛔ 이 구조를 이번에 바꾸지 않았다.

---

## 2. 실행 확인 (PoC) — 무엇을 했고 무엇을 못 했나

### 2.1 ⛔ Windows EXE 는 이 환경에서 만들 수 없다

| 확인 | 결과 |
|---|---|
| 이 컨테이너 | **Linux x86_64** |
| PyInstaller | **미설치** |
| Electron 바이너리 | **미설치**(`node_modules` 없음) · 화면 장치도 없음 |

PyInstaller 는 **크로스 컴파일을 하지 않는다** — Windows EXE 는 Windows 에서
빌드해야 한다. 그래서 지시서 §25 의 EXE PoC 는 **수행 불가**다.
⛔ 「만들었다」고 적지 않는다.

### 2.2 대신 확인한 것 — EXE 에서 실제로 깨질 만한 것들

EXE 의 진짜 위험은 「Python 이 없다」가 아니라 **작업 디렉터리가 달라지는 것**과
**데이터를 어디에 쓰느냐**다. 그 둘은 Linux 에서도 그대로 재현된다.

`cwd=/` 에서, DB 위치를 환경변수로 딴 곳에 두고 실행했다.

```
cd / && DATABASE_PATH=<임시>/userdata/database python -m procurement init
                                            run --host 127.0.0.1 --port 8931
```

| # | 항목 | 결과 |
|---|---|---|
| 1 | Python 없는 환경 가정 | ⛔ **확인 못 함**(EXE 를 못 만듦) |
| 2 | 프로그램 시작 (cwd=`/`) | ✅ 정상 |
| 3 | FastAPI 서버 시작 | ✅ `Uvicorn running on 127.0.0.1:8931` |
| 4 | 웹 UI 접근 | ✅ HTTP 200 · 화면 내용 정상 |
| 5 | SQLite DB 생성/접근 | ✅ 지정한 폴더에 `procurement.db` 생성 |
| 6 | Excel 업로드 | ✅ HTTP 200 (합성 파일) |
| 7 | DB 저장 | ✅ `stored=true` |
| 8 | 검색 | ✅ HTTP 200 |
| 9 | 정책 필터 | ✅ 여성기업 2건 |
| 10 | 구매유형 검토 | ✅ 규칙 자동판정 동작 |
| 11 | 대시보드 | ✅ 총액·정책별 실적 정상 |
| 12 | CSV export | ✅ HTTP 200 |
| 13 | 프로그램 종료 | ✅ SIGTERM → `Application shutdown complete` · **잔존 프로세스 없음** |

추가로 **화면이 외부 리소스를 하나도 부르지 않음**을 확인했다(`http(s)://` 참조
0건 — SVG 네임스페이스 제외). **인터넷 없이 동작한다.**

⛔ 합성 데이터만 썼고, 프로젝트 파일·DB 는 건드리지 않았다(`git status` 비어 있음).

---

## 3. Python 의존성

| 항목 | 값 |
|---|---|
| Python | **3.12 이상** (`requires-python = ">=3.12"`) |
| 런타임 의존성 | `pydantic` · `pydantic-settings` · `python-dotenv` · `fastapi` · `uvicorn[standard]` · `openpyxl` |
| ⛔ 없는 것 | **pandas 없음 · numpy 없음** — 무거운 native 스택이 없다 |
| HTTP 클라이언트 | **표준 라이브러리 `urllib`** (`httpx` 는 시험 전용) |

### 3.1 native 확장 (EXE 에서 챙겨야 할 것)

| 패키지 | native | Windows 비고 |
|---|---|---|
| `pydantic_core` | ✅ | 필수. 휠 제공 |
| `uvloop` | ✅ | **Windows 에서 설치되지 않는다** — uvicorn 이 알아서 안 쓴다 |
| `httptools` · `watchfiles` · `websockets` | ✅ | `uvicorn[standard]` 가 끌고 온다 |
| `openpyxl` | ❌ 순수 파이썬 | 폐쇄망에서도 휠 하나면 된다 |

⚠️ `watchfiles` 는 **자동 리로드용**이라 배포본에 필요 없다. `uvicorn[standard]`
대신 기본 `uvicorn` 으로 줄이면 번들이 가벼워지지만, **의존성 변경은 PM 승인
사항**이라 이번에 바꾸지 않았다.

---

## 4. 🔴 EXE 에서 깨질 곳 — 필수 수정 후보 2건

> ⛔ **고치지 않았다.** 지시서 §27 에 따라 보고만 한다.

### 4.1 `_PROJECT_ROOT` 가 소스 위치를 기준으로 잡힌다

```
[필수 수정 후보 ①]
파일   : src/procurement/core/config/settings.py:28
문제   : _PROJECT_ROOT = Path(__file__).resolve().parents[4]
현재   : 개발환경에서는 프로젝트 루트가 정확히 잡힌다(확인함).
EXE    : PyInstaller 로 묶으면 이 파일이 임시 폴더(_MEIPASS) 안에 놓이고,
         패키지 깊이도 달라져 parents[4] 가 **엉뚱한 상위 폴더**를 가리킨다.
         DATABASE_PATH·DATA_PATH·LOG_PATH 의 기본값이 전부 그 위에 얹힌다.
왜     : 기본값이 임시 폴더면 프로그램을 끌 때 고객 데이터가 사라진다.
최소안 : 실행 형태를 구분해 기준 폴더를 정한다.
         - 번들 실행(sys.frozen)이면 → 사용자 데이터 폴더
         - 아니면 → 지금 그대로
영향   : 기본값만 바뀐다. 환경변수로 지정하면 지금도 그 값이 이긴다.
시험   : 번들/비번들 양쪽에서 기본 경로가 무엇이 되는지 고정.
```

⚠️ **Electron 경로에서는 이미 막혀 있다.** `electron/backend.js` 가
`DATABASE_PATH` 를 사용자 데이터 폴더로 **명시해서** 넘긴다. 위 문제는 백엔드
EXE 를 **단독 실행**할 때 드러난다.

### 4.2 `.env` 를 현재 작업 디렉터리에서 찾는다

```
[필수 수정 후보 ②]
파일   : src/procurement/core/config/settings.py:38
문제   : SettingsConfigDict(env_file=".env")  ← 상대경로
현재   : 프로젝트 루트에서 실행하므로 잘 찾는다.
EXE    : 바탕화면 바로가기로 켜면 작업 디렉터리가 임의라 .env 를 못 찾는다.
왜     : API 키(SMPP_API_KEY 등)를 파일로 주려면 찾을 자리가 정해져야 한다.
최소안 : 사용자 데이터 폴더의 .env 를 함께 보게 한다.
영향   : 환경변수로 넘기는 지금 방식은 그대로 동작한다.
시험   : 작업 디렉터리를 바꿔 실행해도 설정을 찾는지.
```

### 4.3 그 밖에 작업 디렉터리에 기대는 코드 — **없다**

소스 전체를 훑었다.

| 검사 | 결과 |
|---|---|
| `os.getcwd()` · `Path.cwd()` · `Path(".")` | **0건** |
| 상대경로 `open("...")` | **0건** |
| 화면 파일 경로 | `Path(__file__).resolve().parent / "static" / "index.html"` — **파일 기준** ✅ |
| 업로드 파일 경로 | 화면이 파일 선택 대화상자로 받은 **절대경로**를 그대로 넘긴다 ✅ |
| CSV 내보내기 | `StreamingResponse` — **디스크에 쓰지 않는다** ✅ |

⭐ 화면·업로드·내보내기 모두 작업 디렉터리와 무관하다. 남은 건 위 두 건뿐이다.

---

## 5. 데이터가 어디에 저장되는가

| 데이터 | 저장 위치 | 방식 | EXE 배포 시 주의 |
|---|---|---|---|
| 지출 데이터 | `purchase` · `import_batch` | **DB** | 사용자 데이터 폴더로 분리 필요 |
| 기업정보 | `company` | **DB** | 〃 |
| 인증정보 | `certification` | **DB** | 〃 |
| 인증 source version | `policy_company_source` | **DB** | 〃 |
| 정책 목표율 | `policy_target` | **DB** | 〃 |
| 구매유형 검토 | `purchase_review` | **DB** | 〃 |
| review history | `purchase_review_history` | **DB** | 〃 |
| 업로드/미적재 이력 | `import_batch` · `import_rejection` | **DB** | 〃 |

⭐ **전부 SQLite 파일 하나(`procurement.db`)에 들어간다.** 별도 파일로 흩어지는
사용자 데이터가 없다 — 백업·이전이 파일 하나 복사로 끝난다.

프로그램이 디스크에 쓰는 곳은 그 DB 파일과, 사용자가 **직접 위치를 고른** 표준
양식 저장(`uploads/template.py`)뿐이다.

### 5.1 DB 위치와 생성 방식

| 항목 | 현재 |
|---|---|
| 기본 경로 | `<프로젝트 루트>/database/procurement.db` |
| 바꾸는 법 | 환경변수 `DATABASE_PATH` (Electron 이 이미 이렇게 넘긴다) |
| 없을 때 | `python -m procurement init` 이 만든다. 부모 폴더도 자동 생성 |
| 스키마 보완 | `bootstrap()` 이 `CREATE TABLE IF NOT EXISTS` + 필요한 컬럼 추가 |
| ⛔ | 기존 데이터를 지우지 않는다 |

---

## 6. 설치 위치와 데이터 위치는 **반드시 나눠야 한다**

| 두면 | 무슨 일이 생기나 |
|---|---|
| `C:\Program Files\...\database\procurement.db` | 일반 사용자 권한으로 **쓸 수 없다.** DB 생성부터 실패한다 |
| 〃 | 프로그램을 새 버전으로 덮으면 **고객 데이터가 함께 지워질 수 있다** |

**권장 구조**

```
프로그램   C:\Program Files\...\            (설치 시 관리자 권한 · 이후 읽기만)
고객 데이터 %APPDATA%\procurement-desktop\  (일반 사용자 권한으로 쓰기 가능)
              └─ database\procurement.db
```

⭐ **이미 그렇게 만들어져 있다.** `electron/backend.js` 가 Electron 의
`userData` 폴더를 `DATABASE_PATH` 로 넘긴다. 지금 필요한 것은 그 값을 실제
설치본에서 확인하는 일이지, 구조를 새로 만드는 일이 아니다.

⛔ 이번 STEP 에서 경로를 바꾸지 않았다.

---

## 7. Excel · 기업정보 FILE/API

### 7.1 Excel — **Microsoft Excel 을 설치할 필요가 없다**

| 항목 | 값 |
|---|---|
| 처리 | `openpyxl` (**순수 파이썬**) |
| 형식 | `.xlsx` 읽기·쓰기. ⚠️ **`.xls`(구형)는 못 읽는다** |
| Excel 프로그램 | ⛔ **필요 없다.** COM·Excel 자동화를 쓰지 않는다(코드로 확인) |
| 임시 파일 | 만들지 않는다 — 경로를 받아 바로 읽는다 |
| 대용량 | 실측 2,300행·4,900행·98,000행 파일을 처리해 왔다 |

### 7.2 FILE 방식 — 추가 의존성 없음

```
고객 PC → 파일 선택 대화상자(Electron) → 절대경로 → 백엔드 → DB
```

✅ 인터넷 불필요. ✅ 추가 프로그램 불필요.

### 7.3 API 방식 — 인터넷과 키가 필요하다

| 항목 | 값 |
|---|---|
| 대상 | `apis.data.go.kr` (공공데이터포털) — 여성·장애인·창업·직접생산 |
| 전송 | 표준 `urllib` (추가 패키지 없음) |
| 인터넷 | **필요** |
| 키 | `SMPP_API_KEY` · `STARTUP_API_KEY` — **환경변수** |
| 타임아웃·재시도 | `EXTERNAL_API_TIMEOUT_SECONDS` · `EXTERNAL_API_MAX_ATTEMPTS` |
| ⚠️ | 일부가 **`http://`** 다. 기관 방화벽·프록시에서 막힐 수 있다 |

⛔ **API 키를 EXE 안에 넣지 않는다.** 키는 고객 기관이 발급받는 값이고, 실행
파일에 넣으면 꺼내 볼 수 있다. 설정 화면이나 사용자 폴더의 `.env` 로 받아야
하며, **그 자리를 정하는 것이 §4.2 의 수정 후보**다.

⚠️ FILE 방식만 쓴다면 인터넷이 전혀 필요 없다.

---

## 8. 환경변수 목록

| 이름 | 필수 | 기본값 | EXE 에서 |
|---|---|---|---|
| `DATABASE_PATH` | ⭐ **사실상 필수** | 프로젝트 루트/`database` | **Electron 이 사용자 폴더로 지정** |
| `DATABASE_FILENAME` | ✕ | `procurement.db` | 그대로 |
| `PURCHASE_PERIOD_DATE_FIELD` | ✕ | `resolution_date` | 그대로 (비우면 연도 조회가 503) |
| `ADMIN_API_TOKEN` | ✕ | 없음 | 없으면 설정 변경 API 가 503. 단독 PC 라면 불필요 |
| `SMPP_API_KEY` · `STARTUP_API_KEY` | API 쓸 때만 | 없음 | ⛔ **EXE 에 넣지 않는다** — 고객이 입력 |
| `EXTERNAL_API_TIMEOUT_SECONDS` · `..._MAX_ATTEMPTS` | ✕ | 있음 | 그대로 |
| `DATA_MODE` · `ENVIRONMENT` · `DEBUG` | ✕ | 있음 | 배포본은 `production`/`False` 권장 |
| `DASHBOARD_ACHIEVEMENT_DISPLAY_THRESHOLDS` | ✕ | 없음 | 그대로 |
| `DATA_PATH` · `LOG_PATH` | ✕ | 프로젝트 루트 하위 | ⚠️ **지금 실제로 쓰는 코드가 없다** |

---

## 9. Windows 환경 점검

| 항목 | 판단 |
|---|---|
| 관리자 권한 | **설치할 때만.** 실행·DB 생성·업로드·내보내기는 일반 권한으로 충분 — 단 §6 대로 데이터를 사용자 폴더에 둘 때 |
| 포트 | `127.0.0.1` + **빈 포트 자동 선택**. 고정 8000 이 아니라 충돌 없음 |
| 외부 접속 | **불필요** — localhost 전용. 방화벽 인바운드 규칙이 필요 없다 |
| 방화벽 팝업 | 127.0.0.1 바인딩이라 보통 뜨지 않으나, ⚠️ **Windows 에서 실제 확인 필요** |
| 백신·SmartScreen | ⚠️ **서명 없는 EXE 는 「알 수 없는 게시자」 경고가 뜬다.** 코드 서명 인증서로 해결. 이번에 구현하지 않음 |
| 브라우저 | **필요 없다** — Electron 창 안에서 뜬다 |

---

## 10. 🔴 로그가 없다

| 확인 | 결과 |
|---|---|
| 로깅 프레임워크 | **없다.** `procurement/core/__init__.py` 에 「Logging 설정 (예정)」 |
| 지금 오류를 보는 법 | ① uvicorn 콘솔 출력 ② API 오류 응답 ③ 화면 안내문 |
| EXE 에서 | ⚠️ **콘솔 창이 없으면 ①이 사라진다** |

Electron 이 백엔드 표준출력을 받고 있으므로(`PYTHONUNBUFFERED=1`) **그것을 파일로
남기는 것이 가장 작은 해결책**이다. ⛔ 로그 시스템을 새로 만들지 않았다 —
STEP 125 이후 판단할 일이다.

---

## 11. 패키징 방법 비교

### 후보 A — PyInstaller 로 백엔드를 EXE 로 묶고, Electron 이 그것을 실행 ⭐ **권장**

| | |
|---|---|
| 장점 | 고객 PC 에 **Python 설치 불필요** · `backend.js` 가 이미 `backendExecutable` 분기를 갖고 있다 · 파일 하나로 떨어진다 |
| 단점 | Windows 에서 빌드해야 한다 · native 패키지 hidden import 를 챙겨야 한다 · 서명 없으면 SmartScreen 경고 |
| 적합성 | **높음.** 코드 구조가 이미 이 방식을 전제로 갈라져 있다 |
| 추가 설정 | `.spec`(`web/static/index.html` 동봉) · §4 의 두 경로 수정 |
| 예상 문제 | `_MEIPASS` 경로(§4.1) · `.env` 위치(§4.2) · `pydantic_core`·`httptools` 수집 |

### 후보 B — Python embeddable 배포판을 함께 넣기

| | |
|---|---|
| 장점 | PyInstaller 특유의 경로 문제가 없다 |
| 단점 | 설치 용량이 크고 폴더가 지저분하다 · 의존성을 직접 넣어야 한다 |
| 적합성 | 중간 |

### 후보 C — 고객 PC 에 Python 사전 설치

| | |
|---|---|
| 장점 | 개발 그대로 |
| 단점 | ⛔ **고객 PC 에 Python 설치를 요구할 수 없다**(전제 위반) |
| 적합성 | 낮음 |

**→ 후보 A 를 권장한다.** 다만 **권장과 적용은 다르다.** 이번 STEP 에서
`.spec` 을 만들지도, 의존성에 PyInstaller 를 넣지도 않았다.

---

## 12. 권장 배포 형태

| 항목 | 값 |
|---|---|
| 권장 형태 | `Setup.exe`(NSIS 등) → 설치 → 바탕화면 바로가기 → 창 실행 |
| 설치 파일 | electron-builder 로 생성 (⛔ **아직 도입 안 함** — PM 결정 대기) |
| 실행 방식 | 바로가기 더블클릭 → Electron 창 → 내부 FastAPI |
| 브라우저 | **필요 없음** |
| DB 위치 | `%APPDATA%\procurement-desktop\database\procurement.db` |
| 사용자 데이터 | 위 DB 파일 **하나** |
| 인터넷 | FILE 방식만 쓰면 **불필요** · API 방식은 필요 |
| Python 설치 | **불필요**(후보 A 기준) |
| Excel 설치 | **불필요** |

### 12.1 업데이트와 데이터 보존

```
v1 설치 → 고객 데이터 축적(%APPDATA%) → v2 로 덮어쓰기 → 데이터 그대로
```

| 항목 | 판단 |
|---|---|
| 업데이트 시 DB 유지 | ✅ 데이터가 설치 폴더 밖이면 안전 |
| migration | ✅ `bootstrap()` 이 없는 테이블·컬럼을 보완한다. 기존 값을 지우지 않는다 |
| 재설치 시 보존 | ✅ 설치 제거가 `%APPDATA%` 를 지우지 않도록 설정하면 유지 |
| ⚠️ 위험 | 데이터를 설치 폴더에 두면 **덮어쓰기·제거 때 사라진다** → §6 |
| ⛔ | 자동 업데이트 기능을 만들지 않았다 |

---

## 13. STEP 125 권장 작업

1. **§4 의 두 경로를 고친다** — PM 승인 후. 번들 실행일 때의 기준 폴더와
   `.env` 위치. 시험으로 고정.
2. **Windows 개발 PC 에서 PyInstaller PoC** — `.spec` 초안, native 패키지 수집,
   §2.2 의 13개 항목을 Windows 에서 다시 확인.
3. **`electron-builder` 도입 여부 결정** — PM 승인 사항.
4. **로그를 파일로 남길지 결정**(§10).
5. **코드 서명 인증서**가 필요한지 결정(§9).
6. `uvicorn[standard]` → `uvicorn` 축소 검토(§3.1) — 의존성 변경이므로 승인 필요.

⛔ 1~6 중 어느 것도 이번 STEP 에서 하지 않았다.
