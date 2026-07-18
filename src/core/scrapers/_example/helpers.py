"""Optional import-light descriptor helpers for the copyable example."""


def decode_sku(raw: object) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("must be a nonblank string")
    return raw.strip()
