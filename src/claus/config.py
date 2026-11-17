"""Configuration: defaults, TOML layers, trust, effective settings (DESIGN §4)."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import tomllib
from dataclasses import dataclass
from typing import Any, Callable

from claus.rules import Rule, RuleError, parse_rule

DIR_CONFIG_NAME = ".claus.toml"

BUILTIN_TEXT_RULES = [
    "?+mime:text/*",
    "?+mime:application/json",
    "?+mime:application/xml",
    "?+mime:application/javascript",
    "?+mime:application/x-*script",
    "?+mime:application/toml",
    "?+mime:application/yaml",
    "?+mime:application/x-yaml",
    "?+mime:application/sql",
    "?+mime:application/x-ndjson",
]

BUILTIN_PRIVACY_RULES = [
    "!-name:.env",
    "!-name:.env.*",
    "!-name:*.pem",
    "!-name:*.key",
    "!-name:id_rsa*",
    "!-name:id_ed25519*",
    "!-name:id_ecdsa*",
    "!-name:id_dsa*",
    "!-iname:*secret*",
    "!-iname:*credential*",
    "!-name:.netrc",
    "!-name:.pgpass",
    "!-name:*.kdbx",
    "!-path:/.git/",
    "!-path:/.ssh/",
    "!-path:/.gnupg/",
]

DEFAULT_SYSTEM_PROMPT = (
    "You write one-line summaries of files for a directory listing. Describe what the "
    "file is, does, or contains, as specifically as possible. Plain text, never more "
    "than {max_length} characters; aim for about two thirds of that. Do not start with "
    "'This file', and do not repeat the file name."
)
DEFAULT_PROMPT = (
    "Summarize the file provided on stdin. File name (hint only, the file may be "
    "renamed): {name}. MIME type: {mime}. {truncated} The summary must fit in "
    "{max_length} characters."
)
DEFAULT_PROMPT_ADD_DIR = (
    "Read and summarize the file at {path}. MIME type: {mime}. The summary must fit in "
    "{max_length} characters."
)

DEFAULTS: dict[str, Any] = {
    "trust_dir_config": "filters",
    "builtin_privacy_rules": True,
    "summary": {
        "model": "haiku",
        "effort": "low",
        "max_length": 120,
        "max_bytes": 65536,
        "delivery": "stdin",
        "timeout": 120.0,
        "retries": 2,
        "concurrency": 4,
        "confirm_threshold": 25,
        "system_prompt": DEFAULT_SYSTEM_PROMPT,
        "prompt": DEFAULT_PROMPT,
        "prompt_add_dir": DEFAULT_PROMPT_ADD_DIR,
        "claude_path": "claude",
        "extra_args": [],
        "env": {},
        "budget_usd": 0.0,
    },
    "filter": {
        "rules": [],
        "max_size": "16MiB",
        "skip_empty": True,
    },
    "cache": {
        "path": "",
        "retention_days": 30.0,
    },
    "listing": {
        "skip_dirs": [".git", "node_modules", "__pycache__", ".venv", ".tox"],
        "time_style": "ls",
    },
}

# Expected types for validation. A tuple means any of these types.
_NUM = (int, float)
SCHEMA: dict[str, Any] = {
    "trust_dir_config": str,
    "builtin_privacy_rules": bool,
    "summary": {
        "model": str, "effort": str, "max_length": int, "max_bytes": int, "delivery": str,
        "timeout": _NUM, "retries": int, "concurrency": int, "confirm_threshold": int,
        "system_prompt": str, "prompt": str, "prompt_add_dir": str, "claude_path": str,
        "extra_args": list, "env": dict, "budget_usd": _NUM,
    },
    "filter": {"rules": list, "max_size": (str, int), "skip_empty": bool},
    "cache": {"path": str, "retention_days": _NUM},
    "listing": {"skip_dirs": list, "time_style": str},
}
CHOICES = {
    ("trust_dir_config",): {"none", "filters", "all"},
    ("summary", "effort"): {"low", "medium", "high", "xhigh", "max"},
    ("summary", "delivery"): {"stdin", "add-dir"},
    ("listing", "time_style"): {"ls", "iso", "relative"},
}

# What a directory .claus.toml may set, per trust level (DESIGN §4.2).
_DIR_FILTER_KEYS = {"rules", "max_size", "skip_empty"}
_DIR_SUMMARY_KEYS = {
    "model", "effort", "max_length", "max_bytes", "delivery", "system_prompt", "prompt",
    "prompt_add_dir",
}
DIR_ALLOWED: dict[str, dict[str, set[str]]] = {
    "none": {},
    "filters": {"filter": _DIR_FILTER_KEYS},
    "all": {"filter": _DIR_FILTER_KEYS, "summary": _DIR_SUMMARY_KEYS},
}

# JSON schema for the structured output. The description carries the length limit,
# which the model follows more reliably than the prompt alone.
SUMMARY_SCHEMA_DESCRIPTION = "One line, plain text, at most {max_length} characters."


def summary_schema(max_length: int) -> dict:
    return {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": SUMMARY_SCHEMA_DESCRIPTION.format(max_length=max_length),
            }
        },
        "required": ["summary"],
        "additionalProperties": False,
    }

LAYER_BUILTIN = 0
LAYER_GLOBAL = 1
LAYER_DIR_BASE = 2
LAYER_CLI = 1_000_000

Warn = Callable[[str], None]


class ConfigError(Exception):
    pass


def xdg_dir(var: str, fallback: str) -> str:
    v = os.environ.get(var)
    return v if v and os.path.isabs(v) else os.path.expanduser(fallback)


def default_global_config_path() -> str:
    return os.path.join(xdg_dir("XDG_CONFIG_HOME", "~/.config"), "claus", "config.toml")


def default_cache_path() -> str:
    return os.path.join(xdg_dir("XDG_CACHE_HOME", "~/.cache"), "claus", "cache.sqlite")


_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kmgt]?)(i?)b?\s*$", re.I)


def parse_size(v: str | int) -> int:
    """Parse ``16MiB``, ``1M``, ``100k``, ``2048`` → bytes (K/M/G/T are 1024-based)."""
    if isinstance(v, int):
        return v
    m = _SIZE_RE.match(v)
    if not m:
        raise ConfigError(f"invalid size {v!r}")
    num, unit = float(m.group(1)), m.group(2).lower()
    mult = {"": 1, "k": 1 << 10, "m": 1 << 20, "g": 1 << 30, "t": 1 << 40}[unit]
    return int(num * mult)


def _type_ok(value: Any, expected: Any) -> bool:
    if expected in (int, _NUM) or (isinstance(expected, tuple) and int in expected):
        if isinstance(value, bool):
            return False
    return isinstance(value, expected)


def validate(data: dict, source: str, warn: Warn) -> dict:
    """Drop unknown keys and wrongly-typed values, with a warning for each."""
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key not in SCHEMA:
            warn(f"{source}: unknown key {key!r} ignored")
            continue
        expected = SCHEMA[key]
        if isinstance(expected, dict):
            if not isinstance(value, dict):
                warn(f"{source}: [{key}] must be a table; ignored")
                continue
            sect: dict[str, Any] = {}
            for k, v in value.items():
                if k not in expected:
                    warn(f"{source}: unknown key {key}.{k} ignored")
                elif not _type_ok(v, expected[k]):
                    warn(f"{source}: {key}.{k} has the wrong type; ignored")
                elif (key, k) in CHOICES and v not in CHOICES[(key, k)]:
                    warn(f"{source}: {key}.{k} must be one of {sorted(CHOICES[(key, k)])}; ignored")
                else:
                    sect[k] = v
            out[key] = sect
        elif not _type_ok(value, expected):
            warn(f"{source}: {key} has the wrong type; ignored")
        elif (key,) in CHOICES and value not in CHOICES[(key,)]:
            warn(f"{source}: {key} must be one of {sorted(CHOICES[(key,)])}; ignored")
        else:
            out[key] = value
    return out


def restrict_dir_layer(data: dict, trust: str, source: str, warn: Warn) -> dict:
    """Keep only the keys a directory config may set at this trust level."""
    allowed = DIR_ALLOWED[trust]
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key not in allowed:
            warn(f"{source}: {key!r} cannot be set in a directory config (trust_dir_config={trust!r}); ignored")
            continue
        sect = {}
        for k, v in value.items():
            if k in allowed[key]:
                sect[k] = v
            else:
                warn(f"{source}: {key}.{k} cannot be set in a directory config (trust_dir_config={trust!r}); ignored")
        out[key] = sect
    return out


def load_toml(path: str) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def merge_into(base: dict, layer: dict) -> None:
    """Merge ``layer`` into ``base`` in place. Rules are *not* merged here."""
    for key, value in layer.items():
        if isinstance(value, dict):
            sect = base.setdefault(key, {})
            for k, v in value.items():
                if key == "filter" and k == "rules":
                    continue
                sect[k] = copy.deepcopy(v)
        else:
            base[key] = value


@dataclass
class Layer:
    kind: str  # built-in | global | dir | cli
    source: str  # file path or label
    index: int  # rule layer index
    data: dict

    def rule_texts(self) -> list[str]:
        return list(self.data.get("filter", {}).get("rules", []))


@dataclass
class Effective:
    """Effective settings for files in one directory."""

    settings: dict
    rules: list[Rule]
    layers: list[Layer]
    fingerprint: str = ""
    max_size: int = 0

    @property
    def summary(self) -> dict:
        return self.settings["summary"]

    @property
    def sources(self) -> list[str]:
        return [layer.source for layer in self.layers]


def compute_fingerprint(summary: dict) -> str:
    delivery = summary["delivery"]
    parts = {
        "model": summary["model"],
        "effort": summary["effort"],
        "system_prompt": summary["system_prompt"],
        "prompt": summary["prompt_add_dir"] if delivery == "add-dir" else summary["prompt"],
        "schema": summary_schema(summary["max_length"]),
        "max_length": summary["max_length"],
        "max_bytes": summary["max_bytes"],
        "delivery": delivery,
    }
    blob = json.dumps(parts, sort_keys=True).encode()
    return hashlib.blake2b(blob, digest_size=8).hexdigest()


class ConfigResolver:
    """Builds :class:`Effective` configs per directory, caching each directory."""

    def __init__(
        self,
        global_path: str | None = None,
        use_files: bool = True,
        cli_settings: dict | None = None,
        cli_rules: list[str] | None = None,
        warn: Warn | None = None,
    ) -> None:
        self.warn: Warn = warn or (lambda msg: None)
        self.use_files = use_files
        self._lock = threading.Lock()
        self._dir_layer_cache: dict[str, Layer | None] = {}
        self._eff_cache: dict[str, Effective] = {}

        self.global_path = global_path or default_global_config_path()
        global_data: dict = {}
        self.global_loaded = False
        if use_files and os.path.isfile(self.global_path):
            try:
                global_data = validate(load_toml(self.global_path), self.global_path, self.warn)
                self.global_loaded = True
            except (OSError, tomllib.TOMLDecodeError) as e:
                raise ConfigError(f"{self.global_path}: {e}") from e
        elif global_path and use_files:
            raise ConfigError(f"{global_path}: no such config file")

        builtin = copy.deepcopy(DEFAULTS)
        privacy = global_data.get("builtin_privacy_rules", True)
        builtin["filter"]["rules"] = BUILTIN_TEXT_RULES + (BUILTIN_PRIVACY_RULES if privacy else [])
        self.builtin_layer = Layer("built-in", "built-in", LAYER_BUILTIN, builtin)
        self.global_layer = (
            Layer("global", self.global_path, LAYER_GLOBAL, global_data) if global_data else None
        )
        cli = validate(cli_settings or {}, "command line", self.warn)
        cli.setdefault("filter", {})["rules"] = list(cli_rules or [])
        self.cli_layer = Layer("cli", "command line", LAYER_CLI, cli)

        # Global-only settings: built-in + global + cli (no directory layers).
        self.base = self._merge([self.builtin_layer, self.global_layer, self.cli_layer])
        self.trust = self.base["trust_dir_config"]
        # Parse fixed rule layers once (errors here are fatal).
        self._fixed_rules: dict[int, list[Rule]] = {}
        for layer in (self.builtin_layer, self.global_layer, self.cli_layer):
            if layer is not None:
                self._fixed_rules[layer.index] = self._parse_layer_rules(layer, fatal=True)

    # -- helpers ---------------------------------------------------------------

    @staticmethod
    def _merge(layers: list[Layer | None]) -> dict:
        out: dict = {}
        for layer in layers:
            if layer is not None:
                merge_into(out, layer.data)
        return out

    def _parse_layer_rules(self, layer: Layer, fatal: bool = False) -> list[Rule]:
        rules: list[Rule] = []
        label = layer.source if layer.kind in ("global", "dir") else layer.kind
        for i, text in enumerate(layer.rule_texts()):
            if not isinstance(text, str):
                self.warn(f"{label}: rule {text!r} is not a string; ignored")
                continue
            try:
                rules.extend(parse_rule(text, layer.index, i, label))
            except RuleError as e:
                if fatal:
                    raise ConfigError(str(e)) from e
                self.warn(f"{label}: {e}; ignored")
        return rules

    def _dir_layer(self, d: str) -> Layer | None:
        if d in self._dir_layer_cache:
            return self._dir_layer_cache[d]
        layer = None
        path = os.path.join(d, DIR_CONFIG_NAME)
        if self.use_files and self.trust != "none" and os.path.isfile(path):
            try:
                data = validate(load_toml(path), path, self.warn)
                data = restrict_dir_layer(data, self.trust, path, self.warn)
                depth = len([p for p in d.split(os.sep) if p])
                layer = Layer("dir", path, LAYER_DIR_BASE + depth, data)
            except (OSError, tomllib.TOMLDecodeError) as e:
                self.warn(f"{path}: {e}; ignored")
        self._dir_layer_cache[d] = layer
        return layer

    @staticmethod
    def _ancestors(d: str) -> list[str]:
        d = os.path.abspath(d)
        chain = []
        while True:
            chain.append(d)
            parent = os.path.dirname(d)
            if parent == d:
                break
            d = parent
        return list(reversed(chain))

    # -- public ----------------------------------------------------------------

    def for_dir(self, d: str) -> Effective:
        """Effective config for files directly inside directory ``d``."""
        d = os.path.abspath(d)
        with self._lock:
            eff = self._eff_cache.get(d)
            if eff is not None:
                return eff
            dir_layers = [layer for a in self._ancestors(d) if (layer := self._dir_layer(a))]
            layers = [self.builtin_layer]
            if self.global_layer:
                layers.append(self.global_layer)
            layers += dir_layers
            layers.append(self.cli_layer)
            settings = self._merge(layers)
            # global-only keys always come from base
            for key in ("trust_dir_config", "builtin_privacy_rules", "cache", "listing"):
                settings[key] = copy.deepcopy(self.base[key])
            for k in set(self.base["summary"]) - _DIR_SUMMARY_KEYS:
                settings["summary"][k] = copy.deepcopy(self.base["summary"][k])
            rules: list[Rule] = []
            for layer in layers:
                if layer.index in self._fixed_rules:
                    rules += self._fixed_rules[layer.index]
                else:
                    rules += self._parse_layer_rules(layer)
            try:
                max_size = parse_size(settings["filter"]["max_size"])
            except ConfigError as e:
                self.warn(f"filter.max_size: {e}; using default")
                max_size = parse_size(DEFAULTS["filter"]["max_size"])
            eff = Effective(settings, rules, layers, compute_fingerprint(settings["summary"]), max_size)
            self._eff_cache[d] = eff
            return eff

    def cache_path(self) -> str:
        p = self.base["cache"]["path"]
        return os.path.expanduser(p) if p else default_cache_path()


# -- TOML output (for `claus config`) -------------------------------------------


def _toml_value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)
    if isinstance(v, list):
        if not v:
            return "[]"
        inner = ",\n".join(f"  {_toml_value(x)}" for x in v)
        return f"[\n{inner},\n]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{json.dumps(k)} = {_toml_value(x)}" for k, x in v.items()) + " }"
    raise TypeError(type(v))


def dump_toml(data: dict) -> str:
    lines: list[str] = []
    for k, v in data.items():
        if not isinstance(v, dict):
            lines.append(f"{k} = {_toml_value(v)}")
    for k, v in data.items():
        if isinstance(v, dict):
            lines.append(f"\n[{k}]")
            for kk, vv in v.items():
                lines.append(f"{kk} = {_toml_value(vv)}")
    return "\n".join(lines).lstrip("\n") + "\n"


__all__ = [
    "BUILTIN_PRIVACY_RULES", "BUILTIN_TEXT_RULES", "ConfigError", "ConfigResolver", "DEFAULTS",
    "DIR_CONFIG_NAME", "Effective", "Layer", "compute_fingerprint",
    "default_cache_path", "default_global_config_path", "dump_toml", "parse_size",
    "summary_schema",
]
