"""Tests for subtitle annotation module."""
import pytest

from app.personas._shared.subtitle_annotator import (
    parse_srt_blocks,
    _parse_annotations,
    annotations_to_bcc_entries,
    Annotation,
)


SAMPLE_SRT = """1
00:00:01,000 --> 00:00:04,000
This is the first subtitle line

2
00:00:05,500 --> 00:00:08,200
Something funny happens here

3
00:00:10,000 --> 00:00:13,500
And then the conclusion
"""


def test_parse_srt_blocks():
    blocks = parse_srt_blocks(SAMPLE_SRT)
    assert len(blocks) == 3
    assert blocks[0]["text"] == "This is the first subtitle line"
    assert blocks[0]["from_sec"] == pytest.approx(1.0)
    assert blocks[0]["to_sec"] == pytest.approx(4.0)
    assert blocks[1]["from_sec"] == pytest.approx(5.5)
    assert blocks[2]["text"] == "And then the conclusion"


def test_parse_srt_blocks_empty():
    assert parse_srt_blocks("") == []
    assert parse_srt_blocks("no timestamps here") == []


def test_parse_annotations_valid():
    response = '[{"time": 5, "comment": "笨蛋"}, {"time": 10, "comment": "废物"}]'
    annotations = _parse_annotations(response, 30.0, 12)
    assert len(annotations) == 2
    assert annotations[0].from_sec == 5.0
    assert annotations[0].to_sec == 8.0
    assert annotations[0].content == "笨蛋"


def test_parse_annotations_markdown_block():
    response = '```json\n[{"time": 3, "comment": "哼"}]\n```'
    annotations = _parse_annotations(response, 20.0, 12)
    assert len(annotations) == 1
    assert annotations[0].content == "哼"


def test_parse_annotations_clamps_time():
    response = '[{"time": 100, "comment": "太迟了"}]'
    annotations = _parse_annotations(response, 30.0, 12)
    assert len(annotations) == 1
    assert annotations[0].from_sec == 27.0  # clamped to 30-3


def test_parse_annotations_invalid_json():
    assert _parse_annotations("not json at all", 30.0, 12) == []


def test_parse_annotations_max_count():
    items = [{"time": i, "comment": f"c{i}"} for i in range(20)]
    response = str(items).replace("'", '"')
    annotations = _parse_annotations(response, 100.0, 5)
    assert len(annotations) == 5


def test_annotations_to_bcc_entries():
    annotations = [
        Annotation(from_sec=5.0, to_sec=8.0, content="杂鱼"),
        Annotation(from_sec=15.0, to_sec=18.0, content="绷不住了"),
    ]
    entries = annotations_to_bcc_entries(annotations)
    assert len(entries) == 2
    assert entries[0]["location"] == 1  # top position
    assert entries[0]["content"] == "杂鱼"
    assert entries[1]["from"] == 15.0
