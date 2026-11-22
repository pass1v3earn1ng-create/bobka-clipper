"""Settings: config.yaml merged over defaults, secrets from the environment."""
import copy
import os

from .util import ROOT, log

DEFAULTS = {
    "channel": "1rlandectv",
    "clips_min": 3,
    "clips_max": 8,
    "strictness": 1.0,
    "clip_min_sec": 18,
    "clip_max_sec": 30,
    "clip_target_sec": 22,
    "language": "ru",
    "subtitles": True,
    "english_line": True,
    "handle_text": "twitch.tv/1rlandectv",
    "layout": "auto",
    "cam_box": None,
    "skip_start_sec": 120,
    "skip_end_sec": 60,
    "chat_delay_sec": 6,
    "max_vod_age_hours": 48,
    "fps": 30,
    "whisper_fast": "small",
    "whisper_subs": "medium",
    "llm_model": "claude-sonnet-5-5",
    "send_clean_copy": False,
    "video_max_height": 1080,
}


class Cfg(dict):
    def __getattr__(self, k):
        try:
            return self[k]
        except KeyError as e:
            raise AttributeError(k) from e


def load(path=None):
    cfg = copy.deepcopy(DEFAULTS)
    path = path or os.environ.get("CLIPPER_CONFIG") or os.path.join(ROOT, "config.yaml")
    if os.path.exists(path):
        try:
            import yaml
            with open(path, encoding="utf-8") as f:
                user = yaml.safe_load(f) or {}
            for k, v in user.items():
                if k in DEFAULTS and (v is not None or k in ("cam_box", "language")):
                    cfg[k] = v
        except Exception as e:  # a typo in config must not kill the run
            log(f"config.yaml не прочитался ({e}), беру настройки по умолчанию")
    cfg["clip_min_sec"] = max(3, float(cfg["clip_min_sec"]))
    cfg["clip_max_sec"] = max(cfg["clip_min_sec"] + 2, float(cfg["clip_max_sec"]))
    cfg["clip_target_sec"] = min(cfg["clip_max_sec"], max(cfg["clip_min_sec"], float(cfg["clip_target_sec"])))
    cfg["clips_min"] = max(0, int(cfg["clips_min"]))
    cfg["clips_max"] = max(0, int(cfg["clips_max"]))
    cfg["strictness"] = min(3.0, max(0.3, float(cfg["strictness"])))
    if isinstance(cfg.get("language"), str) and not cfg["language"].strip():
        cfg["language"] = None
    cfg["telegram_token"] = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    cfg["telegram_chat"] = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    cfg["anthropic_key"] = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    cfg["models_dir"] = os.environ.get("WHISPER_MODELS_DIR", os.path.join(ROOT, ".models"))
    return Cfg(cfg)

