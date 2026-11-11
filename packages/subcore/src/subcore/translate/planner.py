"""Planner: decide how many parts a unit is translated in (FR-2.2, ARCHITECTURE §3.3).

Every part still receives the whole source as context; parts only limit how
many cues the model must *output* in one call.
"""

from __future__ import annotations

import math

from .. import srt

__all__ = ["estimate_tokens", "plan_parts", "DEFAULT_OUTPUT_FACTOR", "OUTPUT_BUDGET", "MIN_PART_CUES"]

DEFAULT_OUTPUT_FACTOR = 1.3  # vi output is ~1.3x the source token count
OUTPUT_BUDGET = 0.8  # use at most 80% of max_output_tokens
MIN_PART_CUES = 100
JSON_OVERHEAD_TOKENS = 12  # {"i":123,"text":""} wrapper per cue


def estimate_tokens(text: str) -> int:
    """Cheap offline token estimate (no API call; quota is scarce)."""
    return math.ceil(len(text) / 3.2)


def _cost(cue: srt.Cue, factor: float) -> int:
    return math.ceil(estimate_tokens(cue.text) * factor) + JSON_OVERHEAD_TOKENS


def plan_parts(
    cues: list[srt.Cue],
    ordinals: list[int],
    max_output_tokens: int,
    *,
    output_factor: float = DEFAULT_OUTPUT_FACTOR,
    min_part_cues: int = MIN_PART_CUES,
    force_parts: int | None = None,
) -> list[list[int]]:
    """Split ``ordinals`` (1-based cue ordinals to translate) into ordered parts.

    One part when the estimated output fits ``OUTPUT_BUDGET`` of the model limit;
    otherwise parts are cut at the largest silences between cues, each with at least
    ``min_part_cues`` targets. ``force_parts`` overrides the count (used when splitting
    after repeated failures).
    """
    if not ordinals:
        return []
    costs = [_cost(cues[o - 1], output_factor) for o in ordinals]
    total = sum(costs)
    budget = max(1, int(max_output_tokens * OUTPUT_BUDGET))
    if force_parts:
        # Splitting after failures may go below the usual minimum part size.
        n, min_size = max(1, min(force_parts, len(ordinals))), 1
    else:
        wanted = max(1, math.ceil(total / budget))
        n, min_size = max(1, min(wanted, len(ordinals) // max(1, min_part_cues))), min_part_cues
    if n == 1:
        return [list(ordinals)]

    cuts: list[int] = []  # positions into `ordinals`: part k = ordinals[cuts[k-1]:cuts[k]]
    prefix = [0]
    for c in costs:
        prefix.append(prefix[-1] + c)
    prev = 0
    for b in range(1, n):
        remaining_parts = n - b
        target_cost = total * b / n
        ideal = next((p for p in range(prev + 1, len(ordinals)) if prefix[p] >= target_cost), len(ordinals) - 1)
        lo = max(prev + min_size, ideal - max(3, len(ordinals) // (n * 10)))
        hi = min(len(ordinals) - min_size * remaining_parts, ideal + max(3, len(ordinals) // (n * 10)))
        if lo > hi:
            lo = hi = max(prev + 1, min(ideal, len(ordinals) - remaining_parts))
        best, best_key = lo, None
        for p in range(lo, hi + 1):
            before, after = cues[ordinals[p - 1] - 1], cues[ordinals[p] - 1]
            gap = after.start_ms - before.end_ms
            key = (gap, -abs(p - ideal))
            if best_key is None or key > best_key:
                best, best_key = p, key
        cuts.append(best)
        prev = best
    bounds = [0, *cuts, len(ordinals)]
    return [ordinals[a:b] for a, b in zip(bounds, bounds[1:]) if b > a]
