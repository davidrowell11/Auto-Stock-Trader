# Installing the app on every platform

The app is one Flutter project in `app/`. It's for your own use, so you don't
need the App Store or Play Store.

## Easiest: download it (Mac, Windows, Android)

GitHub builds the app for you every time it changes, so you don't need Xcode,
Visual Studio or Android Studio. Get the files from the
[App downloads](https://github.com/davidrowell11/Auto-Stock-Trader/releases/tag/app-latest) page.

**Mac** (`AutoStockTrader-mac.dmg`)

1. Download the dmg and double-click it. A window opens with the app and an
   **Applications** folder.
2. Drag the app onto **Applications**.
3. Double-click it in Applications. The first time, macOS says it can't check
   the developer (the app isn't signed with a paid Apple account). Click **Done**.
4. Open **System Settings > Privacy & Security**, scroll down to the message
   about auto_stock_trader, click **Open Anyway** and enter your Mac password.
   After this it opens normally.

If step 4 doesn't show the button, open **Terminal** and paste
`xattr -dr com.apple.quarantine /Applications/auto_stock_trader.app`, then open the app again.

**Windows** (`AutoStockTrader-windows-setup.exe`)

1. Download and run the setup file. If SmartScreen warns that the app is
   unrecognized, choose **More info > Run anyway**.
2. Click through the installer. It installs to `C:\Program Files\Auto Stock Trader`
   and adds a Start menu shortcut (and a desktop one if you tick the box).

**Android** (`AutoStockTrader-android.apk`)

1. On the phone, download the file and open it.
2. Allow "install unknown apps" for your browser when asked.

## Connecting the app to your bot

The first time the app opens it asks for two things:

- **Server address:** your Replit app's address, for example
  `auto-stock-trader.replit.app`. You can leave off `https://`; the app adds it.
- **App token:** the value of the `AUTOTRADER_APP_TOKEN` secret, from the
  **Secrets** tool in your Replit project.

The app keeps both in the device's secure storage, so you only enter them once
per device. To look around before the bot is set up, tap **Explore with sample
data first** instead.

## Where things are

- **Test** and **Live:** the account, what the bot is doing right now,
  holdings, activity, and (Live) buys waiting for your approval.
- **AI analyst:** its recent calls, a chat box, **What the bot learned**
  (weekly lessons and how much each idea source counts) and the
  **Strategy lab** (rule changes it tested). In Live, new lessons and rule
  changes wait here for **Use in Live** or **Not now**.
- **Settings:** budgets, approve mode, trading hours, **AI analyst costs**
  (cheaper reviews and the monthly spending cap), and your always-watch and
  never-buy lists.

## Updating to a new version

When the bot gets new features, the server (Replit) is updated first, then a
new app is published on the
[App downloads](https://github.com/davidrowell11/Auto-Stock-Trader/releases/tag/app-latest)
page. Install it the same way as the first time.

Every build has the same app name and ID and a higher version number, so a new
download replaces the old app instead of installing a second copy. Your server
address and token are kept.

- **Mac:** quit the app, open the new dmg and drag the app onto
  **Applications** again. When asked, choose **Replace**. If macOS asks to let
  the app use your saved login (keychain), choose **Always Allow**.
- **Windows:** quit the app and run the new setup file. It updates the same
  install (it closes the app for you if it's still open).
- **Android:** open the new apk and tap **Update**. All builds are signed with
  the same saved key, so Android accepts it as an update. The one exception is
  the first build after the signing key was set up: if Android says the app
  "conflicts with an existing package", uninstall the old one once and install
  the new one. After that, updates install over the top.

**iPhone** can't be downloaded this way: Apple only lets you install your own
apps on an iPhone through Xcode on a Mac (see below), or TestFlight with a
paid developer account.

## Building it yourself

You only need this for iPhone, or if you'd rather build from the code.

### One-time setup (any computer)

1. Install Flutter: https://docs.flutter.dev/get-started/install
   (pick your operating system and follow the steps).
2. Run `flutter doctor` and fix anything it marks with a red X for the
   platforms you want.
3. Get the code and its packages:

   ```sh
   git clone https://github.com/davidrowell11/Auto-Stock-Trader.git
   cd Auto-Stock-Trader/app
   flutter pub get
   ```

After installing, open the app and either connect it to your bot server
(the address and `AUTOTRADER_APP_TOKEN`) or tap **Explore with sample data first**.

### iPhone (needs a Mac)

1. Install **Xcode** from the Mac App Store, open it once, and accept the license.
2. Plug in your iPhone. On the phone, turn on **Settings > Privacy & Security > Developer Mode** and restart.
3. Open `app/ios/Runner.xcworkspace` in Xcode. Under **Signing & Capabilities**,
   choose your Apple ID as the Team (a free Apple ID works) and change the
   Bundle Identifier to something unique, such as `com.yourname.autostocktrader`.
4. Install it:

   ```sh
   flutter run --release -d <your iPhone>   # flutter devices lists the name
   ```

5. On the phone, trust the developer: **Settings > General > VPN & Device Management**.

With a free Apple ID the app stops opening after **7 days**, and you
re-run step 4 to renew it. A paid Apple Developer account ($99 a year) makes
it last a year and lets you use TestFlight.

### Android

1. Install **Android Studio** (it brings the Android SDK), then run
   `flutter doctor --android-licenses`.
2. Build the app:

   ```sh
   flutter build apk --release
   ```

3. Install it:
   - **With a cable:** turn on **Developer options > USB debugging** on the phone, plug it in, and run `flutter install`.
   - **Without a cable:** copy `app/build/app/outputs/flutter-apk/app-release.apk` to the phone and open it. Allow "install unknown apps" for your file app when asked.

### Mac (needs Xcode)

```sh
flutter build macos --release
```

The app is at `app/build/macos/Build/Products/Release/auto_stock_trader.app`.
Drag it into **Applications**. The first time, right-click it and choose
**Open**, because it isn't signed by an Apple developer account.

### Windows

1. Install **Visual Studio 2022** (Community is free) with the
   **Desktop development with C++** workload.
2. Build:

   ```powershell
   flutter build windows --release
   ```

3. The app is the whole folder `app\build\windows\x64\runner\Release\`. Copy
   that folder somewhere permanent, such as `C:\Program Files\Auto Stock Trader`,
   and make a shortcut to `auto_stock_trader.exe`. Windows SmartScreen may warn
   that the app is unrecognized: choose **More info > Run anyway**.

### Web (optional, for a quick look in a browser)

```sh
flutter build web --release --no-web-resources-cdn
```

Host the `app/build/web` folder anywhere that serves static files. Browsers
have no Face ID, so the web version asks you to confirm before opening Live.

## Updating a build you made yourself

Pull the latest code (`git pull`), run `flutter pub get`, and repeat the build
and install steps. Downloaded apps update as described in
[Updating to a new version](#updating-to-a-new-version).

## Face ID and fingerprint

The Live section locks with Face ID, Touch ID, a fingerprint or Windows Hello.
If your device has none of these, set a passcode or PIN first; otherwise Live
stays locked.
