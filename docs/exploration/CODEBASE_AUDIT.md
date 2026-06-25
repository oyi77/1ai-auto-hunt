# Codebase Audit — 2026-06-25

> **Protocol:** SURPASS.md §3 — CODEBASE EXPLORATION
> **Scope:** Full codebase initial audit (first run)

---

## Stack

| Component | Technology | Version | Purpose |
|-----------|-----------|---------|---------|
| Language | Python | 3.11+ (`requires-python = ">=3.11"`) | Core runtime |
| Web Framework | FastAPI | ≥0.115 | REST API server |
| ORM | SQLAlchemy | ≥2.0 | Database abstraction + Alembic migrations |
| Browser Automation | Playwright | ≥1.45 | Headless browser for checkout/factory |
| Validation | Pydantic + pydantic-settings | ≥2.7 / ≥2.3 | Settings, models, request/response schemas |
| HTTP Client | httpx | ≥0.27 | Async HTTP for API integrations |
| Logging | structlog | ≥24.1 | Structured logging |
| CLI Framework | Click + Rich | ≥8.1 / ≥13.7 | `hunt` command interface with rich output |
| Task Queue | aioredis | ≥2.0 | Redis-backed async task queue |
| Build System | Hatchling | — | PEP 517 package build |
| Linting | Ruff + mypy | ≥0.5 / ≥1.10 | Code quality + strict type checking |
| Testing | pytest + pytest-asyncio + pytest-cov | ≥8.2 | Unit / integration / E2E |

---

## Directory Structure

```
1ai-auto-hunt/                          # 35 .py files, ~7,000 LOC
├── README.md                           # Project overview, quickstart, architecture
├── SURPASS.md                          # Competitive intelligence protocol
├── pyproject.toml                      # Package config, deps, tool config
├── src/                                # 35 Python modules
│   ├── __init__.py
│   ├── core/                           # Shared infrastructure (8 files)
│   │   ├── config.py                   # Pydantic BaseSettings singleton (202 LOC)
│   │   ├── db.py                       # SQLAlchemy async engine + session factory (159 LOC)
│   │   ├── logger.py                   # structlog setup with JSON/console renderers (99 LOC)
│   │   ├── proxy.py                    # 1proxy rotation, quality scoring, health checks (232 LOC)
│   │   ├── captcha.py                  # 2Captcha + CapSolver abstraction (211 LOC)
│   │   ├── phone.py                    # SMS-Activate integration (232 LOC)
│   │   ├── schema.py                   # Shared Pydantic base models (152 LOC)
│   │   └── exceptions.py              # Custom exception hierarchy (123 LOC)
│   ├── hunts/                          # The 7 revenue hunts
│   │   ├── __init__.py
│   │   ├── factory/                    # Account Factory (4 files)
│   │   │   ├── creator.py              # Multi-platform account creation (737 LOC)
│   │   │   ├── models.py              # SQLAlchemy models: Account, AgingSchedule, Sale (210 LOC)
│   │   │   └── pricing.py            # Account pricing logic (154 LOC)
│   │   ├── boost/                      # Boost Service (3 files)
│   │   │   ├── anti_detect.py         # Anti-detection for social platforms (316 LOC)
│   │   │   ├── models.py             # BoostOrder, DeliveryLog models (215 LOC)
│   │   │   └── pricing.py            # Boost pricing engine (290 LOC)
│   │   ├── checkout/                   # Flash Sale Bot (5 files)
│   │   │   ├── shopee.py              # Shopee checkout adapter (395 LOC)
│   │   │   ├── tokped.py             # Tokopedia checkout adapter (450 LOC)
│   │   │   ├── timer.py             # Sub-second timing engine (356 LOC)
│   │   │   ├── anti_bot.py          # Bot detection bypass (288 LOC)
│   │   │   └── models.py           # Checkout models (202 LOC)
│   │   ├── domain/                     # Domain Hunter (3 files)
│   │   │   ├── scanner.py            # TLD scanner + expired domain finder (480 LOC)
│   │   │   ├── vet.py               # DA/PA/Spam/backlink analysis (595 LOC)
│   │   │   └── models.py           # Domain models (204 LOC)
│   │   ├── stream/                     # Streaming Farm (scaffolded)
│   │   │   └── __init__.py
│   │   ├── kdp/                        # KDP Publisher (scaffolded)
│   │   │   └── __init__.py
│   │   └── media/                      # AI Media Factory (scaffolded)
│   │       └── __init__.py
│   └── api/                            # FastAPI REST API (4 files)
│       ├── app.py                      # App factory with CORS, routers, error handling (166 LOC)
│       ├── auth.py                     # JWT + API key authentication (250 LOC)
│       └── deps.py                    # Dependency injection (DB session, current user) (166 LOC)
├── tests/                              # Test directories (empty — needs bootstrapping)
│   ├── unit/
│   ├── integration/
│   └── e2e/
├── configs/                            # Configuration files (empty — YAML pending)
└── docs/                               # SURPASS protocol artifacts
    ├── research/
    │   ├── FEATURE_MATRIX.md           # ← master comparison table
    │   ├── GAP_ANALYSIS.md             # ← prioritized gap registry
    │   └── competitors/                # ← empty (competitor profiles pending)
    ├── decisions/                      # ← empty (research pending)
    ├── exploration/
    │   └── CODEBASE_AUDIT.md           # ← this file
    └── sprints/
        └── SPRINT_1.md                # ← sprint 1 plan
```

---

## Static Analysis

| Finding | Severity | Location | Notes |
|---------|----------|----------|-------|
| No test files | 🔴 Critical | `tests/` | Zero tests exist; coverage is 0% |
| No config YAML files | 🟡 Medium | `configs/` | Settings/env only; no YAML config files yet |
| 3 hunt modules scaffold-only | 🟡 Medium | `stream/`, `kdp/`, `media/` | Only `__init__.py` stubs |
| No CLI entry point | 🟡 Medium | `src/cli.py` | `pyproject.toml` declares `hunt = "src.cli:main"` but file doesn't exist |
| Largest file: `factory/creator.py` | 🟡 Medium | 737 LOC | Close to 800-line threshold; consider splitting aging logic |
| Large files: `domain/vet.py`, `domain/scanner.py` | 🟡 Medium | 595, 480 LOC | Domain analysis is complex; vet.py near threshold |

---

## Test Coverage

| Metric | Value | Target |
|--------|-------|--------|
| Line coverage | 0% | 80% (enforced in `pyproject.toml` via `fail_under = 80`) |
| Unit tests | 0 | 60% of suite |
| Integration tests | 0 | 30% of suite |
| E2E tests | 0 | 10% of suite |
| Critical paths tested | 0/7 | 7/7 |

**Status:** Code exists but no tests have been written. This is the highest-priority quality gap.

---

## Performance

| Area | Status | Notes |
|------|--------|-------|
| Checkout latency | 🟡 | Timer module (`356 LOC`) implements sub-second scheduling; needs benchmarking |
| Proxy rotation | 🟢 | `proxy.py` has health checks + quality scoring; async via httpx |
| Database queries | 🟡 | SQLAlchemy async engine configured; no query optimization or indexing yet |
| API response time | 🟢 | FastAPI async; no known bottlenecks at current scale |
| Browser automation | 🟡 | Playwright adapters for Shopee/Tokped exist; no latency benchmarks |

---

## Security

| Finding | Severity | Notes |
|---------|----------|-------|
| Auth implemented | 🟢 | JWT + API key auth in `src/api/auth.py` (250 LOC) |
| Secret management | 🟢 | Pydantic `SecretStr` for all keys in `config.py`; env-var resolution |
| Input validation | 🟢 | Pydantic models + `schema.py` base types |
| Rate limiting | 🟡 | Not yet implemented in API middleware |
| No CORS lockdown | 🟡 | CORS currently allows all origins (development default) |
| Dependency audit | 🔍 | No `pip-audit` or `safety` check configured |

---

## Architecture Score

| Dimension | Score | Notes |
|-----------|-------|-------|
| Scalability | 🟡 | FastAPI async + Redis queue + SQLAlchemy async; horizontal scaling possible but not battle-tested |
| Maintainability | 🟢 | Clean module boundaries, Pydantic settings, structured logging, type hints throughout |
| Extensibility | 🟢 | Modular hunt architecture — new hunts plug into `src/hunts/` with shared `src/core/` |
| Observability | 🟡 | structlog configured; no Prometheus metrics or distributed tracing |
| Security | 🟡 | Auth + secret management done; rate limiting and CORS lockdown pending |

**Overall:** 🟢 — Well-structured codebase with solid foundations. Main gaps are testing and observability.

---

## Quick Wins (High impact, low effort — do now)

1. **Bootstrap test suite** — Create `tests/conftest.py` with fixtures (test DB, mock proxy, mock captcha), write unit tests for `core/` modules. Unblocks the 80% coverage target.
2. **Create `src/cli.py`** — Wire up Click commands for all 7 hunts. The entry point is declared in `pyproject.toml` but the file doesn't exist.

---

## Scheduled Improvements (High impact, high effort — roadmap)

1. **Observability stack** — Prometheus metrics endpoint (`/metrics`), request duration histograms, per-hunt success/failure counters, distributed tracing for multi-step workflows.
2. **Complete scaffolded hunts** — `stream/`, `kdp/`, `media/` need full implementations (Sprint 3+).
