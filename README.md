# Crypto SMC Signal Bot

A 24/7 Smart-Money-Concepts scanner for crypto perpetuals. Detects high-probability setups
(BOS / MSS + OB / FVG / liquidity pools / volume expansion) on 1H and 4H, checks funding
rates and news sentiment, and pushes alerts to **email** or **Telegram**.

Built to run **free** on GitHub Actions today, and migrate to **Oracle Cloud Free** (or any
VPS/Docker host) later with zero code changes — just a different run mode.

## Quick start (local)

1. `python -m venv .venv && .venv\Scripts\activate` (Windows) or `source .venv/bin/activate`
2. `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in your email or Telegram credentials.
4. Run once: `python -m src.main --once`
5. Run forever: `python -m src.main --loop`

### Gmail app password

If using Gmail SMTP, enable 2-Step Verification and create an **App Password** at
<https://myaccount.google.com/apppasswords>. Put it in `SMTP_PASSWORD`.

## Free 24/7 deployment — GitHub Actions

1. Push this folder to a **private** GitHub repo.
2. Go to **Settings → Secrets and variables → Actions** and add:
   - `NOTIFIER` = `email` (or `telegram`)
   - `SMTP_USER`, `SMTP_PASSWORD`, `EMAIL_FROM`, `EMAIL_TO` (Gmail app password)
   - *or* `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
3. The workflow [`.github/workflows/scan.yml`](.github/workflows/scan.yml) runs every 15
   minutes and commits dedupe state back to the repo.
4. Trigger once manually via **Actions → crypto-scan → Run workflow** to verify.

**Note on cadence:** GitHub's scheduled workflows run at best every 5 minutes but can lag
under heavy load; 15 minutes is more reliable. For faster reaction, migrate to Oracle.

## Migrate to Oracle Cloud Free (always-on)

No code changes. Just a different run mode.

1. Create a free **Ampere A1** VM (Oracle Cloud Always Free tier).
2. Install Docker.
3. `docker build -t crypto-signals .`
4. `docker run -d --name crypto-signals --env-file .env --restart unless-stopped crypto-signals`

The container runs `python -m src.main --loop` — a long-running scheduler inside the
process. Funding, news, and scan cadence are all controlled by env vars.

## How signals work

A signal is only emitted when **all** of these align:

1. **Structure event** on 1H or 4H — a recent **BOS** (Break of Structure) or
   **MSS** (Market Structure Shift).
2. **At least one confluence** in the same direction:
   - Unfilled **FVG** (Fair Value Gap)
   - **Order Block** (last opposing candle before an impulsive move)
   - **Equal highs/lows** — liquidity pool / resting stops
   - **Volume expansion** on the breaking candle (>1.8× 20-bar avg)
3. **Funding-rate check** — extreme funding against the direction is flagged as caution.
4. **News context** — top CryptoPanic headlines for the asset attached for narrative.

Each alert contains:

- Symbol / timeframe / direction
- Entry zone (from the nearest OB or FVG)
- **Invalidation level** (where the setup is wrong)
- Bulleted reasoning tying every condition to the decision
- Latest news headlines with vote sentiment

**Silence is a feature.** If nothing qualifies, the bot stays quiet — no overtrading.

## File layout

```
src/
  config.py          central env-driven config
  data.py            ccxt: OHLCV, funding, universe
  smc.py             BOS, MSS, FVG, OB, EQH/EQL, volume expansion
  signals.py         composes primitives into Signal objects
  news.py            CryptoPanic public feed
  state.py           JSON-backed dedupe (prevents spam)
  formatter.py       signal -> human-readable text
  notifiers/
    base.py          Notifier ABC
    email_smtp.py    Gmail SMTP
    telegram.py      Telegram Bot API
  main.py            --once (Actions) or --loop (Oracle)
.github/workflows/scan.yml    free 24/7 cron on GitHub Actions
Dockerfile                    container for Oracle / any VPS
```

## Tuning knobs

Edit [src/smc.py](src/smc.py) to tighten or loosen detection:

- `swing_points(left, right)` — bigger values = fewer, stronger swings
- `detect_volume_expansion(mult=1.8)` — raise for louder breaks only
- `detect_order_blocks(impulse_mult=1.5)` — higher = stricter OBs
- `detect_equal_levels(tolerance_pct=0.0015)` — how close highs must be to "equal"

Edit [src/state.py](src/state.py) `_DEFAULT_SUPPRESS_HOURS` to control dedupe window.

## Disclaimer

This is an educational signal scanner. It does not place trades and does not constitute
financial advice. Always validate setups yourself before executing.
