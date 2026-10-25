# generate_hash.py — 대시보드 로그인 비밀번호 해시 생성 (Astro_Manager와 같은 방식)
import sys

from werkzeug.security import generate_password_hash

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("사용법: python generate_hash.py <원하는_비밀번호>")
        sys.exit(1)
    hashed = generate_password_hash(sys.argv[1])
    print(".env 파일에 아래 줄을 넣으세요 (docker compose가 $를 변수로 해석하지 않도록 $$로 바꿨습니다):")
    print(f"ADMIN_PASSWORD_HASH={hashed.replace('$', '$$')}")
