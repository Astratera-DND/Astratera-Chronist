"""Tests for the data layer and the playback time-stretcher (no sound card needed).

    cd tools/astratera_chronist
    pip install pytest
    python -m pytest tests -q
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import make_test_session                      # noqa: E402
import chronist_core as oc                    # noqa: E402
from chronist_audio import PartSource, Stretcher   # noqa: E402


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    out = tmp_path_factory.mktemp("rec")
    make_test_session.main(out)
    return out, oc.load_session(out / "2026-10-22_session")


def test_parts_are_joined_with_real_durations(session):
    _, s = session
    assert [p.path.name for p in s.parts] == [f"session_part00{i}.wav" for i in range(3)]
    assert s.parts[1].start == pytest.approx(40.013, abs=1e-3)      # not 1800-style rounding
    assert s.duration == pytest.approx(105.513, abs=1e-3)


def test_damaged_header_is_recovered(session):
    _, s = session
    assert s.parts[2].repaired and s.parts[2].frames > 0
    assert any("header was damaged" in w for w in s.warnings)
    assert any("RECORDING_END is missing" in w for w in s.warnings)


def test_markers(session):
    _, s = session
    kinds = [m.kind for m in s.markers]
    assert kinds.count("scene") == 4 and kinds.count("layout") == 6
    assert kinds.count("break") == 1                                  # BREAK entries exist twice in the file
    brk = next(m for m in s.markers if m.kind == "break")
    assert brk.detail == "12:10"
    assert "Taverne Zum Drachen" in [m.label for m in s.markers]       # prefix, number and extension cleaned
    late = s.markers[-1]
    assert late.beyond and late.t == pytest.approx(s.duration)         # clamped, flagged


def test_audio_timeline_ignores_break_music(session):
    _, s = session
    assert s.audio.music_at(0).name == "Dark Theme"
    assert s.audio.music_at(60).name == "Combat Drums"                 # the break music (paused) is skipped
    assert s.audio.music_at(71).name == "Throne Room"
    assert s.audio.oneshot_at(34).kind == "quickfire"


def test_peaks_match_brute_force(session):
    _, s = session
    spb = oc.samples_per_bin(s.sample_rate)
    mins, maxs = oc.merge_part_peaks([oc.compute_part_peaks(p, spb) for p in s.parts])
    x = np.concatenate([p.samples()[:, 0] for p in s.parts])
    n = -(-len(x) // spb)
    assert len(mins) == n
    for i in (0, 1, 400, 800, n - 1):                                  # includes bins on part boundaries
        assert maxs[i] == x[i * spb:(i + 1) * spb].max()
        assert mins[i] == x[i * spb:(i + 1) * spb].min()
    lo, hi = oc.PeakPyramid(mins, maxs, spb / s.sample_rate).render(0, s.duration, 500)
    assert hi.max() > 0.3 and lo.min() < -0.3


def _tdir(out):
    return out / "2026-10-22_session" / "transcript"


def test_transcript_found_by_session_name(session):
    _, s = session
    assert len(s.transcript_candidates) == 1                          # json + srt + vtt + txt are ONE transcript
    c = s.transcript_candidates[0]
    assert c.stem == "2026-10-22_session_transcript" and c.exact
    assert [f.suffix for f in c.files] == [".json", ".srt", ".vtt", ".txt"]
    assert oc.choose_default(s.transcript_candidates) is c            # opened without asking


def test_all_transcript_formats_give_the_same_passages(session):
    out, _ = session
    ref = oc.load_transcript(_tdir(out) / "2026-10-22_session_transcript.json")
    for ext in (".srt", ".vtt", ".txt"):
        t = oc.load_transcript(_tdir(out) / f"2026-10-22_session_transcript{ext}")
        assert len(t.segments) == len(ref.segments) == 24
        assert [x.text for x in t.segments] == [x.text for x in ref.segments]
        assert [x.speaker for x in t.segments] == [x.speaker for x in ref.segments]
        assert t.segments[3].start == pytest.approx(ref.segments[3].start, abs=1.0 if ext == ".txt" else 0.01)
        assert not t.has_meta


def test_plain_text_variants(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("Session 5\n\n[0:00:08.4 - 0:00:12.9] Anna: Welcome.\n[0:00:13] Ben: Thanks!\n  and more\n\n"
                 "[0:00:20] Anna: Note: the door is open.\n[0:00:25] Ben: Okay.\n[0:00:30] Anna: Roll.\n[0:00:35] Ben: Sure.\n",
                 encoding="utf-8")                                     # a name counts as speaker once it repeats 3 times
    t = oc.load_transcript(f)
    assert t.segments[3].end == 30.0 and t.segments[2].end == 25.0     # no end time: ends where the next begins
    assert [(x.start, x.speaker, x.text) for x in t.segments][:3] == [
        (8.4, "Anna", "Welcome."), (13.0, "Ben", "Thanks! and more"), (20.0, "Anna", "Note: the door is open.")]
    g = tmp_path / "b.txt"
    g.write_text("00:08 The tavern is loud.\n01:05:03 Late line\n[725.5s] seconds style\n", encoding="utf-8")
    assert sorted(x.start for x in oc.load_transcript(g).segments) == [8.0, 725.5, 3903.0]
    h = tmp_path / "c.txt"
    h.write_text("just prose, no times at all", encoding="utf-8")
    with pytest.raises(ValueError, match="no timestamps"):
        oc.load_transcript(h)


def test_several_different_transcripts_need_a_choice(tmp_path):
    root = tmp_path / "2026-10-24_session_05"
    (root / "transcript" / "_work").mkdir(parents=True)
    for n in ("voice_sample_transcript.json", "other_transcript.srt", "notes.md"):
        (root / "transcript" / n).write_text("x")
    cands = oc.find_transcripts(root, "session_05")
    assert [c.stem for c in cands] == ["other_transcript", "voice_sample_transcript"]
    assert oc.choose_default(cands) is None                           # -> the app asks
    (root / "transcript" / "2026-10-24_session_05_transcript.txt").write_text("x")
    cands = oc.find_transcripts(root, "session_05")
    assert oc.choose_default(cands).stem == "2026-10-24_session_05_transcript"   # name of the session wins


def test_transcript_alignment_warning(session):
    out, s = session
    tr = oc.load_transcript(_tdir(out) / "2026-10-22_session_transcript.json")
    assert oc.check_transcript_alignment(tr, s) is None
    tr.parts = tr.parts[1:]                                            # as if part000 had been left out
    assert "Times may be offset" in oc.check_transcript_alignment(tr, s)


@pytest.mark.parametrize("rate", [0.75, 1.5, 2.0])
def test_stretch_keeps_pitch_and_scales_time(session, rate):
    _, s = session
    sr = s.sample_rate
    src = PartSource(s.parts)
    st = Stretcher(src, sr, rate)
    st.reset(2 * sr)
    y = st.read(4 * sr)[:, 0].astype(float)
    consumed = (st.pos - 2 * sr) / sr
    assert consumed == pytest.approx(4 * rate, rel=0.02)               # 4 s of output eats 4*rate s of source

    def peak(sig):
        return np.fft.rfftfreq(len(sig), 1 / sr)[np.abs(np.fft.rfft(sig * np.hanning(len(sig)))).argmax()]
    ref = src.read(2 * sr, int(4 * sr * rate))[:, 0].astype(float)
    assert abs(peak(y) - peak(ref)) < 4                                # pitch unchanged
