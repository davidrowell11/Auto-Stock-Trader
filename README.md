# Auto Stock Trader

A personal app that trades US stocks automatically. It tests itself with fake
money (the **Test** tab) before trading real money (the **Live** section),
and the same bot runs both.

| Folder | What it is | Status |
| --- | --- | --- |
| `engine/` | The trading bot (Python): strategy, risk rules, Alpaca connection, trade journal | Step 1 |
| `app/` | The Flutter app for iOS, Android, Mac and Windows | Step 3 |

See [`engine/README.md`](engine/README.md) to run the bot.
