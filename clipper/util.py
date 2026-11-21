"""Small shared helpers: logging, subprocess, time formatting."""
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")
FONTS = os.path.join(ASSETS, "fonts")


def log(*parts):
    print(time.strftime("[%H:%M:%S]"), *parts, flush=True)


def run(cmd, timeout=None, retries=1, binary=False, cwd=None):
    """Run a command, retrying on failure. Returns CompletedProcess."""
    last = None
    for attempt in range(max(1, retries)):
        try:
            p = subprocess.run(
                [str(c) for c in cmd], cwd=cwd, timeout=timeout,
                capture_output=True, text=not binary,
            )
        except subprocess.TimeoutExpired:
            last = f"timeout after {timeout}s"
        else:
            if p.returncode == 0:
                return p
            err = p.stderr if not binary else p.stderr.decode("utf-8", "replace")
            last = f"exit {p.returncode}: {(err or '').strip()[-1200:]}"
        if attempt + 1 < retries:
            time.sleep(4 * (attempt + 1))
    raise RuntimeError(f"{os.path.basename(str(cmd[0]))} failed: {last}")


def ffprobe(path):
    p = run(["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", path], timeout=120)
    return json.loads(p.stdout)


def stream_starts(path):
    """(format_start, video_start, audio_start) in seconds."""
    info = ffprobe(path)
    fmt = float(info.get("format", {}).get("start_time") or 0)
    v = a = None
    for s in info.get("streams", []):
        st = s.get("start_time")
        st = float(st) if st not in (None, "N/A") else fmt
        if s.get("codec_type") == "video" and v is None:
            v = st
        if s.get("codec_type") == "audio" and a is None:
            a = st
    return fmt, (fmt if v is None else v), (fmt if a is None else a)


def grab_frame(src, t, out_path):
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, t):.2f}", "-i", src,
         "-frames:v", "1", "-q:v", "3", out_path], timeout=180, retries=2)
    return out_path


def video_size(path):
    for s in ffprobe(path).get("streams", []):
        if s.get("codec_type") == "video":
            return int(s["width"]), int(s["height"])
    raise RuntimeError(f"в {os.path.basename(path)} нет видео")


def media_duration(path):
    info = ffprobe(path)
    return float(info.get("format", {}).get("duration") or 0)


def hms(sec):
    sec = int(round(max(0, sec)))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def twitch_t(sec):
    sec = int(max(0, sec))
    h, r = divmod(sec, 3600)
    m, s = divmod(r, 60)
    return f"{h}h{m:02d}m{s:02d}s"


def human_len(sec):
    sec = int(round(sec))
    h, r = divmod(sec, 3600)
    m = r // 60
    if h:
        return f"{h} ч {m} мин"
    return f"{m} мин"


def die(msg, code=1):
    log("ERROR:", msg)
    sys.exit(code)
