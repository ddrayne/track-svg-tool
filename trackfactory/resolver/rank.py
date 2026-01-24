from __future__ import annotations

from .model import Candidate


def pick_best(candidates: list[Candidate]) -> Candidate | None:
    if not candidates:
        return None
    return max(candidates, key=lambda c: c.score)
