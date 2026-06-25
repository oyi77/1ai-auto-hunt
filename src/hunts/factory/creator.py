"""Automated account creation for multiple platforms.

Each ``create_<platform>()`` method follows the same flow:

1. Rent a proxy from the proxy manager.
2. Rent a phone number from the phone verifier.
3. Launch a Playwright browser with the proxy.
4. Navigate to the registration page.
5. Fill the form with generated credentials.
6. Solve any captcha that appears.
7. Submit the verification code received via SMS.
8. Store the account in the database.

``AccountCreator`` wraps the core services (``ProxyManager``,
``CaptchaSolver``, ``PhoneVerifier``) as async context managers internally.
Each ``create_*`` method is a standalone async call — no outer context
manager required.
"""

from __future__ import annotations

import asyncio
import random
import string
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from src.core.captcha import CaptchaSolver
from src.core.db import SessionLocal
from src.core.exceptions import AccountCreationError, CaptchaError, PhoneVerificationError
from src.core.logger import get_logger
from src.core.phone import PhoneVerifier
from src.core.proxy import ProxyManager
from src.hunts.factory.models import Account, AccountStatus, Platform

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# SMS-Activate service codes per platform
# ---------------------------------------------------------------------------

_PLATFORM_SERVICE: dict[str, str] = {
    "gmail": "go",
    "instagram": "ig",
    "tiktok": "tk",
    "twitter": "tw",
    "shopee": "sh",
}


# ---------------------------------------------------------------------------
# Credential generation helpers
# ---------------------------------------------------------------------------

_NAMES_FIRST = [
    "Andi", "Budi", "Citra", "Dewi", "Eka", "Fajar", "Gita",
    "Hadi", "Indra", "Joko", "Kartika", "Lestari", "Maya",
    "Nanda", "Omar", "Putri", "Rizky", "Sari", "Taufik", "Utami",
]

_NAMES_LAST = [
    "Pratama", "Wijaya", "Santoso", "Hidayat", "Nugroho",
    "Putra", "Putri", "Saputra", "Anggraini", "Lestari",
]


def _random_name() -> tuple[str, str]:
    """Generate a random Indonesian-sounding name."""
    return random.choice(_NAMES_FIRST), random.choice(_NAMES_LAST)


def _random_username(prefix: str = "") -> str:
    """Generate a unique username."""
    suffix = uuid.uuid4().hex[:6]
    base = prefix or random.choice(_NAMES_FIRST).lower()
    return f"{base}_{suffix}"


def _random_password(length: int = 16) -> str:
    """Generate a strong random password."""
    chars = string.ascii_letters + string.digits + "!@#$%"
    return "".join(random.choices(chars, k=length))


def _random_email(username: str) -> str:
    """Generate an email using Gmail dot-trick variants.

    The dot-trick lets many unique signups funnel into a single real
    Gmail inbox by inserting ``.`` at random positions.
    """
    domains = ["gmail.com", "googlemail.com"]
    indices = sorted(random.sample(range(len(username)), min(3, len(username))))
    dotted = ".".join(username[i] for i in indices)
    return f"{dotted}@{random.choice(domains)}"


def _random_birthday() -> tuple[int, int, int]:
    """Generate a random birthday for someone aged 20–35."""
    return random.randint(1990, 2004), random.randint(1, 12), random.randint(1, 28)


# ---------------------------------------------------------------------------
# Browser helpers
# ---------------------------------------------------------------------------

async def _launch_browser(proxy_url: str | None):
    """Launch a Playwright Chromium browser with anti-detection patches.

    Returns ``(playwright, browser, context, page)`` — caller must close all.
    """
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    launch_opts: dict = {
        "headless": True,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--no-first-run",
            "--no-default-browser-check",
        ],
    }
    if proxy_url:
        launch_opts["proxy"] = {"server": proxy_url}

    browser = await pw.chromium.launch(**launch_opts)
    context = await browser.new_context(
        viewport={"width": 1366, "height": 768},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        locale="id-ID",
        timezone_id="Asia/Jakarta",
    )
    # Stealth patches — hide webdriver fingerprint
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'languages', { get: () => ['id-ID', 'id', 'en'] });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
    """)
    page = await context.new_page()
    return pw, browser, context, page


async def _cleanup_browser(pw, browser, context, page) -> None:
    """Safely close all browser resources."""
    for resource in (page, context, browser):
        try:
            await resource.close()
        except Exception:
            pass
    try:
        await pw.stop()
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Captcha detection
# ---------------------------------------------------------------------------

async def _detect_and_solve_captcha(
    page, solver: CaptchaSolver, site_url: str
) -> bool:
    """Detect and solve any captcha on the current page.

    Returns ``True`` if a captcha was found and solved.
    """
    # reCAPTCHA iframe
    try:
        recaptcha_frame = page.frame_locator("iframe[src*='recaptcha']")
        site_key_el = await recaptcha_frame.locator("[data-sitekey]").element_handle(timeout=3000)
        if site_key_el:
            site_key = await site_key_el.get_attribute("data-sitekey")
            if site_key:
                token = await solver.solve_recaptcha_v2(site_url=site_url, site_key=site_key)
                await page.evaluate(
                    "token => { const el = document.getElementById('g-recaptcha-response'); if (el) el.value = token; }",
                    token,
                )
                logger.info("recaptcha_solved")
                return True
    except Exception:
        pass

    # hCaptcha iframe
    try:
        hcaptcha_frame = page.frame_locator("iframe[src*='hcaptcha']")
        site_key_el = await hcaptcha_frame.locator("[data-sitekey]").element_handle(timeout=3000)
        if site_key_el:
            site_key = await site_key_el.get_attribute("data-sitekey")
            if site_key:
                token = await solver.solve_hcaptcha(site_url=site_url, site_key=site_key)
                await page.evaluate(
                    "token => { const el = document.querySelector('[name=h-captcha-response]'); if (el) el.value = token; }",
                    token,
                )
                logger.info("hcaptcha_solved")
                return True
    except Exception:
        pass

    return False


# ---------------------------------------------------------------------------
# Database helper
# ---------------------------------------------------------------------------

async def _save_account(
    platform: Platform,
    username: str,
    password: str,
    email: str | None,
    phone: str | None,
    proxy_ip: str,
    status: AccountStatus,
    device_serial: str | None = None,
) -> Account:
    """Insert a new ``Account`` row and return it."""
    session = SessionLocal()
    try:
        account = Account(
            platform=platform,
            username=username,
            password=password,
            email=email,
            phone=phone,
            proxy_ip=proxy_ip,
            device_serial=device_serial,
            status=status,
            age_days=0,
            followers=0,
            sell_price=0.50,
        )
        session.add(account)
        await session.commit()
        await session.refresh(account)
        return account
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


# ---------------------------------------------------------------------------
# AccountCreator
# ---------------------------------------------------------------------------

class AccountCreator:
    """Create accounts on various platforms using browser automation.

    Usage::

        creator = AccountCreator()
        account = await creator.create_gmail()

    Each method is self-contained: it manages proxy, phone, captcha, and
    browser lifecycles internally.
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def create_gmail(self) -> Account:
        """Create a Gmail account using the dot-trick.

        Flow: Google signup → name → birthday → choose username (dot-trick)
        → password → phone verify → captcha → agree ToS → store.
        """
        first, last = _random_name()
        base_username = _random_username(first.lower())
        password = _random_password()
        email = _random_email(base_username)
        year, month, day = _random_birthday()

        pw = browser = context = page = None
        async with ProxyManager() as pm:
            proxy_url = await pm.get_proxy("http")
            async with PhoneVerifier() as pv:
                num = await pv.get_number(_PLATFORM_SERVICE["gmail"])

                try:
                    pw, browser, context, page = await _launch_browser(proxy_url)

                    await page.goto(
                        "https://accounts.google.com/signup", wait_until="networkidle"
                    )
                    await asyncio.sleep(random.uniform(1.0, 3.0))

                    # Step 1: Name
                    await page.fill('input[name="firstName"]', first)
                    await page.fill('input[name="lastName"]', last)
                    await page.click("#collectNameNext")
                    await page.wait_for_load_state("networkidle")
                    await asyncio.sleep(random.uniform(1.0, 2.0))

                    # Step 2: Birthday
                    await page.select_option("#month", str(month))
                    await page.fill("#day", str(day))
                    await page.fill("#year", str(year))
                    await page.select_option("#gender", str(random.choice([1, 2])))
                    await page.click("#birthdaygenderNext")
                    await page.wait_for_load_state("networkidle")
                    await asyncio.sleep(random.uniform(1.0, 2.0))

                    # Step 3: Choose username
                    username_input = page.locator('input[name="Username"]')
                    if await username_input.count() > 0:
                        await username_input.fill(base_username)
                        await page.click("#next")
                        await page.wait_for_load_state("networkidle")
                        await asyncio.sleep(random.uniform(1.0, 2.0))

                    # Step 4: Password
                    pwd_input = page.locator('input[name="Passwd"]')
                    pwd2_input = page.locator('input[name="PasswdAgain"]')
                    if await pwd_input.count() > 0:
                        await pwd_input.fill(password)
                        if await pwd2_input.count() > 0:
                            await pwd2_input.fill(password)
                        await page.click("#createpasswordNext")
                        await page.wait_for_load_state("networkidle")
                        await asyncio.sleep(random.uniform(1.0, 2.0))

                    # Step 5: Phone verification
                    phone_input = page.locator(
                        '#phoneNumberId, input[name="phoneNumber"]'
                    )
                    if await phone_input.count() > 0:
                        await phone_input.fill(num.phone)
                        await page.click("#next, button[type='submit']")
                        await asyncio.sleep(random.uniform(3.0, 6.0))
                        code = await pv.get_code(num.activation_id, timeout=120)
                        code_input = page.locator('#code, input[name="code"]')
                        if await code_input.count() > 0:
                            await code_input.fill(code)
                            await page.click("#next, button[type='submit']")
                            await page.wait_for_load_state("networkidle")

                    # Step 6: Captcha
                    async with CaptchaSolver() as cs:
                        await _detect_and_solve_captcha(page, cs, page.url)

                    # Step 7: Agree to ToS
                    agree_btn = page.locator(
                        'button:has-text("I agree"), button:has-text("Setuju")'
                    )
                    if await agree_btn.count() > 0:
                        await agree_btn.click()
                        await page.wait_for_load_state("networkidle")

                    account = await _save_account(
                        platform=Platform.GMAIL,
                        username=base_username,
                        password=password,
                        email=email,
                        phone=num.phone,
                        proxy_ip=proxy_url or "direct",
                        status=AccountStatus.FRESH,
                    )
                    logger.info(
                        "gmail_created",
                        account_id=account.id,
                        username=base_username,
                    )
                    return account

                except Exception as exc:
                    logger.error("gmail_creation_failed", error=str(exc))
                    try:
                        await pv.cancel(num.activation_id)
                    except Exception:
                        pass
                    await _save_account(
                        platform=Platform.GMAIL,
                        username=base_username,
                        password=password,
                        email=email,
                        phone=num.phone,
                        proxy_ip=proxy_url or "direct",
                        status=AccountStatus.FAILED,
                    )
                    raise AccountCreationError(
                        f"Gmail creation failed: {exc}",
                        context={"platform": "gmail", "username": base_username},
                    ) from exc
                finally:
                    if pw:
                        await _cleanup_browser(pw, browser, context, page)

    async def create_instagram(self) -> Account:
        """Create an Instagram account.

        Flow: IG signup → email → full name → username → password →
        birthday → verification code → captcha → store.
        """
        first, last = _random_name()
        full_name = f"{first} {last}"
        username = _random_username(first.lower())
        password = _random_password()
        email = _random_email(username)
        year, month, day = _random_birthday()

        pw = browser = context = page = None
        async with ProxyManager() as pm:
            proxy_url = await pm.get_proxy("http")
            async with PhoneVerifier() as pv:
                num = await pv.get_number(_PLATFORM_SERVICE["instagram"])

                try:
                    pw, browser, context, page = await _launch_browser(proxy_url)

                    await page.goto(
                        "https://www.instagram.com/accounts/emailsignup/",
                        wait_until="networkidle",
                    )
                    await asyncio.sleep(random.uniform(2.0, 4.0))

                    # Fill signup form
                    email_f = page.locator(
                        'input[name="emailOrPhone"], input[name="email"]'
                    )
                    if await email_f.count() > 0:
                        await email_f.fill(email)
                    await page.fill('input[name="fullName"]', full_name)
                    await page.fill('input[name="username"]', username)
                    await page.fill('input[name="password"]', password)

                    # Submit
                    await page.click('button[type="submit"]')
                    await page.wait_for_load_state("networkidle")
                    await asyncio.sleep(random.uniform(2.0, 4.0))

                    # Birthday
                    selects = page.locator("select")
                    if await selects.count() >= 3:
                        await selects.nth(0).select_option(str(month))
                        await selects.nth(1).select_option(str(day))
                        await selects.nth(2).select_option(str(year))
                        next_btn = page.locator(
                            'button:has-text("Next"), button:has-text("Selanjutnya")'
                        )
                        if await next_btn.count() > 0:
                            await next_btn.click()
                            await page.wait_for_load_state("networkidle")
                            await asyncio.sleep(random.uniform(2.0, 3.0))

                    # Phone verification
                    code_input = page.locator(
                        'input[name="confirmationCode"], input[name="sms_code"]'
                    )
                    if await code_input.count() > 0:
                        code = await pv.get_code(num.activation_id, timeout=120)
                        await code_input.fill(code)
                        confirm_btn = page.locator(
                            'button:has-text("Confirm"), button:has-text("Next")'
                        )
                        if await confirm_btn.count() > 0:
                            await confirm_btn.click()
                            await page.wait_for_load_state("networkidle")

                    # Captcha
                    async with CaptchaSolver() as cs:
                        await _detect_and_solve_captcha(page, cs, page.url)

                    account = await _save_account(
                        platform=Platform.INSTAGRAM,
                        username=username,
                        password=password,
                        email=email,
                        phone=num.phone,
                        proxy_ip=proxy_url or "direct",
                        status=AccountStatus.FRESH,
                    )
                    logger.info("instagram_created", account_id=account.id, username=username)
                    return account

                except Exception as exc:
                    logger.error("instagram_creation_failed", error=str(exc))
                    try:
                        await pv.cancel(num.activation_id)
                    except Exception:
                        pass
                    await _save_account(
                        platform=Platform.INSTAGRAM,
                        username=username,
                        password=password,
                        email=email,
                        phone=num.phone,
                        proxy_ip=proxy_url or "direct",
                        status=AccountStatus.FAILED,
                    )
                    raise AccountCreationError(
                        f"Instagram creation failed: {exc}",
                        context={"platform": "instagram", "username": username},
                    ) from exc
                finally:
                    if pw:
                        await _cleanup_browser(pw, browser, context, page)

    async def create_tiktok(self) -> Account:
        """Create a TikTok account.

        Flow: TikTok signup → birthday → phone/email → send code →
        enter code → sign up → captcha → store.
        """
        first, last = _random_name()
        username = _random_username(first.lower())
        password = _random_password()
        email = _random_email(username)

        pw = browser = context = page = None
        async with ProxyManager() as pm:
            proxy_url = await pm.get_proxy("http")
            async with PhoneVerifier() as pv:
                num = await pv.get_number(_PLATFORM_SERVICE["tiktok"])

                try:
                    pw, browser, context, page = await _launch_browser(proxy_url)

                    await page.goto(
                        "https://www.tiktok.com/signup", wait_until="networkidle"
                    )
                    await asyncio.sleep(random.uniform(2.0, 4.0))

                    # Switch to phone/email signup
                    phone_tab = page.locator('div:has-text("Use phone or email")')
                    if await phone_tab.count() > 0:
                        await phone_tab.click()
                        await asyncio.sleep(1.0)

                    # Birthday
                    _, month, day, year = None, *_random_birthday()
                    selects = page.locator("select")
                    if await selects.count() >= 3:
                        await selects.nth(0).select_option(str(month))
                        await selects.nth(1).select_option(str(day))
                        await selects.nth(2).select_option(str(year))

                    # Phone
                    phone_input = page.locator(
                        'input[placeholder*="Phone"], input[data-e2e="signup-phone-input"]'
                    )
                    if await phone_input.count() > 0:
                        await phone_input.fill(num.phone)
                    else:
                        email_input = page.locator(
                            'input[placeholder*="Email"], input[data-e2e="signup-email-input"]'
                        )
                        if await email_input.count() > 0:
                            await email_input.fill(email)

                    # Password
                    pwd = page.locator('input[type="password"]')
                    if await pwd.count() > 0:
                        await pwd.fill(password)

                    # Send code
                    send_btn = page.locator('button:has-text("Send code")')
                    if await send_btn.count() > 0:
                        await send_btn.click()
                        await asyncio.sleep(3.0)
                        code = await pv.get_code(num.activation_id, timeout=120)
                        code_input = page.locator(
                            'input[placeholder*="code"], input[aria-label*="code"]'
                        )
                        if await code_input.count() > 0:
                            await code_input.fill(code)

                    # Sign up
                    signup_btn = page.locator(
                        'button[data-e2e="signup-button"], button:has-text("Sign up")'
                    )
                    if await signup_btn.count() > 0:
                        await signup_btn.click()
                        await page.wait_for_load_state("networkidle")
                        await asyncio.sleep(random.uniform(2.0, 4.0))

                    # Captcha
                    async with CaptchaSolver() as cs:
                        await _detect_and_solve_captcha(page, cs, page.url)

                    account = await _save_account(
                        platform=Platform.TIKTOK,
                        username=username,
                        password=password,
                        email=email,
                        phone=num.phone,
                        proxy_ip=proxy_url or "direct",
                        status=AccountStatus.FRESH,
                    )
                    logger.info("tiktok_created", account_id=account.id, username=username)
                    return account

                except Exception as exc:
                    logger.error("tiktok_creation_failed", error=str(exc))
                    try:
                        await pv.cancel(num.activation_id)
                    except Exception:
                        pass
                    await _save_account(
                        platform=Platform.TIKTOK,
                        username=username,
                        password=password,
                        email=email,
                        phone=num.phone,
                        proxy_ip=proxy_url or "direct",
                        status=AccountStatus.FAILED,
                    )
                    raise AccountCreationError(
                        f"TikTok creation failed: {exc}",
                        context={"platform": "tiktok", "username": username},
                    ) from exc
                finally:
                    if pw:
                        await _cleanup_browser(pw, browser, context, page)

    async def create_twitter(self) -> Account:
        """Create a Twitter / X account.

        Flow: Twitter signup → name → email → verify → password →
        captcha → store.
        """
        first, last = _random_name()
        username = _random_username(first.lower())
        password = _random_password()
        email = _random_email(username)

        pw = browser = context = page = None
        async with ProxyManager() as pm:
            proxy_url = await pm.get_proxy("http")
            async with PhoneVerifier() as pv:
                num = await pv.get_number(_PLATFORM_SERVICE["twitter"])

                try:
                    pw, browser, context, page = await _launch_browser(proxy_url)

                    await page.goto(
                        "https://twitter.com/i/flow/signup", wait_until="networkidle"
                    )
                    await asyncio.sleep(random.uniform(2.0, 4.0))

                    # Name
                    name_input = page.locator(
                        'input[name="name"], input[data-testid="ocfEnterTextTextInput"]'
                    )
                    if await name_input.count() > 0:
                        await name_input.fill(f"{first} {last}")
                        next_btn = page.locator(
                            'button:has-text("Next"), [data-testid="ocfEnterTextNextButton"]'
                        )
                        if await next_btn.count() > 0:
                            await next_btn.click()
                            await asyncio.sleep(random.uniform(1.5, 3.0))

                    # Email
                    email_input = page.locator(
                        'input[name="phone_number"], input[data-testid="ocfEnterTextTextInput"]'
                    )
                    if await email_input.count() > 0:
                        await email_input.fill(email)
                        next_btn = page.locator('button:has-text("Next")')
                        if await next_btn.count() > 0:
                            await next_btn.click()
                            await asyncio.sleep(random.uniform(1.5, 3.0))

                    # Verification code
                    code_input = page.locator(
                        'input[name="verfication_code"], input[data-testid="ocfEnterTextTextInput"]'
                    )
                    if await code_input.count() > 0:
                        code = await pv.get_code(num.activation_id, timeout=120)
                        await code_input.fill(code)
                        next_btn = page.locator('button:has-text("Next")')
                        if await next_btn.count() > 0:
                            await next_btn.click()
                            await asyncio.sleep(random.uniform(1.5, 3.0))

                    # Password
                    pwd_input = page.locator(
                        'input[name="password"], input[type="password"]'
                    )
                    if await pwd_input.count() > 0:
                        await pwd_input.fill(password)
                        next_btn = page.locator('button:has-text("Next")')
                        if await next_btn.count() > 0:
                            await next_btn.click()
                            await page.wait_for_load_state("networkidle")

                    # Captcha
                    async with CaptchaSolver() as cs:
                        await _detect_and_solve_captcha(page, cs, page.url)

                    account = await _save_account(
                        platform=Platform.TWITTER,
                        username=username,
                        password=password,
                        email=email,
                        phone=num.phone,
                        proxy_ip=proxy_url or "direct",
                        status=AccountStatus.FRESH,
                    )
                    logger.info("twitter_created", account_id=account.id, username=username)
                    return account

                except Exception as exc:
                    logger.error("twitter_creation_failed", error=str(exc))
                    try:
                        await pv.cancel(num.activation_id)
                    except Exception:
                        pass
                    await _save_account(
                        platform=Platform.TWITTER,
                        username=username,
                        password=password,
                        email=email,
                        phone=num.phone,
                        proxy_ip=proxy_url or "direct",
                        status=AccountStatus.FAILED,
                    )
                    raise AccountCreationError(
                        f"Twitter creation failed: {exc}",
                        context={"platform": "twitter", "username": username},
                    ) from exc
                finally:
                    if pw:
                        await _cleanup_browser(pw, browser, context, page)

    async def create_shopee(self) -> Account:
        """Create a Shopee Indonesia account.

        Flow: Shopee signup → phone → OTP → set password → captcha → store.
        Shopee accounts are phone-first (Indonesia market).
        """
        first, last = _random_name()
        username = _random_username(first.lower())
        password = _random_password()

        pw = browser = context = page = None
        async with ProxyManager() as pm:
            proxy_url = await pm.get_proxy("http")
            async with PhoneVerifier() as pv:
                num = await pv.get_number(_PLATFORM_SERVICE["shopee"])

                try:
                    pw, browser, context, page = await _launch_browser(proxy_url)

                    await page.goto(
                        "https://shopee.co.id/buyer/signup", wait_until="networkidle"
                    )
                    await asyncio.sleep(random.uniform(2.0, 4.0))

                    # Phone number
                    phone_input = page.locator(
                        'input[name="phone"], input[placeholder*="nomor"], input[type="tel"]'
                    )
                    if await phone_input.count() > 0:
                        await phone_input.fill(num.phone)
                        await asyncio.sleep(0.5)

                    # Request OTP
                    otp_btn = page.locator(
                        'button:has-text("Kirim"), button:has-text("Send"), button:has-text("OTP")'
                    )
                    if await otp_btn.count() > 0:
                        await otp_btn.click()
                        await asyncio.sleep(3.0)

                    # OTP input — Shopee uses individual digit boxes
                    code = await pv.get_code(num.activation_id, timeout=120)
                    otp_input = page.locator(
                        'input[name="otp"], input[aria-label*="OTP"], input[placeholder*="OTP"]'
                    )
                    if await otp_input.count() > 0:
                        digits = list(code)
                        for i, digit in enumerate(digits):
                            box = otp_input.nth(i) if await otp_input.count() > 1 else otp_input
                            await box.fill(digit)
                            await asyncio.sleep(random.uniform(0.2, 0.5))

                    # Set password
                    pwd_input = page.locator('input[name="password"], input[type="password"]')
                    if await pwd_input.count() > 0:
                        await pwd_input.fill(password)

                    # Submit
                    submit_btn = page.locator(
                        'button[type="submit"], button:has-text("Daftar"), button:has-text("Sign Up")'
                    )
                    if await submit_btn.count() > 0:
                        await submit_btn.click()
                        await page.wait_for_load_state("networkidle")
                        await asyncio.sleep(random.uniform(2.0, 4.0))

                    # Captcha
                    async with CaptchaSolver() as cs:
                        await _detect_and_solve_captcha(page, cs, page.url)

                    account = await _save_account(
                        platform=Platform.SHOPEE,
                        username=username,
                        password=password,
                        email=None,
                        phone=num.phone,
                        proxy_ip=proxy_url or "direct",
                        status=AccountStatus.FRESH,
                    )
                    logger.info("shopee_created", account_id=account.id, username=username)
                    return account

                except Exception as exc:
                    logger.error("shopee_creation_failed", error=str(exc))
                    try:
                        await pv.cancel(num.activation_id)
                    except Exception:
                        pass
                    await _save_account(
                        platform=Platform.SHOPEE,
                        username=username,
                        password=password,
                        email=None,
                        phone=num.phone,
                        proxy_ip=proxy_url or "direct",
                        status=AccountStatus.FAILED,
                    )
                    raise AccountCreationError(
                        f"Shopee creation failed: {exc}",
                        context={"platform": "shopee", "username": username},
                    ) from exc
                finally:
                    if pw:
                        await _cleanup_browser(pw, browser, context, page)
