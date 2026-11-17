"""Filter rules: parsing and resolution (DESIGN §4.3).

Rule syntax::

    [!|?] (+|-) type:pattern

``!`` = strong (outermost layer wins), no prefix = normal (innermost/last wins),
``?`` = weak (innermost/last wins, only when nothing stronger matches).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase

STRONG, NORMAL, WEAK = 2, 1, 0
STRENGTH_PREFIX = {STRONG: "!", NORMAL: "", WEAK: "?"}
STRENGTH_NAME = {STRONG: "strong", NORMAL: "normal", WEAK: "weak"}
KINDS = ("mime", "name", "iname", "ext", "path")


class RuleError(ValueError):
    """A rule string could not be parsed."""


@dataclass(frozen=True)
class Rule:
    strength: int
    include: bool
    kind: str  # mime | name | iname | path   ("ext" is expanded to "name")
    pattern: str
    text: str  # original rule text, for display
    layer: int = 0  # 0 = built-in (outermost); larger = more inner
    order: int = 0  # position within its layer
    source: str = "built-in"  # human-readable layer description

    @property
    def needs_mime(self) -> bool:
        return self.kind == "mime"

    def key(self) -> tuple[int, int, int]:
        """Precedence key: the matching rule with the largest key wins."""
        if self.strength == STRONG:
            return (self.strength, -self.layer, -self.order)
        return (self.strength, self.layer, self.order)

    def matches(self, name: str, path: str, mime: str | None) -> bool:
        if self.kind == "mime":
            return mime is not None and fnmatchcase(mime, self.pattern)
        if self.kind == "name":
            return fnmatchcase(name, self.pattern)
        if self.kind == "iname":
            return fnmatchcase(name.lower(), self.pattern.lower())
        if self.kind == "path":
            return self.pattern in path
        raise AssertionError(self.kind)

    def describe(self) -> str:
        return f"{self.text}  [{STRENGTH_NAME[self.strength]}, {self.source}]"


def parse_rule(text: str, layer: int = 0, order: int = 0, source: str = "built-in") -> list[Rule]:
    """Parse one rule string. ``ext:`` expands to several ``name:`` rules."""
    s = text.strip()
    strength = NORMAL
    if s[:1] == "!":
        strength, s = STRONG, s[1:]
    elif s[:1] == "?":
        strength, s = WEAK, s[1:]
    if s[:1] not in ("+", "-"):
        raise RuleError(f"rule {text!r}: expected '+' or '-' (after optional '!' or '?')")
    include, s = s[0] == "+", s[1:]
    kind, sep, pattern = s.partition(":")
    if not sep or kind not in KINDS:
        raise RuleError(f"rule {text!r}: expected TYPE:PATTERN with TYPE in {', '.join(KINDS)}")
    if not pattern:
        raise RuleError(f"rule {text!r}: empty pattern")
    if kind == "ext":
        exts = [e.strip().lstrip(".") for e in pattern.split(",") if e.strip()]
        if not exts:
            raise RuleError(f"rule {text!r}: no extensions given")
        return [
            Rule(strength, include, "name", f"*.{e}", text.strip(), layer, order, source)
            for e in exts
        ]
    return [Rule(strength, include, kind, pattern, text.strip(), layer, order, source)]


def parse_rules(texts: list[str], layer: int, source: str) -> list[Rule]:
    out: list[Rule] = []
    for i, t in enumerate(texts):
        out.extend(parse_rule(t, layer, i, source))
    return out


@dataclass
class Decision:
    """Outcome of resolving rules for one file.

    ``include`` is ``None`` when the outcome depends on the (not yet known) MIME
    type; call :func:`decide` again with the MIME type.
    """

    include: bool | None
    winner: Rule | None = None
    matched: list[Rule] = field(default_factory=list)

    @property
    def undetermined(self) -> bool:
        return self.include is None

    def reason(self) -> str:
        if self.include is None:
            return "needs MIME type"
        if self.winner is None:
            return "no matching rule (default: exclude)"
        return self.winner.describe()


def decide(rules: list[Rule], name: str, path: str, mime: str | None, mime_known: bool = True) -> Decision:
    """Resolve ``rules`` for one file.

    With ``mime_known=False`` the MIME rules are treated as unknown. If their result
    could change the outcome, the decision is undetermined (``include is None``).
    """
    matched: list[Rule] = []
    unknown: list[Rule] = []
    for r in rules:
        if r.needs_mime and not mime_known:
            unknown.append(r)
        elif r.matches(name, path, mime):
            matched.append(r)

    winner = max(matched, key=Rule.key) if matched else None
    outcome = winner.include if winner else False

    if unknown:
        wkey = winner.key() if winner else None
        for r in unknown:
            if (wkey is None or r.key() > wkey) and r.include != outcome:
                return Decision(None, None, matched)
    return Decision(outcome, winner, matched)
