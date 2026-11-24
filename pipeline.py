"""From a stream recording to finished vertical clips in Telegram."""
import json
import os
import time

import numpy as np

from . import audio, chat, detect, llm, render, text
from .asr import ASR
from .util import grab_frame, hms, human_len, log, run, stream_starts, twitch_t, video_size, \
    media_duration

PRE_WIN, POST_WIN = 32.0, 12.0   # seconds of speech looked at around each loud moment
START = time.time()


# ---------------------------------------------------------------- sources

class TwitchSource:
    """A past broadcast on Twitch. Stream time = time in the VOD."""

    def __init__(self, vod_id, info, workdir, max_h=1080):
        from . import twitch
        self.vod_id = vod_id
        self.workdir = workdir
        self.url = twitch.pick_video_format(info, max_h)["url"]
        self.title = (info.get("title") or "").strip() or f"эфир {vod_id}"
        self.duration = float(info.get("duration") or 0)

    def link(self, t):
        return f"https://www.twitch.tv/videos/{self.vod_id}?t={twitch_t(t)}"

    def piece(self, start, dur, idx, raw):
        """Download a piece; return (file, stream time of the file's zero point)."""
        from . import twitch
        path = os.path.join(self.workdir, f"piece_{idx}.ts")
        twitch.download_piece(self.url, start, dur, path)
        f_st, _v_st, a_st = stream_starts(path)
        pcm = run(["ffmpeg", "-v", "error", "-i", path, "-t", "12", "-vn", "-ac", "1",
                   "-ar", "16000", "-f", "s16le", "-"], binary=True, timeout=120).stdout
        got, conf = audio.align(np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0, raw, start)
        if conf < 0.3:
            log(f"кусок {idx}: звук не сопоставился (conf {conf:.2f}), беру время как есть")
            got = start
        return path, got - (a_st - f_st)

    def extra_frames(self, n, workdir):
        out = []
        if self.duration < 60:
            return out
        for k in range(n):
            t = self.duration * (0.08 + 0.84 * k / max(1, n - 1))
            p = os.path.join(workdir, f"frame_x{k}.jpg")
            try:
                out.append(grab_frame(self.url, t, p))
            except Exception as e:
                log(f"кадр {hms(t)} не скачался: {e}")
        return out


class FileSource:
    """A local recording (e.g. what OBS writes to disk)."""

    def __init__(self, path, workdir):
        self.path = path
        self.workdir = workdir
        self.title = os.path.basename(path)
        self.duration = media_duration(path)
        f_st, _v, a_st = stream_starts(path)
        self.shift = a_st - f_st   # audio sample 0 sits this far into the file

    def extract_audio(self):
        raw = os.path.join(self.workdir, "audio16k.raw")
        run(["ffmpeg", "-v", "error", "-y", "-i", self.path, "-vn", "-ac", "1", "-ar", "16000",
             "-f", "s16le", raw], timeout=7200)
        return raw

    def link(self, t):
        return f"{self.title} @ {hms(t)}"

    def piece(self, start, dur, idx, raw):
        return self.path, -self.shift

    def extra_frames(self, n, workdir):
        out = []
        for k in range(n):
            t = self.duration * (0.05 + 0.9 * k / max(1, n - 1))
            p = os.path.join(workdir, f"frame_x{k}.jpg")
            try:
                out.append(grab_frame(self.path, t + self.shift, p))
            except Exception:
                pass
        return out


# ---------------------------------------------------------------- steps

def _iou(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    return inter / max(1e-9, aw * ah + bw * bh - inter)


def find_webcam(src, chosen, cfg, st, workdir, tg):
    if cfg.layout == "blur":
        return None
    if cfg.cam_box:
        return {"box": [float(v) for v in cfg.cam_box], "how": "из config.yaml"}
    import cv2
    from . import facecam
    paths = []
    for c in chosen:
        for frac in (0.3, 0.7):
            t = c["start"] + frac * (c["end"] - c["start"]) - c["origin"]
            p = os.path.join(workdir, f"frame_{c['id']}_{int(frac * 10)}.jpg")
            try:
                paths.append(grab_frame(c["piece"], t, p))
            except Exception:
                pass
    paths += src.extra_frames(8, workdir)
    imgs = [im for im in (cv2.imread(p) for p in paths) if im is not None]
    if imgs:
        size = max({im.shape for im in imgs}, key=lambda s: sum(im.shape == s for im in imgs))
        imgs = [im for im in imgs if im.shape == size]
    debug = os.path.join(workdir, "webcam.jpg")
    cam = facecam.detect(imgs, debug_path=debug) if len(imgs) >= 3 else None
    key = cfg.get("channel_key") or cfg["channel"]
    cams = st.setdefault("cams", {})
    prev = cams.get(key) or (st.get("cam") if not cfg.get("is_other") else None)
    if cam:
        if not prev or _iou(prev["box"], cam["box"]) < 0.7:
            try:
                tg.photo(debug, cfg.get("label", "") + "так вижу вебку (зелёная рамка) — она пойдёт "
                                "сверху клипа. если рамка мимо, пропиши cam_box в config.yaml")
            except Exception as e:
                log(f"не отправил кадр с вебкой: {e}")
        cams[key] = cam
        return cam
    if prev and cfg.layout != "blur":
        log("беру прошлое положение вебки")
        return prev
    return None


def pick_moments(raw, comments, cfg):
    loud = audio.loudness_db(raw)
    speech = audio.speech_mask(raw, len(loud))
    chatf = chat.features(comments, len(loud), cfg.chat_delay_sec) if comments else None
    cands, chat_w = detect.candidates(loud, speech, chatf, cfg)
    log(f"кандидатов {len(cands)}; чат: {chatf['total'] if chatf else 0} сообщений "
        f"(вес {chat_w}), «клип» в чате: {len(chatf['marks']) if chatf else 0}")
    if not cands:
        return [], chatf
    dur = audio.duration(raw)
    fast = ASR(cfg.whisper_fast, cfg)
    for i, c in enumerate(cands):
        w0, w1 = max(0.0, c["t"] - PRE_WIN), min(dur, c["t"] + POST_WIN)
        c.update(id=i, win=(w0, w1))
        c["segs"] = fast.transcribe(audio.slice_f32(raw, w0, w1), w0, beam=1)
        c["tscore"], c["tfeat"] = text.text_score(c["segs"], c["t"])
        c["chat_lines"] = chat.lines_between(chatf, w0, w1)
    sig = np.array([c["signal"] for c in cands], np.float32)
    ts = np.array([c["tscore"] for c in cands], np.float32)
    zs = (sig - sig.mean()) / (sig.std() + 1e-6)
    zt = (ts - ts.mean()) / (ts.std() + 1e-6)
    for c, a, b in zip(cands, zs, zt):
        c["final"] = float(0.6 * a + 0.4 * b + (1.5 if c["marked"] else 0.0))
    ranked = llm.rank(cfg, cands, cfg.clip_min_sec, cfg.clip_max_sec)
    if ranked:
        for c in cands:
            r = ranked.get(c["id"])
            c["llm"] = r
            c["final"] = (r["score"] if r else -5.0) + 0.15 * c["final"] + (2.0 if c["marked"] else 0)
    cands.sort(key=lambda c: -c["final"])
    for c in cands:
        log(f"  {hms(c['t'])}  итог {c['final']:+.2f}  сигнал {c['signal']:+.2f}  "
            f"текст {c['tscore']:+.2f}  громче на {c['loud_db']} дБ"
            f"{'  «клип»' if c['marked'] else ''}  {c['tfeat']}")
    # no quota: every moment that is worth it goes in
    strict = float(cfg.strictness)

    def worth_it(c):
        if c["marked"] and cfg.get("force_marks", True):   # somebody wrote "клип"
            return True
        if ranked:
            return bool(c.get("llm")) and c["llm"]["score"] >= min(9.5, 6.0 * strict)
        f = c["tfeat"]
        reaction = c["signal"] >= 4.0 * strict and (f["words"] >= 3 or f["laugh"] >= 1)
        emotion = c["signal"] >= 2.5 * strict and (f["laugh"] >= 1 or f["swear"] >= 1 or f["inter"] >= 2)
        return reaction or emotion

    def has_something(c):                    # speech, laughter, a "клип" or a really loud bang
        f = c["tfeat"]
        return c["marked"] or f["words"] >= 3 or f["laugh"] >= 1 or c["loud_db"] >= 12

    cap = int(cfg.clips_max)
    force = bool(cfg.get("force_marks", True))
    marked = [c for c in cands if c["marked"]] if force else []
    rest = [c for c in cands if (not force or not c["marked"]) and has_something(c)]
    if ranked:
        rest = [c for c in rest if c.get("llm") and c["llm"]["score"] >= min(9.5, 5.0 * strict)]
    if cap:   # only the strongest few of this stream (cands are sorted best first)
        rest = rest[: max(0, cap - len(marked))]
    else:
        rest = [c for c in rest if worth_it(c)]
    chosen = marked + rest
    if len(chosen) < int(cfg.clips_min):   # even a quiet stream gives a few to pick from
        have = {c["id"] for c in chosen}
        chosen += [c for c in cands if c["id"] not in have][: int(cfg.clips_min) - len(chosen)]
    chosen.sort(key=lambda c: -c["final"])
    log(f"клипов будет: {len(chosen)} из {len(cands)} кандидатов")
    for c in chosen:
        lo, hi = c["win"]
        r = c.get("llm")
        if r and 0 <= r["start"] < r["end"] <= hi - lo + 0.5 and \
                cfg.clip_min_sec - 1 <= r["end"] - r["start"] <= cfg.clip_max_sec + 1:
            c["start"], c["end"] = lo + r["start"], min(hi, lo + r["end"])
        else:
            c["start"], c["end"] = text.choose_bounds(text.all_words(c["segs"]), c["t"], lo, hi,
                                                      cfg.clip_min_sec, cfg.clip_max_sec,
                                                      cfg.clip_target_sec)
    return chosen, chatf


def subtitles(raw, c, cfg, subs_asr):
    cs, ce = c["start"], c["end"]
    a = audio.slice_f32(raw, cs - 0.3, ce + 0.3)
    segs = subs_asr.transcribe(a, cs - 0.3)
    ru = text.subtitle_chunks(text.all_words(segs), cs, ce)
    en = []
    if cfg.english_line and segs:
        tr = llm.translate(cfg, [text.norm_line(s["text"]) for s in segs])
        if tr:
            en = text.subtitle_lines([dict(s, text=t) for s, t in zip(segs, tr)], cs, ce)
        else:
            en = text.subtitle_lines(subs_asr.transcribe(a, cs - 0.3, task="translate"), cs, ce)
    return ru, en


def _safe(what, fn):
    try:
        fn()
    except Exception as e:
        log(f"Telegram: {what} не отправилось ({e})")


def _fetch(src, c, raw):
    c["piece"], c["origin"] = src.piece(c["start"] - 4, c["end"] - c["start"] + 8, c["id"], raw)


def _caption(r):
    lines = [f"#{r['rank']} · {hms(r['start'])} · {r['dur']:.0f} сек"
             + (" · кто-то написал «клип»" if r["marked"] else "")]
    if r["said"]:
        lines.append(f"«{r['said'][:300]}»")
    if r["why"]:
        lines.append(r["why"])
    lines.append(r["link"])
    return "\n".join(lines)


def process(src, raw_path, comments, cfg, tg, st, outdir, workdir):
    outdir = os.path.abspath(outdir)
    os.makedirs(outdir, exist_ok=True)
    raw = audio.load_raw(raw_path)
    log(f"эфир «{src.title}», {human_len(audio.duration(raw))}")
    chosen, _chatf = pick_moments(raw, comments, cfg)
    if not chosen:
        _safe("сообщение", lambda: tg.text(cfg.get("label", "") + f"«{src.title}»: годных моментов не нашлось 🤷"))
        return []
    first = chosen[:8]                      # enough frames to find the webcam
    for c in first:
        _fetch(src, c, raw)
    cam = find_webcam(src, first, cfg, st, workdir, tg)
    src_w, src_h = video_size(first[0]["piece"])
    layout = "cam_top" if cam and cfg.layout in ("auto", "cam_top") else "blur"
    geo = render.geometry(src_w, src_h, layout, cam["box"] if cam else None)
    log(f"раскладка: {geo['layout']}")
    subs_asr = ASR(cfg.whisper_subs, cfg) if cfg.subtitles else None
    _safe("заголовок", lambda: tg.text(
        cfg.get("label", "") + f"🎬 {src.title}\n{human_len(src.duration)} · лучших моментов: {len(chosen)}. "
        f"присылаю по одному, сверху лучшие"))

    budget = float(os.environ.get("CLIPPER_BUDGET_MIN", "250")) * 60
    results = []
    for rank_i, c in enumerate(chosen, 1):
        if time.time() - START > budget:
            left = len(chosen) - rank_i + 1
            log(f"время запуска на исходе, не успеваю ещё {left}")
            _safe("остаток", lambda: tg.text(
                f"не успел ещё {left} клипов: GitHub даёт запуску 5 часов. лучшие уже выше", silent=True))
            break
        try:
            if "piece" not in c:
                _fetch(src, c, raw)
            dur = c["end"] - c["start"]
            ru, en = subtitles(raw, c, cfg, subs_asr) if subs_asr else ([], [])
            name = f"{rank_i:02d}_{hms(c['start']).replace(':', '-')}"
            ass = render.write_ass(os.path.join(workdir, f"{name}.ass"), geo, ru, en,
                                   cfg.handle_text, dur)
            out = os.path.join(outdir, f"{name}.mp4")
            render.render(c["piece"], c["start"] - c["origin"], dur, geo, out, workdir, cfg.fps,
                          os.path.basename(ass))
            clean = None
            if cfg.send_clean_copy:
                clean = os.path.join(outdir, f"{name}_clean.mp4")
                render.render(c["piece"], c["start"] - c["origin"], dur, geo, clean, workdir,
                              cfg.fps, None)
            said = " ".join(x["text"] for x in ru) or text.norm_line(
                text.window_text(c["segs"], c["start"], c["end"]))
            r = {"rank": rank_i, "path": out, "clean": clean, "start": round(c["start"], 2),
                 "end": round(c["end"], 2), "dur": round(dur, 2), "said": said,
                 "why": (c.get("llm") or {}).get("why", ""), "marked": c["marked"],
                 "loud_db": c["loud_db"], "score": round(c["final"], 2),
                 "link": src.link(c["start"])}
            results.append(r)
            log(f"клип {rank_i}: {hms(c['start'])} +{dur:.0f}с «{said[:60]}»")
            _safe(f"клип {rank_i}", lambda: tg.video(r["path"], _caption(r), 1080, 1920, r["dur"]))
            if clean:
                _safe(f"чистый клип {rank_i}", lambda: tg.document(clean, f"#{rank_i} без субтитров"))
        except Exception as e:
            log(f"клип {rank_i} не получился: {e}")
        finally:
            piece = c.get("piece")
            if piece and isinstance(src, TwitchSource) and os.path.exists(piece):
                os.remove(piece)

    with open(os.path.join(outdir, "clips.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    n = len(results)
    word = "клип" if n % 10 == 1 and n % 100 != 11 else \
        "клипа" if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14) else "клипов"
    _safe("итог", lambda: tg.text(cfg.get("label", "") + f"всё, {n} {word} с «{src.title}»", silent=True))
    return results
