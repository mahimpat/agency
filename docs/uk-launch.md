# UK Launch: Printing, Posting, Costs and Rules

*Researched October 2026. Prices change, so confirm current rates before relying on them. Each print-and-post service below has a test mode or free trial you can use to check.*

## How a letter gets to a child

```
Agents write and fact-check → render an A4 PDF (letter front, Real history card back)
   → print-and-post API prints it double-sided, folds it into a window envelope,
     prints the child's address in the window zone, and hands it to Royal Mail
   → it arrives through the letterbox (2nd class: usually 2-3 working days)
```

The letters are designed for this. Page 1 keeps the **address-window zone clear** at the top left, with the figure's place line opposite it, just like a real letter.

## Print-and-post options

| Service | What it is | Price per A4 letter incl. postage | API | Notes |
|---|---|---|---|---|
| **Stannp** (recommended, built in) | UK print-and-post platform | Advertised from **£0.38** at high volume. The Free plan lists **£0.88**, falling to £0.44-0.48 on paid plans at volume | Yes. **Test mode makes a free PDF proof** without posting | Built into `letters send`. Accepts PDF, A4, double-sided, white-out address zone, 1st-class add-on |
| Intelliprint | UK hybrid mail, printed in Leeds | From **84p** incl. 2nd class | Yes (REST, Zapier, n8n) | Good backup. The price source is a few years old |
| Docmail | UK hybrid mail | **£1.05** colour A4, economy postage (ex VAT) | Yes | Pay as you go, no minimums |
| PostGrid UK | International print-and-mail API | Quoted in USD | Yes | Ask for a UK quote |
| Do it yourself | Home printer + window envelope + stamp | ~15p print and envelope + **91p** 2nd class stamp (**£1.80** 1st class) | n/a | **Best for the first 5-10 test letters** |

Royal Mail stamp prices from 7 April 2026: 2nd class letter 91p, 1st class letter £1.80. A folded A4 sheet in a DL or C5 envelope is a standard "letter" (up to 100g).

**Recommendation:**
1. **Validation (first 5-10 letters):** print at home and post them yourself. Run `letters write ... --print-address address.json` so the address prints in the window position, then use a DL window envelope and a 2nd class stamp. No accounts, no integration risk, about £1 a letter.
2. **Before launch:** make a Stannp account and send a few **test-mode** letters (`letters send letter.pdf --to address.json`). Check that the address lands in the window and nothing important is hidden by the white-out zone. Stannp publishes exact template measurements, so compare against them.
3. **Launch:** `letters send ... --live`. The `cost` field in each response shows exactly what you paid.

## Unit economics in pounds (estimates)

| Per subscriber per month | Story Letters, £7 (1 letter) | Pen Pal from History, £12 (2 letters) |
|---|---|---|
| Print + envelope + 2nd class postage (~£0.88 each on Stannp's Free plan) | £0.88 | £1.76 |
| AI (writing, fact-check, reading replies) | ~£0.15 | ~£0.30 |
| Card payments (Stripe UK cards ~1.5% + 20p) | ~£0.31 | ~£0.38 |
| **Total cost** | **~£1.34** | **~£2.44** |
| **Gross margin** | **~£5.66 (81%)** | **~£9.56 (80%)** |

- **VAT:** you only register once turnover passes **£90,000** a year. After that, 20% VAT comes out of your prices (£12 becomes £10 net), so plan your prices with that in mind.
- **Volume:** paid Stannp plans bring postage-inclusive letters down to about £0.44-0.48 at higher volumes.
- **1st class** costs more and isn't needed. Letters aren't urgent, and 2nd class adds to the "real post" feel.

## Market (UK)

- **126,000 children were electively home educated in England** on census day in autumn 2025, up from 111,700 a year earlier. **175,900** were home educated at some point during 2024/25 (DfE). Add Scotland, Wales and Northern Ireland, plus school-going children whose parents want enrichment.
- **Grandparents buying gifts** are a big UK segment. Christmas and birthdays favour prepaid 6- or 12-month plans.
- **UK competitor to watch: Banjo Robinson.** It's a personalised pen-pal cat for ages 5-8, and 88% of parents were satisfied in a Mumsnet product test. It proves UK parents pay for personalised letters for children. It isn't history, and it doesn't answer each child's own letter the way ours does.

## UK rules checklist (before taking money)

This is a checklist, not legal advice. Check the current guidance for each item.

- [ ] **Data protection (UK GDPR):** register with the ICO and pay the data protection fee (about £50 a year for most small businesses). Publish a privacy notice. Collect data **from parents only**, keep it to the minimum, and delete reply photos after reading.
- [ ] **Children's data:** the service is bought and run by parents, so children never get accounts. Review the ICO's Age Appropriate Design Code ("Children's Code") anyway, because children's letters pass through the service.
- [ ] **Consumer law:** online subscriptions normally carry a 14-day cancellation right under the Consumer Contracts Regulations. Show clear prices including postage, and make cancelling easy. **New subscription rules under the DMCC Act 2024** (reminder notices, easy exit) are being phased in, so check their status before launch.
- [ ] **Advertising (ASA/CAP code):** you must be able to back up claims like "every fact checked". Keep fact-check records (the `.json` file saved with each letter). Be open that letters are written with AI help.
- [ ] **Business setup:** start as a sole trader (register for Self Assessment with HMRC) or a limited company. Choose a trading name and check the domain.
- [ ] **Inserts:** if you later add stickers or small items to envelopes, check toy-safety rules first. Paper-only letters keep this simple.

## UK marketing channels

- **Mumsnet and Netmums:** read the self-promotion rules. Mumsnet also runs paid product tests, as Banjo Robinson did.
- **UK home-education Facebook groups** and local home-ed co-ops. Many have resource-sharing days.
- **Instagram home-ed and "screen-free childhood" accounts:** offer founding families free months in return for honest posts.
- **Grandparent gifting:** run a Christmas campaign (October to early December) for a prepaid "12 letters from history" plan.
- **Subreddits** for UK parenting and home education, only where promotion is allowed.

## Sources

- [Stannp UK letters](https://www.stannp.com/uk/letters)
- [Stannp detailed pricing](https://www.stannp.com/uk/detailed-pricing)
- [Stannp letters API](https://www.stannp.com/uk/direct-mail-api/letters)
- [Stannp: how to send a letter through the API](https://knowledge.stannp.com/eu/how-to-send-a-letter-through-api)
- [Stannp: sending letters with Make.com](https://knowledge.stannp.com/eu/webhook-make-letter)
- [Intelliprint pricing](https://www.intelliprint.net/pricing)
- [Docmail prices](https://www.docmail.co.uk/prices/)
- [PostGrid UK hybrid mail API](https://www.postgrid.co.uk/hybrid-mail-api/)
- [Royal Mail 2026 stamp prices (Be Clever With Your Cash)](https://becleverwithyourcash.com/royal-mail-stamp-price-hike)
- [Current Royal Mail postage rates 2026 (Mailcoms)](https://www.mailcoms.co.uk/current-royal-mail-postage-rates/)
- [DfE: Elective home education, autumn term 2025/26](https://explore-education-statistics.service.gov.uk/find-statistics/elective-home-education)
- [GOV.UK: Elective home education statistics](https://www.gov.uk/government/statistics/elective-home-education-autumn-term-2025)
- [Banjo Robinson, Mumsnet product test](https://cloud.mumsnet.com/talk/mumsnet_product_tests/3887741-Read-what-Mumsnet-users-and-their-children-thought-of-Banjo-Robinson-s-personalised-pen-pal-experience?page=2)
