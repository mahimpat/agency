"""letters - write, fact-check, and mail letters from history.

Examples:
  letters figures
  letters write examples/children/maya.json einstein
  letters write examples/children/leo.json amelia-earhart --reply-photo leo-letter.jpg
  letters send output/maya-einstein.html --to examples/addresses/to.json --sender examples/addresses/from.json
"""

import argparse
import json
import sys
from datetime import datetime
from importlib.resources import files
from pathlib import Path

import anthropic

from . import llm, mail, pipeline, render


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
    result = pipeline.write_letter(child, args.figure, reply=reply, history=history)

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

    html_path = out_dir / f"{stem}.html"
    html_path.write_text(render.render_html(result.letter, args.figure, name), encoding="utf-8")
    print(f"{result.status.upper()}: {html_path}  (fact-check rounds: {len(result.checks)})")
    if result.status == "needs_review":
        for issue in result.checks[-1].issues:
            print(f"  - \"{issue.quote}\": {issue.problem}")


def cmd_send(args: argparse.Namespace) -> None:
    html = Path(args.letter).read_text(encoding="utf-8")
    to = json.loads(Path(args.to).read_text(encoding="utf-8"))
    sender = json.loads(Path(args.sender).read_text(encoding="utf-8"))
    result = mail.send_letter(html, to, sender, description=Path(args.letter).stem)
    print(f"Lob letter {result.get('id')} created. Expected delivery: {result.get('expected_delivery_date')}")
    if result.get("url"):
        print(f"PDF proof: {result['url']}")


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
    p.add_argument("--out", default="output")
    p.set_defaults(func=cmd_write)

    p = sub.add_parser("send", help="Mail a rendered letter with Lob")
    p.add_argument("letter", help="Rendered letter .html")
    p.add_argument("--to", required=True, help="Recipient address JSON")
    p.add_argument("--sender", required=True, help="Return address JSON")
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
    except (llm.RefusalError, RuntimeError, ValueError, FileNotFoundError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
