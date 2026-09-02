# 연도별 · 정책별 목표비율 — 구현 결과 (2026-09-02 · STEP 93)

`TARGET_RATE_STRUCTURE_ANALYSIS.md` 의 분석과 `DECISIONS.md` §0.20 의 확정을
실제 코드로 옮긴 결과다.

---

## 1. 무엇을 만들었나

```
사용자 화면 (연도 + 정책 + 목표비율)
      ↓  PUT /policy-targets/{year}/{policy_code}
PolicyTargetAdminService → PolicyTargetRepository → policy_target 테이블
                                                          ↓
                          DashboardDataService._resolve_target_rates(연도)
                                                          ↓
                          {policy_id: 목표비율}  ← 기존 계산기가 받는 모양 그대로
                                                          ↓
                          ProcurementAchievementCalculator (변경 없음)
```

⭐ **계산기·판정 규칙·기간 필터·인증 처리는 한 줄도 바뀌지 않았다.** 분석에서
예측한 대로, 바뀐 것은 **목표비율을 어디서 읽는가** 뿐이다.

---

## 2. DB

```sql
CREATE TABLE IF NOT EXISTS policy_target (
    policy_target_id INTEGER PRIMARY KEY,
    year             INTEGER NOT NULL,
    policy_id        INTEGER NOT NULL,
    target_rate      TEXT NOT NULL,
    created_at       DATETIME NOT NULL,
    updated_at       DATETIME NOT NULL,
    UNIQUE (year, policy_id),
    FOREIGN KEY (policy_id) REFERENCES policy (policy_id)
)
```

| 결정 | 이유 |
|---|---|
| `UNIQUE (year, policy_id)` | 한 연도의 한 정책에는 목표비율이 **하나만**. 애플리케이션을 우회해도 DB 가 막는다 |
| `target_rate TEXT` | 기존 `policy.target_rate` 와 같은 방식. `REAL` 이면 `37.5` 가 부동소수 오차로 그대로 돌아오지 않는다 |
| **`company_id` 없음** | ⛔ 목표비율의 축은 **연도 × 정책** 뿐이다(§0.20) |
| 해제 = **행 삭제** | 0 은 "미설정" 이 아니다. 행을 지워야 조회가 `None` 이 되어 기존 미설정 경로를 그대로 탄다 |

기존 DB 에는 `init_db()` 가 `CREATE TABLE IF NOT EXISTS` 로 더한다. ⛔ 기존
`policy` 테이블과 그 `target_rate` 컬럼은 건드리지 않는다.

---

## 3. API

| 메서드 | 경로 | 권한 |
|---|---|---|
| GET | `/policy-targets?year=2026` | 없음(조회) |
| PUT | `/policy-targets/{year}/{policy_code}` | `ADMIN_API_TOKEN` — **기존 규칙 그대로** |

**GET 응답** — 활성 정책 **전체**가 담긴다. 미설정 정책도 빼지 않는다.

```json
{
  "year": 2026,
  "items": [
    {"year": 2026, "policy_id": 1, "policy_code": "SMALL_BUSINESS",
     "policy_name": "중소기업", "is_active": true,
     "target_rate": "50", "target_rate_status": "SET",
     "updated_at": "2026-09-02T13:00:00"},
    {"year": 2026, "policy_id": 3, "policy_code": "DISABLED",
     "policy_name": "장애인기업", "is_active": true,
     "target_rate": null, "target_rate_status": "NOT_SET",
     "updated_at": null}
  ]
}
```

- 정책 코드와 **정책명**을 함께 준다 → ⛔ 화면이 정책명을 들고 있지 않는다
- `target_rate` 는 **문자열** → `Decimal` 정밀도 보존
- `target_rate_status` 를 함께 준다 → `null` 을 0 으로 오해할 수 없다

**PUT 요청** — `{"target_rate": "37.5"}` / 해제는 `{"target_rate": null}`

- **멱등**하다. 같은 값을 몇 번 보내도 행이 하나다
- `target_rate` 키가 **없으면 422** → "바꾸지 않음" 과 "해제" 를 구분한다
- JSON number 는 **422** → `float` 를 거치면 `37.5` 의 정밀도가 깨진다
- 비활성 정책(GREEN)은 **422** → 계산 대상이 아닌 정책에 목표를 두지 않는다

---

## 4. 화면

「정책별 달성률」 **바로 아래**에 "목표비율 관리" 카드 하나를 더했다. 목표비율이
없어 달성률이 안 나온다는 사실을 보는 그 자리에서 바로 입력하게 하기 위해서다.

```
목표비율 관리                          [ 연도 2026 ▼ ]  [저장]
──────────────────────────────────────────────────────
중소기업        [ 50    ] %
여성기업        [ 60    ] %
장애인기업      [       ] %   ← 비워 두면 미설정
창업기업        [ 10    ] %
```

- 정책 목록·정책명은 **서버가 준다**(⛔ 하드코딩 없음)
- 연도를 바꾸면 **그 연도 값을 다시 읽는다**(⛔ 화면에 남은 값을 재사용하지 않음)
- **바뀐 칸만** 저장 요청을 보낸다 → 손대지 않은 정책·다른 연도는 그대로다
- 빈칸은 `null`(해제)로 보낸다 → ⛔ 0 으로 바꿔 보내지 않는다
- ⛔ **기업을 고르는 입력이 없다**

---

## 5. 계산 검증 — PM 확정 예제(§13)

목표비율을 **API 로 넣고** 대시보드가 계산한 실제 값이다(합성 데이터).

| 거래처 | 지출 | 여성 | 창업 | 중소 |
|---|---|---|---|---|
| A | 60만 | O | O | O |
| B | 40만 | O | X | O |
| C | 20만 | X | O | O |

기관 전체 지출 = **120만원**

| 정책 | 목표 | 실적 | 구매비율 | 달성률 | §13 기대 |
|---|---|---|---|---|---|
| 중소기업 | 50% | 1,200,000 | 100.00% | **200.00%** | 200% ✅ |
| 여성기업 | 60% | 1,000,000 | 83.33% | **138.89%** | 138.89% ✅ |
| 창업기업 | 10% | 800,000 | 66.67% | **666.67%** | 666.67% ✅ |
| 장애인기업 | 미설정 | — | — | **미계산** | — |

⭐ **정책 실적 합계 300만원 > 기관 전체 120만원 — 정상이다.** A기업의 60만원이
세 정책 실적에 모두 들어간다. ⛔ 정책 간 지출을 차감하거나 배타적으로 나누지
않는다.

---

## 6. ⛔ 하지 않은 것

| 금지 항목 | 확인 |
|---|---|
| 구매처별 목표비율 | `policy_target` 컬럼에 `company_id`·`business_no` 없음 |
| 기관 테이블 | `institution` · `organization` 테이블 없음 |
| `purchase.institution_id` | 컬럼 없음 |
| 계산기 구조 변경 | `calculators/` 변경 0줄 |
| 인증 판정 변경 | `rules/` 변경 0줄 |
| 결의일자 기준 변경 | `core/period.py` 변경 0줄 · 설정 기본값 그대로 |
| 신규 정책 추가 | `MVP_POLICY_SEEDS` 5종 그대로 |
| 목표비율을 20/40/60/80/100 으로 제한 | `0 < x <= 100`. `37`·`42.5` 정상 입력 |
| `Policy.target_rate` 삭제 | 컬럼 유지 |
| 기존 target-rate API 삭제 | `PUT /policies/{code}/target-rate` 유지 |

전부 시험으로 고정했다(`tests/test_policy_target_rate.py`
`TestForbiddenThingsWereNotDone`).

---

## 7. 기존 것을 남긴 이유

| 무엇 | 왜 남겼나 |
|---|---|
| `policy.target_rate` 컬럼 | 기존 테스트·코드 호환. 마이그레이션 위험 회피 |
| `PUT /policies/{code}/target-rate` | 기존 테스트가 검증 중. 새 경로와 충돌하지 않는다 |
| `Policy.target_rate` 읽는 하위호환 경로 | `policy_target_repository` 를 주입하지 않은 호출부(기존 테스트)를 깨지 않기 위해 |

⛔ **다만 운영 경로는 새 경로 하나다.** `app.py` 의 조립 지점이
`PolicyTargetRepository` 를 주입하므로, 실제 대시보드는 **연도별 목표비율만**
읽는다. `policy.target_rate` 에 값이 남아 있어도 계산에 쓰이지 않는다 —
시험으로 고정했다(`test_the_legacy_column_is_not_read`).

---

## 8. 다음에 정리할 수 있는 것 (⛔ 이번 범위 아님)

- `policy.target_rate` 컬럼과 구 API 의 제거 — 기존 테스트를 함께 정리해야 한다
- `collectors/sync_service.py` — STEP 92 에서 확인한 **도달하지 않는** 인증
  저장 경로. 동작에는 영향이 없다
