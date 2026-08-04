# Issue #11 — 실제 업무 기준 반영을 위한 분석 및 설계안

## 문서 정보

| 항목 | 내용 |
|------|------|
| Version | v1.0 (제안/Draft) |
| Status | PM 검토 대기 |
| Last Updated | 2026-08-04 |
| 성격 | **분석·설계 제안 문서** — 코드/DB/설계문서 변경 없음 |
| 관련 Issue | #10 (Calculator Foundation, 완료) |

> 본 문서는 Issue #11의 산출물(분석 보고서 + DATABASE_DESIGN 개정안 + 다음 Issue 제안서)이다.
> **이 문서 자체는 어떤 코드도 설계문서(DATABASE_DESIGN.md)도 변경하지 않는다.** PM 승인 후 별도 Issue에서 구현한다.

---

# Part 1. 분석 보고서

## 1.1 현재 Calculator 계산 방식 (Issue #10 결과)

`ProcurementAchievementCalculator.calculate_policy_purchase(policy_id)` 의 현재 로직:

```text
1) CertificationRepository.find_by_policy(policy_id) 로 해당 정책 인증 전체 조회
2) 인증의 company_id 집합을 구성 (유효기간 무시)
3) PurchaseRepository.find_all() 중 company_id 가 그 집합에 속하는 구매의 amount 합산
```

즉 **"해당 정책 인증을 (기간과 무관하게 한 번이라도) 보유한 기업의 모든 구매금액"** 을 합산한다.

## 1.2 문제점 분석

### 문제 1 — 인증 유효기간 미적용 (기존 설계 문서 위반)

`docs/POLICY_DEFINITION.md` 의 **공통 계산 원칙(94~102행)** 은 다음을 명시한다.

> - 인증 유효기간 내의 구매만 인정한다.
> - 구매일을 기준으로 인증 여부를 판단한다.

그러나 현재 Calculator 는 `Certification.valid_from` / `valid_to` 를 **전혀 참조하지 않는다.**

**결함 시나리오**

| 구분 | 값 |
|------|-----|
| Certification.valid_from | 2026-01-01 |
| Certification.valid_to | 2026-12-31 |
| Purchase.purchase_date | 2025-12-01 (인증 시작 전) |

→ 실무·설계상 **정책 실적에서 제외**되어야 하지만, 현재는 **포함**된다.
→ 인증 만료 이후(예: 2027-03-01) 구매도 동일하게 잘못 포함된다.

**영향**: 달성률이 실제보다 과대(overstated) 계산될 수 있어 감사 대응 시 신뢰성 문제로 직결된다.

### 문제 2 — 정책별 판정 기준 차이 미반영

현재는 모든 정책을 **동일한 로직(인증 보유 → 구매 합산)** 으로 처리한다. 그러나 실제 업무 기준은 정책마다 다르다.

| 정책 | 실무 판정 기준 (PM 전달) | POLICY_DEFINITION.md 기재 | 현재 구현 |
|------|--------------------------|---------------------------|-----------|
| 중소기업 | 대금 **지급일**이 유효기간 내 | "인증 여부 확인" | 유효기간 무시 |
| 여성기업 | 대금 **지급일**이 유효기간 내 | "인증 유효기간 확인" | 유효기간 무시 |
| 장애인기업 | 대금 **지급일**이 유효기간 내 | "인증 유효기간 확인" | 유효기간 무시 |
| 창업기업 | **계약시점(계약일)**이 유효기간 내 | "인증 유효기간 확인" | 유효기간 무시 |
| 녹색제품 | 제품/기업 기준 | "제품 또는 기업 기준 확인 필요" | 기업 인증 기준 |
| (향후) 자활용사촌 | **기간 무관**, 거래 유무 + 건별 **품목 갯수·금액** | (향후 지원 예정) | 미지원 |

**핵심 차이 3가지**
1. **일반 정책(중소·여성·장애인)** 은 **지급일** 기준, **창업기업** 은 **계약일** 기준 → 서로 다른 날짜를 봐야 한다.
2. **녹색제품** 은 "제품 기준" 판정 가능성 → 기업-인증 기준과 다른 축(품목 단위).
3. **자활용사촌** 은 기간을 보지 않고 **거래 유무 + 품목 갯수·금액** 을 집계 → 완전히 다른 계산 모델.

### 문제 3 — Purchase 데이터 구조의 표현 한계

현재 `Purchase` 컬럼: `purchase_id, business_no, company_id, company_name, purchase_date, amount, created_at, updated_at`

| 실무 요구 | 필요 데이터 | 현재 | 결과 |
|-----------|-------------|------|------|
| 일반 정책 = 지급일 기준 | 대금 지급일 | `purchase_date` 1개 (의미 미확정) | 지급일/계약일 구분 불가 |
| 창업기업 = 계약일 기준 | 계약일 | 없음 | **날짜 2종 동시 필요** → 현재 표현 불가 |
| 자활용사촌 = 품목 갯수 | 품목 수(수량) | 없음 | 갯수 집계 불가 |
| 녹색제품 = 제품 기준 | 품목/제품 정보 | 없음 | 제품 단위 판정 불가 |

`purchase_date` 하나로는 "계약일 기준"과 "지급일 기준"을 **동시에** 판정할 수 없다는 것이 가장 근본적인 제약이다.

## 1.3 문서 간 불일치 정리 (PM 확정 필요)

분석 과정에서 **설계 문서와 PM 전달 실무 규칙 사이의 불일치**를 발견했다. 임의 판단하지 않고 확정 요청한다.

| # | POLICY_DEFINITION.md | PM 전달 실무 규칙 | 확정 필요 사항 |
|---|----------------------|-------------------|----------------|
| A | "**구매일** 기준으로 판단" (모든 정책 동일) | 일반=**지급일**, 창업=**계약일** (정책별 상이) | 어느 것을 정본으로? |
| B | 유효기간 내 구매만 인정 | (동일) 유효기간 반영 | 일치 ✅ |
| C | 자활용사촌 = 향후 지원 예정 | 자활용사촌 규칙 언급 | MVP 포함 여부 |
| D | 녹색제품 = "제품 또는 기업 기준" | (미언급) | 기업 기준으로 단순화? |

---

# Part 2. DATABASE_DESIGN.md 개정안

> 아래는 **제안**이며, 실제 `DATABASE_DESIGN.md` 는 이 문서에서 변경하지 않는다.
> 두 가지 접근(A/B)을 제시하고 권고안을 명시한다.

## 2.1 접근 A — 최소 개정 (POLICY_DEFINITION 준수, 스키마 무변경)

`purchase_date` 를 **판정 기준일**로 사용하여 유효기간만 적용한다.

- **DATABASE_DESIGN.md 변경**: 없음 (컬럼 추가 없음)
- **효과**: 문제 1(유효기간 미적용) 즉시 해소. POLICY_DEFINITION.md 공통 원칙 준수.
- **한계**: 창업기업 "계약일 기준", 자활용사촌 "품목 갯수", 녹색제품 "제품 기준" 은 반영 불가.

## 2.2 접근 B — 실무 기준 완전 반영 (스키마 개정)

### 2.2.1 Purchase 테이블 개정 (컬럼 추가)

| Column | Type | Required | Description | 비고 |
|--------|------|----------|-------------|------|
| purchase_id | INTEGER | Yes | 내부 고유 ID (PK) | 기존 |
| business_no | TEXT | Yes | 사업자등록번호 | 기존 |
| company_id | INTEGER | No | Company 참조 (매칭 후) | 기존 |
| company_name | TEXT | Yes | 공급업체명 | 기존 |
| **contract_date** | DATE | Yes | **계약일 — 창업기업 판정 기준** | **신규** |
| **payment_date** | DATE | Yes | **대금 지급일(지출완료) — 일반 정책 판정 기준** | **신규** |
| amount | NUMERIC | Yes | 구매금액 | 기존 |
| **item_count** | INTEGER | No | **구매 품목 수 — 자활용사촌 집계용** | **신규** |
| created_at | DATETIME | Yes | 생성일시 | 기존 |
| updated_at | DATETIME | Yes | 수정일시 | 기존 |

**`purchase_date` 처리 옵션 (택1, PM 확정 필요)**
- (B-1) `purchase_date` → `payment_date` 로 **의미 확정**(리네임)하고 `contract_date` 신규 추가.
- (B-2) `purchase_date` **유지**하고 `contract_date` / `payment_date` 둘 다 신규 추가 (기존 데이터 하위호환).

> 권고: **B-1**. `purchase_date` 의 의미가 모호(계약일? 지급일?)하므로 `payment_date` 로 확정하는 편이 명확하다. 단, 이미 저장된 데이터가 있다면 마이그레이션 필요.

### 2.2.2 Policy 테이블 개정 (판정 방식 컬럼 추가)

| Column | Type | Required | Description | 비고 |
|--------|------|----------|-------------|------|
| policy_id | INTEGER | Yes | PK | 기존 |
| policy_code | TEXT | Yes | 정책 코드 (Unique) | 기존 |
| policy_name | TEXT | Yes | 정책명 | 기존 |
| description | TEXT | No | 설명 | 기존 |
| is_active | BOOLEAN | Yes | 사용 여부 | 기존 |
| **evaluation_basis** | TEXT | Yes | **판정 기준일 유형** | **신규** |
| created_at | DATETIME | Yes | 생성일시 | 기존 |
| updated_at | DATETIME | Yes | 수정일시 | 기존 |

**`evaluation_basis` 허용 값(enum) 제안**

| 값 | 의미 | 적용 정책 |
|----|------|-----------|
| `PAYMENT_DATE` | 지급일이 인증 유효기간 내 | 중소·여성·장애인·녹색제품 |
| `CONTRACT_DATE` | 계약일이 인증 유효기간 내 | 창업기업 |
| `VENDOR_EXISTENCE` | 기간 무관, 거래 유무 + 품목·금액 | (향후) 자활용사촌 |

> 이 컬럼을 두면 Calculator 가 **정책 코드를 하드코딩하지 않고 데이터로 규칙을 분기**할 수 있어, 정책이 늘어도 코드 수정 없이 대응된다. (YAGNI 준수: 세 값만 정의)

### 2.2.3 (참고) 계산결과 모델 확장 — 설계문서 아님

`AchievementResult` 는 DB 테이블이 아니라 계산결과 dataclass 이므로 DATABASE_DESIGN 대상은 아니다. 다만 자활용사촌(품목 기준) 지원 시 **`item_count` 필드 추가**가 필요함을 기록해 둔다.

## 2.3 권고 — 단계적 접근

**1단계(접근 A) → 2단계(접근 B)** 순서를 권고한다.

- 먼저 **접근 A** 로 유효기간 판정을 도입하여 가장 큰 결함(문제 1)과 설계 문서 위반을 즉시 해소한다. 스키마 변경이 없어 위험이 낮다.
- 이후 **접근 B** 로 정책별 기준(계약일/품목)을 세분화한다. 스키마·모델·Repository·테스트 변경이 크므로 별도 Issue 로 분리한다.

---

# Part 3. 변경 영향 분석

## 3.1 기존 Model 영향

| Model | 접근 A | 접근 B |
|-------|--------|--------|
| `Purchase` | 영향 없음 | `contract_date`, `payment_date`, `item_count` 필드 추가 (dataclass 변경) |
| `Policy` | 영향 없음 | `evaluation_basis` 필드 추가 |
| `Certification` | 영향 없음 (valid_from/valid_to 이미 존재) | 동일 |
| `Company` | 영향 없음 | 영향 없음 |

## 3.2 기존 Repository 영향

| Repository | 접근 A | 접근 B |
|------------|--------|--------|
| `PurchaseRepository` | 영향 없음 | `CREATE TABLE` / `insert` / `_row_to_purchase` 에 신규 컬럼 반영. 조회 시 날짜 파싱 추가 |
| `PolicyRepository` | 영향 없음 | `CREATE TABLE` / `insert` / `_row_to_policy` 에 `evaluation_basis` 반영 |
| `CertificationRepository` | 영향 없음. 단, **정책+유효기간 조회 편의 메서드**(예: `find_by_policy` 는 이미 존재) 재사용 가능 | 동일 |
| `CompanyRepository` | 영향 없음 | 영향 없음 |

> 접근 A 에서 Calculator 가 유효기간을 판정하려면 `Certification.valid_from/valid_to` 와 `company_id` 가 필요하다. `CertificationRepository.find_by_policy(policy_id)` 가 이미 이 정보를 모두 반환하므로 **Repository 추가 없이** 구현 가능하다.

## 3.3 Calculator 영향

| 항목 | 접근 A | 접근 B |
|------|--------|--------|
| `_sum_policy_purchase` | 유효기간 판정 로직 추가: 구매의 판정기준일이 그 기업의 해당 정책 인증 유효기간에 포함되는지 검사 | 정책의 `evaluation_basis` 에 따라 지급일/계약일/거래유무 분기 |
| 계산 정확성 | 과대계상 해소 | 실무 완전 반영 |
| `AchievementResult` | 변경 없음 | 자활용사촌 지원 시 `item_count` 추가 |

**접근 A 판정 로직(의사코드)**
```text
정책 P 의 구매 인정 조건:
  구매 X 의 company_id 가 C 이고,
  C 가 P 에 대해 보유한 인증 중 하나라도
  valid_from <= X.purchase_date <= valid_to  이면 인정
```
- 한 기업이 같은 정책 인증을 복수 보유할 수 있으므로 **어느 하나라도 포함되면 인정**.
- 경계 포함 여부(`<=` inclusive) 확정 필요 → 아래 미결 질문 참조.

## 3.4 기존 테스트 영향

| 테스트 파일 | 접근 A | 접근 B |
|-------------|--------|--------|
| `test_procurement_achievement.py` | 다수 케이스가 "유효기간 무시" 전제로 작성됨 → **유효기간 내 날짜로 데이터 보정 필요**. 기간 밖 제외 케이스 신규 추가 | 추가 보정 + 신규 컬럼 반영 |
| `test_purchase_repository.py` | 영향 없음 | `purchase_date` 관련 케이스 및 컬럼 일치 검증(`test_columns_match_design`) 수정 |
| `test_policy_repository.py` | 영향 없음 | 컬럼 일치 검증(`test_columns_match_design`) 수정 |
| 그 외 | 영향 없음 | 영향 없음 |

> 현재 계산 테스트들은 인증 `valid_from=2026-01-01 / valid_to=2026-12-31`, 구매 `purchase_date=2026-03-15` 로 이미 유효기간 내에 있어, 접근 A 도입 시 **대부분 그대로 통과**할 가능성이 높다. 다만 "유효기간을 실제로 검사한다"는 것을 보장하는 **경계·기간밖 케이스는 신규로 추가**해야 한다.

## 3.5 REQUIREMENTS.md / 문서 범위 영향

- **자활용사촌** 은 `POLICY_DEFINITION.md`(35~43행)·`REQUIREMENTS.md`(9. 향후 확장) 모두에서 **MVP 밖**이다. MVP 에 포함하려면 두 문서의 범위 정의를 **먼저 개정**해야 한다.
- **녹색제품 "제품 기준"** 은 현재 데이터 모델에 품목(Item)/제품 개념이 없어, 별도 설계(품목 테이블)가 필요하다. 이는 접근 B 보다 더 큰 범위이므로 **별도 트랙**으로 분리 권고.

---

# Part 4. 다음 구현 Issue 제안서

우선순위와 위험도를 고려한 분리안이다. (번호는 제안이며 실제 부여는 PM 재량)

| 제안 Issue | 제목 | 범위 | 선행 | 위험도 |
|-----------|------|------|------|--------|
| **#12** | Calculator 유효기간 판정 적용 (접근 A) | `purchase_date` 기준으로 인증 유효기간 내 구매만 집계하도록 Calculator 수정. 스키마·모델 변경 없음 | #10 | 낮음 |
| **#13** | DATABASE_DESIGN 개정 — Purchase 날짜/품목 컬럼 | `contract_date`, `payment_date`, `item_count` 추가(문서 확정 + Model + Repository + 테스트) | #12 | 중간 |
| **#14** | DATABASE_DESIGN 개정 — Policy 판정기준 컬럼 | `evaluation_basis` 추가(문서 + Model + Repository + 시드 매핑) | #13 | 중간 |
| **#15** | Calculator 정책별 기준 분기 (접근 B) | `evaluation_basis` 에 따라 지급일/계약일 분기. 창업기업 계약일 판정 반영 | #13, #14 | 중간 |
| **#16** | (범위 확정 후) 자활용사촌 유형 지원 | REQUIREMENTS/POLICY_DEFINITION 개정 → 거래유무+품목수 집계, `AchievementResult.item_count` | #14, #15 + 범위 개정 | 높음 |
| **#17** | (별도 트랙) 녹색제품 제품 기준 지원 | 품목(Item) 데이터 모델 설계 필요 | 별도 설계 | 높음 |

**권고 실행 순서**: #12(즉시 가치·저위험) → #13 → #14 → #15 → (범위 개정) #16 → #17

---

# 미결 질문 (PM 확정 요청)

| # | 질문 | 기본 권고 |
|---|------|-----------|
| Q1 | 판정 기준일 정본: POLICY_DEFINITION의 "구매일 단일 기준" vs PM의 "일반=지급일/창업=계약일" | PM 실무 규칙 채택 → 접근 B (단, #12는 우선 purchase_date로 유효기간만 도입) |
| Q2 | `purchase_date` 처리: `payment_date`로 리네임(B-1) vs 3개 병존(B-2) | B-1 (의미 명확화) |
| Q3 | `evaluation_basis` 를 Policy 컬럼으로 둘지 vs 코드 상수 매핑 | Policy 컬럼 (데이터 기반 분기) |
| Q4 | 자활용사촌 MVP 포함 여부 | 향후 확장 유지 (문서상 MVP 밖) |
| Q5 | 유효기간 경계 포함 여부: `valid_from <= date <= valid_to` inclusive? | inclusive 권고 (경계일 인정) |
| Q6 | 녹색제품 판정: "기업 기준"으로 단순화 vs "제품 기준" 구현 | MVP는 기업 기준, 제품 기준은 #17 별도 트랙 |
| Q7 | 목표율(target_rate): 현재 입력값 방식 유지 vs DB 관리 | 당분간 입력값 유지 (Out of Scope) |

---

# 요약

- **가장 시급한 결함**: 인증 유효기간 미적용 → `POLICY_DEFINITION.md` 공통 원칙 위반. **접근 A(#12)** 로 스키마 변경 없이 즉시 해소 가능.
- **실무 완전 반영**: 정책별 판정 기준 차이(지급일/계약일/품목) 는 **스키마 개정(접근 B)** 필요 → #13~#15 로 분리.
- **범위 밖 항목**: 자활용사촌·녹색제품 제품기준 은 문서 범위 개정 또는 별도 설계 선행.
- 본 Issue(#11) 에서는 **어떤 코드·DB·설계문서도 변경하지 않았다.** PM 승인 후 위 Issue 순서로 구현한다.
