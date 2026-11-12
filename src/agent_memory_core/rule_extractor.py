"""Deterministic rule-based fact extraction: no model, no network, no downloads.

Scope is explicit and narrow: regex template rules that operators can extend
with their own JSON rule files. Builtin rules cover common Chinese
room/preference utterances so ``POST /v1/memories/process`` works with a text
payload and *zero* model infrastructure.

Output matches the shared fact contract used by every extractor:
``{subject_id | subject_name, predicate, value: dict, confidence: 0..1}``.
"""
from __future__ import annotations

import json
import re
from typing import Iterable

ROOM_WORDS = ("主卧", "次卧", "卧室", "客厅", "书房", "厨房", "卫生间", "浴室", "阳台", "玄关", "儿童房")
_ROOMS = "|".join(ROOM_WORDS)

BUILTIN_RULES = [
    {
        "name": "noise_quiet",
        "regex": r"[^。！？；\n]{0,12}"
                 r"(?:保持安静|安静一点|安静些|安静|静音|小声|别吵|不要吵|太吵)",
        "predicate": "noise_preference",
        "value": {"level": "quiet"},
        "confidence": 0.7,
    },
    {
        "name": "light_dim",
        "regex": r"[^。！？；\n]{0,8}"
                 r"(?:灯光|光线|照明|灯)[^。！？；\n]{0,8}"
                 r"(?:调暗|暗一点|暗些|柔和|不要太亮|别太亮|刺眼|太亮|太强)",
        "predicate": "light_preference",
        "value": {"level": "dim"},
        "confidence": 0.7,
    },
    {
        "name": "light_bright",
        "regex": r"[^。！？；\n]{0,8}"
                 r"(?:灯光|光线|照明|灯)[^。！？；\n]{0,8}"
                 r"(?:调亮|亮一点|亮些|开灯|太暗)",
        "predicate": "light_preference",
        "value": {"level": "bright"},
        "confidence": 0.7,
    },
    {
        "name": "temperature_celsius",
        "regex": r"[^。！？；\n]{0,8}"
                 r"(?:空调|温度|暖气|制冷|制热)[^\d。！？；\n]{0,6}"
                 r"(?P<celsius>\d{1,2})\s*(?:度|℃)",
        "predicate": "temperature_preference",
        "value": {"celsius": "{celsius:int}"},
        "confidence": 0.75,
    },
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


def _room_in_sentence(text: str, start: int, end: int) -> str | None:
    """Return the room word mentioned in the sentence containing the match."""
    hits = [(text.find(word, start, end), word) for word in ROOM_WORDS]
    hits = [(pos, word) for pos, word in hits if pos >= 0]
    if not hits:
        return None
    return min(hits)[1]


class RuleFactExtractor:
    """Template extractor: deterministic, offline, no model weights."""

    def __init__(self, *, rules: Iterable[dict] | None = None,
                 default_subject: str = "user", confidence: float = 0.7):
        if not isinstance(default_subject, str) or not default_subject.strip():
            raise ValueError("default_subject must be a non-empty string")
        self.default_subject = default_subject.strip()
        source = BUILTIN_RULES if rules is None else rules
        self.rules = [_compile_rule(rule, i) for i, rule in enumerate(source)]

    @classmethod
    def from_file(cls, path: str, **kwargs) -> "RuleFactExtractor":
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
        rules = payload.get("rules") if isinstance(payload, dict) else payload
        if not isinstance(rules, list) or not rules:
            raise RuleExtractorConfigError("rule file must contain a non-empty 'rules' array")
        return cls(rules=rules, **kwargs)

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
                    subject = _room_in_sentence(text, *span) or self.default_subject
                confidence = rule["confidence"] if subject != self.default_subject else min(rule["confidence"], 0.55)
                candidate = {"subject_id": subject, "predicate": rule["predicate"],
                             "value": value, "confidence": confidence}
                key = (subject, rule["predicate"])
                if key not in facts or facts[key]["confidence"] < confidence:
                    facts[key] = candidate
        return list(facts.values())
