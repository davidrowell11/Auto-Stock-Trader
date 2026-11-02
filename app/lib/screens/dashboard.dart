import 'dart:async';

import 'package:flutter/material.dart';

import '../api.dart';
import '../models.dart';
import '../state.dart';
import '../theme.dart';
import '../widgets.dart';
import 'stock_detail.dart';

/// The Test tab and the Live section. Same bot, separate money and history.
class Dashboard extends StatefulWidget {
  const Dashboard({super.key, required this.mode});
  final String mode;

  @override
  State<Dashboard> createState() => _DashboardState();
}

class _DashboardState extends State<Dashboard> {
  Summary? summary;
  List<Holding> holdings = [];
  List<EquityPoint> curve = [];
  List<EquityPoint> bench = [];
  List<ActivityEvent> events = [];
  List<Approval> approvals = [];
  WeeklyReport? weekly;
  String? error;
  double? budgetDraft;
  Timer? timer;

  bool get live => widget.mode == 'live';

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => refresh());
    timer = Timer.periodic(const Duration(seconds: 30), (_) => refresh());
  }

  @override
  void dispose() {
    timer?.cancel();
    super.dispose();
  }

  TraderApi get api => AppScope.of(context).api!;

  Future<void> refresh() async {
    if (!mounted) return;
    try {
      final m = widget.mode;
      final results = await Future.wait([
        api.summary(m),
        api.positions(m),
        api.equity(m),
        api.events(m),
        api.approvals(m),
        api.weekly(m),
      ]);
      final c = results[2] as List<EquityPoint>;
      List<EquityPoint> b = [];
      if (c.length > 1) {
        // Benchmark: what the same starting money would be worth in SPY.
        try {
          final days = DateTime.now().difference(c.first.ts).inDays + 5;
          final spy = await api.bars('SPY', days: days.clamp(5, 1000), mode: m);
          final start = spy.where((x) => !x.t.isBefore(c.first.ts)).toList();
          if (start.length > 1) {
            final scale = c.first.equity / start.first.c;
            b = [for (final x in start) EquityPoint(x.t, x.c * scale)];
          }
        } catch (_) {}
      }
      if (!mounted) return;
      setState(() {
        summary = results[0] as Summary;
        holdings = results[1] as List<Holding>;
        curve = c;
        bench = b;
        events = results[3] as List<ActivityEvent>;
        approvals = results[4] as List<Approval>;
        weekly = results[5] as WeeklyReport;
        error = null;
      });
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.message);
    } catch (e) {
      // e.g. the server sent something the app doesn't understand
      if (mounted) {
        setState(() => error = 'Unexpected reply from the bot server');
      }
    }
  }

  Future<void> _run(Future<void> Function() action, String done) async {
    try {
      await action();
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(done)));
      }
    } on ApiException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(e.message)));
      }
    }
    await refresh();
  }

  @override
  Widget build(BuildContext context) {
    final s = summary;
    final demo = AppScope.of(context).demo;
    return PageFrame(
      onRefresh: refresh,
      children: [
        if (error != null) ...[
          ErrorBox(error!, onRetry: refresh),
          const SizedBox(height: 12),
        ],
        if (s == null && error == null)
          const Padding(
            padding: EdgeInsets.all(60),
            child: Center(child: CircularProgressIndicator()),
          ),
        if (s != null) ...[
          _header(s, demo),
          if (live && approvals.isNotEmpty) ...[
            SectionTitle(
              'Waiting for your OK',
              trailing: Pill('${approvals.length}', color: AppColors.live),
            ),
            for (final a in approvals) _approvalCard(a),
          ],
          if (!live) _budgetCard(s),
          if (weekly != null) _weeklyCard(weekly!),
          SectionTitle(
            'Holdings',
            trailing: Text(
              '${holdings.length} stocks',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
          if (holdings.isEmpty)
            const GlassCard(
              child: Text(
                'Nothing held yet. The bot buys when it finds a strong, steady uptrend that passes every safety check.',
              ),
            ),
          for (final h in holdings)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: _holdingTile(h),
            ),
          const SectionTitle('Activity'),
          GlassCard(
            padding: const EdgeInsets.symmetric(vertical: 6),
            child: Column(
              children: [
                if (events.isEmpty)
                  const ListTile(title: Text('No activity yet')),
                for (final e in events.take(15)) _eventTile(e),
              ],
            ),
          ),
        ],
      ],
    );
  }

  Widget _header(Summary s, bool demo) {
    final t = Theme.of(context).textTheme;
    final status = s.error != null
        ? ('Needs attention', AppColors.loss, Icons.error_outline_rounded)
        : s.halted != null
        ? (
            'Paused by safety rule',
            AppColors.warn,
            Icons.pause_circle_outline_rounded,
          )
        : s.running
        ? (
            s.marketOpen == false ? 'Running · market closed' : 'Running',
            AppColors.gain,
            Icons.bolt_rounded,
          )
        : ('Stopped', AppColors.muted, Icons.stop_circle_outlined);
    return GlassCard(
      glow: live ? AppColors.live : AppColors.accent,
      padding: const EdgeInsets.fromLTRB(20, 18, 20, 12),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              ModeBadge(widget.mode, demo: demo),
              const Spacer(),
              Pill(status.$1, color: status.$2, icon: status.$3),
            ],
          ),
          const SizedBox(height: 18),
          Text(
            live ? 'Real account value' : 'Test account value',
            style: t.bodyMedium?.copyWith(color: AppColors.muted),
          ),
          const SizedBox(height: 4),
          Text(
            s.equity == null ? '—' : money.format(s.equity),
            style: t.displaySmall?.copyWith(
              fontWeight: FontWeight.w800,
              letterSpacing: -1,
            ),
          ),
          const SizedBox(height: 6),
          if (s.todayChange != null)
            Row(
              children: [
                ChangeText(
                  amount: s.todayChange!,
                  percent: s.todayChangePct ?? 0,
                ),
                const SizedBox(width: 8),
                Text(
                  'today',
                  style: t.bodySmall?.copyWith(color: AppColors.muted),
                ),
              ],
            ),
          const SizedBox(height: 14),
          EquityChart(points: curve, benchmark: bench),
          Row(
            children: [
              _legendDot(
                curve.length > 1 && curve.last.equity >= curve.first.equity
                    ? AppColors.gain
                    : AppColors.loss,
                'Bot',
              ),
              const SizedBox(width: 14),
              if (bench.isNotEmpty)
                _legendDot(AppColors.muted, 'Just holding SPY'),
              const Spacer(),
              if (s.lastCycle != null)
                Text(
                  'checked ${ago(DateTime.parse(s.lastCycle!))}',
                  style: t.bodySmall?.copyWith(color: AppColors.muted),
                ),
            ],
          ),
          if (s.error != null)
            Padding(
              padding: const EdgeInsets.only(top: 10),
              child: Text(s.error!, style: TextStyle(color: AppColors.loss)),
            ),
          if (s.halted != null)
            Padding(
              padding: const EdgeInsets.only(top: 10),
              child: Text(s.halted!, style: TextStyle(color: AppColors.warn)),
            ),
          const SizedBox(height: 12),
          Row(
            children: [
              _stat('Cash', s.cash),
              _stat('Invested', s.invested),
              _stat(
                live ? 'Live cap' : 'Budget',
                s.budget,
                fallback: 'Whole account',
              ),
            ],
          ),
          const SizedBox(height: 12),
          Row(
            children: [
              Expanded(
                child: FilledButton.icon(
                  style: FilledButton.styleFrom(
                    backgroundColor: s.running
                        ? Colors.grey.withValues(alpha: 0.25)
                        : (live ? AppColors.live : AppColors.accent),
                    foregroundColor: s.running
                        ? (isDark(context) ? Colors.white : Colors.black87)
                        : Colors.white,
                    padding: const EdgeInsets.symmetric(vertical: 14),
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(14),
                    ),
                  ),
                  onPressed: () => _run(
                    () => api.setRunning(widget.mode, !s.running),
                    s.running ? 'Bot stopped' : 'Bot started',
                  ),
                  icon: Icon(
                    s.running ? Icons.pause_rounded : Icons.play_arrow_rounded,
                  ),
                  label: Text(
                    s.running ? 'Stop bot' : 'Start bot',
                    style: const TextStyle(fontWeight: FontWeight.w700),
                  ),
                ),
              ),
              const SizedBox(width: 10),
              OutlinedButton.icon(
                style: OutlinedButton.styleFrom(
                  foregroundColor: AppColors.loss,
                  side: BorderSide(color: AppColors.loss),
                  padding: const EdgeInsets.symmetric(
                    vertical: 14,
                    horizontal: 16,
                  ),
                  shape: RoundedRectangleBorder(
                    borderRadius: BorderRadius.circular(14),
                  ),
                ),
                onPressed: _killSwitch,
                icon: const Icon(Icons.power_settings_new_rounded),
                label: const Text('Kill switch'),
              ),
            ],
          ),
        ],
      ),
    );
  }

  Widget _legendDot(Color c, String label) => Row(
    children: [
      Container(
        width: 8,
        height: 8,
        decoration: BoxDecoration(color: c, shape: BoxShape.circle),
      ),
      const SizedBox(width: 6),
      Text(label, style: Theme.of(context).textTheme.bodySmall),
    ],
  );

  Widget _stat(String label, double? v, {String fallback = '—'}) => Expanded(
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: Theme.of(context).textTheme.bodySmall
              ?.copyWith(color: AppColors.muted),
        ),
        const SizedBox(height: 2),
        Text(
          v == null ? fallback : moneyRound.format(v),
          style: const TextStyle(fontWeight: FontWeight.w700),
        ),
      ],
    ),
  );

  Future<void> _killSwitch() async {
    final choice = await showDialog<String>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Kill switch'),
        content: Text(
          live
              ? 'Stop the bot right now. You can also sell every stock it holds in your real account.'
              : 'Stop the Test bot right now, and optionally sell all of its test holdings.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(c, 'stop'),
            child: const Text('Stop only'),
          ),
          FilledButton(
            style: FilledButton.styleFrom(
              backgroundColor: AppColors.loss,
              foregroundColor: AppColors.onColor,
            ),
            onPressed: () => Navigator.pop(c, 'sell'),
            child: const Text('Stop and sell all'),
          ),
        ],
      ),
    );
    if (choice == null) return;
    try {
      final r = await api.kill(widget.mode, sellAll: choice == 'sell');
      final msg = r.failed.isNotEmpty
          ? 'Bot stopped, but it couldn\'t sell ${r.failed.join(', ')}. Check your broker app.'
          : choice == 'sell'
          ? 'Bot stopped and its holdings sold'
          : 'Bot stopped';
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(msg)));
      }
    } on ApiException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(e.message)));
      }
    }
    await refresh();
  }

  Widget _approvalCard(Approval a) => Padding(
    padding: const EdgeInsets.only(bottom: 10),
    child: GlassCard(
      glow: AppColors.live,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(
                'Buy ${a.qty.toStringAsFixed(0)} ${a.symbol}',
                style: Theme.of(context).textTheme.titleMedium
                    ?.copyWith(fontWeight: FontWeight.w800),
              ),
              const Spacer(),
              Text(ago(a.ts), style: Theme.of(context).textTheme.bodySmall),
            ],
          ),
          const SizedBox(height: 8),
          for (final r in a.reasons.take(4))
            Padding(
              padding: const EdgeInsets.only(bottom: 4),
              child: Text('• $r', style: Theme.of(context).textTheme.bodySmall),
            ),
          const SizedBox(height: 8),
          Row(
            children: [
              Expanded(
                child: OutlinedButton(
                  onPressed: () => _run(
                    () => api.decide(widget.mode, a.id, false),
                    'Skipped ${a.symbol}',
                  ),
                  child: const Text('Skip'),
                ),
              ),
              const SizedBox(width: 10),
              Expanded(
                child: FilledButton(
                  style: FilledButton.styleFrom(
                    backgroundColor: AppColors.gain,
                    foregroundColor: Colors.black,
                  ),
                  onPressed: () => _run(
                    () => api.decide(widget.mode, a.id, true),
                    'Bought ${a.symbol}',
                  ),
                  child: const Text(
                    'Approve',
                    style: TextStyle(fontWeight: FontWeight.w700),
                  ),
                ),
              ),
            ],
          ),
        ],
      ),
    ),
  );

  Widget _budgetCard(Summary s) {
    final equity = s.equity ?? 100000;
    final maxBudget = (equity < 1000 ? 1000.0 : equity);
    final value = (budgetDraft ?? s.budget ?? maxBudget).clamp(
      100.0,
      maxBudget,
    );
    return Padding(
      padding: const EdgeInsets.only(top: 14),
      child: GlassCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(Icons.savings_outlined, color: AppColors.test),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    'Test budget',
                    style: Theme.of(context).textTheme.titleMedium
                        ?.copyWith(fontWeight: FontWeight.w700),
                  ),
                ),
                Text(
                  moneyRound.format(value),
                  style: const TextStyle(
                    fontWeight: FontWeight.w800,
                    fontSize: 18,
                  ),
                ),
              ],
            ),
            const SizedBox(height: 4),
            Text(
              'How much fake money the bot may use for this test.',
              style: Theme.of(context).textTheme.bodySmall
                  ?.copyWith(color: AppColors.muted),
            ),
            Slider(
              value: value,
              min: 100,
              max: maxBudget,
              activeColor: AppColors.test,
              onChanged: (v) =>
                  setState(() => budgetDraft = (v / 100).round() * 100.0),
              onChangeEnd: (v) => _run(() async {
                await api.updateSettings('test', {
                  'budget': (v / 100).round() * 100.0,
                });
                budgetDraft = null;
              }, 'Test budget saved'),
            ),
          ],
        ),
      ),
    );
  }

  Widget _weeklyCard(WeeklyReport w) => Padding(
    padding: const EdgeInsets.only(top: 14),
    child: GlassCard(
      child: Row(
        children: [
          Icon(Icons.insights_rounded, color: AppColors.accent),
          const SizedBox(width: 12),
          Expanded(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'This week',
                  style: Theme.of(context).textTheme.titleSmall
                      ?.copyWith(fontWeight: FontWeight.w700),
                ),
                const SizedBox(height: 4),
                Text(
                  '${w.buys} buys · ${w.sells} sells · analyst approved ${w.analystApproved} of ${w.analystReviews}',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
          ),
          Text(
            pct(w.changePct),
            style: TextStyle(
              color: AppColors.change(w.changePct),
              fontWeight: FontWeight.w800,
              fontSize: 16,
            ),
          ),
        ],
      ),
    ),
  );

  Widget _holdingTile(Holding h) => GlassCard(
    padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 14),
    onTap: () => Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => StockDetail(holding: h, mode: widget.mode),
      ),
    ),
    child: Row(
      children: [
        CircleAvatar(
          radius: 21,
          backgroundColor: AppColors.change(h.gain).withValues(alpha: 0.14),
          child: Text(
            h.symbol.substring(0, h.symbol.length.clamp(1, 2)),
            style: TextStyle(
              color: AppColors.change(h.gain),
              fontWeight: FontWeight.w800,
              fontSize: 13,
            ),
          ),
        ),
        const SizedBox(width: 14),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                h.symbol,
                style: const TextStyle(
                  fontWeight: FontWeight.w800,
                  fontSize: 16,
                ),
              ),
              const SizedBox(height: 2),
              Text(
                '${h.qty.toStringAsFixed(h.qty % 1 == 0 ? 0 : 2)} shares · stop ${h.stop == null ? '—' : money.format(h.stop)}',
                style: Theme.of(context).textTheme.bodySmall
                    ?.copyWith(color: AppColors.muted),
              ),
            ],
          ),
        ),
        Column(
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Text(
              money.format(h.value),
              style: const TextStyle(fontWeight: FontWeight.w700),
            ),
            const SizedBox(height: 2),
            Text(
              pct(h.gainPct),
              style: TextStyle(
                color: AppColors.change(h.gain),
                fontWeight: FontWeight.w700,
                fontSize: 13,
              ),
            ),
          ],
        ),
      ],
    ),
  );

  Widget _eventTile(ActivityEvent e) {
    final (icon, color) = switch (e.kind) {
      'buy' => (Icons.south_west_rounded, AppColors.gain),
      'sell' => (Icons.north_east_rounded, AppColors.loss),
      'approval' => (Icons.how_to_reg_rounded, AppColors.live),
      'kill' => (Icons.power_settings_new_rounded, AppColors.loss),
      _ => (Icons.tune_rounded, AppColors.muted),
    };
    return ListTile(
      dense: true,
      leading: Icon(icon, color: color, size: 20),
      title: Text(e.message),
      trailing: Text(ago(e.ts), style: Theme.of(context).textTheme.bodySmall),
    );
  }
}
