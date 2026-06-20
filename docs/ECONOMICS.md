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
   margin** when a referred user pays. This is the only payout that can cause a
   loss if your markup is too thin.
3. **Partner markup** — added by a partner **on top of** your markup. The buyer
   pays it; it does **not** come out of your margin. Self-funding → no loss.

## Key results

- **Break-even markup**: because WATA's commission alone is ~7–8%, selling at the
  bare minimum (`amount = minPrice*count`) actually loses the commission. You need
  ~1–2% markup just to cover it (it varies per order — run the calculator).

- **Safe markup with the 5% referral**: you keep money on a referred sale only if
  `amount*(1 − 5%) ≥ price + commission`. For the sample numbers that's ≈ **6.6%**.
  Anything above that and the referral never costs you money.

- **At the default 20% markup** (sample numbers): buyer pays 79.20₽, margin
  12.36₽, and **8.40₽ after** the 5% referral. Comfortably safe.

- **Partner markup is free for you.** A partner setting +30% just makes their
  buyers pay 30% more, which the partner keeps. Your margin is unchanged. The bot
  enforces `partner markup ≤ 50% − MARKUP_PERCENT` (`PartnerService.max_partner_markup`)
  so the total stays under WATA's +50% ceiling.

- **No multi-level compounding.** Each paid order credits **exactly one** earner:
  if it came through a partner bot, the partner is paid their pre-computed markup;
  otherwise the buyer's referrer earns 5%. There is no stacking, so "someone
  invites someone who invites someone" can never multiply a single order's payout.

## Recommendation

- Keep **`MARKUP_PERCENT = 20`** (default). It clears the ~8% WATA commission and
  the 5% referral with margin to spare, and still leaves partners **30%** of room.
- Never drop below **~10%** — under that the 5% referral plus commission can wipe
  out (or invert) the margin on referred sales.
- Want partners to have more room? Lower your markup — but each point you give up
  is a point off your own margin, and don't cross the ~10% floor.

Run the calculator with your own order's numbers before changing anything:

```bash
python scripts/markup_calc.py --min-price 1.32 --count 50 --price 61.12 --commission 5.72
```
