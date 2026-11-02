import 'package:auto_stock_trader/demo.dart';
import 'package:auto_stock_trader/models.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('demo data covers both modes and keeps them separate', () async {
    final api = DemoApi();
    final test = await api.summary('test');
    final live = await api.summary('live');
    expect(test.mode, 'test');
    expect(live.mode, 'live');
    expect(test.equity, isNot(live.equity));
    expect(await api.approvals('test'), isEmpty);
    expect(await api.approvals('live'), isNotEmpty);
  });

  test('approving a live trade removes it from the queue', () async {
    final api = DemoApi();
    final pending = await api.approvals('live');
    await api.decide('live', pending.first.id, true);
    expect(await api.approvals('live'), isEmpty);
  });

  test('budget changes stick per mode', () async {
    final api = DemoApi();
    await api.updateSettings('test', {'budget': 5000.0});
    expect((await api.settings('test')).budget, 5000);
    expect((await api.settings('live')).budget, 2500);
  });

  test('models parse server JSON', () {
    final s = Summary.fromJson({
      'mode': 'test', 'running': true, 'approve_mode': false, 'live_enabled': false,
      'pending_approvals': 0, 'equity': 1000, 'today_change_pct': 0.01,
    });
    expect(s.connected, isTrue);
    final a = Approval.fromJson({
      'id': 3, 'ts': '2026-11-02T04:00:00+00:00', 'symbol': 'AAPL', 'side': 'buy',
      'qty': 2, 'limit_price': null, 'reasons': ['Score 0.80'],
    });
    expect(a.reasons, ['Score 0.80']);
  });
}
