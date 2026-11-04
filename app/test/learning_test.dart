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
}
