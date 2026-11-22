"""Everything that talks to Twitch: listing VODs, audio, chat, video pieces.

Uses yt-dlp (no Twitch keys needed) and TwitchDownloaderCLI for the chat replay.
"""
import glob
import os
import re
import shutil

from .util import log, run

VOD_RE = re.compile(r"(?:videos/|^v?)(\d{6,})")


def parse_vod_id(s):
    m = VOD_RE.search((s or "").strip())
    return m.group(1) if m else None


def _ydl(extra=None):
    from yt_dlp import YoutubeDL
    opts = {
        "quiet": True, "no_warnings": True, "noprogress": True,
        "socket_timeout": 30, "retries": 10, "fragment_retries": 10,
    }
    opts.update(extra or {})
    return YoutubeDL(opts)


def list_vods(channel, limit=5):
    """Newest past broadcasts first. 'recording' = the stream is still live."""
    url = f"https://www.twitch.tv/{channel}/videos?filter=archives&sort=time"
    with _ydl({"extract_flat": "in_playlist", "playlistend": limit, "skip_download": True}) as y:
        info = y.extract_info(url, download=False) or {}
    vods = []
    for e in info.get("entries") or []:
        vid = parse_vod_id(str(e.get("id") or e.get("url") or ""))
        if not vid:
            continue
        thumb = str(e.get("thumbnail") or "")
        vods.append({
            "id": vid,
            "title": e.get("title") or "",
            "duration": e.get("duration") or 0,
            "recording": "404_processing" in thumb,
        })
    return vods


def vod_info(vod_id):
    with _ydl({"skip_download": True}) as y:
        return y.extract_info(f"https://www.twitch.tv/videos/{vod_id}", download=False)


def _is_hls(f):
    return str(f.get("protocol") or "").startswith("m3u8")


def pick_video_format(info, max_h=1080):
    vids = [f for f in info.get("formats") or []
            if _is_hls(f) and f.get("vcodec") not in (None, "none") and f.get("height")]
    if not vids:
        raise RuntimeError("у эфира нет видеопотоков (VOD удалён или только для сабов?)")
    ok = [f for f in vids if f["height"] <= max_h] or vids
    return max(ok, key=lambda f: (f.get("height") or 0, f.get("fps") or 0, f.get("tbr") or 0))


def download_audio(vod_id, workdir):
    """Whole stream audio -> 16 kHz mono raw PCM (int16). Returns path."""
    tmpl = os.path.join(workdir, "audio_src.%(ext)s")
    with _ydl({"format": "bestaudio/worst", "outtmpl": tmpl,
               "concurrent_fragment_downloads": 8}) as y:
        y.download([f"https://www.twitch.tv/videos/{vod_id}"])
    src = [p for p in glob.glob(os.path.join(workdir, "audio_src.*")) if not p.endswith(".part")]
    if not src:
        raise RuntimeError("звук эфира не скачался")
    raw = os.path.join(workdir, "audio16k.raw")
    run(["ffmpeg", "-v", "error", "-y", "-i", src[0], "-vn", "-ac", "1", "-ar", "16000",
         "-f", "s16le", raw], timeout=3600)
    os.remove(src[0])
    return raw


def find_tdl():
    for cand in (os.environ.get("TDL_PATH"), shutil.which("TwitchDownloaderCLI"),
                 os.path.join(os.getcwd(), "tools", "TwitchDownloaderCLI")):
        if cand and os.path.exists(cand):
            return cand
    return None


def download_chat(vod_id, out_path):
    exe = find_tdl()
    if not exe:
        log("TwitchDownloaderCLI не найден — ищу моменты без чата")
        return None
    try:
        run([exe, "chatdownload", "--id", vod_id, "-o", out_path,
             "--collision", "Overwrite", "--banner", "false", "-t", "4"],
            timeout=1800, retries=2)
    except Exception as e:
        log(f"чат не скачался ({e}) — ищу моменты без чата")
        return None
    return out_path if os.path.exists(out_path) else None


def download_piece(media_url, start, dur, out_path):
    """Copy a piece of the VOD (no re-encode). Start may land a bit early (keyframes) —
    the exact offset is found later by matching audio."""
    run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, start):.2f}", "-i", media_url,
         "-t", f"{dur:.2f}", "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy",
         "-f", "mpegts", out_path], timeout=900, retries=3)
    return out_path
