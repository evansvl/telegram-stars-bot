"""Security and cursor-safety tests for the Toncenter v3 scanner."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from typing import Any

import aiohttp
import pytest
from app.ton.scanner import (
    TonScanner,
    TonScannerHttpError,
    TonScannerNetworkError,
    TonScannerProtocolError,
    _parse_transfer,
)

_DEST_RAW = "0:" + "00" * 32
_DEST_FRIENDLY = "EQAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAM9c"
_SOURCE_RAW = "0:" + "11" * 32
_OTHER_RAW = "0:" + "22" * 32


def _transaction(
    tx_hash: str = "tx-1",
    *,
    lt: int = 101,
    utime: int = 1_700_000_000,
    value: object = "1234567890",
    comment: object = "merchant",
) -> dict[str, Any]:
    return {
        "finality": 2,
        "emulated": False,
        "description": {"aborted": False},
        "account": _DEST_RAW,
        "hash": tx_hash,
        "lt": str(lt),
        "now": utime,
        "in_msg": {
            "bounced": False,
            "source": _SOURCE_RAW,
            "destination": _DEST_RAW,
            "value": value,
            "message_content": {"decoded": {"comment": comment}},
        },
    }


class _Response:
    def __init__(self, status: int, body: object, *, headers: dict[str, str] | None = None) -> None:
        self.status = status
        self._body = body
        self.headers = headers or {}

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def json(self, *, content_type: object = None) -> object:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _Session:
    def __init__(self, *responses: _Response | Exception) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, object]] = []

    def get(self, url: str, **kwargs: object) -> _Response:
        self.calls.append(
            {
                "url": url,
                "params": dict(kwargs.get("params", {})),  # type: ignore[arg-type]
                "headers": dict(kwargs.get("headers", {})),  # type: ignore[arg-type]
                "timeout": kwargs.get("timeout"),
            }
        )
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def _set_path(value: dict[str, Any], path: tuple[str, ...], replacement: object) -> None:
    cursor: dict[str, Any] = value
    for key in path[:-1]:
        cursor = cursor[key]
    cursor[path[-1]] = replacement


def test_parse_finalized_incoming_transfer() -> None:
    transfer = _parse_transfer(_transaction())

    assert transfer is not None
    assert transfer.lt == 101
    assert transfer.hash == "tx-1"
    assert transfer.value_nano == 1_234_567_890
    assert transfer.value_ton == Decimal("1.234567890")
    assert transfer.comment == "merchant"
    assert transfer.utime == 1_700_000_000
    assert transfer.source == _SOURCE_RAW
    assert transfer.destination == _DEST_RAW


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("finality",), 1),
        (("emulated",), True),
        (("description", "aborted"), True),
        (("in_msg", "bounced"), True),
        (("in_msg", "source"), ""),
        (("in_msg", "value"), 0),
    ],
)
def test_parse_skips_trustworthy_non_payment_transactions(
    path: tuple[str, ...], replacement: object
) -> None:
    raw = _transaction()
    _set_path(raw, path, replacement)

    assert _parse_transfer(raw) is None


def test_parse_skips_finalized_external_wallet_control_transaction() -> None:
    raw = _transaction()
    raw["in_msg"]["source"] = None
    raw["in_msg"]["bounced"] = None
    raw["in_msg"]["value"] = None

    assert _parse_transfer(raw, expected_destination_raw=_DEST_RAW) is None


@pytest.mark.parametrize(
    ("path", "replacement"),
    [
        (("finality",), None),
        (("emulated",), None),
        (("description", "aborted"), None),
        (("in_msg", "bounced"), None),
        (("in_msg", "destination"), " "),
        (("account",), _OTHER_RAW),
        (("in_msg", "value"), "-1"),
        (("in_msg", "value"), "1.5"),
        (("in_msg", "value"), True),
        (("hash",), ""),
        (("lt",), "0"),
        (("now",), 0),
    ],
)
def test_parse_fails_closed_on_malformed_transaction(
    path: tuple[str, ...], replacement: object
) -> None:
    raw = _transaction()
    _set_path(raw, path, replacement)

    with pytest.raises(TonScannerProtocolError):
        _parse_transfer(raw)


def test_parse_requires_configured_destination_not_just_response_self_consistency() -> None:
    raw = _transaction()
    raw["account"] = _OTHER_RAW
    raw["in_msg"]["destination"] = _OTHER_RAW

    with pytest.raises(TonScannerProtocolError):
        _parse_transfer(raw, expected_destination_raw=_DEST_RAW)


def test_friendly_configured_address_matches_canonical_raw_response() -> None:
    session = _Session()
    scanner = TonScanner(  # type: ignore[arg-type]
        "https://toncenter.example/api/v3", "", _DEST_FRIENDLY, session
    )

    assert scanner._address_raw == _DEST_RAW


def test_invalid_configured_address_is_rejected() -> None:
    with pytest.raises(ValueError, match="invalid TON deposit address"):
        TonScanner(  # type: ignore[arg-type]
            "https://toncenter.example/api/v3", "", "EQ-not-an-address", _Session()
        )


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ({}, ""),
        ({"decoded": None}, ""),
        ({"decoded": {"comment": 123}}, ""),
        ({"decoded": "plain text"}, "plain text"),
        ({"decoded": {"text": "text field"}}, "text field"),
        ({"comment": " direct "}, " direct "),
    ],
)
def test_comment_parsing_is_defensive_and_preserves_exact_text(
    content: dict[str, object], expected: str
) -> None:
    raw = _transaction()
    raw["in_msg"]["message_content"] = content

    transfer = _parse_transfer(raw)

    assert transfer is not None
    assert transfer.comment == expected


@pytest.mark.asyncio
async def test_scanner_paginates_and_uses_exact_utime_lt_cursor() -> None:
    first_page = {
        "transactions": [
            _transaction("cursor", lt=10, utime=100),
            _transaction("same-second", lt=11, utime=100),
        ]
    }
    second_page = {"transactions": [_transaction("newer", lt=12, utime=101)]}
    session = _Session(_Response(200, first_page), _Response(200, second_page))
    scanner = TonScanner(
        "https://toncenter.example/api/v3/",
        "api-secret",
        _DEST_FRIENDLY,
        session,  # type: ignore[arg-type]
    )

    transfers = await scanner.incoming_transfers(since_utime=100, since_lt=10, limit=2)

    assert [item.hash for item in transfers] == ["same-second", "newer"]
    assert len(session.calls) == 2
    assert session.calls[0]["url"] == "https://toncenter.example/api/v3/transactions"
    assert session.calls[0]["params"] == {
        "account": _DEST_FRIENDLY,
        "start_utime": 99,
        "start_lt": 10,
        "limit": 2,
        "offset": 0,
        "sort": "asc",
    }
    assert session.calls[1]["params"] == {
        **session.calls[0]["params"],  # type: ignore[dict-item]
        "offset": 2,
    }
    headers = session.calls[0]["headers"]
    assert headers == {
        "Accept": "application/json",
        "User-Agent": "telegram-stars-bot/1.0 (TON payment scanner)",
        "X-API-Key": "api-secret",
    }


@pytest.mark.asyncio
async def test_timestamp_only_cursor_replays_same_second_without_duplicates() -> None:
    duplicate = _transaction("same-hash", lt=10, utime=100)
    session = _Session(_Response(200, {"transactions": [duplicate, deepcopy(duplicate)]}))
    scanner = TonScanner(  # type: ignore[arg-type]
        "https://toncenter.example/api/v3", "", _DEST_FRIENDLY, session
    )

    transfers = await scanner.incoming_transfers(since_utime=100, limit=1000)

    assert [item.hash for item in transfers] == ["same-hash"]
    assert "X-API-Key" not in session.calls[0]["headers"]  # type: ignore[operator]


@pytest.mark.asyncio
async def test_rejected_optional_api_key_falls_back_once_and_stays_disabled() -> None:
    session = _Session(
        _Response(403, {"error": "bad key"}),
        _Response(200, {"transactions": []}),
        _Response(200, {"transactions": []}),
    )
    scanner = TonScanner(
        "https://toncenter.example/api/v3",
        "stale",
        _DEST_FRIENDLY,
        session,  # type: ignore[arg-type]
    )
    scanner._public_request_interval = 0

    assert await scanner.incoming_transfers(since_utime=0) == []
    assert await scanner.incoming_transfers(since_utime=0) == []

    assert session.calls[0]["headers"]["X-API-Key"] == "stale"  # type: ignore[index]
    assert "X-API-Key" not in session.calls[1]["headers"]  # type: ignore[operator]
    assert "X-API-Key" not in session.calls[2]["headers"]  # type: ignore[operator]


@pytest.mark.asyncio
async def test_rate_limit_retries_honor_retry_after() -> None:
    session = _Session(
        _Response(429, {"error": "slow down"}, headers={"Retry-After": "0"}),
        _Response(200, {"transactions": []}),
    )
    scanner = TonScanner(  # type: ignore[arg-type]
        "https://toncenter.example/api/v3", "api-key", _DEST_FRIENDLY, session
    )

    assert await scanner.incoming_transfers(since_utime=0) == []
    assert len(session.calls) == 2


@pytest.mark.asyncio
async def test_http_failure_on_later_page_raises_instead_of_returning_partial_data() -> None:
    session = _Session(
        _Response(
            200,
            {
                "transactions": [
                    _transaction("one", lt=1, utime=1),
                    _transaction("two", lt=2, utime=2),
                ]
            },
        ),
        _Response(500, {"error": "temporary"}),
    )
    scanner = TonScanner(  # type: ignore[arg-type]
        "https://toncenter.example/api/v3", "", _DEST_FRIENDLY, session
    )
    scanner._public_request_interval = 0

    with pytest.raises(TonScannerHttpError) as raised:
        await scanner.incoming_transfers(since_utime=0, limit=2)

    assert raised.value.status == 500


@pytest.mark.asyncio
async def test_network_and_protocol_failures_are_not_empty_results() -> None:
    network_session = _Session(aiohttp.ClientConnectionError("offline"))
    network_scanner = TonScanner(
        "https://toncenter.example/api/v3",
        "",
        _DEST_FRIENDLY,
        network_session,  # type: ignore[arg-type]
    )
    with pytest.raises(TonScannerNetworkError):
        await network_scanner.incoming_transfers(since_utime=0)

    malformed_session = _Session(_Response(200, ["not-an-object"]))
    malformed_scanner = TonScanner(
        "https://toncenter.example/api/v3",
        "",
        _DEST_FRIENDLY,
        malformed_session,  # type: ignore[arg-type]
    )
    with pytest.raises(TonScannerProtocolError):
        await malformed_scanner.incoming_transfers(since_utime=0)

    malformed_row_session = _Session(_Response(200, {"transactions": [123]}))
    malformed_row_scanner = TonScanner(
        "https://toncenter.example/api/v3",
        "",
        _DEST_FRIENDLY,
        malformed_row_session,  # type: ignore[arg-type]
    )
    with pytest.raises(TonScannerProtocolError):
        await malformed_row_scanner.incoming_transfers(since_utime=0)

    invalid_json_session = _Session(_Response(200, ValueError("bad json")))
    invalid_json_scanner = TonScanner(
        "https://toncenter.example/api/v3",
        "",
        _DEST_FRIENDLY,
        invalid_json_session,  # type: ignore[arg-type]
    )
    with pytest.raises(TonScannerProtocolError):
        await invalid_json_scanner.incoming_transfers(since_utime=0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"since_utime": -1},
        {"since_utime": 0, "since_lt": -1},
        {"since_utime": 0, "limit": 0},
        {"since_utime": 0, "limit": 1001},
        {"since_utime": 0, "limit": True},
    ],
)
@pytest.mark.asyncio
async def test_invalid_cursor_or_page_size_is_rejected(kwargs: dict[str, object]) -> None:
    scanner = TonScanner(
        "https://toncenter.example/api/v3",
        "",
        _DEST_FRIENDLY,
        _Session(),  # type: ignore[arg-type]
    )

    with pytest.raises(ValueError):
        await scanner.incoming_transfers(**kwargs)  # type: ignore[arg-type]
