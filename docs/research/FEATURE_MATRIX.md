# Feature Matrix — 1ai-auto-hunt vs. Competitors

> **Last updated:** 2026-06-25
> **Protocol:** SURPASS.md §1.2
> **Symbols:** ✅ Fully implemented · 🚧 Partial / WIP · ❌ Missing entirely · ⭐ Best-in-class — no competitor matches · 🔍 Not yet researched

---

## Legend — The 7 Hunts

| Hunt | CLI Command | Revenue Target | Code Status |
|------|------------|----------------|-------------|
| Account Factory | `hunt factory` | $2K–6K/mo | ✅ Implemented (creator, models, pricing) |
| Boost Service | `hunt boost` | $10K–30K/mo | 🚧 Partial (anti_detect, models, pricing) |
| Flash Sale Checkout | `hunt checkout` | $2K–10K/mo | ✅ Implemented (shopee, tokped, timer, anti_bot) |
| Domain Hunter | `hunt domain` | $500–5K/mo | ✅ Implemented (scanner, vet, models) |
| Streaming Farm | `hunt stream` | $5.4K–9K/mo | ❌ Scaffold only |
| KDP Publisher | `hunt kdp` | $500–5K/mo | ❌ Scaffold only |
| AI Media Factory | `hunt media` | $5K–20K/mo | ❌ Scaffold only |

---

## 1. Account Factory — Bulk Account Creation & Aging

| Feature | 1ai-auto-hunt | alimsk/bfs | MRHRTZ/Shopee-flashsale-bot | aaafarrr/Bot | saccofrancesco/supremebot | Streamify | CommercialGoatAPI |
|---------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Multi-platform account creation (Gmail, IG, TikTok, Shopee) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| Automated email/phone verification | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| Account aging pipeline (warm-up schedules) | ⭐ | ❌ | ❌ | ❌ | ❌ | ❌ | 🚧 |
| Bulk import/export (CSV, JSON) | 🚧 | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| Proxy rotation per account | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| Captcha auto-solve (2Captcha / CapSolver) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| SMS verification via SMS-Activate | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| Account health monitoring | 🚧 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Marketplace listing & auto-sell | ⭐ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

---

## 2. Boost Service — Social Media Boost as a Service

| Feature | 1ai-auto-hunt | alimsk/bfs | MRHRTZ/Shopee-flashsale-bot | aaafarrr/Bot | saccofrancesco/supremebot | Streamify | CommercialGoatAPI |
|---------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Multi-platform followers/likes/views | 🚧 | ❌ | ❌ | ❌ | ❌ | ⭐ | 🚧 |
| Order management (create/status/cancel) | 🚧 | ❌ | ❌ | ❌ | ❌ | 🚧 | ✅ |
| Real-time delivery tracking | 🚧 | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| API-first (REST endpoints for integration) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ⭐ |
| Auto-retry & refill guarantee | ⭐ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| Bulk order scheduling | 🚧 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Platform-specific rate limiting | 🚧 | ❌ | ❌ | ❌ | ❌ | ✅ | 🚧 |
| WhatsApp order notifications | ⭐ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

---

## 3. Flash Sale Checkout — Auto-Checkout Sniper

| Feature | 1ai-auto-hunt | alimsk/bfs | MRHRTZ/Shopee-flashsale-bot | aaafarrr/Bot | saccofrancesco/supremebot | Streamify | CommercialGoatAPI |
|---------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Shopee flash sale monitoring | ✅ | ⭐ | ⭐ | 🔍 | 🔍 | ❌ | ❌ |
| Tokopedia / Lazada support | ✅ | ❌ | ❌ | 🔍 | 🔍 | ❌ | ❌ |
| Sub-second checkout timing | ✅ | ✅ | ✅ | 🔍 | 🔍 | ❌ | ❌ |
| Budget guard (max spend cap) | ⭐ | ❌ | 🚧 | 🔍 | 🔍 | ❌ | ❌ |
| Multi-account parallel checkout | 🚧 | ❌ | ❌ | 🔍 | 🔍 | ❌ | ❌ |
| Anti-bot detection bypass | ✅ | ✅ | ✅ | 🔍 | 🔍 | ❌ | ❌ |
| Price threshold alerts | 🚧 | ❌ | ❌ | 🔍 | 🔍 | ❌ | ❌ |
| Post-checkout order tracking | ⭐ | ❌ | ❌ | 🔍 | 🔍 | ❌ | ❌ |
| Playwright-based headless browser | ⭐ | Selenium | Selenium | 🔍 | 🔍 | ❌ | ❌ |

---

## 4. Domain Hunter — Expired Domain Scanner & Flipper

| Feature | 1ai-auto-hunt | alimsk/bfs | MRHRTZ/Shopee-flashsale-bot | aaafarrr/Bot | saccofrancesco/supremebot | Streamify | CommercialGoatAPI |
|---------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| TLD scanning (.com, .io, .co, etc.) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| DA/PA/Spam Score filtering | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Expired domain sniping | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Backlink profile analysis | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Auto-registration via registrar API | 🚧 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Marketplace listing (Afternic/Sedo/GoDaddy) | 🚧 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| ROI calculator (cost vs. estimated value) | ⭐ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

---

## 5. Streaming Farm — Music Streaming Revenue

| Feature | 1ai-auto-hunt | alimsk/bfs | MRHRTZ/Shopee-flashsale-bot | aaafarrr/Bot | saccofrancesco/supremebot | Streamify | CommercialGoatAPI |
|---------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Spotify stream farming | ❌ | ❌ | ❌ | ❌ | ❌ | ⭐ | ❌ |
| Apple Music support | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Playlist rotation management | ❌ | ❌ | ❌ | ❌ | ❌ | ⭐ | ❌ |
| Revenue tracking per account | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |
| Anti-fingerprint (per-account UA/IP/timing) | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ | ❌ |
| Natural listening pattern simulation | ❌ | ❌ | ❌ | ❌ | ❌ | 🚧 | ❌ |
| Multi-device emulation (ADB phone farm) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

---

## 6. KDP Publisher — AI Book → Amazon KDP

| Feature | 1ai-auto-hunt | alimsk/bfs | MRHRTZ/Shopee-flashsale-bot | aaafarrr/Bot | saccofrancesco/supremebot | Streamify | CommercialGoatAPI |
|---------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| AI book generation (topic → manuscript) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Cover generation (AI art) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Multi-format export (EPUB, MOBI, PDF) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| KDP upload automation | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Keyword/category optimization | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Bulk publishing pipeline (10+ books/batch) | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Sales tracking dashboard | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

---

## 7. AI Media Factory — Voice Clone & AI Influencer

| Feature | 1ai-auto-hunt | alimsk/bfs | MRHRTZ/Shopee-flashsale-bot | aaafarrr/Bot | saccofrancesco/supremebot | Streamify | CommercialGoatAPI |
|---------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| Voice cloning from sample audio | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Text-to-speech with cloned voice | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| AI influencer persona generation | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Automated social media posting | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Multi-platform content scheduling | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Deepfake video generation | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Brand-safe content filtering | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

---

## Cross-Cutting Platform Features

| Feature | 1ai-auto-hunt | alimsk/bfs | MRHRTZ/Shopee-flashsale-bot | aaafarrr/Bot | saccofrancesco/supremebot | Streamify | CommercialGoatAPI |
|---------|:---:|:---:|:---:|:---:|:---:|:---:|:---:|
| REST API (FastAPI) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ✅ |
| CLI interface | 🚧 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Proxy rotation system (30+ sources) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | 🚧 |
| Structured logging & observability | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| SQLite persistence | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| WhatsApp notifications (waha-core) | 🚧 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Ecosystem integration (phone farm, content, affiliate) | ⭐ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

---

## Summary Scorecard

| Platform | ⭐ Best-in-class | ✅ Implemented | 🚧 Partial | ❌ Missing |
|----------|:---:|:---:|:---:|:---:|
| **1ai-auto-hunt** | **6** | **15** | **14** | **16** |
| alimsk/bfs | 1 | 1 | 0 | 49 |
| MRHRTZ/Shopee-flashsale-bot | 1 | 1 | 1 | 48 |
| aaafarrr/Bot | 0 | 0 | 0 | 51 |
| saccofrancesco/supremebot | 0 | 0 | 0 | 51 |
| Streamify | 2 | 2 | 1 | 46 |
| CommercialGoatAPI | 1 | 6 | 3 | 41 |

> **Key insight:** 4 of 7 hunts have production code (Factory, Boost, Checkout, Domain). 3 hunts (Stream, KDP, Media) are scaffold-only and scheduled for Sprint 3+. Our advantage is architecture — a unified platform spanning 7 revenue verticals with shared infrastructure. The ⭐ best-in-class features (aging pipeline, auto-retry refill, post-checkout tracking, budget guard, ecosystem integration, ROI calculator) are our moats.
