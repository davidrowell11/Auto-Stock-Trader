import 'dart:math';

import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';

import '../api.dart';
import '../models.dart';
import '../state.dart';
import '../theme.dart';
import '../widgets.dart';

/// One holding: candlestick chart with buy/sell markers and the bot's reasons.
class StockDetail extends StatefulWidget {
  const StockDetail({super.key, required this.holding, required this.mode});
  final Holding holding;
  final String mode;

  @override
  State<StockDetail> createState() => _StockDetailState();
}

class _StockDetailState extends State<StockDetail> {
  List<PriceBar> bars = [];
  List<Trade> trades = [];
  String? error;
  int days = 90;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => load());
  }

  Future<void> load() async {
    final api = AppScope.of(context).api!;
    try {
      final b = await api.bars(
        widget.holding.symbol,
        days: days,
        mode: widget.mode,
      );
      final t = (await api.trades(widget.mode))
          .where((x) => x.symbol == widget.holding.symbol)
          .toList();
      if (mounted) {
        setState(() {
          bars = b;
          trades = t;
          error = null;
        });
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final h = widget.holding;
    final t = Theme.of(context).textTheme;
    return Scaffold(
      appBar: AppBar(title: Text(h.symbol)),
      body: PageFrame(
        onRefresh: load,
        children: [
          GlassCard(
            glow: AppColors.change(h.gain),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Text(
                      money.format(h.price),
                      style: t.headlineMedium?.copyWith(
                        fontWeight: FontWeight.w800,
                      ),
                    ),
                    const Spacer(),
                    ModeBadge(widget.mode, demo: AppScope.of(context).demo),
                  ],
                ),
                const SizedBox(height: 4),
                ChangeText(amount: h.gain, percent: h.gainPct),
                const SizedBox(height: 4),
                Text(
                  'since the bot bought at ${money.format(h.avgPrice)}',
                  style: t.bodySmall?.copyWith(color: AppColors.muted),
                ),
                const SizedBox(height: 16),
                SegmentedButton<int>(
                  segments: const [
                    ButtonSegment(value: 30, label: Text('1M')),
                    ButtonSegment(value: 90, label: Text('3M')),
                    ButtonSegment(value: 180, label: Text('6M')),
                    ButtonSegment(value: 365, label: Text('1Y')),
                  ],
                  selected: {days},
                  onSelectionChanged: (s) {
                    setState(() => days = s.first);
                    load();
                  },
                ),
                const SizedBox(height: 14),
                if (error != null)
                  Text(error!, style: TextStyle(color: AppColors.loss)),
                if (bars.length > 1)
                  Semantics(
                    label:
                        '${widget.holding.symbol} price chart, last $days days: '
                        '\$${bars.first.c.toStringAsFixed(2)} to '
                        '\$${bars.last.c.toStringAsFixed(2)}',
                    excludeSemantics: true,
                    child: SizedBox(height: 260, child: _candles()),
                  ),
                if (bars.length <= 1 && error == null)
                  const SizedBox(
                    height: 260,
                    child: Center(child: CircularProgressIndicator()),
                  ),
                const SizedBox(height: 10),
                Wrap(
                  spacing: 14,
                  runSpacing: 6,
                  children: [
                    _key(AppColors.gain, 'Bot bought'),
                    _key(AppColors.loss, 'Bot sold'),
                    if (h.stop != null)
                      _key(AppColors.warn, 'Stop ${money.format(h.stop)}'),
                  ],
                ),
              ],
            ),
          ),
          const SectionTitle('Position'),
          GlassCard(
            child: Column(
              children: [
                _row('Shares', shares(h.qty)),
                _row('Value', money.format(h.value)),
                _row('Average cost', money.format(h.avgPrice)),
                _row(
                  'Stop-loss (moves up as price rises)',
                  h.stop == null ? '—' : money.format(h.stop),
                ),
                _row('Sector', h.sector),
              ],
            ),
          ),
          const SectionTitle('Why the bot bought it'),
          GlassCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                if (h.whyBought.isEmpty)
                  const Text('No notes saved for this trade.'),
                for (final r in h.whyBought)
                  Padding(
                    padding: const EdgeInsets.only(bottom: 10),
                    child: Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Icon(
                          r.startsWith('Analyst')
                              ? Icons.auto_awesome_rounded
                              : Icons.check_circle_outline_rounded,
                          size: 18,
                          color: r.startsWith('Analyst')
                              ? AppColors.accent
                              : AppColors.gain,
                        ),
                        const SizedBox(width: 10),
                        Expanded(child: Text(r)),
                      ],
                    ),
                  ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _key(Color c, String label) => Row(
    mainAxisSize: MainAxisSize.min,
    children: [
      Container(
        width: 10,
        height: 10,
        decoration: BoxDecoration(
          color: c,
          borderRadius: BorderRadius.circular(3),
        ),
      ),
      const SizedBox(width: 6),
      Text(label, style: Theme.of(context).textTheme.bodySmall),
    ],
  );

  Widget _row(String k, String v) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 6),
    child: Row(
      children: [
        Expanded(
          child: Text(k, style: TextStyle(color: AppColors.muted)),
        ),
        Text(v, style: const TextStyle(fontWeight: FontWeight.w700)),
      ],
    ),
  );

  Widget _candles() {
    final lo = bars.map((b) => b.l).reduce(min),
        hi = bars.map((b) => b.h).reduce(max);
    final pad = (hi - lo) * 0.08;
    DateTime day(DateTime t) {
      // Calendar day in UTC. Trades happen in US market hours, when the UTC
      // date and the New York date are the same, whatever the phone's time zone.
      final u = t.toUtc();
      return DateTime.utc(u.year, u.month, u.day);
    }

    double xOf(DateTime d) {
      // Index of the first bar on or after the trade day.
      final i = bars.indexWhere((b) => !day(b.t).isBefore(day(d)));
      return (i < 0 ? bars.length - 1 : i).toDouble();
    }

    final marks = trades.where((tr) => !tr.ts.isBefore(bars.first.t)).toList();
    return Stack(
      children: [
        CandlestickChart(
          CandlestickChartData(
            minY: lo - pad,
            maxY: hi + pad,
            minX: -1,
            maxX: bars.length.toDouble(),
            gridData: FlGridData(
              drawVerticalLine: false,
              getDrawingHorizontalLine: (_) => FlLine(
                color: Colors.grey.withValues(alpha: 0.12),
                strokeWidth: 1,
              ),
            ),
            borderData: FlBorderData(show: false),
            titlesData: FlTitlesData(
              topTitles: const AxisTitles(),
              bottomTitles: const AxisTitles(),
              leftTitles: const AxisTitles(),
              rightTitles: AxisTitles(
                sideTitles: SideTitles(
                  showTitles: true,
                  reservedSize: 52,
                  getTitlesWidget: (v, meta) => (v == meta.min || v == meta.max)
                      ? const SizedBox.shrink()
                      : SideTitleWidget(
                          meta: meta,
                          child: Text(
                            v.toStringAsFixed(v < 10 ? 2 : 0),
                            style: TextStyle(
                              fontSize: 10,
                              color: AppColors.muted,
                            ),
                          ),
                        ),
                ),
              ),
            ),
            rangeAnnotations: RangeAnnotations(
              horizontalRangeAnnotations: [
                if (widget.holding.stop != null)
                  HorizontalRangeAnnotation(
                    y1: widget.holding.stop! - (hi - lo) * 0.002,
                    y2: widget.holding.stop! + (hi - lo) * 0.002,
                    color: AppColors.warn.withValues(alpha: 0.8),
                  ),
              ],
              verticalRangeAnnotations: [
                for (final m in marks)
                  VerticalRangeAnnotation(
                    x1: xOf(m.ts) - 0.5,
                    x2: xOf(m.ts) + 0.5,
                    color: (m.isBuy ? AppColors.gain : AppColors.loss)
                        .withValues(alpha: 0.18),
                  ),
              ],
            ),
            candlestickSpots: [
              for (var i = 0; i < bars.length; i++)
                CandlestickSpot(
                  x: i.toDouble(),
                  open: bars[i].o,
                  high: bars[i].h,
                  low: bars[i].l,
                  close: bars[i].c,
                ),
            ],
            candlestickPainter: DefaultCandlestickPainter(
              candlestickStyleProvider: (spot, i) {
                final c = spot.close >= spot.open
                    ? AppColors.gain
                    : AppColors.loss;
                return CandlestickStyle(
                  lineColor: c,
                  lineWidth: 1,
                  bodyStrokeColor: c,
                  bodyStrokeWidth: 0,
                  bodyFillColor: c,
                  bodyWidth: max(2, 260 / bars.length),
                  bodyRadius: 1,
                );
              },
            ),
          ),
        ),
      ],
    );
  }
}
