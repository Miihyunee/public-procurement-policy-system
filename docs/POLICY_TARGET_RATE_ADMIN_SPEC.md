# 정책 목표율 등록 기능 명세 (Spec)

## 문서 정보

| 항목 | 값 |
|---|---|
| 문서 종류 | 명세서 (Spec) — **PM 결정 반영 완료 · 구현 착수 가능** |
| 작성일 | 2026-08-09 |
| 최종 갱신 | 2026-08-09 (PM 결정 D-11~D-17 반영) |
| 기준 커밋 | `main = a2cd31b` |
| 대상 | 작업 A — 정책 목표율 등록 기능 |
| 구현 여부 | ❌ **아직 구현하지 않음.** 본 PR 은 명세만 담는다. |

> 본 문서의 결정 사항은 **PM 승인 완료** 상태다(D-11 · D-12 · D-13 · D-14 · D-15 · D-16 · D-17).
> 다만 **구현은 본 Spec PR 병합 후 별도 Issue/브랜치**에서 진행한다.

## PM 확정 사항 요약

| ID | 결정 |
|---|---|
| **D-11** | `NULL` 복원 **허용**. 키 누락 → 422 / 명시적 `null` → 해제 / 문자열 → 설정 |
| **D-12** | **A안** — 변경 이력 테이블 **이번 구현 제외**. 향후 별도 Issue |
| **D-13** | `0 < target_rate <= 100`. **법정 상한이 아니라 구조적 상한**으로 기록 |
| **D-14** | **환경변수 관리자 토큰**. 미설정 시 쓰기 API 비활성. `GET` 은 대상 제외 |
| **D-15** | **현재 `main` 의 실제 seed 코드가 정본** |
| **D-16** | 요청 `target_rate` 는 **문자열로 통일**. 숫자 허용용 변환 로직 만들지 않음 |
| **D-17** | `is_active = false` 정책은 **목표율 변경 불가**(활성 정책 한정) |

---

# 0. 코드 실측 결과 — 지시서와 다른 부분 (선행 보고)

지시서 1장("보고서의 내용과 코드가 다르면 코드 실측 결과를 우선하여 보고한다")에 따라
먼저 보고한다.

## 0.1 정책 코드 정본 — **D-15 확정**

### 확정된 원칙

> **현재 `main` 에 실제로 저장되어 있는 코드가 정본이다.** (PM 결정 D-15)

따라서 본 Spec 의 API 는 아래 5개 코드를 그대로 사용한다.
**코드를 임의로 변경하지 않는다.**

| # | 정본 `policy_code` (`main` 실측) | 정책명 | `evaluation_basis` | `target_rate` |
|---|---|---|---|---|
| 1 | `SMALL_BUSINESS` | 중소기업 | `PAYMENT_DATE` | `NULL` |
| 2 | **`WOMAN`** | 여성기업 | `PAYMENT_DATE` | `NULL` |
| 3 | **`DISABLED`** | 장애인기업 | `PAYMENT_DATE` | `NULL` |
| 4 | `STARTUP` | 창업기업 | `CONTRACT_DATE` | `NULL` |
| 5 | **`GREEN`** | 녹색제품 | `PAYMENT_DATE` | `NULL` |

출처: `src/procurement/database/bootstrap.py` — `MVP_POLICY_SEEDS` (실측).

### 문서에 남아 있는 다른 표기 (임의 변경하지 않음)

| 표기 | 등장 위치 | 처리 |
|---|---|---|
| `WOMEN_BUSINESS` · `DISABLED_BUSINESS` | 지시서 본문 | ❌ 정본 아님. 코드 변경하지 않음 |
| `GREEN_PRODUCT` | `docs/DATA_DICTIONARY.md` · `docs/proposals/ISSUE23-SPEC-project-bootstrap.md` · `tests/test_policy_repository.py`(테스트 임의 문자열) | ❌ 정본 아님. **이번 작업에서 문서·코드를 정리하지 않는다**(PM 원칙: 임의 정리 금지) |

### ⚠️ 남은 확인 1건 — `GREEN` vs `GREEN_PRODUCT`

PM 결정 D-15 는 **"현재 main 의 실제 seed 를 기준으로 한다"** 이나,
같은 항목에 열거된 목록에는 5번째가 **`GREEN_PRODUCT`** 로 적혀 있다.

| 근거 | 값 |
|---|---|
| `main` 실제 seed (`bootstrap.py:88`) | **`GREEN`** |
| D-15 목록 표기 | `GREEN_PRODUCT` |
| 이전 확정 이력 (`ISSUE23-SPEC-project-bootstrap.md` Q1) | "제안했던 `GREEN_PRODUCT` 대신 **`GREEN` 으로 확정**됨" |

본 Spec 은 **D-15 의 원칙("main 이 정본")을 우선 적용해 `GREEN` 을 채택**했다.
이전 확정 이력과도 일치한다. `GREEN_PRODUCT` 로 바꾸려면 seed·테스트·문서 변경이
동반되므로 **PM 이 명시적으로 지시하지 않는 한 변경하지 않는다.**

> 구현 착수 시점에 이 1건만 확인해 주시면 된다. 다른 코드는 이견이 없다.

## 0.2 테이블명 표기 정정

이전 문서(`POLICY_IMPLEMENTATION_STATUS.md` 8장)에서 `policies.target_rate` 로 적었으나,
실제 테이블명은 **`policy`(단수)** 이다. 본 문서는 실제 스키마 표기를 따른다.

## 0.3 그 외 지시서 1장 항목 — 실측 일치 확인

| 항목 | 실측 결과 |
|---|---|
| `main` 커밋 | `a2cd31b` ✅ |
| pytest | 447 passed ✅ |
| ruff | All checks passed ✅ |
| mypy strict | src 39 · tests 20, 오류 0 ✅ |
| 열린 PR | 0건 ✅ |
| 사업자번호 정규화 · 9자리 보정 금지 · 체크섬 Warning | 구현됨 ✅ |
| Matcher C안 · `PurchaseImporter` · `rematch()` · `find_unmatched()` | 구현됨 ✅ |
| Bootstrap · Dashboard · `target_rate = NULL` 처리 · E2E A~E | 구현됨 ✅ |

→ **이미 구현된 기능을 다시 구현하지 않는다.**

---

# 1. 현재 상태

## 1.1 목표율 저장 구조 (실측)

```sql
CREATE TABLE IF NOT EXISTS policy (
    policy_id       INTEGER PRIMARY KEY,
    policy_code     TEXT UNIQUE NOT NULL,
    policy_name     TEXT NOT NULL,
    description     TEXT,
    is_active       BOOLEAN NOT NULL,
    evaluation_basis TEXT NOT NULL,
    target_rate     TEXT,          -- nullable. Decimal 정밀도 보존을 위해 TEXT 저장
    created_at      DATETIME NOT NULL,
    updated_at      DATETIME NOT NULL
)
```

- `Policy.target_rate: Decimal | None` — 미설정은 `None`.
- 저장 시 `_rate_to_db()` 로 문자열 변환, 조회 시 `_rate_from_db()` 로 `Decimal` 복원.

## 1.2 현재 가능한 것 / 불가능한 것

| 기능 | 현재 | 근거 (실측) |
|---|---|---|
| 목표율 조회 | ✅ | `find_by_policy_code()` · `find_by_id()` · `find_active()` · `find_active_with_target_rate()` |
| **목표율 변경** | ❌ | `PolicyRepository` 에 `update` 계열 메서드가 **하나도 없다** |
| 목표율 최초 등록 | 🟡 seed 뿐 | `bootstrap.seed_policies()` — **전부 `target_rate=None`** 으로 등록 |
| 쓰기 API | ❌ | `app.py` 의 엔드포인트는 `GET /dashboard/summary` 하나뿐 |
| 변경 이력 | ❌ | 이력 테이블 없음. `policy.updated_at` 컬럼만 존재 |
| 목표율 검증 | 🟡 부분 | `_validate_required()` 에 `target_rate > 0` 만 존재. **상한 없음** |

> `PolicyRepository` 모듈 docstring 에도 명시되어 있다:
> "본 Repository 는 Foundation 단계 범위로, Insert/조회/집계만 제공합니다.
> **Update/Delete 및 비즈니스 로직은 이후 Issue 에서 구현합니다.**"
> 즉 목표율 변경 부재는 누락이 아니라 **의도적으로 미룬 범위**다.

## 1.3 다른 Repository 의 UPDATE 선례

`PurchaseRepository.update_company_id()` 가 유일한 UPDATE 선례다.

```python
def update_company_id(self, purchase_id: int, company_id: int) -> bool:
    ...
    "UPDATE purchase SET company_id = ? WHERE purchase_id = ?"
```

- **부분 갱신 전용 메서드**(엔티티 전체 저장이 아님)
- 대상이 없으면 `False` 반환
- → 목표율 갱신도 **같은 패턴**을 따르는 것이 일관적이다.

---

# 2. 문제 정의

1. 공식 목표율이 확정되어도 **운영자가 값을 넣을 방법이 없다.**
   현재 유일한 경로는 SQLite 에 직접 `UPDATE` 를 실행하는 것뿐이다.
2. 직접 SQL 실행은 다음 위험이 있다.
   - `_validate_required()` 의 `target_rate > 0` 검증을 우회한다.
   - `updated_at` 갱신을 누락한다.
   - `Decimal` 정밀도 규약(TEXT 저장)을 위반한 값이 들어갈 수 있다.
   - 오타로 잘못된 `policy_code` 를 갱신해도 아무도 모른다.
3. 결과적으로 **중소기업·창업기업·장애인기업 3종은 계산 로직이 완성되어 있음에도
   달성률을 실제로 볼 수 없다.**

---

# 3. 목표

| # | 목표 |
|---|---|
| 1 | 운영자가 확정된 목표율을 **API 를 통해** 등록할 수 있게 한다 |
| 2 | 등록 시 **기존 `insert` 경로와 동일한 검증**을 적용한다 |
| 3 | 현재 등록된 정책과 목표율을 **조회**할 수 있게 한다 |
| 4 | **기존 Dashboard/Calculator 경로를 일절 변경하지 않는다** |

## 3.1 비목표 (이번 범위에서 하지 않는 것)

| 항목 | 이유 |
|---|---|
| 목표율 **값** 을 코드에 seed | 지시서 6장 — 공식 근거 미확인 |
| 정책 신규 생성 / 삭제 API | 이번 범위 밖 |
| `policy_name` · `evaluation_basis` 등 다른 필드 변경 API | 판정 기준일 변경은 계산 결과를 바꾼다. 별도 결정 필요 |
| 여성기업 복수 목표율 구조 | 지시서 7장 — 구현 중단 |
| 장애인표준사업장 목표율 | 지시서 8장 — 구현 금지 |
| Group C 분모 구조 | 지시서 9장 — 구현 금지 |
| 녹색제품 목표율·판정단위 | 지시서 15장 — `NULL` 유지 |
| 인증/인가 시스템 | 지시서 5장 D-14 — 대규모 구현 금지 |

---

# 4. API 범위

## 4.1 `GET /policies` — 정책 목록 조회

**목적**: 현재 등록된 정책과 목표율 확인, `NULL` 여부 확인.

응답 예시(정상):

```json
{
  "policies": [
    {
      "policy_code": "SMALL_BUSINESS",
      "policy_name": "중소기업",
      "evaluation_basis": "PAYMENT_DATE",
      "is_active": true,
      "target_rate": null,
      "target_rate_status": "NOT_SET",
      "updated_at": "2026-08-09T00:00:00"
    }
  ]
}
```

| 필드 | 형식 | 비고 |
|---|---|---|
| `target_rate` | **문자열 또는 `null`** | 기존 Dashboard 규약과 동일(`Decimal` → 문자열). `null` 은 JSON `null` |
| `is_active` | boolean | 비활성 정책은 변경 불가(D-17)이므로 클라이언트가 구분할 수 있어야 한다 |
| `target_rate_status` | `SET` / `NOT_SET` | 클라이언트가 `null` 을 0 으로 오해하지 않도록 명시 |
| `updated_at` | ISO 8601 | 기존 날짜 규약 |

> `target_rate_status` 는 Dashboard 의 `TARGET_RATE_NOT_SET` 과 **목적이 같지만 별개 값**이다.
> Dashboard 의 `DashboardStatus` 를 재사용하지 않는다 — 그쪽은 달성 상태, 이쪽은 설정 상태다.

## 4.2 `PUT /policies/{policy_code}/target-rate` — 목표율 변경

요청:

```json
{ "target_rate": "50" }
```

`null` 로 되돌리는 요청(목표율 해제, D-11 승인됨):

```json
{ "target_rate": null }
```

응답(200): 변경 후 정책 1건 (4.1 과 동일 스키마).

| 항목 | 값 |
|---|---|
| Method | `PUT` (멱등 — 같은 값 재요청 시 결과 동일) |
| 경로 식별자 | `policy_code` (`policy_id` 아님 — 운영자가 읽을 수 있는 값) |
| 요청 본문 `target_rate` | **문자열 또는 `null` 만** (D-16 확정. 숫자 → 422) |
| 인증 | **`Authorization: Bearer <관리자 토큰>` 필수** (D-14) |
| 대상 | **활성 정책만**. 비활성 정책 → 422 (D-17) |

> **요청 본문에서 `target_rate` 를 문자열로 받는 이유**: JSON number 로 받으면
> Pydantic 이 `float` 를 거쳐 `Decimal` 로 변환하면서 정밀도가 손상될 수 있다.
> 응답에서 문자열로 직렬화하는 기존 규약과도 대칭이 된다.

---

# 5. Service / Repository 계층 구조

## 5.1 계층도

```text
기존(계산 경로) — 변경 없음
FastAPI
 → DashboardApiService
 → DashboardDataService
 → Calculator
 → PurchaseRepository / CertificationRepository / PolicyRepository
 → SQLite

신규(설정 경로) — 이번 제안
FastAPI
 → PolicyAdminService
 → PolicyRepository
 → SQLite
```

**두 경로는 `PolicyRepository` 를 공유하되, 서비스 계층에서 분리된다.**
설정 변경은 계산이 아니므로 Calculator 를 지나지 않는다.

## 5.2 준수 원칙 (지시서 3장)

| 원칙 | 준수 방법 |
|---|---|
| `DashboardApiService` 수정 금지 | 파일을 열지 않는다 |
| `DashboardDataService` 수정 금지 | 파일을 열지 않는다 |
| `Calculator` 수정 금지 | 파일을 열지 않는다 |
| endpoint 에서 Repository 직접 호출 금지 | 엔드포인트는 `PolicyAdminService` 만 주입받는다 |
| composition root 는 `app.py` 한 곳 | `build_policy_admin(db_path)` 를 `build_dashboard_api()` 옆에 추가 |
| 기존 Dashboard API 동작 변경 금지 | `GET /dashboard/summary` 의 코드·응답 스키마 무변경 |

## 5.3 신규 파일 (제안)

| 파일 | 역할 |
|---|---|
| `src/procurement/admin/__init__.py` | 패키지 export |
| `src/procurement/admin/policy_admin.py` | `PolicyAdminService` — 조회/변경 유스케이스 |
| `src/procurement/admin/response.py` | `PolicyItemResponseModel` · `PolicyListResponseModel` · `TargetRateUpdateRequest` |

> `api/` 패키지에 넣지 않는다. `api/` 는 문서상 "대시보드 데이터를 API 응답 형태로
> 제공하는 API 계층"으로 한정되어 있어, 설정 기능을 넣으면 그 정의가 깨진다.

## 5.4 기존 파일 변경 (제안)

| 파일 | 변경 |
|---|---|
| `src/procurement/database/policy_repository.py` | `update_target_rate()` **추가만**. 기존 메서드 무변경 |
| `src/procurement/app.py` | `build_policy_admin()` + 엔드포인트 2개 + 예외 핸들러 **추가만** |

## 5.5 `PolicyRepository.update_target_rate()` (제안 시그니처)

```python
def update_target_rate(self, policy_code: str, target_rate: Decimal | None) -> Policy | None:
    """정책의 목표율을 변경합니다.

    Returns:
        변경된 Policy. 해당 policy_code 가 없으면 None.
    """
```

- `UPDATE policy SET target_rate = ?, updated_at = ? WHERE policy_code = ?`
- `updated_at` 을 **반드시 함께 갱신**한다(직접 SQL 실행의 문제점 2 해소).
- 대상 없음은 예외가 아니라 `None` — `update_company_id()` 의 `bool` 반환 선례와 동일한 사고.
- 검증은 서비스가 아니라 **Repository 에서도 수행**한다(`insert` 와 동일 규칙 적용).

---

# 6. D-11 검토 — 목표율을 `NULL` 로 되돌릴 수 있는가?

## 6.0 PM 결정 — **허용 확정** ✅

요청 본문 해석은 다음 3가지로 구분한다.

| 요청 본문 | 의미 | 응답 |
|---|---|---|
| `target_rate` **키 자체가 없음** | 잘못된 요청 | **422** |
| `{"target_rate": null}` | **목표율 해제**(설정 → 미설정) | 200 |
| `{"target_rate": "5.0"}` | 목표율 설정 | 200 |

명시적 `null` 로 기존 목표율을 제거할 수 있어야 한다.

## 6.1 결정 근거

| # | 근거 |
|---|---|
| 1 | 시스템에는 이미 `NULL` 이 **정상 상태**로 정의되어 있다. Dashboard 는 `TARGET_RATE_NOT_SET` / "목표율 미설정" 을 정식 상태로 표시한다. 되돌리기를 막으면 **시스템이 표현할 수 있는 상태에 도달할 수 없는 경로**가 생긴다 |
| 2 | 현재 5개 정책이 전부 `NULL` 이다. 즉 `NULL` 은 예외가 아니라 **기본값**이다 |
| 3 | **오입력 복구 경로**가 된다. 되돌리기가 없으면 잘못 넣은 값을 지우려고 다시 SQL 을 직접 실행하게 되어, 이 기능을 만든 이유가 무너진다 |
| 4 | 정부 기준 변경 시 "이전 목표율은 더 이상 유효하지 않으나 새 값은 미확정" 상태가 실제로 발생할 수 있다. 이때 **낡은 값을 남겨두는 것이 `NULL` 보다 위험하다** — 낡은 목표율로 계산된 달성률이 정상 수치처럼 보이기 때문 |

## 6.2 단점과 대응

| 단점 | 대응 |
|---|---|
| 실수로 `null` 을 보내 설정이 지워질 수 있다 | 요청 본문에 `target_rate` **키 자체가 없으면 422**. 명시적 `"target_rate": null` 만 해제로 인정한다 |
| 해제 사실이 기록되지 않는다 | D-12 참조 |
| `null` 해제와 "값 미변경"이 구분되지 않는다 | 위와 동일 — 키 존재 여부로 구분한다 |

## 6.3 확정 상태

**승인 완료.** 6.0 의 3분기 규칙대로 구현한다.

---

# 7. D-12 검토 — 변경 이력

## 7.1 두 방안

| 안 | 내용 |
|---|---|
| **A안** | `policy.updated_at` 만 사용. 이력 테이블 없음 |
| **B안** | `policy_target_rate_history` 테이블 신설 (이전값/새값/변경시각/변경자/사유) |

## 7.2 비교

| 기준 | A안 | B안 |
|---|---|---|
| 구현 비용 | 없음(이미 존재) | 테이블·Repository·마이그레이션·테스트 추가 |
| "누가 언제 무엇을 왜 바꿨나" 추적 | ❌ 마지막 변경 시각만 | ✅ |
| 잘못된 목표율로 계산된 과거 결과 소급 검증 | ❌ 불가 | ✅ 가능 |
| Bootstrap 스키마 영향 | 없음 | `_REQUIRED_SCHEMA` · `verify_bootstrap()` 확장 필요 |
| 변경자 기록 | 불가 | **D-14(접근 통제)가 정해져야 의미 있음** — 인증이 없으면 "누가"를 채울 수 없다 |

## 7.3 PM 결정 — **A안 확정. 변경 이력 테이블은 이번 구현에서 제외** ✅

이번 구현 범위:

- 현재 값만 저장한다(`policy.target_rate` · `policy.updated_at`).
- **`policy_target_rate_history` 테이블을 만들지 않는다.**
- Bootstrap 스키마(`_REQUIRED_SCHEMA`)를 변경하지 않는다.

> 📌 **향후 별도 Issue 필요**: 목표율은 모든 달성률 계산의 분모이므로,
> 장기적으로 "언제 · 얼마에서 얼마로 · 누가 · 왜" 변경했는지 추적할 수단이 필요하다.
> D-14(관리자 토큰)만으로는 사용자 단위 식별이 되지 않으므로, 사용자 인증 도입 시점에
> 이력 테이블(B안)을 함께 검토한다. **이번 범위에서는 구현하지 않는다.**

## 7.4 A안 채택 근거

1. **B안의 핵심 가치인 "변경자 추적"은 D-14 가 정해지기 전에는 구현할 수 없다.**
   인증이 없는 상태에서 이력 테이블을 만들면 `changed_by` 가 항상 `NULL` 인
   테이블이 남는다. 그 상태의 이력은 `updated_at` 대비 추가 가치가 크지 않다.
2. 현재 정책은 **5종**이고 목표율은 **연 단위로 바뀌는 값**이다. 변경 빈도가 매우 낮다.
3. 다만 목표율은 **모든 달성률 계산의 분모**이므로 장기적으로 이력은 필요하다.
   → 지금 버리지 말고 **Issue 로 남긴다**.

> ⚠️ A안의 알려진 한계: **목표율이 언제 얼마에서 얼마로 바뀌었는지 추적할 수 없다.**
> `policy.updated_at` 은 "마지막으로 바뀐 시각"만 알려준다.
> 이는 **PM 이 수용한 한계**이지 미발견 결함이 아니다.

---

# 8. D-13 검토 — 목표율 상한

## 8.0 PM 결정 — **`0 < target_rate <= 100` 확정** ✅

> **100 은 정부 정책상 법정 상한이 아니다.**
> 현재 시스템의 **구매비율 정의에서 파생되는 구조적 상한**이다.

이 문구를 코드 주석·오류 메시지·API 문서에 **그대로 유지**한다.
정부 공식 기준으로 표현하지 않는다.

| 검증 | 값 |
|---|---|
| 하한 | `target_rate > 0` (**기존 `insert` 경로와 동일 규칙**) |
| 상한 | `target_rate <= 100` (**구조적 상한**) |
| 위반 | 422 |

## 8.1 현재 구조에서 목표율의 의미 (코드 실측)

`ProcurementAchievementCalculator._achievement_rate()`:

```text
구매비율(purchase_rate) = 정책 인정 구매액 ÷ 전체 구매액 × 100
달성률(achievement_rate) = 구매비율 ÷ 목표율 × 100
```

즉 목표율은 **"전체 구매액 대비 몇 %를 이 정책 대상에서 사더라도 목표 달성으로 볼 것인가"**
를 뜻하는 **비율의 기준값**이며, 달성률 계산에서 **나눗셈의 분모**로 쓰인다.

| 성질 | 결과 |
|---|---|
| `target_rate = 0` | 0 나눗셈 → 이미 `> 0` 검증으로 차단됨 ✅ |
| `target_rate` 가 클수록 | 달성률이 낮게 나온다(달성이 어려워짐) |
| `target_rate > 100` | **수학적으로는 계산된다.** 다만 구매비율은 정의상 최대 100 이므로 달성률이 100 을 넘을 수 없게 된다 |

## 8.2 일반 정책에서 허용 가능한 범위

현재 구조(분모 = 전체 구매액)에서는 구매비율이 `0 ≤ x ≤ 100` 이므로,
**목표율이 100 을 넘으면 달성이 구조적으로 불가능하다.** 즉 100 초과는
현재 분모 구조에서 **의미가 없다.**

## 8.3 Group C 확장 시 충돌 여부

Group C(기술개발생산품·온누리상품권·국가유공자자활용사촌)는 분모가 다르다.

| 정책 | 분모(정부 파일 기준) |
|---|---|
| 온누리상품권 | 기관 경상경비 |
| 국가유공자자활용사촌 | 생산가능품목 구매액 |
| 기술개발생산품 | **다른 정책의 계산 결과** |

분모가 "전체 구매액"이 아니면 **비율이 100 을 넘을 수 있는지 자체가 달라진다.**
다만 여기에는 더 중요한 사실이 있다:

> **Group C 는 현재 Calculator 구조로 계산할 수 없다.** 목표율 상한을 100 으로 두든
> 두지 않든, Group C 는 어차피 분모 구조부터 새로 설계해야 한다.
> 따라서 **"Group C 때문에 지금 상한을 두지 못한다"는 논리는 성립하지 않는다.**

## 8.4 현재 단계에서 상한을 두는 것이 안전한가?

| 관점 | 판단 |
|---|---|
| 오입력 차단 | 상한이 있으면 `5` 를 `500` 으로 잘못 입력하는 사고를 막는다 |
| 잘못된 정책 계산 방지 | 상한이 없으면 달성 불가능한 목표율이 조용히 저장되어 **모든 정책이 SHORTAGE 로 표시**될 수 있다 |
| 확장성 | Group C 는 어차피 별도 설계 대상(8.3) |
| 근거 | ⚠️ **100 이라는 숫자는 정부 파일에서 확인된 값이 아니다.** 현재 Calculator 의 분모 정의에서 **파생되는 수학적 상한**이다 |

## 8.5 확정된 표기 (코드·문서에 그대로 사용)

```text
검증: 0 < target_rate <= 100

상한 100 의 근거:
    정부 정책상 법정 상한이 아니다.
    현재 시스템은 구매비율 = 정책 인정 구매액 ÷ 전체 구매액 × 100 으로 정의하므로
    구매비율이 100 을 넘을 수 없고, 목표율 100 초과는 달성이 구조적으로 불가능해진다.
    즉 이 상한은 현재 분모 정의에서 파생되는 구조적 상한이다.
    분모 구조가 다른 정책(Group C)을 도입할 때 이 제약을 재검토한다.
```

> ⛔ **이 상한을 "정부 공식 기준" · "법정 상한" 으로 표현하지 않는다.**
> 오류 메시지에도 "법정" 이라는 표현을 쓰지 않는다.

---

# 9. D-14 검토 — 쓰기 API 접근 통제

## 9.1 현재 상태 (실측)

- 인증·인가 코드가 **전혀 없다.** 미들웨어·의존성·토큰 검증 없음.
- 현재 유일한 엔드포인트가 **읽기 전용**이라 지금까지 문제가 되지 않았다.
- `PUT` 이 추가되는 순간 **처음으로 외부에서 데이터를 바꿀 수 있는 경로**가 생긴다.

## 9.2 세 방안 비교

| 안 | 내용 | 장점 | 단점 |
|---|---|---|---|
| **가** | 내부망 전제, 통제 없음 | 구현 0 | 네트워크 설정 실수 시 **누구나 목표율 변경 가능**. 배포 형태가 문서화되어 있지 않아 "내부망"이 실제로 보장되는지 확인 불가 |
| **나** | 본격 인증(사용자·세션·권한) 도입 | 완전 | 지시서 5장이 금지한 "대규모 구현". MVP 범위를 크게 벗어남 |
| **다** | **최소 보호 장치** — 환경변수 기반 관리자 토큰 1개를 헤더로 검증 | 구현 소규모(의존성 1개). 오조작·외부 접근을 실질적으로 차단 | 사용자 단위 식별 불가(D-12 B안의 `changed_by` 를 채우지 못함) |

## 9.3 PM 결정 — **다안(환경변수 관리자 토큰) 확정** ✅

```text
PUT 요청 → FastAPI Depends(require_admin_token)
         → Authorization: Bearer <토큰> 을 환경변수 값과 비교
         → 불일치 → 401
관리자 토큰 미설정 → 쓰기 API 비활성 (접근 거부)
GET /policies 는 읽기 전용이므로 관리자 인증 대상에서 제외
```

| 항목 | 확정 내용 |
|---|---|
| 인증 방식 | **`Authorization: Bearer <토큰>` 헤더** (PM 지시 "Authorization 등 명확한 방식") |
| 토큰 저장 | **환경변수만.** `.env`(이미 gitignore) — 기존 인증키 원칙과 동일 |
| 코드·문서·GitHub 기록 | ⛔ **금지** |
| 토큰 미설정 시 | **쓰기 API 비활성 / 접근 거부** |
| `GET /policies` | 관리자 인증 **대상 아님** |
| 비교 방식 | `secrets.compare_digest()` (타이밍 공격 방지, 표준 라이브러리) |

> 🔒 **실제 토큰 값을 코드·문서·GitHub·PR 본문·테스트에 기록하지 않는다.**
> 테스트는 fixture 에서 주입한 테스트 전용 값을 사용한다.

## 9.4 기존 API 영향 최소화 (PM 지시)

인증 구현이 **기존 API 응답이나 예외 처리에 영향을 주지 않도록** 범위를 제한한다.

| 금지 | 대체 |
|---|---|
| 전역 미들웨어 추가 | ❌ — `GET /dashboard/summary` 까지 통과하게 되어 영향 범위가 커진다 |
| 전역 예외 핸들러 추가/변경 | ❌ — **기존 전역 예외 처리 방식을 변경하지 않는다**(PM 지시 9장) |
| 채택 | ✅ **`PUT` 엔드포인트에만 붙는 FastAPI `Depends`** + **엔드포인트 내부에서만 HTTP 변환** |

미설정 시 "비활성" 의 구현 방식은 다음 중 하나로 하되, **기존 라우트에는 영향이 없어야 한다.**

| 방식 | 동작 |
|---|---|
| 가 (권장) | 토큰 미설정이면 `PUT` 라우트를 **등록하지 않는다** → 404. 공격자에게 기능 존재 자체를 노출하지 않음 |
| 나 | 라우트는 등록하되 **503** 반환 → 운영자가 "설정 누락"임을 알기 쉬움 |

> 운영 편의(원인 파악 용이)를 고려하면 **나안**, 노출 최소화를 고려하면 **가안**이다.
> 어느 쪽이든 **`GET` 과 기존 Dashboard 엔드포인트는 영향을 받지 않는다.**
> 구현 시 **나안(503)** 으로 진행하되, PM 이 가안을 원하시면 라우트 미등록으로 바꾼다.

---

# 10. 데이터 검증 규칙

| # | 규칙 | 응답 | 비고 |
|---|---|---|---|
**모든 규칙 확정 완료.**

| # | 규칙 | 응답 | 근거 |
|---|---|---|---|
| 1 | `policy_code` 가 존재하지 않음 | **404** | |
| 2 | `target_rate` 키 누락 | **422** | D-11 — 명시적 해제와 구분 |
| 3 | `target_rate` 가 숫자 문자열이 아님 (`"abc"`, `""`) | **422** | |
| 4 | `target_rate` 가 **문자열이 아닌 JSON number** (`8.0`) | **422** | D-16 — 숫자 허용용 변환 로직을 만들지 않는다 |
| 5 | `target_rate <= 0` | **422** | **기존 `insert` 경로와 동일 규칙**(`_validate_required`) |
| 6 | `target_rate > 100` | **422** | D-13 — **구조적 상한**(법정 상한 아님) |
| 7 | `target_rate = null` | **200** (해제) | D-11 승인 |
| 8 | 소수 자릿수 | 소수 둘째 자리까지 허용 | 기존 `Decimal` 저장 형식과 일치 |
| 9 | **비활성 정책(`is_active = false`) 변경** | **422** | **D-17 — 대상은 활성 정책으로 한정** |

> 규칙 5 를 서비스가 아니라 **Repository 에서도** 적용한다. 그래야 `insert` 와
> `update` 두 경로의 규칙이 갈라지지 않는다(1.2 의 "직접 SQL 우회" 문제 해소).

## 10.1 D-16 — 입력 타입 확정

요청 본문의 `target_rate` 는 **문자열 또는 `null` 만** 받는다.

```json
{"target_rate": "8.0"}     ✅
{"target_rate": null}      ✅  (해제)
{"target_rate": 8.0}       ❌  422
{"target_rate": 8}         ❌  422
{}                         ❌  422
```

근거: JSON number 는 `float` 를 거치면서 `Decimal` 정밀도가 손상될 수 있다.
**숫자 입력을 허용하기 위한 별도 변환 로직을 만들지 않는다**(PM 지시 6항).
Pydantic 모델에서 `StrictStr | None` 로 선언해 변환 자체를 차단한다.

## 10.2 D-17 — 비활성 정책 처리 확정

| 상황 | 동작 |
|---|---|
| `is_active = true` 정책의 목표율 변경 | ✅ 허용 |
| `is_active = false` 정책의 목표율 변경 | ❌ **422** — "비활성 정책의 목표율은 변경할 수 없습니다" |
| `GET /policies` 목록 | 활성/비활성 **모두 표시**하고 `is_active` 필드로 구분 |

> `GET` 까지 활성만 보여주면 비활성 정책이 왜 변경되지 않는지 확인할 방법이 없다.
> **조회는 전체, 변경은 활성 한정**으로 분리한다.
>
> 참고: 현재 `main` 의 5개 seed 는 **전부 `is_active = true`** 이므로,
> 이 규칙이 지금 당장 어떤 정책을 막지는 않는다. 향후를 위한 규칙이다.

---

# 11. 오류 처리

## 11.1 응답 형식

기존 규약(`{"detail": "..."}`)을 그대로 사용한다. `app.py` 의
`CalculatorValidationError → 422` 핸들러와 같은 형태다.

| 상태 | 발생 조건 |
|---|---|
| 200 | 정상 |
| 401 | 관리자 토큰 불일치·누락 (D-14) |
| 404 | `policy_code` 없음 |
| 422 | 검증 실패 (10장) — 비활성 정책 변경 시도 포함 |
| 503 | 관리자 토큰 미설정으로 쓰기 API 비활성 (D-14, 9.4 나안) |

## 11.2 예외 → HTTP 변환 위치 (PM 지시 9장 준수)

> **기존 API 의 전역 예외 처리 방식을 변경하지 않는다.**
> 필요한 변환은 **목표율 관리 엔드포인트 내부에서만** 수행한다.

| 예외 | 매핑 | 변환 위치 |
|---|---|---|
| `PolicyNotFoundError` (신규) | 404 | **엔드포인트 내부** |
| `PolicyValidationError` (**기존 재사용**) | 422 | **엔드포인트 내부** |
| 관리자 토큰 불일치 | 401 | `Depends` 에서 `HTTPException` |

⛔ **하지 않는 것**

- `@app.exception_handler(PolicyValidationError)` 같은 **전역 핸들러 추가 금지.**
  `PolicyValidationError` 는 기존 `insert` 경로에서도 발생하므로, 전역 핸들러를 붙이면
  **기존 경로의 응답이 조용히 바뀔 수 있다.**
- 기존 `CalculatorValidationError → 422` 핸들러 **수정 금지.**

> `PolicyValidationError` 는 이미 `policy_repository.py` 에 있다. 새로 만들지 않는다.

---

# 12. 기존 Dashboard / Calculator 영향

## 12.1 코드 영향

| 대상 | 영향 |
|---|---|
| `DashboardApiService` | **없음** (파일 무변경) |
| `DashboardDataService` | **없음** (파일 무변경) |
| `ProcurementAchievementCalculator` | **없음** (파일 무변경) |
| `GET /dashboard/summary` 응답 스키마 | **없음** |
| `PolicyRepository` 기존 메서드 | **없음** (메서드 추가만) |
| `bootstrap.py` | **없음** (seed 값 변경 없음 — 전부 `NULL` 유지) |

## 12.2 확인 필요 (구현 시 반드시 검증)

| # | 항목 |
|---|---|
| 1 | `app.py` 에 라우트를 추가해도 기존 8개 `test_app.py` 테스트가 그대로 통과하는가 |
| 2 | 전역 예외 핸들러 목록이 **기존과 동일**한가(`CalculatorValidationError` 1개만) |
| 3 | Swagger(`/docs`) 에 새 엔드포인트가 정상 노출되는가 |
| 4 | 관리자 토큰 미설정 상태에서 `GET /dashboard/summary` 와 `GET /policies` 가 정상 동작하는가 |

## 12.3 동작 영향 (의도된 변경)

목표율이 `NULL` → 숫자로 바뀌면 Dashboard 응답의 해당 정책이
`TARGET_RATE_NOT_SET` 에서 실제 계산 상태(`NORMAL`/`SHORTAGE`)로 바뀐다.
**이것이 이 기능의 목적이며, 코드 변경이 아니라 데이터 변경에 의한 것이다.**

---

# 13. 테스트 계획

## 13.1 Repository — `test_policy_repository.py` (기존 파일에 추가)

| # | 테스트 |
|---|---|
| R-1 | `update_target_rate()` 로 `NULL` → `Decimal("50")` 변경 후 조회 시 값 일치 |
| R-2 | 변경 시 `updated_at` 이 갱신된다 |
| R-3 | 변경해도 `created_at` 은 바뀌지 않는다 |
| R-4 | 존재하지 않는 `policy_code` → `None` 반환 |
| R-5 | `target_rate <= 0` → `PolicyValidationError` |
| R-6 | `Decimal("12.34")` 저장 후 정밀도 손실 없이 복원 |
| R-7 | `None` 으로 되돌리기 성공 (D-11) |
| R-8 | 다른 정책의 `target_rate` 는 영향받지 않는다 |
| R-9 | `target_rate > 100` → `PolicyValidationError` (D-13) |
| R-10 | 정본 5개 코드(`SMALL_BUSINESS`/`WOMAN`/`DISABLED`/`STARTUP`/`GREEN`) 전부 변경 가능 (D-15) |

## 13.2 Service — `test_policy_admin.py` (신규)

| # | 테스트 |
|---|---|
| S-1 | `list_policies()` 가 5개 정책을 반환 |
| S-2 | `target_rate=None` 인 정책의 `target_rate_status == "NOT_SET"` |
| S-3 | 값 설정 후 `"SET"` 으로 바뀐다 |
| S-4 | 존재하지 않는 코드 → `PolicyNotFoundError` |
| S-5 | 서비스가 Calculator 를 사용하지 않는다(의존성 미주입으로 구조 보장) |
| S-6 | 비활성 정책 변경 시도 → `PolicyValidationError` (D-17) |
| S-7 | `list_policies()` 는 비활성 정책도 반환한다 (D-17, 조회는 전체) |

## 13.3 API — `test_policy_admin_api.py` (신규)

| # | 테스트 |
|---|---|
| A-1 | `GET /policies` → 200, 5건 |
| A-2 | 미설정 정책의 `target_rate` 가 JSON `null` (문자열 `"None"` 아님) |
| A-3 | `PUT` 정상 → 200, 변경값 반영 |
| A-4 | `PUT` 후 `GET /dashboard/summary` 의 상태가 `TARGET_RATE_NOT_SET` → 계산 상태로 전환 |
| A-5 | 없는 `policy_code` → 404 |
| A-6 | `target_rate: "0"` → 422 |
| A-7 | `target_rate: "abc"` → 422 |
| A-8 | `target_rate` 키 누락 → 422 |
| A-9 | `target_rate: null` → 200, 해제됨 (D-11) |
| A-10 | `target_rate: "100.01"` → 422 (D-13 구조적 상한) |
| A-11 | `target_rate: "100"` → 200 (경계값) |
| A-12 | 토큰 누락 / 불일치 → 401 (D-14) |
| A-13 | 토큰 미설정 환경 → `PUT` 503, **`GET` 2종은 정상 200** (D-14, 9.4) |
| A-14 | `target_rate: 8.0` (JSON number) → 422 (D-16) |
| A-15 | 비활성 정책 `PUT` → 422 (D-17) |
| A-16 | 같은 값 2회 `PUT` → 결과 동일(멱등) |
| A-17 | 전역 예외 핸들러가 기존과 동일한 1개인지 확인 (11.2) |

## 13.4 회귀

| # | 항목 |
|---|---|
| G-1 | 기존 `test_app.py` 8건 전부 통과 |
| G-2 | 기존 `test_dashboard_api.py` 전부 통과 |
| G-3 | 기존 E2E 시나리오 A~E 전부 통과 |
| G-4 | 전체 447건 + 신규분 통과 |
| G-5 | ruff · mypy strict 통과 |

## 13.5 테스트 원칙

- **실제 목표율 수치를 테스트에 넣더라도 그것은 seed 가 아니다.** 테스트 fixture 의
  값(`"50"` 등)은 계산 경로 검증용 임의값이며, `bootstrap.py` 의 seed 는 `NULL` 을 유지한다.
- 관리자 토큰 실제값을 테스트에 하드코딩하지 않는다.

---

# 14. 보안 / 접근 통제 고려사항

| # | 항목 |
|---|---|
| 1 | 관리자 토큰은 **환경변수만** 사용. 코드·문서·GitHub·PR 본문·테스트에 기록 금지 🔒 |
| 2 | 토큰 미설정 시 쓰기 API **비활성** (D-14 확정) — "설정 안 하면 무방비"를 방지 |
| 3 | 토큰 비교는 `secrets.compare_digest()` 사용 (타이밍 공격 방지) |
| 4 | 오류 응답에 토큰 값이나 환경변수명 이상의 정보를 노출하지 않는다 |
| 5 | `GET /policies` 는 목표율만 노출(개인정보·사업자번호 없음) → 관리자 인증 대상 아님 |
| 6 | 로그에 요청 헤더·본문 전체를 남기지 않는다(토큰 유출 방지) |
| 7 | 인증은 **`PUT` 엔드포인트에만** 적용. 전역 미들웨어를 쓰지 않는다 |
| 8 | 배포 형태(내부망 여부)는 여전히 **미확정**이나, D-14 채택으로 그 전제에 의존하지 않게 되었다 |

---

# 15. 향후 확장 고려사항

| # | 항목 | 현재 결정 |
|---|---|---|
| 1 | **변경 이력 테이블** | D-12 A안 확정 → **이번 구현 제외.** 목표율은 모든 달성률의 분모이므로 향후 **별도 Issue 로 등록 필요**(사용자 인증 도입 시점에 함께 검토) |
| 2 | 복수 목표율(구매유형별) — 여성기업 | ⛔ 지시서 7장 — **구현 중단.** 단, `PUT` 경로를 `/target-rate` 로 한정해 두면 향후 `/target-rates` 를 별도 추가하는 확장이 가능하다 |
| 3 | 연도별 목표율 | **미확정** — 정부 목표율이 연 단위로 바뀌는지 확인되지 않음 |
| 4 | 분모가 다른 정책(Group C) | ⛔ 지시서 9장 — 구현 금지. 도입 시 D-13 상한 재검토 |
| 5 | 정책 신규 생성/삭제 API | 범위 밖 |
| 6 | `evaluation_basis` 변경 API | 판정 기준일 변경은 **계산 결과를 바꾼다.** 공식 근거 확인 전 도입하지 않는다 |
| 7 | 사용자 단위 인증 | D-14 나안 — 필요해지면 별도. 도입 시 D-12 B안(변경자 추적)과 함께 검토 |

---

# 16. 결정 사항 — **전부 확정 완료**

| ID | 결정 사항 | 확정 내용 |
|---|---|---|
| **D-11** | `NULL` 복원 | ✅ **허용.** 키 누락 → 422 / `null` → 해제 / 문자열 → 설정 |
| **D-12** | 변경 이력 | ✅ **A안.** 이력 테이블 이번 구현 제외, 향후 별도 Issue |
| **D-13** | 목표율 상한 | ✅ **`0 < x <= 100`.** 법정 상한 아님 — **구조적 상한**으로 기록 |
| **D-14** | 쓰기 API 접근 통제 | ✅ **환경변수 관리자 토큰.** `Authorization: Bearer`. 미설정 시 쓰기 비활성. `GET` 제외 |
| **D-15** | 정책 코드 정본 | ✅ **현재 `main` 의 실제 seed 가 정본** (0.1 참조) |
| **D-16** | 입력 타입 | ✅ **문자열로 통일.** 숫자 허용 변환 로직 만들지 않음 |
| **D-17** | 비활성 정책 | ✅ **변경 불가.** 관리 대상은 활성 정책 한정 |

## 16.1 구현 착수 전 남은 확인 1건

| 항목 | 내용 |
|---|---|
| `GREEN` vs `GREEN_PRODUCT` | D-15 원칙("main 이 정본")에 따라 **`GREEN` 채택.** D-15 목록 표기와 다르므로 확인만 부탁드린다 (0.1 참조) |

## 16.2 구현 순서 (PM 지시 9장)

1. `PolicyRepository.update_target_rate()`
2. `admin/` 패키지의 `PolicyAdminService`
3. 목표율 조회/설정 응답 모델
4. FastAPI 엔드포인트
5. Repository → Service → API 계층 테스트
6. 기존 Dashboard / Calculator 회귀 테스트

⛔ **수정 금지 대상 (PM 지시)**

| 대상 | 이유 |
|---|---|
| `ProcurementAchievementCalculator` | 계산 로직 |
| Rule Engine (`DateBasisRule` 등) | 판정 기준일 로직 |
| `DashboardDataService` | 대시보드 조립 로직 |
| `DashboardApiService` | 대시보드 응답 계층 |
| **기존 전역 예외 처리 방식** | 11.2 — 엔드포인트 내부 변환만 사용 |

---

# 17. 이번 단계에서 하지 않은 것 (지시서 17장 대조)

| 금지 항목 | 준수 |
|---|---|
| 여성기업 단일 5% 근사 | ✅ 하지 않음 |
| 여성기업 구매유형 구조 | ✅ 하지 않음 |
| 장애인표준사업장 목표율 | ✅ 하지 않음 |
| 녹색제품 목표율 | ✅ 하지 않음 (`GREEN` 유지, `target_rate = NULL`) |
| 녹색제품 판정단위 | ✅ 하지 않음 |
| Group C 분모 | ✅ 하지 않음 |
| 정책 간 의존성 | ✅ 하지 않음 |
| 실제 API 응답 필드 추측 | ✅ 하지 않음 |
| API 저장/캐싱 가능 여부 추측 | ✅ 하지 않음 |
| 고객 파일 구조 추측 | ✅ 하지 않음 |
| 음수 거래 상계 규칙 | ✅ 하지 않음 |
| 판정 기준일의 공식 근거 주장 | ✅ 하지 않음 |
| **목표율 값 seed** | ✅ **하지 않음 — 5종 전부 `NULL` 유지** |
| 법정 비율 추측 | ✅ 하지 않음 (D-13 상한 100 은 **구조적 상한**으로만 기록) |
| `GREEN` 삭제 | ✅ 하지 않음 — 유지 |
| 기존 코드·정책 데이터 임의 정리 | ✅ 하지 않음 — `GREEN_PRODUCT` 표기가 남은 문서·테스트도 **건드리지 않음** |

---

# 18. 미확정 사항

실제 데이터 또는 공식 근거가 확보되어야 진행 가능한 항목이다.
**추측으로 채우지 않았다.**

| 항목 | 필요한 것 |
|---|---|
| `GREEN` vs `GREEN_PRODUCT` | PM 확인 1건 (D-15 원칙상 `GREEN` 채택) |
| 목표율 상한의 **정책적** 근거 | 정부 자료 (현재는 구조적 근거만 있음 — 그렇게만 기록함) |
| 목표율이 연도별로 바뀌는지 | 정부 자료 |
| 배포 형태(내부망 여부) | 운영 환경 정보 (D-14 채택으로 이 전제에 의존하지는 않게 됨) |
| 중소기업·창업기업·장애인기업의 **실제 목표율 값** | 공식 자료 — 확인 전까지 `NULL` |
| 여성기업 구매유형 구분 | 실제 고객 구매데이터 |
| 장애인표준사업장 "1000분의 8%" 해석 | 공식 근거 |
| 녹색제품 목표율·판정단위 | 공식 기준 자료 |
| Group C 분모 구조 | 공식 근거 + 설계 결정 |
| 인증 API 응답 필드 | 실제 인증키·응답 원문 |
| `Company.representative_name` nullable 전환 | 실제 API 응답 |
| Certification 인증 상태 필드 | 실제 API 응답 |
| 고객 파일 구조 | 실제 고객 파일 |
| 음수/0 금액 처리 | 실제 거래 데이터 (Issue #49) |
