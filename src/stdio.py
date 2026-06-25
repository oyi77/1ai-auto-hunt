"""stdio interface for 1ai-auto-hunt.

Allows other tools/agents to invoke hunt operations via stdin/stdout JSON-RPC.

Usage:
    echo '{"method":"hunt.factory.create","params":{"platform":"gmail","count":1}}' | python -m src.stdio

    # Or as subprocess from another Python tool:
    result = subprocess.run(
        ["python", "-m", "src.stdio"],
        input=json.dumps({"method": "hunt.scan", "params": {"url": "..."}}),
        capture_output=True, text=True
    )
    response = json.loads(result.stdout)
"""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any


# ── Method Registry ────────────────────────────────────────────────────

METHODS: dict[str, Any] = {}


def method(name: str):
    """Register a stdio method."""
    def decorator(fn):
        METHODS[name] = fn
        return fn
    return decorator


@method("hunt.status")
async def hunt_status(**_: Any) -> dict:
    return {
        "version": "0.1.0",
        "hunts": ["factory", "boost", "checkout", "domain", "stream", "kdp", "media"],
        "methods": list(METHODS.keys()),
    }


@method("hunt.factory.create")
async def hunt_factory_create(platform: str, count: int = 1) -> dict:
    from src.hunts.factory.creator import AccountCreator
    creator = AccountCreator()
    results = []
    for _ in range(count):
        if platform == "gmail":
            acct = await creator.create_gmail()
        elif platform == "instagram":
            acct = await creator.create_instagram()
        else:
            return {"error": f"Unsupported platform: {platform}"}
        results.append({"username": acct.username, "status": acct.status})
    return {"created": len(results), "accounts": results}


@method("hunt.boost.pricing")
async def hunt_boost_pricing(platform: str, action: str, quantity: int) -> dict:
    from src.hunts.boost.pricing import PricingEngine
    engine = PricingEngine()
    result = engine.calculate(platform=platform, action=action, quantity=quantity)
    return result.to_dict()


@method("hunt.checkout.scan")
async def hunt_checkout_scan(url: str) -> dict:
    from src.hunts.checkout.shopee import ShopeeEngine
    engine = ShopeeEngine()
    item = await engine.fetch_item(url)
    return {
        "name": item.get("name"),
        "price": item.get("price"),
        "stock": item.get("stock"),
    }


@method("hunt.domain.scan")
async def hunt_domain_scan(tld: str = "com", min_da: int = 20) -> list:
    from src.hunts.domain.scanner import DomainScanner
    scanner = DomainScanner()
    return await scanner.scan(tld=tld, min_da=min_da)


# ── Main Loop ──────────────────────────────────────────────────────────

async def handle_request(req: dict) -> dict:
    """Process a single JSON-RPC request."""
    method_name = req.get("method", "")
    params = req.get("params", {})
    request_id = req.get("id")

    if method_name not in METHODS:
        return {"id": request_id, "error": {"code": -32601, "message": f"Method not found: {method_name}"}}

    try:
        result = await METHODS[method_name](**params)
        return {"id": request_id, "result": result}
    except Exception as e:
        return {"id": request_id, "error": {"code": -32000, "message": str(e)}}


async def main() -> None:
    """Read JSON requests from stdin, write responses to stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError:
            resp = {"error": {"code": -32700, "message": "Parse error"}}
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
            continue

        resp = await handle_request(req)
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())
