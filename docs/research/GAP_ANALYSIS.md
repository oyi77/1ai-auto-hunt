# Gap Analysis — 1ai-auto-hunt

> **Last updated:** 2026-06-25
> **Protocol:** SURPASS.md §1.3
> **Source:** FEATURE_MATRIX.md competitor comparison

---

## Priority Classification

- **P0** — Competitor has it, we don't. Blocker to being competitive. Fix first.
- **P1** — We have it but competitor does it better. Fix to surpass.
- **P2** — Nobody has it. First-mover opportunity. Reserve 20% capacity.

---

## Gap Registry

### GAP-001: Flash Sale Selenium-to-Playwright Migration

| Field | Value |
|-------|-------|
| **Priority** | P0 |
| **Status** | 🚧 In Progress |
| **Hunt** | Flash Sale Checkout |
| **Feature** | Sub-second checkout via headless browser |
| **Competitors** | alimsk/bfs, MRHRTZ/Shopee-flashsale-bot (both Selenium-based) |
| **Description** | Competitors use Selenium for Shopee flash sale automation. We chose Playwright for better async performance, but the checkout flow needs to reach sub-second latency parity with Selenium-based bots. Current Playwright implementation in `src/hunts/checkout/` is scaffolded but not optimized for timing-critical operations. |
| **Competitive Position** | Both alimsk/bfs and MRHRTZ/Shopee-flashsale-bot achieve reliable sub-second checkout on Shopee. Our Playwright approach has inherent async advantages (CDP protocol vs WebDriver), but we haven't exploited them yet. |
| **Surpass Target** | ⭐ — Playwright CDP + pre-loaded session cookies + request interception to bypass page load entirely |
| **Implementation Status** | Directory scaffolded (`src/hunts/checkout/`), no production code yet |

---

### GAP-002: Account Aging Pipeline

| Field | Value |
|-------|-------|
| **Priority** | P0 |
| **Status** | ❌ Not Started |
| **Hunt** | Account Factory |
| **Feature** | Automated account warm-up and aging schedules |
| **Competitors** | CommercialGoatAPI (partial aging via API) |
| **Description** | Raw accounts have near-zero value. The aging pipeline must simulate organic activity over 7–30 days: profile completion, content engagement, friend/follow accumulation, gradual activity ramp. CommercialGoatAPI does basic aging via API calls, but lacks device-level behavioral simulation. Our phone farm integration (`1ai-phonefarm`) enables physical device aging — a massive differentiator. |
| **Competitive Position** | CommercialGoatAPI offers API-level aging (simulated clicks). Physical device aging via ADB phone farm is unexploited territory. |
| **Surpass Target** | ⭐ — Physical device aging via ADB with realistic touch/swipe patterns, app usage sessions, and randomized daily schedules |
| **Implementation Status** | Not started. Depends on `1ai-phonefarm` integration. |

---

### GAP-003: Boost Service Order Management

| Field | Value |
|-------|-------|
| **Priority** | P0 |
| **Status** | 🚧 In Progress |
| **Hunt** | Boost Service |
| **Feature** | Full order lifecycle: create → track → deliver → refill |
| **Competitors** | CommercialGoatAPI (⭐ best-in-class order API), Streamify (partial) |
| **Description** | CommercialGoatAPI has a mature order management API with status tracking, auto-refill, and drip delivery. Our boost service needs to match this with: order creation, real-time status polling, automatic refill on drop, and delivery confirmation. Integration with `1ai-social` (26 platform adapters) gives us execution breadth no competitor matches. |
| **Competitive Position** | CommercialGoatAPI's API is polished but platform-locked (single pane). Streamify focuses only on streaming. We can surpass with cross-platform boost + WhatsApp notifications + CLI management. |
| **Surpass Target** | ⭐ — Unified order API across all 26 platforms in `1ai-social`, with WhatsApp status notifications via `waha-core` |
| **Implementation Status** | Directory scaffolded (`src/hunts/boost/`), REST endpoints being designed |

---

### GAP-004: Domain Backlink Profile Analysis

| Field | Value |
|-------|-------|
| **Priority** | P1 |
| **Status** | 🚧 Partial |
| **Hunt** | Domain Hunter |
| **Feature** | Deep backlink analysis before domain purchase |
| **Competitors** | No direct competitor in our matrix covers domain hunting |
| **Description** | Basic DA/PA/Spam Score filtering is implemented, but deep backlink profile analysis (anchor text distribution, referring domain quality, link velocity trends, toxic link detection) is only partially done. Without this, we risk purchasing domains with hidden penalties that destroy resale value. |
| **Competitive Position** | No matrix competitor operates in the domain space. This is a P1 because we need it internally for quality, not because competitors beat us on it. |
| **Surpass Target** | 🟢 Better — Integrate Ahrefs/Moz API for automated backlink scoring, with a composite "flip score" combining DA, backlink quality, and estimated traffic |
| **Implementation Status** | DA/PA filtering works. Backlink API integration pending. |

---

### GAP-005: Streaming Farm Anti-Fingerprint Evasion

| Field | Value |
|-------|-------|
| **Priority** | P1 |
| **Status** | 🚧 Partial |
| **Hunt** | Streaming Farm |
| **Feature** | Advanced fingerprint evasion for streaming platforms |
| **Competitors** | Streamify (⭐ established player in streaming) |
| **Description** | Streamify has years of anti-detection refinement for Spotify/Apple Music. Their fingerprint evasion includes canvas fingerprint randomization, WebGL noise injection, audio context fingerprint spoofing, and behavioral timing jitter. Our current implementation handles UA/IP rotation but lacks deeper fingerprint layers. Our phone farm advantage (real devices) partially compensates but browser-based streams still need fingerprint hardening. |
| **Competitive Position** | Streamify is the market leader for streaming specifically. Our edge is real device emulation via ADB, but browser-based scaling needs fingerprint parity. |
| **Surpass Target** | ⭐ — Real device streaming via ADB phone farm (physical devices have native fingerprints) + Playwright stealth patches for browser scale |
| **Implementation Status** | Basic UA/IP rotation works. Canvas/WebGL spoofing not implemented. ADB integration planned. |

---

### GAP-006: KDP Sales Tracking Dashboard

| Field | Value |
|-------|-------|
| **Priority** | P1 |
| **Status** | 🚧 Partial |
| **Hunt** | KDP Publisher |
| **Feature** | Real-time KDP sales and royalty tracking |
| **Competitors** | No matrix competitor covers KDP publishing |
| **Description** | Book generation and KDP upload automation are implemented, but sales tracking is a stub. Need: KDP dashboard scraping or Amazon API integration for real-time sales data, royalty calculations, BSR (Best Seller Rank) tracking, and per-title P&L. Without this, we can't optimize which topics/categories generate the most revenue. |
| **Competitive Position** | No competitor in our matrix operates in KDP. This is P1 because it's critical for optimizing the publishing pipeline, not because we're behind a competitor. |
| **Surpass Target** | 🟢 Better — Automated daily KDP report pull + per-title ROI calculation + topic recommendation engine based on historical sales data |
| **Implementation Status** | `hunt kdp generate` and `hunt kdp publish` work. Sales tracking is a placeholder. |

---

### GAP-007: AI Influencer Content Scheduling

| Field | Value |
|-------|-------|
| **Priority** | P2 |
| **Status** | 🚧 Partial |
| **Hunt** | AI Media Factory |
| **Feature** | Cross-platform content scheduling for AI influencers |
| **Competitors** | No matrix competitor covers AI influencer management |
| **Description** | AI influencer persona generation and automated posting work, but lack a unified content calendar with optimal posting times, A/B testing of content variants, engagement analytics, and growth rate tracking. This is a first-mover opportunity — no competitor in the space has an integrated AI influencer + scheduling + analytics pipeline. |
| **Competitive Position** | First-mover. No competitor combines AI influencer generation with scheduling and analytics. Social media schedulers (Buffer, Hootsuite) don't handle AI-generated personas. |
| **Surpass Target** | ⭐ — Unified content calendar with AI-optimized posting times, engagement prediction model, and automated A/B testing of post variants |
| **Implementation Status** | Basic posting works. Calendar, scheduling, and analytics not started. |

---

## Gap Summary

| Priority | Count | Gaps | Sprint Target |
|----------|-------|------|---------------|
| P0 | 3 | GAP-001, GAP-002, GAP-003 | Sprint 1–2 |
| P1 | 3 | GAP-004, GAP-005, GAP-006 | Sprint 3–4 |
| P2 | 1 | GAP-007 | Sprint 5+ (20% capacity) |

---

## Resolution Protocol

Per SURPASS.md §4.2, before closing any gap:

```
[ ] Gap is documented here with GAP-XXX ID
[ ] Research written in docs/decisions/[feature].md
[ ] At least 3 implementation options evaluated
[ ] Selected approach documented with rationale
[ ] Tests written covering new behavior
[ ] FEATURE_MATRIX.md updated (❌→✅ or ✅→⭐)
[ ] Passes "would a competitor's user switch for this?" test
```

> ⚠️ **No P1 work while any P0 is open. No P2 work while any P1 is open.**
