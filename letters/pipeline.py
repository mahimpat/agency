"""The agent pipeline: read the child's reply -> write -> fact-check -> revise."""

import json
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel, Field

from . import llm

MAX_REVISIONS = 2


class Letter(BaseModel):
    greeting: str = Field(description="e.g. 'Dear Maya,'")
    body: list[str] = Field(description="The letter's paragraphs, in order")
    signoff: str = Field(description="Closing line and signature, e.g. 'Your friend across time,\\nAlbert'")
    postscript: str = Field(description="A P.S. that asks the child one question to answer in their reply")
    real_history: list[str] = Field(
        description="3-5 short 'Real history' notes for the back page: true facts behind the letter, "
                    "and anything in the letter that is storytelling rather than fact"
    )


class Issue(BaseModel):
    quote: str = Field(description="The exact words from the letter that have the problem")
    problem: str
    fix: str


class FactCheck(BaseModel):
    passes: bool = Field(description="True only if there are no issues at all")
    issues: list[Issue]


class ChildReply(BaseModel):
    legible: bool = Field(description="False if most of the letter can't be read")
    text: str = Field(description="The child's letter, transcribed as written (keep their spelling)")
    questions: list[str] = Field(description="Every question the child asked, in plain words")
    shared_news: list[str] = Field(description="Things the child told about themselves or their life")
    needs_human: bool = Field(
        description="True if the letter mentions harm, abuse, danger, serious sadness, or anything "
                    "a trusted adult should see before any reply is written"
    )
    needs_human_reason: str = Field(description="Why, or an empty string")


@dataclass
class Result:
    letter: Letter | None
    status: str  # "ready", "needs_review" (fact-check still failing), or "held" (child reply flagged)
    checks: list[FactCheck] = field(default_factory=list)
    reply: ChildReply | None = None
    note: str = ""


def load_child(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_reply(photo: str | Path | None = None, text: str | None = None) -> ChildReply:
    """Transcribe a child's handwritten reply from a photo (or take typed text)."""
    system = llm.load_text("prompts", "read_reply")
    if photo:
        content = [llm.image_block(photo), {"type": "text", "text": "Read this child's letter."}]
    else:
        content = f"<child_letter>\n{text}\n</child_letter>"
    return llm.ask(system, content, ChildReply, effort="low")


def _writer_input(child: dict, fact_sheet: str, reply: ChildReply | None, history: str) -> str:
    parts = [
        f"<figure_fact_sheet>\n{fact_sheet}\n</figure_fact_sheet>",
        f"<child>\n{json.dumps(child, indent=2, ensure_ascii=False)}\n</child>",
    ]
    if history:
        parts.append(f"<previous_letters>\n{history}\n</previous_letters>")
    if reply:
        parts.append(f"<child_reply>\n{reply.model_dump_json(indent=2)}\n</child_reply>")
        parts.append("Write the next letter as a direct, warm answer to the child's reply.")
    else:
        parts.append("Write the first letter to this child.")
    return "\n\n".join(parts)


def write_letter(
    child: dict,
    figure: str,
    reply: ChildReply | None = None,
    history: str = "",
) -> Result:
    if reply and reply.needs_human:
        return Result(None, "held", reply=reply, note=reply.needs_human_reason)
    if reply and not reply.legible:
        return Result(None, "held", reply=reply, note="The child's letter couldn't be read.")

    fact_sheet = llm.load_text("figures", figure)
    writer = llm.load_text("prompts", "writer")
    checker = llm.load_text("prompts", "checker")
    request = _writer_input(child, fact_sheet, reply, history)

    letter = llm.ask(writer, request, Letter, effort="high")
    checks: list[FactCheck] = []
    for attempt in range(MAX_REVISIONS + 1):
        check = llm.ask(
            checker,
            f"<figure_fact_sheet>\n{fact_sheet}\n</figure_fact_sheet>\n\n"
            f"<child_age>{child.get('age')}</child_age>\n"
            f"<child_country>{child.get('country', 'GB')}</child_country>\n\n"
            f"<letter>\n{letter.model_dump_json(indent=2)}\n</letter>",
            FactCheck,
            effort="high",
        )
        checks.append(check)
        if check.passes and not check.issues:
            return Result(letter, "ready", checks, reply)
        if attempt == MAX_REVISIONS:
            break
        letter = llm.ask(
            writer,
            f"{request}\n\n<your_draft>\n{letter.model_dump_json(indent=2)}\n</your_draft>\n\n"
            f"<fact_check_issues>\n{check.model_dump_json(indent=2)}\n</fact_check_issues>\n\n"
            "Rewrite the letter fixing every issue. Keep everything else that works.",
            Letter,
            effort="high",
        )
    return Result(letter, "needs_review", checks, reply, note="Fact-check still found issues; review before mailing.")
