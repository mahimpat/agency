"""Render a Letter as a printable, mail-ready HTML page (A4 or US Letter, front + back).

Each figure gets stationery that fits their era, so a letter looks like a real
object: a typed page from 1950s Princeton, an airmail note, papyrus, parchment.
Fonts are embedded so the file prints identically anywhere.

UK print-and-post services (e.g. Stannp) mail A4 letters folded into window
envelopes and print the recipient's address into a zone at the top left of the
first page, so by default that zone is kept clear, like a real letter's layout.
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

def _ammonite() -> str:
    """An ammonite: a spiral shell with ribs, drawn like a pencil field sketch."""
    import math
    cx = cy = 60
    points, ribs = [], []
    for i in range(0, 241):
        theta = i * 0.06
        r = 2 + 3.1 * theta
        x, y = cx + r * math.cos(theta), cy + r * math.sin(theta)
        points.append(f"{x:.1f} {y:.1f}")
        if i % 8 == 0 and theta > 3:
            r2 = r - 3.1 * math.pi * 2 * 0.45
            ribs.append(f"M{x:.1f} {y:.1f} L{cx + r2 * math.cos(theta):.1f} {cy + r2 * math.sin(theta):.1f}")
    return ('<svg class="sketch ammonite" viewBox="0 0 120 120" aria-hidden="true"><g fill="none" '
            'stroke="currentColor" stroke-linecap="round">'
            f'<path d="M{" L".join(points)}" stroke-width="1.5"/>'
            f'<path d="{" ".join(ribs)}" stroke-width=".7"/></g></svg>')


AMMONITE_SKETCH = _ammonite()

BRIDGE_SKETCH = """<svg class="sketch" viewBox="0 0 240 120" aria-hidden="true">
<g fill="none" stroke="currentColor" stroke-linecap="round">
<path d="M10 104 C 30 80, 34 70, 40 60 L 40 104 M230 104 C 210 80, 206 70, 200 60 L 200 104" stroke-width="1.2"/>
<path d="M34 40 h12 v26 h-12 z M194 40 h12 v26 h-12 z" stroke-width="1.4"/>
<path d="M40 42 C 90 96, 150 96, 200 42" stroke-width="1.6"/>
<path d="M40 70 L 200 70" stroke-width="1.4"/>
<path d="M60 58 v12 M80 70 v-6 M100 70 v-3 M120 70 v-2 M140 70 v-3 M160 70 v-6 M180 58 v12" stroke-width=".7"/>
<path d="M70 116 q 50 -10 100 0" stroke-width=".6" stroke-dasharray="3 4"/>
</g></svg>"""

PUNCHCARD_SKETCH = """<svg class="sketch" viewBox="0 0 200 110" aria-hidden="true">
<g fill="none" stroke="currentColor" stroke-linecap="round">
<rect x="10" y="20" width="170" height="70" rx="3" stroke-width="1.4" transform="rotate(-4 95 55)"/>
<g fill="currentColor" stroke="none" transform="rotate(-4 95 55)">
<circle cx="30" cy="35" r="3"/><circle cx="50" cy="35" r="3"/><circle cx="90" cy="35" r="3"/><circle cx="130" cy="35" r="3"/>
<circle cx="40" cy="55" r="3"/><circle cx="70" cy="55" r="3"/><circle cx="110" cy="55" r="3"/><circle cx="150" cy="55" r="3"/>
<circle cx="30" cy="75" r="3"/><circle cx="80" cy="75" r="3"/><circle cx="120" cy="75" r="3"/><circle cx="160" cy="75" r="3"/></g>
</g></svg>"""

BEETLE_SKETCH = """<svg class="sketch" viewBox="0 0 120 140" aria-hidden="true">
<g fill="none" stroke="currentColor" stroke-linecap="round">
<ellipse cx="60" cy="80" rx="26" ry="36" stroke-width="1.5"/>
<path d="M60 44 v72" stroke-width="1"/>
<ellipse cx="60" cy="38" rx="14" ry="10" stroke-width="1.3"/>
<path d="M54 30 q -8 -14 -18 -16 M66 30 q 8 -14 18 -16" stroke-width="1"/>
<path d="M35 64 l-18 -8 M34 82 l-20 2 M37 100 l-16 14 M85 64 l18 -8 M86 82 l20 2 M83 100 l16 14" stroke-width="1"/>
</g></svg>"""

LIGHTHOUSE_SKETCH = """<svg class="sketch tall" viewBox="0 0 120 150" aria-hidden="true">
<g fill="none" stroke="currentColor" stroke-linecap="round">
<path d="M48 128 L54 40 H66 L72 128 Z" stroke-width="1.5"/>
<path d="M52 40 h16 v-12 h-16 z M50 28 h20 M60 28 v-8 M54 20 h12" stroke-width="1.3"/>
<path d="M51 70 h18 M50 96 h20" stroke-width=".8"/>
<path d="M66 34 L104 24 M66 34 L104 44 M54 34 L16 24 M54 34 L16 44" stroke-width=".6" stroke-dasharray="3 3"/>
<path d="M8 136 q 14 -8 28 0 t 28 0 t 28 0 t 28 0" stroke-width="1"/>
<path d="M14 146 q 12 -6 24 0 t 24 0 t 24 0 t 24 0" stroke-width=".7"/>
</g></svg>"""

ROCKET_SKETCH = """<svg class="sketch" viewBox="0 0 220 120" aria-hidden="true">
<g fill="none" stroke="currentColor" stroke-linecap="round">
<rect x="40" y="52" width="90" height="26" rx="12" stroke-width="1.5"/>
<path d="M118 52 v-34 h10 v34" stroke-width="1.4"/>
<path d="M114 18 h18" stroke-width="1.4"/>
<circle cx="70" cy="92" r="20" stroke-width="1.5"/><circle cx="70" cy="92" r="3" stroke-width="1"/>
<circle cx="138" cy="96" r="12" stroke-width="1.4"/>
<path d="M70 72 v40 M50 92 h40 M56 78 l28 28 M84 78 l-28 28" stroke-width=".6"/>
<path d="M30 78 L140 78 M20 112 H200" stroke-width="1"/>
<path d="M124 12 q -6 -8 2 -10 q 8 -2 4 -8" stroke-width=".7" stroke-dasharray="2 3"/>
</g></svg>"""

MOON_SKETCH = """<svg class="sketch tall" viewBox="0 0 140 140" aria-hidden="true">
<g fill="none" stroke="currentColor" stroke-linecap="round">
<circle cx="70" cy="70" r="48" stroke-width="1.5"/>
<circle cx="52" cy="54" r="9" stroke-width=".8"/><circle cx="84" cy="82" r="13" stroke-width=".8"/>
<circle cx="80" cy="44" r="5" stroke-width=".8"/><circle cx="50" cy="92" r="6" stroke-width=".8"/>
<path d="M18 120 q 10 -14 22 -4 l6 -8 l6 8" stroke-width="1"/>
<path d="M8 128 H132" stroke-width=".7"/>
</g></svg>"""

CHEESE_SKETCH = """<svg class="sketch" viewBox="0 0 200 130" aria-hidden="true">
<g fill="none" stroke="currentColor" stroke-linecap="round">
<ellipse cx="80" cy="56" rx="54" ry="16" stroke-width="1.5"/>
<path d="M26 56 v28 c 0 9, 24 16, 54 16 s 54 -7, 54 -16 v-28" stroke-width="1.5"/>
<path d="M80 56 L 128 46 M80 56 L 116 68" stroke-width="1.1"/>
<path d="M40 74 q 6 2 10 0 M96 86 q 6 2 10 0 M62 90 q 4 2 8 0" stroke-width=".7"/>
<path d="M150 18 L 176 104 M146 20 q 4 -10 14 -4 M168 96 l 18 -6 l 6 18 l -18 6 z" stroke-width="1.2"/>
<path d="M10 116 q 40 -8 90 -2 t 90 0" stroke-width=".7" stroke-dasharray="3 4"/>
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
    "mary-anning": {
        "name": "Mary Anning", "place": "Lyme Regis, Dorset", "class": "victorian",
        "margin_note": AMMONITE_SKETCH, "flourish": "",
    },
    "isambard-kingdom-brunel": {
        "name": "Isambard Kingdom Brunel", "place": "Westminster, London", "class": "victorian",
        "margin_note": BRIDGE_SKETCH, "flourish": "",
    },
    "ada-lovelace": {
        "name": "Ada Lovelace", "place": "London", "class": "victorian",
        "margin_note": PUNCHCARD_SKETCH, "flourish": "",
    },
    "florence-nightingale": {
        "name": "Florence Nightingale", "place": "London", "class": "victorian",
        "margin_note": "", "flourish": "",
    },
    "mary-seacole": {
        "name": "Mary Seacole", "place": "London", "class": "victorian",
        "margin_note": "", "flourish": "",
    },
    "charles-darwin": {
        "name": "Charles Darwin", "place": "Down House, Kent", "class": "victorian",
        "margin_note": BEETLE_SKETCH, "flourish": "",
    },
    "william-shakespeare": {
        "name": "William Shakespeare", "place": "Bankside, London", "class": "parchment",
        "margin_note": "", "flourish": "",
    },
    "elizabeth-i": {
        "name": "Elizabeth I, Queen of England", "place": "The Palace of Greenwich", "class": "parchment",
        "margin_note": "", "flourish": "",
    },
    "grace-darling": {
        "name": "Grace Darling", "place": "Longstone Lighthouse, Farne Islands", "class": "victorian",
        "margin_note": LIGHTHOUSE_SKETCH, "flourish": "",
    },
    "george-stephenson": {
        "name": "George Stephenson", "place": "Tapton House, Chesterfield", "class": "victorian",
        "margin_note": ROCKET_SKETCH, "flourish": "",
    },
    "neil-armstrong": {
        "name": "Neil Armstrong", "place": "Houston, Texas", "class": "typed",
        "margin_note": MOON_SKETCH, "flourish": "",
    },
    "isaac-newton": {
        "name": "Isaac Newton", "place": "Trinity College, Cambridge", "class": "parchment",
        "margin_note": "", "flourish": "",
    },
    "al-khwarizmi": {
        "name": "Al-Khwarizmi", "place": "The House of Wisdom, Baghdad", "class": "parchment",
        "margin_note": '<div class="translated">Translated from the Arabic</div>', "flourish": "",
    },
    "samuel-pepys": {
        "name": "Samuel Pepys", "place": "Seething Lane, London", "class": "parchment",
        "margin_note": CHEESE_SKETCH, "flourish": "",
    },
    "walter-tull": {
        "name": "Walter Tull", "place": "Northampton", "class": "victorian",
        "margin_note": "", "flourish": "",
    },
    "emmeline-pankhurst": {
        "name": "Emmeline Pankhurst", "place": "London", "class": "typed",
        "margin_note": '<div class="pencil" style="color:#5b2a6e">Votes for Women</div>', "flourish": "",
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
@page { size: PAGE_SIZE; margin: 0; }
* { box-sizing: border-box; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; background: #d9d4c8; }
.page { width: var(--pw); height: var(--ph); overflow: hidden; margin: 0 auto; position: relative;
  padding: 0.9in 0.85in 0.75in; page-break-after: always; break-after: page; }
/* Address window: kept clear for the print service, or filled for home printing.
   Matches a UK DL/C5 window envelope position; check against your provider's guide. */
.window { position: absolute; left: 20mm; top: 45mm; width: 95mm; height: 38mm;
  font: 10.5pt/1.35 Georgia, 'Times New Roman', serif; color: #222; padding: 3mm 4mm; }
.with-window { padding-top: 92mm; }
.with-window .place { position: absolute; top: 48mm; right: 0.85in; max-width: 70mm; margin: 0; }
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
.pencil { position: absolute; top: 0.75in; right: 0.38in; font-family: 'Hand', cursive; font-size: 22pt;
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
.sketch { position: absolute; right: 0.4in; bottom: 0.3in; width: 1.7in; color: #6b4524; opacity: .45; transform: rotate(-6deg); }
.mirror { font-family: 'Hand', cursive; font-size: 1.25em; transform: scaleX(-1); text-align: right; margin-top: 0.15in; color: #6b4524; }

/* 5. Victorian Britain: steel-nib ink on cream laid writing paper */
.victorian { color: #2b1f14; font-family: 'Fell', Georgia, serif; font-size: calc(var(--size) * 1.1); line-height: 1.5;
  background:
    url("GRAIN"),
    repeating-linear-gradient(90deg, rgba(120,100,70,.07) 0 1px, transparent 1px 0.95in),
    repeating-linear-gradient(0deg, rgba(120,100,70,.045) 0 1px, transparent 1px 3px),
    radial-gradient(ellipse at center, transparent 65%, rgba(140,110,60,.16) 100%),
    #f5edd9; }
.victorian .place { font-family: 'FellItalic', Georgia, serif; }
.victorian .signature { color: #2b1f14; }
.victorian .sketch { color: #5a4630; opacity: .4; bottom: 0.25in; }
.sketch.ammonite { width: 1.15in; right: 0.55in; }
.sketch.tall { width: 0.85in; right: 0.4in; bottom: 0.25in; }

/* Back page: an archive index card with the real history, and room to write back */
.back { display: flex; flex-direction: column; overflow: hidden; background: #fbfaf6; font-family: 'Typewriter', 'Courier New', monospace; color: #26231f; font-size: 11.5pt; line-height: 1.55; }
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


PAPER = {"A4": ("210mm", "297mm", "A4"), "LETTER": ("8.5in", "11in", "8.5in 11in")}

# Shrinks the type a little at a time until the front fits on one sheet (runs at PDF time).
FIT_SCRIPT = """<script>
document.fonts.ready.then(() => {
  const page = document.querySelector('.front');
  let size = parseFloat(getComputedStyle(page).getPropertyValue('--size'));
  while (page.scrollHeight > page.clientHeight + 1 && size > 8.5) {
    size -= 0.25;
    page.style.setProperty('--size', size + 'pt');
  }
});
</script>"""


def render_html(
    letter: Letter,
    figure: str,
    child_name: str,
    paper: str = "A4",
    window: bool = True,
    address_lines: list[str] | None = None,
) -> str:
    """paper: "A4" (UK) or "LETTER" (US). window: keep the envelope address zone clear.
    address_lines: print this address in the window yourself (for printing at home)."""
    pw, ph, page_size = PAPER[paper]
    theme = THEMES.get(figure) or {
        "name": figure.replace("-", " ").title(), "place": "", "class": "typed",
        "margin_note": "", "flourish": "",
    }
    first_name = theme["name"].split()[0]
    closing_line, signature = _split_signoff(letter.signoff)
    body = "\n".join(f"<p>{escape(p)}</p>" for p in letter.body)
    notes = "\n".join(f"<li>{escape(n)}</li>" for n in letter.real_history)
    ps = escape(letter.postscript.removeprefix("P.S.").strip())
    style = (STYLE.replace("PAGE_SIZE", page_size).replace("GRAIN", GRAIN)
             .replace("FIBERS_H", FIBERS_H).replace("FIBERS_V", FIBERS_V))
    window_html = ""
    if window:
        window_html = '<div class="window">' + "<br>".join(escape(x) for x in (address_lines or [])) + "</div>"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>A letter from {escape(theme["name"])} to {escape(child_name)}</title>
<style>{_font_faces()}
{style}</style></head>
<body style="--pw: {pw}; --ph: {ph}">
<section class="page front {theme["class"]}{" with-window" if window else ""}" style="--size: {_font_size(letter)}">
  {window_html}
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
{FIT_SCRIPT}
</body></html>
"""
