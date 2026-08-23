"""Offline ranking metrics.

Pure functions over labels and scores -- no database, no model -- so they are
directly unit-testable and usable from both `training.py` (promotion gate) and
`scripts/eval_ml.py` (the prior-vs-learned report).

AUC answers "does the model order a random positive above a random negative",
which is the right question for the promotion gate because it is threshold-free
and stable on small samples. Recall@K / NDCG@K / MAP@K answer "is the *top of
the list* good", which is what a user actually sees. They disagree often enough
that reporting only one of them hides real regressions.
"""
import math
from typing import Dict, Iterable, List, Sequence, Tuple


def roc_auc(labels: Sequence[float], scores: Sequence[float]) -> float:
    """Area under the ROC curve, via the rank-sum (Mann-Whitney U) identity.

    Ties get averaged ranks, which is what keeps a model that outputs one
    constant for every input at exactly 0.5 instead of an accidental 1.0.
    Returns 0.5 -- deliberately uninformative -- when one class is absent, since
    AUC is undefined there and returning 0.0 or 1.0 would corrupt the gate.
    """
    if len(labels) != len(scores):
        raise ValueError(f"labels/scores length mismatch: {len(labels)} vs {len(scores)}")
    pos = sum(1 for y in labels if y > 0.5)
    neg = len(labels) - pos
    if pos == 0 or neg == 0:
        return 0.5

    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        average_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = average_rank
        i = j + 1

    rank_sum = sum(ranks[i] for i, y in enumerate(labels) if y > 0.5)
    return (rank_sum - pos * (pos + 1) / 2.0) / (pos * neg)


def log_loss(labels: Sequence[float], probs: Sequence[float], eps: float = 1e-12) -> float:
    """Mean binary cross-entropy. Reported alongside AUC because AUC is blind to
    calibration -- a model can rank perfectly and still output 0.99 for everything."""
    if not labels:
        return 0.0
    total = 0.0
    for y, p in zip(labels, probs):
        p = min(max(p, eps), 1.0 - eps)
        total += -(y * math.log(p) + (1.0 - y) * math.log(1.0 - p))
    return total / len(labels)


def _ranked_labels(group: Sequence[Tuple[float, float]]) -> List[float]:
    """Labels reordered by descending score; ties broken deterministically."""
    return [label for _, label in sorted(group, key=lambda p: (-p[0], p[1]))]


def recall_at_k(groups: Iterable[Sequence[Tuple[float, float]]], k: int = 10) -> float:
    """Mean fraction of a user's relevant items that appear in their top K.

    `groups` is one sequence of (score, label) per user. Grouping matters: a
    single pooled list would let a heavy user's many interactions dominate the
    metric entirely.
    """
    totals, n = 0.0, 0
    for group in groups:
        labels = _ranked_labels(group)
        relevant = sum(1 for y in labels if y > 0.5)
        if relevant == 0:
            continue
        hits = sum(1 for y in labels[:k] if y > 0.5)
        totals += hits / relevant
        n += 1
    return totals / n if n else 0.0


def ndcg_at_k(groups: Iterable[Sequence[Tuple[float, float]]], k: int = 10) -> float:
    """Normalised discounted cumulative gain -- position-aware, unlike recall."""
    totals, n = 0.0, 0
    for group in groups:
        labels = _ranked_labels(group)
        relevant = sum(1 for y in labels if y > 0.5)
        if relevant == 0:
            continue
        dcg = sum(
            (1.0 / math.log2(i + 2)) for i, y in enumerate(labels[:k]) if y > 0.5
        )
        ideal = sum(1.0 / math.log2(i + 2) for i in range(min(relevant, k)))
        if ideal > 0:
            totals += dcg / ideal
            n += 1
    return totals / n if n else 0.0


def map_at_k(groups: Iterable[Sequence[Tuple[float, float]]], k: int = 10) -> float:
    """Mean average precision at K."""
    totals, n = 0.0, 0
    for group in groups:
        labels = _ranked_labels(group)
        relevant = sum(1 for y in labels if y > 0.5)
        if relevant == 0:
            continue
        hits, precision_sum = 0, 0.0
        for i, y in enumerate(labels[:k]):
            if y > 0.5:
                hits += 1
                precision_sum += hits / (i + 1)
        totals += precision_sum / min(relevant, k)
        n += 1
    return totals / n if n else 0.0


def evaluate(
    labels: Sequence[float],
    probs: Sequence[float],
    group_ids: Sequence[str],
    k: int = 10,
) -> Dict[str, float]:
    """The full metric set for one (labels, probs) pair grouped by user."""
    grouped: Dict[str, List[Tuple[float, float]]] = {}
    for gid, prob, label in zip(group_ids, probs, labels):
        grouped.setdefault(gid, []).append((prob, label))
    groups = list(grouped.values())

    return {
        "auc": roc_auc(labels, probs),
        "log_loss": log_loss(labels, probs),
        f"recall@{k}": recall_at_k(groups, k),
        f"ndcg@{k}": ndcg_at_k(groups, k),
        f"map@{k}": map_at_k(groups, k),
        "n_samples": len(labels),
        "n_groups": len(groups),
        "positive_rate": (sum(1 for y in labels if y > 0.5) / len(labels)) if labels else 0.0,
    }
