"""1ai-auto-hunt CLI — command-line interface for all 7 hunts.

Entry-point (installed via ``pyproject.toml [project.scripts]``)::

    hunt factory create gmail --count 100
    hunt checkout monitor --url https://shopee.co.id/product/123 --budget 500000
    hunt domain scan --tld .com --min-da 20
    ...

Each hunt is a Click group with subcommands.  Output is rendered via Rich.
"""

from __future__ import annotations

import json
import sys
import time
from typing import Any

import click
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

console = Console()
err_console = Console(stderr=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _banner() -> None:
    """Print the 1ai-auto-hunt banner."""
    console.print(
        Panel.fit(
            "[bold cyan]1ai-auto-hunt[/] — [italic]I hunt money.[/]",
            border_style="bright_cyan",
        )
    )


def _success(msg: str) -> None:
    console.print(f"[bold green]✓[/] {msg}")


def _error(msg: str) -> None:
    err_console.print(f"[bold red]✗[/] {msg}")


def _info(msg: str) -> None:
    console.print(f"[bold blue]ℹ[/] {msg}")


def _make_table(title: str, columns: list[str]) -> Table:
    """Create a styled Rich table."""
    table = Table(title=title, show_header=True, header_style="bold magenta")
    for col in columns:
        table.add_column(col)
    return table


def _print_json(data: Any) -> None:
    """Pretty-print JSON output."""
    console.print_json(json.dumps(data, default=str, indent=2))


def _api_base() -> str:
    """Resolve API base URL from env or default."""
    import os

    return os.environ.get("HUNT_API_URL", "http://localhost:8000")


def _auth_headers() -> dict[str, str]:
    """Build auth headers from env token."""
    import os

    token = os.environ.get("HUNT_API_TOKEN", "")
    if token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _api_get(path: str, params: dict | None = None) -> dict:
    """GET request to the API."""
    import httpx

    resp = httpx.get(
        f"{_api_base()}{path}",
        params=params,
        headers=_auth_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _api_post(path: str, data: dict | None = None) -> dict:
    """POST request to the API."""
    import httpx

    resp = httpx.post(
        f"{_api_base()}{path}",
        json=data,
        headers=_auth_headers(),
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def _api_delete(path: str) -> None:
    """DELETE request to the API."""
    import httpx

    resp = httpx.delete(
        f"{_api_base()}{path}",
        headers=_auth_headers(),
        timeout=30,
    )
    resp.raise_for_status()


def _with_spinner(message: str, fn, *args, **kwargs):
    """Run a callable with a Rich spinner."""
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task(message, total=None)
        return fn(*args, **kwargs)


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(version="0.1.0", prog_name="1ai-auto-hunt")
def cli():
    """1ai-auto-hunt — Automated commerce hunting platform.

    I hunt money.
    """
    pass


# ===========================================================================
# hunt factory — Account Factory
# ===========================================================================

@cli.group("factory")
def factory():
    """Account factory — create, age, and sell accounts."""
    pass


@factory.command("create")
@click.argument("platform", type=click.Choice(["gmail", "instagram", "tiktok", "shopee"]))
@click.option("--count", "-n", type=int, default=10, help="Number of accounts to create")
@click.option("--proxy-pool", default=None, help="Named proxy pool to use")
@click.option("--phone-verify/--no-phone-verify", default=False, help="Enable phone verification")
def factory_create(platform: str, count: int, proxy_pool: str | None, phone_verify: bool):
    """Create new accounts for a platform."""
    _info(f"Creating {count} {platform} accounts...")
    try:
        result = _with_spinner(
            f"Creating {count} {platform} accounts...",
            _api_post,
            "/hunts/factory/accounts",
            {
                "platform": platform,
                "count": count,
                "proxy_pool": proxy_pool,
                "phone_verify": phone_verify,
            },
        )
        table = _make_table(f"Created {platform} Accounts", ["ID", "Username", "Status", "Proxy"])
        for acct in result.get("items", []):
            table.add_row(
                acct.get("id", ""),
                acct.get("username", ""),
                acct.get("status", ""),
                acct.get("proxy", ""),
            )
        console.print(table)
        _success(f"Created {len(result.get('items', []))} accounts")
    except Exception as exc:
        _error(f"Failed: {exc}")


@factory.command("list")
@click.option("--platform", "-p", type=click.Choice(["gmail", "instagram", "tiktok", "shopee"]))
@click.option("--status", "-s", type=click.Choice(["aging", "ready", "sold", "banned"]))
@click.option("--limit", "-l", type=int, default=50)
def factory_list(platform: str | None, status: str | None, limit: int):
    """List accounts with optional filters."""
    try:
        params: dict[str, Any] = {"limit": limit}
        if platform:
            params["platform"] = platform
        if status:
            params["status"] = status
        result = _with_spinner("Fetching accounts...", _api_get, "/hunts/factory/accounts", params)
        table = _make_table("Accounts", ["ID", "Platform", "Username", "Status", "Age (days)"])
        for acct in result.get("items", []):
            table.add_row(
                acct.get("id", ""),
                acct.get("platform", ""),
                acct.get("username", ""),
                acct.get("status", ""),
                str(acct.get("age_days", "")),
            )
        console.print(table)
        _info(f"Total: {result.get('total', 0)}")
    except Exception as exc:
        _error(f"Failed: {exc}")


@factory.command("sell")
@click.option("--order-id", required=True, help="Order ID to fulfill")
@click.option("--account-ids", multiple=True, help="Specific account IDs to sell")
def factory_sell(order_id: str, account_ids: tuple[str, ...]):
    """Sell/deliver accounts for an order."""
    _info(f"Fulfilling order {order_id}...")
    try:
        result = _with_spinner(
            "Processing sale...",
            _api_post,
            f"/hunts/factory/orders/{order_id}/fulfill",
            {"account_ids": list(account_ids)} if account_ids else {},
        )
        _success(f"Order {order_id} fulfilled — {result.get('delivered', 0)} accounts delivered")
    except Exception as exc:
        _error(f"Failed: {exc}")


# ===========================================================================
# hunt boost — Boost Service
# ===========================================================================

@cli.group("boost")
def boost():
    """Boost service — followers, likes, views as a service."""
    pass


@boost.command("order")
@click.option("--platform", "-p", required=True, type=click.Choice(["instagram", "tiktok", "youtube", "twitter"]))
@click.option("--action", "-a", required=True, type=click.Choice(["followers", "likes", "views", "comments"]))
@click.option("--target", "-t", required=True, help="Target URL (profile/post/video)")
@click.option("--qty", "-q", required=True, type=int, help="Quantity to deliver")
@click.option("--speed", default="normal", type=click.Choice(["slow", "normal", "fast"]))
def boost_order(platform: str, action: str, target: str, qty: int, speed: str):
    """Place a boost order."""
    _info(f"Ordering {qty} {action} for {target} on {platform}...")
    try:
        result = _with_spinner(
            "Placing order...",
            _api_post,
            "/hunts/boost/orders",
            {
                "platform": platform,
                "action": action,
                "target_url": target,
                "quantity": qty,
                "speed": speed,
            },
        )
        _success(f"Order created: {result.get('order_id', 'unknown')}")
        _print_json(result)
    except Exception as exc:
        _error(f"Failed: {exc}")


@boost.command("status")
@click.option("--order-id", required=True, help="Boost order ID")
def boost_status(order_id: str):
    """Check the status of a boost order."""
    try:
        result = _with_spinner("Checking status...", _api_get, f"/hunts/boost/orders/{order_id}")
        table = _make_table(f"Order {order_id}", ["Field", "Value"])
        for key, val in result.items():
            table.add_row(key, str(val))
        console.print(table)
    except Exception as exc:
        _error(f"Failed: {exc}")


@boost.command("list")
@click.option("--status", "-s", type=click.Choice(["pending", "running", "completed", "failed"]))
@click.option("--limit", "-l", type=int, default=50)
def boost_list(status: str | None, limit: int):
    """List boost orders."""
    try:
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        result = _with_spinner("Fetching orders...", _api_get, "/hunts/boost/orders", params)
        table = _make_table("Boost Orders", ["ID", "Platform", "Action", "Qty", "Done", "Status"])
        for order in result.get("items", []):
            table.add_row(
                order.get("id", ""),
                order.get("platform", ""),
                order.get("action", ""),
                str(order.get("quantity", "")),
                str(order.get("delivered", "")),
                order.get("status", ""),
            )
        console.print(table)
    except Exception as exc:
        _error(f"Failed: {exc}")


# ===========================================================================
# hunt checkout — Flash Sale Sniper
# ===========================================================================

@cli.group("checkout")
def checkout():
    """Flash sale auto-checkout — Shopee, Tokped, Lazada."""
    pass


@checkout.command("monitor")
@click.option("--url", required=True, help="Product page URL")
@click.option("--budget", required=True, type=float, help="Maximum price (IDR)")
@click.option("--threshold", type=float, default=None, help="Price threshold to trigger (IDR)")
@click.option("--platform", "-p", type=click.Choice(["shopee", "tokped", "lazada"]), default="shopee")
@click.option("--qty", type=int, default=1)
def checkout_monitor(url: str, budget: float, threshold: float | None, platform: str, qty: int):
    """Start monitoring a product for price drops."""
    _info(f"Monitoring {url} — budget: {budget:,.0f} IDR")
    try:
        result = _with_spinner(
            "Setting up monitor...",
            _api_post,
            "/hunts/checkout/monitors",
            {
                "url": url,
                "platform": platform,
                "budget": budget,
                "threshold": threshold,
                "quantity": qty,
                "auto_checkout": True,
            },
        )
        _success(f"Monitor {result.get('id', '')} created — status: {result.get('status', '')}")
    except Exception as exc:
        _error(f"Failed: {exc}")


@checkout.command("snipe")
@click.option("--url", required=True, help="Product page URL")
@click.option("--budget", required=True, type=float, help="Maximum price (IDR)")
@click.option("--platform", "-p", type=click.Choice(["shopee", "tokped", "lazada"]), default="shopee")
@click.option("--qty", type=int, default=1)
@click.option("--payment", default="wallet", help="Payment method")
def checkout_snipe(url: str, budget: float, platform: str, qty: int, payment: str):
    """Immediately attempt to purchase a product."""
    _info(f"Sniping {url} — budget: {budget:,.0f} IDR")
    try:
        result = _with_spinner(
            "Sniping product...",
            _api_post,
            "/hunts/checkout/snipe",
            {
                "url": url,
                "platform": platform,
                "budget": budget,
                "quantity": qty,
                "payment_method": payment,
            },
        )
        _success(f"Order {result.get('order_id', '')}: {result.get('status', '')}")
        _info(result.get("message", ""))
    except Exception as exc:
        _error(f"Failed: {exc}")


@checkout.command("orders")
@click.option("--status", "-s", type=click.Choice(["pending", "processing", "completed", "failed"]))
@click.option("--limit", "-l", type=int, default=50)
def checkout_orders(status: str | None, limit: int):
    """List checkout orders."""
    try:
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        result = _with_spinner("Fetching orders...", _api_get, "/hunts/checkout/orders", params)
        table = _make_table("Checkout Orders", ["ID", "Platform", "Status", "Qty", "Total", "Created"])
        for order in result.get("items", []):
            table.add_row(
                order.get("id", ""),
                order.get("platform", ""),
                order.get("status", ""),
                str(order.get("quantity", "")),
                f"{order.get('total_price', 0):,.0f}",
                order.get("created_at", ""),
            )
        console.print(table)
    except Exception as exc:
        _error(f"Failed: {exc}")


# ===========================================================================
# hunt domain — Expired Domain Scanner + Flipper
# ===========================================================================

@cli.group("domain")
def domain():
    """Domain hunter — scan expired domains and flip for profit."""
    pass


@domain.command("scan")
@click.option("--tld", multiple=True, default=[".com", ".net", ".org"], help="TLDs to scan")
@click.option("--min-da", type=int, default=0, help="Minimum Domain Authority")
@click.option("--min-pa", type=int, default=0, help="Minimum Page Authority")
@click.option("--max-price", type=float, default=50.0, help="Max registration price (USD)")
@click.option("--max-spam", type=float, default=10.0, help="Max spam score")
@click.option("--keywords", multiple=True, help="Required keywords")
@click.option("--limit", type=int, default=100)
def domain_scan(
    tld: tuple[str, ...],
    min_da: int,
    min_pa: int,
    max_price: float,
    max_spam: float,
    keywords: tuple[str, ...],
    limit: int,
):
    """Scan for expired domains matching filters."""
    _info(f"Scanning domains — DA≥{min_da}, PA≥{min_pa}, price≤${max_price}")
    try:
        result = _with_spinner(
            "Scanning expired domains...",
            _api_post,
            "/hunts/domain/scan",
            {
                "tlds": list(tld),
                "min_da": min_da,
                "min_pa": min_pa,
                "max_price": max_price,
                "max_spam_score": max_spam,
                "keywords": list(keywords),
                "limit": limit,
            },
        )
        _success(f"Scan {result.get('id', '')} started — status: {result.get('status', '')}")
        if result.get("results"):
            table = _make_table("Found Domains", ["Domain", "DA", "PA", "Spam", "Price", "Backlinks"])
            for d in result["results"]:
                table.add_row(
                    d.get("domain", ""),
                    str(d.get("da", "")),
                    str(d.get("pa", "")),
                    f"{d.get('spam_score', 0):.1f}",
                    f"${d.get('price_usd', 0):.2f}",
                    str(d.get("backlinks", "")),
                )
            console.print(table)
    except Exception as exc:
        _error(f"Failed: {exc}")


@domain.command("snipe")
@click.option("--domain-name", required=True, help="Domain to register")
@click.option("--registrar", default="namesilo", help="Registrar to use")
@click.option("--years", type=int, default=1, help="Registration period")
@click.option("--privacy/--no-privacy", default=True)
def domain_snipe(domain_name: str, registrar: str, years: int, privacy: bool):
    """Register a specific domain."""
    _info(f"Registering {domain_name} via {registrar} for {years} year(s)...")
    try:
        result = _with_spinner(
            "Registering domain...",
            _api_post,
            "/hunts/domain/snipe",
            {
                "domain": domain_name,
                "registrar": registrar,
                "years": years,
                "privacy": privacy,
            },
        )
        _success(f"{result.get('domain', '')}: {result.get('status', '')} — {result.get('message', '')}")
    except Exception as exc:
        _error(f"Failed: {exc}")


@domain.command("results")
@click.option("--min-da", type=int, default=0)
@click.option("--max-price", type=float, default=100.0)
@click.option("--limit", type=int, default=50)
def domain_results(min_da: int, max_price: float, limit: int):
    """List all found domains from scans."""
    try:
        result = _with_spinner(
            "Fetching results...",
            _api_get,
            "/hunts/domain/results",
            {"min_da": min_da, "max_price": max_price, "limit": limit},
        )
        table = _make_table("Domain Results", ["Domain", "DA", "PA", "Spam", "Price", "Status"])
        for d in result:
            table.add_row(
                d.get("domain", ""),
                str(d.get("da", "")),
                str(d.get("pa", "")),
                f"{d.get('spam_score', 0):.1f}",
                f"${d.get('price_usd', 0):.2f}",
                d.get("status", ""),
            )
        console.print(table)
    except Exception as exc:
        _error(f"Failed: {exc}")


@domain.command("portfolio")
@click.option("--limit", type=int, default=50)
def domain_portfolio(limit: int):
    """List domains in the portfolio."""
    try:
        result = _with_spinner(
            "Fetching portfolio...",
            _api_get,
            "/hunts/domain/portfolio",
            {"limit": limit},
        )
        table = _make_table("Domain Portfolio", ["Domain", "DA", "PA", "Cost", "Listing", "Status"])
        for d in result.get("items", []):
            table.add_row(
                d.get("domain", ""),
                str(d.get("da", "")),
                str(d.get("pa", "")),
                f"${d.get('purchase_price', 0):.2f}",
                f"${d.get('listing_price', 0):.2f}" if d.get("listing_price") else "—",
                d.get("status", ""),
            )
        console.print(table)
        _info(f"Total: {result.get('total', 0)}")
    except Exception as exc:
        _error(f"Failed: {exc}")


@domain.command("sell")
@click.option("--domain-name", required=True, help="Domain to list for sale")
@click.option("--price", required=True, type=float, help="Asking price (USD)")
@click.option("--marketplace", default="afternic", help="Marketplace to list on")
def domain_sell(domain_name: str, price: float, marketplace: str):
    """List a portfolio domain for sale."""
    _info(f"Listing {domain_name} at ${price:.2f} on {marketplace}...")
    try:
        result = _with_spinner(
            "Listing domain...",
            _api_post,
            "/hunts/domain/sell",
            {
                "domain": domain_name,
                "asking_price": price,
                "marketplace": marketplace,
            },
        )
        _success(result.get("message", "Listed"))
    except Exception as exc:
        _error(f"Failed: {exc}")


# ===========================================================================
# hunt stream — Streaming Farm
# ===========================================================================

@cli.group("stream")
def stream():
    """Streaming farm — Spotify / Apple Music play generation."""
    pass


@stream.command("farm")
@click.option("--platform", "-p", type=click.Choice(["spotify", "apple_music", "youtube_music"]), default="spotify")
@click.option("--accounts", required=True, type=int, help="Number of accounts to use")
@click.option("--playlist", default=None, help="Playlist ID to stream")
@click.option("--plays", type=int, default=10, help="Plays per account")
@click.option("--hours", type=float, default=8.0, help="Session duration in hours")
@click.option("--geo", default=None, help="Country code for geo-targeting")
def stream_farm(
    platform: str,
    accounts: int,
    playlist: str | None,
    plays: int,
    hours: float,
    geo: str | None,
):
    """Start a streaming farm session."""
    _info(f"Starting {platform} farm — {accounts} accounts, {plays} plays each, {hours}h")
    try:
        result = _with_spinner(
            "Starting farm...",
            _api_post,
            "/hunts/stream/farm",
            {
                "platform": platform,
                "account_count": accounts,
                "playlist_id": playlist,
                "plays_per_account": plays,
                "duration_hours": hours,
                "geo_target": geo,
            },
        )
        _success(f"Farm {result.get('id', '')} — status: {result.get('status', '')}")
    except Exception as exc:
        _error(f"Failed: {exc}")


@stream.command("farms")
@click.option("--platform", "-p", type=click.Choice(["spotify", "apple_music", "youtube_music"]))
@click.option("--status", "-s", type=click.Choice(["starting", "running", "paused", "stopped", "error"]))
@click.option("--limit", type=int, default=50)
def stream_farms(platform: str | None, status: str | None, limit: int):
    """List streaming farm sessions."""
    try:
        params: dict[str, Any] = {"limit": limit}
        if platform:
            params["platform"] = platform
        if status:
            params["status"] = status
        result = _with_spinner("Fetching farms...", _api_get, "/hunts/stream/farms", params)
        table = _make_table("Stream Farms", ["ID", "Platform", "Accounts", "Plays", "Status", "Revenue"])
        for farm in result.get("items", []):
            table.add_row(
                farm.get("id", ""),
                farm.get("platform", ""),
                str(farm.get("account_count", "")),
                f"{farm.get('completed_plays', 0)}/{farm.get('total_plays', 0)}",
                farm.get("status", ""),
                f"${farm.get('estimated_revenue', 0):.2f}",
            )
        console.print(table)
    except Exception as exc:
        _error(f"Failed: {exc}")


@stream.command("revenue")
@click.option("--month", required=True, help="Month in YYYY-MM format")
@click.option("--platform", "-p", type=click.Choice(["spotify", "apple_music", "youtube_music"]))
def stream_revenue(month: str, platform: str | None):
    """Get revenue report for a month."""
    try:
        params: dict[str, Any] = {"month": month}
        if platform:
            params["platform"] = platform
        result = _with_spinner("Fetching revenue...", _api_get, "/hunts/stream/revenue", params)
        table = _make_table(f"Revenue — {month}", ["Metric", "Value"])
        table.add_row("Total Plays", str(result.get("total_plays", 0)))
        table.add_row("Total Hours", f"{result.get('total_hours', 0):.1f}")
        table.add_row("Revenue", f"${result.get('estimated_revenue_usd', 0):.2f}")
        table.add_row("Proxy Cost", f"${result.get('cost_proxy_usd', 0):.2f}")
        table.add_row("Net Profit", f"${result.get('net_profit_usd', 0):.2f}")
        console.print(table)
    except Exception as exc:
        _error(f"Failed: {exc}")


# ===========================================================================
# hunt kdp — KDP Book Factory
# ===========================================================================

@cli.group("kdp")
def kdp():
    """KDP publisher — AI book generation and Amazon publishing."""
    pass


@kdp.command("generate")
@click.option("--topic", required=True, help="Book topic or title")
@click.option("--genre", type=click.Choice([
    "nonfiction", "fiction", "self_help", "business", "technology", "children", "cooking", "other",
]), default="nonfiction")
@click.option("--chapters", type=int, default=10, help="Number of chapters")
@click.option("--words", type=int, default=2000, help="Words per chapter")
@click.option("--count", type=int, default=1, help="Number of book variants")
@click.option("--language", default="en", help="Language code")
@click.option("--images/--no-images", default=False, help="Generate illustrations")
def kdp_generate(
    topic: str,
    genre: str,
    chapters: int,
    words: int,
    count: int,
    language: str,
    images: bool,
):
    """Generate AI-authored books."""
    _info(f"Generating {count} book(s) on '{topic}' ({genre}, {chapters} chapters)")
    try:
        result = _with_spinner(
            "Generating books...",
            _api_post,
            "/hunts/kdp/generate",
            {
                "topic": topic,
                "genre": genre,
                "chapter_count": chapters,
                "words_per_chapter": words,
                "count": count,
                "language": language,
                "include_images": images,
            },
        )
        table = _make_table("Generated Books", ["ID", "Title", "Status", "Chapters"])
        for book in result:
            table.add_row(
                book.get("id", ""),
                book.get("title", ""),
                book.get("status", ""),
                str(book.get("chapter_count", "")),
            )
        console.print(table)
        _success(f"Generated {len(result)} book(s)")
    except Exception as exc:
        _error(f"Failed: {exc}")


@kdp.command("publish")
@click.option("--book-id", required=True, help="Book ID to publish")
@click.option("--format", "fmt", type=click.Choice(["ebook", "paperback", "hardcover"]), default="ebook")
@click.option("--price", required=True, type=float, help="List price (USD)")
@click.option("--categories", multiple=True, help="KDP categories (max 3)")
@click.option("--keywords", multiple=True, help="KDP keywords (max 7)")
@click.option("--kdp-select/--no-kdp-select", default=True, help="Enroll in KDP Select")
def kdp_publish(
    book_id: str,
    fmt: str,
    price: float,
    categories: tuple[str, ...],
    keywords: tuple[str, ...],
    kdp_select: bool,
):
    """Publish a book to Amazon KDP."""
    _info(f"Publishing {book_id} as {fmt} at ${price:.2f}")
    try:
        result = _with_spinner(
            "Publishing to KDP...",
            _api_post,
            "/hunts/kdp/publish",
            {
                "book_id": book_id,
                "format": fmt,
                "price_usd": price,
                "categories": list(categories),
                "keywords": list(keywords),
                "enable_kdp_select": kdp_select,
            },
        )
        _success(f"{result.get('book_id', '')}: {result.get('status', '')}")
        if result.get("asin"):
            _info(f"ASIN: {result['asin']}")
    except Exception as exc:
        _error(f"Failed: {exc}")


@kdp.command("books")
@click.option("--status", "-s", type=click.Choice([
    "generating", "generated", "formatting", "formatted", "publishing", "published", "failed",
]))
@click.option("--genre", type=click.Choice([
    "nonfiction", "fiction", "self_help", "business", "technology", "children", "cooking", "other",
]))
@click.option("--limit", type=int, default=50)
def kdp_books(status: str | None, genre: str | None, limit: int):
    """List books in the pipeline."""
    try:
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        if genre:
            params["genre"] = genre
        result = _with_spinner("Fetching books...", _api_get, "/hunts/kdp/books", params)
        table = _make_table("KDP Books", ["ID", "Title", "Genre", "Status", "Words", "Sales"])
        for book in result.get("items", []):
            table.add_row(
                book.get("id", ""),
                book.get("title", ""),
                book.get("genre", ""),
                book.get("status", ""),
                str(book.get("total_words", "")),
                str(book.get("sales_count", "")),
            )
        console.print(table)
    except Exception as exc:
        _error(f"Failed: {exc}")


@kdp.command("revenue")
@click.option("--month", required=True, help="Month in YYYY-MM format")
def kdp_revenue(month: str):
    """Get KDP revenue report."""
    try:
        result = _with_spinner("Fetching revenue...", _api_get, "/hunts/kdp/revenue", {"month": month})
        table = _make_table(f"KDP Revenue — {month}", ["Metric", "Value"])
        table.add_row("Total Books", str(result.get("total_books", 0)))
        table.add_row("Total Sales", str(result.get("total_sales", 0)))
        table.add_row("Royalty", f"${result.get('total_royalty_usd', 0):.2f}")
        table.add_row("KENP Reads", str(result.get("kenp_reads", 0)))
        table.add_row("KENP Royalty", f"${result.get('kenp_royalty_usd', 0):.2f}")
        console.print(table)
    except Exception as exc:
        _error(f"Failed: {exc}")


# ===========================================================================
# hunt media — Deepfake / AI Media
# ===========================================================================

@cli.group("media")
def media():
    """Deepfake and AI media — voice cloning, AI influencer factory."""
    pass


@media.command("voice-clone")
@click.option("--input", "input_file", required=True, help="Source audio file path")
@click.option("--text", required=True, help="Text to synthesize in cloned voice")
@click.option("--output", "output_file", default=None, help="Output file path")
@click.option("--language", default="en", help="Language code")
@click.option("--stability", type=float, default=0.5, help="Voice stability (0-1)")
@click.option("--clarity", type=float, default=0.75, help="Voice clarity (0-1)")
def media_voice_clone(
    input_file: str,
    text: str,
    output_file: str | None,
    language: str,
    stability: float,
    clarity: float,
):
    """Clone a voice and synthesize speech."""
    _info(f"Cloning voice from {input_file}...")
    try:
        result = _with_spinner(
            "Cloning voice...",
            _api_post,
            "/hunts/media/voice-clone",
            {
                "source_audio_path": input_file,
                "text": text,
                "output_format": "mp3",
                "language": language,
                "stability": stability,
                "clarity": clarity,
            },
        )
        _success(f"Voice clone {result.get('id', '')}: {result.get('status', '')}")
        if result.get("output_url"):
            _info(f"Output: {result['output_url']}")
    except Exception as exc:
        _error(f"Failed: {exc}")


@media.command("ai-influencer")
@click.option("--name", required=True, help="Influencer display name")
@click.option("--persona", required=True, help="Personality description")
@click.option("--platforms", multiple=True, default=["instagram", "tiktok"])
@click.option("--voice-id", default=None, help="Voice clone ID for audio content")
@click.option("--frequency", default="daily", type=click.Choice(["daily", "twice_daily", "weekly"]))
@click.option("--posts", type=int, default=30, help="Number of posts to generate")
def media_ai_influencer(
    name: str,
    persona: str,
    platforms: tuple[str, ...],
    voice_id: str | None,
    frequency: str,
    posts: int,
):
    """Create an AI influencer persona."""
    _info(f"Creating AI influencer '{name}'...")
    try:
        result = _with_spinner(
            "Creating influencer...",
            _api_post,
            "/hunts/media/ai-influencer",
            {
                "name": name,
                "persona": persona,
                "platforms": list(platforms),
                "voice_id": voice_id,
                "posting_frequency": frequency,
            },
        )
        _success(f"Influencer {result.get('id', '')} created: {result.get('name', '')}")
        _info(f"Platforms: {', '.join(result.get('platforms', []))}")
    except Exception as exc:
        _error(f"Failed: {exc}")


@media.command("influencers")
@click.option("--status", "-s", type=click.Choice(["creating", "active", "paused", "retired"]))
@click.option("--limit", type=int, default=50)
def media_influencers(status: str | None, limit: int):
    """List AI influencers."""
    try:
        params: dict[str, Any] = {"limit": limit}
        if status:
            params["status"] = status
        result = _with_spinner("Fetching influencers...", _api_get, "/hunts/media/influencers", params)
        table = _make_table("AI Influencers", ["ID", "Name", "Platforms", "Posts", "Followers", "Status"])
        for inf in result.get("items", []):
            table.add_row(
                inf.get("id", ""),
                inf.get("name", ""),
                ", ".join(inf.get("platforms", [])),
                str(inf.get("total_posts", "")),
                str(inf.get("total_followers", "")),
                inf.get("status", ""),
            )
        console.print(table)
    except Exception as exc:
        _error(f"Failed: {exc}")


@media.command("generate-post")
@click.option("--influencer-id", required=True, help="Influencer ID")
@click.option("--platform", required=True, help="Target platform")
@click.option("--type", "content_type", type=click.Choice(["image", "video", "audio", "text"]), default="image")
@click.option("--topic", default=None, help="Post topic")
@click.option("--caption", default=None, help="Caption text")
@click.option("--schedule", default=None, help="Schedule datetime (ISO 8601)")
def media_generate_post(
    influencer_id: str,
    platform: str,
    content_type: str,
    topic: str | None,
    caption: str | None,
    schedule: str | None,
):
    """Generate a post for an AI influencer."""
    _info(f"Generating {content_type} post for {influencer_id} on {platform}...")
    try:
        result = _with_spinner(
            "Generating post...",
            _api_post,
            "/hunts/media/generate-post",
            {
                "influencer_id": influencer_id,
                "platform": platform,
                "content_type": content_type,
                "topic": topic,
                "caption": caption,
                "schedule_at": schedule,
            },
        )
        _success(f"Post {result.get('id', '')}: {result.get('status', '')}")
        if result.get("caption"):
            _info(f"Caption: {result['caption'][:100]}...")
    except Exception as exc:
        _error(f"Failed: {exc}")


# ===========================================================================
# Auth commands (top-level)
# ===========================================================================

@cli.command("login")
@click.option("--email", required=True, help="Account email")
@click.option("--password", required=True, hide_input=True, help="Account password")
def login_cmd(email: str, password: str):
    """Log in and store the JWT token."""
    try:
        result = _with_spinner(
            "Logging in...",
            _api_post,
            "/auth/login",
            {"email": email, "password": password},
        )
        token = result.get("access_token", "")
        if token:
            import os

            os.environ["HUNT_API_TOKEN"] = token
            _success(f"Logged in — token expires in {result.get('expires_in', 0)}s")
            _info("Set HUNT_API_TOKEN environment variable to persist across sessions:")
            console.print(f"  export HUNT_API_TOKEN={token}")
        else:
            _error("No token returned")
    except Exception as exc:
        _error(f"Login failed: {exc}")


@cli.command("me")
def me_cmd():
    """Show current user profile."""
    try:
        result = _with_spinner("Fetching profile...", _api_get, "/auth/me")
        table = _make_table("User Profile", ["Field", "Value"])
        for key, val in result.items():
            table.add_row(key, str(val))
        console.print(table)
    except Exception as exc:
        _error(f"Failed: {exc}")


# ===========================================================================
# Entry point
# ===========================================================================

def main():
    """CLI entry point."""
    _banner()
    cli(standalone_mode=True)


if __name__ == "__main__":
    main()
