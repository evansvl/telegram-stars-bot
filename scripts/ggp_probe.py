#!/usr/bin/env python3
"""Read-only probe for the GreenGamePay API (пополнистим.рф).

Confirms the documented behaviour before we build a provider, and answers the
open question: can PaymentHub create an invoice for a non-Steam (stars) flow,
and does paying it fund the balance that ``buyStars`` spends?

It performs ONLY safe calls:
  * POST /api/token              (auth)
  * POST /api/check_balance      (read)
  * POST /api/get_stars_price    (read)
  * POST /pay_hub/create_invoice (creates a PENDING invoice — no money moves
                                  until a human actually pays the link)
  * POST /pay_hub/get_invoice    (read)

It NEVER calls /api/buyStars, so nothing is delivered and your balance is not
spent. The access token is redacted from all output.

Run it without committing secrets — pass credentials via env vars:

  PowerShell:
    $env:GGP_USERNAME="evansvl"; $env:GGP_PASSWORD="..."
    .venv\\Scripts\\python.exe scripts\\ggp_probe.py
  bash:
    GGP_USERNAME=evansvl GGP_PASSWORD=... .venv/Scripts/python.exe scripts/ggp_probe.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any

BASE = os.environ.get("GGP_BASE", "https://пополнистим.рф")


def _ascii_url(url: str) -> str:
    """IDNA-encode the host so urllib can resolve the Cyrillic domain."""
    scheme, _, rest = url.partition("://")
    host, slash, path = rest.partition("/")
    host_ascii = host.encode("idna").decode("ascii")
    return f"{scheme}://{host_ascii}{slash}{path}"


def _post(path: str, body: dict[str, Any], token: str | None = None) -> tuple[int, Any]:
    url = _ascii_url(f"{BASE}{path}")
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, _parse(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, _parse(exc.read())
    except Exception as exc:
        return 0, {"error": str(exc)}


def _parse(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {"raw": raw.decode("utf-8", "replace")}


def _show(title: str, status: int, body: Any) -> None:
    print(f"\n=== {title}  (HTTP {status}) ===")
    print(json.dumps(body, ensure_ascii=False, indent=2))


def main() -> int:
    username = os.environ.get("GGP_USERNAME")
    password = os.environ.get("GGP_PASSWORD")
    if not username or not password:
        print("Set GGP_USERNAME and GGP_PASSWORD env vars first.", file=sys.stderr)
        return 2

    print(f"base: {BASE}  (-> {_ascii_url(BASE)})")

    status, body = _post("/api/token", {"username": username, "password": password})
    token = body.get("access_token") if isinstance(body, dict) else None
    safe = dict(body) if isinstance(body, dict) else {"body": body}
    if "access_token" in safe:
        safe["access_token"] = "<REDACTED>"
    _show("token", status, safe)
    if not token:
        print("\nNo token — stopping.", file=sys.stderr)
        return 1

    _show("check_balance", *_post("/api/check_balance", {}, token))
    _show("get_stars_price (50)", *_post("/api/get_stars_price", {"quantity": 50}, token))

    # Poll mode: after paying an invoice, set GGP_POLL_ID=<payment_id> to see if
    # the status flipped to PAID and whether check_balance went up (above).
    poll_id = os.environ.get("GGP_POLL_ID")
    if poll_id:
        _show("get_invoice (poll)", *_post("/pay_hub/get_invoice", {"payment_id": poll_id}, token))
        print("\nDone (poll mode). Compare check_balance above to its pre-payment value.")
        return 0

    # Create a PaymentHub invoice. Nothing is charged until someone opens and pays
    # the returned sub_link. All fields overridable via env for real top-ups.
    amount_pay = float(os.environ.get("GGP_AMOUNT_PAY", "120"))
    amount_receive = float(os.environ.get("GGP_AMOUNT_RECEIVE", "100"))
    steam_user = os.environ.get("GGP_STEAM_USER", "probe_test")
    payment_type = os.environ.get("GGP_PAYMENT_TYPE", "CARD")
    invoice_req = {
        "payment_type": payment_type,
        "amount_pay": amount_pay,
        "amount_receive": amount_receive,
        "currency": "RUB",
        "url_success": None,
        "steam_username": steam_user,
        "payment_info": f"top up {steam_user}",
        "required_telegram_username": None,
    }
    status, body = _post("/pay_hub/create_invoice", invoice_req, token)
    _show("create_invoice", status, body)
    payment_id = body.get("payment_id") if isinstance(body, dict) else None
    if payment_id:
        _show("get_invoice", *_post("/pay_hub/get_invoice", {"payment_id": payment_id}, token))
        print(f"\nTo run the real PAID test: open the sub_link above, pay it, then re-run with\n"
              f"  set GGP_POLL_ID={payment_id}")

    print("\nDone. No buyStars call was made; nothing was delivered or spent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
