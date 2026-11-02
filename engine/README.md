# Trading engine

The bot that does the trading. Test (fake money) and Live (real money) run
the same code; only the Alpaca account differs.

## What it does each cycle (every 5 minutes while the market is open)

1. **Watches what it owns.** Moves each trailing stop up as the price rises,
   and sells when the stop is hit, the trend breaks (close below the 50-day
   average), or a penny stock's trading volume dries up.
2. **Looks for new buys.** Scores every stock in its list for a strong,
   steady uptrend, rejects anything that fails a safety check, and buys the
   best ones that fit the risk rules.

## Safety rules

Hard limits live in `autotrader/config.py` (`HardLimits`). Settings can be
tuned, but are always clamped inside them.

| Rule | Default | Hard limit |
| --- | --- | --- |
| Max per stock | 5% | 10% |
| Max open positions | 15 | 25 |
| Stop loss | 2x daily move (ATR), trailing | never more than 8% below entry |
| Daily loss cap | stop buying at -2% | -3% |
| Drawdown | halve sizes at -10%, pause at -15% | pause at -15% |
| Cash reserve | 10% | at least 5% |
| One sector | 25% | 25% |
| Penny stocks (under $5) | 2% each, 10% total, $1M+ traded daily | same |

Penny stocks also need a higher score, are skipped after a 50%+ jump in 5
days (possible pump-and-dump), use limit orders only, and get tighter stops.
OTC/pink-sheet stocks, options and margin are never used.

In Live, **approve mode** is on by default: buys wait in the journal for your
approval instead of being placed. Protective sells never wait.

## Setup

1. Make a free account at [alpaca.markets](https://alpaca.markets) and create
   API keys for the paper account (and later the live one).
2. Store the keys as environment variables (on Replit, use **Secrets**). Never
   commit them or paste them in chat.

   | Variable | Used for |
   | --- | --- |
   | `ALPACA_TEST_KEY_ID`, `ALPACA_TEST_SECRET_KEY` | Test (paper) account |
   | `ALPACA_LIVE_KEY_ID`, `ALPACA_LIVE_SECRET_KEY` | Live account |
   | `AUTOTRADER_LIVE_ENABLED=true` | Required before Live will start |

3. Install and run:

   ```sh
   cd engine
   pip install -r requirements.txt
   python -m autotrader --mode test --once              # one cycle
   python -m autotrader --mode test --budget 5000       # keep running with a $5,000 budget
   ```

Any setting can be changed with an `AUTOTRADER_<NAME>` variable, for example
`AUTOTRADER_ALWAYS_WATCH=PLTR,SOFI` or `AUTOTRADER_NEVER_BUY=GME`.

Every trade, stop and pending approval is saved to `data/journal.sqlite3`.

## Tests

```sh
cd engine
PYTHONPATH=.:tests python -m unittest discover -s tests
```

## Coming next

* Step 2: backtesting, fast replay, congressional/insider/hedge fund signals,
  SEC filings check for penny stocks, and the AI analyst.
* Step 3: the app (Test tab, Live section, approvals, budget slider).
