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

## How the markups stack

1. **Operator markup (`MARKUP_PERCENT`)** — your own margin, applied on every bot
   (yours and partners'). Set on the server.
2. **Referral 5% (`REFERRAL_PERCENT`)** — paid to a referrer **out of your
   margin** when a referred user pays on the main bot.
3. **Partner markup** — a partner-bot owner sets **their own** markup, charged
   **on top of** your operator markup, so a buyer on a partner bot pays *more*
   than on your main bot. The partner earns that extra markup, **minus your cut**
   (`PARTNER_COMMISSION_PERCENT`, default 10% *of the partner's markup*). Because
   the total is still clamped to WATA's +50%, the partner's markup is capped at
   `50% − MARKUP_PERCENT` (`PartnerService.max_partner_markup`).

The referral 5% comes out of your margin; the partner markup does **not** — it is
extra money the buyer pays, of which you even keep a cut. An order never pays both
a partner and a referrer (see below).

## Key results

- **Break-even markup**: because WATA's commission alone is ~7–8%, selling at the
  bare minimum (`amount = minPrice*count`) actually loses the commission. You need
  ~1–2% markup just to cover it (it varies per order — run the calculator).

- **Safe markup (main bot)**: you keep money only if `amount*(1 − 0.05) ≥ price +
  commission` once a referral applies. For the sample numbers that's ≈ **9%**.

- **At the default 20% markup** (sample numbers): buyer pays 79.20₽, margin
  12.36₽ → **8.40₽** after a 5% referral. Safe.

- **Partner sales are strictly more profitable for you.** The buyer pays operator
  + partner markup; you keep your full operator margin **plus** 10% of the partner
  markup. Example: partner sets 20% on top of your 20%. On 50 stars the buyer pays
  92.40₽; the extra 13.20₽ is the partner markup, of which the partner gets 11.88₽
  and you keep 1.32₽ on top of your normal 12.36₽ margin (→ 13.68₽).

- **No multi-level compounding.** Each paid order credits **exactly one** earner:
  if it came through a partner bot, the partner is paid their markup share;
  otherwise the buyer's referrer earns 5%. There is no stacking, so "someone
  invites someone who invites someone" can never multiply a single order's payout.

## Recommendation

- Keep **`MARKUP_PERCENT = 20`** with **`PARTNER_COMMISSION_PERCENT = 10`**. That
  clears the ~8% WATA commission and the 5% referral with margin to spare, and
  leaves partners room for up to a **30%** markup of their own under the +50% cap.
- Never let `MARKUP_PERCENT` drop below roughly **`REFERRAL_PERCENT +
  WATA_commission`** (~13% here) or referred main-bot sales start losing money.
- `PARTNER_COMMISSION_PERCENT` is your slice of the partner's markup, so it only
  ever *adds* to your take — raising it earns you more without risk.

Run the calculator with your own order's numbers before changing anything:

```bash
python scripts/markup_calc.py --min-price 1.32 --count 50 --price 61.12 --commission 5.72
```
