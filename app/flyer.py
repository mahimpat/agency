"""The welcome flyer: one A4 sheet (two sides) for the grown-ups, posted with a family's first letter.

It's for parents, never the child: the letter is the child's, so nothing in it sells. The flyer's job is
to get the child writing back (that's what keeps families), explain safety, and offer sharing and gifts.
It's the same for every family on a plan, so each version is made once and kept in storage.
"""

import base64
import hashlib
import tempfile
from html import escape
from pathlib import Path

import segno

from .config import COMPANY, PLANS, PRODUCT, Settings
from .storage import get_storage

HERE = Path(__file__).parent
FONTS = {"Lora": ("lora.woff2", "normal"), "LoraItalic": ("lora-italic.woff2", "normal"),
         "Fell": ("im-fell.woff2", "normal"), "Hand": ("caveat.woff2", "normal")}


def _fonts() -> str:
    faces = []
    for family, (name, style) in FONTS.items():
        path = HERE / "static" / "fonts" / name
        if not path.exists():
            path = HERE.parent / "letters" / "fonts" / name
        data = base64.b64encode(path.read_bytes()).decode()
        faces.append(f"@font-face{{font-family:'{family}';font-style:{style};font-weight:400 700;"
                     f"src:url(data:font/woff2;base64,{data}) format('woff2');}}")
    return "\n".join(faces)


def _qr(url: str) -> str:
    return segno.make(url, error="m").svg_inline(scale=2.4, dark="#26221d", border=0)


STYLE = """
@page { size: A4; margin: 0; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { margin: 0; color: #26221d; font: 10.6pt/1.5 'Lora', Georgia, serif; }
.side { width: 210mm; height: 297mm; box-sizing: border-box; padding: 18mm 20mm 16mm; position: relative;
  background: #fbf8f1; break-after: page; overflow: hidden; }
.side:last-child { break-after: auto; }
.top { display: flex; justify-content: space-between; align-items: baseline; border-bottom: .8pt solid #26221d;
  padding-bottom: 2.5mm; margin-bottom: 9mm; font-size: 8.5pt; letter-spacing: .18em; text-transform: uppercase; }
.top b { color: #9f3328; font-weight: 600; }
h1 { font: 400 30pt/1.08 'Fell', serif; margin: 0 0 4mm; }
h2 { font: 400 17pt/1.15 'Fell', serif; margin: 0 0 2.5mm; }
.lead { font-size: 12.5pt; line-height: 1.5; margin: 0 0 9mm; max-width: 150mm; }
.steps { display: grid; gap: 7mm; }
.step { display: grid; grid-template-columns: 13mm 1fr; gap: 4mm; }
.num { font: 400 26pt/1 'Fell', serif; color: #9f3328; }
.step p { margin: 0 0 1.5mm; }
ul { margin: 1.5mm 0 0; padding-left: 4.5mm; } li { margin-bottom: 1mm; break-inside: avoid; }
.upload { display: flex; gap: 6mm; align-items: center; background: #fff; border: .8pt solid #d6cbb5; padding: 4mm 5mm;
  margin-top: 2.5mm; }
.upload svg { flex: none; width: 24mm; height: 24mm; }
.url { font-weight: 600; word-break: break-all; }
.safe { position: absolute; left: 20mm; right: 20mm; bottom: 16mm; background: #f1ebdd; padding: 5mm 6mm; font-size: 9.6pt; }
.safe h2 { font-size: 14pt; }
.safe ul { columns: 2; column-gap: 8mm; }
.hand { font: 400 15pt/1.2 'Hand', cursive; color: #1e2a55; }
.cards { display: grid; grid-template-columns: 1fr 1fr; gap: 7mm; margin: 6mm 0 8mm; }
.card { background: #fff; border: .8pt solid #d6cbb5; padding: 5mm; display: flex; flex-direction: column; gap: 2mm; }
.card svg { width: 22mm; height: 22mm; margin-top: auto; }
.card p { margin: 0; }
.manage { border-top: .6pt solid #d6cbb5; padding-top: 6mm; }
.manage ul { columns: 2; column-gap: 8mm; }
.foot { position: absolute; left: 20mm; right: 20mm; bottom: 12mm; font-size: 7.8pt; color: #6d655a;
  border-top: .6pt solid #d6cbb5; padding-top: 2.5mm; }
"""


def flyer_html(settings: Settings, plan: str) -> str:
    site = settings.base_url.rstrip("/")
    short = escape(site.replace("https://", "").replace("http://", ""))
    account, share, gift = f"{site}/account", f"{site}/?ref=flyer", f"{site}/gift?ref=flyer"
    replies = PLANS[plan]["reply"]
    contact = escape(settings.contact_email)
    if replies:
        step3 = f"""<div class="step"><div class="num">3</div><div>
  <h2>Send us their letter</h2>
  <p>Take a photo of it (or type it in) on your account page. We read it, delete the photo straight away, and the
  next letter answers them, usually within a week or so.</p>
  <div class="upload">{_qr(account)}<div><p>Scan to open your account, or go to</p>
  <p class="url">{short}/account</p></div></div></div></div>"""
    else:
        step3 = f"""<div class="step"><div class="num">3</div><div>
  <h2>Want their letters answered?</h2>
  <p>On Story Letters, your child gets a new letter each month. If they'd love their pen pal to write back
  to them, Pen Pal from History ({PLANS['penpal']['price_label']}) answers their letters. You can switch any time
  from your account at <span class="url">{short}/account</span>.</p></div></div>"""
    company = escape(COMPANY)
    number = f" · Company no. {escape(settings.company_number)}" if settings.company_number else ""
    address = f" · {escape(settings.company_address)}" if settings.company_address else ""
    return f"""<!doctype html><html lang="en-GB"><head><meta charset="utf-8"><title>Welcome</title>
<style>{_fonts()}{STYLE}</style></head><body>
<section class="side">
  <div class="top"><span><b>For grown-ups</b></span><span>{escape(PRODUCT)}</span></div>
  <h1>Welcome! The first letter has arrived.</h1>
  <p class="lead">Inside is an introduction from your child's first pen pal. Here's how to make the most of it.</p>
  <div class="steps">
    <div class="step"><div class="num">1</div><div>
      <h2>Read it together, or let them read it alone</h2>
      <p>Some children like a quiet first read; others love it read aloud. The "Real history" card on the back
      shows which parts are true.</p></div></div>
    <div class="step"><div class="num">2</div><div>
      <h2>Help them write back</h2>
      <ul>
        <li>Any paper, any length. A drawing counts.</li>
        <li>Don't worry about spelling. Pen pals never correct it.</li>
        <li>Questions are perfect: {"the next letter answers them" if replies else "they make great conversation starters"}.</li>
        <li>Please leave out surnames, school, address and phone numbers.</li>
      </ul></div></div>
    {step3}
  </div>
  <div class="safe"><h2>How we keep it safe</h2><ul>
    <li>Every letter is fact-checked against real history before it's posted.</li>
    <li>Letters are written with AI and checked, and a person reviews any the checks flag.</li>
    <li>Children's replies go through safety checks. If something worries us, a trained person reads it.</li>
    <li>We never ask a child for personal details.</li>
    <li>You can read each letter before it's posted: turn on previews in your account.</li>
    <li>Photos of replies are deleted as soon as they're read.</li>
  </ul></div>
</section>
<section class="side">
  <div class="top"><span><b>For grown-ups</b></span><span>{escape(PRODUCT)}</span></div>
  <h1>We'd love to hear how it went</h1>
  <p class="lead">We're a small UK team{", and you're one of our first families" if settings.flyer_founding else ""}.
  Tell us what your child thought, what they asked, and what we could do better: just reply to any of our emails
  or write to <b>{contact}</b>.</p>
  <p class="hand">{"Thank you for joining us at the very start." if settings.flyer_founding else "Thank you for joining us."} ~ The Letters from History team</p>
  <div class="cards">
    <div class="card"><h2>Know a family who'd love this?</h2>
      <p>Pass on this link, or let them scan the code.</p><p class="url">{short}/?ref=flyer</p>{_qr(share)}</div>
    <div class="card"><h2>A present for someone special</h2>
      <p>Give 3, 6 or 12 months of Pen Pal from History. Paid once, nothing to renew, and the certificate arrives by email.</p>
      <p class="url">{short}/gift?ref=flyer</p>{_qr(gift)}</div>
  </div>
  <div class="manage"><h2>Your account</h2><ul>
    <li>Preview letters before they're posted</li>
    <li>Update your child's details or address</li>
    <li>{"Send your child's replies" if replies else "Switch to Pen Pal from History"}</li>
    <li>Cancel any time with one click</li>
  </ul><p class="url" style="margin-top:2mm">{short}/account</p></div>
  <div class="foot">{escape(PRODUCT)} is a product of {company}{number}{address} · {contact}</div>
</section>
</body></html>"""


def flyer_pdf(settings: Settings, plan: str, to_pdf) -> bytes:
    """The flyer for this plan as PDF bytes, made once per version and kept in storage."""
    html = flyer_html(settings, plan)
    key = f"flyers/welcome-{plan}-{hashlib.sha256(html.encode()).hexdigest()[:12]}.pdf"
    store = get_storage(settings)
    if store.exists(key):
        return store.get(key)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "flyer.pdf"
        to_pdf(html, path)
        data = path.read_bytes()
    store.put(key, data, "application/pdf")
    return data
