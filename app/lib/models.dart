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
    this.positions,
    this.marketOpen,
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
  final int? positions;
  final bool? marketOpen;

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
    positions: (j['positions'] as num?)?.toInt(),
    marketOpen: j['market_open'] as bool?,
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

class AnalystView {
  AnalystView({
    required this.day,
    required this.symbol,
    required this.price,
    required this.action,
    required this.confidence,
    required this.reasoning,
  });
  final String day, symbol, action, reasoning;
  final double price, confidence;
  bool get isBuy => action == 'buy';
  factory AnalystView.fromJson(Map<String, dynamic> j) => AnalystView(
    day: j['day'] as String,
    symbol: j['symbol'] as String,
    price: _d(j['price']),
    action: j['action'] as String,
    confidence: _d(j['confidence']),
    reasoning: j['reasoning'] as String,
  );
}

class AnalystFeed {
  AnalystFeed({
    required this.views,
    required this.total,
    required this.approved,
  });
  final List<AnalystView> views;
  final int total, approved;
  factory AnalystFeed.fromJson(Map<String, dynamic> j) => AnalystFeed(
    views: [for (final v in j['views'] as List) AnalystView.fromJson(v)],
    total: (j['total'] as num).toInt(),
    approved: (j['approved'] as num).toInt(),
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
  });
  final double? budget;
  final bool approveMode;
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
