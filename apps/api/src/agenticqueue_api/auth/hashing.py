"""Argon2id hashing helpers for local auth and agent token secrets."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError, VerificationError
from argon2.low_level import Type

ARGON2_MEMORY_COST_KIB = 19456
ARGON2_TIME_COST = 2
ARGON2_PARALLELISM = 1

_HASHER = PasswordHasher(
    time_cost=ARGON2_TIME_COST,
    memory_cost=ARGON2_MEMORY_COST_KIB,
    parallelism=ARGON2_PARALLELISM,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)


def hash_passcode(passcode: str) -> str:
    """Hash one human passcode using pinned OWASP 2024 Argon2id parameters."""

    return _HASHER.hash(passcode)


def verify_passcode(passcode: str, passcode_hash: str) -> bool:
    """Return whether a passcode matches an encoded Argon2id hash."""

    try:
        return _HASHER.verify(passcode_hash, passcode)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def hash_token_secret(raw_secret: str) -> str:
    """Hash one agent token secret using the same Argon2id policy."""

    return hash_passcode(raw_secret)


def verify_token_secret(raw_secret: str, token_hash: str) -> bool:
    """Return whether an agent token secret matches its encoded hash."""

    return verify_passcode(raw_secret, token_hash)
