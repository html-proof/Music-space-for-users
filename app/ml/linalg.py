"""Vector primitives with a numpy backend and a pure-Python fallback.

numpy is a hard requirement in requirements.txt, but the fallback exists so
that a slim deploy which somehow lacks it degrades to slower scoring instead of
failing every request with an ImportError at import time. Only the *serving*
path is covered: training imports numpy directly and is expected to fail loudly
without it, because a second untested optimiser would be a liability rather
than a safety net.

Vectors are plain lists of float on the fallback path and 1-D float32 ndarrays
under numpy. Callers must treat them as opaque and only combine them through
the functions here.
"""
import math
from typing import Iterable, List, Sequence

try:  # pragma: no cover - exercised by whichever backend is installed
    import numpy as _np
    HAS_NUMPY = True
except ImportError:  # pragma: no cover
    _np = None
    HAS_NUMPY = False


def zeros(dim: int):
    if HAS_NUMPY:
        return _np.zeros(dim, dtype=_np.float32)
    return [0.0] * dim


def from_pairs(dim: int, pairs: Iterable[tuple]):
    """Build a dense vector from (index, value) pairs, accumulating collisions.

    Hash collisions are additive rather than last-write-wins; that is what makes
    the hashing trick behave like a sparse feature map.
    """
    vec = zeros(dim)
    for idx, value in pairs:
        vec[idx % dim] += value
    return vec


def add_scaled(target, other, scale: float):
    """target += other * scale, in place where the backend allows it."""
    if HAS_NUMPY:
        target += other * _np.float32(scale)
        return target
    for i, value in enumerate(other):
        target[i] += value * scale
    return target


def l2_norm(vec) -> float:
    if HAS_NUMPY:
        return float(_np.linalg.norm(vec))
    return math.sqrt(sum(v * v for v in vec))


def normalize(vec):
    """Return a unit-length copy. A zero vector is returned unchanged.

    Normalising to unit length is what lets a dot product be read directly as a
    cosine, which every similarity call downstream relies on.
    """
    norm = l2_norm(vec)
    if norm <= 1e-12:
        return vec
    if HAS_NUMPY:
        return (vec / _np.float32(norm)).astype(_np.float32)
    return [v / norm for v in vec]


def dot(a, b) -> float:
    if HAS_NUMPY:
        return float(_np.dot(a, b))
    return float(sum(x * y for x, y in zip(a, b)))


def cosine(a, b) -> float:
    """Cosine similarity, safe on zero vectors (returns 0.0)."""
    na, nb = l2_norm(a), l2_norm(b)
    if na <= 1e-12 or nb <= 1e-12:
        return 0.0
    return dot(a, b) / (na * nb)


def sigmoid(x: float) -> float:
    """Logistic function, overflow-safe for large-magnitude scores."""
    if x >= 0.0:
        return 1.0 / (1.0 + math.exp(-min(x, 60.0)))
    e = math.exp(max(x, -60.0))
    return e / (1.0 + e)


def score_linear(features: Sequence[float], weights: Sequence[float], bias: float) -> float:
    """w·x + b. Length mismatch is a programming error, not a data condition."""
    if len(features) != len(weights):
        raise ValueError(
            f"feature/weight length mismatch: {len(features)} vs {len(weights)}. "
            "A stored model was probably fitted on a different FEATURE_NAMES."
        )
    if HAS_NUMPY:
        return float(_np.dot(_np.asarray(features, dtype=_np.float64), weights)) + bias
    return sum(f * w for f, w in zip(features, weights)) + bias


def to_list(vec) -> List[float]:
    """Plain float list, for JSON persistence."""
    if HAS_NUMPY and isinstance(vec, _np.ndarray):
        return [float(v) for v in vec.tolist()]
    return [float(v) for v in vec]
