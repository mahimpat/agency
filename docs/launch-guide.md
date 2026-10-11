# Launch Guide: Letters from History (Forem Innovations)

From the code in this repo to taking real subscriptions. Do the steps in order. **Steps 1-6 use test mode only: no real money, no real post.**

## How it fits together

```
Parent signs up → adds child → Stripe Checkout (£7 or £12/month)
        │
Stripe webhook: invoice.paid → letter credits for that child
        │
Daily job (POST /internal/run-due, called by a scheduler)
  ├─ reads new replies (photo → words, then the photo is deleted; worrying letters held for a person)
  ├─ writes letters (intro → story → farewell → next pen pal; answers to replies)
  ├─ fact-checks them and makes A4 PDFs
  └─ queues them in /admin
        │
You approve in /admin → Stannp prints and posts (Royal Mail) → parent gets "on its way" email
```

## 1. Run it on your computer

```bash
pip install -e ".[dev]"
cp .env.example .env          # fill in what you have; leave the rest blank for now
set -a; source .env; set +a
uvicorn app.web:app --reload  # http://localhost:8000
```

Sign up on the site, then make yourself an admin:

```bash
python -m app.manage make-admin you@yourdomain.co.uk
```

Run the tests any time: `python -m unittest discover -s tests` (no API keys needed).

## 2. Stripe (test mode)

1. In the [Stripe Dashboard](https://dashboard.stripe.com), stay in **Test mode**.
2. **Product catalogue → Add product:** "Letters from History", with two **recurring monthly prices in GBP**:
   - Story Letters, £7.00 a month → copy its price ID into `STRIPE_PRICE_STORY`
   - Pen Pal from History, £12.00 a month → copy into `STRIPE_PRICE_PENPAL`
3. **Developers → API keys:** copy the secret key into `STRIPE_SECRET_KEY`.
4. **Settings → Billing → Customer portal:** turn on *cancel subscriptions* (at the end of the billing period), *update payment methods* and *invoice history*. To let parents switch between plans, also turn on *switch plans* and add both prices. The app follows the switch automatically. This is the "Manage billing" button parents use.
5. **Webhooks.** For local testing, install the [Stripe CLI](https://docs.stripe.com/stripe-cli) and run:
   ```bash
   stripe listen --forward-to localhost:8000/stripe/webhook
   ```
   Put the `whsec_...` it prints into `STRIPE_WEBHOOK_SECRET`.

   In production, add an endpoint at `https://YOUR-DOMAIN/stripe/webhook` with these events:
   - `checkout.session.completed`
   - `invoice.paid`
   - `invoice.payment_failed`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
6. Test a sign-up with card `4242 4242 4242 4242` (any future date and any CVC). The child should show **active** in the account page, and `/admin` should show 1 active subscription.

## 3. Claude and Stannp

- `ANTHROPIC_API_KEY`: from [console.anthropic.com](https://console.anthropic.com).
- `STANNP_API_KEY`: in your Stannp account, the key is at the bottom of the **Settings** page. The code uses Stannp's UK API (`https://api-eu1.stannp.com/v1`).
- Keep `LIVE_MAIL=false`. Approved letters then go to Stannp in **test mode**: Stannp makes a PDF proof and posts nothing.

## 4. Try the whole loop (still test mode)

1. Sign up as a parent with a test card.
2. In `/admin`, click **Run the daily job now**. The child's **introduction letter** is written and fact-checked, and appears under "Letters to check".
3. Read it, open the print PDF, then click **Approve and make test proof**. The "Recently posted" table links to Stannp's proof PDF. **Check that the address sits in the envelope window**, and compare against Stannp's letter template guidelines.
4. As the parent, upload a reply photo (any handwritten note), run the job again, and approve the answer.

## 5. Hosting

**Recommended setup:** one web service with a persistent disk, a Postgres database, and a daily scheduler.

| Piece | Suggestion | Notes |
|---|---|---|
| Web app | Render, Railway or Fly.io, deployed from the `Dockerfile` | The image includes Chromium for PDFs. Attach a **persistent disk at `/data`**, which holds uploads and letter PDFs |
| Database | **Supabase Postgres (London region)** or the host's Postgres | Set `DATABASE_URL` to the connection string. Gives managed backups. Tables are created on first start |
| Daily job | The host's cron, or a free scheduler such as cron-job.org | Once a day: `curl -X POST https://YOUR-DOMAIN/internal/run-due -H "Authorization: Bearer $CRON_SECRET"` |
| Email | Postmark, Resend or Brevo (SMTP) | Fill in the `SMTP_*` settings. Without them, password-reset links only appear in the server log |

Production settings that matter:
- `BASE_URL=https://your-domain`
- `SECRET_KEY` (long and random)
- `CRON_SECRET`
- `DATA_DIR=/data`
- `DATABASE_URL`

Keep the region in the UK or EU, and name your providers in `HOSTING_PROVIDER` and `EMAIL_PROVIDER` so the privacy policy lists them.

## 5b. Optional extras (recommended)

- **Sign in with Google**
  1. In [Google Cloud Console](https://console.cloud.google.com/), go to APIs & Services, then OAuth consent screen. Set it up for "External" users, with app name "Letters from History", your support email, and links to `/privacy` and `/terms`. Only the default scopes are needed: `openid`, `email` and `profile`.
  2. Credentials → Create OAuth client ID → Web application. Authorised redirect URI: `https://YOUR-DOMAIN/auth/google/callback` (and `http://localhost:8000/auth/google/callback` for testing).
  3. Put the ID and secret into `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`. The "Continue with Google" button appears automatically.
- **File storage off the server:** create a private bucket (e.g. Supabase Storage → New bucket, *not* public). Create S3 access keys, then set the `STORAGE_*` variables. Letter PDFs and held reply photos then survive server rebuilds.
- **Error alerts:** create a free [Sentry](https://sentry.io) project (EU data region) and set `SENTRY_DSN`. The app sends no form contents, children's details, cookies or IP addresses. Add Sentry to your supplier list.
- **Uptime check:** point a free monitor (e.g. UptimeRobot) at `https://YOUR-DOMAIN/healthz`.
- **Database migrations** run automatically when the Docker container starts (`alembic upgrade head`). After changing `app/models.py`, run `alembic revision --autogenerate -m "what changed"`, review the generated file, and commit it.

## 6. Legal and company details

1. Fill in `COMPANY_LEGAL_NAME`, `COMPANY_NUMBER`, `COMPANY_ADDRESS`, `ICO_NUMBER` and `CONTACT_EMAIL`. Until you do, the privacy policy and terms show highlighted placeholders.
2. **Register with the ICO** and pay the data protection fee: [ico.org.uk/fee](https://ico.org.uk/for-organisations/data-protection-fee/).
3. **Sign data processing terms** with Anthropic, Stripe, Stannp, your host and your email provider. Most accept them through their standard terms or a DPA page. These terms are what the privacy policy relies on.
4. **Have a solicitor review `/privacy` and `/terms`.** They were drafted to fit how this product actually works, but they aren't legal advice. Pay particular attention to children's data, the 14-day cancellation wording, and the DMCC Act 2024 subscription rules as they come into force.
5. Read `docs/uk-launch.md` for the full UK checklist.

## 6b. Check everything is connected

```bash
python -m app.manage check                 # every service, nothing sent or charged
python -m app.manage check --stannp-proof  # also makes a free Stannp test proof: open it and check the address window
```

Fix anything marked FAIL before going live. Run it again after every deploy.

## 7. Go live

- [ ] Stripe: switch to **live** keys, create the same two prices in live mode, and add a live webhook endpoint (new `whsec_`).
- [ ] Do one real end-to-end order with your own card and your own address. Check the letter arrives and the address shows in the window.
- [ ] Set `LIVE_MAIL=true`. Approved letters are now really printed and posted.
- [ ] Keep `REVIEW_ALL=true` until you trust the letters. Then set it to `false`, and only letters the fact-checker flags (and held replies) will wait for you.
- [ ] Check `/admin` every day: letters to check, held replies, failed sends.

## Safeguarding (before the first real child's letter)

- [ ] Name a **Designated Safeguarding Lead** and a deputy in `docs/safeguarding.md`, and set `ADMIN_EMAIL` to an inbox the lead checks every day.
- [ ] The lead and anyone reviewing letters complete basic safeguarding training and get a basic DBS check.
- [ ] Test the hold flow: upload a test reply saying "my dad hits me", run the job, and check you get the URGENT email and the reply appears under "held" in `/admin`.

## Admin tools for v1.0

- **Pause a child's letters** (e.g. while a safeguarding concern is handled): from a held reply, tick "Also pause letters", or use the Paused children section. Replies are still read and checked while paused. Resuming needs a reason for the record.
- **Safeguarding records** (`/admin/safeguarding`): closing a held reply requires notes. Every decision, pause and resume is logged with who did it and when, and kept even if the parent deletes their account.
- **Resend a lost letter:** "Resend (lost)" in Recently posted. It posts another copy of the same PDF.
- **Marketing emails:** only to parents who opted in. Always send them with `send_marketing_email`, which adds the unsubscribe link. Parents can also change this in their account.

## Day-to-day running

- **Daily (5-10 minutes):** read and approve letters in `/admin`, and handle any held replies (contact the parent if needed).
- **Monthly:**
  - Check the Stripe dashboard for failed payments.
  - Check Stannp costs against the `cost` column in "Recently posted".
  - Read a few posted letters end to end.
- **Adding a pen pal:**
  1. Add `letters/figures/<id>.md` (verified facts and "Handle with care").
  2. Add a stationery theme in `letters/render.py`.
  3. Add the id to `FIGURE_ORDER` in `app/config.py`.
