import bcrypt

MIN_PASSWORD_BYTES = 8
MAX_PASSWORD_BYTES = 72  # bcrypt ignores anything past 72 bytes

# Verified against for unknown emails so both login failures take the same time.
_DUMMY_HASH = bcrypt.hashpw(b"timing-equaliser", bcrypt.gensalt()).decode()


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    secret = password.encode()
    if len(secret) > MAX_PASSWORD_BYTES:
        # Never valid (signup refuses it); still spend the same time hashing.
        bcrypt.checkpw(secret[:MAX_PASSWORD_BYTES], _DUMMY_HASH.encode())
        return False
    ok = bcrypt.checkpw(secret, (password_hash or _DUMMY_HASH).encode())
    return ok and password_hash is not None
