"""Find candidate moments in a stream from cheap signals.

Score per 0.5 s:
  * loud reaction: how much louder than the last ~90 s, boosted when it is speech
    (screaming/laughing into the mic, not just an explosion in the game);
  * a lot of talking in a row;
  * chat getting busy (shifted back by the chat delay), laughs and hype words count more;
  * "клип"/"clip" written in chat = this moment goes in almost for sure.
"""
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

from .audio import HOP


def movavg(x, n):
    n = int(max(1, n))
    if n <= 1:
        return x.astype(np.float32)
    k = np.ones(n, np.float32) / n
    return np.convolve(x, k, mode="same").astype(np.float32)


def rolling_median(x, n):
    n = int(max(3, n)) | 1
    if len(x) == 0:
        return x
    half = n // 2
    pad = np.pad(x, (half, half), mode="edge")
    stride = max(1, n // 15)
    idx = np.arange(0, len(x), stride)
    med = np.median(sliding_window_view(pad, n)[idx], axis=1)
    return np.interp(np.arange(len(x)), idx, med).astype(np.float32)


def robust_z(x, floor):
    """How unusual each value is compared with the rest of the stream.
    `floor` keeps a near-constant signal (quiet chat, flat game noise) from exploding."""
    x = x.astype(np.float32)
    med = float(np.median(x))
    mad = float(np.median(np.abs(x - med))) * 1.4826
    return (x - med) / max(mad, floor)


def score_timeline(loud, speech, chatf, cfg):
    n = len(loud)
    L = movavg(loud, 2)
    base = rolling_median(L, 90 / HOP)
    excess = np.clip(L - base, 0, 25)
    talk = movavg(speech, 3 / HOP)
    react = movavg(excess, 3 / HOP) * (0.35 + 0.65 * talk)
    density = movavg(speech, 12 / HOP)
    score = np.clip(robust_z(react, 0.5), -3, 15) + 0.35 * np.clip(robust_z(density, 0.05), -2, 3)
    parts = {"excess": excess, "talk": talk}

    chat_w = 0.0
    if chatf and chatf["total"] > 0:
        raw = chatf["msgs"] + 1.5 * chatf["laugh"] + chatf["hype"]
        busy = movavg(raw, 12 / HOP) * (12 / HOP)
        total = chatf["total"]
        chat_w = 1.0 if total >= 60 else 0.7 if total >= 20 else 0.4
        score = score + chat_w * np.clip(robust_z(busy, 1.0), 0, 8)
        parts["chat"] = busy
    score = movavg(score, 4 / HOP)

    marked = np.zeros(n, bool)
    for m in (chatf or {}).get("marks", []):
        i0, i1 = int((m - 20) / HOP), int((m - 1) / HOP)
        i0, i1 = max(0, i0), min(n, max(i1, i0 + 1))
        score[i0:i1] += 6.0
        marked[i0:i1] = True
    parts["marked"] = marked

    # do not clip the intro / the raid at the end
    lo = int(cfg.skip_start_sec / HOP)
    hi = n - int(cfg.skip_end_sec / HOP)
    if hi - lo > int(120 / HOP):
        score[:lo] = -1e6
        score[hi:] = -1e6
    return score, parts, chat_w


def pick_peaks(score, min_value, at_least=3, max_peaks=400, min_gap_sec=40):
    """All local peaks above `min_value` (at least `at_least` of them), strongest first.
    max_peaks only keeps the speech pass within GitHub's time on a wild 10-hour stream."""
    s = score.copy()
    gap = int(min_gap_sec / HOP)
    peaks = []
    while len(peaks) < max_peaks:
        i = int(np.argmax(s))
        v = float(s[i])
        if v < -1e5:
            break
        if v < min_value and len(peaks) >= at_least:
            break
        peaks.append((i, v))
        s[max(0, i - gap): i + gap] = -1e6
    return peaks


def candidates(loud, speech, chatf, cfg):
    score, parts, chat_w = score_timeline(loud, speech, chatf, cfg)
    out = []
    cap = int(cfg.get("clips_max") or 0)
    if cap:
        peaks = pick_peaks(score, 1.0, at_least=max(3, int(cfg.clips_min)), max_peaks=max(40, 6 * cap))
    else:
        peaks = pick_peaks(score, 2.0 * float(cfg.strictness), at_least=max(3, int(cfg.clips_min)))
    for i, v in peaks:
        # the loudest point is the reaction; it usually comes right after the thing itself
        j0, j1 = max(0, i - int(6 / HOP)), min(len(score), i + int(6 / HOP))
        local = parts["excess"][j0:j1]
        peak = (j0 + int(np.argmax(local))) * HOP if len(local) and local.max() > 3 else i * HOP
        out.append({
            "t": round(float(peak), 2),
            "signal": round(v, 3),
            "loud_db": round(float(parts["excess"][i]), 1),
            "talk": round(float(parts["talk"][i]), 2),
            "chat": round(float(parts["chat"][i]), 1) if "chat" in parts else 0.0,
            "marked": any(m - 25 <= peak <= m + 3 for m in (chatf or {}).get("marks", [])),
        })
    return out, chat_w
