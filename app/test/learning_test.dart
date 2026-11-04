import 'package:auto_stock_trader/demo.dart';
import 'package:auto_stock_trader/models.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('learning report parses the server JSON', () {
    final l = Learning.fromJson({
      'active': null,
      'proposed': {
        'ts': '2026-11-04T18:00:00+00:00',
        'lessons': ['Lesson one.'],
        'trades': 4,
        'wins': 1,
        'weights': [
          {'source': 'screener', 'label': 'Today\'s gainers', 'weight': 0.9},
        ],
        'stats': {},
      },
      'finished_trades': 0,
      'needs': 0,
      'every_days': 7,
    });
    expect(l.active, isNull);
    expect(l.proposed!.lessons, ['Lesson one.']);
    expect(l.proposed!.weights.single.weight, 0.9);
  });

  test('demo: Live uses the lessons only after you approve them', () async {
    final api = DemoApi();
    final before = await api.learning('live');
    expect(before.active, isNull);
    expect(before.proposed, isNotNull);
    final after = await api.decideLearning(true);
    expect(after.active, isNotNull);
    expect(after.proposed, isNull);
    expect((await api.learning('test')).active, isNotNull);
  });

  test('lab report parses the server JSON', () {
    final r = LabReport.fromJson({
      'enabled': true,
      'running': false,
      'changes': [],
      'proposed': {
        'changes': [
          {
            'setting': 'atr_stop_multiple',
            'label': 'Stop',
            'standard': 2.0,
            'now': 2.5,
          },
        ],
        'entry': {
          'ts': '2026-11-04T18:00:00+00:00',
          'change': 'Stop: 2 → 2.5',
          'kept': true,
          'verdict': 'Passed',
          'evidence': ['AMD bought 2026-03-02: -6.8% → not bought'],
          'unseen': {
            'current': {'return': 0.08, 'sharpe': 0.9, 'max_drop': 0.07},
            'new': {'return': 0.11, 'sharpe': 1.1, 'max_drop': 0.06},
          },
        },
      },
      'experiments': [],
      'every_days': 7,
      'limits_note': 'x',
    });
    expect(r.proposed!.single.now, 2.5);
    expect(r.proposedEntry!.evidence, hasLength(1));
    expect(
      AiSpending.fromJson({'enabled': true, 'spent': 1.5, 'cap': 20}).cap,
      20,
    );
  });

  test('demo: Live uses lab changes only after you approve them', () async {
    final api = DemoApi();
    expect((await api.lab('live')).proposed, isNotNull);
    final after = await api.decideLab(true);
    expect(after.changes, isNotEmpty);
    expect(after.proposed, isNull);
  });
}
