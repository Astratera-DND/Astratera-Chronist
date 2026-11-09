"""Creates a small fake session (synthetic audio) to try Astratera Chronist without a real recording.

    python tests/make_test_session.py  [output_folder]

Part 2 gets a damaged WAV header and the markers contain a mark beyond the end of
the audio, so the "crash" handling can be seen as well.
"""
import json
import math
import struct
import sys
import wave
from pathlib import Path

SR = 44100


def tone_part(path: Path, seconds: float, seed: int, damage: bool = False):
    n = int(seconds * SR)
    frames = bytearray()
    for i in range(n):
        t = i / SR
        env = 0.5 + 0.5 * math.sin(2 * math.pi * (0.25 + 0.07 * seed) * t)          # slow "speech" swell
        gate = 1.0 if (t * 1.7 + seed) % 3 < 2.2 else 0.05                           # pauses
        v = env * gate * (0.45 * math.sin(2 * math.pi * (140 + 30 * seed) * t)
                          + 0.2 * math.sin(2 * math.pi * 520 * t))
        frames += struct.pack("<h", int(v * 28000))
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(bytes(frames))
    if damage:                          # simulate a crash: header says length 0
        raw = bytearray(path.read_bytes())
        raw[4:8] = struct.pack("<I", 0)
        raw[40:44] = struct.pack("<I", 0)
        path.write_bytes(bytes(raw))


def main(out: Path):
    sess = out / "2026-10-22_session"
    sess.mkdir(parents=True, exist_ok=True)
    tdir = sess / "transcript"                      # transcripts live inside the session folder
    tdir.mkdir(exist_ok=True)
    lens = [40.013, 40.0, 25.5]
    start = 0.0
    parts = []
    for i, L in enumerate(lens):
        name = f"session_part{i:03d}.wav"
        tone_part(sess / name, L, i + 1, damage=(i == 2))
        parts.append({"file": name, "start": round(start, 3), "duration": L})
        start += L
    total = start

    def m(t, note="", chunk="session_part000.wav", **kw):
        d = {"timecode": "00:00:00", "elapsed_seconds": t, "chunk": chunk, "note": note}
        d.update(kw)
        return d

    markers = [
        {"event": "RECORDING_START", "datetime": "2026-10-22T19:00:00", "timecode": "00:00:00",
         "elapsed_seconds": 0, "chunk": "", "note": "Recording started"},
        m(1.0, "Scene: 01_Taverne_Zum_Drachen.png"),
        m(2.0, "Layout: Karte"),
        m(9.5, ""),
        m(14.0, "Scene: 02_Der_Markt"),
        m(15.0, "Layout: Charaktere"),
        m(18.0, "Dragon appears!"),
        m(19.0, "Layout: Karte"),
        m(21.0, "Layout: Charaktere"),
        m(22.0, "Layout: Karte"),
        m(23.0, "Scene: 03_Die_Kanalisation"),
        m(24.0, "Wichtig: Hinweis"),
        m(55.0, "--- BREAK START ---"),
        {"event": "BREAK_START", "datetime": "2026-10-22T19:30:00", "timecode": "00:00:55",
         "elapsed_seconds": 55.0, "chunk": "session_part001.wav", "note": "Break started"},
        m(55.0, "--- BREAK END ---"),
        {"event": "BREAK_END", "datetime": "2026-10-22T19:42:10", "timecode": "00:00:55",
         "elapsed_seconds": 55.0, "chunk": "session_part001.wav", "note": "Break ended"},
        m(70.0, "Scene: 04_Der_Thronsaal", "session_part001.wav"),
        m(90.0, "Layout: Karte", "session_part002.wav"),
        m(total + 30, "Mark after the crash", "session_part002.wav"),
    ]
    log = [
        {"kind": "music", "name": "01_Dark_Theme.mp3", "folder": "Ambient", "elapsed_seconds": 0.0,
         "scene": "", "recording_paused": False, "note": "already playing at recording start"},
        {"kind": "sfx", "name": "Door_Creak.wav", "folder": "Doors", "elapsed_seconds": 8.0,
         "scene": "01_Taverne_Zum_Drachen", "recording_paused": False},
        {"kind": "music", "name": "02_Combat_Drums.mp3", "folder": "Combat", "elapsed_seconds": 30.0,
         "scene": "02_Der_Markt", "recording_paused": False},
        {"kind": "quickfire", "name": "Sword_Hit.wav", "folder": "Quick Fire", "elapsed_seconds": 33.0,
         "scene": "02_Der_Markt", "recording_paused": False},
        {"kind": "music", "name": "Break_Lounge.mp3", "folder": "Break", "elapsed_seconds": 55.0,
         "scene": "", "recording_paused": True},
        {"kind": "music", "name": "03_Throne_Room.mp3", "folder": "Ambient", "elapsed_seconds": 70.5,
         "scene": "04_Der_Thronsaal", "recording_paused": False},
    ]
    words = ["Welcome back to Silverfen.", "The tavern is loud tonight.", "You see a dragon in the market square!",
             "Roll for initiative.", "The sewers smell terrible.", "The king looks at you in silence."]
    names = ["DM", "Anna", "Ben", "Unknown"]
    segs, t, i = [], 3.0, 0
    while t < total - 4:
        segs.append({"start": round(t, 2), "end": round(t + 3.5, 2), "text": words[i % len(words)],
                     "speaker": names[i % len(names)]})
        t += 4.2
        i += 1
    transcript = {"recording": "session", "language": "en", "model": "test", "parts": parts, "duration": total,
                  "segments": segs, "events": [], "corrections": [],
                  "speakers": [{"name": n, "seconds": 20.0, "passages": 5} for n in names]}
    (sess / "session_markers.json").write_text(json.dumps(markers, indent=1), encoding="utf-8")
    (sess / "session_audio_log.json").write_text(json.dumps(log, indent=1), encoding="utf-8")
    name = f"{sess.name}_transcript"                # e.g. 2026-10-22_session_transcript.json / .srt / .vtt / .txt
    (tdir / f"{name}.json").write_text(json.dumps(transcript, indent=1), encoding="utf-8")

    def ts(t, sep):
        h, rem = divmod(t, 3600)
        m, sec = divmod(rem, 60)
        return f"{int(h):02d}:{int(m):02d}:{int(sec):02d}{sep}{int(round((sec % 1) * 1000)):03d}"

    srt, vtt, txt = [], ["WEBVTT", ""], []
    for k, sg in enumerate(segs, 1):
        srt += [str(k), f"{ts(sg['start'], ',')} --> {ts(sg['end'], ',')}", f"{sg['speaker']}: {sg['text']}", ""]
        vtt += [f"{ts(sg['start'], '.')} --> {ts(sg['end'], '.')}", f"<v {sg['speaker']}>{sg['text']}", ""]
        txt.append(f"[{ts(sg['start'], '.')[:-4]}] {sg['speaker']}: {sg['text']}")
    (tdir / f"{name}.srt").write_text("\n".join(srt), encoding="utf-8")
    (tdir / f"{name}.vtt").write_text("\n".join(vtt), encoding="utf-8")
    (tdir / f"{name}.txt").write_text("\n".join(txt), encoding="utf-8")
    print(f"Test session written to {sess}  ({total:.1f} s of audio)")


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "test_recordings"))
