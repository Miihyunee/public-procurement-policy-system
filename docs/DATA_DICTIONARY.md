# Data Dictionary

## 문서 정보

| 항목 | 내용 |
|------|------|
| Version | v1.0 |
| Status | Draft |
| Last Updated | 2026-08-06 |
| 관련 문서 | `DATABASE_DESIGN.md`, `DATA_DICTIONARY.md`, `DATA_ACQUISITION_PLAN.md`, `PURCHASE_DATA_SPEC.md` |

---

# 1. 목적

본 문서는 시스템이 관리하는 **주요 데이터 항목의 표준 정의**를 제공한다.

동일한 개념이 기관마다 다른 이름으로 제공되므로(예: 사업자번호 / 사업자등록번호 / 업체번호),
**시스템 내부에서 사용할 하나의 표준 이름과 의미를 고정**하여 혼선을 방지하는 것이 목적이다.

- 실제 컬럼 정의(테이블 구조)는 `DATABASE_DESIGN.md`가 기준이다.
- 외부 데이터 → 표준 컬럼 변환 규칙은 `DATA_DICTIONARY.md`가 기준이다.
- 본 문서는 **항목의 의미·타입·출처·사용 목적**을 설명한다.

## 표기 규칙

| 표기 | 의미 |
|---|---|
| 출처 · **외부수집** | 외부 기관에서 수집하는 데이터 |
| 출처 · **고객제공** | 고객(공공기관)이 제공하는 데이터 |
| 출처 · **시스템생성** | 시스템이 저장·계산 과정에서 생성 |
| 출처 · **운영설정** | 운영자가 등록·관리하는 기준 정보 |

---

# 2. 논리명 · 물리명 대조표 (DB / API 기준)

> 향후 **DB와 API의 기준 문서**로 사용하는 표다.
> 물리명은 **현재 구현된 실제 컬럼명**이며, 코드·DB·테스트에 이미 적용되어 있다.

## 2.1 기업 (Company)

| 논리명 | 물리명 | 타입 | 설명 |
|---|---|---|---|
| 기업 내부 ID | `company_id` | INTEGER (PK) | 시스템이 부여하는 기업 고유 번호 |
| 사업자등록번호 | `business_no` | TEXT (Unique) | 10자리 숫자. 모든 데이터 결합의 기준 키 |
| 기업명 | `company_name` | TEXT | 상호 |
| 대표자명 | `representative_name` | TEXT | 대표자 성명 (nullable 허용 예정) |
| 생성일시 | `created_at` | DATETIME | 데이터 생성 시각 |
| 수정일시 | `updated_at` | DATETIME | 데이터 최종 수정 시각 |

## 2.2 정책 (Policy)

| 논리명 | 물리명 | 타입 | 설명 |
|---|---|---|---|
| 정책 내부 ID | `policy_id` | INTEGER (PK) | 정책 고유 번호 |
| 정책코드 | `policy_code` | TEXT (Unique) | 정책 식별 코드 (예: `SMALL_BUSINESS`) |
| 정책명 | `policy_name` | TEXT | 화면 표시용 정책 이름 |
| 정책설명 | `description` | TEXT (NULL) | 정책 부가 설명 |
| 사용여부 | `is_active` | BOOLEAN | 계산 대상 포함 여부 |
| 판정기준일 유형 | `evaluation_basis` | TEXT | `PAYMENT_DATE` / `CONTRACT_DATE` |
| 목표 구매비율 | `target_rate` | TEXT(Decimal) (NULL) | 목표율(%). NULL이면 계산 제외 |
| 생성일시 | `created_at` | DATETIME | |
| 수정일시 | `updated_at` | DATETIME | |

## 2.3 인증 (Certification)

| 논리명 | 물리명 | 타입 | 설명 |
|---|---|---|---|
| 인증 내부 ID | `certification_id` | INTEGER (PK) | 인증 고유 번호 |
| 기업 ID | `company_id` | INTEGER (FK) | 대상 기업 |
| 정책 ID | `policy_id` | INTEGER (FK) | 대상 정책 |
| 인증시작일 | `valid_from` | DATE | 인증 유효기간 시작 (nullable 허용 예정) |
| 인증종료일 | `valid_to` | DATE | 인증 유효기간 종료 (nullable 허용 예정) |
| 인증번호 | `certificate_number` | TEXT (NULL) | 확인서·인증서 번호 |
| 발급기관 | `issuing_agency` | TEXT (NULL) | 인증 발급 기관명 |
| 생성일시 | `created_at` | DATETIME | |
| 수정일시 | `updated_at` | DATETIME | |

## 2.4 구매 (Purchase)

| 논리명 | 물리명 | 타입 | 설명 |
|---|---|---|---|
| 구매 내부 ID | `purchase_id` | INTEGER (PK) | 구매 건 고유 번호 |
| 사업자등록번호 | `business_no` | TEXT | 공급업체 사업자등록번호 (중복 가능) |
| 업체명 | `company_name` | TEXT | 공급업체 상호 |
| 기업 ID | `company_id` | INTEGER (FK, NULL) | 매칭된 기업. 매칭 전 NULL |
| 계약일 | `contract_date` | DATE | 창업기업 판정 기준일 |
| 지급일 | `payment_date` | DATE | 일반 정책 판정 기준일 |
| **구매금액** | **`amount`** | TEXT(Decimal) | 구매(지출) 금액. 0 초과 |
| 생성일시 | `created_at` | DATETIME | |
| 수정일시 | `updated_at` | DATETIME | |

## 2.5 계산 결과 (API 응답 · 저장 대상 아님)

| 논리명 | 물리명 | 타입 | 설명 |
|---|---|---|---|
| 전체 구매액 | `total_purchase_amount` | string(Decimal) | 기관 전체 구매 합계 |
| 정책별 구매액 | `purchase_amount` | string(Decimal) | 해당 정책 인정 구매 합계 |
| 목표 구매비율 | `target_rate` | string(Decimal) | 정책 목표율(%) |
| 달성률 | `achievement_rate` | string(Decimal) | 목표 대비 달성 정도(%) |
| 부족률 | `shortage_rate` | string(Decimal) | `max(0, 100 - 달성률)` |
| 달성상태 코드 | `status` | string | `NORMAL` / `WARNING` / `SHORTAGE` |
| 달성상태 표시명 | `status_label` | string | 정상 / 주의 / 부족 |

> API 응답에서 금액·비율은 **정밀도 보존을 위해 문자열로 직렬화**된다.

## 2.6 명명 규칙 — **D-001 확정** ✅

> **PM 결정 (D-001)**: **현재 구현된 물리명을 유지**한다.
> 기존 코드와의 일관성을 우선하며, **물리명 변경을 위한 리팩터링은 수행하지 않는다.**
> 논리명과 물리명의 차이는 **본 문서에서 관리**한다.

| 논리명 | 참고 명칭(초기 논의) | **확정 물리명** | 비고 |
|---|---|---|---|
| 사업자등록번호 | `business_registration_number` | **`business_no`** | 4개 테이블·Repository·테스트에 적용됨 |
| 구매금액 | `purchase_amount` | **`amount`** (Purchase 테이블) | 아래 주의사항 참조 |
| 지급일 | `payment_date` | `payment_date` | 초기 논의와 일치 |

### ⚠️ `amount` vs `purchase_amount` 구분 (혼동 주의)

두 이름은 **서로 다른 값**이므로 혼용하면 안 된다.

| 이름 | 의미 | 위치 |
|---|---|---|
| **`amount`** | **구매 건별 금액** (원천 데이터) | `Purchase` 테이블 컬럼 |
| **`purchase_amount`** | **정책별 구매액 합계** (계산 결과) | API 응답 필드 |

→ 신규 코드·문서 작성 시 위 구분을 따른다.

---

# 3. 핵심 식별 항목

## 3.1 business_no — 사업자등록번호

| 항목 | 내용 |
|---|---|
| **의미** | 국세청이 부여하는 사업자 고유번호. 10자리 숫자 |
| **데이터 타입** | `str` (TEXT) |
| **출처** | 외부수집 + 고객제공 (양쪽에 모두 존재) |
| **사용 목적** | **모든 데이터 결합의 기준 키**. 구매 데이터와 인증 데이터를 연결 |
| **저장 형식** | 하이픈 제거한 숫자 10자리 (`1234567890`) |
| **주의사항** | 기관별로 `123-45-67890` 형태로 제공되므로 **수집 시 정규화 필수**. 앞자리 `0`이 있을 수 있어 **문자열로 저장**한다(숫자 변환 금지) |
| **사용 위치** | `Company.business_no`(Unique), `Purchase.business_no` |

> ⚠️ `Purchase.business_no`는 **중복 가능**하다(한 업체에서 여러 건 구매). 반면
> `Company.business_no`는 **Unique**다.

## 3.2 company_id — 기업 내부 ID

| 항목 | 내용 |
|---|---|
| **의미** | 시스템이 부여하는 기업 고유 번호 (Primary Key) |
| **데이터 타입** | `int` |
| **출처** | 시스템생성 |
| **사용 목적** | 테이블 간 참조. `Certification`·`Purchase`가 기업을 가리킬 때 사용 |
| **주의사항** | `Purchase.company_id`는 **매칭 전에는 `None`**. 매칭에 실패한 구매는 정책 실적으로 인정되지 않고 전체 구매액에만 포함된다 |

## 3.3 policy_id / policy_code — 정책 식별자

| 항목 | 내용 |
|---|---|
| **의미** | `policy_id`는 내부 고유 번호, `policy_code`는 정책을 식별하는 코드값 |
| **데이터 타입** | `policy_id`: `int` / `policy_code`: `str` (Unique) |
| **출처** | 운영설정 |
| **사용 목적** | 정책별 실적 집계 및 목표율 관리의 기준 |
| **값 예시** | `SMALL_BUSINESS`, `WOMAN`, `DISABLED`, `STARTUP`, `GREEN_PRODUCT` (**확정 필요**) |
| **주의사항** | 코드값은 한 번 정하면 데이터에 고정되므로 **초기 확정이 중요**하다 |

---

# 4. 기업 정보 (Company)

| 표준 항목 | 의미 | 타입 | 출처 | 사용 목적 | 비고 |
|---|---|---|---|---|---|
| `business_no` | 사업자등록번호 | `str` | 외부수집·고객제공 | 결합 기준 키 | Unique, 필수 |
| `company_name` | 기업명(상호) | `str` | 외부수집·고객제공 | 표시, 보조 매칭 | 필수 |
| `representative_name` | 대표자명 | `str` | 외부수집 | 기업 정보 검증 | 현재 필수 → **nullable 전환 예정**(아래 참조) |
| `company_id` | 내부 고유 ID | `int` | 시스템생성 | 참조 키 | PK |
| `created_at` / `updated_at` | 생성·수정 일시 | `datetime` | 시스템생성 | 이력 관리 | |

> ✅ **PM 승인 완료 — `representative_name` nullable 허용**
> 인증 조회 API가 대표자명을 제공하지 않을 수 있어, 수집 데이터만으로 기업을 생성할 수 없는
> 문제가 있었다. **nullable 허용 방향으로 승인**되었으며, 스키마 변경은 **Sprint B(구축)**
> 에서 Bootstrap 구현과 함께 반영한다. (`DATA_ACQUISITION_PLAN.md` R-4)

---

# 5. 인증 정보 (Certification)

기업이 특정 정책의 자격을 보유했음을 나타내는 데이터. **외부 수집의 핵심 대상**이다.

| 표준 항목 | 의미 | 타입 | 출처 | 사용 목적 | 비고 |
|---|---|---|---|---|---|
| `company_id` | 대상 기업 | `int` | 시스템생성 | 기업 연결 | 필수 |
| `policy_id` | 대상 정책 | `int` | 운영설정 | 정책 연결 | 필수 |
| `valid_from` | **인증 시작일** | `date` | 외부수집 | **유효기간 판정** | 현재 필수 → **nullable 전환 예정** |
| `valid_to` | **인증 종료일** | `date` | 외부수집 | **유효기간 판정** | 현재 필수 → **nullable 전환 예정** |
| `certificate_number` | 인증서 번호 | `str \| None` | 외부수집 | 증빙·추적 | 선택 |
| `issuing_agency` | 발급기관 | `str \| None` | 외부수집 | 출처 관리 | 선택 |
| `certification_id` | 내부 고유 ID | `int` | 시스템생성 | PK | |

## 5.1 유효기간의 중요성

이 시스템의 판정 규칙은 다음과 같다.

```
valid_from  <=  판정 기준일  <=  valid_to     → 실적 인정
```

- 경계값을 **포함**한다(inclusive).
- 즉 **인증 유효기간이 없으면 실적 판정이 불가능**하다.
- 따라서 수집 시 유효기간 확보가 **가장 중요한 요구사항**이다.

> ✅ **PM 승인 완료 — `valid_from` / `valid_to` nullable 허용**
> Backlog 정책(사회적기업·자활기업·자활용사촌)은 유효기간이 제공되지 않을 수 있어
> **nullable 허용 방향으로 승인**되었다. 스키마 변경은 **Sprint B(구축)** 에서 반영한다.
>
> ⚠️ 단, **유효기간이 NULL인 경우의 판정 규칙은 아직 정의되지 않았다.**
> (기간 무관으로 인정 / 계산 제외 등) Backlog 정책 확장 시 Rule Engine에서 정의해야 한다.
> **MVP 5종은 모두 유효기간이 제공되므로 이번 범위에서는 영향이 없다.**
> (`DATA_ACQUISITION_PLAN.md` R-3)

- 동일 기업이 같은 정책 인증을 **여러 건** 보유할 수 있으며, 그중 하나라도 조건을 만족하면 인정한다.

---

# 6. 정책 정보 (Policy)

| 표준 항목 | 의미 | 타입 | 출처 | 사용 목적 | 비고 |
|---|---|---|---|---|---|
| `policy_code` | 정책 코드 | `str` | 운영설정 | 정책 식별 | Unique, 필수 |
| `policy_name` | 정책명 | `str` | 운영설정 | 화면 표시 | 필수 |
| `description` | 정책 설명 | `str \| None` | 운영설정 | 참고 | 선택 |
| `is_active` | 사용 여부 | `bool` | 운영설정 | 계산 대상 필터 | 필수, 기본 `True` |
| `evaluation_basis` | **판정 기준일 유형** | `str` | 운영설정 | 기준일 선택 | 필수. `PAYMENT_DATE` / `CONTRACT_DATE` |
| `target_rate` | **목표 구매비율(%)** | `Decimal \| None` | 운영설정 | 달성률 계산 | 선택(NULL 허용). 값이 있으면 0 초과 |

## 6.1 evaluation_basis 값

| 값 | 의미 | 적용 정책 (현행) |
|---|---|---|
| `RESOLUTION_DATE` | **결의일자** 기준 | 중소기업, 여성기업, 장애인기업 |
| `RESOLUTION_OR_CONTRACT_DATE` | 결의일자 **또는** 계약일자 기준 | 창업기업 |
| `PAYMENT_DATE` | 대금 지급일 기준 | 녹색제품(**비활성** — MVP 계산 제외) |
| `CONTRACT_DATE` | 계약일 기준 | 현재 사용하는 활성 정책 없음 |

> ⚠️ 2026-08-31 · 2026-08-14 고객 확정 반영(`DECISIONS.md` §0.12.1).
> ⛔ 신고기준일(`issue_date`)은 판정에도 연도 귀속에도 쓰지 않는다.

## 6.2 target_rate

- 정책별 **목표 구매비율(%)** 이며 달성률 계산의 기준이 된다.
- **NULL이면 대시보드 계산 대상에서 제외**된다(목표가 없으므로 달성률을 낼 수 없음).
- ⚠️ 법정 비율 값은 아직 확정되지 않았다. (Bootstrap Issue #23의 미결 사항)

> ⚠️ **2026-09-02(STEP 93) 이후 `policy.target_rate` 는 계산에 쓰이지 않는다.**
> 목표비율의 정본은 **`policy_target`(연도 × 정책)** 이다 — 아래 6.3.
> 이 컬럼은 기존 코드·테스트 호환을 위해 남겨 두었을 뿐이다.

## 6.3 목표비율 (PolicyTarget) — **연도 × 정책**

| 표준 항목 | 물리명 | 타입 | 비고 |
|---|---|---|---|
| 대상 연도 | `year` | INTEGER | 구매의 **결의일자 연도**와 맞춘다 |
| 정책 | `policy_id` | INTEGER (FK) | |
| 목표 구매비율 | `target_rate` | TEXT(Decimal) | 0 초과 100 이하. `37.5` 같은 임의 값 허용 |

- ⛔ **구매처(Company) 단위가 아니다.** 목표비율은 *"기관 전체 지출 중 그 정책의
  인증기업에 지출한 금액이 차지해야 하는 비율"* 이므로 축은 연도 × 정책 뿐이다
  (`DECISIONS.md` §0.20).
- ⛔ **미설정 ≠ 0%.** 행이 없는 것이 미설정이며, 다른 연도 값이나
  `policy.target_rate` 로 메우지 않는다.
- ⛔ 화면의 달성률 표시 구간(20/40/60/80/100)은 **표시 기준**이며 목표비율
  입력값을 그 값들로 제한하지 않는다.
- ⭐ 한 거래처가 여러 정책 인증을 가지면 그 지출은 **각 정책 실적에 모두** 들어간다.
  따라서 **정책 실적의 합계가 기관 전체 지출을 넘을 수 있고, 그것이 정상**이다.

---

# 7. 구매 정보 (Purchase)

고객이 제공하는 지출 데이터. 상세 규격은 `PURCHASE_DATA_SPEC.md` 참조.

| 표준 항목 | 의미 | 타입 | 출처 | 사용 목적 | 비고 |
|---|---|---|---|---|---|
| `business_no` | 공급업체 사업자등록번호 | `str` | 고객제공 | **기업 매칭 키** | 필수 |
| `company_name` | 공급업체명 | `str` | 고객제공 | 표시·보조 매칭 | 필수 |
| `contract_date` | **계약일** | `date` | 고객제공 | 창업기업 판정 기준일 | 필수 |
| `payment_date` | **대금 지급일**(지출완료일) | `date` | 고객제공 | 일반 정책 판정 기준일 | 필수 |
| `amount` | **구매금액** | `Decimal` | 고객제공 | 실적 집계 | 필수, 0 초과 |
| `company_id` | 매칭된 기업 | `int \| None` | 시스템생성 | 인증 연결 | 매칭 전 `None` |
| `purchase_id` | 내부 고유 ID | `int` | 시스템생성 | PK | |

## 7.1 금액 취급 원칙

- 금액은 **`Decimal`** 로 다루며 부동소수(float)를 사용하지 않는다.
- 저장 시 **문자열**로 보관하여 정밀도를 보존한다.
- API 응답에서도 **문자열**로 직렬화한다(정밀도 손실 방지).

## 7.2 날짜 항목 주의

- `contract_date`와 `payment_date`는 **둘 다 필수**다.
  정책마다 사용하는 기준일이 다르므로, 하나만 있으면 일부 정책을 계산할 수 없다.
- 고객 데이터에 지급일이 없는 경우가 실무에서 흔하므로 **사전 확인이 필요**하다.

---

# 8. 계산 결과 항목 (시스템 생성)

계산 과정에서 생성되며 저장 대상이 아닌 **파생 값**이다.

| 항목 | 의미 | 타입 | 산출 방법 |
|---|---|---|---|
| `total_purchase_amount` | 기관 전체 구매액 | `Decimal` | 전체 구매 합계 |
| `purchase_amount` | 정책별 구매액 | `Decimal` | 해당 정책 인정 구매 합계 |
| `achievement_rate` | **달성률(%)** | `Decimal` | (정책 구매비율 ÷ 목표율) × 100 |
| `shortage_rate` | **부족률(%)** | `Decimal` | `max(0, 100 - 달성률)` |
| `status` | 달성 상태 코드 | `str` | `NORMAL` / `WARNING` / `SHORTAGE` |
| `status_label` | 달성 상태 표시명 | `str` | 정상 / 주의 / 부족 |

## 8.1 status 판정 기준

| 상태 | 조건 | 표시명 |
|---|---|---|
| `NORMAL` | 달성률 ≥ 100 | 정상 |
| `WARNING` | 80 ≤ 달성률 < 100 | 주의 |
| `SHORTAGE` | 달성률 < 80 | 부족 |

---

# 9. 수집 관리 항목 (제안 — 미구현)

수집 이력 관리를 위해 향후 필요한 항목이다. (`DATA_ACQUISITION_PLAN.md`의 "수집 이력 관리" 원칙)

| 제안 항목 | 의미 | 타입 | 목적 |
|---|---|---|---|
| `source` | 데이터 출처 식별자 | `str` | DS-001 등. 문제 발생 시 추적 |
| `collected_at` | 수집 일시 | `datetime` | 최신성 확인 |
| `source_updated_at` | 원본 기준일자 | `date` | 원본 데이터 시점 관리 |

> 현재 `Certification`에는 `issuing_agency`만 있고 수집 시점 정보가 없다.
> 데이터 신뢰성 추적을 위해 추가 검토를 권장한다.

---

# 10. 용어 정리

| 용어 | 설명 |
|---|---|
| **판정 기준일** | 인증 유효 여부를 판단하는 날짜. 현행은 **결의일자**이며 창업기업만 결의일자 OR 계약일자 |
| **우선구매** | 공공기관이 특정 정책 대상 기업 제품을 우선 구매하도록 한 제도 |
| **목표율(target_rate)** | 전체 구매액 대비 해당 정책이 차지해야 할 목표 비율(%). **연도 × 정책** 단위로 관리한다 |
| **달성률(achievement_rate)** | 목표 대비 실제 달성 정도(%). 100이면 목표 달성 |
| **매칭(Matching)** | 구매 데이터의 사업자번호로 기업을 찾아 연결하는 과정 |
