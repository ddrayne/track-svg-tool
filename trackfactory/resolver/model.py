from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Provenance(BaseModel):
    source_type: Literal["wikimedia_svg", "osm", "pdf", "poster", "other"]
    url: str
    license: str | None = None
    attribution: str | None = None
    retrieved_utc: str
    sha256: str | None = None
    notes: str | None = None


class Candidate(BaseModel):
    source_type: Literal["wikimedia_svg", "osm", "pdf", "poster"]
    title: str
    url: str
    score: float
    payload: dict[str, Any] = Field(default_factory=dict)


class TrackCanonical(BaseModel):
    track_id: str
    name: str
    config: str = "default"
    direction: Literal["CW", "CCW", "BOTH"] = "BOTH"
    centerline: list[list[float]]
    left_boundary: list[list[float]] | None = None
    right_boundary: list[list[float]] | None = None
    pitlane: list[list[list[float]]] | None = None
    start_finish: dict[str, float] | None = None
    length_m: float | None = None
    qa: dict[str, Any] | None = None
    provenance: list[Provenance] = Field(default_factory=list)
