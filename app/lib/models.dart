double _d(dynamic v) => v == null ? 0 : (v as num).toDouble();
double? _dn(dynamic v) => v == null ? null : (v as num).toDouble();
List<String> _sl(dynamic v) =>
    v == null ? [] : [for (final s in v as List) s.toString()];

class Summary {
  Summary({
    required this.mode,
    required this.running,
    this.error,
    this.halted,
    this.lastCycle,
    this.budget,
    required this.approveMode,
    required this.liveEnabled,
    required this.pendingApprovals,
    this.equity,
    this.cash,
    this.todayChange,
    this.todayChangePct,
    this.invested,
    this.otherHoldings,
    this.positions,
    this.marketOpen,
    this.session,
    this.mood,
    this.moodLevel,
    this.buyingPaused,
  });

  final String mode;
  final bool running;
  final String? error;
  final String? halted;
  final String? lastCycle;
  final double? budget;
  final bool approveMode;
  final bool liveEnabled;
  final int pendingApprovals;
  final double? equity, cash, todayChange, todayChangePct, invested;

  /// Stocks in the account the bot didn't buy (it leaves them alone).
  final double? otherHoldings;
  final int? positions;
  final bool? marketOpen;
  final String? session; // regular, pre (4am-9:30am), after (4pm-8pm), overnight (8pm-4am) or closed
  final String? mood; // market mood note from the last cycle
  final String? moodLevel; // calm, cautious, risk-off or unknown
  final String? buyingPaused; // set when Live stopped itself for safety

  bool get connected => equity != null;

  factory Summary.fromJson(Map<String, dynamic> j) => Summary(
    mode: j['mode'] as String,
    running: j['running'] as bool? ?? false,
    error: j['error'] as String?,
    halted: j['halted'] as String?,
    lastCycle: j['last_cycle'] as String?,
    budget: _dn(j['budget']),
    approveMode: j['approve_mode'] as bool? ?? true,
    liveEnabled: j['live_enabled'] as bool? ?? false,
    pendingApprovals: (j['pending_approvals'] as num?)?.toInt() ?? 0,
    equity: _dn(j['equity']),
    cash: _dn(j['cash']),
    todayChange: _dn(j['today_change']),
    todayChangePct: _dn(j['today_change_pct']),
    invested: _dn(j['invested']),
    otherHoldings: _dn(j['other_holdings']),
    positions: (j['positions'] as num?)?.toInt(),
    marketOpen: j['market_open'] as bool?,
    session: j['session'] as String?,
    mood: j['mood'] as String?,
    moodLevel: j['mood_level'] as String?,
    buyingPaused: j['buying_paused'] as String?,
  );
}

/// Shares, with fractions shown only when there are any (e.g. 12 or 0.277).
String shares(double q) => q % 1 == 0
    ? q.toStringAsFixed(0)
    : q.toStringAsFixed(3).replaceAll(RegExp(r'\.?0+$'), '');

class ReadinessCheck {
  ReadinessCheck(this.name, this.ok, this.detail);
  final String name;
  final bool ok;
  final String detail;
}

/// Whether Live may start: Test results goal plus the start-small safety checks.
class Readiness {
  Readiness({
    required this.ready,
    required this.readyExceptResults,
    required this.skipAllowed,
    required this.checks,
    this.starterCap,
  });

  final bool ready, readyExceptResults, skipAllowed;
  final List<ReadinessCheck> checks;
  final double? starterCap;

  factory Readiness.fromJson(Map<String, dynamic> j) => Readiness(
    ready: j['ready'] as bool? ?? false,
    readyExceptResults: j['ready_except_results'] as bool? ?? false,
    skipAllowed: j['skip_results_allowed'] as bool? ?? false,
    starterCap: _dn(j['starter_cap']),
    checks: [
      for (final c in (j['checks'] as List? ?? []))
        ReadinessCheck(
          c['name'] as String,
          c['ok'] as bool? ?? false,
          c['detail'] as String? ?? '',
        ),
    ],
  );
}

class Holding {
  Holding({
    required this.symbol,
    required this.qty,
    required this.avgPrice,
    required this.price,
    required this.value,
    required this.gain,
    required this.gainPct,
    this.stop,
    required this.sector,
    required this.whyBought,
  });

  final String symbol;
  final double qty, avgPrice, price, value, gain, gainPct;
  final double? stop;
  final String sector;
  final List<String> whyBought;

  factory Holding.fromJson(Map<String, dynamic> j) => Holding(
    symbol: j['symbol'] as String,
    qty: _d(j['qty']),
    avgPrice: _d(j['avg_price']),
    price: _d(j['price']),
    value: _d(j['value']),
    gain: _d(j['gain']),
    gainPct: _d(j['gain_pct']),
    stop: _dn(j['stop']),
    sector: j['sector'] as String? ?? 'Unknown',
    whyBought: _sl(j['why_bought']),
  );
}

class Trade {
  Trade({
    required this.ts,
    required this.symbol,
    required this.side,
    required this.qty,
    required this.price,
    required this.reasons,
  });

  final DateTime ts;
  final String symbol, side;
  final double qty, price;
  final List<String> reasons;

  bool get isBuy => side == 'buy';

  factory Trade.fromJson(Map<String, dynamic> j) => Trade(
    ts: DateTime.parse(j['ts'] as String),
    symbol: j['symbol'] as String,
    side: j['side'] as String,
    qty: _d(j['qty']),
    price: _d(j['price']),
    reasons: _sl(j['reasons']),
  );
}

class EquityPoint {
  EquityPoint(this.ts, this.equity);
  final DateTime ts;
  final double equity;
  factory EquityPoint.fromJson(Map<String, dynamic> j) =>
      EquityPoint(DateTime.parse(j['ts'] as String), _d(j['equity']));
}

class ActivityEvent {
  ActivityEvent({required this.ts, required this.kind, required this.message});
  final DateTime ts;
  final String kind, message;
  factory ActivityEvent.fromJson(Map<String, dynamic> j) => ActivityEvent(
    ts: DateTime.parse(j['ts'] as String),
    kind: j['kind'] as String,
    message: j['message'] as String,
  );
}

/// What the bot is doing right now, for the "What the bot is doing" section.
class LiveStatus {
  LiveStatus({
    required this.now,
    required this.since,
    this.nextCheck,
    required this.working,
    required this.steps,
    this.lastCycle,
  });

  final String now;
  final DateTime since;
  final DateTime? nextCheck;
  final bool working; // a check is running at this moment
  final List<BotStep> steps; // newest first
  final LastCycle? lastCycle;

  factory LiveStatus.fromJson(Map<String, dynamic> j) => LiveStatus(
    now: j['now'] as String,
    since: DateTime.parse(j['since'] as String),
    nextCheck: j['next_check'] == null
        ? null
        : DateTime.parse(j['next_check'] as String),
    working: j['working'] as bool? ?? false,
    steps: [
      for (final x in (j['steps'] as List? ?? const []))
        BotStep(DateTime.parse(x['ts'] as String), x['text'] as String),
    ],
    lastCycle: j['last_cycle'] == null
        ? null
        : LastCycle.fromJson(j['last_cycle'] as Map<String, dynamic>),
  );
}

class BotStep {
  BotStep(this.ts, this.text);
  final DateTime ts;
  final String text;
}

/// Results of the bot's most recent check.
class LastCycle {
  LastCycle({
    required this.ts,
    required this.summary,
    required this.checked,
    required this.skipped,
    required this.skippedTotal,
  });

  final DateTime ts;
  final String summary;
  final int checked;
  final List<String> skipped; // "SYMBOL: why it wasn't bought"
  final int skippedTotal;

  factory LastCycle.fromJson(Map<String, dynamic> j) => LastCycle(
    ts: DateTime.parse(j['ts'] as String),
    summary: j['summary'] as String,
    checked: (j['checked'] as num?)?.toInt() ?? 0,
    skipped: [for (final x in (j['skipped'] as List? ?? const [])) '$x'],
    skippedTotal: (j['skipped_total'] as num?)?.toInt() ?? 0,
  );
}

class AnalystView {
  AnalystView({
    required this.day,
    required this.symbol,
    required this.price,
    required this.action,
    required this.confidence,
    required this.reasoning,
    this.model,
  });
  final String day, symbol, action, reasoning;
  final double price, confidence;
  final String? model; // which Claude model reviewed it
  bool get isBuy => action == 'buy';
  bool get isQuick => model?.contains('haiku') ?? false;
  factory AnalystView.fromJson(Map<String, dynamic> j) => AnalystView(
    day: j['day'] as String,
    symbol: j['symbol'] as String,
    price: _d(j['price']),
    action: j['action'] as String,
    confidence: _d(j['confidence']),
    reasoning: j['reasoning'] as String,
    model: j['model'] as String?,
  );
}

class AnalystFeed {
  AnalystFeed({
    required this.views,
    required this.total,
    required this.approved,
    this.quick = 0,
  });
  final List<AnalystView> views;
  final int total, approved;
  final int quick; // reviews the fast, cheaper model settled on its own
  factory AnalystFeed.fromJson(Map<String, dynamic> j) => AnalystFeed(
    views: [for (final v in j['views'] as List) AnalystView.fromJson(v)],
    total: (j['total'] as num).toInt(),
    approved: (j['approved'] as num).toInt(),
    quick: (j['quick'] as num?)?.toInt() ?? 0,
  );
}

class Approval {
  Approval({
    required this.id,
    required this.ts,
    required this.symbol,
    required this.side,
    required this.qty,
    this.limitPrice,
    required this.reasons,
  });
  final int id;
  final DateTime ts;
  final String symbol, side;
  final double qty;
  final double? limitPrice;
  final List<String> reasons;
  factory Approval.fromJson(Map<String, dynamic> j) => Approval(
    id: (j['id'] as num).toInt(),
    ts: DateTime.parse(j['ts'] as String),
    symbol: j['symbol'] as String,
    side: j['side'] as String,
    qty: _d(j['qty']),
    limitPrice: _dn(j['limit_price']),
    reasons: _sl(j['reasons']),
  );
}

class BotSettings {
  BotSettings({
    this.budget,
    required this.approveMode,
    required this.alwaysWatch,
    required this.neverBuy,
    required this.maxPositionPct,
    required this.maxOpenPositions,
    required this.dailyLossPct,
    required this.cashReservePct,
    this.afterHoursBuys = false,
    this.overnightBuys = false,
    this.quickReviews = false,
  });
  final double? budget;
  final bool quickReviews; // a fast, cheaper model reviews first; close calls get the full analyst
  final bool approveMode;
  final bool afterHoursBuys; // new buys in pre-market and after hours
  final bool
  overnightBuys; // new buys 8pm-4am New York time (Test only for now)
  final List<String> alwaysWatch, neverBuy;
  final double maxPositionPct, dailyLossPct, cashReservePct;
  final int maxOpenPositions;
  factory BotSettings.fromJson(Map<String, dynamic> j) => BotSettings(
    budget: _dn(j['budget']),
    approveMode: j['approve_mode'] as bool? ?? true,
    alwaysWatch: _sl(j['always_watch']),
    neverBuy: _sl(j['never_buy']),
    maxPositionPct: _d(j['max_position_pct']),
    maxOpenPositions: (j['max_open_positions'] as num?)?.toInt() ?? 15,
    dailyLossPct: _d(j['daily_loss_pct']),
    cashReservePct: _d(j['cash_reserve_pct']),
    afterHoursBuys: j['after_hours_buys'] as bool? ?? false,
    overnightBuys: j['overnight_buys'] as bool? ?? false,
    quickReviews: j['quick_reviews'] as bool? ?? false,
  );
}

class WeeklyReport {
  WeeklyReport({
    required this.since,
    required this.changePct,
    required this.buys,
    required this.sells,
    required this.analystReviews,
    required this.analystApproved,
  });
  final String since;
  final double changePct;
  final int buys, sells, analystReviews, analystApproved;
  factory WeeklyReport.fromJson(Map<String, dynamic> j) => WeeklyReport(
    since: j['since'] as String,
    changePct: _d(j['change_pct']),
    buys: (j['buys'] as num).toInt(),
    sells: (j['sells'] as num).toInt(),
    analystReviews: (j['analyst_reviews'] as num).toInt(),
    analystApproved: (j['analyst_approved'] as num).toInt(),
  );
}

class PriceBar {
  PriceBar(this.t, this.o, this.h, this.l, this.c, this.v);
  final DateTime t;
  final double o, h, l, c, v;
  factory PriceBar.fromJson(Map<String, dynamic> j) => PriceBar(
    DateTime.parse(j['t'] as String),
    _d(j['o']),
    _d(j['h']),
    _d(j['l']),
    _d(j['c']),
    _d(j['v']),
  );
}

/// One version of what the bot learned from its finished trades.
class LearnedMemory {
  LearnedMemory({
    required this.ts,
    required this.lessons,
    required this.trades,
    required this.wins,
    required this.weights,
    this.lessonsError,
    this.notebook = const [],
    this.reviews = const [],
    this.calibration,
    this.exam,
    this.practiceTrades = 0,
  });
  final DateTime ts;
  final List<String> lessons;

  /// Lessons with the trades for and against each; the bot decides which are proven.
  final List<NotebookEntry> notebook;

  /// The analyst's own reviews of its finished trades, newest first.
  final List<PostMortem> reviews;

  /// How sure it said it was against how often it was right.
  final Calibration? calibration;

  /// Old lessons vs new on past setups, the last time the notebook changed.
  final ExamResult? exam;
  final int practiceTrades;

  /// Why new lessons couldn't be written last time (e.g. the analyst was out of credits).
  final String? lessonsError;
  final int trades, wins;

  /// How much each idea source counts when ranking buys (1.0 = unchanged).
  final List<({String label, double weight})> weights;

  factory LearnedMemory.fromJson(Map<String, dynamic> j) => LearnedMemory(
    ts: DateTime.parse(j['ts'] as String),
    lessons: [for (final l in j['lessons'] as List) l.toString()],
    lessonsError: j['lessons_error'] as String?,
    trades: (j['trades'] as num).toInt(),
    wins: (j['wins'] as num).toInt(),
    weights: [
      for (final w in j['weights'] as List)
        (label: w['label'] as String, weight: (w['weight'] as num).toDouble()),
    ],
    notebook: [
      for (final e in (j['notebook'] as List? ?? []))
        NotebookEntry.fromJson(e as Map<String, dynamic>),
    ],
    reviews: [
      for (final r in (j['reviews'] as List? ?? []))
        PostMortem.fromJson(r as Map<String, dynamic>),
    ],
    calibration: j['calibration'] == null
        ? null
        : Calibration.fromJson(j['calibration'] as Map<String, dynamic>),
    exam: j['exam'] == null
        ? null
        : ExamResult.fromJson(j['exam'] as Map<String, dynamic>),
    practiceTrades: (j['practice_trades'] as num?)?.toInt() ?? 0,
  );
}

class NotebookEntry {
  NotebookEntry({
    required this.text,
    required this.status,
    required this.support,
    required this.against,
    this.avgReturn,
  });
  final String text;
  final String status; // active, candidate or retired
  final int support, against;

  /// What the trades behind it made on average (null when unknown).
  final double? avgReturn;
  factory NotebookEntry.fromJson(Map<String, dynamic> j) => NotebookEntry(
    text: j['text'] as String,
    status: j['status'] as String? ?? 'candidate',
    support: (j['support'] as List? ?? []).length,
    against: (j['against'] as List? ?? []).length,
    avgReturn: _dn((j['money'] as Map?)?['avg_return']),
  );
}

class PostMortem {
  PostMortem({
    required this.trade,
    required this.expected,
    required this.happened,
    required this.cause,
    required this.lesson,
  });
  final String trade, expected, happened, cause, lesson;
  factory PostMortem.fromJson(Map<String, dynamic> j) => PostMortem(
    trade: j['trade'] as String? ?? '',
    expected: j['expected'] as String? ?? '',
    happened: j['happened'] as String? ?? '',
    cause: j['cause'] as String? ?? 'luck',
    lesson: j['lesson'] as String? ?? '',
  );
}

class Calibration {
  Calibration({
    required this.calls,
    this.said,
    this.right,
    required this.shift,
    required this.bins,
    required this.note,
  });
  final int calls;
  final double? said, right;
  final double shift; // added to its confidence before the bar
  final List<({String label, int calls, double said, double right})> bins;
  final String note;
  factory Calibration.fromJson(Map<String, dynamic> j) => Calibration(
    calls: (j['calls'] as num?)?.toInt() ?? 0,
    said: _dn(j['said']),
    right: _dn(j['right']),
    shift: _d(j['shift']),
    bins: [
      for (final b in (j['bins'] as List? ?? []))
        (
          label: b['label'] as String,
          calls: (b['calls'] as num).toInt(),
          said: _d(b['said']),
          right: _d(b['right']),
        ),
    ],
    note: j['note'] as String? ?? '',
  );
}

class ExamResult {
  ExamResult({
    required this.cases,
    required this.oldScore,
    required this.newScore,
    required this.passed,
    this.oldMoney,
    this.newMoney,
  });
  final int cases;
  // Lower is better (0 perfect, 0.25 a coin flip).
  final double oldScore, newScore;
  final bool passed;

  /// Total return of the past trades it would have let through, before and after.
  final double? oldMoney, newMoney;
  factory ExamResult.fromJson(Map<String, dynamic> j) => ExamResult(
    cases: (j['cases'] as num?)?.toInt() ?? 0,
    oldScore: _d(j['old_score']),
    newScore: _d(j['new_score']),
    passed: j['passed'] as bool? ?? true,
    oldMoney: _dn(j['old_money']),
    newMoney: _dn(j['new_money']),
  );
}

/// The "What the bot learned" card: the memory in use and, in Live, a new one waiting for your OK.
class Learning {
  Learning({
    this.active,
    this.proposed,
    required this.finishedTrades,
    required this.needs,
    required this.everyDays,
    this.scorecard,
  });
  final LearnedMemory? active, proposed;
  final int finishedTrades, needs, everyDays;

  /// How the analyst's approved and blocked stocks did a month later (null until graded).
  final Scorecard? scorecard;

  factory Learning.fromJson(Map<String, dynamic> j) => Learning(
    active: j['active'] == null ? null : LearnedMemory.fromJson(j['active']),
    proposed: j['proposed'] == null
        ? null
        : LearnedMemory.fromJson(j['proposed']),
    finishedTrades: (j['finished_trades'] as num).toInt(),
    needs: (j['needs'] as num).toInt(),
    everyDays: (j['every_days'] as num).toInt(),
    scorecard: j['scorecard'] == null
        ? null
        : Scorecard.fromJson(j['scorecard'] as Map<String, dynamic>),
  );
}

/// One side of the scorecard: the stocks the analyst approved, or the ones it blocked.
class ScoreSide {
  ScoreSide({required this.calls, this.avgReturn, this.avgVsMarket});
  final int calls;
  final double? avgReturn, avgVsMarket;
  factory ScoreSide.fromJson(Map<String, dynamic> j) => ScoreSide(
    calls: (j['calls'] as num).toInt(),
    avgReturn: (j['avg_return'] as num?)?.toDouble(),
    avgVsMarket: (j['avg_vs_market'] as num?)?.toDouble(),
  );
}

/// A graded call: a stock and how it did against the market over the next month.
typedef GradedCall = ({String symbol, String day, double vsMarket});

/// Were the analyst's calls right? Graded about a month after each review.
class Scorecard {
  Scorecard({
    required this.approved,
    required this.blocked,
    required this.waiting,
    required this.verdict,
    required this.horizonDays,
    this.missed = const [],
    this.letThrough = const [],
  });
  final ScoreSide approved, blocked;
  final int waiting, horizonDays;
  final String verdict;
  final List<GradedCall> missed, letThrough;

  bool get hasVerdict =>
      approved.avgVsMarket != null && blocked.avgVsMarket != null;

  static List<GradedCall> _calls(Object? raw) => [
    for (final c in (raw as List?) ?? const [])
      (
        symbol: c['symbol'] as String,
        day: c['day'] as String,
        vsMarket: (c['vs_market'] as num).toDouble(),
      ),
  ];

  factory Scorecard.fromJson(Map<String, dynamic> j) => Scorecard(
    approved: ScoreSide.fromJson(j['approved'] as Map<String, dynamic>),
    blocked: ScoreSide.fromJson(j['blocked'] as Map<String, dynamic>),
    waiting: (j['waiting'] as num?)?.toInt() ?? 0,
    verdict: j['verdict'] as String,
    horizonDays: (j['horizon_days'] as num?)?.toInt() ?? 20,
    missed: _calls(j['missed']),
    letThrough: _calls(j['let_through']),
  );
}

/// One rule the strategy lab changed from the standard rules.
class LabChange {
  LabChange({required this.label, required this.standard, required this.now});
  final String label;
  final double standard, now;
  factory LabChange.fromJson(Map<String, dynamic> j) => LabChange(
    label: j['label'] as String,
    standard: _d(j['standard']),
    now: _d(j['now']),
  );
}

/// One weekly experiment: the idea, whether it was kept, and why.
class LabExperiment {
  LabExperiment({
    required this.ts,
    required this.change,
    required this.kept,
    required this.verdict,
    required this.evidence,
    this.unseenCurrent,
    this.unseenNew,
  });
  final DateTime ts;
  final String change, verdict;
  final bool kept;
  final List<String> evidence;

  /// Results on the newer prices it wasn't tuned on: return, sharpe, max_drop.
  final Map<String, dynamic>? unseenCurrent, unseenNew;

  factory LabExperiment.fromJson(Map<String, dynamic> j) {
    final unseen = j['unseen'] as Map<String, dynamic>?;
    return LabExperiment(
      ts: DateTime.parse(j['ts'] as String),
      change: j['change'] as String,
      kept: j['kept'] as bool? ?? false,
      verdict: j['verdict'] as String? ?? '',
      evidence: [for (final e in (j['evidence'] as List?) ?? []) e.toString()],
      unseenCurrent: unseen?['current'] as Map<String, dynamic>?,
      unseenNew: unseen?['new'] as Map<String, dynamic>?,
    );
  }
}

class LabReport {
  LabReport({
    required this.enabled,
    required this.running,
    required this.changes,
    this.proposed,
    this.proposedEntry,
    required this.experiments,
    required this.everyDays,
    required this.limitsNote,
  });
  final bool enabled, running;
  final List<LabChange> changes;
  final List<LabChange>? proposed;
  final LabExperiment? proposedEntry;
  final List<LabExperiment> experiments;
  final int everyDays;
  final String limitsNote;

  factory LabReport.fromJson(Map<String, dynamic> j) {
    final p = j['proposed'] as Map<String, dynamic>?;
    final entry = p?['entry'] as Map<String, dynamic>?;
    return LabReport(
      enabled: j['enabled'] as bool? ?? false,
      running: j['running'] as bool? ?? false,
      changes: [
        for (final c in (j['changes'] as List?) ?? []) LabChange.fromJson(c),
      ],
      proposed: p == null
          ? null
          : [for (final c in p['changes'] as List) LabChange.fromJson(c)],
      proposedEntry: entry != null && entry['ts'] != null
          ? LabExperiment.fromJson(entry)
          : null,
      experiments: [
        for (final e in (j['experiments'] as List?) ?? [])
          LabExperiment.fromJson(e),
      ],
      everyDays: (j['every_days'] as num?)?.toInt() ?? 7,
      limitsNote: j['limits_note'] as String? ?? '',
    );
  }
}

/// What the AI analyst has cost this month and the cap.
class AiSpending {
  AiSpending({required this.enabled, required this.spent, this.cap});
  final bool enabled;
  final double spent;
  final double? cap;
  factory AiSpending.fromJson(Map<String, dynamic> j) => AiSpending(
    enabled: j['enabled'] as bool? ?? false,
    spent: _d(j['spent']),
    cap: _dn(j['cap']),
  );
}

/// One safety rule you can change, with the range the bot allows.
class SafetyRule {
  SafetyRule({
    required this.key,
    required this.label,
    required this.help,
    required this.unit,
    required this.low,
    required this.high,
    required this.step,
    required this.value,
    required this.recommended,
    double? ifReset,
    required this.looserWhenHigher,
    required this.setBy,
    this.pendingValue,
    this.pendingFrom,
  }) : ifReset = ifReset ?? recommended;
  final String key, label, help;
  final String unit; // percent, count or times
  final double low, high, step, value, recommended;
  // What "Use recommended" for every rule gives: the default, or the lab's value if it has one.
  final double ifReset;
  final bool looserWhenHigher; // which way takes more risk
  final String setBy; // you, lab or recommended
  final double? pendingValue; // a Live change waiting for the market open
  final DateTime? pendingFrom;

  bool looser(double from, double to) =>
      looserWhenHigher ? to > from + 1e-9 : to < from - 1e-9;

  String show(double v) => switch (unit) {
    'percent' => '${_trim(v * 100)}%',
    'count' => v.round().toString(),
    _ => '${_trim(v)}x',
  };

  static String _trim(double v) {
    final s = v.toStringAsFixed(2);
    return s.replaceAll(RegExp(r'\.?0+$'), '');
  }

  SafetyRule withValue(double v, {String? setBy}) => SafetyRule(
    key: key,
    label: label,
    help: help,
    unit: unit,
    low: low,
    high: high,
    step: step,
    value: v,
    recommended: recommended,
    ifReset: ifReset,
    looserWhenHigher: looserWhenHigher,
    setBy: setBy ?? this.setBy,
    pendingValue: pendingValue,
    pendingFrom: pendingFrom,
  );

  factory SafetyRule.fromJson(Map<String, dynamic> j) {
    final p = j['pending'] as Map<String, dynamic>?;
    return SafetyRule(
      key: j['key'] as String,
      label: j['label'] as String? ?? j['key'] as String,
      help: j['help'] as String? ?? '',
      unit: j['unit'] as String? ?? 'percent',
      low: _d(j['low']),
      high: _d(j['high']),
      step: _d(j['step']),
      value: _d(j['value']),
      recommended: _d(j['recommended']),
      ifReset: j['if_reset'] == null ? null : _d(j['if_reset']),
      looserWhenHigher: (j['looser_when'] as String? ?? 'higher') == 'higher',
      setBy: j['set_by'] as String? ?? 'recommended',
      pendingValue: p == null ? null : _dn(p['value']),
      pendingFrom: p == null
          ? null
          : DateTime.tryParse(p['from'] as String? ?? ''),
    );
  }
}

class RulesReport {
  RulesReport({
    required this.rules,
    required this.starterLock,
    required this.note,
    this.waiting = const [],
  });
  final List<SafetyRule> rules;
  final bool starterLock; // Live's first 30 days: only safer changes
  final String note;
  final List<String> waiting; // changes just saved that start at the next open
  factory RulesReport.fromJson(Map<String, dynamic> j) => RulesReport(
    rules: [
      for (final r in (j['rules'] as List? ?? []))
        SafetyRule.fromJson(r as Map<String, dynamic>),
    ],
    starterLock: j['starter_lock'] as bool? ?? false,
    note: j['note'] as String? ?? '',
    waiting: _sl(j['waiting']),
  );
}
