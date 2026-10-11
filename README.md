# Letters from History

**History writes back.** Kids get real paper letters from historical figures (Einstein, Amelia Earhart, Cleopatra, Leonardo da Vinci) that are written for them. When a child writes back, the next letter answers their questions.

AI agents write each letter, fact-check it, render it on era-appropriate stationery and mail it. This repo is the **demand-test kit**: everything needed to find out whether parents will pay before building the full product.

## What's here

| Path | What it is |
|---|---|
| `docs/market-research-letters-from-history.md` | Competitors, market size, unit economics, risks |
| `docs/validation-playbook.md` | **Start here.** A 3-week, under-£150 plan to test demand |
| `docs/uk-launch.md` | UK printing and posting (Stannp), Royal Mail costs, unit economics in £, UK rules |
| `site/` | Landing page with waitlist (deploy the folder to Netlify; the form works with no setup) |
| `samples/` | Four sample letters (HTML + print-ready A4 PDF), one per figure |
| `letters/` | The agent pipeline: read the child's reply, write, fact-check, revise, render to PDF, post |
| `letters/figures/` | Verified fact sheets: the only facts letters may use, plus "handle with care" rules |
| `letters/prompts/` | Instructions for the writer, the fact-checker and the reply reader (plain text, editable) |

## How the agents work

```
child's reply photo ──▶ Reply reader ──(flags anything worrying for a human)──┐
                                                                              ▼
child profile + figure fact sheet ──────────────────────────────────▶ Writer ──▶ Fact-checker
                                                                       ▲              │
                                                                       └── revise ◀───┘ (up to 2x)
                                                                                      │
                                              ready / needs_review ──▶ Render A4 PDF ──▶ Stannp (UK print + Royal Mail)
```

- **Writer:** writes in the figure's voice, at the child's reading level, using only facts from the figure's fact sheet.
- **Fact-checker:** an independent pass that flags unsupported facts, invented quotes, spoilers (a figure knowing their own future), child-safety problems and reading level.
- **Reply reader:** transcribes the child's handwriting from a photo and lists their questions. If anything suggests the child might need help, the letter is **held for a human** and no automatic reply is written.

## Setup

```bash
pip install -e .
export ANTHROPIC_API_KEY=sk-ant-...      # https://console.anthropic.com
export STANNP_API_KEY=...                # https://www.stannp.com (UK print and post)
```

## Usage

```bash
letters figures                                         # list figures
letters write examples/children/maya.json einstein      # first letter
letters write examples/children/leo.json amelia-earhart --reply-text "$(cat examples/replies/leo-reply.txt)"
letters write examples/children/leo.json amelia-earhart --reply-photo leo-letter.jpg
letters write examples/children/maya.json einstein --print-address examples/addresses/maya.json   # to post it yourself
letters send output/<letter>.pdf --to examples/addresses/maya.json          # Stannp TEST proof (free, nothing posted)
letters send output/<letter>.pdf --to examples/addresses/maya.json --live   # really print and post it
```

Each `write` saves:
- an A4 `.html` letter and a print-ready `.pdf` (needs Chrome or Chromium; set `CHROME=/path` if it isn't found),
- a `.json` record with the fact-check results.

The status is `ready`, `needs_review` (the fact-check still found issues, so read it first), or `held` (a human must read the child's letter).

Printing and posting:
- `send` uses **Stannp** (UK) and needs `STANNP_API_KEY`. It runs in **test mode unless you pass `--live`**: you get a PDF proof and nothing is posted or charged. If your dashboard shows a different API URL, set `STANNP_API_URL`.
- For the US, set the child's `"country": "US"` (US Letter paper, American English) and use `--provider lob --sender address.json` with `LOB_API_KEY`.

## Development

```bash
python -m unittest discover -s tests     # pipeline tests (fake model, no API key needed)
python samples/build.py                  # re-render sample letters (set CHROME=/path/to/chrome for PDFs)
```

To add a historical figure:
1. Add `letters/figures/<id>.md` using the same sections as the existing sheets.
2. Add a stationery theme for it in `letters/render.py`.
