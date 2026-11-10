import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:local_auth/local_auth.dart';

import '../api.dart';
import '../models.dart';
import '../state.dart';
import '../theme.dart';
import '../widgets.dart';

/// Change the bot's safety rules, one slider each, inside the ranges the bot allows.
class SafetyRulesScreen extends StatefulWidget {
  const SafetyRulesScreen({super.key, required this.mode, this.budget});
  final String mode; // test or live
  final double? budget; // to show what a percent means in dollars

  @override
  State<SafetyRulesScreen> createState() => _SafetyRulesScreenState();
}

class _SafetyRulesScreenState extends State<SafetyRulesScreen> {
  RulesReport? report;
  final Map<String, double> draft = {};
  String? error;
  bool saving = false;

  bool get live => widget.mode == 'live';
  Color get modeColor => live ? AppColors.live : AppColors.test;
  TraderApi get api => AppScope.of(context).api!;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => load());
  }

  Future<void> load() async {
    try {
      final r = await api.rules(widget.mode);
      if (mounted) {
        setState(() {
          report = r;
          draft.clear();
          error = null;
        });
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.message);
    }
  }

  double valueOf(SafetyRule r) => draft[r.key] ?? r.value;

  List<SafetyRule> get changed => [
    for (final r in report?.rules ?? <SafetyRule>[])
      if (draft.containsKey(r.key) && (draft[r.key]! - r.value).abs() > 1e-9) r,
  ];

  Future<bool> _confirmIdentity() async {
    if (kIsWeb) return true; // the web preview has no Face ID; the confirm dialog stands in
    final auth = LocalAuthentication();
    try {
      // No lock set up on this device: the confirm dialog is the check.
      if (!await auth.isDeviceSupported()) return true;
      return await auth.authenticate(
        localizedReason: 'Allow riskier safety rules for real money',
      );
    } catch (_) {
      return false; // locked out or failed: real money, so stay safe
    }
  }

  Future<void> save({bool reset = false}) async {
    final rules = report?.rules ?? [];
    final riskier = [
      for (final r in reset ? rules : changed)
        if (r.looser(r.value, reset ? r.ifReset : draft[r.key]!)) r,
    ];
    if (live && riskier.isNotEmpty && !(report?.starterLock ?? false)) {
      final ok = await showDialog<bool>(
        context: context,
        builder: (c) => AlertDialog(
          title: const Text('Allow more risk with real money?'),
          content: Text(
            '${riskier.map((r) => r.label).join(', ')} '
            '${riskier.length == 1 ? 'takes' : 'take'} more risk. '
            'Safer changes start now; these start at the next market open (9:30am New York time).',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(c, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(c, true),
              child: const Text('Confirm'),
            ),
          ],
        ),
      );
      if (ok != true || !await _confirmIdentity()) return;
    }
    setState(() => saving = true);
    try {
      final r = await api.updateRules(widget.mode, {
        for (final c in changed) c.key: draft[c.key],
      }, reset: reset);
      if (!mounted) return;
      setState(() {
        report = r;
        draft.clear();
      });
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: Text(
            r.waiting.isEmpty
                ? 'Saved'
                : 'Saved. Riskier changes start at the next market open.',
          ),
        ),
      );
    } on ApiException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(e.message)));
      }
    } finally {
      if (mounted) setState(() => saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final r = report;
    final dirty = changed.isNotEmpty;
    // Live's starter period refuses riskier changes, and the whole save with them.
    final blocked =
        live &&
        (r?.starterLock ?? false) &&
        changed.any((c) => c.looser(c.value, draft[c.key]!));
    return Scaffold(
      extendBodyBehindAppBar: true,
      appBar: AppBar(
        title: Text('Safety rules · ${live ? 'Live' : 'Test'}'),
        actions: [
          if (r != null)
            TextButton(
              onPressed: saving ? null : () => _askReset(),
              child: const Text('Recommended'),
            ),
        ],
      ),
      bottomNavigationBar: dirty
          ? SafeArea(
              child: Padding(
                padding: const EdgeInsets.fromLTRB(16, 8, 16, 12),
                child: Row(
                  children: [
                    Expanded(
                      child: OutlinedButton(
                        onPressed: saving ? null : () => setState(draft.clear),
                        child: const Text('Undo'),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      flex: 2,
                      child: FilledButton(
                        onPressed: saving || blocked ? null : save,
                        child: Text(
                          blocked
                              ? 'Only safer changes for now'
                              : 'Save ${changed.length} change${changed.length == 1 ? '' : 's'}',
                        ),
                      ),
                    ),
                  ],
                ),
              ),
            )
          : null,
      body: AuroraBackground(
        child: SafeArea(
          child: PageFrame(
            onRefresh: load,
            children: [
              if (error != null) ErrorBox(error!, onRetry: load),
              if (r == null && error == null) ...[
                const SkeletonCard(),
                const SizedBox(height: 12),
                const SkeletonCard(),
              ],
              if (r != null) ...[
                GlassCard(
                  glow: modeColor,
                  child: Row(
                    children: [
                      Icon(
                        r.starterLock
                            ? Icons.lock_rounded
                            : Icons.shield_rounded,
                        color: modeColor,
                      ),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Text(
                          "${r.note} The bot's fixed hard limits sit above every range.",
                          style: t.bodyMedium,
                        ),
                      ),
                    ],
                  ),
                ),
                for (final rule in r.rules) ...[
                  const SizedBox(height: 12),
                  _ruleCard(rule, r.starterLock),
                ],
              ],
            ],
          ),
        ),
      ),
    );
  }

  Future<void> _askReset() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Back to the recommended rules?'),
        content: const Text(
          'Your own values are forgotten. Every rule goes back to the tested '
          'default, or to the strategy lab\'s value where it has one.',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(c, true),
            child: const Text('Use recommended'),
          ),
        ],
      ),
    );
    if (ok == true) await save(reset: true);
  }

  String? _dollars(SafetyRule r, double v) {
    final b = widget.budget;
    if (b == null || r.unit != 'percent') return null;
    return '${moneyRound.format(b * v)} of ${moneyRound.format(b)}';
  }

  Widget _ruleCard(SafetyRule r, bool starterLock) {
    final t = Theme.of(context).textTheme;
    final v = valueOf(r);
    final edited = draft.containsKey(r.key) && (v - r.value).abs() > 1e-9;
    final riskier = r.looser(r.value, v);
    final blocked = live && starterLock && riskier;
    final divisions = ((r.high - r.low) / r.step).round();
    final dollars = _dollars(r, v);
    final badge = switch (r.setBy) {
      'you' => Pill(
        'Set by you',
        color: AppColors.accent,
        icon: Icons.person_rounded,
      ),
      'lab' => Pill(
        'Set by the lab',
        color: AppColors.accent2,
        icon: Icons.science_rounded,
      ),
      _ => null,
    };
    return GlassCard(
      glow: edited ? (riskier ? AppColors.warn : AppColors.gain) : null,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Expanded(
                child: Text(
                  r.label,
                  style: t.titleSmall?.copyWith(fontWeight: FontWeight.w700),
                ),
              ),
              const SizedBox(width: 12),
              AnimatedSwitcher(
                duration: const Duration(milliseconds: 180),
                child: Text(
                  r.show(v),
                  key: ValueKey(v),
                  style: t.headlineSmall?.copyWith(
                    fontWeight: FontWeight.w800,
                    fontFeatures: const [FontFeature.tabularFigures()],
                    color: edited
                        ? (riskier ? AppColors.warn : AppColors.gain)
                        : null,
                  ),
                ),
              ),
            ],
          ),
          if (dollars != null)
            Text(dollars, style: t.bodySmall?.copyWith(color: AppColors.muted)),
          Slider(
            value: v.clamp(r.low, r.high),
            min: r.low,
            max: r.high,
            divisions: divisions > 0 ? divisions : null,
            label: r.show(v),
            activeColor: modeColor,
            semanticFormatterCallback: (x) => '${r.label} ${r.show(x)}',
            onChanged: saving
                ? null
                : (x) => setState(() {
                    final snapped =
                        r.low + ((x - r.low) / r.step).round() * r.step;
                    draft[r.key] = double.parse(snapped.toStringAsFixed(6));
                  }),
          ),
          Row(
            children: [
              Text(
                r.show(r.low),
                style: t.bodySmall?.copyWith(color: AppColors.muted),
              ),
              const Spacer(),
              Text(
                'Recommended ${r.show(r.recommended)}',
                style: t.bodySmall?.copyWith(color: AppColors.muted),
              ),
              const Spacer(),
              Text(
                r.show(r.high),
                style: t.bodySmall?.copyWith(color: AppColors.muted),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(r.help, style: t.bodySmall),
          if (badge != null || r.pendingValue != null || edited) ...[
            const SizedBox(height: 10),
            Wrap(
              spacing: 8,
              runSpacing: 6,
              children: [
                ?badge,
                if (r.pendingValue != null)
                  Pill(
                    'Changes to ${r.show(r.pendingValue!)} at the next open',
                    color: AppColors.warn,
                    icon: Icons.schedule_rounded,
                  ),
                if (edited && blocked)
                  Pill(
                    'Live\'s first 30 days: only safer changes',
                    color: AppColors.loss,
                    icon: Icons.lock_rounded,
                  )
                else if (edited && riskier)
                  Pill(
                    live ? 'More risk: starts at the next open' : 'More risk',
                    color: AppColors.warn,
                    icon: Icons.trending_up_rounded,
                  )
                else if (edited)
                  Pill(
                    'Safer',
                    color: AppColors.gain,
                    icon: Icons.shield_rounded,
                  ),
              ],
            ),
          ],
        ],
      ),
    );
  }
}
