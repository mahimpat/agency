"""Safeguarding checks on children's replies, independent of the AI.

This is a deliberately simple, conservative backstop. It does not decide that
a letter is safe; it can only make a reply *more* likely to be held for a
person. Wording that looks worrying always reaches a human, even if the AI
assessment missed it. False alarms are expected and fine: a person reads them.
"""

import re
from dataclasses import dataclass, field

# Personal details a child (or a letter photo) might include. Removed before
# anything is stored or shown to the letter writer.
PII_PATTERNS = {
    "email address": re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
    "phone number": re.compile(r"(?:\+44\s?|\b0)\d(?:[\s-]?\d){8,9}\b"),
    "postcode": re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", re.IGNORECASE),
    "web or social link": re.compile(r"(?:https?://|www\.)\S+|(?<!\w)@[A-Za-z0-9_.]{3,}"),
    "street address": re.compile(r"\b\d{1,4}\s+[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?\s+(?:Road|Rd|Street|St|Lane|Ln|Avenue|Ave|Close|Drive|Dr|Way|Crescent|Gardens|Grove|Place|Terrace|Court)\b"),
}

# Words that must always reach a person. Grouped so the reviewer sees why.
RISK_PATTERNS = {
    "possible harm to the child": r"\b(hurts?|hurting|hit|hits|hitting|beat|beats|kicks?|slaps?|smack(?:s|ed)?|punch(?:es|ed)?)\s+me\b|\bhurt\s+myself\b|\bbruises?\b",
    "self-harm or suicide": r"\b(kill\s+myself|want\s+to\s+die|wish\s+i\s+(?:was|were)\s+dead|suicid\w*|self[\s-]?harm|cut(?:ting)?\s+myself)\b",
    "secrets or grooming signs": r"\b(our\s+secret|keep\s+(?:it\s+|this\s+)?(?:a\s+)?secret|don'?t\s+tell\s+(?:anyone|mum|mom|dad|my\s+parents)|touch(?:es|ed|ing)?\s+me|private\s+parts?)\b",
    "fear at home or school": r"\b(scared|afraid|frightened)\s+(?:of|at)\s+(?:home|school|my\s+(?:mum|mom|dad|step\w*|brother|sister|teacher|uncle|aunt))\b|\brun\s+away\b|\bnot\s+safe\b|\bunsafe\b",
    "neglect": r"\b(no\s+food|haven'?t\s+eaten|always\s+hungry|nobody\s+(?:looks\s+after|cares\s+about|loves)\s+me|left\s+(?:alone|on\s+my\s+own)\s+all)\b",
    "bullying": r"\b(bull(?:y|ies|ied|ying))\b",
}
_RISK = {name: re.compile(pattern, re.IGNORECASE) for name, pattern in RISK_PATTERNS.items()}


@dataclass
class Screen:
    risk_reasons: list[str] = field(default_factory=list)
    personal_info: list[str] = field(default_factory=list)

    @property
    def must_hold(self) -> bool:
        return bool(self.risk_reasons)


def screen(text: str) -> Screen:
    result = Screen()
    for name, pattern in _RISK.items():
        if pattern.search(text):
            result.risk_reasons.append(name)
    for name, pattern in PII_PATTERNS.items():
        if pattern.search(text):
            result.personal_info.append(name)
    return result


def redact(text: str) -> str:
    for pattern in PII_PATTERNS.values():
        text = pattern.sub("[removed]", text)
    return text
