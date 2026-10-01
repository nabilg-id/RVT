"""Checkpoint: crash-safe resume via JSON status file."""
import json
from pathlib import Path

CHECKPOINT_FILE = ".rch-checkpoint.json"


def _cp_path(output_dir):
    return Path(output_dir) / CHECKPOINT_FILE


def load_checkpoint(output_dir):
    p = _cp_path(output_dir)
    try:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


def save_checkpoint(output_dir, data):
    p = _cp_path(output_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(p)


def mark_completed(output_dir, video_id):
    cp = load_checkpoint(output_dir) or {"channel": None, "total": 0, "completed": [], "failed": []}
    if video_id not in cp["completed"]:
        cp["completed"].append(video_id)
    save_checkpoint(output_dir, cp)


def mark_failed(output_dir, video_id):
    cp = load_checkpoint(output_dir) or {"channel": None, "total": 0, "completed": [], "failed": []}
    if video_id not in cp["failed"]:
        cp["failed"].append(video_id)
    save_checkpoint(output_dir, cp)


def clear_checkpoint(output_dir):
    p = _cp_path(output_dir)
    try:
        if p.exists():
            p.unlink()
    except Exception:
        pass
