import 'package:flutter/material.dart';

import '../api.dart';
import '../models.dart';
import '../state.dart';
import '../theme.dart';
import '../widgets.dart';
import 'safety_rules.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  BotSettings? test, live;
  AiSpending? spending;
  String? error;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => load());
  }

  TraderApi get api => AppScope.of(context).api!;

  Future<void> load() async {
    try {
      final r = await Future.wait([api.settings('test'), api.settings('live')]);
      if (mounted) {
        setState(() {
          test = r[0];
          live = r[1];
          error = null;
        });
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.message);
    }
    try {
      final s = await api.aiSpending();
      if (mounted) setState(() => spending = s);
    } on ApiException {
      // An older server without the cap: the rest of Settings still works.
    }
  }

  Future<void> save(String mode, Map<String, dynamic> changes) async {
    try {
      final s = await api.updateSettings(mode, changes);
      if (!mounted) return;
      setState(() => mode == 'test' ? test = s : live = s);
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('Saved')));
    } on ApiException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(e.message)));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final app = AppScope.of(context);
    final t = Theme.of(context).textTheme;
    return PageFrame(
      onRefresh: load,
      children: [
        Text(
          'Settings',
          style: t.headlineSmall?.copyWith(fontWeight: FontWeight.w800),
        ),
        if (error != null) ...[
          const SizedBox(height: 12),
          ErrorBox(error!, onRetry: load),
        ],
        if (test != null) ...[
          const SectionTitle('Safety rules'),
          GlassCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _rule('Max per stock', pct(test!.maxPositionPct, sign: false)),
                _rule('Max stocks at once', '${test!.maxOpenPositions}'),
                _rule(
                  'Stop buying for the day after a loss of',
                  pct(test!.dailyLossPct, sign: false),
                ),
                _rule(
                  'Cash always kept aside',
                  pct(test!.cashReservePct, sign: false),
                ),
                const SizedBox(height: 6),
                Text(
                  'Test shown. You can change these and four more, each within the bot\'s fixed hard limits.',
                  style: t.bodySmall?.copyWith(color: AppColors.muted),
                ),
                const SizedBox(height: 12),
                Wrap(
                  spacing: 10,
                  runSpacing: 8,
                  children: [
                    FilledButton.tonalIcon(
                      onPressed: () => _openRules('test', test!.budget),
                      icon: const Icon(Icons.tune_rounded),
                      label: const Text('Edit Test rules'),
                    ),
                    if (live != null)
                      OutlinedButton.icon(
                        onPressed: () => _openRules('live', live!.budget),
                        icon: Icon(Icons.tune_rounded, color: AppColors.live),
                        label: const Text('Edit Live rules'),
                      ),
                  ],
                ),
              ],
            ),
          ),
        ],
        if (live != null) ...[
          const SectionTitle('Live (real money)'),
          GlassCard(
            glow: AppColors.live,
            child: Column(
              children: [
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Approve each trade'),
                  subtitle: const Text(
                    'Every real buy waits for your tap. Turn off for full auto. Protective sells never wait.',
                  ),
                  value: live!.approveMode,
                  activeThumbColor: AppColors.live,
                  onChanged: (v) => _confirmAuto(v),
                ),
                const Divider(),
                ListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Live budget cap'),
                  subtitle: Text(
                    live!.budget == null
                        ? 'Starter cap of \$1,000'
                        : 'At most ${moneyRound.format(live!.budget)} of real money',
                  ),
                  trailing: const Icon(Icons.edit_rounded),
                  onTap: () => _editCap(),
                ),
              ],
            ),
          ),
        ],
        if (test != null && live != null) ...[
          const SectionTitle('Trading hours'),
          GlassCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'Test works from Sunday 8pm to Friday 8pm New York time, overnight included. '
                  'Live works 4am to 8pm on weekdays. Outside regular hours (9:30am to 4pm) the '
                  'bot only uses limit orders and sells to protect your money. Overnight (8pm to '
                  '4am) it only trades stocks your broker trades overnight, and buys at most one '
                  'new stock a night. Penny stocks are only bought in regular hours.',
                  style: t.bodySmall?.copyWith(color: AppColors.muted),
                ),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Test: buy before and after hours'),
                  subtitle: const Text('New buys from 4am and until 8pm'),
                  value: test!.afterHoursBuys,
                  activeThumbColor: AppColors.test,
                  onChanged: (v) => save('test', {'after_hours_buys': v}),
                ),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Test: buy overnight'),
                  subtitle: const Text(
                    'New buys from 8pm to 4am. The thinnest trading of the day, so the bot '
                    'learns from these buys separately. Test only for now.',
                  ),
                  value: test!.overnightBuys,
                  activeThumbColor: AppColors.test,
                  onChanged: (v) => save('test', {'overnight_buys': v}),
                ),
                const Divider(),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Live: buy before and after hours'),
                  subtitle: const Text(
                    'Real money. Prices jump more outside regular hours, so try it in Test first.',
                  ),
                  value: live!.afterHoursBuys,
                  activeThumbColor: AppColors.live,
                  onChanged: (v) => save('live', {'after_hours_buys': v}),
                ),
              ],
            ),
          ),
          const SectionTitle('AI analyst costs'),
          GlassCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'With cheaper reviews, a fast low-cost Claude model looks at each stock first. '
                  'Only clear calls stand: a confident buy with no red flags, or a skip that names one. '
                  'Close calls and every penny stock still go to the full analyst.',
                  style: t.bodySmall?.copyWith(color: AppColors.muted),
                ),
                if (spending?.enabled ?? false) ...[
                  ListTile(
                    contentPadding: EdgeInsets.zero,
                    title: const Text('Monthly spending cap'),
                    subtitle: Text(
                      'About ${money.format(spending!.spent)} spent this month of '
                      '${moneyRound.format(spending!.cap ?? 0)}. At the cap the analyst pauses until '
                      'the 1st: no penny stocks, other buys on the rules alone.',
                    ),
                    trailing: const Icon(Icons.edit_rounded),
                    onTap: _editAiCap,
                  ),
                  const Divider(),
                ],
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Test: cheaper AI reviews'),
                  value: test!.quickReviews,
                  activeThumbColor: AppColors.test,
                  onChanged: (v) => save('test', {'quick_reviews': v}),
                ),
                const Divider(),
                SwitchListTile(
                  contentPadding: EdgeInsets.zero,
                  title: const Text('Live: cheaper AI reviews'),
                  subtitle: const Text(
                    'Try it in Test first and check the analyst\'s calls.',
                  ),
                  value: live!.quickReviews,
                  activeThumbColor: AppColors.live,
                  onChanged: (v) => save('live', {'quick_reviews': v}),
                ),
              ],
            ),
          ),
        ],
        if (test != null) ...[
          const SectionTitle('Your stock lists'),
          GlassCard(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                _listEditor(
                  'Always watch',
                  'Stocks the bot always checks',
                  test!.alwaysWatch,
                  AppColors.accent,
                  (v) async {
                    await save('test', {'always_watch': v});
                    await save('live', {'always_watch': v});
                  },
                ),
                const Divider(height: 28),
                _listEditor(
                  'Never buy',
                  'Stocks the bot must never buy',
                  test!.neverBuy,
                  AppColors.loss,
                  (v) async {
                    await save('test', {'never_buy': v});
                    await save('live', {'never_buy': v});
                  },
                ),
              ],
            ),
          ),
        ],
        const SectionTitle('App'),
        GlassCard(
          child: Column(
            children: [
              SwitchListTile(
                contentPadding: EdgeInsets.zero,
                title: const Text('Dark mode'),
                value: app.themeMode == ThemeMode.dark,
                onChanged: (_) => app.toggleTheme(),
              ),
              const Divider(),
              ListTile(
                contentPadding: EdgeInsets.zero,
                title: Text(app.demo ? 'Demo mode' : 'Bot server'),
                subtitle: Text(
                  app.demo ? 'Showing sample data' : app.serverUrl,
                ),
                trailing: TextButton(
                  onPressed: app.disconnect,
                  child: Text(app.demo ? 'Connect a server' : 'Disconnect'),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }

  Future<void> _openRules(String mode, double? budget) async {
    await Navigator.of(context).push(
      MaterialPageRoute<void>(
        builder: (_) => SafetyRulesScreen(mode: mode, budget: budget),
      ),
    );
    await load(); // show what changed
  }

  Widget _rule(String k, String v) => Padding(
    padding: const EdgeInsets.symmetric(vertical: 6),
    child: Row(
      children: [
        Expanded(child: Text(k)),
        Text(v, style: const TextStyle(fontWeight: FontWeight.w700)),
      ],
    ),
  );

  Future<void> _confirmAuto(bool approve) async {
    if (!approve) {
      final ok = await showDialog<bool>(
        context: context,
        builder: (c) => AlertDialog(
          title: const Text('Switch Live to full auto?'),
          content: const Text(
            'The bot will buy with real money without asking you first. All safety rules still apply.',
          ),
          actions: [
            TextButton(
              onPressed: () => Navigator.pop(c, false),
              child: const Text('Cancel'),
            ),
            FilledButton(
              onPressed: () => Navigator.pop(c, true),
              child: const Text('Turn on full auto'),
            ),
          ],
        ),
      );
      if (ok != true) return;
    }
    await save('live', {'approve_mode': approve});
  }

  Future<void> _editAiCap() async {
    final ctrl = TextEditingController(
      text: spending?.cap?.toStringAsFixed(0) ?? '20',
    );
    final result = await showDialog<String>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Monthly AI spending cap'),
        content: TextField(
          controller: ctrl,
          keyboardType: TextInputType.number,
          decoration: const InputDecoration(
            prefixText: '\$ ',
            hintText: 'For example 20',
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(c, ctrl.text),
            child: const Text('Save'),
          ),
        ],
      ),
    );
    if (result == null) return;
    final v = parseMoney(result.trim());
    if (v == null) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              '"${result.trim()}" isn\'t an amount. The cap was not changed.',
            ),
          ),
        );
      }
      return;
    }
    try {
      final s = await api.setAiCap(v);
      if (!mounted) return;
      setState(() => spending = s);
      ScaffoldMessenger.of(context)
          .showSnackBar(const SnackBar(content: Text('Saved')));
    } on ApiException catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(context)
            .showSnackBar(SnackBar(content: Text(e.message)));
      }
    }
  }

  Future<void> _editCap() async {
    final ctrl = TextEditingController(
      text: live!.budget?.toStringAsFixed(0) ?? '',
    );
    final result = await showDialog<String>(
      context: context,
      builder: (c) => AlertDialog(
        title: const Text('Live budget cap'),
        content: TextField(
          controller: ctrl,
          keyboardType: TextInputType.number,
          decoration: const InputDecoration(
            prefixText: '\$ ',
            hintText: 'For example 1000',
          ),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(c),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(c, ctrl.text),
            child: const Text('Save'),
          ),
        ],
      ),
    );
    if (result == null) return;
    final text = result.trim();
    final v = text.isEmpty ? null : parseMoney(text);
    if (v == null) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          SnackBar(
            content: Text(
              text.isEmpty
                  ? 'Live always needs a cap. The cap was not changed.'
                  : '"$text" isn\'t an amount. The cap was not changed.',
            ),
          ),
        );
      }
      return;
    }
    await save('live', {'budget': v});
  }

  Widget _listEditor(
    String title,
    String hint,
    List<String> items,
    Color color,
    Future<void> Function(List<String>) onSave,
  ) {
    final ctrl = TextEditingController();
    void add() {
      final s = ctrl.text.trim().toUpperCase();
      if (s.isEmpty || items.contains(s)) return;
      onSave([...items, s]);
    }

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(title, style: const TextStyle(fontWeight: FontWeight.w700)),
        Text(
          hint,
          style: Theme.of(context).textTheme.bodySmall
              ?.copyWith(color: AppColors.muted),
        ),
        const SizedBox(height: 10),
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            for (final s in items)
              InputChip(
                label: Text(s),
                side: BorderSide(color: color.withValues(alpha: 0.5)),
                onDeleted: () => onSave(items.where((x) => x != s).toList()),
              ),
            SizedBox(
              width: 120,
              child: TextField(
                controller: ctrl,
                textCapitalization: TextCapitalization.characters,
                onSubmitted: (_) => add(),
                decoration: const InputDecoration(
                  isDense: true,
                  hintText: 'Add ticker',
                  border: OutlineInputBorder(),
                ),
              ),
            ),
          ],
        ),
      ],
    );
  }
}

/// Reads amounts like "5000", "\$5,000", "5 000" or "5k". Null if it isn't one.
double? parseMoney(String text) {
  var t = text.toLowerCase().replaceAll(RegExp(r'[\s,\$]'), '');
  var scale = 1.0;
  if (t.endsWith('k')) {
    scale = 1000;
    t = t.substring(0, t.length - 1);
  }
  final v = double.tryParse(t);
  if (v == null || v < 0 || v.isNaN || v.isInfinite) return null;
  return v * scale;
}
