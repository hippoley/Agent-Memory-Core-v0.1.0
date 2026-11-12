"""Deterministic rule-based fact extraction: no model, no network, no downloads.

Scope is explicit and narrow: regex template rules that operators supply or
extend through JSON rule files. Nothing in this module is domain specific —
the built-in rules cover only generic personal preferences (likes, dislikes,
allergies) so ``POST /v1/memories/process`` works with a text payload and
*zero* model infrastructure.

Domain vocabularies (rooms, devices, tickets, accounts, ...) are never baked
into the code. Load one of the example packs under ``resources/rules`` with
``RuleFactExtractor.from_preset("home-zh")``, or point ``MEMORY_RULE_PATTERNS``
at your own JSON file, which supplies both ``subjects`` and ``rules``.

Output matches the shared fact contract used by every extractor:
``{subject_id | subject_name, predicate, value: dict, confidence: 0..1}``.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

# Domain vocabularies are configuration, never code. An extractor built with no
# ``subject_words`` attributes every fact to ``default_subject``; supply your own
# lexicon (or a rule pack) when a domain has real subjects to distinguish.
SUBJECT_WORDS: tuple[str, ...] = ()

_RULES_DIR = Path(__file__).with_name("resources") / "rules"

DEFAULT_RULES = [
    {
        "name": "dislikes",
        "regex": r"(?:我)?(?:不喜欢|讨厌|受不了|不爱吃|不爱喝|不吃|不喝)"
                 r"(?P<item>[^，。！？、\s]{1,16})",
        "predicate": "dislikes",
        "value": {"item": "{item}"},
        "confidence": 0.7,
    },
    {
        "name": "allergy",
        "regex": r"(?:我)?(?:对)?(?P<item>[^，。！？、\s]{1,16})(?:过敏)",
        "predicate": "allergy",
        "value": {"item": "{item}"},
        "confidence": 0.8,
    },
    {
        "name": "likes",
        # Negative lookbehind: "我不喜欢太吵" is a dislike, not a like. The
        # zero-model path must not invert polarity, or the ledger records the
        # opposite of what the user said.
        "regex": r"(?:我)?(?<!不)(?:喜欢|偏爱|爱吃|爱喝|更爱)(?P<item>[^，。！？、\s]{1,16})",
        "predicate": "likes",
        "value": {"item": "{item}"},
        "confidence": 0.65,
    },
]

_TEMPLATE = re.compile(r"^\{(?P<group>[a-zA-Z_][a-zA-Z0-9_]*)(?::(?P<cast>int|float))?\}$")
_PREDICATE = re.compile(r"[a-z][a-z0-9_]{0,99}")


class RuleExtractorConfigError(ValueError):
    """Raised when a custom rule file violates the rule contract."""


def _compile_rule(rule: dict, index: int) -> dict:
    if not isinstance(rule, dict):
        raise RuleExtractorConfigError(f"rule #{index} must be an object")
    unknown = set(rule) - {"name", "regex", "predicate", "value", "confidence"}
    if unknown:
        raise RuleExtractorConfigError(f"rule #{index} has unknown fields: {sorted(unknown)}")
    regex = rule.get("regex")
    predicate = rule.get("predicate")
    value = rule.get("value")
    confidence = rule.get("confidence", 0.6)
    if not isinstance(regex, str) or not regex:
        raise RuleExtractorConfigError(f"rule #{index} requires a regex")
    try:
        compiled = re.compile(regex)
    except re.error as exc:
        raise RuleExtractorConfigError(f"rule #{index} regex does not compile: {exc}") from exc
    if not isinstance(predicate, str) or not _PREDICATE.fullmatch(predicate):
        raise RuleExtractorConfigError(f"rule #{index} predicate must be snake_case")
    if not isinstance(value, dict) or not value:
        raise RuleExtractorConfigError(f"rule #{index} value must be a non-empty object")
    for key, template in value.items():
        if not isinstance(key, str):
            raise RuleExtractorConfigError(f"rule #{index} value keys must be strings")
        if isinstance(template, bool) or not isinstance(template, (str, int, float)):
            raise RuleExtractorConfigError(
                f"rule #{index} value entries must be scalars or '{{group}}' templates")
        if isinstance(template, str) and "{" in template and not _TEMPLATE.fullmatch(template):
            raise RuleExtractorConfigError(
                f"rule #{index} value entries must be '{{group}}', '{{group:int}}' or '{{group:float}}'")
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 < confidence <= 1:
        raise RuleExtractorConfigError(f"rule #{index} confidence must be within (0,1]")
    return {"name": rule.get("name") or f"rule_{index}", "regex": compiled,
            "predicate": predicate, "value": dict(value), "confidence": float(confidence)}


def _render_value(template: dict, match: re.Match) -> dict | None:
    rendered = {}
    for key, spec in template.items():
        if not isinstance(spec, str):
            rendered[key] = spec
            continue
        group = _TEMPLATE.fullmatch(spec)
        if group is None:
            rendered[key] = spec
            continue
        raw = match.group(group.group("group"))
        if raw is None:
            return None
        cast = group.group("cast")
        if cast == "int":
            try:
                rendered[key] = int(raw)
            except ValueError:
                return None
        elif cast == "float":
            try:
                rendered[key] = float(raw)
            except ValueError:
                return None
        else:
            rendered[key] = raw
    return rendered


_SENTENCE_SPLIT = re.compile(r"[^。！？；\n]+")


def _sentences(text: str):
    """Yield (start, end) spans of sentence-like fragments."""
    for match in _SENTENCE_SPLIT.finditer(text):
        yield match.start(), match.end()


def _subject_in_sentence(text: str, start: int, end: int,
                         subject_words: Iterable[str]) -> str | None:
    """Return the configured subject word mentioned in the matching sentence."""
    hits = [(text.find(word, start, end), word) for word in subject_words]
    hits = [(pos, word) for pos, word in hits if pos >= 0]
    if not hits:
        return None
    return min(hits)[1]


def available_presets() -> list[str]:
    """Names of the example rule packs shipped with the package."""
    if not _RULES_DIR.is_dir():
        return []
    return sorted(path.stem for path in _RULES_DIR.glob("*.json"))


def _load_rule_document(path: Path) -> tuple[list[dict], list[str]]:
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)
    rules = payload.get("rules") if isinstance(payload, dict) else payload
    if not isinstance(rules, list) or not rules:
        raise RuleExtractorConfigError(
            f"rule file must contain a non-empty 'rules' array: {path}")
    subjects = payload.get("subjects", []) if isinstance(payload, dict) else []
    if not isinstance(subjects, list) or any(not isinstance(s, str) or not s for s in subjects):
        raise RuleExtractorConfigError("'subjects' must be an array of non-empty strings")
    return rules, list(subjects)


class RuleFactExtractor:
    """Template extractor: deterministic, offline, no model weights."""

    def __init__(self, *, rules: Iterable[dict] | None = None,
                 default_subject: str = "user", confidence: float = 0.7,
                 subject_words: Iterable[str] | None = None,
                 include_defaults: bool = True):
        if not isinstance(default_subject, str) or not default_subject.strip():
            raise ValueError("default_subject must be a non-empty string")
        self.default_subject = default_subject.strip()
        words = list(SUBJECT_WORDS if subject_words is None else subject_words)
        if any(not isinstance(word, str) or not word for word in words):
            raise RuleExtractorConfigError("subject_words must be non-empty strings")
        self.subject_words = tuple(words)
        if rules is None:
            source = list(DEFAULT_RULES)
        else:
            supplied = list(rules)
            if not include_defaults:
                source = supplied
            else:
                # A domain pack adds rules; it must not silently drop the generic
                # preference rules every caller relies on. Same-name rules win.
                declared = {r.get("name") for r in supplied if isinstance(r, dict)}
                source = supplied + [r for r in DEFAULT_RULES if r["name"] not in declared]
        self.rules = [_compile_rule(rule, i) for i, rule in enumerate(source)]

    @classmethod
    def from_file(cls, path: str, **kwargs) -> "RuleFactExtractor":
        rules, subjects = _load_rule_document(Path(path))
        kwargs.setdefault("subject_words", subjects)
        return cls(rules=rules, **kwargs)

    @classmethod
    def from_preset(cls, name: str, **kwargs) -> "RuleFactExtractor":
        """Load one of the shipped example packs, e.g. ``home-zh``."""
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name or ""):
            raise RuleExtractorConfigError(f"invalid preset name: {name!r}")
        path = _RULES_DIR / f"{name}.json"
        if not path.is_file():
            raise RuleExtractorConfigError(
                f"unknown rule preset {name!r}; available: {available_presets()}")
        return cls.from_file(str(path), **kwargs)

    def __call__(self, text: str) -> list[dict]:
        if not isinstance(text, str) or not text.strip() or len(text) > 100000:
            raise ValueError("Nonempty text up to 100000 characters required")
        spans = list(_sentences(text))
        facts: dict[tuple[str, str], dict] = {}
        for rule in self.rules:
            for match in rule["regex"].finditer(text):
                value = _render_value(rule["value"], match)
                if value is None:
                    continue
                groups = match.groupdict()
                subject = (groups.get("subject") or "").strip()
                if not subject:
                    span = next(((s, e) for s, e in spans if s <= match.start() < e), (0, len(text)))
                    subject = _subject_in_sentence(text, *span, self.subject_words) or self.default_subject
                confidence = rule["confidence"] if subject != self.default_subject else min(rule["confidence"], 0.55)
                candidate = {"subject_id": subject, "predicate": rule["predicate"],
                             "value": value, "confidence": confidence}
                key = (subject, rule["predicate"])
                if key not in facts or facts[key]["confidence"] < confidence:
                    facts[key] = candidate
        return list(facts.values())
