# Auto Stock Trader app

One Flutter app for iPhone, Android, Mac, Windows (and web), with the same
look everywhere. It connects to the bot server in `../engine`.

## Screens

* **Test**: fake-money trading, budget slider, holdings, activity, weekly report.
* **Live**: real money, locked behind Face ID / fingerprint, approvals for
  trades in approve mode, Live budget cap, kill switch.
* **Analyst**: the AI analyst's scorecard, recent calls, and a chat to ask it questions.
* **Settings**: approve mode, always-watch and never-buy lists, safety rules, theme.

Tap "Explore with sample data first" on the connect screen to try it
without a server.

## Run it

```sh
cd app
flutter pub get
flutter run                 # picks a connected phone or simulator
flutter run -d macos        # or windows, chrome
```

Builds: `flutter build ios`, `flutter build appbundle`, `flutter build macos`,
`flutter build windows`, `flutter build web --no-web-resources-cdn`.
iOS and Mac builds need a Mac with Xcode; Windows builds need Windows.

## Connect

Enter the server address (for example `https://your-bot.replit.app`) and the
`AUTOTRADER_APP_TOKEN` you set on the server. The token is kept in the
device's secure storage.

## Tests

```sh
flutter analyze && flutter test
```
