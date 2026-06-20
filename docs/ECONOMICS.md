# Markup economics — how not to lose money

This explains how the markups, the referral reward and the partner program
interact, and what to set so you never sell at a loss. Use
`scripts/markup_calc.py` to plug in your real WATA numbers.

## The numbers

Per order WATA gives you:

- `minPrice` — minimum sale price per star.
- `price` — what WATA charges **you** (purchase cost).
- `commission` — WATA's fee.

The buyer pays `amount`, constrained by WATA:

```
minPrice*count  ≤  amount  ≤  minPrice*count * 1.5      (markup capped at +50%)
```

Your margin on an order is `amount − price − commission`.

From a real order in this project (50 stars): `minPrice=1.32`, so
`minPrice*count = 66.00`; `price+commission ≈ 66.84`; WATA commission ≈ **7–8%**
of the amount.

## Three things that take money out

1. **Operator markup (`MARKUP_PERCENT`)** — your own margin. Set on the server.
2. **Referral 5% (`REFERRAL_PERCENT`)** — paid to a referrer **out of your
   margin** when a referred user pays.
3. **Partner commission (`PARTNER_COMMISSION_PERCENT`, default 10%)** — paid to a
   partner-bot owner **out of your margin** on every sale through their bot.
   Buyers on a partner bot pay the *same* price as on your main bot.

Both 2 and 3 come out of your margin, so your markup must be thick enough to
cover the larger of the two. An order never pays both (see below).

## Key results

- **Break-even markup**: because WATA's commission alone is ~7–8%, selling at the
  bare minimum (`amount = minPrice*count`) actually loses the commission. You need
  ~1–2% markup just to cover it (it varies per order — run the calculator).

- **Safe markup**: you keep money only if `amount*(1 − p) ≥ price + commission`,
  where `p` is the larger payout rate (the 10% partner commission). For the sample
  numbers that's ≈ **12.5%**.

- **At the default 20% markup** (sample numbers): buyer pays 79.20₽, margin
  12.36₽ → **8.40₽** after a 5% referral, or **4.44₽** after a 10% partner
  commission. Both safe.

- **No multi-level compounding.** Each paid order credits **exactly one** earner:
  if it came through a partner bot, the partner is paid their commission;
  otherwise the buyer's referrer earns 5%. There is no stacking, so "someone
  invites someone who invites someone" can never multiply a single order's payout.

## Recommendation

- Keep **`MARKUP_PERCENT = 20`** with **`PARTNER_COMMISSION_PERCENT = 10`**. That
  clears the ~8% WATA commission and the 10% partner cut with margin to spare.
- Never let `MARKUP_PERCENT` drop below roughly **`PARTNER_COMMISSION_PERCENT +
  WATA_commission`** (~18% here) or partner-bot sales start losing money.
- If you raise the partner commission, raise your markup by at least as much.

Run the calculator with your own order's numbers before changing anything:

```bash
python scripts/markup_calc.py --min-price 1.32 --count 50 --price 61.12 --commission 5.72
```
