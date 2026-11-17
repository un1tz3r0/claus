import pytest

from claus.rules import NORMAL, STRONG, WEAK, RuleError, decide, parse_rule


def rules(*specs):
    """specs: (layer, [texts])"""
    out = []
    for layer, texts in specs:
        for i, t in enumerate(texts):
            out += parse_rule(t, layer, i, f"L{layer}")
    return out


def test_parse_basic():
    (r,) = parse_rule("!-name:*.pem")
    assert (r.strength, r.include, r.kind, r.pattern) == (STRONG, False, "name", "*.pem")
    (r,) = parse_rule("?+mime:text/*")
    assert (r.strength, r.include) == (WEAK, True)
    (r,) = parse_rule("+path:/src/")
    assert r.strength == NORMAL


def test_parse_ext_expands():
    rs = parse_rule("-ext:lock,.min.js")
    assert [r.pattern for r in rs] == ["*.lock", "*.min.js"]
    assert all(r.kind == "name" for r in rs)


@pytest.mark.parametrize("bad", ["name:x", "+bogus:x", "+name:", "!?+name:x", "+ext:,"])
def test_parse_errors(bad):
    with pytest.raises(RuleError):
        parse_rule(bad)


def test_default_is_exclude():
    d = decide([], "a.txt", "/x/a.txt", "text/plain")
    assert d.include is False and d.winner is None


def test_normal_innermost_wins_and_last_in_layer():
    rs = rules((1, ["+name:*.txt"]), (2, ["-name:*.txt"]))
    assert decide(rs, "a.txt", "/a.txt", None).include is False
    rs = rules((1, ["-name:*.txt", "+name:a.*"]))
    assert decide(rs, "a.txt", "/a.txt", None).include is True


def test_strong_outermost_wins():
    rs = rules((1, ["!-path:/vendor/"]), (5, ["!+name:*.py"]))
    assert decide(rs, "x.py", "/p/vendor/x.py", None).include is False
    # first strong rule wins within one layer
    rs = rules((1, ["!+name:*.py", "!-name:x.*"]))
    assert decide(rs, "x.py", "/x.py", None).include is True


def test_strong_beats_inner_normal():
    rs = rules((1, ["!-name:*.env"]), (9, ["+name:*"]))
    assert decide(rs, "a.env", "/a.env", None).include is False


def test_weak_only_without_stronger():
    rs = rules((0, ["?+mime:text/*"]), (1, ["-name:*.log"]))
    assert decide(rs, "a.log", "/a.log", "text/plain").include is False
    assert decide(rs, "a.txt", "/a.txt", "text/plain").include is True
    # a weak rule in an inner layer still loses to an outer normal rule
    rs = rules((1, ["-name:*.txt"]), (5, ["?+name:*.txt"]))
    assert decide(rs, "a.txt", "/a.txt", None).include is False


def test_iname_and_path():
    rs = rules((1, ["+iname:README*", "+path:/docs/"]))
    assert decide(rs, "readme.md", "/r/readme.md", None).include is True
    assert decide(rs, "x.txt", "/r/docs/x.txt", None).include is True
    assert decide(rs, "x.txt", "/r/src/x.txt", None).include is False


def test_lazy_mime():
    rs = rules((0, ["?+mime:text/*", "!-name:.env"]))
    # strong name rule decides without the MIME type
    assert decide(rs, ".env", "/.env", None, mime_known=False).include is False
    # otherwise the MIME type is needed
    assert decide(rs, "a.txt", "/a.txt", None, mime_known=False).undetermined
    # an include that no unknown MIME rule could flip is decided
    rs = rules((0, ["?-mime:image/*"]), (1, ["+name:*.txt"]))
    assert decide(rs, "a.txt", "/a.txt", None, mime_known=False).include is True
    # only exclude MIME rules and nothing matched: exclude either way
    rs = rules((0, ["-mime:image/*"]))
    assert decide(rs, "a", "/a", None, mime_known=False).include is False
