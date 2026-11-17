import time

import pytest

from claus.lsfmt import display_width, human_size, safe_name, time_col


@pytest.mark.parametrize("n,expected", [
    (0, "0"), (1023, "1023"), (1024, "1.0K"), (1536, "1.5K"), (1025, "1.1K"),
    (10 * 1024, "10K"), (10 * 1024 + 1, "11K"), (1 << 20, "1.0M"), (1023 * 1024 + 1, "1.0M"),
    (5 << 30, "5.0G"),
])
def test_human_size(n, expected):
    assert human_size(n) == expected


def test_time_col_styles():
    now = time.time()
    assert ":" in time_col(now - 60, "ls", now)
    old = time_col(now - 400 * 86400, "ls", now)
    assert ":" not in old and old.strip()[-4:].isdigit()
    assert time_col(now - 7200, "relative", now) == "2h ago"
    assert len(time_col(now, "iso", now)) == 16


def test_safe_name_and_width():
    assert safe_name("a\nb") == "a?b"
    assert safe_name("ok name") == "ok name"
    assert display_width("日本") == 4
