# Issue #20 명세서 — Policy Target Rate 관리 기능 검토

- 문서 유형: **설계 분석 · 명세 (Spec)** — 구현/DB 변경은 본 명세 검토·승인 후 별도 Issue 로 진행
- 작성 목적: 정책별 목표율(target_rate)을 시스템에서 관리하고,
  `DashboardDataService` 가 외부 입력 없이 목표율을 조회할 수 있도록 개선하기 위한
  설계안을 비교·검토하고 **구현 여부를 판단**한다.
- 관련 코드: `procurement.dashboard.data_service.DashboardDataService` (Issue #19),
  `procurement.calculators.procurement_achievement.ProcurementAchievementCalculator`,
  `procurement.models.policy.Policy`

> ⚠️ 본 명세는 **문서만** 포함한다. 코드·테스트·DB 변경은 없다.
> DB 변경은 아래 "구현 여부 판단"에서 방향을 확정한 뒤 후속 Issue 에서 진행한다.

---

## 1. 배경 및 목표

### 1.1 목표 (PM 지시)
- 정책별 목표율을 **시스템에서 관리**한다.
- `DashboardDataService` 가 **외부 입력 없이** 목표율을 조회할 수 있도록 개선한다.
- **현재 Calculator 구조와 호환**을 유지한다.

### 1.2 범위 밖 (하지 않는 것)
- Dashboard UI / API 구현
- 기존 계산 로직(달성률 공식, 규칙 판정) 변경
- 본 명세 승인 전 DB 스키마 변경

---

## 2. 현황 분석 (As-Is)

### 2.1 목표율의 현재 흐름
현재 목표율(`target_rate`)은 **어디에도 저장되지 않으며**, 호출자가 매번
`{policy_id: 목표율}` 형태의 dict 로 주입한다.

```
호출자(테스트/미래 API) ──target_rates(dict)──▶ DashboardDataService.build_summary()
                                                    └─▶ Calculator.calculate_all(target_rates)
                                                            └─▶ 정책별 달성률 계산
```

- `Calculator.calculate_all(target_rates: dict[int, Decimal])` — 목표율을 인자로 받는다.
- `DashboardDataService.build_summary(target_rates: dict[int, Decimal])` — 그대로 계산기에 전달하고,
  결과에 목표율을 다시 붙여 `PolicySummary.target_rate` 를 채운다.

### 2.2 Policy 테이블/모델 현황
`docs/DATABASE_DESIGN.md` 및 `Policy` 모델 기준, 현재 컬럼은 다음과 같다.

| Column | Type | 비고 |
|---|---|---|
| policy_id | INTEGER | PK |
| policy_code | TEXT | Unique |
| policy_name | TEXT | |
| description | TEXT | 선택 |
| is_active | BOOLEAN | |
| evaluation_basis | TEXT | PAYMENT_DATE / CONTRACT_DATE |
| created_at / updated_at | DATETIME | |

→ **`target_rate` 컬럼은 존재하지 않는다.**

### 2.3 문제점
1. 목표율이 시스템에 없으므로, 향후 Dashboard API/UI 가 매번 목표율을 알아야 한다.
2. 목표율의 "정본(single source of truth)"이 없어, 화면마다 값이 달라질 위험이 있다.
3. 목표율 변경 이력·근거를 남길 수 없다(법정 목표율은 연도별로 바뀔 수 있음).

---

## 3. 요구사항 정리

| # | 요구사항 | 우선순위 |
|---|---|---|
| R1 | 정책별 목표율을 시스템에 저장·조회할 수 있어야 한다 | 필수 |
| R2 | `DashboardDataService` 가 목표율을 **직접 조회**해 요약을 생성할 수 있어야 한다 | 필수 |
| R3 | 기존 Calculator(`calculate_all(target_rates)`)는 **변경하지 않는다** | 필수 |
| R4 | 목표율이 등록되지 않은 정책에 대한 **동작이 정의**되어야 한다 | 필수 |
| R5 | (선택) 목표율의 연도별/기간별 이력 관리 | 검토 |

---

## 4. 설계안 비교

### 방안 A — `Policy` 테이블에 `target_rate` 컬럼 추가
정책 1건이 목표율 1개를 가진다.

```
Policy(policy_id, policy_code, ..., evaluation_basis, target_rate)
```

- **장점**: 구조가 단순하고 조회가 가장 빠르다(정책 조회 1회로 목표율 확보). 마이그레이션 작은 편.
- **단점**: 연도별·기간별 목표율 변경 이력을 남길 수 없다. 목표율이 NULL/미설정인 상태 처리 필요.

### 방안 B — 별도 `PolicyTargetRate` 테이블 (기간/연도별 이력)
정책 1건이 기간별 목표율 N개를 가진다.

```
PolicyTargetRate(target_rate_id, policy_id, target_rate, effective_from, effective_to, created_at)
```

- **장점**: 목표율 이력·연도별 관리 가능(법정 목표율 변경 대응). 근거(Audit)와 잘 맞는다.
- **단점**: 구조·조회가 복잡(기준일에 유효한 목표율 선택 로직 필요). 현재 단계에는 과설계일 수 있다.

### 방안 C — 설정 파일/코드 상수로 관리
`config` 또는 상수 모듈에 `{policy_code: target_rate}` 를 둔다.

- **장점**: DB 변경이 전혀 없다. 가장 빠르게 도입 가능.
- **단점**: "시스템에서 관리"(R1)라는 목표에 미달(운영 중 수정 어려움, 정본이 코드에 묶임).
  DB 정본 원칙과 어긋난다.

### 4.1 비교표

| 기준 | A: Policy 컬럼 | B: 별도 테이블 | C: 설정/상수 |
|---|---|---|---|
| R1 시스템 관리 | ✅ | ✅ | △(코드에 묶임) |
| R2 Dashboard 조회 | ✅ 단순 | ✅ 조회 로직 필요 | ✅ |
| R5 이력 관리 | ❌ | ✅ | ❌ |
| 구현 복잡도 | 낮음 | 높음 | 매우 낮음 |
| DB 변경 규모 | 컬럼 1개 | 테이블 1개 | 없음 |
| 현재 단계 적합성 | ✅ | 과설계 | 목표 미달 |

---

## 5. 권장안

### 5.1 데이터 구조: **방안 A (Policy.target_rate 컬럼 추가)** 권장
- 현재 단계(Dashboard 도입기)에서 R1·R2·R3 를 가장 단순하게 만족한다.
- 이력 관리(R5)는 실제 요구가 확인될 때 **방안 B 로 확장**할 수 있다(정책→목표율 1:1 을
  1:N 으로 승격). 지금 B 를 도입하는 것은 과설계로 판단.

### 5.2 데이터 타입/제약 (제안)
- 타입: `TEXT`(기존 Decimal 저장 규약과 동일하게 문자열로 저장) — 모델에서는 `Decimal`.
- 의미: 목표 구매비율(%) . 예: 50% → `Decimal("50")`.
- 제약: `0 < target_rate` (기존 Calculator 의 `target_rate <= 0` 거부 규칙과 정합).
- 미설정 처리(R4): 아래 5.4 참조.

### 5.3 DashboardDataService 개선 방향 (R2, R3)
**Calculator 는 절대 변경하지 않는다.** 목표율 조회 책임은 Dashboard 계층에 둔다.

- `DashboardDataService` 가 `PolicyRepository`(또는 목표율 조회용 Repository)를 추가로 주입받는다.
- 신규 메서드(예시): `build_summary_from_registered_targets()` — 활성 정책들의 목표율을
  DB 에서 조회해 `{policy_id: target_rate}` dict 를 구성한 뒤, **기존** `Calculator.calculate_all()` 에 전달.
- 기존 `build_summary(target_rates)` 는 **그대로 유지**(외부 주입 방식도 계속 지원 → 하위호환).

```
[개선 후]
DashboardDataService.build_summary_from_registered_targets()
   └─ PolicyRepository 에서 활성 정책 + target_rate 조회
   └─ {policy_id: target_rate} 구성
   └─ Calculator.calculate_all(dict)   ← 계산기 시그니처/로직 불변
```

→ 목표율을 "외부 입력 없이 조회"(R2)하면서도 **Calculator 는 dict 를 받는 구조 그대로 호환**(R3).

### 5.4 미설정 목표율 처리 (R4) — 확정 필요
목표율이 없는(NULL) 정책을 요약에 어떻게 넣을지 정책 결정이 필요하다. 후보:

- (a) **요약에서 제외** — 목표율 없는 정책은 대시보드에 표시하지 않음.
- (b) **에러** — 활성 정책인데 목표율이 없으면 검증 예외.
- (c) **기본값** — 시스템 기본 목표율(예: 정의된 상수)로 대체.

→ 권장: **(a) 제외** (초기 단계에서 가장 안전, 데이터 정비 전에도 화면이 깨지지 않음).
   단, 관리 화면에서 "목표율 미설정" 을 별도로 노출하는 것은 이후 UI 이슈에서 고려.

---

## 6. DB 영향 및 마이그레이션 검토

> 실제 변경은 본 명세 승인 후 후속 Issue 에서 수행한다.

- 변경 범위: `Policy` 테이블에 `target_rate TEXT` 컬럼 1개 추가.
- Required 여부: 기존 데이터 호환을 위해 **초기에는 NULL 허용**(선택) 권장. 이후 데이터 정비
  완료 시 NOT NULL 승격 여부 재검토.
- 영향 파일(예상): `docs/DATABASE_DESIGN.md`(Policy 정의), `Policy` 모델, `PolicyRepository`
  (CREATE TABLE / insert / row 매핑 / 검증). **Calculator·Dashboard 계산 로직은 무영향.**
- 하위호환: 컬럼 추가만으로 기존 조회/집계는 영향 없음. `evaluation_basis` 추가(Issue #14)와
  동일한 "데이터 구조 확장" 패턴을 따른다.

---

## 7. 구현 여부 판단

- **구현 진행 권장**: R1·R2 는 Dashboard API(#21 이후)의 선행 조건이므로 필요하다.
- **권장 순서**:
  1. **#20-1 (DB/모델)**: `Policy.target_rate` 컬럼 추가 (방안 A). 데이터 구조 전용,
     계산 로직 무변경 — Issue #14 와 동일한 축소된 범위.
  2. **#20-2 (Dashboard 조회)**: `DashboardDataService` 에 목표율 자동 조회 메서드 추가.
     기존 `build_summary(target_rates)` 는 유지. 미설정 처리(5.4)는 (a) 제외로.
- **보류/후속**: 방안 B(이력 테이블)는 연도별 목표율 요구가 실제로 확인될 때 별도 Issue.

---

## 8. PM 확인 필요 사항 (Open Questions)

| # | 질문 | 기본 제안 |
|---|---|---|
| Q1 | 데이터 구조를 방안 A(Policy 컬럼)로 진행할까? | A 권장 |
| Q2 | 목표율 미설정 정책의 처리 = 제외/에러/기본값 중? | (a) 제외 |
| Q3 | `target_rate` 초기 NULL 허용 여부 | NULL 허용 후 정비 |
| Q4 | 연도별 이력(방안 B)은 지금 필요 없는가? | 지금은 불필요(후속) |
| Q5 | 목표율 등록/수정 UI·API 는 이후 이슈로 분리하는가? | 분리 권장 |

---

## 9. 요약

- 현재 목표율은 저장되지 않고 호출자가 주입 → 정본 부재.
- **방안 A(Policy.target_rate 컬럼)** 로 목표율을 시스템에서 관리하고,
  **DashboardDataService 가 조회**하도록 개선하되, **Calculator 는 그대로**(dict 입력 유지) 두어 호환.
- DB 변경은 컬럼 1개 추가 수준으로, 계산 로직에는 영향 없음.
- 구현은 **#20-1(DB/모델) → #20-2(Dashboard 조회)** 2단계 권장.
- 위 Open Questions(특히 Q2 미설정 처리) 확정 후 구현 착수.
