"""Which VODs are already done. Lives in state/processed.json and is committed back by the workflow."""
import json
import os
import time

from .util import ROOT

PATH = os.path.join(ROOT, "state", "processed.json")


def load():
    try:
        with open(PATH, encoding="utf-8") as f:
            st = json.load(f)
    except (OSError, ValueError):
        st = {}
    st.setdefault("vods", {})
    return st


def save(st):
    os.makedirs(os.path.dirname(PATH), exist_ok=True)
    tmp = PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    os.replace(tmp, PATH)


def mark(st, vod_id, status, **extra):
    rec = st["vods"].setdefault(str(vod_id), {})
    rec.update(extra)
    rec["status"] = status
    rec["at"] = time.strftime("%Y-%m-%d %H:%M", time.gmtime())
    if status == "error":
        rec["attempts"] = rec.get("attempts", 0) + 1
        if rec["attempts"] >= 3:
            rec["status"] = "failed"
    return rec


def is_finished(st, vod_id):
    return st["vods"].get(str(vod_id), {}).get("status") in ("done", "failed", "skipped")
