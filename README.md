# Institutional Swing Trading Alert System — v5

A two-stage email alert bot implementing **Trading_Bot_Final_Prompt_v5.docx**
verbatim, scanning **23 crypto perpetuals** on Daily and 4H timeframes for
high-probability swing setups.

- **Stage 1 — Zone Alert (STANDBY)** fires when HTF confluence is detected.
- **Stage 2 — Execution Alert (ENTRY READY)** fires when price retraces into
  the defined zone and an LTF trigger (1H or 15M) confirms.

Only setups with **confidence ≥ 7/10** are emailed. Setups with **≥ 8/10**
carry a **[PRIORITY]** tag.

---

## What it detects

Each cycle (every 60 seconds on Oracle), the bot evaluates four conditions:

| Code | Condition | Type |
|---|---|---|
| **A** | HTF Key Level Reaction (close within 0.5% of a level with ≥2 prior touches) | Primary |
| **B** | HTF Pattern Detection — 11 classifiers (see below) | Secondary |
| **C** | HTF Market Structure Shift (displacement body > 5-bar avg, 1+ ATR through level, no immediate rejection) | Primary |
| **D** | Liquidity Event — 5 classes (sweep+reclaim, sweep+accept, untapped, internal, external) | Secondary |

A delivered alert requires **(A or C) AND (B or D)** in the same direction.

### Condition B — pattern classifiers

Continuation: **Bull Flag · Bear Flag · Pennant · Rectangle · Ascending Triangle · Descending Triangle · Broadening Formation**

Reversal: **Double Top · Double Bottom · Head and Shoulders · Inverse Head and Shoulders · Rounding Top · Rounding Bottom**

Fallback: **Pattern Breakout — Unclassified** (confidence capped at 6/10)

### Filters that suppress alerts

- **Volatility regime** — Daily ATR > 2.5× 20-day avg → all alerts paused
- **Regime "Transitioning"** — auto-deducts 2 confidence points
- **BTC alignment** — Tier 1/2 setups require BTC bias to match; Tier 3 bypasses
- **48-hour cooldown** per asset (unless full invalidation + structural reset)

---

## Universe (tiered per v5)

- **Tier 1** (BTC-correlated majors): BTC, ETH, SOL, BNB, XRP
- **Tier 2** (semi-correlated mid-caps): ADA, AVAX, LINK, DOT, POL¹, ATOM, NEAR, LTC
- **Tier 3** (narrative / relative-strength): ONDO, INJ, SUI, SEI, TIA, AAVE, UNI, ARB, TRX, OP

¹ MATIC was rebranded to POL in 2024.

---

## Architecture

```
bot/
├── config.py                 universe, tiers, thresholds, scoring weights
├── exchange.py               ccxt factory w/ Binance→OKX→KuCoin→Bitget fallback
├── indicators.py             ADX, ATR, swing pivots, body/displacement helpers
├── regime.py                 Trending / Ranging / Transitioning classifier + vol filter
├── btc_corr.py               BTC bias + Tier 1/2/3 alignment gating
├── cooldown.py               48h per-asset registry
├── conditions/
│   ├── zones.py              Zone primitive (FVG/OB/imbalance/boundary/neckline)
│   ├── a_key_level.py        Condition A
│   ├── c_mss.py              Condition C
│   ├── d_liquidity.py        Condition D (5 classes)
│   └── b_patterns.py         Condition B — 11 classifiers
├── scoring.py                1–10 confidence engine
├── state_machine.py          two-stage setup lifecycle (persisted JSON)
├── ltf_validation.py         1H/15M micro-MSS / displacement / rejection
├── targets.py                TP1 / TP2 / R:R calculation
├── formatter.py              16-field alert output
├── email_notify.py           Gmail SMTP
└── main.py                   scanner loop entrypoint
```

---

## Setup — local testing

```powershell
cd "C:\Users\ThinkPad\Desktop\Crypto Signals"
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
# Fill in SMTP_USER, SMTP_PASSWORD, EMAIL_FROM, EMAIL_TO
python -m bot.main --once
```

A single scan takes 30–90 seconds depending on exchange latency.

---

## Deployment — Oracle Cloud Free (24/7)

### 1. Provision the VM

1. Sign up at <https://signup.cloud.oracle.com> (credit card for verification, never charged on Always Free).
2. Create an **Always Free Ampere A1 VM** — 4 OCPU, 24 GB RAM (uses way more than we need; gives headroom).
3. Choose **Canonical Ubuntu 22.04** as the image.
4. Open **only outbound** in the security list (we don't need inbound for this bot).
5. Download the SSH key pair Oracle provides — save the private key as `~/.ssh/oracle_v5.pem`.

### 2. Install Docker on the VM

```bash
ssh -i ~/.ssh/oracle_v5.pem ubuntu@<VM_PUBLIC_IP>

sudo apt update && sudo apt install -y docker.io
sudo usermod -aG docker $USER
exit
# log back in for group change
ssh -i ~/.ssh/oracle_v5.pem ubuntu@<VM_PUBLIC_IP>
```

### 3. Deploy the bot

```bash
git clone https://github.com/Talha-Awan30/crypto_signals.git
cd crypto_signals
cp .env.example .env
nano .env       # paste your Gmail app password + email addresses

docker build -t crypto-v5 .
docker run -d \
  --name crypto-v5 \
  --restart unless-stopped \
  --env-file .env \
  -v $(pwd)/state:/app/state \
  crypto-v5
```

### 4. Confirm it's running

```bash
docker logs -f crypto-v5
```

You should see `=== v5 scan start ===` followed by exchange selection, BTC
context, and per-asset evaluations. Press `Ctrl+C` to detach (doesn't stop the
container).

### 5. Updating

```bash
cd crypto_signals
git pull
docker build -t crypto-v5 .
docker stop crypto-v5 && docker rm crypto-v5
docker run -d --name crypto-v5 --restart unless-stopped \
  --env-file .env -v $(pwd)/state:/app/state crypto-v5
```

---

## Alert volume — realistic expectations

- v5 spec demands score ≥ 8 (institutional). We relaxed to **≥ 7** to deliver
  your target of **0–5 alerts/day**.
- Most days you will see **1–3 Stage 1 alerts**, with **0–1 Stage 2 promotions**.
- A Stage 1 with no Stage 2 = price never retraced into the zone within
  7 candles. This is logged as expired — no alarm.

To make it louder, lower `MIN_SCORE_DELIVER` in `bot/config.py`.
To make it quieter, raise it back to 8 (strict v5).

---

## Output format

Every Stage 2 alert contains:

- Coin / Tier, Direction, Market Regime, Timeframes
- All conditions that fired (A/B/C/D)
- Pattern name + category (if Condition B fired)
- Liquidity context with classification
- Key level, retracement zone, LTF confirmation trigger
- Entry zone, TP1, TP2, SL, R:R to TP2
- BTC correlation context
- Confidence score with PRIORITY tag if ≥ 8

---

## Disclaimer

This is an educational alert system. It does NOT execute trades. Final entry
confirmation and execution are the trader's responsibility.
