"""Unit tests for Claude JSON extraction — no network, no database."""
import pytest

from pipeline import _parse_model_json


def test_plain_object_parses():
    assert _parse_model_json('{"summary": "ok", "sections": []}')["summary"] == "ok"


def test_markdown_fence_is_stripped():
    raw = '```json\n{"summary": "fenced", "sections": []}\n```'
    assert _parse_model_json(raw)["summary"] == "fenced"


def test_leading_prose_still_finds_the_object():
    raw = 'Here is the report:\n{"summary": "prose", "sections": []}\nThanks.'
    assert _parse_model_json(raw)["summary"] == "prose"


def test_truncated_json_raises():
    with pytest.raises(ValueError, match="No usable JSON"):
        _parse_model_json('{"summary": "cut off", "sections": [')


def test_empty_raises():
    with pytest.raises(ValueError, match="Empty"):
        _parse_model_json("   ")


def test_array_root_is_rejected():
    with pytest.raises(ValueError, match="No usable JSON"):
        _parse_model_json("[1, 2, 3]")
