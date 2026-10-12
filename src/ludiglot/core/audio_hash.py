from __future__ import annotations


def fnv1a_32(text: str) -> int:
    """FNV-1a 32-bit hash for ASCII/UTF-8 bytes."""
    data = text.encode("utf-8")
    h = 0x811C9DC5
    for b in data:
        h ^= b
        h = (h * 0x01000193) & 0xFFFFFFFF
    return h
