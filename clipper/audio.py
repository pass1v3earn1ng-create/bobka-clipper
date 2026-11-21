"""Stream audio features: loudness and where somebody is talking."""
import math

import numpy as np

SR = 16000
HOP = 0.5  # seconds per analysis step


def load_raw(path):
    return np.memmap(path, dtype=np.int16, mode="r")


def duration(raw):
    return len(raw) / SR


def slice_f32(raw, start, end):
    i0 = max(0, int(start * SR))
    i1 = min(len(raw), int(end * SR))
    if i1 <= i0:
        return np.zeros(0, np.float32)
    return np.asarray(raw[i0:i1], dtype=np.float32) / 32768.0


def loudness_db(raw):
    hop = int(SR * HOP)
    n = len(raw) // hop
    out = np.full(n, -90.0, np.float32)
    step = 4096
    for i in range(0, n, step):
        k = min(step, n - i)
        blk = np.asarray(raw[i * hop:(i + k) * hop], dtype=np.float32).reshape(k, hop) / 32768.0
        rms = np.sqrt(np.mean(blk * blk, axis=1) + 1e-12)
        out[i:i + k] = 20.0 * np.log10(rms + 1e-6)
    return out


def speech_mask(raw, n_hops):
    """1.0 where Silero VAD hears speech (her voice, but also game dialogue)."""
    from faster_whisper.vad import VadOptions, get_speech_timestamps
    mask = np.zeros(n_hops, np.float32)
    chunk = SR * 600
    opts = VadOptions(min_silence_duration_ms=250, speech_pad_ms=80)
    hop = SR * HOP
    for off in range(0, len(raw), chunk):
        a = np.asarray(raw[off:off + chunk], dtype=np.float32) / 32768.0
        if len(a) < SR:
            continue
        for s in get_speech_timestamps(a, opts):
            i0 = int((off + s["start"]) / hop)
            i1 = int(math.ceil((off + s["end"]) / hop))
            mask[i0:min(i1, n_hops)] = 1.0
    return mask


def align(piece_audio, raw, expected_start, search=20.0):
    """Find where piece_audio (float32 @16k) starts in the full stream audio.
    Returns (start_seconds, confidence 0..1)."""
    seg = piece_audio[: SR * 10].astype(np.float64)
    if len(seg) < SR * 2 or np.sqrt(np.mean(seg ** 2)) < 1e-4:
        return expected_start, 0.0
    ref0 = max(0.0, expected_start - search)
    ref = slice_f32(raw, ref0, expected_start + search + len(seg) / SR).astype(np.float64)
    if len(ref) < len(seg):
        return expected_start, 0.0
    nfft = 1 << int(math.ceil(math.log2(len(ref) + len(seg))))
    corr = np.fft.irfft(np.fft.rfft(ref, nfft) * np.conj(np.fft.rfft(seg, nfft)), nfft)
    corr = corr[: len(ref) - len(seg) + 1]
    c2 = np.concatenate([[0.0], np.cumsum(ref ** 2)])
    energy = np.sqrt(np.maximum(c2[len(seg):] - c2[: len(c2) - len(seg)], 1e-12))
    ncorr = corr / (energy * np.linalg.norm(seg) + 1e-9)
    k = int(np.argmax(ncorr))
    return ref0 + k / SR, float(ncorr[k])
