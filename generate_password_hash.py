import argparse
import hashlib
import secrets


def pbkdf2_hash_password(password: str, salt: str | None = None, iterations: int = 200000) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${dk.hex()}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate PBKDF2 password hashes for the Streamlit steam coil app.")
    parser.add_argument("password", help="Plain-text password to hash")
    parser.add_argument("--iterations", type=int, default=200000, help="PBKDF2 iterations")
    args = parser.parse_args()
    print(pbkdf2_hash_password(args.password, iterations=args.iterations))


if __name__ == "__main__":
    main()
