import 'package:flutter/material.dart';

import '../api.dart';
import '../models.dart';
import '../state.dart';
import '../theme.dart';
import '../widgets.dart';

/// "Strategy lab": the rule changes the bot proved on past prices, one waiting
/// for your OK in Live, and its recent experiments.
class LabCard extends StatefulWidget {
  const LabCard({super.key, required this.mode});
  final String mode;

  @override
  State<LabCard> createState() => _LabCardState();
}

class _LabCardState extends State<LabCard> {
  LabReport? data;
  String? error;
  bool busy = false;

  TraderApi get api => AppScope.of(context).api!;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => load());
  }

  Future<void> load() => _run(() => api.lab(widget.mode));

  Future<void> _run(Future<LabReport> Function() call) async {
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
    final muted = t.bodySmall?.copyWith(color: AppColors.muted);
    final d = data;
    final live = widget.mode == 'live';
    if (d != null && !d.enabled) return const SizedBox.shrink();
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        SectionTitle(
          'Strategy lab',
          trailing: !live && d != null
              ? TextButton.icon(
                  onPressed: busy || d.running ? null : () => _run(api.runLab),
                  icon: const Icon(Icons.science_rounded, size: 18),
                  label: Text(d.running ? 'Testing…' : 'Test an idea now'),
                )
              : null,
        ),
        if (error != null) ErrorBox(error!, onRetry: load),
        if (busy && d == null) const LinearProgressIndicator(),
        if (d != null && d.proposed != null) _proposal(d),
        if (d != null)
          GlassCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: [
                Text(
                  'Once every ${d.everyDays} days the bot tries one small rule change on past '
                  'prices. It keeps it only if it also wins on newer prices it never tuned on, '
                  'and fixes some of the current rules\' losing trades. ${d.limitsNote}',
                  style: muted,
                ),
                const SizedBox(height: 12),
                if (d.changes.isEmpty)
                  Text(
                    '${live ? 'Live' : 'Test'} uses the standard rules.',
                    style: t.bodyMedium,
                  )
                else ...[
                  Text(
                    '${live ? 'Live' : 'Test'} uses these changes:',
                    style: t.bodyMedium,
                  ),
                  const SizedBox(height: 6),
                  for (final c in d.changes) _changeRow(c),
                  Align(
                    alignment: Alignment.centerRight,
                    child: TextButton(
                      onPressed: busy
                          ? null
                          : () => _run(() => api.undoLab(widget.mode)),
                      child: const Text('Undo: back to standard rules'),
                    ),
                  ),
                ],
                if (d.experiments.isNotEmpty) ...[
                  const Divider(height: 24),
                  Text('Recent experiments', style: muted),
                  const SizedBox(height: 6),
                  for (final e in d.experiments.take(5)) _experiment(e),
                ],
              ],
            ),
          ),
      ],
    );
  }

  Widget _changeRow(LabChange c) => Padding(
    padding: const EdgeInsets.only(bottom: 6),
    child: Row(
      children: [
        Expanded(child: Text(c.label)),
        Pill('${_fmt(c.standard)} → ${_fmt(c.now)}', color: AppColors.accent),
      ],
    ),
  );

  Widget _proposal(LabReport d) {
    final e = d.proposedEntry;
    return Padding(
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
            for (final c in d.proposed!) _changeRow(c),
            if (e != null) ...[
              const SizedBox(height: 6),
              _results(e),
              for (final line in e.evidence)
                Text('• $line', style: Theme.of(context).textTheme.bodySmall),
            ],
            const SizedBox(height: 12),
            Wrap(
              spacing: 10,
              runSpacing: 8,
              alignment: WrapAlignment.end,
              children: [
                OutlinedButton(
                  onPressed: busy
                      ? null
                      : () => _run(() => api.decideLab(false)),
                  child: const Text('Not now'),
                ),
                FilledButton(
                  onPressed: busy
                      ? null
                      : () => _run(() => api.decideLab(true)),
                  child: const Text('Use in Live'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  Widget _experiment(LabExperiment e) => Padding(
    padding: const EdgeInsets.only(bottom: 10),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Icon(
              e.kept ? Icons.check_circle_rounded : Icons.cancel_rounded,
              size: 18,
              color: e.kept ? AppColors.gain : AppColors.muted,
            ),
            const SizedBox(width: 8),
            Expanded(child: Text(e.change)),
            Text(
              ago(e.ts),
              style: Theme.of(context).textTheme.bodySmall
                  ?.copyWith(color: AppColors.muted),
            ),
          ],
        ),
        Padding(
          padding: const EdgeInsets.only(left: 26, top: 2),
          child: Text(
            e.verdict,
            style: Theme.of(context).textTheme.bodySmall
                ?.copyWith(color: AppColors.muted),
          ),
        ),
      ],
    ),
  );

  Widget _results(LabExperiment e) {
    final a = e.unseenCurrent, b = e.unseenNew;
    if (a == null || b == null) return const SizedBox.shrink();
    String pct(dynamic v) => '${((v as num) * 100).toStringAsFixed(1)}%';
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Text(
        'On newer prices it never tuned on: return ${pct(a['return'])} → ${pct(b['return'])}, '
        'worst drop ${pct(a['max_drop'])} → ${pct(b['max_drop'])}. It fixed these losing trades:',
        style: Theme.of(context).textTheme.bodySmall,
      ),
    );
  }

  String _fmt(double v) =>
      v == v.roundToDouble() ? v.toStringAsFixed(0) : v.toString();
}
