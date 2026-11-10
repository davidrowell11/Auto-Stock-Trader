# Trading engine

The bot that does the trading. Test (fake money) and Live (real money) run
the same code; only the Alpaca account differs.

## When it trades

Every 5 minutes from 4am to 8pm New York time on trading days (Alpaca's
calendar, so holidays and early closes are handled).

- **Regular hours (9:30am to 4pm):** everything below.
- **Pre-market (4am to 9:30am) and after hours (4pm to 8pm):** limit orders
  only, using the latest trade price. It always watches what it owns and
  sells when a safety stop hits (accepting up to 2% under the last trade).
  New buys happen only if "buy before and after hours" is on (default: on
  for Test, off for Live). They are whole shares, pay at most 0.5% over the
  last trade, and never penny stocks.
- An order still unfilled at the next check is cancelled and placed again
  at the new price, so a pre-market sell can't sit below a falling market.

## What it does each cycle

1. **Watches what it owns.** Moves each trailing stop up as the price rises,
   and sells when the stop is hit, the trend breaks (close below the 50-day
   average), or a penny stock's trading volume dries up.
2. **Looks for new buys.** Scores every stock in its list for a strong,
   steady uptrend and rejects anything that fails a safety check. The list is
   the fixed set of large names in `universe.py` plus, each day, Alpaca's
   most-active and top-gaining stocks (`signals/screener.py`; set
   `AUTOTRADER_SCREENER=false` to turn that off).
3. **Reads the news.** Before any buy it pulls the last two days of headlines
   for the stock from Alpaca's news feed. A share offering, investigation,
   bankruptcy, delisting, executive departure or short-seller report blocks
   the buy; the headlines also go to the AI analyst.
4. **Checks the experts.** The top candidates get a score boost for
   open-market insider buying (SEC Form 4) and for purchases by members of
   Congress whose past picks actually did well (a leaderboard built from
   their trade history).
5. **Asks the AI analyst.** Claude reviews each buy the rules want to make,
   searches recent news for red flags (earnings next week, share offerings,
   fraud, hype), and says buy or skip with a confidence score and a short
   reason. It can only veto a buy. Its views are saved as a scorecard.
   With cheaper reviews on (default in Test, a switch for Live), a fast,
   low-cost model looks first; its answer stands only when it's a clear
   call (a buy at least 15 points above the confidence needed with no red
   flags, or a skip that names a red flag). Close calls and every penny
   stock go to the full analyst.
6. **Learns from its own trades** (`learning.py`). Each buy records what
   pointed the bot at the stock (trend rules, the screener, insiders,
   Congress, the analyst). Once a week it looks at its finished trades:
   sources that keep leading to winners count a little more when ranking
   buys and losing ones a little less (always between 0.7x and 1.3x, and
   only after five finished trades), and Claude writes up to six short
   lessons that go into every analyst review. Test uses new lessons at once;
   Live only after you approve them in the app. Learning never touches
   stops, sizes, budgets or penny-stock limits.
7. **Tests one rule change a week** (`lab.py`, the strategy lab). It tries
   one step on one setting (safety-stop distance, minimum trend score),
   replays it on the older two thirds of three years of prices, then on the
   newer third it never tuned on. It keeps the change only if it wins on
   both (Sharpe +0.10 on the unseen part, higher return, a drop no more than
   1 point worse) and fixes some of the current rules' losing trades. Test
   uses it at once; Live after your approval; either can be undone.
8. **Grades the analyst** (`scorecard.py`). With the weekly review it looks
   at every analyst call at least a month old (20 trading days) and compares
   how the approved and the blocked stocks did against SPY over that month.
   The result is shown in the app ("Were its calls right?") and given to the
   weekly lessons, so the analyst hears about winners it turned away too.

**AI spending cap** (`spend.py`): every Claude call is priced from its token
counts and counted toward a monthly cap ($20 by default, set in the app).
Before a call starts, its worst-case cost (its whole output allowance plus
what its web searches could add) is set aside, and the call only goes out if
that still fits, so calls running side by side can't overshoot the cap.
At the cap the analyst pauses until the 1st: no penny-stock buys, other buys
on the rules alone, as when Claude can't be reached.

## Safety rules

Hard limits live in `autotrader/config.py` (`HardLimits`). Settings can be
tuned, but are always clamped inside them.

Eight rules can be changed in the app (`RULES` in `config.py`, served by
`GET/PUT /api/{mode}/rules`): max per stock, max stocks at once, the daily loss
stop, cash kept aside, most one trade may lose, new stocks per day, max in one
sector and the safety stop distance. Each has a range inside the hard limits,
and the server checks every value again. A rule you set wins over the strategy
lab, and the lab stops experimenting with it. In Live, safer changes start at
once, riskier ones start at the next 9:30am ET open, and during the 30-day
starter period rules can only be made safer. Every change goes in the activity
feed.

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

* Hedge fund (13F) signals.
* Push notifications for trades and alerts (the app's activity feed covers this for now).
