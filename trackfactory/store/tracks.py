import json
import re
import unicodedata
from pathlib import Path


def slugify(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-{2,}", "-", value)
    return value.strip("-")


def tracks_root() -> Path:
    return Path(__file__).resolve().parents[2] / "tracks"


def track_config_dir(track_id: str, config: str) -> Path:
    return tracks_root() / track_id / config


def write_canonical(track_id: str, config: str, canonical: dict) -> Path:
    out_dir = track_config_dir(track_id, config)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "canonical.json"
    out_path.write_text(json.dumps(canonical, indent=2), encoding="utf-8")
    return out_path


def write_svg(track_id: str, config: str, name: str, svg_text: str) -> Path:
    out_dir = track_config_dir(track_id, config)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / name
    out_path.write_text(svg_text, encoding="utf-8")
    return out_path


def write_sources(track_id: str, sources: dict) -> Path:
    out_dir = tracks_root() / track_id
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "sources.json"
    out_path.write_text(json.dumps(sources, indent=2), encoding="utf-8")
    return out_path
