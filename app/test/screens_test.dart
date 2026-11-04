import 'package:auto_stock_trader/demo.dart';
import 'package:auto_stock_trader/screens/analyst.dart';
import 'package:auto_stock_trader/screens/settings.dart';
import 'package:auto_stock_trader/state.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

Widget _wrap(Widget child) {
  final state = AppState()..api = DemoApi();
  return AppScope(
    state: state,
    child: MaterialApp(home: Scaffold(body: child)),
  );
}

void main() {
  testWidgets('analyst screen shows what the bot learned and the lab', (
    t,
  ) async {
    t.view.physicalSize = const Size(390, 9000);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.reset);
    await t.pumpWidget(_wrap(const AnalystScreen()));
    await t.pumpAndSettle();
    expect(find.text('What the bot learned'), findsOneWidget);
    expect(find.text('Strategy lab'), findsOneWidget);
    expect(t.takeException(), isNull);
  });

  testWidgets('settings show the AI spending cap', (t) async {
    t.view.physicalSize = const Size(390, 9000);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.reset);
    await t.pumpWidget(_wrap(const SettingsScreen()));
    await t.pumpAndSettle();
    expect(find.text('Monthly spending cap'), findsOneWidget);
    expect(t.takeException(), isNull);
  });
}
