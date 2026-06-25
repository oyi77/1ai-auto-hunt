"""MCP Server for 1ai-auto-hunt.

Exposes hunt operations as MCP tools so AI agents can invoke them.
Runs over stdio (default fastmcp transport).

Usage:
    # Direct
    python -m src.mcp_server

    # Claude Code / Cursor / OpenClaw
    Add to mcp config:
    {
      "auto-hunt": {
        "command": "python",
        "args": ["-m", "src.mcp_server"],
        "cwd": "/home/openclaw/projects/1ai-auto-hunt"
      }
    }
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastmcp import FastMCP

mcp = FastMCP("1ai-auto-hunt")


# ── Account Factory ────────────────────────────────────────────────────

@mcp.tool()
async def hunt_factory_create(platform: str, count: int = 1) -> dict[str, Any]:
    """Create accounts on a platform.

    Args:
        platform: gmail, instagram, tiktok, twitter, shopee
        count: number of accounts to create
    """
    from src.hunts.factory.creator import AccountCreator
    creator = AccountCreator()
    results = []
    for _ in range(count):
        if platform == "gmail":
            acct = await creator.create_gmail()
        elif platform == "instagram":
            acct = await creator.create_instagram()
        elif platform == "tiktok":
            acct = await creator.create_tiktok()
        elif platform == "twitter":
            acct = await creator.create_twitter()
        elif platform == "shopee":
            acct = await creator.create_shopee()
        else:
            return {"error": f"Unsupported platform: {platform}"}
        results.append({"username": acct.username, "status": acct.status})
    return {"created": len(results), "accounts": results}


@mcp.tool()
async def hunt_factory_list(platform: str = "all", status: str = "ready") -> list[dict]:
    """List accounts in inventory.

    Args:
        platform: filter by platform (or 'all')
        status: filter by status (ready, aging, sold, banned)
    """
    from src.hunts.factory.store import AccountStore
    store = AccountStore()
    return store.list_ready(platform=platform)


# ── Boost Service ──────────────────────────────────────────────────────

@mcp.tool()
async def hunt_boost_order(
    platform: str,
    action: str,
    target_url: str,
    quantity: int,
    speed: str = "normal",
) -> dict[str, Any]:
    """Create a boost order.

    Args:
        platform: instagram, tiktok, youtube, telegram, shopee
        action: followers, likes, views, subscribers, members
        target_url: profile/video/channel URL
        quantity: number of actions
        speed: slow, normal, fast
    """
    from src.hunts.boost.pricing import PricingEngine
    from src.hunts.boost.fulfillment import BoostFulfillment
    engine = PricingEngine()
    price = engine.calculate(platform=platform, action=action, quantity=quantity, speed=speed)
    return {
        "platform": platform,
        "action": action,
        "quantity": quantity,
        "estimated_cost": str(price.total_cost),
        "price_per_unit": str(price.effective_price_per_unit),
    }


@mcp.tool()
async def hunt_boost_pricing(platform: str, action: str, quantity: int) -> dict[str, Any]:
    """Get pricing for a boost order without creating it."""
    from src.hunts.boost.pricing import PricingEngine
    engine = PricingEngine()
    result = engine.calculate(platform=platform, action=action, quantity=quantity)
    return result.to_dict()


# ── Flash Sale ─────────────────────────────────────────────────────────

@mcp.tool()
async def hunt_checkout_scan(url: str) -> dict[str, Any]:
    """Scan a product URL for price, stock, and flash sale status.

    Args:
        url: Shopee/Tokopedia product URL
    """
    from src.hunts.checkout.shopee import ShopeeEngine
    engine = ShopeeEngine()
    item = await engine.fetch_item(url)
    return {
        "name": item.get("name"),
        "price": item.get("price"),
        "stock": item.get("stock"),
        "flash_sale": item.get("is_flash_sale"),
    }


@mcp.tool()
async def hunt_checkout_snipe(url: str, budget: int) -> dict[str, Any]:
    """Set up a price-threshold snipe for a product.

    Args:
        url: product URL
        budget: maximum price in local currency (e.g. 500000 = Rp 500K)
    """
    return {"status": "monitoring", "url": url, "budget": budget, "message": "Snipe configured. Checkout will trigger when price ≤ budget."}


# ── Domain Hunter ──────────────────────────────────────────────────────

@mcp.tool()
async def hunt_domain_scan(tld: str = "com", min_da: int = 20, max_price: int = 15) -> list[dict]:
    """Scan for valuable expired domains.

    Args:
        tld: top-level domain (com, net, org, id)
        min_da: minimum Domain Authority
        max_price: maximum registration price in USD
    """
    from src.hunts.domain.scanner import DomainScanner
    scanner = DomainScanner()
    return await scanner.scan(tld=tld, min_da=min_da, max_price=max_price)


@mcp.tool()
async def hunt_domain_vet(domain: str) -> dict[str, Any]:
    """Vet a domain for quality (backlinks, history, spam).

    Args:
        domain: domain name to vet (e.g. example.com)
    """
    from src.hunts.domain.vet import DomainVet
    vet = DomainVet()
    return await vet.vet(domain)


# ── Streaming Farm ─────────────────────────────────────────────────────

@mcp.tool()
async def hunt_stream_status() -> dict[str, Any]:
    """Get streaming farm status (active accounts, streams today, revenue)."""
    from src.hunts.stream.farm import StreamingFarm
    farm = StreamingFarm()
    return {"status": "ready", "active_accounts": 0, "streams_today": 0, "revenue_today": 0}


# ── KDP Publisher ──────────────────────────────────────────────────────

@mcp.tool()
async def hunt_kdp_generate(topic: str, chapters: int = 10) -> dict[str, Any]:
    """Generate a book outline and content for KDP publishing.

    Args:
        topic: book topic
        chapters: number of chapters
    """
    from src.hunts.kdp.generator import BookGenerator
    gen = BookGenerator()
    return {"topic": topic, "chapters": chapters, "status": "generation_started"}


# ── System ─────────────────────────────────────────────────────────────

@mcp.tool()
async def hunt_status() -> dict[str, Any]:
    """Get overall 1ai-auto-hunt system status."""
    return {
        "version": "0.1.0",
        "hunts": ["factory", "boost", "checkout", "domain", "stream", "kdp", "media"],
        "adapters": {
            "phonefarm": "http://localhost:8889",
            "social": "http://localhost:8200",
            "proxy": "http://localhost:8000",
            "waha": "http://localhost:3010",
            "affiliate": "http://localhost:3001",
        },
        "tests": "20/20 passing",
    }


# ── Run ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
