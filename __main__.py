"""python -m clipper check | run | file | test-telegram"""
import argparse
import os
import shutil
import sys
import tempfile
import time
import traceback

from . import config, state
from .util import log


def cmd_check(cfg, args):
    """Which past broadcasts still need clips (own channel + others). Writes todo=<ids>."""
    from . import twitch
    st = state.load()
    todo = []
    if args.vod.strip():
        vid = twitch.parse_vod_id(args.vod)
        if not vid:
            log(f"не понял, какой это эфир: {args.vod!r}. Нужна ссылка вида twitch.tv/videos/123…")
        else:
            todo.append(vid)
    else:
        errors = []
        for ch in config.channels(cfg):
            try:
                todo += _new_vods(config.for_channel(cfg, ch), st, twitch, ch)
            except Exception as e:  # one broken channel must not stop the others
                log(f"{ch}: не смог проверить эфиры: {e}")
                errors.append(f"{ch}: {str(e)[:200]}")
        # Twitch hiccup or a typo in a nickname: say so at most twice a day, never fail the run
        if errors and time.time() - st.get("check_error_at", 0) > 12 * 3600:
            st["check_error_at"] = time.time()
            try:
                from . import telegram
                telegram.connect(cfg).text("не могу проверить эфиры:\n" + "\n".join(errors)
                                           + "\nпопробую снова через час")
            except Exception:
                pass
        state.save(st)
    log("нарезать:", ", ".join(todo) or "нечего")
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as f:
            f.write(f"todo={','.join(todo)}\n")


def _new_vods(cfg, st, twitch, channel):
    todo = []
    own = channel == cfg["channel"] and not cfg.get("is_other")
    first_run = not any(r.get("channel", cfg["channel"] if own else None) == channel
                        for r in st["vods"].values())
    vods = twitch.list_vods(channel, limit=5)
    if not vods:
        log(f"{channel}: нет сохранённых эфиров"
            + (" — проверь, что в Twitch включено «Сохранять прошлые трансляции»" if own else ""))
    for v in vods:
        if state.is_finished(st, v["id"]):
            continue
        if v["recording"]:
            log(f"{channel} {v['id']} ещё идёт — нарежу после конца эфира")
            continue
        info = twitch.vod_info(v["id"])
        if info.get("is_live"):
            log(f"{channel} {v['id']} ещё идёт — нарежу после конца эфира")
            continue
        ended = (info.get("timestamp") or 0) + (info.get("duration") or 0)
        if ended and time.time() - ended > float(cfg.max_vod_age_hours) * 3600:
            state.mark(st, v["id"], "skipped", title=(info.get("title") or "")[:80], channel=channel)
            log(f"{channel} {v['id']} старше {cfg.max_vod_age_hours} ч — пропускаю")
            continue
        todo.append(v["id"])
        if first_run:
            break   # first time we see this channel: only its newest stream, no backlog
    return todo


def cmd_run(cfg, args):
    from . import chat, pipeline, telegram, twitch
    ids = [twitch.parse_vod_id(x) for x in args.vods.split(",") if x.strip()]
    ids = [x for x in ids if x]
    if not ids:
        log("нечего нарезать")
        return
    st = state.load()
    tg = telegram.connect(cfg)
    started = time.time()
    for vid in ids:
        if time.time() - started > 150 * 60:   # leave the rest for the next hourly run
            log(f"уже 2,5 часа в работе — {vid} и дальше нарежу в следующий запуск")
            break
        work = tempfile.mkdtemp(prefix=f"vod{vid}_")
        try:
            log(f"=== эфир {vid} ===")
            info = twitch.vod_info(vid)
            if info.get("is_live"):
                log("эфир ещё идёт — пропускаю")
                continue
            channel = str(info.get("uploader_id") or "").lower()
            ccfg = config.for_channel(cfg, channel)
            if ccfg.get("is_other"):
                log(f"чужой канал {channel}: свои настройки кампании")
            src = pipeline.TwitchSource(vid, info, work, int(ccfg.video_max_height))
            raw = twitch.download_audio(vid, work)
            chat_path = twitch.download_chat(vid, os.path.join(work, "chat.json"))
            comments = chat.load(chat_path) if chat_path else []
            res = pipeline.process(src, raw, comments, ccfg, tg, st,
                                   os.path.join(args.out, vid), work)
            state.mark(st, vid, "done", clips=len(res), title=src.title[:80], channel=channel)
        except Exception as e:
            traceback.print_exc()
            rec = state.mark(st, vid, "error", error=str(e)[:300])
            msg = f"не вышло нарезать эфир {vid}: {str(e)[:300]}"
            msg += "\nпопробую ещё раз через час" if rec["status"] == "error" else "\nсдаюсь после 3 попыток"
            try:
                tg.text(msg)
            except Exception:
                pass
        finally:
            state.save(st)
            shutil.rmtree(work, ignore_errors=True)


def cmd_file(cfg, args):
    from . import chat, pipeline, telegram
    if not os.path.exists(args.path):
        log(f"нет файла {args.path}")
        sys.exit(1)
    st = state.load()
    tg = telegram.connect(cfg) if not args.no_telegram else telegram.Telegram("", "")
    work = tempfile.mkdtemp(prefix="file_")
    try:
        src = pipeline.FileSource(os.path.abspath(args.path), work)
        raw = src.extract_audio()
        comments = chat.load(args.chat) if args.chat else []
        name = os.path.splitext(os.path.basename(args.path))[0]
        pipeline.process(src, raw, comments, cfg, tg, st, os.path.join(args.out, name), work)
        state.save(st)
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
        else:
            log(f"рабочая папка: {work}")


def cmd_test_telegram(cfg, args):
    from . import telegram
    tg = telegram.connect(cfg)
    if tg.enabled:
        tg.text("бот на связи ✅ после эфира сюда придут клипы")
        log("сообщение отправлено")
    else:
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser(prog="clipper", description="автонарезка клипов с Twitch")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("check")
    p.add_argument("--vod", default="")
    p.add_argument("--github-output", default="")
    p = sub.add_parser("run")
    p.add_argument("--vods", default="")
    p.add_argument("--out", default="clips")
    p = sub.add_parser("file")
    p.add_argument("path")
    p.add_argument("--chat", default="")
    p.add_argument("--out", default="clips")
    p.add_argument("--no-telegram", action="store_true")
    p.add_argument("--keep", action="store_true")
    sub.add_parser("test-telegram")
    args = ap.parse_args()
    cfg = config.load()
    {"check": cmd_check, "run": cmd_run, "file": cmd_file,
     "test-telegram": cmd_test_telegram}[args.cmd](cfg, args)


if __name__ == "__main__":
    main()
