"""One conversation that arrived as several recordings.

A transfer, a callback, a dropped line. Graded separately, each part fails the
requirements satisfied in another part — the false negative a reviewer then has
to unpick by hand.

The audio cannot be joined first (no ffmpeg on this host), so each part is
transcribed on its own and the timestamps are rebased. Every timestamp in a
report is a claim about where the evidence is; getting the offset wrong sends a
reviewer to silence or to the wrong sentence, which is worse than not merging.
"""
import pytest

from pipeline import merge_transcripts


def _part(utts, duration=None, text=None):
    return {"utterances": [{"speaker": s, "start": a, "end": b, "text": t}
                           for s, a, b, t in utts],
            "audio_duration": duration,
            "text": text or " ".join(t for _, _, _, t in utts)}


def test_a_single_part_is_unchanged():
    """The ordinary case must pass through untouched."""
    p = _part([("A", 0, 1000, "hello"), ("B", 2000, 3000, "hi")], duration=10)
    out = merge_transcripts([p])
    assert [u["start"] for u in out["utterances"]] == [0, 2000]


def test_the_second_part_is_offset_by_the_first_recordings_length():
    a = _part([("A", 0, 5000, "part one")], duration=300)        # 5 minutes
    b = _part([("B", 0, 4000, "part two")], duration=120)
    out = merge_transcripts([a, b])
    assert [u["start"] for u in out["utterances"]] == [0, 300_000]
    assert [u["end"] for u in out["utterances"]] == [5000, 304_000]


def test_the_offset_uses_audio_length_not_the_last_word():
    """A recording ending in ten seconds of hold music is ten seconds long that
    the transcript never mentions. Offsetting by the last utterance would slide
    every later timestamp earlier, and they would all be wrong."""
    a = _part([("A", 0, 5000, "talking")], duration=60)   # 60s of audio, 5s of speech
    b = _part([("B", 0, 1000, "later")], duration=30)
    out = merge_transcripts([a, b])
    assert out["utterances"][1]["start"] == 60_000, "offset fell back to the last word"


def test_three_parts_accumulate():
    parts = [_part([("A", 0, 1000, f"p{i}")], duration=100) for i in range(3)]
    out = merge_transcripts(parts)
    assert [u["start"] for u in out["utterances"]] == [0, 100_000, 200_000]


def test_a_missing_duration_still_advances_the_offset():
    """Better to shift by the last utterance than to stack two recordings on top
    of each other at the same timestamps."""
    a = _part([("A", 0, 9000, "one")], duration=None)
    b = _part([("B", 0, 1000, "two")], duration=50)
    out = merge_transcripts([a, b])
    assert out["utterances"][1]["start"] >= 9000


def test_the_text_is_joined_in_order():
    out = merge_transcripts([_part([("A", 0, 1, "first")], duration=10),
                             _part([("B", 0, 1, "second")], duration=10)])
    assert out["text"].index("first") < out["text"].index("second")


def test_the_result_looks_like_an_ordinary_transcript():
    """Nothing downstream should need to know the call had parts."""
    out = merge_transcripts([_part([("A", 0, 1000, "x")], duration=5)])
    assert set(out) == {"utterances", "text"}
    assert set(out["utterances"][0]) == {"speaker", "start", "end", "text"}


def test_empty_parts_do_not_break_it():
    out = merge_transcripts([_part([], duration=30), _part([("A", 0, 1000, "x")], duration=10)])
    assert out["utterances"][0]["start"] == 30_000
