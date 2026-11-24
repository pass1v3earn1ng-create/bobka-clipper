"""Optional: Claude picks the best moments and translates subtitles.
Works only when the ANTHROPIC_API_KEY secret is set; otherwise everything runs on heuristics."""
import json
import re

from .util import log

API = "https://api.anthropic.com/v1/messages"

RANK_SYS = """You pick clips from a live stream (the game and language vary; the streamer may swear and joke darkly).
For each candidate you get what was said in a ~35 s window (times in seconds from the window start), chat lines and loudness.
Judge how well it works as a standalone TikTok/Shorts clip: something funny, a fail, rage, a wtf moment, hype, a sharp line.
Boring talk, menus, silence and game cutscene dialogue score low. The clip must make sense from its very first second.
Choose where to cut inside the window: start right where the situation begins, end right after the punchline or reaction.
Reply with JSON only: [{"id": <id>, "score": <0-10>, "start": <sec>, "end": <sec>, "why": "<3-8 words in Russian, lowercase>"}]"""

TR_SYS = """Translate Russian stream subtitle lines into short casual English, the way a 20-something gamer talks.
Keep swearing and slang, lowercase, add nothing, no quotes. Same number of lines, same order.
Reply with JSON only: ["...", "..."]"""


def _call(cfg, system, user, max_tokens):
    import requests
    r = requests.post(API, timeout=180, headers={
        "x-api-key": cfg.anthropic_key, "anthropic-version": "2023-06-01",
        "content-type": "application/json"}, json={
        "model": cfg.llm_model, "max_tokens": max_tokens, "system": system,
        "messages": [{"role": "user", "content": user}]})
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
    return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")


def _json(text):
    m = re.search(r"[\[{].*[\]}]", text or "", re.S)
    return json.loads(m.group(0)) if m else None


def rank(cfg, cands, min_len, max_len):
    if not cfg.anthropic_key or not cands:
        return None
    out = {}
    for i in range(0, len(cands), 40):
        part = _rank_batch(cfg, cands[i:i + 40], min_len, max_len)
        if part is None and not out:
            return None
        out.update(part or {})
    log(f"Claude оценил {len(out)} моментов")
    return out or None


def _rank_batch(cfg, cands, min_len, max_len):
    blocks = []
    for c in cands:
        w0, w1 = c["win"]
        speech = "\n".join(f"[{max(0, s['start'] - w0):.1f}-{min(w1, s['end']) - w0:.1f}] {s['text']}"
                           for s in c["segs"]) or "(no speech)"
        chat = "\n".join(c.get("chat_lines") or []) or "(none)"
        blocks.append(f"id: {c['id']}\nwindow: 0-{w1 - w0:.0f} s, reaction peak at {c['t'] - w0:.1f} s, "
                      f"loudness +{c['loud_db']} dB{', viewers wrote CLIP here' if c['marked'] else ''}\n"
                      f"speech:\n{speech}\nchat:\n{chat}")
    user = (f"Clip length must be {min_len:.0f}-{max_len:.0f} s, ideally about {cfg.clip_target_sec:.0f} s: "
            f"enough build-up to get what is going on, then the moment and the reaction.\n\n"
            + "\n\n---\n\n".join(blocks))
    try:
        data = _json(_call(cfg, RANK_SYS, user, 4000))
    except Exception as e:
        log(f"Claude не ответил ({e}) — выбираю сам")
        return None
    out = {}
    for d in data if isinstance(data, list) else []:
        try:
            out[int(d["id"])] = {"score": float(d["score"]), "start": float(d["start"]),
                                 "end": float(d["end"]), "why": str(d.get("why", ""))[:80]}
        except (KeyError, TypeError, ValueError):
            continue
    return out


def translate(cfg, lines):
    if not cfg.anthropic_key or not lines:
        return None
    try:
        data = _json(_call(cfg, TR_SYS, json.dumps(lines, ensure_ascii=False), 2000))
    except Exception as e:
        log(f"перевод через Claude не вышел ({e})")
        return None
    if isinstance(data, list) and len(data) == len(lines):
        return [str(x) for x in data]
    return None
