"""Speech to text with faster-whisper (CPU). Times returned are absolute stream seconds."""
import json
import os
import re

from .util import log

# phrases Whisper invents on silence/music (mostly from Russian YouTube subtitle credits)
HALLU = re.compile(
    r"(продолжение следует|субтитр|dimatorzok|синецкая|егорова|спасибо за просмотр|"
    r"подписывайтесь|ставьте лайк|благодарю за внимание|до новых встреч|игорь негода|"
    r"смотрите продолжение|thank you for watching|thanks for watching|subtitles by|"
    r"please subscribe|amara\.org|like and subscribe)", re.I)

PROMPTS = {
    "ru": "Стрим GTA 6. Речь как есть, с матом: бля, сука, пиздец, нахуй, ахахах.",
    "en": "GTA 6 stream. Casual speech, swearing as is: fuck, shit, damn, hahaha.",
}


def _words(text):
    return re.findall(r"\w+", text.lower())


def _prompt_echo(text, prompt):
    if not prompt:
        return False
    tw, pw = _words(text), set(_words(prompt))
    return len(tw) >= 4 and sum(w in pw for w in tw) / len(tw) > 0.6


def _spread(start, end, text):
    toks = text.split()
    if not toks:
        return []
    step = (end - start) / len(toks)
    return [{"start": start + i * step, "end": start + (i + 1) * step - 0.02,
             "word": (" " if i else "") + t, "p": 1.0} for i, t in enumerate(toks)]


class ASR:
    def __init__(self, model_name, cfg):
        self.name = model_name
        self.cfg = cfg
        self._model = None
        self.fake = os.environ.get("CLIPPER_FAKE_ASR")

    def model(self):
        if self._model is None:
            from faster_whisper import WhisperModel
            os.makedirs(self.cfg.models_dir, exist_ok=True)
            log(f"загружаю распознавание речи ({self.name})")
            self._model = WhisperModel(self.name, device="cpu", compute_type="int8",
                                       cpu_threads=os.cpu_count() or 2,
                                       download_root=self.cfg.models_dir)
        return self._model

    def transcribe(self, audio, offset, task="transcribe", beam=5):
        """audio: float32 @16 kHz starting at stream time `offset`."""
        if self.fake:
            return self._fake(offset, offset + len(audio) / 16000.0, task)
        if len(audio) < 16000 * 0.5:
            return []
        lang = self.cfg.language or None
        prompt = PROMPTS.get(lang or "", None) if task == "transcribe" else None
        segs, _info = self.model().transcribe(
            audio, language=lang, task=task, beam_size=beam,
            word_timestamps=(task == "transcribe"),
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 300, "speech_pad_ms": 150},
            condition_on_previous_text=False, initial_prompt=prompt,
            temperature=[0.0, 0.2, 0.4])
        out = []
        for s in segs:
            text = (s.text or "").strip()
            if not text or HALLU.search(text) or _prompt_echo(text, prompt):
                continue
            if s.no_speech_prob > 0.6 and s.avg_logprob < -0.8:
                continue
            if s.compression_ratio > 2.4:
                continue
            words = [{"start": w.start + offset, "end": w.end + offset,
                      "word": w.word, "p": w.probability} for w in (s.words or [])]
            out.append({"start": s.start + offset, "end": s.end + offset,
                        "text": text, "words": words})
        return out

    def _fake(self, start, end, task):
        """Test mode: read a prepared transcript instead of running Whisper."""
        with open(self.fake, encoding="utf-8") as f:
            data = json.load(f)
        out = []
        for s in data:
            if s["end"] <= start or s["start"] >= end:
                continue
            text = s.get("en", "") if task == "translate" else s["text"]
            if not text:
                continue
            words = []
            if task == "transcribe":
                words = [w for w in (s.get("words") or _spread(s["start"], s["end"], text))
                         if start <= w["start"] < end]
            out.append({"start": s["start"], "end": s["end"], "text": text, "words": words})
        return out
