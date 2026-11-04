import 'package:flutter/material.dart';
import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import 'api.dart';
import 'demo.dart';

/// App-wide state: which bot server we're connected to, and the theme.
class AppState extends ChangeNotifier {
  // On Mac, use the login keychain: the newer data-protection keychain needs an
  // Apple developer account to sign the app, and the downloadable build has none.
  static const _storage = FlutterSecureStorage(
    mOptions: MacOsOptions(usesDataProtectionKeychain: false),
  );

  TraderApi? api;
  bool demo = false;
  bool loaded = false;
  ThemeMode themeMode = ThemeMode.dark;
  bool liveUnlocked = false; // reset whenever the app restarts
  String serverUrl = '';

  Future<void> load() async {
    try {
      final url = await _storage.read(key: 'server_url');
      final token = await _storage.read(key: 'app_token');
      final theme = await _storage.read(key: 'theme');
      if (theme == 'light') themeMode = ThemeMode.light;
      if (url != null && token != null && url.isNotEmpty) {
        api = HttpApi(url, token);
        serverUrl = url;
      }
    } catch (_) {
      // Secure storage can be unavailable (e.g. some browsers); start disconnected.
    }
    loaded = true;
    notifyListeners();
  }

  /// Checks the address and token with the server, then saves them securely.
  Future<void> connect(String url, String token) async {
    final candidate = HttpApi(url, token.trim());
    await candidate.ping();
    api = candidate;
    serverUrl = candidate.base;
    demo = false;
    try {
      await _storage.write(key: 'server_url', value: serverUrl);
      await _storage.write(key: 'app_token', value: token.trim());
    } catch (_) {}
    notifyListeners();
  }

  void startDemo() {
    api = DemoApi();
    demo = true;
    notifyListeners();
  }

  Future<void> disconnect() async {
    api = null;
    demo = false;
    liveUnlocked = false;
    try {
      await _storage.delete(key: 'server_url');
      await _storage.delete(key: 'app_token');
    } catch (_) {}
    notifyListeners();
  }

  void toggleTheme() {
    themeMode = themeMode == ThemeMode.dark ? ThemeMode.light : ThemeMode.dark;
    _storage
        .write(
          key: 'theme',
          value: themeMode == ThemeMode.dark ? 'dark' : 'light',
        )
        .catchError((_) {});
    notifyListeners();
  }

  void unlockLive() {
    liveUnlocked = true;
    notifyListeners();
  }
}

/// Makes [AppState] available to every screen.
class AppScope extends InheritedNotifier<AppState> {
  const AppScope({super.key, required AppState state, required super.child})
    : super(notifier: state);

  static AppState of(BuildContext context) =>
      context.dependOnInheritedWidgetOfExactType<AppScope>()!.notifier!;
}
