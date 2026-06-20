#!/usr/bin/env python3
"""Markup safety calculator for the Telegram Stars bot.

Given one real order's WATA numbers, prints:
  * the break-even operator markup (covers WATA commission),
  * the safe operator markup so the 5% referral never causes a loss,
  * your margin at a chosen markup (before and after the referral payout),
  * the partner markup cap (room left under WATA's +50% ceiling),
  * a partner sale: what the buyer pays, what the partner earns, and your cut.

The partner sets their own markup on top of yours; the buyer pays more and you
keep a cut (``--partner`` percent) of that extra markup, so partner sales only
ever add to your take. Get the numbers from a real order: ``min_price`` is WATA's
minPrice per star; ``price`` and ``commission`` come back when the order is
created (stored on the Order row). Example (from this project's logs):

    python scripts/markup_calc.py --min-price 1.32 --count 50 --price 61.12 --commission 5.72
"""

from __future__ import annotations

import argparse
from decimal import Decimal


def _pct(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--min-price", type=Decimal, required=True, help="WATA minPrice per star (RUB)")
    p.add_argument("--count", type=int, default=50, help="stars in the sample order")
    p.add_argument("--price", type=Decimal, required=True, help="WATA purchase price for the order")
    p.add_argument("--commission", type=Decimal, required=True, help="WATA commission")
    p.add_argument("--referral", type=Decimal, default=Decimal("5"), help="referral percent")
    p.add_argument("--partner", type=Decimal, default=Decimal("10"),
                   help="operator cut, pct of the partner's markup")
    p.add_argument("--partner-markup", type=Decimal, default=Decimal("20"),
                   help="markup a partner sets on top of yours")
    p.add_argument("--operator", type=Decimal, default=Decimal("20"), help="your markup percent")
    a = p.parse_args()

    min_total = a.min_price * a.count
    cost = a.price + a.commission
    # On the main bot the only payout is the 5% referral; it sets the no-loss floor.
    ref = a.referral / Decimal("100")

    break_even = (cost / min_total - 1) * 100
    safe = (cost / (1 - ref) / min_total - 1) * 100

    amount = min_total * (1 + a.operator / 100)
    margin = amount - cost
    margin_after_ref = margin - amount * a.referral / 100

    # Partner sale: total markup capped at +50%, partner markup capped accordingly.
    cap = max(Decimal("0"), Decimal("50") - a.operator)
    pmarkup = min(a.partner_markup, cap)
    total_amount = min_total * (1 + (a.operator + pmarkup) / 100)
    gross_markup = total_amount - amount  # extra the buyer pays vs the main bot
    partner_earn = gross_markup * (1 - a.partner / 100)
    operator_cut = gross_markup - partner_earn

    print(f"min_total (minPrice*count) : {_pct(min_total)} RUB")
    print(f"cost (price + commission)  : {_pct(cost)} RUB")
    print(f"WATA commission of amount  : {_pct(a.commission / amount * 100)}%")
    print("-" * 52)
    print(f"break-even markup          : {_pct(max(Decimal('0'), break_even))}%")
    print(f"safe markup (no loss after 5% referral): {_pct(max(Decimal('0'), safe))}%")
    print(f"partner markup cap (50 - operator): {_pct(cap)}%")
    print("-" * 52)
    print(f"main bot at operator markup {a.operator}%:")
    print(f"  buyer pays (amount)      : {_pct(amount)} RUB")
    print(f"  margin                   : {_pct(margin)} RUB")
    print(f"  after {a.referral}% referral       : {_pct(margin_after_ref)} RUB"
          + ("  <-- LOSS" if margin_after_ref < 0 else ""))
    print("-" * 52)
    print(f"partner bot at partner markup {_pct(pmarkup)}% (cut {a.partner}%):")
    print(f"  buyer pays (amount)      : {_pct(total_amount)} RUB")
    print(f"  partner earns            : {_pct(partner_earn)} RUB")
    print(f"  your margin + cut        : {_pct(margin + operator_cut)} RUB")
    print("-" * 52)
    print("Note: the referral is paid OUT OF your margin; the partner markup is")
    print("extra money the buyer pays, of which you keep a cut. Each order pays")
    print("exactly one earner (partner OR referrer), so invites cannot compound.")


if __name__ == "__main__":
    main()
