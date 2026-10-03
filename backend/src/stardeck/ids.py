"""Record ids: a short prefix plus 64 random bits, unique across every workspace."""

import secrets


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(8)}"
