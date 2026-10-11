# Letters from History

**History writes back.** Children get real paper letters from historical figures that are written for them. UK pen pals include Mary Anning, Isambard Kingdom Brunel, Ada Lovelace, Florence Nightingale, Charles Darwin, Mary Seacole and William Shakespeare, alongside Einstein, Amelia Earhart, Leonardo da Vinci and Cleopatra. When a child writes back, the next letter answers their questions.

AI agents write each letter, fact-check it, render it on era-appropriate stationery and mail it. This repo is the **demand-test kit**: everything needed to find out whether parents will pay before building the full product.

## What's here

| Path | What it is |
|---|---|
| `docs/market-research-letters-from-history.md` | Competitors, market size, unit economics, risks |
| `docs/validation-playbook.md` | **Start here.** A 3-week, under-£150 plan to test demand |
| `docs/uk-launch.md` | UK printing and posting (Stannp), Royal Mail costs, unit economics in £, UK rules |
| `docs/launch-guide.md` | **Full product launch:** Stripe, Stannp, Supabase, hosting, go-live checklist |
| `docs/safeguarding.md` | Safeguarding policy: how replies are checked and what to do when one is held |
| `app/` | The web app: accounts, Stripe subscriptions, daily letter job, admin review, privacy policy and terms |
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
- **Reply reader + safeguarding:** transcribes the child's handwriting and runs two independent safety checks (an AI risk assessment and a rule-based screen that can only add caution). Personal details are removed. Anything worrying is **held for a person** and no automatic reply is written (see `docs/safeguarding.md`).

## Run the web app

```bash
pip install -e ".[dev]"
uvicorn app.web:app --reload              # http://localhost:8000
python -m app.manage make-admin you@example.com
```

See `docs/launch-guide.md` for Stripe, Stannp, hosting and going live.

Built in for launch:
- email verification before payment, and optional Sign in with Google;
- rate limits on login, sign-up, password reset and uploads;
- a lock so two daily runs can't overlap;
- Alembic migrations;
- local or S3-compatible file storage;
- optional Sentry error alerts (no personal data sent);
- self-hosted fonts;
- automatic 12-month data retention.

## Setup (letter pipeline on its own)

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
