#!/usr/bin/env python3
"""Traffic generator for demos: fills the dashboard and helps trigger alerts.

Standard library only, so it runs anywhere Python 3.11+ is installed
(including Windows PowerShell):

    python scripts/load-test.py --url http://<alb-host> --api-key <key> --rps 20 --duration 300

Sends a realistic mix: ~70% valid, ~20% declined (rule violations), ~10% malformed.
"""

from __future__ import annotations

import argparse
import json
import random
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

VALID_CARDS = ["4111111111111111", "5555555555554444", "378282246310005"]


def make_payload(rng: random.Random) -> dict:
    roll = rng.random()
    payload = {
        "transaction_id": str(uuid.uuid4()),
        "amount": f"{rng.uniform(1, 3000):.2f}",
        "currency": rng.choice(["SGD", "USD", "EUR", "MYR"]),
        "card_number": rng.choice(VALID_CARDS),
        "card_expiry": "12/30",
        "merchant_id": "MER-LOAD01",
        "country": rng.choice(["SG", "MY", "US", "GB"]),
    }
    if roll < 0.70:
        return payload
    if roll < 0.90:  # business-rule violations -> DECLINED
        violation = rng.choice(["amount", "card", "country", "currency", "expired"])
        if violation == "amount":
            payload["amount"] = "25000.00"
        elif violation == "card":
            payload["card_number"] = "4111111111111112"
        elif violation == "country":
            payload["country"] = "KP"
        elif violation == "currency":
            payload["currency"] = "XYZ"
        else:
            payload["card_expiry"] = "01/20"
        return payload
    payload.pop("amount")  # malformed -> 400
    return payload


def send(url: str, api_key: str, payload: dict) -> str:
    request = urllib.request.Request(  # noqa: S310 - URL supplied by the operator
        f"{url}/validate",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json", "X-API-Key": api_key},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
            return str(response.status)
    except urllib.error.HTTPError as err:
        return str(err.code)
    except (urllib.error.URLError, TimeoutError):
        return "conn-error"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", required=True, help="base URL, e.g. http://localhost:8080")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--rps", type=float, default=10, help="requests per second")
    parser.add_argument("--duration", type=int, default=120, help="seconds")
    parser.add_argument("--workers", type=int, default=20)
    args = parser.parse_args()

    url = args.url.rstrip("/")
    rng = random.Random()  # noqa: S311 - load generation, not security
    stats: Counter[str] = Counter()
    lock = threading.Lock()

    def task() -> None:
        status = send(url, args.api_key, make_payload(rng))
        with lock:
            stats[status] += 1

    deadline = time.monotonic() + args.duration
    next_report = time.monotonic() + 10
    interval = 1 / args.rps
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        while time.monotonic() < deadline:
            pool.submit(task)
            time.sleep(interval)
            if time.monotonic() >= next_report:
                with lock:
                    print(f"[{time.strftime('%H:%M:%S')}] status counts: {dict(stats)}", flush=True)
                next_report += 10
    print(f"done. totals: {dict(stats)}")


if __name__ == "__main__":
    main()
