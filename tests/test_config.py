from pathlib import Path

import pytest

from claus.config import BUILTIN_PRIVACY_RULES, ConfigError, ConfigResolver, parse_size


def make(tmp_path: Path, global_text: str | None = None, **kw):
    warnings: list[str] = []
    gp = None
    if global_text is not None:
        gp = tmp_path / "global.toml"
        gp.write_text(global_text)
    r = ConfigResolver(global_path=str(gp) if gp else None, warn=warnings.append, **kw)
    return r, warnings


def test_parse_size():
    assert parse_size("16MiB") == 16 << 20
    assert parse_size("1k") == 1024
    assert parse_size("2048") == 2048
    assert parse_size(5) == 5
    with pytest.raises(ConfigError):
        parse_size("lots")


def test_dir_layer_filters_only_by_default(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / ".claus.toml").write_text(
        '[filter]\nrules = ["-name:*.log"]\n[summary]\nmodel = "opus"\nclaude_path = "/bin/evil"\n'
    )
    r, warnings = make(tmp_path)
    eff = r.for_dir(str(d))
    assert eff.summary["model"] == "haiku"
    assert eff.summary["claude_path"] == "claude"
    assert any("-name:*.log" == x.text for x in eff.rules)
    assert any("summary" in w for w in warnings)


def test_trust_all_allows_summary_but_never_executable(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / ".claus.toml").write_text('[summary]\nmodel = "sonnet"\nclaude_path = "/bin/evil"\nconcurrency = 99\n')
    r, warnings = make(tmp_path, 'trust_dir_config = "all"\n')
    eff = r.for_dir(str(d))
    assert eff.summary["model"] == "sonnet"
    assert eff.summary["claude_path"] == "claude"
    assert eff.summary["concurrency"] == 4
    assert sum("cannot be set" in w for w in warnings) == 2


def test_trust_none_ignores_dir_files(tmp_path):
    d = tmp_path / "proj"
    d.mkdir()
    (d / ".claus.toml").write_text('[filter]\nrules = ["+name:*"]\n')
    r, _ = make(tmp_path, 'trust_dir_config = "none"\n')
    assert not any(x.text == "+name:*" for x in r.for_dir(str(d)).rules)


def test_inner_scalar_wins_and_rules_accumulate(tmp_path):
    outer = tmp_path / "a"
    inner = outer / "b"
    inner.mkdir(parents=True)
    (outer / ".claus.toml").write_text('[filter]\nrules = ["-name:x"]\nmax_size = "1k"\n')
    (inner / ".claus.toml").write_text('[filter]\nrules = ["+name:x"]\nmax_size = "2k"\n')
    r, _ = make(tmp_path, "")
    eff = r.for_dir(str(inner))
    assert eff.max_size == 2048
    texts = [x.text for x in eff.rules]
    assert texts.index("-name:x") < texts.index("+name:x")
    layers = {x.text: x.layer for x in eff.rules if x.text in ("-name:x", "+name:x")}
    assert layers["+name:x"] > layers["-name:x"]


def test_cli_layer_is_innermost(tmp_path):
    r, _ = make(tmp_path, '[summary]\nmodel = "sonnet"\n', cli_settings={"summary": {"model": "opus"}},
                cli_rules=["-name:a"])
    eff = r.for_dir(str(tmp_path))
    assert eff.summary["model"] == "opus"
    assert max(eff.rules, key=lambda x: x.layer).text == "-name:a"


def test_fingerprint_depends_on_model_and_prompt(tmp_path):
    a = make(tmp_path, "", cli_settings={"summary": {"model": "haiku"}})[0].for_dir(str(tmp_path))
    b = make(tmp_path, "", cli_settings={"summary": {"model": "sonnet"}})[0].for_dir(str(tmp_path))
    c = make(tmp_path, "", cli_settings={"summary": {"concurrency": 9}})[0].for_dir(str(tmp_path))
    assert a.fingerprint != b.fingerprint
    assert a.fingerprint == c.fingerprint


def test_privacy_rules_toggle(tmp_path):
    on = make(tmp_path, "")[0].for_dir(str(tmp_path))
    off = make(tmp_path, "builtin_privacy_rules = false\n")[0].for_dir(str(tmp_path))
    assert {r.text for r in on.rules} >= set(BUILTIN_PRIVACY_RULES)
    assert not ({r.text for r in off.rules} & set(BUILTIN_PRIVACY_RULES))


def test_validation_warnings(tmp_path):
    _, warnings = make(tmp_path, 'bogus = 1\n[summary]\nmax_length = "long"\neffort = "extreme"\n')
    assert len(warnings) == 3


def test_bad_rule_in_global_is_fatal(tmp_path):
    with pytest.raises(ConfigError):
        make(tmp_path, '[filter]\nrules = ["nonsense"]\n')


def test_explicit_missing_config_is_error(tmp_path):
    with pytest.raises(ConfigError):
        ConfigResolver(global_path=str(tmp_path / "nope.toml"))
