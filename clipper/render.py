"""Vertical 9:16 clip: webcam on top + game below (or game on a blurred background),
subtitles in the channel style, loudness evened out for phones."""
import os
import shutil

from .util import FONTS, run

OUT_W, OUT_H = 1080, 1920
WHITE, BLACK, LIME = "&H00FFFFFF", "&H00100C0C", "&H002EFFC8"   # ASS colours are &HAABBGGRR


def _even(v):
    return int(round(v / 2.0)) * 2


def geometry(src_w, src_h, layout, cam_box=None):
    """Crops (in source pixels) and where the text goes (in output pixels)."""
    if layout == "cam_top" and cam_box:
        x, y, w, h = cam_box[0] * src_w, cam_box[1] * src_h, cam_box[2] * src_w, cam_box[3] * src_h
        cam_h = max(520, min(820, _even(OUT_W * h / max(w, 1))))
        target = OUT_W / cam_h
        if w / h > target:            # wider than the panel: trim the sides
            nw = h * target
            x += (w - nw) / 2
            w = nw
        else:                         # taller: trim mostly from the bottom, keep the head
            nh = w / target
            y += (h - nh) * 0.35
            h = nh
        cam = (_even(w), _even(h), _even(x), _even(y))
        game_h = OUT_H - cam_h
        gw = src_h * OUT_W / game_h
        gh = src_h
        if gw > src_w:
            gw, gh = src_w, src_w * game_h / OUT_W
        gw, gh = _even(gw), _even(gh)
        gy = _even((src_h - gh) / 2)
        # slide the game crop sideways if the webcam sits inside it
        best = None
        for gx in sorted({_even((src_w - gw) / 2), 0, _even(src_w - gw)},
                         key=lambda v: abs(v - (src_w - gw) / 2)):
            ox = max(0, min(gx + gw, cam[2] + cam[0]) - max(gx, cam[2]))
            oy = max(0, min(gy + gh, cam[3] + cam[1]) - max(gy, cam[3]))
            overlap = ox * oy / float(gw * gh)
            if best is None or overlap < best[0] - 0.02:
                best = (overlap, gx)
        gx = best[1]
        cl, cr = cam[2], cam[2] + cam[0]
        if min(gy + gh, cam[3] + cam[1]) > max(gy, cam[3]):   # webcam shares rows with the crop
            if gx < cr < gx + 0.3 * gw:                       # its right edge pokes in from the left
                gx = min(_even(cr + 6), src_w - gw)
            elif gx + 0.7 * gw < cl < gx + gw:                # its left edge pokes in from the right
                gx = max(0, _even(cl - gw - 6))
        game = (gw, gh, gx, gy)
        ru_y = cam_h + int(game_h * 0.27)
        return {"layout": "cam_top", "cam": cam, "game": game, "cam_h": cam_h,
                "ru_y": ru_y, "tag": (28, cam_h + 22, 7),
                "game_crop_frac": [game[2] / src_w, game[3] / src_h, gw / src_w, gh / src_h]}
    # game in the middle on a blurred copy of itself
    fw = _even(src_w * 0.84)
    fg_h = _even(OUT_W * src_h / fw)
    top = (OUT_H - fg_h) // 2
    return {"layout": "blur", "fg_w": fw, "fg_h": fg_h, "top": top,
            "ru_y": top - 70, "tag": (OUT_W // 2, top + fg_h + 26, 8)}


def _t(sec):
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int(sec % 3600 // 60)
    return f"{h}:{m:02d}:{sec % 60:05.2f}"


def _esc(text):
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def write_ass(path, geo, ru, en, handle, dur):
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {OUT_W}", f"PlayResY: {OUT_H}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "YCbCr Matrix: TV.709", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: RU,Onest ExtraBold,76,{WHITE},{WHITE},{BLACK},&H8C000000,0,0,0,0,100,100,0,0,1,6,3,2,70,70,0,1",
        f"Style: EN,Onest SemiBold,46,{LIME},{LIME},{BLACK},&H8C000000,0,0,0,0,100,100,0,0,1,4,2,8,90,90,0,1",
        f"Style: TAG,Dela Gothic One,30,{LIME},{LIME},&H38100C0C,&H00000000,0,0,0,0,100,100,1,0,3,9,0,7,0,0,0,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    ru_y = geo["ru_y"]
    for c in ru:
        pop = r"\fscx88\fscy88\t(0,90,\fscx100\fscy100)"
        lines.append(f"Dialogue: 2,{_t(c['start'])},{_t(c['end'])},RU,,0,0,0,,"
                     f"{{\\an2\\pos({OUT_W // 2},{ru_y}){pop}}}{_esc(c['text'])}")
    for c in en:
        lines.append(f"Dialogue: 1,{_t(c['start'])},{_t(c['end'])},EN,,0,0,0,,"
                     f"{{\\an8\\pos({OUT_W // 2},{ru_y + 14})}}{_esc(c['text'])}")
    if handle:
        x, y, an = geo["tag"]
        lines.append(f"Dialogue: 0,{_t(0)},{_t(dur + 1)},TAG,,0,0,0,,"
                     f"{{\\an{an}\\pos({x},{y})}}{_esc(handle)}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def _graph(geo, fps, ass_name):
    tail = f"fps={fps},format=yuv420p" + (f",ass={ass_name}:fontsdir=fonts" if ass_name else "")
    if geo["layout"] == "cam_top":
        cw, ch, cx, cy = geo["cam"]
        gw, gh, gx, gy = geo["game"]
        return (f"[0:v]split=2[c][g];"
                f"[c]crop={cw}:{ch}:{cx}:{cy},scale={OUT_W}:{geo['cam_h']}:flags=lanczos,setsar=1[top];"
                f"[g]crop={gw}:{gh}:{gx}:{gy},scale={OUT_W}:{OUT_H - geo['cam_h']}:flags=lanczos,setsar=1[bot];"
                f"[top][bot]vstack=inputs=2,{tail}[v]")
    fw, fh, top = geo["fg_w"], geo["fg_h"], geo["top"]
    return (f"[0:v]split=2[b][f];"
            f"[b]scale=-2:480,crop=270:480,gblur=sigma=14,scale={OUT_W}:{OUT_H},"
            f"eq=brightness=-0.14:saturation=1.15,setsar=1[bg];"
            f"[f]crop={fw}:ih:(iw-{fw})/2:0,scale={OUT_W}:{fh}:flags=lanczos,setsar=1[fg];"
            f"[bg][fg]overlay=0:{top},{tail}[v]")


def prepare_fonts(workdir):
    dst = os.path.join(workdir, "fonts")
    if not os.path.isdir(dst):
        shutil.copytree(FONTS, dst)


def render(src, offset, dur, geo, out_path, workdir, fps=30, ass_name=None):
    """src: video file; offset: where the clip starts inside it (seconds)."""
    prepare_fonts(workdir)
    src, out_path = os.path.abspath(src), os.path.abspath(out_path)
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0.0, offset):.3f}", "-i", src,
           "-t", f"{dur:.3f}", "-filter_complex", _graph(geo, fps, ass_name),
           "-map", "[v]", "-map", "0:a:0?",
           "-af", "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=48000",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
           "-maxrate", "6M", "-bufsize", "12M", "-profile:v", "high", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", out_path]
    run(cmd, timeout=1800, cwd=workdir)
    if os.path.getsize(out_path) > 48 * 1024 * 1024:   # Telegram bots can send up to 50 MB
        kbps = int(44 * 8 * 1024 / max(dur, 1)) - 160
        small = out_path[:-4] + "_s.mp4"
        run(["ffmpeg", "-v", "error", "-y", "-i", out_path, "-c:v", "libx264", "-preset", "veryfast",
             "-b:v", f"{kbps}k", "-maxrate", f"{kbps}k", "-bufsize", f"{2 * kbps}k",
             "-c:a", "copy", "-movflags", "+faststart", small], timeout=1800)
        os.replace(small, out_path)
    return out_path
