#!/usr/bin/env python3
"""Markup safety calculator for the Telegram Stars bot.

Given one real order's WATA numbers, prints:
  * the break-even operator markup (covers WATA commission),
  * the safe operator markup so the 5% referral never causes a loss,
  * your margin at a chosen markup (before and after the referral payout),
  * the partner markup cap (room left under WATA's +50% ceiling).

Get the numbers from a real order: ``min_price`` is WATA's minPrice per star;
``price`` and ``commission`` come back when the order is created (stored on the
Order row). Example (from this project's logs):

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
    p.add_argument("--partner", type=Decimal, default=Decimal("10"), help="partner commission pct")
    p.add_argument("--operator", type=Decimal, default=Decimal("20"), help="your markup percent")
    a = p.parse_args()

    min_total = a.min_price * a.count
    cost = a.price + a.commission
    # An order pays exactly one earner: the partner commission OR the 5% referral.
    # The binding constraint for "no loss" is the larger of the two.
    worst = max(a.referral, a.partner) / Decimal("100")

    break_even = (cost / min_total - 1) * 100
    safe = (cost / (1 - worst) / min_total - 1) * 100

    amount = min_total * (1 + a.operator / 100)
    margin = amount - cost
    margin_after_ref = margin - amount * a.referral / 100
    margin_after_partner = margin - amount * a.partner / 100

    print(f"min_total (minPrice*count) : {_pct(min_total)} RUB")
    print(f"cost (price + commission)  : {_pct(cost)} RUB")
    print(f"WATA commission of amount  : {_pct(a.commission / amount * 100)}%")
    print("-" * 52)
    print(f"break-even markup          : {_pct(max(Decimal('0'), break_even))}%")
    print(f"safe markup (no loss, worst of ref/partner): {_pct(max(Decimal('0'), safe))}%")
    print("-" * 52)
    print(f"at operator markup {a.operator}%:")
    print(f"  buyer pays (amount)      : {_pct(amount)} RUB")
    print(f"  margin                   : {_pct(margin)} RUB")
    print(f"  after {a.referral}% referral       : {_pct(margin_after_ref)} RUB"
          + ("  <-- LOSS" if margin_after_ref < 0 else ""))
    print(f"  after {a.partner}% partner cut   : {_pct(margin_after_partner)} RUB"
          + ("  <-- LOSS" if margin_after_partner < 0 else ""))
    print("-" * 52)
    print("Note: both the referral and the partner commission are paid OUT OF your")
    print("margin. Each order pays exactly one earner (partner OR referrer), so")
    print("multi-level invites cannot compound payouts.")


if __name__ == "__main__":
    main()
