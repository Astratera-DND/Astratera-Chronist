"""The transcript panel: follows the playhead, marks the line being said, search, click-to-seek."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from PyQt6.QtWidgets import QApplication

import chronist_core as oc
from chronist_panels import TranscriptPanel


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def make_panel(app, n=3000):
    segs, t = [], 1.0
    for i in range(n):
        words = " ".join(["dragon" if i % 50 == 0 else "word"] + ["filler"] * (3 + i % 17))
        segs.append(oc.Segment(t, t + 3.0, words, ["Anna", "Ben", "DM"][i % 3]))
        t += 4.0
    panel = TranscriptPanel()
    panel.resize(380, 800)
    panel.show()
    app.processEvents()
    panel.set_transcript(oc.Transcript(Path("x.json"), segs, oc._speaker_stats(segs)))
    return panel


def on_screen(panel, row):
    r = panel.list.visualItemRect(panel._items[row])
    return panel.list.viewport().rect().intersects(r) and r.height() > 0


def test_marks_current_line_and_scrolls_even_far_away(app):
    p = make_panel(app)
    for row in (5, 1500, 2990, 40):                                   # near, far down, the end, back up
        p.set_time(p.tr_.segments[row].start + 1.0)
        assert p.delegate.current == row                              # exactly one line is marked
        assert on_screen(p, row)                                      # ... and it is visible


def test_follow_can_be_switched_off(app):
    p = make_panel(app)
    p.follow.setChecked(False)
    p.list.verticalScrollBar().setValue(0)
    p.set_time(p.tr_.segments[2000].start + 1)
    assert p.delegate.current == 2000 and p.list.verticalScrollBar().value() == 0
    p.follow.setChecked(True)                                         # switching on jumps to the current line
    assert on_screen(p, 2000)


def test_click_seeks_a_little_early(app):
    p = make_panel(app)
    got = []
    p.seekRequested.connect(got.append)
    p._clicked(p._items[10])
    assert got == [pytest.approx(p.tr_.segments[10].start - 1.5)]


def test_search_filters_and_enter_jumps_to_next_match(app):
    p = make_panel(app)
    got = []
    p.seekRequested.connect(got.append)
    p.search.setText("dragon")
    p._filter()                                                       # (normally after a short typing pause)
    shown = [i for i, it in enumerate(p._items) if not it.isHidden()]
    assert shown == list(range(0, 3000, 50))
    p.set_time(p.tr_.segments[120].start)                             # playhead between two matches
    p._jump_to_next_match()
    assert got[-1] == pytest.approx(p.tr_.segments[150].start - 1.5)
    p.search.clear()
    p._filter()
    assert all(not it.isHidden() for it in p._items)


def test_offset_shifts_the_current_line(app):
    p = make_panel(app, 200)
    p.set_time(p.tr_.segments[50].start + 1)
    assert p.delegate.current == 50
    p.offset_spin.setValue(8.0)                                       # transcript runs 8 s ahead of the audio
    assert p.delegate.current == 48
