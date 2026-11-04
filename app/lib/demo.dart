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
      afterHoursBuys: true,
      quickReviews: true,
    ),
    'live': BotSettings(
      budget: 1000,
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
      session: 'regular',
      moodLevel: 'calm',
      mood: 'Market calm: SPY \$683.20 is above its 200-day average',
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
    return AnalystFeed(views: views, total: 42, approved: 29, quick: 31);
  }

  late final _memory = LearnedMemory(
    ts: DateTime.now().subtract(const Duration(days: 2)),
    lessons: [
      'Stocks bought the day after a 15%+ jump on the gainers list usually gave the gain back; be wary of chasing them.',
      'Buys backed by several insiders held up best, even in choppy weeks.',
      'Most losing trades were sold for "trend broke" within a week; a fresh breakout that fails fast is a warning sign.',
    ],
    trades: 23,
    wins: 14,
    weights: [
      (label: 'Insider buying', weight: 1.12),
      (label: "Congress members' buys", weight: 1.0),
      (label: "Today's most active and top gainers", weight: 0.86),
    ],
  );
  bool _liveApproved = false;

  @override
  Future<Learning> learning(String mode) async => Learning(
    active: mode == 'test' || _liveApproved ? _memory : null,
    proposed: mode == 'live' && !_liveApproved ? _memory : null,
    finishedTrades: mode == 'test' ? 23 : 0,
    needs: 0,
    everyDays: 7,
    scorecard: Scorecard(
      approved: ScoreSide(calls: 18, avgReturn: 0.041, avgVsMarket: 0.022),
      blocked: ScoreSide(calls: 9, avgReturn: -0.012, avgVsMarket: -0.031),
      waiting: 11,
      horizonDays: 20,
      verdict:
          'Its vetoes are helping: stocks it blocked did 5.3% worse over the next month '
          'than stocks it approved.',
      missed: [(symbol: 'SMCI', day: '2026-09-14', vsMarket: 0.084)],
      letThrough: [(symbol: 'INTC', day: '2026-08-21', vsMarket: -0.071)],
    ),
  );

  @override
  Future<Learning> decideLearning(bool approve) async {
    _liveApproved = approve;
    return learning('live');
  }

  @override
  Future<Learning> learnNow() => learning('test');

  bool _labApproved = false;
  double _aiCap = 20;

  LabExperiment get _labWin => LabExperiment(
    ts: DateTime.now().subtract(const Duration(days: 3)),
    change: 'Safety stop distance (x average daily move): 2 → 2.5',
    kept: true,
    verdict: 'Passed every check; Test uses it now',
    evidence: [
      'AMD bought at \$148.20: -6.8% → +2.1%',
      'UBER bought at \$71.35: -5.9% → not bought',
    ],
    unseenCurrent: {'return': 0.084, 'sharpe': 0.91, 'max_drop': 0.072},
    unseenNew: {'return': 0.112, 'sharpe': 1.08, 'max_drop': 0.069},
  );

  @override
  Future<LabReport> lab(String mode) async {
    final change = LabChange(
      label: 'Safety stop distance (x average daily move)',
      standard: 2,
      now: 2.5,
    );
    final live = mode == 'live';
    return LabReport(
      enabled: true,
      running: false,
      changes: !live || _labApproved ? [change] : [],
      proposed: live && !_labApproved ? [change] : null,
      proposedEntry: live && !_labApproved ? _labWin : null,
      experiments: [
        _labWin,
        LabExperiment(
          ts: DateTime.now().subtract(const Duration(days: 10)),
          change: 'Lowest trend score it will buy: 0 → 0.2',
          kept: false,
          verdict: 'Better on older prices but not on the newer, unseen ones',
          evidence: [],
        ),
      ],
      everyDays: 7,
      limitsNote: 'Stops stay within 8% of the buy price; sizes, budgets and penny rules never change.',
    );
  }

  @override
  Future<LabReport> decideLab(bool approve) async {
    _labApproved = approve;
    return lab('live');
  }

  @override
  Future<LabReport> undoLab(String mode) => lab(mode);

  @override
  Future<LabReport> runLab() => lab('test');

  @override
  Future<AiSpending> aiSpending() async =>
      AiSpending(enabled: true, spent: 6.42, cap: _aiCap);

  @override
  Future<AiSpending> setAiCap(double monthlyCap) async {
    _aiCap = monthlyCap;
    return aiSpending();
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
      afterHoursBuys: changes['after_hours_buys'] as bool? ?? s.afterHoursBuys,
      quickReviews: changes['quick_reviews'] as bool? ?? s.quickReviews,
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
  Future<Readiness> readiness() async => Readiness(
    ready: false,
    readyExceptResults: true,
    skipAllowed: false,
    starterCap: 1000,
    checks: [
      ReadinessCheck(
        '8+ weeks of Test results',
        false,
        '5.3 of 8 weeks so far',
      ),
      ReadinessCheck('Beats just holding SPY', true, 'Bot +6.4% vs SPY +3.1%'),
      ReadinessCheck(
        'Biggest drop under 10%',
        true,
        'Biggest drop so far 4.2%',
      ),
      ReadinessCheck(
        'Live trading switched on in the server',
        true,
        'AUTOTRADER_LIVE_ENABLED is on',
      ),
      ReadinessCheck('Live Alpaca account connected', true, 'Connected'),
      ReadinessCheck('Live budget cap set (start small)', true, 'Cap \$1,000'),
    ],
  );

  @override
  Future<LiveStatus> live(String mode) async {
    final now = DateTime.now();
    if (_running[mode] != true) {
      return LiveStatus(
        now: 'Stopped. Press Start bot to begin.',
        since: now,
        working: false,
        steps: [],
      );
    }
    final texts = [
      'Checked 52 stocks: bought MSFT',
      'Buying 4 MSFT at about \$412.30',
      'Asking the AI analyst about MSFT',
      'Checking what Congress members and company insiders are buying',
      '3 passed the trend and safety checks: MSFT, AVGO, COST',
      'Looking for buys among 52 stocks',
      'Checking NVDA: price \$121.40, safety stop \$109.80',
      'Watching 3 stocks it owns',
      'Getting prices for 56 stocks',
      'Starting a check',
    ];
    return LiveStatus(
      now: 'Resting until the next check. Checked 52 stocks: bought MSFT.',
      since: now.subtract(const Duration(minutes: 1)),
      nextCheck: now.add(const Duration(minutes: 4)),
      working: false,
      steps: [
        for (var i = 0; i < texts.length; i++)
          BotStep(now.subtract(Duration(seconds: 60 + i * 6)), texts[i]),
      ],
      lastCycle: LastCycle(
        ts: now.subtract(const Duration(minutes: 1)),
        summary: 'Checked 52 stocks: bought MSFT',
        checked: 52,
        skipped: [
          'AVGO: analyst confidence 55% is below the 60% needed',
          'COST: reports earnings in 3 days',
          'TSLA: the uptrend isn\'t steady enough',
        ],
        skippedTotal: 3,
      ),
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
    final raw = [
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
    // Line the made-up history up with the demo holding's price.
    final target = _demoPrices[symbol];
    if (target == null) return raw;
    final k = target / raw.last.c;
    return [
      for (final b in raw)
        PriceBar(b.t, b.o * k, b.h * k, b.l * k, b.c * k, b.v),
    ];
  }

  static const _demoPrices = {
    'NVDA': 131.92,
    'JPM': 248.37,
    'COST': 889.10,
    'PLTR': 70.84,
    'XOM': 114.20,
    'SPY': 683.20,
  };

  @override
  Future<String> ask(String question, String mode) async {
    await Future<void>.delayed(Duration(milliseconds: 500 + _rng.nextInt(400)));
    return "This is demo mode, so I can't look anything up yet. Once your bot server is connected, "
        "I'll answer questions like this using your real positions, trades and today's news.";
  }

  @override
  Stream<String> askStream(String question, String mode) async* {
    final answer = await ask(question, mode);
    for (final word in answer.split(' ')) {
      await Future<void>.delayed(Duration(milliseconds: 25 + _rng.nextInt(40)));
      yield '$word ';
    }
  }
}
