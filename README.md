# Auto Stock Trader

A personal app that trades US stocks automatically. It tests itself with fake
money (the **Test** tab) before trading real money (the **Live** section),
and the same bot runs both.

| Folder | What it is |
| --- | --- |
| `engine/` | The trading bot and its server (Python): strategy, risk rules, expert signals, AI analyst, Alpaca connection, trade journal |
| `app/` | The Flutter app for iPhone, Android, Mac and Windows |
| `docs/` | Setup guides |

## Guides

* [Install the app on iPhone, Android, Mac and Windows](docs/INSTALL_APP.md)
* [Going live: secrets, Replit setup and the real-money checklist](docs/GO_LIVE.md)
* [Running and testing the bot](engine/README.md)
* [Building the app](app/README.md)
