import 'dart:async';

import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import '../api.dart';
import '../models.dart';
import '../state.dart';
import '../theme.dart';
import '../widgets.dart';
import 'settings.dart' show parseMoney;
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
  int rangeDays = 0; // 0: all of it

  List<EquityPoint> _since(List<EquityPoint> points) {
    if (rangeDays == 0 || points.isEmpty) return points;
    final from = points.last.ts.subtract(Duration(days: rangeDays));
    final kept = points.where((p) => !p.ts.isBefore(from)).toList();
    return kept.length > 1 ? kept : points;
  }

  List<ActivityEvent> events = [];
  List<Approval> approvals = [];
  WeeklyReport? weekly;
  Readiness? readiness;
  String? error;
  double? budgetDraft;
  LiveStatus? liveStatus;
  Timer? timer;
  Timer? liveTimer; // the "What the bot is doing" section refreshes faster
  bool refreshing = false,
      liveRefreshing = false; // no pile-up if the server is slow
  bool refreshAgain =
      false; // asked for while one was running (e.g. after Start)

  bool get live => widget.mode == 'live';

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => refresh());
    timer = Timer.periodic(const Duration(seconds: 30), (_) => refresh());
    liveTimer = Timer.periodic(
      const Duration(seconds: 5),
      (_) => refreshLive(),
    );
  }

  /// Just the live status: cheap for the server, so it updates every few seconds.
  Future<void> refreshLive() async {
    // Only while the bot runs; when stopped, the 30-second refresh is enough.
    if (!mounted || summary?.running != true || liveRefreshing) return;
    liveRefreshing = true;
    try {
      final l = await api.live(widget.mode);
      if (mounted) setState(() => liveStatus = l);
    } catch (_) {
      // older servers don't have it; the main refresh shows errors
    } finally {
      liveRefreshing = false;
    }
  }

  @override
  void dispose() {
    timer?.cancel();
    liveTimer?.cancel();
    super.dispose();
  }

  TraderApi get api => AppScope.of(context).api!;

  Future<void> refresh() async {
    if (!mounted) return;
    if (refreshing) {
      refreshAgain = true;
      return;
    }
    refreshing = true;
    try {
      final m = widget.mode;
      final results = await Future.wait([
        api.summary(m),
        api.positions(m),
        api.equity(m),
        api.events(m),
        api.approvals(m),
        api.weekly(m),
        api
            .live(m)
            .then<LiveStatus?>((l) => l)
            .catchError(
              (_) => null, // older servers don't have it
            ),
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
      Readiness? ready;
      if (m == 'live' && !(results[0] as Summary).running) {
        // Only needed while the Start card shows; it fetches months of SPY prices.
        try {
          ready = await api.readiness();
        } catch (_) {} // older servers don't have it
      }
      if (!mounted) return;
      setState(() {
        liveStatus = results[6] as LiveStatus? ?? liveStatus;
        readiness = ready;
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
    } finally {
      refreshing = false;
      if (refreshAgain) {
        refreshAgain = false;
        unawaited(refresh());
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
      // Main column; the side column (holdings, activity) follows.
      // ignore: sort_child_properties_last
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
          if (s.buyingPaused != null) _pausedCard(s.buyingPaused!),
          if (s.mood != null) _moodLine(s.mood!, s.moodLevel),
          if (liveStatus != null) _liveCard(liveStatus!, s),
          if (live && !s.running && readiness != null)
            _readinessCard(readiness!),
          if (live && approvals.isNotEmpty) ...[
            SectionTitle(
              'Waiting for your OK',
              trailing: Pill('${approvals.length}', color: AppColors.live),
            ),
            for (final a in approvals) _approvalCard(a),
          ],
          if (!live) _budgetCard(s),
          if (weekly != null) _weeklyCard(weekly!),
        ],
      ],
      side: [
        if (summary != null) ...[
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
            switch (s.session) {
              'pre' => 'Running · pre-market',
              'after' => 'Running · after hours',
              'closed' => 'Running · market closed',
              _ =>
                s.marketOpen == false ? 'Running · market closed' : 'Running',
            },
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
              fontFeatures: const [FontFeature.tabularFigures()],
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
          AnimatedSwitcher(
            duration: const Duration(milliseconds: 250),
            child: EquityChart(
              key: ValueKey(rangeDays),
              points: _since(curve),
              benchmark: _since(bench),
            ),
          ),
          const SizedBox(height: 6),
          Align(
            alignment: Alignment.centerLeft,
            child: SegmentedButton<int>(
              showSelectedIcon: false,
              style: const ButtonStyle(
                visualDensity: VisualDensity.compact,
                tapTargetSize: MaterialTapTargetSize.shrinkWrap,
              ),
              segments: const [
                ButtonSegment(value: 7, label: Text('1W')),
                ButtonSegment(value: 30, label: Text('1M')),
                ButtonSegment(value: 90, label: Text('3M')),
                ButtonSegment(value: 0, label: Text('All')),
              ],
              selected: {rangeDays},
              onSelectionChanged: (v) => setState(() => rangeDays = v.first),
            ),
          ),
          const SizedBox(height: 4),
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
                onTap: live ? null : () => _typeBudget(s),
              ),
            ],
          ),
          if ((s.otherHoldings ?? 0) >= 1)
            Padding(
              padding: const EdgeInsets.only(top: 10),
              child: Row(
                children: [
                  Expanded(
                    child: Text(
                      'Plus ${moneyRound.format(s.otherHoldings)} in stocks the bot '
                      "isn't managing. It leaves them alone and doesn't count them.",
                      style: t.bodySmall?.copyWith(color: AppColors.muted),
                    ),
                  ),
                  TextButton(
                    onPressed: _adopt,
                    child: const Text('Let the bot manage'),
                  ),
                ],
              ),
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
                        : AppColors.onColor,
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

  Widget _stat(
    String label,
    double? v, {
    String fallback = '—',
    VoidCallback? onTap,
  }) {
    final value = Text(
      v == null ? fallback : moneyRound.format(v),
      style: const TextStyle(fontWeight: FontWeight.w700),
    );
    final column = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          label,
          style: Theme.of(context).textTheme.bodySmall
              ?.copyWith(color: AppColors.muted),
        ),
        const SizedBox(height: 2),
        if (onTap == null)
          value
        else
          Row(
            children: [
              Flexible(child: value),
              const SizedBox(width: 6),
              Icon(Icons.edit_rounded, size: 16, color: AppColors.muted),
            ],
          ),
      ],
    );
    if (onTap == null) return Expanded(child: column);
    return Expanded(
      child: Semantics(
        button: true,
        label: 'Change $label',
        excludeSemantics: false,
        child: InkWell(
          borderRadius: BorderRadius.circular(8),
          onTap: onTap,
          child: column,
        ),
      ),
    );
  }

  /// Type an exact Test budget instead of using the slider.
  Future<void> _typeBudget(Summary s) async {
    final equity = s.equity;
    if (equity == null) return; // not connected to the broker yet
    final maxBudget = equity < 1000
        ? 1000.0
        : equity; // same limits as the slider
    final ctrl = TextEditingController(
      text: s.budget?.toStringAsFixed(0) ?? '',
    );
    final result = await showDialog<String>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Test budget'),
        content: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              'How much of the ${moneyRound.format(equity)} fake money the bot may use.',
            ),
            const SizedBox(height: 12),
            TextField(
              controller: ctrl,
              autofocus: true,
              keyboardType: TextInputType.number,
              decoration: const InputDecoration(
                prefixText: '\$ ',
                hintText: 'For example 10000',
              ),
              // Enter on an empty box does nothing (it isn't "whole account").
              onSubmitted: (v) {
                if (v.trim().isNotEmpty) Navigator.pop(c, v);
              },
            ),
          ],
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c),
            child: const Text('Cancel'),
          ),
          TextButton(
            onPressed: () => Navigator.pop(c, ''),
            child: const Text('Use whole account'),
          ),
          FilledButton(
            onPressed: () =>
                Navigator.pop(c, ctrl.text.trim().isEmpty ? null : ctrl.text),
            child: const Text('Save'),
          ),
        ],
      ),
    );
    if (result == null) return;
    final text = result.trim();
    if (text.isEmpty) {
      await _run(
        () => api.updateSettings('test', {'clear_budget': true}),
        'Test budget: whole account',
      );
      return;
    }
    final v = parseMoney(text);
    if (v == null || v < 100 || v > maxBudget) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              'Enter an amount from \$100 to ${moneyRound.format(maxBudget)}. The budget was not changed.',
            ),
          ),
        );
      }
      return;
    }
    await _run(
      () => api.updateSettings('test', {'budget': v}),
      'Test budget saved: ${moneyRound.format(v)}',
    );
  }

  Future<void> _adopt() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Let the bot manage these stocks?'),
        content: Text(
          'The bot will watch them with safety stops and sell them by its own '
          'rules, as if it had bought them. Use this for stocks it bought and '
          'lost track of, or ones you want it to look after'
          '${live ? ' in your real account' : ''}.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(c, true),
            child: const Text('Hand them over'),
          ),
        ],
      ),
    );
    if (ok != true || !mounted) return;
    ScaffoldMessenger.of(context)
        .showSnackBar(const SnackBar(content: Text('Handing them over...')));
    String msg;
    try {
      final r = await api.adopt(widget.mode);
      msg = r.queued
          ? 'The bot is in the middle of a check. It will take them over as soon as it finishes.'
          : r.symbols.isEmpty
          ? 'Nothing to hand over'
          : 'Now managing ${r.symbols.join(', ')}';
    } on ApiException catch (e) {
      msg = e.message;
    }
    if (mounted) {
      ScaffoldMessenger.of(context)
        ..hideCurrentSnackBar()
        ..showSnackBar(SnackBar(content: Text(msg)));
    }
    await refresh();
  }

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
                'Buy ${shares(a.qty)} ${a.symbol}',
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

  Widget _pausedCard(String reason) => Padding(
    padding: const EdgeInsets.only(top: 14),
    child: GlassCard(
      glow: AppColors.warn,
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Icon(Icons.shield_outlined, color: AppColors.warn),
          const SizedBox(width: 12),
          Expanded(
            child: Text(
              'Safety stop: $reason. No new buys until you press Start. '
              'Stops on what it holds are still active.',
            ),
          ),
        ],
      ),
    ),
  );

  Widget _moodLine(String mood, String? level) {
    final calm = level == 'calm';
    final color = calm ? AppColors.gain : AppColors.warn;
    return Padding(
      padding: const EdgeInsets.fromLTRB(4, 12, 4, 0),
      child: Row(
        children: [
          Icon(
            calm ? Icons.wb_sunny_outlined : Icons.cloud_outlined,
            size: 18,
            color: color,
          ),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              mood,
              style: Theme.of(context).textTheme.bodySmall
                  ?.copyWith(color: AppColors.muted),
            ),
          ),
        ],
      ),
    );
  }

  Widget _readinessCard(Readiness r) {
    final t = Theme.of(context).textTheme;
    return Padding(
      padding: const EdgeInsets.only(top: 14),
      child: GlassCard(
        glow: r.ready ? AppColors.gain : AppColors.live,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Icon(
                  r.ready ? Icons.verified_outlined : Icons.flag_outlined,
                  color: r.ready ? AppColors.gain : AppColors.live,
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    r.ready ? 'Ready for real money' : 'Before real money',
                    style: t.titleMedium?.copyWith(fontWeight: FontWeight.w700),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 4),
            Text(
              r.starterCap != null
                  ? 'Live starts small: at most ${moneyRound.format(r.starterCap)} for the first 30 days.'
                  : 'The bot proves itself in Test before it trades real money.',
              style: t.bodySmall?.copyWith(color: AppColors.muted),
            ),
            const SizedBox(height: 10),
            for (final c in r.checks)
              Semantics(
                label: '${c.name}: ${c.ok ? 'done' : 'not yet'}. ${c.detail}',
                excludeSemantics: true,
                child: Padding(
                  padding: const EdgeInsets.symmetric(vertical: 5),
                  child: Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Icon(
                        c.ok
                            ? Icons.check_circle_rounded
                            : Icons.radio_button_unchecked_rounded,
                        size: 20,
                        color: c.ok ? AppColors.gain : AppColors.muted,
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              c.name,
                              style: const TextStyle(
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                            Text(
                              c.detail,
                              style: t.bodySmall?.copyWith(
                                color: AppColors.muted,
                              ),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }

  /// "What the bot is doing": its current step, recent steps and last results.
  Widget _liveCard(LiveStatus l, Summary s) {
    final t = Theme.of(context).textTheme;
    final time = DateFormat.jms();
    final active = s.running && s.error == null;
    final color = !s.running
        ? AppColors.muted
        : s.error != null
        ? AppColors.loss
        : AppColors.gain;
    String? next;
    if (l.nextCheck != null) {
      final mins = l.nextCheck!.difference(DateTime.now()).inMinutes;
      next = mins < 1 ? 'Next check any moment' : 'Next check in $mins min';
    }
    final last = l.lastCycle;
    return Padding(
      padding: const EdgeInsets.only(top: 14),
      child: GlassCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                SizedBox(
                  width: 16,
                  height: 16,
                  child: l.working
                      ? CircularProgressIndicator(strokeWidth: 2, color: color)
                      : Center(
                          child: Container(
                            width: 10,
                            height: 10,
                            decoration: BoxDecoration(
                              color: color,
                              shape: BoxShape.circle,
                            ),
                          ),
                        ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    'What the bot is doing',
                    style: t.titleMedium?.copyWith(fontWeight: FontWeight.w700),
                  ),
                ),
                if (active && next != null && !l.working)
                  Text(
                    next,
                    style: t.bodySmall?.copyWith(color: AppColors.muted),
                  ),
              ],
            ),
            const SizedBox(height: 10),
            Text(
              l.working ? '${l.now}…' : l.now,
              style: t.bodyLarge?.copyWith(fontWeight: FontWeight.w600),
            ),
            if (last != null) ...[
              const SizedBox(height: 6),
              Text(
                'Last check ${ago(last.ts)} · looked at ${last.checked} stocks',
                style: t.bodySmall?.copyWith(color: AppColors.muted),
              ),
            ],
            if (l.steps.isNotEmpty)
              ExpansionTile(
                tilePadding: EdgeInsets.zero,
                childrenPadding: EdgeInsets.zero,
                shape: const Border(),
                collapsedShape: const Border(),
                title: Text('Step by step', style: t.titleSmall),
                children: [
                  for (final st in l.steps.take(30))
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 4),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          SizedBox(
                            width: 92,
                            child: Text(
                              time.format(st.ts.toLocal()),
                              style: t.bodySmall?.copyWith(
                                color: AppColors.muted,
                                fontFeatures: const [
                                  FontFeature.tabularFigures(),
                                ],
                              ),
                            ),
                          ),
                          Expanded(child: Text(st.text, style: t.bodyMedium)),
                        ],
                      ),
                    ),
                ],
              ),
            if (last != null && last.skipped.isNotEmpty)
              ExpansionTile(
                tilePadding: EdgeInsets.zero,
                childrenPadding: EdgeInsets.zero,
                shape: const Border(),
                collapsedShape: const Border(),
                title: Text(
                  'Why it passed on ${last.skippedTotal} stock${last.skippedTotal == 1 ? '' : 's'}',
                  style: t.titleSmall,
                ),
                children: [
                  for (final x in last.skipped)
                    Padding(
                      padding: const EdgeInsets.symmetric(vertical: 4),
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Padding(
                            padding: const EdgeInsets.only(top: 2, right: 8),
                            child: Icon(
                              Icons.remove_circle_outline_rounded,
                              size: 16,
                              color: AppColors.muted,
                            ),
                          ),
                          Expanded(child: _skipText(x, t)),
                        ],
                      ),
                    ),
                  if (last.skippedTotal > last.skipped.length)
                    Padding(
                      padding: const EdgeInsets.only(top: 4),
                      child: Text(
                        'and ${last.skippedTotal - last.skipped.length} more',
                        style: t.bodySmall?.copyWith(color: AppColors.muted),
                      ),
                    ),
                ],
              ),
          ],
        ),
      ),
    );
  }

  /// "AAPL: price under $1.00" with the symbol in bold.
  Widget _skipText(String x, TextTheme t) {
    final i = x.indexOf(': ');
    if (i < 0) return Text(x, style: t.bodyMedium);
    return Text.rich(
      TextSpan(
        style: t.bodyMedium,
        children: [
          TextSpan(
            text: x.substring(0, i),
            style: const TextStyle(fontWeight: FontWeight.w700),
          ),
          TextSpan(text: '  ${x.substring(i + 2)}'),
        ],
      ),
    );
  }

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
                TextButton.icon(
                  onPressed: () => _typeBudget(s),
                  icon: const Icon(Icons.edit_rounded, size: 18),
                  label: Text(
                    moneyRound.format(value),
                    style: const TextStyle(
                      fontWeight: FontWeight.w800,
                      fontSize: 18,
                    ),
                  ),
                ),
              ],
            ),
            const SizedBox(height: 4),
            Text(
              'How much fake money the bot may use for this test. Drag the slider, or tap the amount to type one.',
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
                '${shares(h.qty)} ${h.qty == 1 ? 'share' : 'shares'} · stop ${h.stop == null ? '—' : money.format(h.stop)}',
                style: Theme.of(context).textTheme.bodySmall
                    ?.copyWith(color: AppColors.muted),
              ),
              if (h.stop != null && h.price > 0) ...[
                const SizedBox(height: 6),
                _roomToStop(h),
              ],
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

  /// How far the price is above its safety stop: a quick read on which holdings are at risk.
  Widget _roomToStop(Holding h) {
    final room = ((h.price - h.stop!) / h.price).clamp(0.0, 1.0);
    final color = room < 0.02
        ? AppColors.loss
        : room < 0.04
        ? AppColors.warn
        : AppColors.gain;
    return Semantics(
      label: '${(room * 100).toStringAsFixed(1)} percent above its safety stop',
      child: Row(
        children: [
          Expanded(
            child: ClipRRect(
              borderRadius: BorderRadius.circular(3),
              child: LinearProgressIndicator(
                value: (room / 0.10).clamp(0.0, 1.0),
                minHeight: 4,
                color: color,
                backgroundColor: color.withValues(alpha: 0.12),
              ),
            ),
          ),
          const SizedBox(width: 8),
          Text(
            '${(room * 100).toStringAsFixed(1)}% to stop',
            style: Theme.of(context).textTheme.labelSmall?.copyWith(
              color: color,
              fontFeatures: const [FontFeature.tabularFigures()],
            ),
          ),
        ],
      ),
    );
  }

  Widget _eventTile(ActivityEvent e) {
    final (icon, color) = switch (e.kind) {
      'buy' => (Icons.south_west_rounded, AppColors.gain),
      'sell' => (Icons.north_east_rounded, AppColors.loss),
      'approval' => (Icons.how_to_reg_rounded, AppColors.live),
      'kill' => (Icons.power_settings_new_rounded, AppColors.loss),
      'learning' => (Icons.psychology_rounded, AppColors.accent),
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
