from __future__ import annotations

import hashlib
import json
import random
import string
from typing import Any

import numpy as np


def canonical_json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_bytes(*parts: Any, namespace: str = "dssp-mock") -> bytes:
    digest = hashlib.sha256()
    digest.update(namespace.encode("utf-8"))
    for part in parts:
        data = canonical_json(part).encode("utf-8")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.digest()


def stable_hex(*parts: Any, namespace: str = "dssp-mock") -> str:
    return digest_bytes(*parts, namespace=namespace).hex()


def stable_random(*parts: Any, namespace: str = "dssp-mock") -> random.Random:
    seed = int.from_bytes(digest_bytes(*parts, namespace=namespace)[:16], "big")
    return random.Random(seed)


def stable_rng(*parts: Any, namespace: str = "dssp-mock") -> np.random.Generator:
    seed = int.from_bytes(digest_bytes(*parts, namespace=namespace)[:16], "big")
    return np.random.default_rng(seed)


def stable_word(*parts: Any, namespace: str, min_length: int = 2, max_length: int = 6) -> str:
    rng = stable_random(*parts, namespace=namespace)
    length = rng.randint(min_length, max_length)
    return "".join(rng.choice(string.ascii_lowercase) for _ in range(length))
