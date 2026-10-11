"""letters - write, fact-check, and mail letters from history.

Examples:
  letters figures
  letters write examples/children/maya.json einstein
  letters write examples/children/leo.json amelia-earhart --reply-photo leo-letter.jpg
  letters write examples/children/maya.json einstein --print-address examples/addresses/maya.json
  letters send output/<letter>.pdf --to examples/addresses/maya.json            # Stannp test proof
  letters send output/<letter>.pdf --to examples/addresses/maya.json --live     # really post it
"""

import argparse
import json
import sys
from datetime import datetime
from importlib.resources import files
from pathlib import Path

import anthropic

from . import llm, mail, pdf, pipeline, render


def cmd_figures(args: argparse.Namespace) -> None:
    for f in sorted(files("letters.figures").iterdir()):
        if f.name.endswith(".md"):
            print(f.name.removesuffix(".md"))


def cmd_write(args: argparse.Namespace) -> None:
    child = pipeline.load_child(args.child)
    name = child.get("name", "child")

    reply = None
    if args.reply_photo or args.reply_text:
        print("Reading the child's letter...")
        reply = pipeline.read_reply(photo=args.reply_photo, text=args.reply_text)
        print(f"  questions: {reply.questions or 'none'}")

    history = Path(args.history).read_text(encoding="utf-8") if args.history else ""
    print(f"Writing {args.figure}'s letter to {name} and fact-checking it...")
    result = pipeline.write_letter(child, args.figure, reply=reply, history=history, kind=args.kind)

    stem = f"{name.lower()}-{args.figure}-{datetime.now():%Y%m%d-%H%M%S}"
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    record = {
        "status": result.status,
        "note": result.note,
        "figure": args.figure,
        "child": name,
        "reply": result.reply.model_dump() if result.reply else None,
        "letter": result.letter.model_dump() if result.letter else None,
        "fact_checks": [c.model_dump() for c in result.checks],
    }
    (out_dir / f"{stem}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

    if result.status == "held":
        print(f"HELD for a human: {result.note}\n  details: {out_dir / (stem + '.json')}")
        sys.exit(2)

    us = child.get("country", "GB").upper() == "US"
    address_lines = None
    if args.print_address:
        a = json.loads(Path(args.print_address).read_text(encoding="utf-8"))
        address_lines = [a["name"], a["line1"], a.get("line2", ""), a["town"], a["postcode"]]
        address_lines = [x for x in address_lines if x]
    html = render.render_html(result.letter, args.figure, name,
                              paper="LETTER" if us else "A4", window=not us, address_lines=address_lines)
    html_path = out_dir / f"{stem}.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"{result.status.upper()}: {html_path}  (fact-check rounds: {len(result.checks)})")
    if pdf.find_chrome():
        pages = pdf.html_to_pdf(html, out_dir / f"{stem}.pdf")
        print(f"  PDF: {out_dir / (stem + '.pdf')} ({pages} pages{'' if pages == 2 else ' - expected 2, check it'})")
    else:
        print("  (Install Chrome or set CHROME=... to also get a print-ready PDF.)")
    if result.status == "needs_review":
        for issue in result.checks[-1].issues:
            print(f"  - \"{issue.quote}\": {issue.problem}")


def cmd_send(args: argparse.Namespace) -> None:
    letter = Path(args.letter)
    to = json.loads(Path(args.to).read_text(encoding="utf-8"))
    if args.provider == "stannp":
        if letter.suffix == ".html":
            pdf_path = letter.with_suffix(".pdf")
            pdf.html_to_pdf(letter.read_text(encoding="utf-8"), pdf_path)
            letter = pdf_path
        result = mail.send_stannp(letter, to, live=args.live, first_class=args.first_class)
    else:
        if not args.sender:
            sys.exit("Lob needs a return address: --sender address.json")
        sender = json.loads(Path(args.sender).read_text(encoding="utf-8"))
        html = letter.with_suffix(".html").read_text(encoding="utf-8")
        result = mail.send_lob(html, to, sender, description=letter.stem, live=args.live)
    mode = "LIVE: will be printed and posted" if args.live else "TEST: proof only, nothing posted"
    print(f"{args.provider} letter {result['id']} created ({mode}).")
    if result.get("cost"):
        print(f"  cost: {result['cost']}")
    if result.get("proof"):
        print(f"  proof PDF: {result['proof']}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="letters", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("figures", help="List available historical figures")
    p.set_defaults(func=cmd_figures)

    p = sub.add_parser("write", help="Write and fact-check a letter")
    p.add_argument("child", help="Child profile JSON (see examples/children)")
    p.add_argument("figure", help="Figure id, e.g. einstein (see `letters figures`)")
    reply = p.add_mutually_exclusive_group()
    reply.add_argument("--reply-photo", help="Photo of the child's handwritten reply")
    reply.add_argument("--reply-text", help="The child's reply, typed")
    p.add_argument("--history", help="Text file with previous letters to this child")
    p.add_argument("--kind", default="story", choices=["intro", "story", "follow_up", "farewell"],
                   help="Which letter in the friendship (a story with no --history is written as an intro)")
    p.add_argument("--print-address", help="Address JSON to print in the window (for printing and posting it yourself)")
    p.add_argument("--out", default="output")
    p.set_defaults(func=cmd_write)

    p = sub.add_parser("send", help="Print and post a letter (test proof unless --live)")
    p.add_argument("letter", help="Rendered letter .pdf (or .html)")
    p.add_argument("--to", required=True, help="Recipient address JSON")
    p.add_argument("--provider", choices=["stannp", "lob"], default="stannp", help="stannp = UK, lob = US")
    p.add_argument("--sender", help="Return address JSON (Lob only)")
    p.add_argument("--first-class", action="store_true", help="Royal Mail 1st class (Stannp add-on)")
    p.add_argument("--live", action="store_true", help="Really print and post it (costs money)")
    p.set_defaults(func=cmd_send)

    args = parser.parse_args()
    try:
        args.func(args)
    except anthropic.AuthenticationError:
        sys.exit("Missing or invalid Claude API key. Set ANTHROPIC_API_KEY (see .env.example).")
    except anthropic.RateLimitError:
        sys.exit("Rate limited by the Claude API. Wait a minute and try again.")
    except anthropic.APIConnectionError:
        sys.exit("Couldn't reach the Claude API. Check your internet connection.")
    except anthropic.APIStatusError as e:
        sys.exit(f"Claude API error {e.status_code}: {e.message}")
    except (llm.RefusalError, RuntimeError, ValueError, FileNotFoundError, KeyError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
