# Installing the app on every platform

The app is one Flutter project in `app/`. It's for your own use, so you don't
need the App Store or Play Store. You build it once per platform and install
it on your own devices.

## One-time setup (any computer)

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

## iPhone (needs a Mac)

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

## Android

1. Install **Android Studio** (it brings the Android SDK), then run
   `flutter doctor --android-licenses`.
2. Build the app:

   ```sh
   flutter build apk --release
   ```

3. Install it:
   - **With a cable:** turn on **Developer options > USB debugging** on the phone, plug it in, and run `flutter install`.
   - **Without a cable:** copy `app/build/app/outputs/flutter-apk/app-release.apk` to the phone and open it. Allow "install unknown apps" for your file app when asked.

## Mac

```sh
flutter build macos --release
```

The app is at `app/build/macos/Build/Products/Release/auto_stock_trader.app`.
Drag it into **Applications**. The first time, right-click it and choose
**Open**, because it isn't signed by an Apple developer account.

## Windows

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

## Web (optional, for a quick look in a browser)

```sh
flutter build web --release --no-web-resources-cdn
```

Host the `app/build/web` folder anywhere that serves static files. Browsers
have no Face ID, so the web version asks you to confirm before opening Live.

## Updating

Pull the latest code (`git pull`), run `flutter pub get`, and repeat the
build and install steps for each device. Your server address and token stay
saved on each device.

## Face ID and fingerprint

The Live section locks with Face ID, Touch ID, a fingerprint or Windows Hello.
If your device has none of these, set a passcode or PIN first; otherwise Live
stays locked.
