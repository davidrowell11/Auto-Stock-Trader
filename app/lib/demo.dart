import 'dart:math';

import 'api.dart';
import 'models.dart';

/// Sample data so the app can be explored before the bot server is set up.
/// Nothing here is real, and the app labels it as a demo everywhere.
class DemoApi implements TraderApi {
  DemoApi() {
    _equity = {
      'test': _curve(10000, 0.0009, 1),
      'live': _curve(2500, 0.0006, 2),
    };
  }

  final _rng = Random(7);
  late final Map<String, List<EquityPoint>> _equity;
  final Map<String, bool> _running = {'test': true, 'live': false};
  final Map<String, BotSettings> _settings = {
    'test': BotSettings(
      budget: 10000,
      approveMode: false,
      alwaysWatch: ['PLTR', 'SOFI'],
      neverBuy: ['GME'],
      maxPositionPct: 0.05,
      maxOpenPositions: 15,
      dailyLossPct: 0.02,
      cashReservePct: 0.10,
    ),
    'live': BotSettings(
      budget: 2500,
      approveMode: true,
      alwaysWatch: [],
      neverBuy: ['GME'],
      maxPositionPct: 0.05,
      maxOpenPositions: 15,
      dailyLossPct: 0.02,
      cashReservePct: 0.10,
    ),
  };
  final List<Approval> _approvals = [
    Approval(
      id: 1,
      ts: DateTime.now().subtract(const Duration(minutes: 12)),
      symbol: 'AVGO',
      side: 'buy',
      qty: 1,
      reasons: [
        'Score 0.86',
        'Price \$171.20 is above its 50-day (\$162.10) and 200-day (\$141.75) averages',
        'Up 18% over 3 months and 31% over 6 months',
        'Analyst (78% confident): Steady uptrend with strong AI-chip demand and no earnings in the next two weeks.',
      ],
    ),
  ];

  List<EquityPoint> _curve(double start, double drift, int seed) {
    final r = Random(seed);
    var v = start;
    final now = DateTime.now();
    return [
      for (var i = 120; i >= 0; i--)
        EquityPoint(
          now.subtract(Duration(days: i)),
          v = v * (1 + drift + (r.nextDouble() - 0.5) * 0.018),
        ),
    ];
  }

  @override
  Future<Summary> summary(String mode) async {
    final eq = _equity[mode]!;
    final last = eq.last.equity, prev = eq[eq.length - 2].equity;
    return Summary(
      mode: mode,
      running: _running[mode]!,
      lastCycle: DateTime.now()
          .subtract(const Duration(minutes: 3))
          .toIso8601String(),
      budget: _settings[mode]!.budget,
      approveMode: _settings[mode]!.approveMode,
      liveEnabled: true,
      pendingApprovals: mode == 'live' ? _approvals.length : 0,
      equity: last,
      cash: last * 0.32,
      todayChange: last - prev,
      todayChangePct: last / prev - 1,
      invested: last * 0.68,
      positions: (await positions(mode)).length,
      marketOpen: true,
    );
  }

  @override
  Future<List<Holding>> positions(String mode) async {
    final scale = mode == 'test' ? 1.0 : 0.25;
    Holding h(
      String s,
      double qty,
      double avg,
      double px,
      String sector,
      List<String> why,
    ) => Holding(
      symbol: s,
      qty: (qty * scale).roundToDouble().clamp(1, 1e9),
      avgPrice: avg,
      price: px,
      value: (qty * scale).roundToDouble().clamp(1, 1e9) * px,
      gain: ((qty * scale).roundToDouble().clamp(1, 1e9)) * (px - avg),
      gainPct: px / avg - 1,
      stop: px * 0.93,
      sector: sector,
      whyBought: why,
    );
    return [
      h('NVDA', 4, 118.40, 131.92, 'Technology', [
        'Score 0.91',
        'Up 22% over 3 months and 48% over 6 months',
        '3 insider(s) bought \$1,240,000 on the open market',
        'Analyst (82% confident): Momentum is strong and recent news is positive; no earnings for 3 weeks.',
      ]),
      h('JPM', 2, 241.10, 248.37, 'Financials', [
        'Score 0.74',
        'Trading at 98% of its 1-year high',
        'Analyst (71% confident): Steady trend, rate outlook supportive.',
      ]),
      h('COST', 1, 902.50, 889.10, 'Consumer', [
        'Score 0.69',
        'Price above both averages',
        'Analyst (66% confident): Defensive name in a steady uptrend.',
      ]),
      h('PLTR', 6, 61.30, 70.84, 'Technology', [
        'Score 0.88',
        'A member of Congress with a 68% hit rate bought on 2026-10-01',
        'Analyst (74% confident): Strong contract news flow; watch valuation.',
      ]),
      h('XOM', 3, 116.80, 114.20, 'Energy', [
        'Score 0.63',
        'Analyst (62% confident): Trend intact but oil prices are choppy.',
      ]),
    ];
  }

  @override
  Future<List<Trade>> trades(String mode) async => [
    for (final p in await positions(mode))
      Trade(
        ts: DateTime.now().subtract(Duration(days: 5 + p.symbol.length * 3)),
        symbol: p.symbol,
        side: 'buy',
        qty: p.qty,
        price: p.avgPrice,
        reasons: p.whyBought,
      ),
    Trade(
      ts: DateTime.now().subtract(const Duration(days: 9)),
      symbol: 'TSLA',
      side: 'sell',
      qty: 2,
      price: 242.10,
      reasons: ['Trend broke: price fell below its 50-day average'],
    ),
  ];

  @override
  Future<List<EquityPoint>> equity(String mode) async => _equity[mode]!;

  @override
  Future<List<ActivityEvent>> events(String mode) async {
    final now = DateTime.now();
    return mode == 'live'
        ? [
            ActivityEvent(
              ts: now.subtract(const Duration(minutes: 12)),
              kind: 'approval',
              message: 'Wants to buy 1 AVGO at about \$171.20. Tap to approve.',
            ),
            ActivityEvent(
              ts: now.subtract(const Duration(hours: 26)),
              kind: 'buy',
              message: 'Bought 1 NVDA at about \$118.40 (you approved)',
            ),
          ]
        : [
            ActivityEvent(
              ts: now.subtract(const Duration(minutes: 40)),
              kind: 'buy',
              message: 'Bought 6 PLTR at about \$61.30',
            ),
            ActivityEvent(
              ts: now.subtract(const Duration(hours: 5)),
              kind: 'sell',
              message: 'Sold 2 TSLA: Trend broke: price fell below its 50-day average',
            ),
            ActivityEvent(
              ts: now.subtract(const Duration(days: 1)),
              kind: 'settings',
              message: 'Settings changed: budget',
            ),
          ];
  }

  @override
  Future<AnalystFeed> analyst(String mode) async {
    final views = [
      AnalystView(
        day: '2026-11-02',
        symbol: 'AVGO',
        price: 171.2,
        action: 'buy',
        confidence: 0.78,
        reasoning: 'Steady uptrend with strong AI-chip demand. No earnings report in the next two weeks and no share offerings.',
      ),
      AnalystView(
        day: '2026-11-02',
        symbol: 'SMCI',
        price: 38.4,
        action: 'skip',
        confidence: 0.35,
        reasoning: 'Earnings are due in 3 days and the stock often swings 15% on results. Waiting until after the report.',
      ),
      AnalystView(
        day: '2026-11-01',
        symbol: 'SOFI',
        price: 4.12,
        action: 'skip',
        confidence: 0.4,
        reasoning: 'Penny stock with a share offering filed last month, which usually pushes the price down.',
      ),
      AnalystView(
        day: '2026-11-01',
        symbol: 'PLTR',
        price: 61.3,
        action: 'buy',
        confidence: 0.74,
        reasoning: 'New government contracts and a top-ranked Congress member bought recently. Valuation is high, so position stays small.',
      ),
      AnalystView(
        day: '2026-10-31',
        symbol: 'NVDA',
        price: 118.4,
        action: 'buy',
        confidence: 0.82,
        reasoning: 'Momentum is strong, three insiders bought on the open market, and recent news is positive.',
      ),
    ];
    return AnalystFeed(views: views, total: 42, approved: 29);
  }

  @override
  Future<List<Approval>> approvals(String mode) async =>
      mode == 'live' ? List.of(_approvals) : [];

  @override
  Future<void> decide(String mode, int id, bool approve) async =>
      _approvals.removeWhere((a) => a.id == id);

  @override
  Future<BotSettings> settings(String mode) async => _settings[mode]!;

  @override
  Future<BotSettings> updateSettings(
    String mode,
    Map<String, dynamic> changes,
  ) async {
    final s = _settings[mode]!;
    return _settings[mode] = BotSettings(
      budget: changes['clear_budget'] == true
          ? null
          : (changes['budget'] as num?)?.toDouble() ?? s.budget,
      approveMode: changes['approve_mode'] as bool? ?? s.approveMode,
      alwaysWatch:
          (changes['always_watch'] as List?)?.cast<String>() ?? s.alwaysWatch,
      neverBuy: (changes['never_buy'] as List?)?.cast<String>() ?? s.neverBuy,
      maxPositionPct: s.maxPositionPct,
      maxOpenPositions: s.maxOpenPositions,
      dailyLossPct: s.dailyLossPct,
      cashReservePct: s.cashReservePct,
    );
  }

  @override
  Future<void> setRunning(String mode, bool running) async =>
      _running[mode] = running;

  @override
  Future<KillResult> kill(String mode, {required bool sellAll}) async {
    _running[mode] = false;
    return (
      sold: sellAll
          ? [for (final p in await positions(mode)) p.symbol]
          : <String>[],
      failed: <String>[],
    );
  }

  @override
  Future<WeeklyReport> weekly(String mode) async => WeeklyReport(
    since: '2026-10-26',
    changePct: mode == 'test' ? 0.021 : 0.008,
    buys: 4,
    sells: 2,
    analystReviews: 11,
    analystApproved: 7,
  );

  @override
  Future<List<PriceBar>> bars(
    String symbol, {
    int days = 180,
    String mode = 'test',
  }) async {
    var p = 50.0 + symbol.codeUnits.fold<int>(0, (a, b) => a + b) % 150;
    final now = DateTime.now();
    final r = Random(symbol.hashCode);
    return [
      for (var i = days; i >= 0; i--)
        () {
          final o = p;
          final c = o * (1 + 0.0015 + (r.nextDouble() - 0.5) * 0.035);
          p = c;
          return PriceBar(
            now.subtract(Duration(days: i)),
            o,
            max(o, c) * (1 + r.nextDouble() * 0.01),
            min(o, c) * (1 - r.nextDouble() * 0.01),
            c,
            1e6,
          );
        }(),
    ];
  }

  @override
  Future<String> ask(String question, String mode) async {
    await Future<void>.delayed(Duration(milliseconds: 600 + _rng.nextInt(600)));
    return "This is demo mode, so I can't look anything up yet. Once your bot server is connected, "
        "I'll answer questions like this using your real positions, trades and today's news.";
  }
}
