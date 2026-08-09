# Launch checklist

Everything the code can do is done. What remains needs credentials or accounts
only you can create. In dependency order.

---

## 1. AssemblyAI cost per hour — 2 minutes

Every margin figure is provisional without this. It is a dashboard lookup.

```bash
echo 'ASSEMBLYAI_COST_PER_HOUR=0.00' >> .env    # your real rate
sudo systemctl restart qaboom
```

Until it is set, `usage_events.vendor_cost_micros` records **0** for
transcription rather than a guess — cost reporting reads obviously empty
instead of quietly wrong.

Sanity check afterwards, at the measured 31-minute average call:

| | per call |
|---|---|
| Grading (Opus 5, measured) | **$0.073** |
| Transcription | *your rate × 0.52 hours* |
| **All-in** | *sum* |

Compare that against the effective per-minute price on each plan
(`plans.py`). Starter bills $0.15/min effective, Growth $0.10/min.

---

## 2. Stripe — 20 minutes

Everything is built and tested against test keys. No real card can be charged
until you deliberately swap them.

1. Create a Stripe account (test mode is on by default).
2. Put the secret key in `.env`:
   ```bash
   echo 'STRIPE_SECRET_KEY=sk_test_...' >> .env
   ```
3. Create the products and prices from `plans.py` — do **not** click these into
   the dashboard by hand, or test and live mode will drift:
   ```bash
   python3 scripts/stripe_setup.py --dry-run
   python3 scripts/stripe_setup.py
   ```
   It prints the `STRIPE_PRICE_*` lines to add to `.env`.
4. Add a webhook endpoint in the Stripe dashboard pointing at
   `https://qaboom.io/billing/webhook`, subscribed to:
   - `customer.subscription.created` / `.updated` / `.deleted`
   - `invoice.created` — this is what posts the overage line
   - `invoice.paid`

   Put its signing secret in `.env` as `STRIPE_WEBHOOK_SECRET`.
5. `sudo systemctl restart qaboom`

**Then run the flow end to end**, in test mode, before telling anyone:
sign up fresh → upload a call → watch the sidebar meter move → `/billing/` →
Checkout with card `4242 4242 4242 4242` → confirm the subscription row exists
and the plan page shows the new allowance.

### Going live

Swap `sk_test_` for `sk_live_`, re-run `stripe_setup.py` against the live key
(it refuses by default — you will need to remove the guard deliberately), add a
live webhook endpoint, and update the `STRIPE_PRICE_*` values. **Validate the
price points with real prospects first** — the numbers in `plans.py` are a
starting frame, not researched pricing.

---

## 3. Clerk production instance — 30 minutes, mostly DNS propagation

This is what removes the "Development mode" badge from the sign-in page every
prospect sees.

1. Clerk dashboard → create a **production** instance for qaboom.io.
2. Add the CNAME records it gives you at Namecheap (`clerk`, `accounts`,
   `clkmail`, and two DKIM records).
3. Wait for Clerk to verify them.
4. Swap both values in `.env`:
   ```
   CLERK_PUBLISHABLE_KEY=pk_live_...
   CLERK_JWKS_URL=https://clerk.qaboom.io/.well-known/jwks.json
   ```
5. `sudo systemctl restart qaboom`

> Existing users are keyed by Clerk user ID. A production instance issues
> **new** IDs, so the four existing accounts will not carry over — they sign up
> again and get fresh, empty orgs. With 4 orgs of test data that is fine; do it
> before you have customers, not after.

---

## 4. Decide the retention window — a conversation, not a setting

`organizations.retention_days` is NULL everywhere, which means keep audio
forever. The job runs nightly and deletes nothing until you set a number.

At ~3.6 MB and 31 minutes per call, 1,000 calls a month is ~3.6 GB/month
growing without bound, doubled by the backup mirror. The 97 GB disk is fine for
now and will not be at volume — object storage is the real answer before then.

Ask a compliance-minded customer what they need to keep, then:

```sql
UPDATE organizations SET retention_days = 730 WHERE id = '...';   -- 24 months
```

```bash
python3 scripts/retention.py --dry-run    # always dry-run first
```

Only the audio file is removed. The call, transcript, report and usage row all
survive — a report keeps its evidence text and loses only playback.

---

## Already running

| | |
|---|---|
| Nightly backup + audio mirror | 03:15, 14-day retention, self-verifying |
| Nightly maintenance | 03:45, retention (inert) + rate-limit purge |
| HSTS and Secure cookies | live |
| CSRF | live on every state-changing POST |
| Usage metering | live; 13 existing calls backfilled |
| Abuse limits | 120 uploads/hour/org, 25 concurrent |
| Startup recovery | re-queues calls stranded by a deploy |

Logs: `/home/claude/backups/qaboom.log` and `nightly.log`.
