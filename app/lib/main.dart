import 'package:flutter/material.dart';
import 'package:local_auth/local_auth.dart';

import 'screens/analyst.dart';
import 'screens/connect.dart';
import 'screens/dashboard.dart';
import 'screens/settings.dart';
import 'state.dart';
import 'theme.dart';
import 'widgets.dart';

void main() {
  final state = AppState()..load();
  runApp(AppScope(state: state, child: const TraderApp()));
}

class TraderApp extends StatelessWidget {
  const TraderApp({super.key});

  @override
  Widget build(BuildContext context) {
    final app = AppScope.of(context);
    return MaterialApp(
      title: 'Auto Stock Trader',
      debugShowCheckedModeBanner: false,
      theme: buildTheme(Brightness.light),
      darkTheme: buildTheme(Brightness.dark),
      themeMode: app.themeMode,
      home: !app.loaded
          ? const Scaffold(body: Center(child: CircularProgressIndicator()))
          : app.api == null
          ? const ConnectScreen()
          : const HomeShell(),
    );
  }
}

/// Bottom tabs on phones, a side rail on wide screens. Same screens everywhere.
class HomeShell extends StatefulWidget {
  const HomeShell({super.key});

  @override
  State<HomeShell> createState() => _HomeShellState();
}

class _HomeShellState extends State<HomeShell> {
  int index = 0;

  static const _destinations = [
    (Icons.science_outlined, Icons.science_rounded, 'Test'),
    (
      Icons.account_balance_wallet_outlined,
      Icons.account_balance_wallet_rounded,
      'Live',
    ),
    (Icons.auto_awesome_outlined, Icons.auto_awesome_rounded, 'Analyst'),
    (Icons.tune_outlined, Icons.tune_rounded, 'Settings'),
  ];

  Widget _page(int i) => switch (i) {
    0 => const Dashboard(key: ValueKey('test'), mode: 'test'),
    1 => const LiveLock(
      child: Dashboard(key: ValueKey('live'), mode: 'live'),
    ),
    2 => const AnalystScreen(),
    _ => const SettingsScreen(),
  };

  @override
  Widget build(BuildContext context) {
    final wide = MediaQuery.sizeOf(context).width >= 900;
    final body = SafeArea(child: _page(index));
    if (wide) {
      return Scaffold(
        body: Row(
          children: [
            NavigationRail(
              selectedIndex: index,
              onDestinationSelected: (i) => setState(() => index = i),
              labelType: NavigationRailLabelType.all,
              leading: const Padding(
                padding: EdgeInsets.symmetric(vertical: 18),
                child: Icon(
                  Icons.show_chart_rounded,
                  color: AppColors.accent,
                  size: 30,
                ),
              ),
              destinations: [
                for (final d in _destinations)
                  NavigationRailDestination(
                    icon: Icon(d.$1),
                    selectedIcon: Icon(d.$2),
                    label: Text(d.$3),
                  ),
              ],
            ),
            const VerticalDivider(width: 1),
            Expanded(child: body),
          ],
        ),
      );
    }
    return Scaffold(
      body: body,
      bottomNavigationBar: NavigationBar(
        selectedIndex: index,
        onDestinationSelected: (i) => setState(() => index = i),
        destinations: [
          for (final d in _destinations)
            NavigationDestination(
              icon: Icon(d.$1),
              selectedIcon: Icon(d.$2),
              label: d.$3,
            ),
        ],
      ),
    );
  }
}

/// Face ID / fingerprint (or device passcode) before showing the Live section.
/// Devices without any lock skip straight through.
class LiveLock extends StatefulWidget {
  const LiveLock({super.key, required this.child});
  final Widget child;

  @override
  State<LiveLock> createState() => _LiveLockState();
}

class _LiveLockState extends State<LiveLock> {
  final auth = LocalAuthentication();
  String? message;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => unlock());
  }

  Future<void> unlock() async {
    final app = AppScope.of(context);
    if (app.liveUnlocked) return;
    try {
      final supported = await auth.isDeviceSupported();
      if (!supported) {
        app.unlockLive();
        return;
      }
      final ok = await auth.authenticate(
        localizedReason: 'Unlock the Live (real money) section',
      );
      if (ok) {
        app.unlockLive();
      } else if (mounted) {
        setState(() => message = 'Not unlocked');
      }
    } catch (e) {
      // Platforms without a lock screen API (e.g. the web preview) fall through.
      app.unlockLive();
    }
  }

  @override
  Widget build(BuildContext context) {
    if (AppScope.of(context).liveUnlocked) return widget.child;
    return Center(
      child: GlassCard(
        glow: AppColors.live,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.lock_rounded, size: 42, color: AppColors.live),
            const SizedBox(height: 12),
            const Text(
              'Live uses real money',
              style: TextStyle(fontWeight: FontWeight.w800, fontSize: 18),
            ),
            const SizedBox(height: 6),
            Text(
              message ?? 'Unlock with Face ID, fingerprint or your passcode.',
            ),
            const SizedBox(height: 14),
            FilledButton.icon(
              onPressed: unlock,
              icon: const Icon(Icons.fingerprint_rounded),
              label: const Text('Unlock'),
            ),
          ],
        ),
      ),
    );
  }
}
