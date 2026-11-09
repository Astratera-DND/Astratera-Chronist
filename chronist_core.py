"""Astratera Chronist - data layer.

Everything in here only READS the recording folder. Nothing is ever written
next to the recording; the peak cache lives in the user's cache directory.
"""
from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
import re
import struct
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional, Tuple

import numpy as np

BIN_SECONDS = 0.05          # one waveform peak pair per 50 ms
PART_RE = re.compile(r"^(?P<base>.*)_part(?P<num>\d+)\.wav$", re.I)

# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
_EXT = re.compile(r"\.(png|jpe?g|webp|gif|bmp|mp3|wav|ogg|flac|m4a|opus|mp4|webm|json|txt)$", re.I)
_LEAD = re.compile(r"^\s*\d+\s*[_\-\. ]+\s*")


def clean_name(s: str) -> str:
    """'02_Taverne_Zum_Drachen.png' -> 'Taverne Zum Drachen'"""
    s = _EXT.sub("", (s or "").strip())
    stripped = _LEAD.sub("", s)
    if stripped:
        s = stripped
    return re.sub(r"\s+", " ", s.replace("_", " ")).strip()


def fmt_time(t: float, ms: bool = False) -> str:
    t = max(0.0, float(t))
    h = int(t // 3600)
    m = int((t % 3600) // 60)
    s = t % 60
    if ms:
        return f"{h:d}:{m:02d}:{s:06.3f}" if h else f"{m:d}:{s:06.3f}"
    s = int(s)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def cache_dir() -> Path:
    if sys.platform.startswith("win"):
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    d = base / "AstrateraChronist" / "cache"
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return d


def read_json(path: Path):
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _num(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _parse_dt(v) -> Optional[datetime]:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v))
    except ValueError:
        return None


# --------------------------------------------------------------------------
# WAV parts (tolerant of broken headers after a crash)
# --------------------------------------------------------------------------
class WavError(Exception):
    pass


@dataclass
class WavInfo:
    path: Path
    sample_rate: int
    channels: int
    data_offset: int
    frames: int
    repaired: bool = False
    size: int = 0
    mtime_ns: int = 0
    start_frame: int = 0            # position on the global timeline

    @property
    def duration(self) -> float:
        return self.frames / self.sample_rate

    @property
    def start(self) -> float:
        return self.start_frame / self.sample_rate

    @property
    def end(self) -> float:
        return self.start + self.duration

    def samples(self) -> np.ndarray:
        """Memory-mapped int16 samples, shape (frames, channels). Nothing is loaded into RAM."""
        mm = np.memmap(self.path, dtype="<i2", mode="r", offset=self.data_offset,
                       shape=(self.frames * self.channels,))
        return mm.reshape(-1, self.channels)


def parse_wav(path: Path) -> WavInfo:
    st = path.stat()
    size = st.st_size
    fmt = None
    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[:4] not in (b"RIFF", b"RF64") or head[8:12] != b"WAVE":
            raise WavError("not a RIFF/WAVE file")
        pos = 12
        while True:
            f.seek(pos)
            hdr = f.read(8)
            if len(hdr) < 8:
                raise WavError("no data chunk found")
            cid = hdr[:4]
            csz = struct.unpack("<I", hdr[4:])[0]
            body = pos + 8
            if cid == b"fmt ":
                raw = f.read(min(csz, 40))
                if len(raw) < 16:
                    raise WavError("fmt chunk too short")
                tag, ch, sr, _, _, bits = struct.unpack("<HHIIHH", raw[:16])
                if tag == 0xFFFE and len(raw) >= 26:       # WAVE_FORMAT_EXTENSIBLE
                    tag = struct.unpack("<H", raw[24:26])[0]
                fmt = (tag, ch, sr, bits)
            elif cid == b"data":
                if fmt is None:
                    raise WavError("data chunk before fmt chunk")
                tag, ch, sr, bits = fmt
                if tag != 1 or bits != 16:
                    raise WavError(f"only 16-bit PCM is supported (got format {tag}, {bits} bit)")
                if ch < 1 or sr < 1:
                    raise WavError("invalid channel count or sample rate")
                avail = max(0, size - body)
                repaired = csz in (0, 0xFFFFFFFF) or csz > avail
                n = avail if repaired else csz
                frames = n // (2 * ch)
                return WavInfo(path, sr, ch, body, frames, repaired, size, st.st_mtime_ns)
            pos = body + csz + (csz & 1)
            if pos >= size:
                raise WavError("no data chunk found")


# --------------------------------------------------------------------------
# peaks
# --------------------------------------------------------------------------
def samples_per_bin(sample_rate: int) -> int:
    return max(1, int(round(sample_rate * BIN_SECONDS)))


def _cache_file(info: WavInfo, spb: int) -> Path:
    key = f"{info.path.resolve()}|{info.size}|{info.mtime_ns}|{spb}|{info.start_frame % spb}"
    return cache_dir() / (hashlib.sha1(key.encode("utf-8")).hexdigest() + ".npz")


def compute_part_peaks(info: WavInfo, spb: int,
                       progress: Optional[Callable[[float], None]] = None,
                       cancelled: Optional[Callable[[], bool]] = None
                       ) -> Optional[Tuple[int, np.ndarray, np.ndarray]]:
    """Min/max per bin on the GLOBAL bin grid. Returns (first_bin, mins, maxs) or None if cancelled."""
    cf = _cache_file(info, spb)
    if cf.exists():
        try:
            with np.load(cf) as z:
                return int(z["first"]), z["mins"], z["maxs"]
        except Exception:
            pass

    S, N = info.start_frame, info.frames
    first_bin = S // spb
    nb = (S + N - 1) // spb - first_bin + 1
    edges = np.clip((first_bin + np.arange(nb + 1, dtype=np.int64)) * spb - S, 0, N)
    data = info.samples()
    mins = np.empty(nb, np.int16)
    maxs = np.empty(nb, np.int16)
    CH = 4096
    for c0 in range(0, nb, CH):
        if cancelled and cancelled():
            return None
        c1 = min(nb, c0 + CH)
        a, b = int(edges[c0]), int(edges[c1])
        blk = np.asarray(data[a:b])
        if info.channels > 1:
            lo, hi = blk.min(axis=1), blk.max(axis=1)
        else:
            lo = hi = blk[:, 0]
        starts = edges[c0:c1] - a
        mins[c0:c1] = np.minimum.reduceat(lo, starts)
        maxs[c0:c1] = np.maximum.reduceat(hi, starts)
        if progress:
            progress(c1 / nb)
    try:
        np.savez(cf, first=first_bin, mins=mins, maxs=maxs)
    except Exception:
        pass
    return first_bin, mins, maxs


class PeakPyramid:
    """Min/max peaks with power-of-two reductions so any zoom level draws instantly."""

    def __init__(self, mins: np.ndarray, maxs: np.ndarray, bin_seconds: float):
        self.bin_s = bin_seconds
        self.levels: List[Tuple[np.ndarray, np.ndarray]] = [(mins, maxs)]
        while len(self.levels[-1][0]) > 64:
            m, M = self.levels[-1]
            n = len(m) // 2 * 2
            lo = np.minimum(m[0:n:2], m[1:n:2])
            hi = np.maximum(M[0:n:2], M[1:n:2])
            if len(m) > n:
                lo = np.append(lo, m[-1])
                hi = np.append(hi, M[-1])
            self.levels.append((lo, hi))

    def render(self, t0: float, t1: float, npx: int) -> Tuple[np.ndarray, np.ndarray]:
        """Per-pixel (low, high) in [-1, 1] for the time range [t0, t1)."""
        lo_out = np.zeros(npx, np.float32)
        hi_out = np.zeros(npx, np.float32)
        if npx <= 0 or t1 <= t0:
            return lo_out, hi_out
        bpp = (t1 - t0) / npx / self.bin_s
        lvl = int(max(0, min(len(self.levels) - 1, math.floor(math.log2(max(bpp, 1e-9))))))
        scale = self.bin_s * (1 << lvl)
        m, M = self.levels[lvl]
        n = len(m)
        edges = np.floor((t0 + (t1 - t0) * np.arange(npx + 1) / npx) / scale).astype(np.int64)
        starts = edges[:-1]
        valid = (starts >= 0) & (starts < n)
        if not valid.any():
            return lo_out, hi_out
        sv = starts[valid]
        n_end = int(min(n, max(int(edges[-1]), int(sv[-1]) + 1)))
        lo_out[valid] = np.minimum.reduceat(m[:n_end], sv) / 32768.0
        hi_out[valid] = np.maximum.reduceat(M[:n_end], sv) / 32768.0
        return lo_out, hi_out


def merge_part_peaks(parts: List[Tuple[int, np.ndarray, np.ndarray]]) -> Tuple[np.ndarray, np.ndarray]:
    """Combine per-part peaks (global grid) into one array; boundary bins are min/max merged."""
    last = max(fb + len(mn) for fb, mn, _ in parts)
    mins = np.zeros(last, np.int16)
    maxs = np.zeros(last, np.int16)
    for fb, mn, mx in parts:
        sl = slice(fb, fb + len(mn))
        both = (mins[sl] != 0) | (maxs[sl] != 0)
        mins[sl] = np.where(both, np.minimum(mins[sl], mn), mn)
        maxs[sl] = np.where(both, np.maximum(maxs[sl], mx), mx)
    return mins, maxs


# --------------------------------------------------------------------------
# markers, breaks, audio log
# --------------------------------------------------------------------------
KIND_SCENE, KIND_LAYOUT, KIND_MANUAL, KIND_BREAK = "scene", "layout", "manual", "break"


@dataclass
class Marker:
    t: float
    kind: str
    label: str
    note: str = ""
    chunk: str = ""
    beyond: bool = False       # sat beyond the end of the audio (crash / truncation)
    detail: str = ""           # e.g. break length


def _dedupe_points(pts: List[Tuple[float, Optional[datetime]]], gap: float = 3.0):
    pts = sorted(pts, key=lambda p: p[0])
    out: List[Tuple[float, Optional[datetime]]] = []
    for t, dt in pts:
        if out and t - out[-1][0] < gap:
            if out[-1][1] is None and dt is not None:
                out[-1] = (out[-1][0], dt)
            continue
        out.append((t, dt))
    return out


def parse_markers(raw, total: float) -> Tuple[List[Marker], Optional[float]]:
    """Returns (markers sorted by time, recording_end_seconds or None)."""
    markers: List[Marker] = []
    b_start: List[Tuple[float, Optional[datetime]]] = []
    b_end: List[Tuple[float, Optional[datetime]]] = []
    end_t: Optional[float] = None
    if not isinstance(raw, list):
        return [], None
    for r in raw:
        if not isinstance(r, dict):
            continue
        ev = str(r.get("event") or "").upper()
        note = str(r.get("note") or "").strip()
        nl = note.lower()
        t = _num(r.get("elapsed_seconds"))
        dt = _parse_dt(r.get("datetime"))
        chunk = str(r.get("chunk") or "")
        if ev == "RECORDING_START":
            continue
        if ev == "RECORDING_END":
            end_t = t
            continue
        if ev == "BREAK_START" or "break start" in nl:
            b_start.append((t, dt))
            continue
        if ev == "BREAK_END" or "break end" in nl:
            b_end.append((t, dt))
            continue
        if nl.startswith("scene:"):
            kind, label = KIND_SCENE, clean_name(note.split(":", 1)[1])
        elif nl.startswith("layout:"):
            kind, label = KIND_LAYOUT, clean_name(note.split(":", 1)[1])
        else:
            kind, label = KIND_MANUAL, note or "Marker"
        markers.append(Marker(t, kind, label or note, note, chunk))

    starts, ends = _dedupe_points(b_start), _dedupe_points(b_end)
    used = set()
    for st, sdt in starts:
        match = None
        for i, (et, edt) in enumerate(ends):
            if i not in used and et >= st - 0.5:
                match = i
                break
        detail = ""
        if match is not None:
            used.add(match)
            edt = ends[match][1]
            if sdt and edt:
                secs = (edt - sdt).total_seconds()
                if secs >= 0:
                    detail = fmt_time(secs)
        else:
            detail = "unfinished"
        markers.append(Marker(st, KIND_BREAK, f"Break {detail}".strip() if detail != "unfinished" else "Break (unfinished)",
                              "break", "", False, detail))

    for m in markers:
        if m.t < 0:
            m.t = 0.0
        if m.t > total + 0.5:
            m.beyond = True
            m.t = total
    markers.sort(key=lambda m: m.t)
    return markers, end_t


@dataclass
class AudioEvent:
    t: float
    kind: str                  # music | sfx | quickfire
    name: str
    raw_name: str = ""
    folder: str = ""
    scene: str = ""
    paused: bool = False
    note: str = ""


def parse_audio_log(raw) -> List[AudioEvent]:
    out: List[AudioEvent] = []
    if not isinstance(raw, list):
        return out
    for r in raw:
        if not isinstance(r, dict):
            continue
        kind = str(r.get("kind") or "").lower()
        if kind not in ("music", "sfx", "quickfire"):
            continue
        raw_name = str(r.get("name") or "")
        out.append(AudioEvent(max(0.0, _num(r.get("elapsed_seconds"))), kind, clean_name(raw_name),
                              raw_name, str(r.get("folder") or ""), clean_name(str(r.get("scene") or "")),
                              bool(r.get("recording_paused")), str(r.get("note") or "")))
    out.sort(key=lambda e: e.t)
    return out


class AudioTimeline:
    """Answers 'what was playing at time t'."""

    def __init__(self, events: List[AudioEvent]):
        self.events = events
        self.music = [e for e in events if e.kind == "music" and not e.paused]
        self.music_t = [e.t for e in self.music]
        self.oneshots = [e for e in events if e.kind in ("sfx", "quickfire") and not e.paused]
        self.oneshot_t = [e.t for e in self.oneshots]
        self.paused_count = sum(1 for e in events if e.paused)

    def music_at(self, t: float) -> Optional[AudioEvent]:
        i = bisect.bisect_right(self.music_t, t + 1e-6) - 1
        return self.music[i] if i >= 0 else None

    def music_end(self, ev: AudioEvent, total: float) -> float:
        i = self.music.index(ev)
        return self.music[i + 1].t if i + 1 < len(self.music) else total

    def oneshot_at(self, t: float) -> Optional[AudioEvent]:
        i = bisect.bisect_right(self.oneshot_t, t + 1e-6) - 1
        return self.oneshots[i] if i >= 0 else None


# --------------------------------------------------------------------------
# transcript
# --------------------------------------------------------------------------
@dataclass
class Segment:
    start: float
    end: float
    text: str
    speaker: str = ""


@dataclass
class Transcript:
    path: Path
    segments: List[Segment]
    speakers: List[dict] = field(default_factory=list)
    parts: List[dict] = field(default_factory=list)
    language: str = ""
    duration: float = 0.0
    fmt: str = "json"
    has_meta: bool = True        # only .json carries the part list and the total length

    def __post_init__(self):
        self.starts = [s.start for s in self.segments]

    def index_at(self, t: float) -> int:
        return bisect.bisect_right(self.starts, t) - 1


TRANSCRIPT_EXTS = (".json", ".srt", ".vtt", ".txt")     # preference order if one transcript exists in several formats

_TS = r"(?:(\d{1,3}):)?(\d{1,2}):(\d{2})(?:[.,](\d{1,3}))?"
_TS_RE = re.compile(_TS)
_SEP = r"(?:-->|->|\u2013|\u2014|-)"
_LINE_TS_RE = re.compile(rf"^\s*[\[(]?\s*(?P<a>{_TS})\s*(?:{_SEP}\s*(?P<b>{_TS}))?\s*[\])]?\s*[:\u2013\u2014-]?\s*(?P<text>.*)$")
_LINE_SEC_RE = re.compile(rf"^\s*[\[(]\s*(?P<a>\d+(?:\.\d+)?)\s*s?\s*(?:{_SEP}\s*(?P<b>\d+(?:\.\d+)?)\s*s?)?\s*[\])]\s*(?P<text>.*)$")
_SPK_A = re.compile(r"^\[?([^\W\d_][\w .\-']{0,23}?)\]?\s*:\s+(\S.*)$", re.S)     # "Anna: text"  /  "[Anna]: text"
_SPK_B = re.compile(r"^\[([^\]]{1,24})\]\s+(\S.*)$", re.S)                           # "[Anna] text"


def _tok_seconds(t) -> float:
    h, m, s, f = t
    return int(h or 0) * 3600 + int(m) * 60 + int(s) + (float("0." + f) if f else 0.0)


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace")


def _parse_cues(text: str) -> List[Segment]:
    """SRT and VTT: '00:00:01,000 --> 00:00:04,000' followed by the text lines."""
    import html
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    segs: List[Segment] = []
    i = 0
    while i < len(lines):
        if "-->" in lines[i]:
            toks = _TS_RE.findall(lines[i])
            if len(toks) >= 2:
                a, b = _tok_seconds(toks[0]), _tok_seconds(toks[1])
                body = []
                i += 1
                while i < len(lines) and lines[i].strip():
                    body.append(lines[i].strip())
                    i += 1
                txt = " ".join(body)
                m = re.search(r"<v(?:\.[^\s>]*)?\s+([^>]+)>", txt)           # WebVTT voice tag
                spk = m.group(1).strip() if m else ""
                txt = html.unescape(re.sub(r"<[^>]+>", "", txt)).strip()
                if txt:
                    segs.append(Segment(a, b, txt, spk))
                continue
        i += 1
    return segs


def _parse_txt(text: str) -> List[Segment]:
    """Plain text with a time in front of each passage: '[00:12:05] Anna: ...', '00:12 text', '[725.3s] text' ..."""
    segs: List[Segment] = []
    glue = False                                    # a line right after a passage continues it
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not line.strip():
            glue = False
            continue
        m = _LINE_TS_RE.match(line)
        if m:
            a = _tok_seconds(_TS_RE.fullmatch(m.group("a")).groups())
            b = _tok_seconds(_TS_RE.fullmatch(m.group("b")).groups()) if m.group("b") else None
            segs.append(Segment(a, b if b is not None else -1.0, m.group("text").strip()))
            glue = True
            continue
        m = _LINE_SEC_RE.match(line)
        if m:
            segs.append(Segment(float(m.group("a")), float(m.group("b")) if m.group("b") else -1.0, m.group("text").strip()))
            glue = True
            continue
        if glue and segs:
            segs[-1].text = (segs[-1].text + " " + line.strip()).strip()
    for i, s in enumerate(segs):                    # passages without an end time end where the next begins
        if s.end < 0:
            nxt = segs[i + 1].start if i + 1 < len(segs) else s.start + 4.0
            s.end = max(s.start, min(nxt, s.start + 15.0))
    return [s for s in segs if s.text]


def _split_speaker(text: str):
    for rx in (_SPK_A, _SPK_B):
        m = rx.match(text)
        if m:
            return m.group(1).strip(), m.group(2).strip()
    return None


def _extract_speakers(segs: List[Segment]) -> None:
    """Turn 'Anna: text' prefixes into speaker labels - but only for names that really repeat."""
    seen: dict = {}
    for s in segs:
        r = None if s.speaker else _split_speaker(s.text)
        if r:
            seen[r[0]] = seen.get(r[0], 0) + 1
    keep = {k for k, n in seen.items() if n >= 3 or k.lower().startswith(("speaker", "unknown"))}
    for s in segs:
        r = None if s.speaker else _split_speaker(s.text)
        if r and r[0] in keep:
            s.speaker, s.text = r


def _speaker_stats(segs: List[Segment]) -> List[dict]:
    acc: dict = {}
    for s in segs:
        if s.speaker:
            d = acc.setdefault(s.speaker, {"name": s.speaker, "seconds": 0.0, "passages": 0})
            d["seconds"] += max(0.0, s.end - s.start)
            d["passages"] += 1
    return sorted(acc.values(), key=lambda d: -d["seconds"])


def load_transcript(path: Path) -> Transcript:
    """Reads .json (full information), .srt, .vtt or .txt (needs times in front of the passages)."""
    ext = path.suffix.lower()
    if ext == ".json":
        d = read_json(path)
        raw = d if isinstance(d, list) else d.get("segments", [])
        meta = {} if isinstance(d, list) else d
        segs = []
        for s in raw:
            try:
                segs.append(Segment(float(s["start"]), float(s.get("end", s["start"])),
                                    str(s.get("text", "")).strip(), str(s.get("speaker") or "")))
            except (KeyError, TypeError, ValueError):
                continue
        segs.sort(key=lambda s: s.start)
        speakers = meta.get("speakers") or _speaker_stats(segs)
        return Transcript(path, segs, speakers, meta.get("parts") or [], str(meta.get("language") or ""),
                          _num(meta.get("duration")), "json", bool(meta))
    text = _read_text(path)
    segs = _parse_cues(text) if "-->" in text else []
    if not segs and ext == ".txt":
        segs = _parse_txt(text)
    elif not segs:
        segs = _parse_txt(text)
    if not segs:
        raise ValueError("no timestamps found - without times the text cannot be placed on the timeline")
    segs.sort(key=lambda s: s.start)
    _extract_speakers(segs)
    return Transcript(path, segs, _speaker_stats(segs), [], "", max(s.end for s in segs), ext.lstrip("."), False)


@dataclass
class TranscriptCandidate:
    """One transcript; may exist in several formats (the Transcribe window writes json, txt, srt and vtt)."""
    stem: str
    files: List[Path]               # best format first
    exact: bool = False             # name matches '<session folder>_transcript' / '<base>_transcript'

    @property
    def best(self) -> Path:
        return self.files[0]

    @property
    def label(self) -> str:
        return f"{self.stem}   [{', '.join(f.suffix.lower().lstrip('.') for f in self.files)}]"


def find_transcripts(root: Path, base: str) -> List[TranscriptCandidate]:
    """Looks in <session>/transcript first (that is where they are saved); falls back to the session folder
    itself and to a 'transcript' folder next to it."""
    expected = {f"{root.name}_transcript".lower(), f"{base}_transcript".lower()}

    def collect(folder: Path, need_name: bool) -> dict:
        groups: dict = {}
        if not folder.is_dir():
            return groups
        files = [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in TRANSCRIPT_EXTS]
        named = [p for p in files if "transcript" in p.stem.lower()]
        for p in (named if (named or need_name) else files):
            groups.setdefault(p.stem.lower(), []).append(p)
        return groups

    groups = collect(root / "transcript", False) or collect(root, True) or collect(root.parent / "transcript", True)
    out: List[TranscriptCandidate] = []
    for key, files in groups.items():
        files.sort(key=lambda p: TRANSCRIPT_EXTS.index(p.suffix.lower()))
        out.append(TranscriptCandidate(files[0].stem, files, key in expected))
    out.sort(key=lambda c: (not c.exact, c.stem.lower()))
    return out


def choose_default(cands: List[TranscriptCandidate]) -> Optional[TranscriptCandidate]:
    """The transcript to open without asking, or None if the user has to choose."""
    if len(cands) == 1:
        return cands[0]
    exact = [c for c in cands if c.exact]
    return exact[0] if len(exact) == 1 else None


def load_transcript_group(c: TranscriptCandidate) -> Tuple[Optional[Transcript], List[str]]:
    """Opens the best readable format of a transcript. Returns (transcript or None, problems met on the way)."""
    problems: List[str] = []
    for f in c.files:
        try:
            return load_transcript(f), problems
        except Exception as e:                       # noqa: BLE001 - reported to the user
            problems.append(f"{f.name}: {e}")
    return None, problems


# --------------------------------------------------------------------------
# session
# --------------------------------------------------------------------------
@dataclass
class Session:
    root: Path
    base: str
    parts: List[WavInfo]
    sample_rate: int
    channels: int
    duration: float
    markers: List[Marker]
    audio: AudioTimeline
    recording_end: Optional[float]
    transcript_candidates: List[TranscriptCandidate]
    markers_path: Optional[Path] = None
    audio_log_path: Optional[Path] = None
    warnings: List[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.root.name


def _pick(cands: List[Path], base: str, suffix: str) -> List[Path]:
    exact = [c for c in cands if c.name.lower() == f"{base}{suffix}".lower()]
    return exact + [c for c in sorted(cands) if c not in exact]


def load_session(root: Path) -> Session:
    root = Path(root)
    if not root.is_dir():
        raise WavError(f"Folder not found: {root}")
    found = []
    for p in root.iterdir():
        m = PART_RE.match(p.name)
        if p.is_file() and m:
            found.append((int(m.group("num")), m.group("base"), p))
    if not found:
        raise WavError("No *_partNNN.wav files in this folder.")
    base = max(set(b for _, b, _ in found), key=lambda b: sum(1 for x in found if x[1] == b))
    found = sorted((n, p) for n, b, p in found if b == base)

    warnings: List[str] = []
    nums = [n for n, _ in found]
    if nums != list(range(nums[0], nums[0] + len(nums))) or nums[0] != 0:
        warnings.append(f"Part numbering is not continuous from 000 (found {', '.join(f'{n:03d}' for n in nums)}).")

    parts: List[WavInfo] = []
    cursor = 0
    for _, p in found:
        try:
            info = parse_wav(p)
        except (WavError, OSError) as e:
            warnings.append(f"{p.name}: skipped ({e})")
            continue
        if info.frames == 0:
            warnings.append(f"{p.name}: contains no audio, skipped")
            continue
        if parts and (info.sample_rate != parts[0].sample_rate or info.channels != parts[0].channels):
            warnings.append(f"{p.name}: different sample rate/channels, skipped")
            continue
        if info.repaired:
            warnings.append(f"{p.name}: header was damaged (crash?), length recovered from file size")
        info.start_frame = cursor
        cursor += info.frames
        parts.append(info)
    if not parts:
        raise WavError("None of the audio parts could be read.")
    sr, ch = parts[0].sample_rate, parts[0].channels
    duration = cursor / sr

    def first_json(suffix: str) -> Optional[Path]:
        c = [p for p in root.glob(f"*{suffix}") if p.is_file()]
        return _pick(c, base, suffix)[0] if c else None

    mpath, apath = first_json("_markers.json"), first_json("_audio_log.json")
    markers, rec_end = [], None
    if mpath:
        try:
            markers, rec_end = parse_markers(read_json(mpath), duration)
        except Exception as e:
            warnings.append(f"Could not read {mpath.name}: {e}")
    else:
        warnings.append("No markers file (*_markers.json) found.")
    events: List[AudioEvent] = []
    if apath:
        try:
            events = parse_audio_log(read_json(apath))
        except Exception as e:
            warnings.append(f"Could not read {apath.name}: {e}")
    else:
        warnings.append("No audio log (*_audio_log.json) found.")
    if rec_end is None and mpath:
        warnings.append("RECORDING_END is missing - the session probably ended in a crash.")
    if any(m.beyond for m in markers):
        warnings.append("Some markers lie beyond the end of the audio and were clamped to the end.")

    return Session(root, base, parts, sr, ch, duration, markers, AudioTimeline(events), rec_end,
                   find_transcripts(root, base), mpath, apath, warnings)


def check_transcript_alignment(tr: Transcript, session: Session) -> Optional[str]:
    """Warn if the transcript was probably not made from exactly these parts."""
    if tr.parts:
        names = [p.get("file") for p in tr.parts]
        mine = [p.path.name for p in session.parts]
        if names != mine:
            return (f"Transcript was made from {len(names)} part(s) ({', '.join(map(str, names[:3]))}"
                    f"{'...' if len(names) > 3 else ''}) but the folder has {len(mine)}. "
                    "Times may be offset - use the offset field if text and audio do not match.")
        for tp, sp in zip(tr.parts, session.parts):
            if abs(_num(tp.get("duration")) - sp.duration) > 1.0:
                return f"Length of {sp.path.name} differs from the transcript's record. Times may be offset."
    elif tr.has_meta and tr.duration:
        if abs(tr.duration - session.duration) > 2.0:
            return "Transcript length differs from the audio length. Times may be offset."
    elif tr.segments and tr.segments[-1].end > session.duration + 2.0:
        return "The transcript runs longer than the audio. Times may be offset."
    return None
