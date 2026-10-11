"""agency - CLI for running a productized AI content agency.

Commands:
  outreach   personalized cold emails for a CSV of leads
  proposal   a client proposal from discovery-call notes
  repurpose  one long-form piece -> a week of social content (the core deliverable)
"""

import argparse
import csv
import sys
from pathlib import Path

import anthropic

from . import llm


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    print(f"wrote {path}")


def _offer_block(offer_path: str) -> str:
    return f"<offer>\n{_read(offer_path)}\n</offer>"


def cmd_outreach(args: argparse.Namespace) -> None:
    system = llm.load_prompt("outreach")
    offer = _offer_block(args.offer)

    with open(args.leads, newline="", encoding="utf-8") as f:
        leads = list(csv.DictReader(f))
    if not leads:
        sys.exit(f"No leads found in {args.leads}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(leads[0].keys()) + ["email_draft"]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for i, lead in enumerate(leads, 1):
            lead_info = "\n".join(f"{k}: {v}" for k, v in lead.items() if v)
            print(f"[{i}/{len(leads)}] {lead.get('name') or lead.get('company') or 'lead'}")
            try:
                draft = llm.generate(system, f"{offer}\n\n<lead>\n{lead_info}\n</lead>", effort="low")
            except llm.RefusalError as e:
                draft = f"ERROR: {e}"
            writer.writerow({**lead, "email_draft": draft})
    print(f"wrote {out_path}  (review every draft before sending)")


def cmd_proposal(args: argparse.Namespace) -> None:
    system = llm.load_prompt("proposal")
    content = f"{_offer_block(args.offer)}\n\n<discovery_notes>\n{_read(args.notes)}\n</discovery_notes>"
    _write(Path(args.out), llm.generate(system, content, effort="high"))


def cmd_repurpose(args: argparse.Namespace) -> None:
    system = llm.load_prompt("repurpose")
    parts = []
    if args.brand:
        parts.append(f"<brand_voice>\n{_read(args.brand)}\n</brand_voice>")
    parts.append(f"<source>\n{_read(args.source)}\n</source>")
    result = llm.generate(system, "\n\n".join(parts), effort="high")
    out = Path(args.out) if args.out else Path("output") / f"{Path(args.source).stem}-content-pack.md"
    _write(out, result)


def main() -> None:
    parser = argparse.ArgumentParser(prog="agency", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("outreach", help="Draft personalized cold emails for a CSV of leads")
    p.add_argument("leads", help="CSV with columns like name,company,website,role,notes")
    p.add_argument("--offer", default="examples/offer.md", help="Your offer description")
    p.add_argument("--out", default="output/outreach.csv")
    p.set_defaults(func=cmd_outreach)

    p = sub.add_parser("proposal", help="Write a client proposal from discovery-call notes")
    p.add_argument("notes", help="Text/markdown file of discovery-call notes")
    p.add_argument("--offer", default="examples/offer.md")
    p.add_argument("--out", default="output/proposal.md")
    p.set_defaults(func=cmd_proposal)

    p = sub.add_parser("repurpose", help="Turn one long-form piece into a week of content")
    p.add_argument("source", help="Transcript, blog post, or newsletter (text/markdown)")
    p.add_argument("--brand", help="Client brand-voice file (tone, audience, banned words)")
    p.add_argument("--out", help="Output path (default: output/<source>-content-pack.md)")
    p.set_defaults(func=cmd_repurpose)

    args = parser.parse_args()
    try:
        args.func(args)
    except anthropic.AuthenticationError:
        sys.exit("Invalid or missing API key. Set ANTHROPIC_API_KEY (see .env.example).")
    except anthropic.RateLimitError:
        sys.exit("Rate limited by the API. Wait a minute and re-run.")
    except anthropic.APIConnectionError:
        sys.exit("Network error reaching the Claude API.")
    except anthropic.APIStatusError as e:
        sys.exit(f"API error {e.status_code}: {e.message}")
    except (llm.RefusalError, RuntimeError, FileNotFoundError) as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
