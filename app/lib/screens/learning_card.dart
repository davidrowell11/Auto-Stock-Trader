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
          trailing: !live && d != null && d.needs == 0
              ? TextButton.icon(
                  onPressed: busy ? null : () => _run(api.learnNow),
                  icon: const Icon(Icons.refresh_rounded, size: 18),
                  label: const Text('Learn now'),
                )
              : null,
        ),
        if (error != null) ErrorBox(error!, onRetry: load),
        if (busy && d == null) const LinearProgressIndicator(),
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
        if (m.lessons.isEmpty)
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
        if (m.weights.isNotEmpty) ...[
          const SizedBox(height: 6),
          Text('How much each idea source counts', style: muted),
          const SizedBox(height: 6),
          for (final w in m.weights) _weightRow(w.label, w.weight),
        ],
      ],
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
