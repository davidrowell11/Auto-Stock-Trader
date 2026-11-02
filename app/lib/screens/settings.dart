import 'package:flutter/material.dart';

import '../api.dart';
import '../models.dart';
import '../state.dart';
import '../theme.dart';
import '../widgets.dart';

class SettingsScreen extends StatefulWidget {
  const SettingsScreen({super.key});

  @override
  State<SettingsScreen> createState() => _SettingsScreenState();
}

class _SettingsScreenState extends State<SettingsScreen> {
  BotSettings? test, live;
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
                        ? 'No cap: the bot may use the whole account'
                        : 'At most ${moneyRound.format(live!.budget)} of real money',
                  ),
                  trailing: const Icon(Icons.edit_rounded),
                  onTap: () => _editCap(),
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
          const SectionTitle('Safety rules'),
          GlassCard(
            child: Column(
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
                  'These are capped by hard limits in the bot that nothing can loosen.',
                  style: t.bodySmall?.copyWith(color: Colors.grey),
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
            hintText: 'Leave empty for no cap',
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
    final v = double.tryParse(result.replaceAll(',', '').trim());
    await save('live', v == null ? {'clear_budget': true} : {'budget': v});
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
              ?.copyWith(color: Colors.grey),
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
