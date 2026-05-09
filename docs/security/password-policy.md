# 비밀번호 정책

본 파일은 SKKU InSight의 비밀번호 정책을 정의한다. NIST SP 800-63B (2017) 가이드라인을 기반으로 한다. 관련 NFR: NFR-16. 인증 흐름은 [`auth-flow.md`](auth-flow.md).

## 결정 (NIST 기반)

| 항목 | 결정 |
|---|---|
| 최소 길이 | **12자** |
| 최대 길이 | 128자 |
| 복잡도 강제 | **하지 않음** (특수문자 등 강제 X) — NIST 권장 |
| 흔한 비밀번호 차단 | **함** (HIBP 또는 내장 deny-list) |
| 사용자별 정보 차단 | 이메일 local part / 사용자 클래스 단어 차단 |
| 최대 시도 후 잠금 | 미사용 (rate limit으로 대체) |
| 정기 변경 강제 | **하지 않음** — NIST 권장 |
| 변경 이력 검사 | 1차 시연: 미사용. 운영 시 마지막 5개 해시 비교 가능 |
| 저장 방식 | **bcrypt cost=12** (NFR-16) |

## NIST가 권장하지 않는 것 (의도적 회피)

- 분기마다 강제 변경 — 사용자 보안 행동을 악화시킴
- 특수문자 조합 강제 — 흔한 패턴(`Pass1!`)으로 우회
- 보안 질문 — 추측 가능

## 길이 기준선

12자는 다음 절충:

- 8자는 GPU brute force에 약함
- 16자 강제는 사용자 부담 큼
- 12자 + 흔한 비밀번호 차단으로 사실상 64+ 비트 entropy 확보

## 흔한 비밀번호 차단

1차: 내장 deny-list 5,000개 (`backend/app/security/common_passwords.txt` — pwned-passwords-top-5000 또는 SecLists 발췌)

2차 (선택): HIBP API의 k-anonymity (`api.pwnedpasswords.com`) — 첫 5자 sha1 해시만 보내서 prefix 매치 결과 비교. 1차 시연에서는 미사용 (외부 의존 + privacy 추가 고려). 운영 시 추가.

## 사용자별 정보 차단

```python
def password_contains_user_info(password: str, email: str | None) -> bool:
    if email:
        local = email.split("@")[0].lower()
        if len(local) >= 4 and local in password.lower():
            return True
    forbidden_terms = {"insight", "skku", "admin", "password", "qwerty"}
    pl = password.lower()
    if any(t in pl for t in forbidden_terms):
        return True
    return False
```

## 검증 의사 코드

```python
def enforce_password_policy(password: str, *, email: str | None = None) -> None:
    if len(password) < 12:
        raise PolicyViolation("auth.weak_password.too_short", "비밀번호는 12자 이상이어야 합니다.")
    if len(password) > 128:
        raise PolicyViolation("auth.weak_password.too_long", "비밀번호가 너무 깁니다.")
    if password in COMMON_PASSWORDS:
        raise PolicyViolation("auth.weak_password.common", "흔한 비밀번호입니다. 다른 비밀번호를 사용해주세요.")
    if password_contains_user_info(password, email):
        raise PolicyViolation("auth.weak_password.contains_user_info", "비밀번호에 사용자 정보를 포함할 수 없습니다.")
    if password.strip() != password:
        raise PolicyViolation("auth.weak_password.whitespace", "양 끝 공백은 허용되지 않습니다.")
    # 복잡도 (대문자 + 숫자 + 특수문자 등) 강제하지 않음 (NIST)
```

## 사용자에게 주는 가이드 문구 (한국어)

UI-01 가입 / UI-05 변경 화면:

> **비밀번호 안내**
> - 12자 이상 사용해 주세요.
> - 다른 사이트에서 쓰는 비밀번호와 다르게 설정해 주세요.
> - 이메일 또는 흔한 단어를 포함할 수 없습니다.
> - 특수문자를 꼭 넣지 않아도 됩니다. 길고 외우기 쉬운 문장이 더 안전합니다.

## 저장

- `passlib[bcrypt]` 사용
- cost (log_rounds) = 12
- 검증: `bcrypt.verify(plain, hashed)`
- 향후 `argon2` 마이그레이션 시 dual-hash 마이그레이션 룰 (사용자 다음 로그인 시 자동 재해시)

```python
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], default="bcrypt", bcrypt__rounds=settings.bcrypt_cost)

def hash_password(p: str) -> str: return pwd_context.hash(p)
def verify_password(p: str, h: str) -> bool: return pwd_context.verify(p, h)
def needs_rehash(h: str) -> bool: return pwd_context.needs_update(h)
```

## 변경 시

- `POST /auth/change-password` (옵션 — 1차 시연에서는 admin만 강제 변경 — `admin-bootstrap.md`).
- 사용자가 변경 시: `current_password` 검증 → `new_password` 정책 검증 → 해시 갱신 → 모든 refresh 폐기 (보안상 모든 디바이스 재로그인).

## 비밀번호 분실 / 재설정

1차 시연에서는 미구현 (이메일 발송 인프라 부담). 운영 단계에 추가:

- 이메일 토큰 + 1시간 만료 + 1회 사용
- 재설정 후 모든 refresh 폐기
- 재설정 페이지는 rate limit 엄격 (1/시간/이메일)

<!-- TODO: 시연용으로 admin이 사용자 비밀번호 강제 reset할 수 있도록 임시 admin endpoint 검토 (운영 단계에서는 제거) -->
