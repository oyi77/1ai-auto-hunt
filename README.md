# 1ai-auto-hunt

> **"I hunt money."**
> Automated commerce hunting platform — account factory, boost service, flash sale sniper, domain flipper, streaming farm, KDP publisher, deepfake media.

## What This Is

1ai-auto-hunt is the **money-making layer** on top of the 1ai-ecosystem. It fills 7 gaps that convert existing infrastructure into revenue streams.

```
hunt factory     ← create + age + sell accounts (Gmail, IG, TikTok, Shopee)
hunt boost       ← social media boost as a service (followers, likes, views)
hunt checkout    ← flash sale auto-checkout (Shopee, Tokped, Lazada)
hunt domain      ← expired domain scanner + flipper
hunt stream      ← Spotify/Apple Music streaming farm
hunt kdp         ← AI book → Amazon KDP publisher
hunt media       ← voice clone + AI influencer factory
```

## Quick Start

```bash
pip install -e ".[dev]"

# Account Factory
hunt factory create gmail --count 100
hunt factory list --platform instagram --status ready
hunt factory sell --order-id ORD-001

# Boost Service
hunt boost order --platform instagram --action followers --target https://ig.com/user --qty 1000
hunt boost status --order-id BOOST-001

# Flash Sale
hunt checkout monitor --url https://shopee.co.id/product/123 --budget 500000
hunt checkout snipe --url https://shopee.co.id/product/123 --threshold 100000

# Domain Hunter
hunt domain scan --tld .com --min-da 20 --max-price 15
hunt domain snipe --domain example.com

# Streaming Farm
hunt stream farm --accounts 100 --playlist my_playlist.json
hunt stream revenue --month 2026-06

# KDP Publisher
hunt kdp generate --topic "AI for beginners" --count 10
hunt kdp publish --book-dir ./books/ai-beginners/

# Deepfake/AI Media
hunt media voice-clone --input source.wav --text "Hello world" --output clone.mp3
hunt media ai-influencer --name "Luna" --posts 30
```

## Architecture

```
1ai-auto-hunt
├── src/
│   ├── core/          ← Shared infrastructure
│   │   ├── config.py  ← Configuration management
│   │   ├── proxy.py   ← 1proxy integration
│   │   ├── captcha.py ← 2captcha integration
│   │   ├── phone.py   ← SMS-Activate integration
│   │   ├── db.py      ← SQLite database
│   │   └── logger.py  ← Structured logging
│   ├── hunts/         ← The 7 hunts
│   │   ├── factory/   ← Account Factory
│   │   ├── boost/     ← Boost Service
│   │   ├── checkout/  ← Flash Sale Bot
│   │   ├── domain/    ← Domain Hunter
│   │   ├── stream/    ← Streaming Farm
│   │   ├── kdp/       ← KDP Book Factory
│   │   └── media/     ← Deepfake/AI Media
│   └── api/           ← REST API (FastAPI)
├── tests/
│   ├── unit/          ← Unit tests (60%)
│   ├── integration/   ← Integration tests (30%)
│   └── e2e/           ← End-to-end tests (10%)
├── docs/              ← SURPASS protocol docs
├── configs/           ← Configuration files
└── pyproject.toml
```

## Ecosystem Integration

| Existing Project | Role in 1ai-auto-hunt |
|-----------------|----------------------|
| `1ai-phonefarm` | Device execution backbone (1000+ devices, ADB, templates) |
| `1ai-social` | Boost execution engine (26 platform adapters, blast system) |
| `omni-account-onboarding` | Email verification + OTP extraction (Stalwart Mail) |
| `1proxy` | Proxy rotation (30+ sources, quality scoring) |
| `waha-core` | WhatsApp HTTP API (bulk messaging, customer notifications) |
| `1ai-content` | AI content generation (video, text, images) |
| `1ai-affiliate` | Click/conversion tracking, smartlinks |

## Revenue Target

| Hunt | Monthly Revenue |
|------|-----------------|
| Account Factory | $2K-6K |
| Boost Service | $10K-30K |
| Flash Sale | $2K-10K |
| Domain Hunter | $500-5K |
| Streaming Farm | $5.4K-9K |
| KDP Publisher | $500-5K |
| Deepfake/AI Media | $5-20K |
| **Total** | **$25K-85K/mo** |

## License

Private — BerkahKarya internal use only.
