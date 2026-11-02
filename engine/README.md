# Trading engine

The bot that does the trading. Test (fake money) and Live (real money) run
the same code; only the Alpaca account differs.

## What it does each cycle (every 5 minutes while the market is open)

1. **Watches what it owns.** Moves each trailing stop up as the price rises,
   and sells when the stop is hit, the trend breaks (close below the 50-day
   average), or a penny stock's trading volume dries up.
2. **Looks for new buys.** Scores every stock in its list for a strong,
   steady uptrend and rejects anything that fails a safety check.
3. **Checks the experts.** The top candidates get a score boost for
   open-market insider buying (SEC Form 4) and for purchases by members of
   Congress whose past picks actually did well (a leaderboard built from
   their trade history).
4. **Asks the AI analyst.** Claude reviews each buy the rules want to make,
   searches recent news for red flags (earnings next week, share offerings,
   fraud, hype), and says buy or skip with a confidence score and a short
   reason. It can only veto a buy. Its views are saved as a scorecard.

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

Penny stocks also need the AI analyst's approval (at 75%+ confidence instead
of 60%), a clean SEC filings check (no recent share offerings, no late
reports, a quarterly report in the last 6 months), a higher score, are skipped after a 50%+ jump in 5
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
   | `AUTOTRADER_SEC_CONTACT` | Your email; the SEC requires it for its free data |
   | `ANTHROPIC_API_KEY` | Turns on the AI analyst (pay per use) |
   | `QUIVER_API_TOKEN` | Optional: Congress trades from Quiver Quantitative (paid) |

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

## Backtesting and fast replay

```sh
python -m autotrader.backtest --years 5                    # full backtest
python -m autotrader.backtest --months 12 --budget 10000   # fast replay of the last year
```

It runs the same bot day by day over past prices, charges 0.10% per trade
(about double a realistic cost), compares against holding SPY, and reports
the first 70% and last 30% of the period separately. The AI analyst and
expert signals are off in backtests because Claude already knows what
happened and the signal feeds aren't point-in-time; they're judged going
forward through the scorecard instead. The default stock list is today's
large companies, so results are flattered by survivorship bias.

## Your own stocks are safe

The bot only manages stocks it bought itself (the ones it keeps a stop for).
Anything else in the same Alpaca account is left alone, including by the kill
switch. Unfilled orders are tracked so the bot never places the same order twice.

## App server

The app talks to the bot through a small web server:

```sh
cd engine
AUTOTRADER_APP_TOKEN=<long random string> python -m autotrader.server
```

It listens on `PORT` (default 8000). Every request must carry the token as
`Authorization: Bearer <token>`. On Replit, set the run command to
`cd engine && python -m autotrader.server`, put the token and your API keys
in Replit Secrets, and use an always-on Reserved VM so the bot keeps running.
The server runs one bot loop per mode (Test and Live) and stores app
settings in `data/app_settings.json`.

## Tests

```sh
cd engine
PYTHONPATH=.:tests python -m unittest discover -s tests
```

## Coming next

* Hedge fund (13F) signals and a stock screener that finds candidates beyond
  the starting list, including penny stocks.
* Push notifications for trades and alerts (the app's activity feed covers this for now).
* Strategy lab: test new strategies on paper next to the main bot.
