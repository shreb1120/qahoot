"""The report timeline. It must never invent data it does not have."""
import pytest
from blueprints.calls_bp import _report_view_model, ts_seconds


class Obj:
    def __init__(self, **kw): self.__dict__.update(kw)


def _call(utterances, duration=None, report_json=None):
    return Obj(
        duration=duration,
        transcript=Obj(raw_transcript_json={"utterances": utterances}) if utterances is not None else None,
        report=Obj(report_json=report_json or {}) if report_json is not None else None,
    )


@pytest.mark.parametrize("value,expected", [
    ("0:32", 32), ("2:41", 161), ("1:02:33", 3753),
    ("—", None), ("", None), (None, None), ("abc", None), ("1:xx", None),
])
def test_ts_seconds_parses_or_refuses(value, expected):
    assert ts_seconds(value) == expected


def test_no_timeline_when_utterances_carry_no_usable_timing():
    """Deriving a duration from utterance timings is correct behaviour; the real
    no-timeline case is timing that yields nothing to draw."""
    vm = _report_view_model(_call([{"speaker": "A", "start": 0, "end": 0, "text": "hi"}]))
    assert vm["has_timeline"] is False
    assert vm["bands"] == []


def test_timeline_appears_when_timing_exists_even_without_a_stored_duration():
    vm = _report_view_model(_call([{"speaker": "A", "start": 0, "end": 12000, "text": "hi"}]))
    assert vm["duration"] == 12 and vm["has_timeline"] is True


def test_no_timeline_without_a_transcript():
    vm = _report_view_model(_call(None, duration=300))
    assert vm["has_timeline"] is False
    assert vm["lines"] == []


def test_null_raw_json_does_not_explode():
    call = Obj(duration=60, transcript=Obj(raw_transcript_json=None), report=None)
    vm = _report_view_model(call)
    assert vm["has_timeline"] is False


def test_duration_falls_back_to_the_last_utterance():
    vm = _report_view_model(_call(
        [{"speaker": "A", "start": 0, "end": 30000, "text": "a"},
         {"speaker": "B", "start": 30000, "end": 90000, "text": "b"}]))
    assert vm["duration"] == 90
    assert vm["has_timeline"] is True


def test_speaker_slots_are_stable_and_by_first_appearance():
    vm = _report_view_model(_call(
        [{"speaker": "B", "start": 0, "end": 1000, "text": "first"},
         {"speaker": "A", "start": 1000, "end": 2000, "text": "second"},
         {"speaker": "B", "start": 2000, "end": 3000, "text": "third"}], duration=3))
    slots = [l["slot"] for l in vm["lines"]]
    assert slots == ["a", "b", "a"], "speaker colour must key off first appearance"


def test_bands_stay_within_the_track():
    vm = _report_view_model(_call(
        [{"speaker": "A", "start": 0, "end": 400000, "text": "over"}], duration=100))
    for b in vm["bands"]:
        assert 0 <= b["left"] <= 100 and b["left"] + b["width"] <= 100.5


def test_markers_only_for_parseable_timestamps():
    rj = {"sections": [{"name": "S", "key": "s", "items": [
        {"name": "covered one", "status": "covered", "timestamp": "0:30"},
        {"name": "missing one", "status": "missing", "timestamp": "—"},
        {"name": "no timestamp", "status": "missing"},
    ]}], "auto_fail_phrases": {"detected": True, "phrases": [
        {"phrase": "bad", "timestamp": "0:45"}, {"phrase": "unknown", "timestamp": None}]}}
    vm = _report_view_model(_call(
        [{"speaker": "A", "start": 0, "end": 60000, "text": "x"}], duration=60, report_json=rj))
    assert len(vm["markers"]) == 2
    assert sorted(vm["marker_kinds"]) == ["crit", "ok"]
    assert "miss" not in vm["marker_kinds"], "legend must not advertise a marker that was never placed"


def test_markers_are_sorted_along_the_timeline():
    rj = {"sections": [{"name": "S", "key": "s", "items": [
        {"name": "late", "status": "covered", "timestamp": "0:50"},
        {"name": "early", "status": "covered", "timestamp": "0:10"},
    ]}], "auto_fail_phrases": {}}
    vm = _report_view_model(_call(
        [{"speaker": "A", "start": 0, "end": 60000, "text": "x"}], duration=60, report_json=rj))
    assert [m["pct"] for m in vm["markers"]] == sorted(m["pct"] for m in vm["markers"])


def test_talk_share_sums_to_about_100():
    vm = _report_view_model(_call(
        [{"speaker": "A", "start": 0, "end": 30000, "text": "a"},
         {"speaker": "B", "start": 30000, "end": 60000, "text": "b"}], duration=60))
    assert sum(vm["talk_share"].values()) in (99, 100, 101)
    assert vm["talk_share"]["a"] == 50
