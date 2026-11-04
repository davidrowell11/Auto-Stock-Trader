import 'package:auto_stock_trader/demo.dart';
import 'package:auto_stock_trader/screens/analyst.dart';
import 'package:auto_stock_trader/screens/settings.dart';
import 'package:auto_stock_trader/state.dart';
import 'package:auto_stock_trader/widgets.dart';
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
    expect(find.text('Were its calls right?'), findsOneWidget);
    expect(find.textContaining('vetoes are helping'), findsOneWidget);
    expect(t.takeException(), isNull);
  });

  testWidgets('the analyst chat answer appears as it is written', (t) async {
    t.view.physicalSize = const Size(390, 9000);
    t.view.devicePixelRatio = 1;
    addTearDown(t.view.reset);
    await t.pumpWidget(_wrap(const AnalystScreen()));
    await t.pumpAndSettle();
    await t.enterText(find.byType(TextField), 'How am I doing?');
    await t.tap(find.byTooltip('Send question'));
    await t.pump(const Duration(milliseconds: 100));
    expect(find.text('How am I doing?'), findsOneWidget);
    expect(find.byType(TypingDots), findsOneWidget);
    await t.pump(const Duration(milliseconds: 1200));
    final partial = find.textContaining('This is demo');
    expect(partial, findsOneWidget);
    for (var i = 0; i < 60; i++) {
      await t.pump(const Duration(milliseconds: 100));
    }
    expect(find.textContaining("today's news."), findsOneWidget);
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
