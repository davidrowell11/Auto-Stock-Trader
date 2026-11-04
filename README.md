# Auto Stock Trader

A personal app that trades US stocks automatically. It tests itself with fake
money (the **Test** tab) before trading real money (the **Live** section),
and the same bot runs both. The bot runs on a server (Replit); the app on
your Mac, Windows PC, Android phone or iPhone controls and watches it.

**[Download the app](https://github.com/davidrowell11/Auto-Stock-Trader/releases/tag/app-latest)**
(Mac, Windows, Android) · [Install guide](docs/INSTALL_APP.md) ·
[Going live](docs/GO_LIVE.md)

## What the bot does

* **Finds buys:** strong, steady uptrends among large US stocks plus each
  day's most active and top-gaining stocks, with extra care for penny stocks.
* **Checks before buying:** recent headlines (share offerings, investigations,
  bankruptcy and similar red flags block a buy), upcoming earnings, insider and
  Congress buying, and an AI analyst (Claude) that can veto any buy.
* **Protects what it owns:** trailing safety stops, trend-break sells, daily
  loss and drawdown limits, a kill switch. It only ever touches stocks it
  bought itself. Stocks that move in step with one it holds share that
  holding's sector limit, and stocks whose prices have stopped updating are
  skipped.
* **Trades 4am to 8pm New York time:** limit orders only outside regular
  hours; protective sells always run.
* **Learns from its own trades:** once a week it scores which idea sources
  have worked, writes lessons for the analyst, and matches each new buy to
  similar past trades. It also grades the analyst: a month after each review
  it checks how the stocks it approved and blocked did against the market.
* **Tests rule changes before trusting them:** a weekly strategy-lab
  experiment that keeps a change only if it wins on prices it was never tuned on.
* **Keeps AI costs down:** a cheaper Claude model handles clear-cut reviews,
  and a monthly spending cap (default $20) pauses the analyst before credits
  run out. Each call's worst-case cost is set aside first, so the month never
  ends over the cap.

Test adapts on its own. Anything that changes how **Live** trades (lessons,
lab rule changes, cheaper reviews, after-hours buying) waits for you to switch
it on or approve it in the app.

| Folder | What it is |
| --- | --- |
| `engine/` | The trading bot and its server (Python): strategy, risk rules, signals, AI analyst, learning, strategy lab, Alpaca connection, trade journal |
| `app/` | The Flutter app for iPhone, Android, Mac and Windows |
| `docs/` | Setup guides |

## Guides

* [Install and update the app on iPhone, Android, Mac and Windows](docs/INSTALL_APP.md)
* [Going live: secrets, Replit setup and the real-money checklist](docs/GO_LIVE.md)
* [How the bot works, running and testing it](engine/README.md)
* [Building the app](app/README.md)
