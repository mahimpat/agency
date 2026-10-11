# Validation Playbook: is there real demand for Letters from History?

Goal: in 2-3 weeks and under $150, find out whether parents will sign up, and whether they want the
"writes back" feature enough to pay more for it. **Don't build the full product until this passes.**

## Pass / fail

| Metric | Pass |
|---|---|
| Visitors who join the waitlist | **3% or more** |
| People who pay a $1 deposit (if you turn it on) | **10 or more** |
| Waitlist sign-ups choosing the $14 "Pen Pal" plan | **30% or more** (this tests the differentiator) |
| Reactions from the 5-10 real test letters | At least half the kids write back unprompted |

- **Below 1% sign-ups after ~500 visitors:** the offer or audience is wrong. Try the grandparent-gift angle once, then stop.
- **Between 1% and 3%:** change the headline or hero letter and test again.

## Week 1: get the kit live

1. **Put the landing page online (free, about 10 minutes).**
   - Create a free [Netlify](https://www.netlify.com) account. Choose "Add new site", then "Deploy manually", and drag the `site/` folder in.
   - Netlify Forms picks up the waitlist form automatically. Sign-ups appear under **Forms** in the Netlify dashboard, and you can turn on email notifications there.
   - Optional: buy a domain (about $12), such as `lettersfromhistory.co`.
   - Optional: count visitors with a free [GoatCounter](https://www.goatcounter.com) account (paste your code into the commented snippet in `site/index.html`). Netlify Analytics also works.
2. **Optional $1 deposit.** Create a Stripe Payment Link for a $1 "Founding family reservation" and paste it into `DEPOSIT_URL` in `site/index.html`. A deposit is a much stronger signal than an email address.
3. **Mail 5-10 real letters to friends' kids.**
   - Add a child profile in `examples/children/` (with the parent's permission).
   - Run `letters write examples/children/<name>.json einstein`.
   - Read the letter and the fact-check result, then print it at home or send it with `letters send` (a Lob test key costs nothing).
   - Ask the parents to photograph the reply and send it to you. Then run `letters write ... --reply-photo photo.jpg` and mail the answer.
   - **Film the moment a kid opens a reply** (with permission). That clip becomes your best ad.

## Week 2: send traffic

Put a different `?ref=` code on each link so you know which channel works (for example `yoursite.com/?ref=fb-homeschool-1`). The waitlist records it.

1. **Homeschool Facebook groups (5-10 groups).** Read each group's rules first. Many allow posts on "resource" days. Example post:
   > My 7-year-old wrote a letter to Amelia Earhart asking if she was scared flying over the ocean. A week later, "Amelia" wrote back and answered every question (true facts only, there's a "real history" card on the back). We're testing this with a few founding families. Would love feedback from homeschoolers: [link]
2. **Reddit:** r/homeschool, r/Parenting, r/daddit, r/Mommit. Only where self-promotion is allowed; otherwise ask for feedback and don't link.
3. **Meta ads test ($50-100):** target parents of children aged 5-12 with interests like homeschool, Little Passports and educational toys. Use the video of a kid opening a letter, or the hero image. Send the traffic to the page with `?ref=meta`.
4. **5 homeschool bloggers or Instagrammers:** offer them a free 3-month founding subscription in return for honest feedback.

## Week 3: decide

Export the waitlist from Netlify and fill in this table:

| Channel (ref) | Visitors | Sign-ups | % | Picked $14 | Picked gift |
|---|---|---|---|---|---|
| fb-homeschool | | | | | |
| reddit | | | | | |
| meta | | | | | |

Then:

- **Passed:** build the full product. That means a parent account with reply upload, Stripe subscriptions, a scheduled monthly run, and more figures. Email the waitlist when founding spots open.
- **Gift was popular:** launch a prepaid "12 letters from history" gift before the holidays.
- **Failed:** you spent about $150 and three weeks instead of months. Pick the next idea from the shortlist.

## Before charging real money

- [ ] Privacy policy and terms, including COPPA for the US. Collect data from parents only, and delete reply photos after reading them.
- [ ] Spot-check a sample of letters by hand each month, even after they pass the automatic fact-check.
- [ ] Say clearly on the site that letters are written with AI help.
- [ ] Use a Lob live key and a real return address.
