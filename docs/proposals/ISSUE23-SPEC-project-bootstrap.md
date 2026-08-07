# Issue #23 명세서 — Project Bootstrap / Database Initialization

- 문서 유형: **설계 명세 (Spec)** — 구현은 본 명세 검토·승인 후 진행
- 작성 목적: 프로젝트를 **처음 받은 사람이 수작업 없이** `clone → init → run` 만으로
  Dashboard API 를 바로 사용할 수 있도록 하는 **Project Bootstrap** 을 설계한다.
- 관련 코드:
  - `procurement.database.connection` (`create_connection` — 경로/파일 자동 생성)
  - `procurement.database.*_repository` (각 `create_table()` — `IF NOT EXISTS`)
  - `procurement.app` (`build_dashboard_api` / `create_app` / `GET /dashboard/summary`)
  - `procurement.__main__` (현재 uvicorn 기동만)
  - `procurement.core.config.settings` (`db_file` 등)
- 분리 출처: #40 (Issue #22), 후속 운영 이슈

> ⚠️ 본 명세는 **문서만** 포함한다. 코드·테스트·의존성 변경은 없다.

---

## 0. 목표 (PM 지시)

이번 이슈의 목적은 **"DB를 만든다"가 아니라 "처음 받은 사람이 바로 실행할 수 있게 만든다"** 이다.

```
git clone  →  init  →  run  →  Dashboard API 정상 동작
```

- 별도 수작업(테이블 수동 생성, 데이터 수동 입력) 없이 실행 가능한 상태를 제공한다.
- 성공 기준: 새 클론 환경에서 `init` 후 `run` 하면 `GET /dashboard/summary` 가 **200** 과
  **의미 있는 데이터**(seed 정책 기반)를 반환한다.

---

## 1. 배경 및 현황 (As-Is)

| 구성 요소 | 현재 상태 | 부트스트랩 관점 평가 |
|---|---|---|
| DB 파일/경로 생성 | `create_connection()` 이 부모 디렉터리 + 파일 자동 생성 | ✅ 이미 있음 (재사용) |
| 테이블 생성 | 각 Repository `create_table()` = `CREATE TABLE IF NOT EXISTS` | ✅ 멱등, 단 **일괄 실행 진입점 없음** |
| 전체 스키마 초기화 | ❌ 4개 테이블을 한 번에 만드는 함수 없음 | ❌ 신규 |
| Seed(초기 정책) | ❌ 없음 | ❌ 신규 |
| Health Check | ❌ 없음 | ❌ 신규 |
| 실행 진입점 | `python -m procurement` → uvicorn 기동만 | 🔶 `init` 명령 없음 |
| 결과 | 빈 환경에서 `GET /dashboard/summary` → **500 `no such table`** | ❌ 실사용 불가 |

핵심: **개별 부품(연결/테이블 생성)은 있으나, 이를 묶어 "한 번에 초기화"하는 오케스트레이션과 seed·검증·CLI 가 없다.**

관련 사실:
- `PolicyRepository` 는 `exists(policy_code)`, `count()` 를 제공 → **seed 멱등 처리에 활용 가능**.
- `Dashboard` 요약은 `find_active_with_target_rate()`(활성 + `target_rate` 설정) 정책만 노출 →
  **seed 에 `target_rate` 를 넣어야 대시보드가 바로 데이터를 보인다.**
- `evaluation_basis` 허용값은 `PAYMENT_DATE` / `CONTRACT_DATE`.

---

## 2. 범위 (PM 요청 6개 영역)

### 2.1 Database 생성
- SQLite 파일 및 경로 자동 생성 → **이미 `create_connection()` 이 처리**. 부트스트랩은 이를 호출만 하면 됨.
- 기존 DB 가 있으면 **재생성하지 않음(idempotent)**. 파괴적 재생성은 기본 동작에서 제외.

### 2.2 모든 테이블 생성
- `Company`, `Policy`, `Certification`, `Purchase` 4개 테이블을 **한 번에** 생성.
- 각 `create_table()` 이 `IF NOT EXISTS` 이므로 **반복 실행 안전**.
- 신규: 이를 순서대로 호출하는 **`init_db()`** (스키마 오케스트레이터).

### 2.3 Seed Data (초기 정책)
- MVP 정책 5종을 초기 등록. `POLICY_DEFINITION.md` 기준.

| 정책 | 제안 policy_code | evaluation_basis | target_rate(제안, **확정 필요**) |
|---|---|---|---|
| 중소기업 | `SMALL_BUSINESS` | PAYMENT_DATE | 예: 50 |
| 여성기업 | `WOMAN` | PAYMENT_DATE | 예: 5 |
| 장애인기업 | `DISABLED` | PAYMENT_DATE | 예: 1 |
| 창업기업 | `STARTUP` | CONTRACT_DATE | 예: (확정 필요) |
| 녹색제품 | `GREEN_PRODUCT` | PAYMENT_DATE | 예: (확정 필요) |

- **evaluation_basis 초기값**: 위 표대로 확정적으로 넣을 수 있음(문서 근거 존재).
- **target_rate 초기값**: 법정 비율이 문서에 명시돼 있지 않음 → **PM/법령 확인 필요**(Open Question Q2).
  - 대시보드가 바로 데이터를 보이려면 `target_rate` 가 있어야 하므로, **예시 기본값을 넣되 값은 확정 후 반영**을 권장.
  - 대안: target_rate 를 NULL 로 seed → 대시보드는 목표율 등록 후 노출(“바로 동작” 목표와는 다소 배치).
- **멱등성**: seed 는 `exists(policy_code)` 로 존재 여부 확인 후 **없을 때만 insert**(재실행 시 중복 없음).

**Seed 배치 제안(권장안):**
- Seed 를 **별도 함수 `seed_policies(db_path)`** 로 분리하고, `init` 이 **기본으로 호출**한다.
- `init --no-seed` 로 seed 생략 가능(스키마만 초기화).
- 근거: 스키마(구조) vs 데이터(정책) 관심사 분리 → 재사용·테스트 용이, 운영 시 seed 정책 교체 유연.

### 2.4 Health Check
- 초기화 후 다음을 검증하고 **명확한 오류 메시지** 제공:
  - DB 파일 존재
  - 4개 테이블 존재 (`sqlite_master` 조회)
  - Policy seed 존재 (`policy` 행 수 ≥ 기대치, 또는 필수 코드 존재)
- 신규: **`verify_bootstrap(db_path) -> HealthReport`** (성공/실패 항목과 사유 반환).
- `init` 종료 단계에서 자동 실행하여 결과를 출력. 실패 시 비정상 종료코드(예: 1).

### 2.5 CLI
- `python -m procurement <command>` 서브커맨드 구조(표준 라이브러리 `argparse`).

| 명령 | 동작 |
|---|---|
| `init` | DB/경로 생성 → 테이블 생성 → seed(기본 포함) → health check |
| `run` | FastAPI 개발 서버 기동(현재 `__main__` 동작) |
| `health` (선택) | health check 만 단독 실행 |

- 옵션(초안): `init --no-seed`, `init --db PATH`, `run --host --port`.
- **얇은 `__main__` 유지**: `__main__` 은 인자 파싱 후 각 기능(`bootstrap` / `app`)에 위임만.

### 2.6 README
- "처음 받은 사람" 기준 최소 실행 절차를 문서화:
  ```bash
  git clone <repo> && cd public-procurement-policy-system
  python -m venv .venv && source .venv/bin/activate
  pip install -e .

  python -m procurement init     # DB 생성 + 테이블 + seed + 검증
  python -m procurement run      # 서버 기동
  # → http://127.0.0.1:8000/dashboard/summary  (200 + seed 데이터)
  # → http://127.0.0.1:8000/docs                (Swagger)
  ```
- 기존 "실행" 섹션을 위 `init → run` 흐름으로 갱신.

---

## 3. 설계안 (구조)

신규 모듈 제안: `procurement/database/bootstrap.py` (또는 `procurement/bootstrap.py`).

```python
# 개념 예시 (구현 아님)

def init_db(db_path=None) -> None:
    """4개 테이블을 생성한다(IF NOT EXISTS, 멱등)."""
    CompanyRepository(db_path).create_table()
    PolicyRepository(db_path).create_table()
    CertificationRepository(db_path).create_table()
    PurchaseRepository(db_path).create_table()

def seed_policies(db_path=None) -> int:
    """MVP 정책 5종을 없을 때만 등록한다(멱등). 등록 건수 반환."""
    repo = PolicyRepository(db_path)
    created = 0
    for spec in _DEFAULT_POLICIES:          # code/name/basis/target_rate
        if not repo.exists(spec.policy_code):
            repo.insert(Policy(...))
            created += 1
    return created

def verify_bootstrap(db_path=None) -> HealthReport:
    """DB/테이블/seed 존재를 점검하고 항목별 결과를 반환한다."""

def bootstrap(db_path=None, seed=True) -> HealthReport:
    """init_db → (seed) → verify 순으로 실행하는 오케스트레이터."""
```

- 계층 원칙: 부트스트랩은 **`database` 계층 책임**(Repository 사용). `app.py` 는 조립/기동만.
- CLI(`__main__`)는 `bootstrap()` / `app` 진입점에 **위임만** 한다.
- `create_app()` 자동 초기화 여부(startup 시 `init_db` 호출)는 **Open Question Q4** 로 분리
  (운영 안전성상 기본은 **명시적 `init`** 권장, 개발 편의 옵션은 추후).

---

## 4. 목표 흐름 (To-Be)

```
git clone
   ↓
pip install -e .
   ↓
python -m procurement init      # bootstrap: DB + 테이블 + seed + health check
   ↓
python -m procurement run       # uvicorn
   ↓
GET /dashboard/summary → 200 + seed 정책 기반 데이터
GET /docs               → Swagger
```

---

## 5. 하위호환 · 범위 밖

### 하위호환
- 기존 Repository/`create_table()`/`app.py`/`DashboardApiService` **시그니처·동작 무변경**.
- 신규 `bootstrap` 모듈 + CLI 서브커맨드 + README 갱신만 추가.
- `run` 은 현재 `python -m procurement` 의 서버 기동 동작을 계승(필요 시 기본 명령을 `run`/도움말로 정리 — Q5).

### 범위 밖 (하지 않는 것)
- 스키마 마이그레이션 프레임워크(Alembic 등) 도입
- 실구매/인증/기업 대량 데이터 적재(수집·매칭 파이프라인은 별도 이슈)
- 인증/권한, UI/Chart
- 파괴적 DB 리셋(`--force drop`) — 필요 시 별도 옵션/이슈

---

## 6. 구현 범위 (승인 시)

| 항목 | 포함 |
|---|---|
| `init_db()` (4개 테이블 일괄, 멱등) | ✅ |
| `seed_policies()` (정책 5종, 멱등) | ✅ |
| `verify_bootstrap()` (DB/테이블/seed 점검 + 오류 메시지) | ✅ |
| `bootstrap()` 오케스트레이터 | ✅ |
| CLI 서브커맨드 `init` / `run` (+선택 `health`) | ✅ |
| 단위 테스트(멱등성·seed·health·CLI 종료코드·init 후 API 200) | ✅ |
| README `init → run` 흐름 갱신 | ✅ |
| 마이그레이션 프레임워크 | ❌ |
| 대량 데이터 적재 | ❌ |

---

## 7. 최종 확정 사항 (PM 결정)

> 본 절이 **현재 유효한 기준**이다. 아래 8장의 Open Questions 는 작성 시점의 기록이며,
> 내용이 다를 경우 **본 절이 우선**한다.

| # | 확정 사항 | 반영 위치 |
|---|---|---|
| 1 | **MVP 정책은 5종** — `SMALL_BUSINESS` · `WOMAN` · `DISABLED` · `STARTUP` · `GREEN` | `database/bootstrap.py` `MVP_POLICY_SEEDS` |
| 2 | `target_rate` 에 **근거 없는 임의값을 입력하지 않는다** | 〃 (seed 시 값 미지정) |
| 3 | **`target_rate=NULL` seed 를 허용**한다 | 〃 |
| 4 | `target_rate=NULL` 정책은 **계산하지 않는다** (Calculator 에 전달하지 않음) | `dashboard/data_service.py` |
| 5 | Dashboard 는 NULL 정책을 **제거하지 않고 `TARGET_RATE_NOT_SET` 으로 표시**한다 | `dashboard/models.py`, `api/response.py` |
| 6 | 사업자번호 **결합 키는 `business_no`** 이다 | `matchers/business_no.py` |
| 7 | 미매칭 구매데이터는 **Company 를 자동 생성하지 않고 보관**한다 (방안 C) | `importers/purchase_importer.py` |
| 8 | 기업정보 확보 후 **`rematch()` 로 재연결**한다 | 〃 (멱등) |
| 9 | **음수 금액 처리는 별도 Issue #49** 에서 결정한다 | `PURCHASE_IMPORT_DESIGN.md` 5장 |
| 10 | 외부 API Collector 는 **인증키·실제 응답 필드 확인 이후** 착수한다 | `EXTERNAL_API_ONBOARDING.md` |

### 7.1 관련 확정 사항 (참고)

| 항목 | 확정 내용 |
|---|---|
| 사업자번호 **자릿수 자동 보정** | **하지 않는다.** 9자리 값도 보정 없이 형식 오류 처리(잘못된 기업 연결 위험 방지) |
| 체크섬 검증 | **Warning 만**, 데이터 차단 없음 (D-002) |
| 물리명 | 현행 유지(`business_no`, `amount`). 리팩터링 없음 (D-001) |

---

## 8. Open Questions 처리 결과

> 작성 당시의 질문과 최종 처리 상태를 기록으로 남긴다.

| # | 질문 | 상태 | 최종 결과 |
|---|---|---|---|
| Q1 | 정책 `policy_code` 확정 | ✅ **해결됨** | `SMALL_BUSINESS` / `WOMAN` / `DISABLED` / `STARTUP` / **`GREEN`** — 제안했던 `GREEN_PRODUCT` 대신 **`GREEN` 으로 확정**됨 |
| Q2 | seed `target_rate` 초기값 | ✅ **해결됨** | **NULL 로 seed.** 법적·공식 근거가 확인되지 않은 값을 넣지 않는다(D-004) |
| Q3 | Seed 별도 함수 + `init` 기본 호출 | ✅ **해결됨** | 제안대로 채택. `seed_policies()` 분리 + `--no-seed` 옵션 제공 |
| Q4 | 서버 startup 자동 `init_db` | ✅ **해결됨** | **호출하지 않음.** 명시적 `init` 명령만 사용(`app.py` 에 `init_db` 호출 없음) |
| Q5 | 인자 없는 실행 동작 | ✅ **해결됨** | **도움말 출력** (`parser.print_help()`, 종료코드 0) |
| Q6 | `health` 서브커맨드 | ✅ **해결됨** | **제공됨.** 정상 0 / 실패 1 종료코드 |
| Q7 | 파괴적 재생성(`--force`) | ⏸ **보류** | 이번 범위 제외. 필요 시 별도 Issue |
| — | DB Migration Framework | 📌 **별도 이슈** | 도입하지 않음. Health Check 의 **구 스키마 감지**로 대응 |
| — | 음수·0 금액 저장 | 📌 **별도 이슈** | **Issue #49** 에서 명세화 후 결정 |

---

## 9. 요약
- 목표는 **clone → init → run → 바로 동작**이며, **달성 확인 완료**
  (`init` 2회 멱등, `GET /dashboard/summary` 200, `/docs` 200).
- 기존 자산(경로/파일 자동 생성, `CREATE TABLE IF NOT EXISTS`, `exists()/count()`)을 재사용하고
  **`init_db` + `seed_policies` + `verify_bootstrap` + CLI(`init`/`run`/`health`) + README** 를 추가했다.
- 모든 초기화는 **멱등**하며, seed 는 **운영자가 설정한 `target_rate` 를 덮어쓰지 않는다.**
- 정책 5종은 **`target_rate=NULL` 로 등록**되며, 대시보드에서 `TARGET_RATE_NOT_SET` 으로 표시된다.
  목표율을 등록하면 코드 변경 없이 자동으로 계산에 포함된다.
