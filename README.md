# Astratera Chronist

Review a recorded D&D session on **one timeline**: the waveform of all audio parts, the chapter markers
from the Weltentor recorder, the music / SFX that was running, and the transcript. Click anywhere to play from there.

Everything is local and offline. The viewer **only reads** your recording folder, it never changes it
(the waveform cache lives in `%LOCALAPPDATA%\AstrateraChronist\cache`).

## What it does

- 🎧 **One continuous player** – all `_partNNN.wav` files are joined into one waveform and played without a gap at the part borders. Play / pause, rewind / forward, speed 0.75x – 2x (voices keep their pitch), volume, time display, draggable playhead.
- 🌊 **Zoomable waveform** – mouse wheel zooms, Shift+wheel or right-drag scrolls, the overview strip on top shows the whole session. The view can follow the playhead.
- 🚩 **Chapter markers on the waveform** – three colour coded lanes: **Scenes** (yellow), **Layouts** (blue), **Manual marks** (red); breaks are violet. Nothing is hidden: markers that are too close together collapse into a count badge (click to zoom in), labels appear where there is room, zooming in reveals the rest.
- 📋 **Jump to marker** – a searchable menu with every marker, colour coded and **sorted by time** (switch on *Group by type* for scenes / layouts / marks / breaks in separate blocks; the choice is remembered). The chips filter by type. Click a marker and the playhead jumps there. Previous / next chapter buttons skip through the types you tick under *Skip to*.
- 🧩 **Part indicators** – under the waveform you see where each audio part starts and ends.
- 🎵 **Now playing** – below that: current scene and layout, the music track that is running at the playhead (with when it started and when the next one comes) and the last SFX / Quick Fire.
- 📝 **Transcript** – found automatically (see below), coloured by speaker. The passage that is being said is marked and the list scrolls along (*Follow playback*). Search shows only matching passages, **Enter** plays from the next match. Click any passage to jump there. A transcript that does not match the audio parts gets a warning and an offset field.

## Using it

1. Start the program, paste the path of a session folder (e.g. `…\Recordings\2026-10-22_session`) or press **Browse**. Dropping the folder onto the window works as well.
   If you pick the *recordings* folder, it finds the sessions inside and lets you choose one.
2. The markers and the audio are ready immediately, the waveform is analysed in the background (a few seconds the first time, instant afterwards).

The folder is expected to look like this:

```
2026-10-22_session/
    session_part000.wav  session_part001.wav ...     mono / 44.1 kHz / 16-bit PCM
    session_markers.json
    session_audio_log.json
    transcript/
        2026-10-22_session_transcript.json           (+ .txt .srt .vtt - the same transcript in other formats)
```

**Transcript lookup:** the viewer looks into the session's `transcript` folder for `<session folder name>_transcript.<ext>`
(`.json`, `.srt`, `.vtt` or `.txt`). If the same transcript exists in several formats it opens the richest one
(`.json` knows the speakers and which audio parts it was made from; `.srt`, `.vtt` and `.txt` only carry times and text).
If there are several *different* transcripts and none carries the session's name, it asks which one to open
(or lets you browse for a file). **Open...** in the transcript panel always lets you pick a file by hand.
A `.txt` transcript needs a time in front of each passage (e.g. `[0:12:05] Anna: ...` or `00:12 text`) - without times the text cannot be placed on the timeline.

### Keyboard

| Key | Action | Key | Action |
|---|---|---|---|
| **Space** | play / pause | **M** | jump-to-marker menu |
| **← / →** | ∓5 s (Shift: 30 s) | **[ / ]**, PgUp / PgDn | previous / next chapter |
| **J / L** | ∓10 s | **+ / -**, wheel | zoom |
| **Home / End** | start / end | **0** | show whole session |
| **, / .** | slower / faster | **T** | show / hide transcript |
| **↑ / ↓** | volume | **Ctrl+O** | choose folder |

### Good to know

- **Mark lead-in:** the DM presses *Ctrl+M* a few seconds *after* the moment they meant. So when you jump to a **manual mark** (from the marker menu, by clicking it on the timeline or with previous / next chapter), playback starts that many seconds earlier - default 5 s, set it next to the speed box (0 = exactly at the mark). Scenes, layouts and breaks are exact and never use it. Clicking a transcript line starts 1.5 s early, because Whisper timestamps are approximate.
- **Breaks** do not exist in the audio (the recording is paused), so they are drawn as a marker with the real length of the break, not as a block.
- **Crashed sessions** work: a damaged last part is recovered, a missing `RECORDING_END` is reported, markers beyond the end of the audio are clamped and drawn hollow.
- **Music** only logs *starts*. A track is "playing" until the next music start – that is how the *Now playing* box shows it. Music or slideshows that ran *during a break* are ignored.

## Run from source

```
cd Astratera_Chronist
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python astratera_chronist.py            (optional: a session folder as argument)
```

Python 3.9+, PyQt6 and numpy – nothing else. A fake session to play with:

```
python tests/make_test_session.py test_recordings
python astratera_chronist.py test_recordings/2026-10-22_session     (it contains a transcript in all four formats)
```

## Build the exe

**On your PC:** double-click `build_chronist.bat` (needs Python 3.9+ installed).
It builds in a private environment and creates `dist\AstrateraChronist.exe`.
`build_chronist.bat folder` makes a folder build instead, which starts faster.

**On GitHub:** the workflow `.github/workflows/build-chronist.yml` tests, builds and smoke-tests the exe on a Windows runner.
It finds `astratera_chronist.py` by itself, so the folder can sit anywhere in the repository.
GitHub only runs workflows that live in `.github/workflows` at the **root of the repository**: if `Astratera_Chronist` is a sub-folder of a bigger
repository, move (or merge) its `.github` folder to the repository root. If `Astratera_Chronist` is a repository of its own, nothing needs moving.

- *Actions → Build Astratera Chronist (Windows exe) → Run workflow*: the exe appears under **Artifacts**.
  Tick / untick *onefile*, and optionally enter a *release_tag* to publish it as a Release at the same time.
- Or push a tag: `git tag chronist-v1.0.0 && git push origin chronist-v1.0.0` – the exe is attached to a Release.

The exe is not code-signed, so Windows SmartScreen may ask once ("More info → Run anyway").

## Troubleshooting

| Problem | Fix |
|---|---|
| No sound | Check the Windows default output device. `CHRONIST_NO_AUDIO=1` switches sound off completely (analysis only). |
| Transcript text is off by a few seconds | The transcript was probably made from different parts – use the **Offset** field in the transcript panel. |
| No transcript opens | Check that it is in the session's `transcript` folder and ends in `.json`, `.srt`, `.vtt` or `.txt`; otherwise use **Open...**. A `.txt` without times cannot be used. |
| "No *_partNNN.wav files" | Choose the folder that directly contains the `_part000.wav` files (or its parent). |
| Waveform stays on "Analysing" | Very large sessions need a moment the first time; the result is cached afterwards. |

## Files

| File | Purpose |
|---|---|
| `astratera_chronist.py` | Main window, loading, transport, shortcuts (start here) |
| `chronist_core.py` | Reads the recording: WAV parts, markers, audio log, transcripts (json / srt / vtt / txt), peak cache |
| `chronist_audio.py` | Gapless multi-part playback engine with pitch-preserving speed change |
| `chronist_timeline.py` | Waveform with marker lanes, overview strip |
| `chronist_panels.py` | Marker menu, now-playing cards, transcript panel |
| `chronist_theme.py` | Colours, style sheet, icons (drawn in code) |
| `tests/` | pytest suite and the fake-session generator |
| `.github/workflows/build-chronist.yml` | GitHub Actions build of the Windows exe |
| `build_chronist.bat` | Local exe build |
| `assets/chronist.ico` | Icon of the exe |

## License

Source code: MIT, like the rest of the repository. The Windows exe bundles PyQt6 and is therefore distributed under GPL-3.0
(see the repository's `LICENSE` and `THIRD_PARTY_NOTICES.md`). Unofficial fan tool, not affiliated with Wizards of the Coast.
