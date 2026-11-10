import 'package:flutter/material.dart';

import '../api.dart';
import '../models.dart';
import '../state.dart';
import '../theme.dart';
import '../widgets.dart';

/// "What the bot learned": lessons from its finished trades and how much each
/// idea source counts now. In Live, new lessons from Test wait here for your OK.
class LearningCard extends StatefulWidget {
  const LearningCard({super.key, required this.mode});
  final String mode;

  @override
  State<LearningCard> createState() => _LearningCardState();
}

class _LearningCardState extends State<LearningCard> {
  Learning? data;
  String? error;
  bool busy = false;

  TraderApi get api => AppScope.of(context).api!;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => load());
  }

  Future<void> load() => _run(() => api.learning(widget.mode));

  Future<void> _run(Future<Learning> Function() call) async {
    if (busy) return;
    setState(() => busy = true);
    try {
      final d = await call();
      if (mounted) {
        setState(() {
          data = d;
          error = null;
        });
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.message);
    } finally {
      if (mounted) setState(() => busy = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final d = data;
    final live = widget.mode == 'live';
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        SectionTitle(
          'What the bot learned',
          trailing: !live && d != null
              ? PopupMenuButton<String>(
                  tooltip: 'Learning options',
                  icon: const Icon(Icons.more_horiz_rounded),
                  enabled: !busy,
                  onSelected: (v) =>
                      v == 'learn' ? _run(api.learnNow) : _practice(),
                  itemBuilder: (_) => [
                    PopupMenuItem(
                      value: 'learn',
                      enabled: d.needs == 0,
                      child: const ListTile(
                        leading: Icon(Icons.refresh_rounded),
                        title: Text('Learn now'),
                      ),
                    ),
                    const PopupMenuItem(
                      value: 'practice',
                      child: ListTile(
                        leading: Icon(Icons.school_rounded),
                        title: Text('Practise on past trades'),
                        subtitle: Text('Once a month, about \$1'),
                      ),
                    ),
                  ],
                )
              : null,
        ),
        if (error != null) ErrorBox(error!, onRetry: load),
        if (busy && d == null) const SkeletonCard(),
        if (d != null && d.proposed != null) _proposal(d.proposed!),
        if (d != null && d.active != null)
          GlassCard(child: _memory(d.active!, live ? 'Live uses' : 'Test uses'))
        else if (d != null && d.proposed == null)
          GlassCard(
            child: Text(
              d.needs > 0
                  ? 'Once a week the bot reviews how its finished trades turned out and '
                        'writes lessons for the AI analyst. It needs ${d.needs} more '
                        'finished trade${d.needs == 1 ? '' : 's'} first.'
                  : live
                  ? 'New lessons are learned in Test first, then shown here for you to approve.'
                  : 'Its first review runs within ${d.everyDays} days, or tap Learn now.',
              style: t.bodyMedium,
            ),
          ),
      ],
    );
  }

  Future<void> _practice() async {
    try {
      final note = await api.practice();
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(note)));
      }
    } on ApiException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(e.message)));
      }
    }
  }

  Widget _proposal(LearnedMemory m) => Padding(
    padding: const EdgeInsets.only(bottom: 10),
    child: GlassCard(
      glow: AppColors.live,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Pill(
            'Waiting for your OK',
            color: AppColors.live,
            icon: Icons.how_to_reg_rounded,
          ),
          const SizedBox(height: 10),
          _memory(m, 'New from Test'),
          const SizedBox(height: 8),
          Text(
            'These only change which stocks rank first and what the analyst watches for. '
            'Stop-losses, budgets and position sizes never change.',
            style: Theme.of(context).textTheme.bodySmall
                ?.copyWith(color: AppColors.muted),
          ),
          const SizedBox(height: 12),
          Wrap(
            spacing: 10,
            runSpacing: 8,
            alignment: WrapAlignment.end,
            children: [
              OutlinedButton(
                onPressed: busy
                    ? null
                    : () => _run(() => api.decideLearning(false)),
                child: const Text('Not now'),
              ),
              FilledButton(
                onPressed: busy
                    ? null
                    : () => _run(() => api.decideLearning(true)),
                child: const Text('Use in Live'),
              ),
            ],
          ),
        ],
      ),
    ),
  );

  Widget _memory(LearnedMemory m, String heading) {
    final t = Theme.of(context).textTheme;
    final muted = t.bodySmall?.copyWith(color: AppColors.muted);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          '$heading: from ${m.trades} finished trades '
          '(${m.wins} made money), ${ago(m.ts)}',
          style: muted,
        ),
        const SizedBox(height: 10),
        if (m.lessonsError != null)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: Text(
              "Couldn't write new lessons this time, so it kept the last ones "
              '(it will try again in a few hours): ${m.lessonsError}',
              style: t.bodySmall?.copyWith(color: AppColors.warn),
            ),
          ),
        if (m.notebook.isNotEmpty)
          ..._notebook(m)
        else if (m.lessons.isEmpty)
          Text('No written lessons yet.', style: t.bodyMedium)
        else
          for (final l in m.lessons)
            Padding(
              padding: const EdgeInsets.only(bottom: 8),
              child: Row(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Padding(
                    padding: const EdgeInsets.only(top: 2, right: 8),
                    child: Icon(
                      Icons.psychology_rounded,
                      size: 18,
                      color: AppColors.accent,
                    ),
                  ),
                  Expanded(child: Text(l)),
                ],
              ),
            ),
        if (m.calibration != null && m.calibration!.calls > 0) ...[
          const Divider(height: 28),
          _calibration(m.calibration!),
        ],
        if (m.exam != null) ...[const SizedBox(height: 10), _exam(m.exam!)],
        if (m.weights.isNotEmpty) ...[
          const Divider(height: 28),
          Text('How much each idea source counts', style: muted),
          const SizedBox(height: 6),
          for (final w in m.weights) _weightRow(w.label, w.weight),
        ],
        if (m.reviews.isNotEmpty) ...[
          const Divider(height: 20),
          Theme(
            data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
            child: ExpansionTile(
              tilePadding: EdgeInsets.zero,
              childrenPadding: EdgeInsets.zero,
              title: Text('Its reviews of its own trades', style: t.titleSmall),
              subtitle: Text(
                '${m.reviews.length} most recent'
                '${m.practiceTrades > 0 ? ' · practised on ${m.practiceTrades} past trades' : ''}',
                style: muted,
              ),
              children: [for (final r in m.reviews) _review(r)],
            ),
          ),
        ],
      ],
    );
  }

  List<Widget> _notebook(LearnedMemory m) {
    final t = Theme.of(context).textTheme;
    final muted = t.bodySmall?.copyWith(color: AppColors.muted);
    final proven = m.notebook.where((e) => e.status == 'active').toList();
    final testing = m.notebook.where((e) => e.status == 'candidate').toList();
    final retired = m.notebook.where((e) => e.status == 'retired').toList();
    Widget group(
      String title,
      String hint,
      List<NotebookEntry> items,
      Color c,
      IconData icon,
    ) => Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Row(
          children: [
            Icon(icon, size: 16, color: c),
            const SizedBox(width: 6),
            Text(title, style: t.labelLarge?.copyWith(color: c)),
            const SizedBox(width: 8),
            Expanded(child: Text(hint, style: muted)),
          ],
        ),
        const SizedBox(height: 8),
        for (final e in items)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [Text(e.text), const SizedBox(height: 4), _evidence(e)],
            ),
          ),
      ],
    );
    return [
      if (proven.isNotEmpty)
        group(
          'Proven',
          'the analyst relies on these',
          proven,
          AppColors.gain,
          Icons.verified_rounded,
        ),
      if (testing.isNotEmpty)
        group(
          'Being tested',
          'shown to it as unproven',
          testing,
          AppColors.accent,
          Icons.science_rounded,
        ),
      if (retired.isNotEmpty)
        Theme(
          data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
          child: ExpansionTile(
            tilePadding: EdgeInsets.zero,
            title: Text('Retired (${retired.length})', style: muted),
            children: [
              for (final e in retired)
                Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: Text(
                    e.text,
                    style: muted?.copyWith(
                      decoration: TextDecoration.lineThrough,
                    ),
                  ),
                ),
            ],
          ),
        ),
      if (proven.isEmpty && testing.isEmpty)
        Text('Every lesson so far has been retired.', style: muted),
    ];
  }

  /// Trades for vs against, as a small split bar with counts.
  Widget _evidence(NotebookEntry e) {
    final total = e.support + e.against;
    return Row(
      children: [
        SizedBox(
          width: 90,
          height: 6,
          child: ClipRRect(
            borderRadius: BorderRadius.circular(3),
            child: Row(
              children: [
                if (e.support > 0)
                  Expanded(
                    flex: e.support,
                    child: Container(color: AppColors.gain),
                  ),
                if (e.against > 0)
                  Expanded(
                    flex: e.against,
                    child: Container(color: AppColors.loss),
                  ),
                if (total == 0)
                  Expanded(child: Container(color: AppColors.muted)),
              ],
            ),
          ),
        ),
        const SizedBox(width: 8),
        Flexible(
          child: Text(
            '${e.support} trade${e.support == 1 ? '' : 's'} for · ${e.against} against'
            '${e.avgReturn == null ? '' : ' · they averaged ${pct(e.avgReturn!)}'}',
            style: Theme.of(context).textTheme.bodySmall
                ?.copyWith(color: AppColors.muted),
          ),
        ),
      ],
    );
  }

  Widget _calibration(Calibration c) {
    final t = Theme.of(context).textTheme;
    final muted = t.bodySmall?.copyWith(color: AppColors.muted);
    Widget bar(double v, Color color) => ClipRRect(
      borderRadius: BorderRadius.circular(3),
      child: LinearProgressIndicator(
        value: v.clamp(0, 1),
        minHeight: 6,
        color: color,
        backgroundColor: color.withValues(alpha: 0.12),
      ),
    );
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Text(
          'How sure it says it is vs how often it\'s right',
          style: t.titleSmall,
        ),
        const SizedBox(height: 4),
        Text(c.note, style: muted),
        const SizedBox(height: 10),
        for (final b in c.bins)
          Padding(
            padding: const EdgeInsets.only(bottom: 10),
            child: Row(
              children: [
                SizedBox(
                  width: 92,
                  child: Text('${b.label}\n${b.calls} calls', style: muted),
                ),
                Expanded(
                  child: Column(
                    children: [
                      bar(b.said, AppColors.accent),
                      const SizedBox(height: 4),
                      bar(b.right, AppColors.gain),
                    ],
                  ),
                ),
                const SizedBox(width: 10),
                SizedBox(
                  width: 74,
                  child: Text(
                    'said ${(b.said * 100).round()}%\nright ${(b.right * 100).round()}%',
                    textAlign: TextAlign.right,
                    style: t.bodySmall?.copyWith(
                      fontFeatures: const [FontFeature.tabularFigures()],
                    ),
                  ),
                ),
              ],
            ),
          ),
      ],
    );
  }

  Widget _exam(ExamResult e) => Row(
    children: [
      Icon(
        e.passed ? Icons.task_alt_rounded : Icons.block_rounded,
        size: 18,
        color: e.passed ? AppColors.gain : AppColors.warn,
      ),
      const SizedBox(width: 8),
      Expanded(
        child: Text(
          e.newMoney != null && e.oldMoney != null
              ? (e.passed
                    ? 'Its latest lessons passed their exam on ${e.cases} past trades: '
                          'the buys it would have made returned ${pct(e.newMoney!)} in total, vs ${pct(e.oldMoney!)} before.'
                    : 'Its latest lessons would have made less on ${e.cases} past trades '
                          '(${pct(e.newMoney!)} vs ${pct(e.oldMoney!)}), so it kept the old ones.')
              : e.passed
              ? 'Its latest lessons passed their exam on ${e.cases} past trades.'
              : 'Its latest lessons made its calls on ${e.cases} past trades worse, so it kept the old ones.',
          style: Theme.of(context).textTheme.bodySmall,
        ),
      ),
    ],
  );

  Widget _review(PostMortem r) {
    final t = Theme.of(context).textTheme;
    final (label, color) = switch (r.cause) {
      'reasoning' => ('Its reasoning', AppColors.accent),
      'rules' => ('The bot\'s rules', AppColors.test),
      'market' => ('The market', AppColors.warn),
      _ => ('Luck', AppColors.muted),
    };
    return Padding(
      padding: const EdgeInsets.only(bottom: 14),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Expanded(
                child: Text(
                  r.trade,
                  style: t.labelLarge?.copyWith(fontWeight: FontWeight.w700),
                ),
              ),
              Pill(label, color: color),
            ],
          ),
          const SizedBox(height: 4),
          Text('Expected: ${r.expected}', style: t.bodySmall),
          Text('Happened: ${r.happened}', style: t.bodySmall),
          if (r.lesson.isNotEmpty)
            Text(
              'Takeaway: ${r.lesson}',
              style: t.bodySmall?.copyWith(color: AppColors.accent),
            ),
        ],
      ),
    );
  }

  Widget _weightRow(String label, double weight) {
    final (text, color) = weight > 1.0
        ? ('Counts more', AppColors.gain)
        : weight < 1.0
        ? ('Counts less', AppColors.warn)
        : ('No change yet', AppColors.muted);
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Row(
        children: [
          Expanded(child: Text(label)),
          Pill('$text · ${weight.toStringAsFixed(2)}×', color: color),
        ],
      ),
    );
  }
}
