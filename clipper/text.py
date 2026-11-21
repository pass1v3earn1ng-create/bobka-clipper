"""What was said around a moment: how 'hot' it is, where to cut, subtitle chunks."""
import re

LAUGH = re.compile(
    r"((?:ха-?){2,}|а?ха-?ха|хах|ахах|хи-?хи|хе-?хе|\(сме[её]тся\)|\[смех\]|\(смех\)|"
    r"haha|ha-ha|\blol\b)", re.I)
SWEAR = re.compile(
    r"\b(бля\w*|блят\w*|сук[аиеу]\b|пизд\w*|ху[йеяюи]\w*|еба\w*|ебу\w*|ебн\w*|ёб\w*|"
    r"нахуй|похуй|оху\w*|fuck\w*|shit\w*|damn|bitch\w*)", re.I)
INTERJ = re.compile(
    r"\b(а{2,}\w*|о{2,}|э{2,}|ой+|ай+|вау|ого+|опа|воу|ох+|господи|боже|блин|"
    r"нет-?нет|да ладно|ты видел|смотри|whoa|wow|omg|oh my god|no{2,})\b", re.I)


def all_words(segs):
    ws = [w for s in segs for w in (s.get("words") or [])]
    return sorted(ws, key=lambda w: w["start"])


def window_text(segs, start, end):
    return " ".join(s["text"] for s in segs if s["end"] > start and s["start"] < end).strip()


def text_score(segs, peak):
    near = [w for w in all_words(segs) if peak - 12 <= w["start"] <= peak + 6]
    txt = " ".join(w["word"].strip() for w in near).lower()
    f = {
        "laugh": len(LAUGH.findall(txt)),
        "swear": len(SWEAR.findall(txt)),
        "inter": len(INTERJ.findall(txt)),
        "marks": txt.count("!") + txt.count("?"),
        "words": len(near),
    }
    score = (1.2 * min(f["laugh"], 3) + 0.7 * min(f["swear"], 3) + 0.5 * min(f["inter"], 4)
             + 0.2 * min(f["marks"], 4) + 0.05 * min(f["words"], 30))
    if f["words"] < 3 and f["laugh"] == 0:
        score -= 1.0
    return score, f


def choose_bounds(words, peak, lo, hi, min_len, max_len, lead=9.0):
    """Cut so the reaction lands near the end and the clip starts ~`lead` s before it,
    preferably where a phrase begins."""
    words = sorted(words, key=lambda w: w["start"])
    end = peak + 4.0
    for w in words:  # let the phrase being said at the end finish
        if w["start"] <= end + 0.6 and w["end"] > end - 0.3 and w["end"] <= peak + 9:
            end = max(end, w["end"] + 0.35)
    end = min(end, hi)
    earliest = max(lo, end - max_len)
    latest = max(earliest, end - min_len)
    target = min(max(peak - lead, earliest), latest)
    best = None
    starts = [nxt["start"] for prev, nxt in zip(words, words[1:]) if nxt["start"] - prev["end"] >= 0.35]
    if words:
        starts.append(words[0]["start"])
    for ws in starts:
        s = ws - 0.25
        if earliest <= s <= latest and (best is None or abs(s - target) < best[0]):
            best = (abs(s - target), s)
    start = best[1] if best and best[0] <= 2.5 else target
    for w in words:  # never start in the middle of a word
        if w["start"] < start < w["end"]:
            start = w["start"] - 0.1 if w["start"] - 0.1 >= earliest else min(w["end"] + 0.05, latest)
            break
    start = max(lo, start)
    if end - start < min_len:
        start = max(lo, end - min_len)
    if end - start > max_len:
        start = end - max_len
    return round(start, 2), round(end, 2)


def norm_line(text):
    t = re.sub(r"\s+", " ", text).strip().lower()
    t = t.lstrip("-–— ").replace("...", "…")
    t = re.sub(r"[.,;:]+$", "", t)
    return t.strip()


def subtitle_chunks(words, clip_start, clip_end, max_chars=18, max_words=4):
    ws = [w for w in words if w["end"] > clip_start + 0.05 and w["start"] < clip_end - 0.05
          and w["word"].strip()]
    out, cur = [], []

    def flush():
        if cur:
            out.append({"start": cur[0]["start"], "end": cur[-1]["end"],
                        "text": " ".join(x["word"].strip() for x in cur)})

    for w in ws:
        joined = " ".join(x["word"].strip() for x in cur + [w])
        if cur and (len(joined) > max_chars or len(cur) >= max_words
                    or w["start"] - cur[-1]["end"] > 0.45
                    or cur[-1]["word"].strip()[-1:] in ".?!,…"):
            flush()
            cur = []
        cur.append(w)
    flush()
    for a, b in zip(out, out[1:]):
        a["end"] = b["start"] if b["start"] - a["end"] < 0.8 else a["end"] + 0.3
    if out:
        out[-1]["end"] = min(out[-1]["end"] + 0.4, clip_end)
    res = []
    for c in out:
        s, e = max(c["start"], clip_start) - clip_start, min(c["end"], clip_end) - clip_start
        t = norm_line(c["text"])
        if t and e - s > 0.15:
            res.append({"start": s, "end": e, "text": t})
    return res


def subtitle_lines(segs, clip_start, clip_end):
    res = []
    for s in segs:
        if s["end"] <= clip_start or s["start"] >= clip_end:
            continue
        t = norm_line(s["text"])
        if t:
            res.append({"start": max(s["start"], clip_start) - clip_start,
                        "end": min(s["end"], clip_end) - clip_start, "text": t})
    return res
