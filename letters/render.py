"""Render a Letter as a printable, mail-ready HTML page (US Letter, front + back).

Each figure gets stationery that fits their era, so a letter looks like a real
object: a typed page from 1950s Princeton, an airmail note, papyrus, parchment.
Fonts are embedded so the file prints identically anywhere (including Lob).
"""

import base64
from functools import cache
from html import escape
from importlib.resources import files

from .pipeline import Letter

FONT_FILES = {
    "Typewriter": ("special-elite.woff2", "normal"),
    "Hand": ("caveat.woff2", "normal"),
    "Fell": ("im-fell.woff2", "normal"),
    "FellItalic": ("im-fell-italic.woff2", "normal"),
    "Signature": ("homemade-apple.woff2", "normal"),
}

# Fine paper grain: SVG fractal noise, tinted per theme with a multiply blend.
GRAIN = (
    "data:image/svg+xml;utf8,"
    "<svg xmlns='http://www.w3.org/2000/svg' width='300' height='300'>"
    "<filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='3' stitchTiles='stitch'/>"
    "<feColorMatrix values='0 0 0 0 .45  0 0 0 0 .38  0 0 0 0 .28  0 0 0 .22 0'/></filter>"
    "<rect width='100%' height='100%' filter='url(%23n)'/></svg>"
)

# Papyrus: stretched noise reads as long plant fibers; two layers cross like woven strips.
def _fibers(freq: str, alpha: str) -> str:
    return (
        "data:image/svg+xml;utf8,"
        "<svg xmlns='http://www.w3.org/2000/svg' width='400' height='400'>"
        f"<filter id='f'><feTurbulence type='fractalNoise' baseFrequency='{freq}' numOctaves='2' stitchTiles='stitch'/>"
        f"<feColorMatrix values='0 0 0 0 .42  0 0 0 0 .29  0 0 0 0 .12  0 0 0 {alpha} -.05'/></filter>"
        "<rect width='100%' height='100%' filter='url(%23f)'/></svg>"
    )


FIBERS_H = _fibers(".004 .22", ".55")
FIBERS_V = _fibers(".25 .004", ".3")

# A few loose sepia strokes in the margin: a bird in flight, like Leonardo's flight studies.
BIRD_SKETCH = """<svg class="sketch" viewBox="0 0 220 160" aria-hidden="true">
<g fill="none" stroke="currentColor" stroke-linecap="round">
<path d="M20 92 C 55 60, 85 58, 108 80 C 130 55, 170 48, 205 70" stroke-width="1.6"/>
<path d="M28 96 C 60 70, 86 70, 107 86 C 132 66, 166 60, 196 76" stroke-width=".8"/>
<path d="M104 80 q 4 10 2 18 q -6 2 -8 -4" stroke-width="1.2"/>
<path d="M60 74 l -6 -8 M74 68 l -4 -10 M88 66 l -2 -10 M140 62 l 2 -10 M156 58 l 4 -10 M172 58 l 6 -8" stroke-width=".7"/>
<path d="M40 128 C 70 118, 100 126, 128 120" stroke-width=".6" stroke-dasharray="2 4"/>
</g></svg>"""

THEMES = {
    "einstein": {
        "name": "Albert Einstein",
        "place": "Princeton, New Jersey",
        "class": "typed",
        "margin_note": '<div class="pencil">E = mc²</div>',
        "flourish": "",
    },
    "amelia-earhart": {
        "name": "Amelia Earhart",
        "place": "Somewhere above the clouds",
        "class": "airmail",
        "margin_note": '<div class="airmail-mark">Via Air Mail · Par Avion</div>',
        "flourish": "",
    },
    "cleopatra": {
        "name": "Cleopatra",
        "place": "From the palace at Alexandria",
        "class": "papyrus",
        "margin_note": '<div class="translated">Translated from the Greek</div>',
        "flourish": "",
    },
    "leonardo-da-vinci": {
        "name": "Leonardo da Vinci",
        "place": "Milan",
        "class": "parchment",
        "margin_note": BIRD_SKETCH,
        "flourish": '<div class="mirror">Hold this line up to a mirror. This is how I write in my notebooks!</div>',
    },
}

# Kept for callers that only need display names.
FIGURE_NAMES = {k: (v["name"], v["place"]) for k, v in THEMES.items()}


@cache
def _font_faces() -> str:
    faces = []
    for family, (filename, style) in FONT_FILES.items():
        data = base64.b64encode(files("letters.fonts").joinpath(filename).read_bytes()).decode()
        faces.append(
            f"@font-face{{font-family:'{family}';font-style:{style};font-weight:400 700;"
            f"src:url(data:font/woff2;base64,{data}) format('woff2');}}"
        )
    return "\n".join(faces)


STYLE = """
@page { size: 8.5in 11in; margin: 0; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; background: #d9d4c8; }
.page { width: 8.5in; min-height: 11in; margin: 0 auto; position: relative;
  padding: 0.95in 1in 0.85in; page-break-after: always; break-after: page; }
.page + .page { margin-top: 0.3in; }
@media print { body { background: none; } .page + .page { margin-top: 0; } }

/* Shared letter anatomy */
.place { text-align: right; margin-bottom: 0.4in; }
.greeting { margin-bottom: 0.18in; }
.body p { margin: 0 0 0.15in; }
.closing { margin-top: 0.22in; }
.signature { font-family: 'Signature', cursive; font-size: 2.1em; line-height: 1.6; margin: 0.04in 0 0 0.1in;
  transform: rotate(-3deg); transform-origin: left center; display: inline-block; }
.ps { margin-top: 0.22in; }

/* 1. Einstein: typed on a 1950s typewriter, pen signature, pencil note in the margin */
.typed { background: #f6f3ea url("GRAIN"); color: #26231f;
  font-family: 'Typewriter', 'Courier New', monospace; font-size: var(--size); line-height: 1.55; }
.typed .body, .typed .greeting, .typed .closing, .typed .place { text-shadow: 0 0 0.6px rgba(30,25,20,.55); }
.typed .signature, .typed .ps { color: #1e2a55; }
.typed .ps { font-family: 'Hand', cursive; font-size: 1.45em; line-height: 1.2; }
.pencil { position: absolute; top: 1.55in; right: 0.38in; font-family: 'Hand', cursive; font-size: 22pt;
  color: #6f6c68; transform: rotate(8deg); opacity: .85; }

/* 2. Amelia Earhart: handwritten in ink on airmail stationery */
.airmail { background: #eef1f3 url("GRAIN"); color: #1d2a57;
  font-family: 'Hand', cursive; font-size: calc(var(--size) * 1.32); line-height: 1.3; }
.airmail::before, .airmail::after { content: ""; position: absolute; left: 0; right: 0; height: 0.16in;
  background: repeating-linear-gradient(135deg, #b8322b 0 0.2in, transparent 0.2in 0.3in, #1f3f7a 0.3in 0.5in, transparent 0.5in 0.6in); }
.airmail::before { top: 0.3in; } .airmail::after { bottom: 0.3in; }
.airmail-mark { position: absolute; top: 0.6in; left: 1in; font-family: 'Fell', serif; font-size: 9.5pt;
  letter-spacing: .18em; text-transform: uppercase; color: #1f3f7a; }
.airmail .place { margin-top: 0.15in; }

/* 3. Cleopatra: reed-pen ink on papyrus */
.papyrus { color: #2a1b0e; font-family: 'Fell', Georgia, serif; font-size: calc(var(--size) * 1.08); line-height: 1.5;
  background:
    url("GRAIN"),
    radial-gradient(ellipse at center, transparent 55%, rgba(110,70,20,.28) 100%),
    url("FIBERS_H"),
    url("FIBERS_V"),
    #e4d09e; }
.papyrus .place, .papyrus .translated { font-family: 'FellItalic', Georgia, serif; }
.papyrus .signature { font-family: 'FellItalic', serif; font-size: 2.2em; transform: none; }
.translated { position: absolute; top: 0.55in; left: 1in; font-size: 10pt; color: #6d5330; }

/* 4. Leonardo: sepia ink on parchment, sketches in the margin, a line of mirror writing */
.parchment { color: #4a2c14; font-family: 'FellItalic', Georgia, serif; font-size: calc(var(--size) * 1.08); line-height: 1.5;
  background:
    url("GRAIN"),
    radial-gradient(circle at 18% 22%, rgba(150,100,40,.10), transparent 30%),
    radial-gradient(circle at 82% 70%, rgba(150,100,40,.12), transparent 28%),
    radial-gradient(ellipse at center, transparent 60%, rgba(120,80,30,.25) 100%),
    #eddcb6; }
.parchment .signature { color: #4a2c14; }
.sketch { position: absolute; right: 0.45in; bottom: 0.55in; width: 2.1in; color: #6b4524; opacity: .45; transform: rotate(-6deg); }
.mirror { font-family: 'Hand', cursive; font-size: 1.25em; transform: scaleX(-1); text-align: right; margin-top: 0.15in; color: #6b4524; }

/* Back page: an archive index card with the real history, and room to write back */
.back { height: 11in; display: flex; flex-direction: column; overflow: hidden; background: #fbfaf6; font-family: 'Typewriter', 'Courier New', monospace; color: #26231f; font-size: 11.5pt; line-height: 1.55; }
.card { background: #fffef9; border: 1px solid #d9d2c0; box-shadow: 0 1px 0 #ece6d6; padding: 0.28in 0.35in 0.2in; }
.card h2 { font: inherit; font-size: 13pt; letter-spacing: .12em; text-transform: uppercase; margin: 0 0 0.12in;
  padding-bottom: 0.1in; border-bottom: 1.5px solid #d65b5b; }
.card h2 small { display: block; letter-spacing: 0; text-transform: none; font-size: 10pt; line-height: 1.4; color: #6b665c; margin-top: 0.04in; }
.card ol { margin: 0; padding-left: 0.3in; line-height: 0.3in;
  background: repeating-linear-gradient(transparent 0 calc(0.3in - 1px), #c9dcea calc(0.3in - 1px) 0.3in); }
.card li { margin: 0; }
.reply { margin-top: 0.35in; flex: 1; display: flex; flex-direction: column; min-height: 0; }
.reply h3 { font-family: 'Hand', cursive; font-weight: 600; font-size: 24pt; margin: 0; color: #1e2a55; }
.reply p { margin: 0.04in 0 0.15in; font-size: 10.5pt; color: #4f4a42; }
.lines { flex: 1; min-height: 0.76in; background: repeating-linear-gradient(transparent 0 0.38in, #b9b3a6 0.38in calc(0.38in + 1px)); }
.fine { margin-top: 0.2in; font-size: 8.5pt; color: #8a8478; text-align: center; }
"""


def _font_size(letter: Letter) -> str:
    # Keep the whole letter on one printed page: shorter letters (young kids) get bigger type.
    words = sum(len(p.split()) for p in letter.body) + len(letter.postscript.split())
    if words <= 200:
        return "14pt"
    if words <= 300:
        return "11.5pt"
    return "10.5pt"


def _split_signoff(signoff: str) -> tuple[str, str]:
    lines = [line.strip() for line in signoff.strip().splitlines() if line.strip()]
    if len(lines) >= 2:
        return " ".join(lines[:-1]), lines[-1]
    return "", lines[0] if lines else ""


def render_html(letter: Letter, figure: str, child_name: str) -> str:
    theme = THEMES.get(figure) or {
        "name": figure.replace("-", " ").title(), "place": "", "class": "typed",
        "margin_note": "", "flourish": "",
    }
    first_name = theme["name"].split()[0]
    closing_line, signature = _split_signoff(letter.signoff)
    body = "\n".join(f"<p>{escape(p)}</p>" for p in letter.body)
    notes = "\n".join(f"<li>{escape(n)}</li>" for n in letter.real_history)
    ps = escape(letter.postscript.removeprefix("P.S.").strip())
    style = STYLE.replace("GRAIN", GRAIN).replace("FIBERS_H", FIBERS_H).replace("FIBERS_V", FIBERS_V)

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>A letter from {escape(theme["name"])} to {escape(child_name)}</title>
<style>{_font_faces()}
{style}</style></head>
<body>
<section class="page {theme["class"]}" style="--size: {_font_size(letter)}">
  {theme["margin_note"]}
  <div class="place">{escape(theme["place"])}</div>
  <div class="greeting">{escape(letter.greeting)}</div>
  <div class="body">{body}</div>
  <div class="closing">{escape(closing_line)}<br><span class="signature">{escape(signature)}</span></div>
  <div class="ps">P.S. {ps}</div>
  {theme["flourish"]}
</section>
<section class="page back">
  <div class="card">
    <h2>Real history<small>The true story behind {escape(first_name)}'s letter, for {escape(child_name)} and a grown-up to read together.</small></h2>
    <ol>{notes}</ol>
  </div>
  <div class="reply">
    <h3>Write back to {escape(first_name)}!</h3>
    <p>Write here or on any paper you like. Then ask a grown-up to take a photo of it and upload it in your Letters from History account. {escape(first_name)}'s next letter will answer you.</p>
    <div class="lines"></div>
  </div>
  <div class="fine">Letters from History · Written with the help of AI, from verified historical facts, and checked for accuracy before printing.</div>
</section>
</body></html>
"""
