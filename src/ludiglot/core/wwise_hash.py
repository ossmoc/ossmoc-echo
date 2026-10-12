from __future__ import annotations


class WwiseHash:
    """Wwise 32-bit FNV-1a hash (lowercase)."""

    FNV_PRIME_32 = 16777619
    FNV_OFFSET_32 = 2166136261

    def hash_int(self, text: str) -> int:
        data = text.lower().encode("utf-8")
        h = self.FNV_OFFSET_32
        for b in data:
            h ^= b
            h = (h * self.FNV_PRIME_32) & 0xFFFFFFFF
        return h

    def hash_str(self, text: str) -> str:
        return str(self.hash_int(text))
