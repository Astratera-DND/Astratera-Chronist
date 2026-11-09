"""Astratera Chronist - playback engine.

All WAV parts are played as ONE continuous stream (no gap at part boundaries).
Audio is read straight from memory-mapped files, so a 4 hour session does not
need to be decoded into RAM. Speed changes use WSOLA time-stretching, so the
voices keep their pitch at 1.5x or 2x.
"""
from __future__ import annotations

import bisect
import os
import threading
from typing import List, Optional

import numpy as np
from PyQt6.QtCore import QElapsedTimer, QIODevice, QObject, QTimer, pyqtSignal
from PyQt6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

from chronist_core import WavInfo


# --------------------------------------------------------------------------
# random access over all parts
# --------------------------------------------------------------------------
class PartSource:
    def __init__(self, parts: List[WavInfo]):
        self.parts = parts
        self.channels = parts[0].channels
        self.starts = [p.start_frame for p in parts]
        self.total = parts[-1].start_frame + parts[-1].frames
        self._maps: List[Optional[np.ndarray]] = [None] * len(parts)

    def _map(self, i: int) -> np.ndarray:
        if self._maps[i] is None:
            self._maps[i] = self.parts[i].samples()
        return self._maps[i]

    def read(self, start: int, n: int) -> np.ndarray:
        """int16 frames [start, start+n), zero padded outside the recording."""
        out = np.zeros((max(n, 0), self.channels), np.int16)
        if n <= 0:
            return out
        end = start + n
        s0 = max(start, 0)
        i = max(bisect.bisect_right(self.starts, s0) - 1, 0)
        while i < len(self.parts) and self.starts[i] < end:
            p = self.parts[i]
            a, b = max(s0, p.start_frame), min(end, p.start_frame + p.frames)
            if b > a:
                out[a - start:b - start] = self._map(i)[a - p.start_frame:b - p.start_frame]
            i += 1
        return out

    def close(self):
        self._maps = [None] * len(self.parts)


# --------------------------------------------------------------------------
# WSOLA time stretcher (pitch preserving speed change)
# --------------------------------------------------------------------------
class Stretcher:
    FADE = 256      # samples of fade-in after every seek (avoids clicks)

    def __init__(self, src: PartSource, sample_rate: int, rate: float = 1.0):
        self.src = src
        self.ch = src.channels
        self.N = int(sample_rate * 0.046) // 2 * 2         # ~46 ms frames
        self.Hs = self.N // 2
        self.tol = int(sample_rate * 0.012)                # +-12 ms search window
        self.win = (0.5 - 0.5 * np.cos(2 * np.pi * np.arange(self.N) / self.N)).astype(np.float32)[:, None]
        self.rate = float(rate)
        self.reset(0)

    def set_rate(self, rate: float):
        self.rate = float(rate)

    def reset(self, pos_frame: int):
        self.pos = float(pos_frame)
        self.ola = np.zeros((self.N, self.ch), np.float32)
        self.prev_start: Optional[int] = None
        self.fifo = np.zeros((0, self.ch), np.float32)
        self.fade = self.FADE

    def _mono(self, x: np.ndarray) -> np.ndarray:
        return x.astype(np.float32).mean(axis=1) if self.ch > 1 else x[:, 0].astype(np.float32)

    def _step(self) -> np.ndarray:
        a = int(round(self.pos))
        if self.prev_start is None:
            s = a
        else:
            target = self._mono(self.src.read(self.prev_start + self.Hs, self.Hs))
            region = self._mono(self.src.read(a - self.tol, self.Hs + 2 * self.tol))
            corr = np.correlate(region, target, "valid")
            s = a + int(np.argmax(corr)) - self.tol
        frame = self.src.read(s, self.N).astype(np.float32) * self.win
        self.ola += frame
        out = self.ola[:self.Hs].copy()
        self.ola[:self.Hs] = self.ola[self.Hs:]
        self.ola[self.Hs:] = 0
        self.prev_start = s
        self.pos += self.Hs * self.rate
        return out

    def read(self, n: int) -> np.ndarray:
        """Next n output frames as int16 (n, channels)."""
        if abs(self.rate - 1.0) < 1e-3:
            out = self.src.read(int(round(self.pos)), n)
            self.pos += n
            self.prev_start = None
            if self.fade > 0:
                k = min(self.fade, n)
                first = self.FADE - self.fade
                ramp = np.linspace(0, 1, self.FADE, dtype=np.float32)[first:first + k][:, None]
                out[:k] = (out[:k].astype(np.float32) * ramp).astype(np.int16)
                self.fade -= k
            return out
        self.fade = 0
        while len(self.fifo) < n:
            self.fifo = np.concatenate([self.fifo, self._step()])
        out, self.fifo = self.fifo[:n], self.fifo[n:]
        return np.clip(out, -32768, 32767).astype(np.int16)


# --------------------------------------------------------------------------
# Qt glue
# --------------------------------------------------------------------------
class _PullDevice(QIODevice):
    """The sink pulls audio from here."""

    def __init__(self, engine: "AudioEngine"):
        super().__init__()
        self._engine = engine

    def isSequential(self) -> bool:
        return True

    def bytesAvailable(self) -> int:
        return (1 << 16) + super().bytesAvailable()

    def readData(self, maxlen: int) -> bytes:
        return self._engine._produce(maxlen)

    def writeData(self, data) -> int:
        return 0


class AudioEngine(QObject):
    positionChanged = pyqtSignal(float)
    playingChanged = pyqtSignal(bool)
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._source: Optional[PartSource] = None
        self._stretch: Optional[Stretcher] = None
        self._sink: Optional[QAudioSink] = None
        self._io: Optional[_PullDevice] = None
        self._sr = 44100
        self._src_ch = 1
        self._out_ch = 1
        self._dur = 0.0
        self._rate = 1.0
        self._volume = 0.8
        self._playing = False
        self._base = 0.0
        self._clock = QElapsedTimer()
        self._tick = QTimer(self)
        self._tick.setInterval(25)
        self._tick.timeout.connect(self._on_tick)
        # CHRONIST_NO_AUDIO=1 turns the sound output off (headless testing, broken audio drivers)
        self.audio_enabled = os.environ.get("CHRONIST_NO_AUDIO", "") not in ("1", "true", "yes")
        self._devices = None
        if self.audio_enabled:
            self._devices = QMediaDevices(self)
            self._devices.audioOutputsChanged.connect(self._on_devices_changed)

    # ---- public ---------------------------------------------------------
    @property
    def loaded(self) -> bool:
        return self._source is not None

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def duration(self) -> float:
        return self._dur

    @property
    def rate(self) -> float:
        return self._rate

    def load(self, parts: List[WavInfo], sample_rate: int):
        self.unload()
        self._source = PartSource(parts)
        self._sr = sample_rate
        self._src_ch = self._source.channels
        self._out_ch = self._src_ch
        self._dur = self._source.total / sample_rate
        self._stretch = Stretcher(self._source, sample_rate, self._rate)
        self._base = 0.0
        self.positionChanged.emit(0.0)

    def unload(self):
        self._halt_sink()
        if self._playing:
            self._playing = False
            self.playingChanged.emit(False)
        self._tick.stop()
        if self._source:
            self._source.close()
        self._source = self._stretch = None
        self._dur = 0.0
        self._base = 0.0

    def position(self) -> float:
        if not self._playing or self._sink is None:
            return self._base
        us = self._sink.processedUSecs()
        if us <= 0 and self._clock.elapsed() > 500:      # backend does not report: wall clock
            t = self._clock.elapsed() / 1000.0
        else:
            t = us / 1e6
        return min(self._dur, self._base + t * self._rate)

    def play(self):
        if not self.loaded or self._playing:
            return
        if self._base >= self._dur - 0.05:
            self._base = 0.0
            with self._lock:
                self._stretch.reset(0)
        if not self._begin_sink():
            return
        self._playing = True
        self._tick.start()
        self.playingChanged.emit(True)

    def pause(self):
        if not self._playing:
            return
        self._base = self.position()
        self._halt_sink()
        self._playing = False
        self._tick.stop()
        self.playingChanged.emit(False)
        self.positionChanged.emit(self._base)

    def toggle(self):
        self.pause() if self._playing else self.play()

    def seek(self, t: float):
        if not self.loaded:
            return
        t = max(0.0, min(self._dur, float(t)))
        was = self._playing
        self._halt_sink()
        self._base = t
        with self._lock:
            self._stretch.reset(int(round(t * self._sr)))
        if was and not self._begin_sink():
            self._playing = False
            self._tick.stop()
            self.playingChanged.emit(False)
        self.positionChanged.emit(t)

    def set_rate(self, rate: float):
        rate = max(0.5, min(3.0, float(rate)))
        if not self.loaded:
            self._rate = rate
            return
        pos = self.position()
        was = self._playing
        self._halt_sink()
        self._rate = rate
        self._base = pos
        with self._lock:
            self._stretch.set_rate(rate)
            self._stretch.reset(int(round(pos * self._sr)))
        if was:
            self._begin_sink()

    def set_volume(self, v: float):
        self._volume = max(0.0, min(1.0, float(v)))
        if self._sink is not None:
            self._sink.setVolume(self._volume)

    # ---- internals --------------------------------------------------------
    def _produce(self, maxlen: int) -> bytes:
        with self._lock:
            st = self._stretch
            if st is None:
                return bytes(maxlen)
            frames = maxlen // (2 * self._out_ch)
            if frames <= 0:
                return b""
            arr = st.read(frames)
            if self._out_ch != self._src_ch:
                arr = np.repeat(arr[:, :1], self._out_ch, axis=1)
            return np.ascontiguousarray(arr).tobytes()

    def _pick_format(self, device) -> Optional[QAudioFormat]:
        for ch in dict.fromkeys((self._src_ch, 2)):
            fmt = QAudioFormat()
            fmt.setSampleRate(self._sr)
            fmt.setChannelCount(ch)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            if device.isFormatSupported(fmt):
                self._out_ch = ch
                return fmt
        return None

    def _begin_sink(self) -> bool:
        if not self.audio_enabled:
            self.error.emit("Sound output is switched off (CHRONIST_NO_AUDIO).")
            return False
        device = QMediaDevices.defaultAudioOutput()
        if device.isNull():
            self.error.emit("No audio output device found.")
            return False
        fmt = self._pick_format(device)
        if fmt is None:
            self.error.emit("The audio device does not accept 44.1 kHz 16-bit audio.")
            return False
        sink = QAudioSink(device, fmt, self)
        sink.setBufferSize(int(self._sr * self._out_ch * 2 * 0.12))      # ~120 ms latency
        sink.setVolume(self._volume)
        io = _PullDevice(self)
        io.open(QIODevice.OpenModeFlag.ReadOnly)
        self._sink, self._io = sink, io
        self._clock.restart()
        sink.start(io)
        return True

    def _halt_sink(self):
        sink, io = self._sink, self._io
        self._sink = self._io = None
        if sink is not None:
            sink.stop()
            sink.deleteLater()
        if io is not None:
            io.close()

    def _on_tick(self):
        pos = self.position()
        if pos >= self._dur - 1e-3:
            self._halt_sink()
            self._base = self._dur
            self._playing = False
            self._tick.stop()
            self.playingChanged.emit(False)
            self.positionChanged.emit(self._dur)
            self.finished.emit()
            return
        self.positionChanged.emit(pos)

    def _on_devices_changed(self):
        if self._playing:
            self.seek(self.position())
