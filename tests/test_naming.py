from pathlib import Path

import pytest

from youtube_tldr import TldrError
from youtube_tldr.naming import (
    avoid_overwrite,
    build_filename,
    resolve_output_path,
    sanitize_field,
)


def test_strips_path_separators_and_traversal():
    assert "/" not in sanitize_field("../../etc/passwd")
    assert "\\" not in sanitize_field("a\\b")
    out = sanitize_field("../../../root")
    assert ".." not in out


def test_strips_template_breakers():
    # ' - ' joiner and ';' literal must not survive in a field
    assert " - " not in sanitize_field("Foo - Bar")
    assert ";" not in sanitize_field("tl;dr hijack")


def test_drops_control_and_emoji():
    assert sanitize_field("hi\x00\x07there 🚀🎉") == "hithere"


def test_byte_cap():
    assert len(sanitize_field("x" * 500, max_bytes=80).encode()) <= 80


def test_empty_becomes_untitled():
    assert sanitize_field("") == "untitled"
    assert sanitize_field("///") == "untitled"


def test_build_filename_shape():
    name = build_filename("My Channel", "Cool Video", "3m42s", "mp4")
    assert name == "My Channel - Cool Video - tl;dr - 3m42s.mp4"


def test_resolve_contains_path(tmp_path):
    p = resolve_output_path(tmp_path, "video", "a.mp4")
    assert p.parent == (tmp_path / "video").resolve()
    assert p.is_relative_to(tmp_path.resolve())


def test_resolve_rejects_escape(tmp_path):
    with pytest.raises(TldrError):
        resolve_output_path(tmp_path, "video", "../../escape.mp4")


def test_avoid_overwrite(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("x")
    alt = avoid_overwrite(f)
    assert alt.name == "a (2).md"
