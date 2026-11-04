# Going live: setup and checklist

This is the path from nothing to real-money trading. Each phase must be done
before the next. Keys and tokens always go in **Replit Secrets** (or your
computer's environment variables). Never paste them into chat, the app or code.

## Phase 1: Run the bot in Test (fake money)

- [ ] Open a free [Alpaca](https://alpaca.markets) account and create **paper** API keys.
- [ ] Import this repo at [replit.com/import](https://replit.com/import) (GitHub), then add these in the **Secrets** tool:

  | Secret | What it is |
  |---|---|
  | `ALPACA_TEST_KEY_ID`, `ALPACA_TEST_SECRET_KEY` | Alpaca paper keys |
  | `AUTOTRADER_APP_TOKEN` | A long random password the app uses to talk to the bot |
  | `AUTOTRADER_SEC_CONTACT` | Your email (the SEC asks programs to identify themselves) |
  | `ANTHROPIC_API_KEY` | Claude API key for the AI analyst (pay as you go; see below) |
  | `QUIVER_API_TOKEN` | Optional: Congress trades (paid) |
  | `FINNHUB_API_KEY` | Optional: free key for the earnings calendar |

- [ ] Press **Run** once to check it starts. The repo's `.replit` file already sets the run command.
- [ ] Open **Deployments** and choose **Reserved VM** (always on), then **Deploy**. Autoscale sleeps when idle and would miss trades.
- [ ] Open `https://<your-app>.replit.app/health` and check it says `{"ok": true}`.
- [ ] In the app, connect with the server address and `AUTOTRADER_APP_TOKEN`.
- [ ] On the Test tab, set the budget and press **Start bot**.

**About the Claude API key.** API credits are bought at
[console.anthropic.com](https://console.anthropic.com) under **Plans & Billing**,
separately from a Claude.ai subscription. Make the key in the same Console
organization that has the credits. In the app's **Settings > AI analyst costs**
you'll see this month's spending and a monthly cap ($20 to start). At the cap
the analyst pauses until the 1st: no penny-stock buys, other buys on the rules
alone. Cheaper reviews (on for Test) cut the cost of most reviews a lot.

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

## Letting Live use what Test learned

Test changes itself as it learns. Live never does without you:

- **What the bot learned** (AI analyst screen): about once a week Test writes
  new lessons and adjusts how much each idea source counts. Live shows them
  with **Use in Live** or **Not now**.
- **Strategy lab** (same screen): when a rule change passes every test, Test
  uses it and Live asks first. **Undo** goes back to the standard rules.
- **Cheaper AI reviews** and **after-hours buying** (Settings): on for Test,
  off for Live until you switch them on.

A good habit: approve a change for Live only after Test has used it for a
couple of weeks without surprises.

## Safety nets that are always on

| Net | What happens |
|---|---|
| Kill switch | One tap stops the bot now; optionally sells everything it bought. Your own stocks are never touched. |
| Safety stop | If Live loses 10% of its money from its peak, or 3 cycles fail in a row, it stops buying and tells you. Stops on what it holds stay active. Press Start to resume. |
| Daily loss cap | No new buys for the rest of the day after a 2% loss. |
| Drawdown brake | Half-size buys at 10% below peak; all buying pauses at 15%. |
| Market mood | Half-size buys and no penny stocks when SPY is below its 200-day average; no new buys in a confirmed market downtrend. |
| Earnings guard | No buys in the week before a company reports earnings. |
| Cool-off | After a stock is sold to protect you (stop or trend break), the bot won't buy it again for a week. |
| Approvals expire | A suggested buy expires after 24 hours, and the rules are checked again when you approve it. |
| Learning limits | What the bot learns can only change which stocks rank first, what the analyst watches for, and (via the lab) the stop distance or minimum trend score within the hard limits. Budgets, position sizes and penny rules never change. |
| AI spending cap | The analyst pauses for the rest of the month at your cap. |

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
`engine/data/app_settings.json`. Download a copy from Replit now and then. The
journal also holds what the bot learned and its AI spending. If the journal is
lost, the bot forgets which stocks it bought. It then leaves
them alone, and you can sell them in the Alpaca app.

## Taxes

Gains on stocks held under a year are taxed as regular income. The wash-sale
rule blocks a loss deduction if you rebuy within 30 days. Alpaca sends a 1099
each year.
