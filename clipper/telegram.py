"""Send clips to Telegram through your own bot."""
import os
import time

from .util import log

API = "https://api.telegram.org/bot{token}/{method}"


class Telegram:
    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id

    @property
    def enabled(self):
        return bool(self.token and self.chat_id)

    def _post(self, method, data=None, files=None, tries=4):
        import requests
        url = API.format(token=self.token, method=method)
        for attempt in range(tries):
            try:
                if files:
                    for f in files.values():
                        f.seek(0)
                r = requests.post(url, data=data, files=files, timeout=300)
                js = r.json()
            except Exception as e:  # network hiccup
                js = {"ok": False, "description": str(e)}
            if js.get("ok"):
                return js.get("result")
            wait = (js.get("parameters") or {}).get("retry_after")
            if attempt + 1 < tries:
                time.sleep(min(60, wait or 5 * (attempt + 1)))
        raise RuntimeError(f"Telegram {method}: {js.get('description')}")

    def text(self, text, silent=False):
        if not self.enabled:
            log("[telegram выключен]", text.replace("\n", " | "))
            return
        self._post("sendMessage", {"chat_id": self.chat_id, "text": text[:4000],
                                   "disable_web_page_preview": "true",
                                   "disable_notification": "true" if silent else "false"})

    def video(self, path, caption, width, height, duration):
        if not self.enabled:
            log(f"[telegram выключен] клип {os.path.basename(path)}")
            return
        with open(path, "rb") as f:
            self._post("sendVideo", {"chat_id": self.chat_id, "caption": caption[:1000],
                                     "supports_streaming": "true", "width": width,
                                     "height": height, "duration": int(round(duration)),
                                     "disable_notification": "true"},
                       files={"video": f})

    def photo(self, path, caption):
        if not self.enabled:
            return
        with open(path, "rb") as f:
            self._post("sendPhoto", {"chat_id": self.chat_id, "caption": caption[:1000]},
                       files={"photo": f})

    def document(self, path, caption):
        if not self.enabled:
            return
        with open(path, "rb") as f:
            self._post("sendDocument", {"chat_id": self.chat_id, "caption": caption[:1000],
                                        "disable_notification": "true"},
                       files={"document": f})


def find_chat(token):
    """If TELEGRAM_CHAT_ID is not set: take the chat of whoever last wrote to the bot."""
    import requests
    try:
        js = requests.get(API.format(token=token, method="getUpdates"), timeout=30).json()
    except Exception as e:
        log(f"Telegram недоступен: {e}")
        return None
    if not js.get("ok"):
        log(f"Telegram: {js.get('description')} — проверь TELEGRAM_BOT_TOKEN")
        return None
    for upd in reversed(js.get("result") or []):
        msg = upd.get("message") or upd.get("edited_message") or {}
        chat = msg.get("chat") or {}
        if chat.get("type") == "private":
            return str(chat["id"])
    return None


def connect(cfg):
    token, chat = cfg.telegram_token, cfg.telegram_chat
    if not token:
        log("TELEGRAM_BOT_TOKEN не задан — клипы останутся только в архиве запуска")
        return Telegram("", "")
    if not chat:
        chat = find_chat(token)
        if not chat:
            log("не знаю, кому слать: напиши своему боту любое сообщение и запусти ещё раз")
            return Telegram("", "")
        tg = Telegram(token, chat)
        tg.text("нашёл тебя 👋 чтобы клипы приходили всегда, добавь в GitHub секрет "
                f"TELEGRAM_CHAT_ID со значением {chat}")
        return tg
    return Telegram(token, chat)
