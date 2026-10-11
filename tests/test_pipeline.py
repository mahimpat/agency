"""Pipeline tests with a scripted fake in place of the Claude API."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from letters import cli, llm, pipeline, render

ROOT = Path(__file__).resolve().parent.parent
CHILD = json.loads((ROOT / "examples/children/leo.json").read_text())


def make_letter(tag: str = "v1") -> pipeline.Letter:
    return pipeline.Letter(
        greeting="Dear Leo,",
        body=[f"I flew across the ocean ({tag}).", "My plane was red <and> shiny."],
        signoff="Your friend in the sky,\nAmelia",
        postscript="P.S. What color is your Lego plane?",
        real_history=["Amelia flew solo across the Atlantic in May 1932."],
    )


PASS = pipeline.FactCheck(passes=True, issues=[])
FAIL = pipeline.FactCheck(
    passes=False,
    issues=[pipeline.Issue(quote="ocean", problem="vague", fix="say Atlantic Ocean")],
)


class FakeAsk:
    """Replays queued answers per output type and records every request."""

    def __init__(self, answers: dict):
        self.answers = {k: list(v) for k, v in answers.items()}
        self.calls = []

    def __call__(self, system, content, output, effort="medium"):
        self.calls.append((output.__name__, content))
        return self.answers[output].pop(0)


class WriteLetterTests(unittest.TestCase):
    def run_with(self, answers, **kwargs):
        fake = FakeAsk(answers)
        with mock.patch.object(llm, "ask", fake):
            result = pipeline.write_letter(CHILD, "amelia-earhart", **kwargs)
        return result, fake

    def test_ready_when_first_check_passes(self):
        result, fake = self.run_with({pipeline.Letter: [make_letter()], pipeline.FactCheck: [PASS]})
        self.assertEqual(result.status, "ready")
        self.assertEqual([c[0] for c in fake.calls], ["Letter", "FactCheck"])
        # The writer saw the real fact sheet and the child's profile.
        self.assertIn("Amelia Earhart", fake.calls[0][1])
        self.assertIn('"name": "Leo"', fake.calls[0][1])

    def test_revises_until_check_passes(self):
        result, fake = self.run_with({
            pipeline.Letter: [make_letter("v1"), make_letter("v2")],
            pipeline.FactCheck: [FAIL, PASS],
        })
        self.assertEqual(result.status, "ready")
        self.assertIn("v2", result.letter.body[0])
        self.assertEqual([c[0] for c in fake.calls], ["Letter", "FactCheck", "Letter", "FactCheck"])
        self.assertIn("say Atlantic Ocean", fake.calls[2][1])  # revision received the issues

    def test_needs_review_after_max_revisions(self):
        n = pipeline.MAX_REVISIONS
        result, fake = self.run_with({
            pipeline.Letter: [make_letter(f"v{i}") for i in range(n + 1)],
            pipeline.FactCheck: [FAIL] * (n + 1),
        })
        self.assertEqual(result.status, "needs_review")
        self.assertEqual(len(result.checks), n + 1)

    def test_flagged_reply_is_held_without_writing(self):
        reply = pipeline.ChildReply(
            legible=True, text="...", questions=[], shared_news=[],
            needs_human=True, needs_human_reason="mentions being hurt",
        )
        result, fake = self.run_with({}, reply=reply)
        self.assertEqual(result.status, "held")
        self.assertEqual(fake.calls, [])

    def test_reply_questions_reach_the_writer(self):
        reply = pipeline.ChildReply(
            legible=True, text="wer you scard", questions=["Were you scared?"],
            shared_news=["Lost first tooth"], needs_human=False, needs_human_reason="",
        )
        result, fake = self.run_with(
            {pipeline.Letter: [make_letter()], pipeline.FactCheck: [PASS]}, reply=reply)
        self.assertIn("Were you scared?", fake.calls[0][1])
        self.assertIn("answer to the child's reply", fake.calls[0][1])


class RenderTests(unittest.TestCase):
    def test_render_escapes_and_includes_back_page(self):
        html = render.render_html(make_letter(), "amelia-earhart", "Leo")
        self.assertIn("&lt;and&gt;", html)
        self.assertIn("Real history", html)
        self.assertIn("P.S. What color", html)
        self.assertNotIn("P.S. P.S.", html)


class CliTests(unittest.TestCase):
    def test_write_command_outputs_html_and_record(self):
        fake = FakeAsk({
            pipeline.ChildReply: [pipeline.ChildReply(
                legible=True, text="hi", questions=["What did you eat?"], shared_news=[],
                needs_human=False, needs_human_reason="")],
            pipeline.Letter: [make_letter()],
            pipeline.FactCheck: [PASS],
        })
        with tempfile.TemporaryDirectory() as out, mock.patch.object(llm, "ask", fake):
            argv = ["letters", "write", str(ROOT / "examples/children/leo.json"), "amelia-earhart",
                    "--reply-text", "what did you eat", "--out", out]
            with mock.patch.object(sys, "argv", argv):
                cli.main()
            files = sorted(p.suffix for p in Path(out).iterdir())
            self.assertEqual(files, [".html", ".json"])
            record = json.loads(next(Path(out).glob("*.json")).read_text())
            self.assertEqual(record["status"], "ready")


class FigureSheetTests(unittest.TestCase):
    def test_every_figure_has_required_sections_and_display_name(self):
        for sheet in (ROOT / "letters/figures").glob("*.md"):
            text = sheet.read_text()
            for section in ("## Write from", "## Verified facts", "## Handle with care"):
                self.assertIn(section, text, f"{sheet.name} missing {section}")
            self.assertIn(sheet.stem, render.FIGURE_NAMES)


if __name__ == "__main__":
    unittest.main()


class UkPrintTests(unittest.TestCase):
    def test_a4_with_clear_address_window_by_default(self):
        html = render.render_html(make_letter(), "einstein", "Leo")
        self.assertIn("size: A4", html)
        self.assertIn('class="window"></div>', html)  # left clear for the print service
        self.assertIn("with-window", html)

    def test_home_print_fills_window_and_us_letter_has_none(self):
        html = render.render_html(make_letter(), "einstein", "Leo", address_lines=["Leo's Family", "1 Example St"])
        self.assertIn("Leo&#x27;s Family<br>1 Example St", html)
        us = render.render_html(make_letter(), "einstein", "Leo", paper="LETTER", window=False)
        self.assertIn("size: 8.5in 11in", us)
        self.assertNotIn('class="window"', us)

    def test_stannp_request_is_test_mode_unless_live(self):
        from letters import mail
        to = {"name": "Maya Smith", "line1": "1 Example Street", "town": "Bristol", "postcode": "BS1 4ST"}
        fields = mail.stannp_fields(b"%PDF-1.4", to, live=False, first_class=False)
        self.assertEqual(fields["test"], "true")
        self.assertEqual(fields["size"], "A4")
        self.assertEqual(fields["recipient[firstname]"], "Maya")
        self.assertEqual(fields["recipient[lastname]"], "Smith")
        self.assertEqual(fields["recipient[country]"], "GB")
        self.assertNotIn("addons", fields)
        live = mail.stannp_fields(b"%PDF-1.4", to, live=True, first_class=True)
        self.assertEqual(live["test"], "false")
        self.assertEqual(live["addons"], "FIRST_CLASS")
