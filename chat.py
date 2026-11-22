"""Chat replay (TwitchDownloader JSON) -> activity per time step + "клип" markers."""
import json
import re

import numpy as np

from .audio import HOP

LAUGH = re.compile(
    r"(kekw|lulw?|omegalul|icant|lmao|lmfao|\blol\b|\bxd+\b|аха|хах|ахх|хех|апх|пхп|"
    r"\bору\b|ржу|ржак|\bкек|\){2,}|😂|🤣|💀)", re.I)
HYPE = re.compile(
    r"(pog|wtf|\bомг\b|\bomg\b|жесть|имба|пизд|бля|ебат|ебать|охуе|нихуя|сука|\bого\b|"
    r"\bвау\b|\bwow\b|!!|\?\?|monkas|клип|clip|🔥|😱)", re.I)
MARK = re.compile(r"^\s*!?\s*(клип|clip)", re.I)
BOTS = {"nightbot", "streamelements", "moobot", "fossabot", "wizebot", "streamlabs",
        "sery_bot", "botrixoficial", "soundalerts", "pokemoncommunitygame"}


def load(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    out = []
    for c in data.get("comments") or []:
        t = c.get("content_offset_seconds")
        body = ((c.get("message") or {}).get("body") or "").strip()
        who = c.get("commenter") or {}
        user = (who.get("name") or who.get("display_name") or "").lower()
        if t is None or not body or user in BOTS:
            continue
        out.append((float(t), user, body))
    out.sort()
    return out


def features(comments, n_hops, delay):
    msgs = np.zeros(n_hops, np.float32)
    laugh = np.zeros(n_hops, np.float32)
    hype = np.zeros(n_hops, np.float32)
    marks = []
    lines = []
    for t, user, body in comments:
        moment = t - delay
        i = int(moment / HOP)
        if 0 <= i < n_hops:
            msgs[i] += 1
            laugh[i] += 1 if LAUGH.search(body) else 0
            hype[i] += 1 if HYPE.search(body) else 0
        if MARK.match(body):
            marks.append(max(0.0, moment))
        lines.append((moment, user, body))
    return {"msgs": msgs, "laugh": laugh, "hype": hype, "marks": marks,
            "total": len(comments), "lines": lines}


def lines_between(chatf, start, end, limit=12):
    if not chatf:
        return []
    return [f"{u}: {b}" for t, u, b in chatf["lines"] if start <= t <= end][:limit]
