# Going live: setup and checklist

This is the path from nothing to real-money trading. Each phase must be done
before the next. Keys and tokens always go in **Replit Secrets** (or your
computer's environment variables). Never paste them into chat, the app or code.

## Phase 1: Run the bot in Test (fake money)

- [ ] Open a free [Alpaca](https://alpaca.markets) account and create **paper** API keys.
- [ ] Create a Replit app from this repo and add these Secrets:

  | Secret | What it is |
  |---|---|
  | `ALPACA_TEST_KEY_ID`, `ALPACA_TEST_SECRET_KEY` | Alpaca paper keys |
  | `AUTOTRADER_APP_TOKEN` | A long random password the app uses to talk to the bot |
  | `AUTOTRADER_SEC_CONTACT` | Your email (the SEC asks programs to identify themselves) |
  | `ANTHROPIC_API_KEY` | Claude API key for the AI analyst (pay as you go) |
  | `QUIVER_API_TOKEN` | Optional: Congress trades (paid) |
  | `FINNHUB_API_KEY` | Optional: free key for the earnings calendar |

- [ ] Set the run command to `cd engine && pip install -r requirements.txt && python -m autotrader.server`.
- [ ] Deploy as a **Reserved VM** (always on). Autoscale sleeps when idle and would miss trades.
- [ ] Open `https://<your-app>.replit.app/health` and check it says `{"ok": true}`.
- [ ] In the app, connect with the server address and `AUTOTRADER_APP_TOKEN`.
- [ ] On the Test tab, set the budget and press **Start bot**.

## Phase 2: Let it prove itself (8+ weeks)

The Live section shows a **Before real money** card. Live can't start until:

- [ ] Test has run for at least 8 weeks.
- [ ] Test beat simply holding SPY over that time.
- [ ] Test's biggest drop was under 10% of its budget.

Check the weekly report and the analyst scorecard along the way. Paper fills
are kinder than real ones, especially for penny stocks, so treat good Test
results as necessary, not as a promise.

## Phase 3: Go live small

- [ ] Open an Alpaca **live** account as a **cash account** (no margin). Sale money takes one business day to settle before it can be reused.
- [ ] Fund it with only what you're prepared to risk.
- [ ] Add the Secrets `ALPACA_LIVE_KEY_ID`, `ALPACA_LIVE_SECRET_KEY` and `AUTOTRADER_LIVE_ENABLED=true`, then redeploy.
- [ ] In Settings, set the **Live budget cap**. It can't be over **$1,000 for the first 30 days** after Live first starts.
- [ ] Keep **Approve each trade** on. Every real buy waits for your tap; protective sells never wait.
- [ ] Unlock Live with Face ID or fingerprint and press **Start bot**.

## Safety nets that are always on

| Net | What happens |
|---|---|
| Kill switch | One tap stops the bot now; optionally sells everything it bought. Your own stocks are never touched. |
| Safety stop | If Live loses 10% of its money from its peak, or 3 cycles fail in a row, it stops buying and tells you. Stops on what it holds stay active. Press Start to resume. |
| Daily loss cap | No new buys for the rest of the day after a 2% loss. |
| Drawdown brake | Half-size buys at 10% below peak; all buying pauses at 15%. |
| Market mood | Half-size buys and no penny stocks when SPY is below its 200-day average; no new buys in a confirmed market downtrend. |
| Earnings guard | No buys in the week before a company reports earnings. |
| Approvals expire | A suggested buy expires after 24 hours, and the rules are checked again when you approve it. |

## When to stop and look (rollback triggers)

Press the kill switch, or Stop, if any of these happen:

- A trade you don't recognize, or a stock bought that's on your never-buy list.
- The app shows **Needs attention** for more than an hour during market hours.
- Live is down more than 5% in a week while SPY is up.
- Anything that makes you uneasy. Stopping is free; the bot can restart any time.

## After 30 days

- [ ] Compare Live with Test side by side. Real fills should be close to paper fills.
- [ ] If they match, raise the Live cap slowly (for example $1,000, then $2,500, then $5,000), at least a month at each step.
- [ ] Only switch off approve mode once you've agreed with most of its suggestions for several weeks.

## Backups

The trade journal lives in `engine/data/journal.sqlite3`, and app settings in
`engine/data/app_settings.json`. Download a copy from Replit now and then. If
the journal is lost, the bot forgets which stocks it bought. It then leaves
them alone, and you can sell them in the Alpaca app.

## Taxes

Gains on stocks held under a year are taxed as regular income. The wash-sale
rule blocks a loss deduction if you rebuy within 30 days. Alpaca sends a 1099
each year.
