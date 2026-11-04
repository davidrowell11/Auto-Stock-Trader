import 'package:flutter/material.dart';

import '../api.dart';
import '../models.dart';
import '../state.dart';
import '../theme.dart';
import '../widgets.dart';
import 'learning_card.dart';

/// The AI analyst: its scorecard, its recent calls, and a chat box.
class AnalystScreen extends StatefulWidget {
  const AnalystScreen({super.key});

  @override
  State<AnalystScreen> createState() => _AnalystScreenState();
}

class _AnalystScreenState extends State<AnalystScreen> {
  String mode = 'test';
  AnalystFeed? feed;
  String? error;
  final question = TextEditingController();
  final chat = <(bool fromUser, String text)>[];
  bool thinking = false;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addPostFrameCallback((_) => load());
  }

  @override
  void dispose() {
    question.dispose();
    super.dispose();
  }

  TraderApi get api => AppScope.of(context).api!;

  Future<void> load() async {
    try {
      final f = await api.analyst(mode);
      if (mounted) {
        setState(() {
          feed = f;
          error = null;
        });
      }
    } on ApiException catch (e) {
      if (mounted) setState(() => error = e.message);
    }
  }

  Future<void> send() async {
    final q = question.text.trim();
    if (q.isEmpty || thinking) return;
    setState(() {
      chat.add((true, q));
      thinking = true;
      question.clear();
    });
    try {
      final a = await api.ask(q, mode);
      if (mounted) setState(() => chat.add((false, a)));
    } on ApiException catch (e) {
      if (mounted) setState(() => chat.add((false, e.message)));
    } finally {
      if (mounted) setState(() => thinking = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final f = feed;
    return PageFrame(
      onRefresh: load,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                'AI analyst',
                style: t.headlineSmall?.copyWith(fontWeight: FontWeight.w800),
              ),
            ),
            SegmentedButton<String>(
              segments: const [
                ButtonSegment(value: 'test', label: Text('Test')),
                ButtonSegment(value: 'live', label: Text('Live')),
              ],
              selected: {mode},
              onSelectionChanged: (s) {
                setState(() => mode = s.first);
                load();
              },
            ),
          ],
        ),
        const SizedBox(height: 6),
        Text(
          'Claude reviews every buy the rules want to make, checks the news, and can veto it.',
          style: t.bodySmall?.copyWith(color: AppColors.muted),
        ),
        const SizedBox(height: 14),
        if (error != null) ErrorBox(error!, onRetry: load),
        if (f != null)
          GlassCard(
            glow: AppColors.accent,
            child: Row(
              children: [
                _score('${f.total}', 'stocks reviewed'),
                _score('${f.approved}', 'approved'),
                _score(
                  f.total == 0
                      ? '—'
                      : '${(100 * f.approved / f.total).round()}%',
                  'approval rate',
                ),
              ],
            ),
          ),
        LearningCard(key: ValueKey(mode), mode: mode),
        const SectionTitle('Ask the analyst'),
        GlassCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (chat.isEmpty)
                Text(
                  'Try "Why did you sell Tesla?" or "What do you think of NVDA?"',
                  style: t.bodySmall?.copyWith(color: AppColors.muted),
                ),
              for (final (fromUser, text) in chat) _bubble(fromUser, text),
              if (thinking)
                const Padding(
                  padding: EdgeInsets.all(8),
                  child: LinearProgressIndicator(),
                ),
              const SizedBox(height: 10),
              Row(
                children: [
                  Expanded(
                    child: TextField(
                      controller: question,
                      onSubmitted: (_) => send(),
                      decoration: InputDecoration(
                        hintText: 'Ask about a stock or a trade',
                        filled: true,
                        border: OutlineInputBorder(
                          borderRadius: BorderRadius.circular(14),
                          borderSide: BorderSide.none,
                        ),
                      ),
                    ),
                  ),
                  const SizedBox(width: 8),
                  IconButton.filled(
                    tooltip: 'Send question',
                    onPressed: send,
                    icon: const Icon(Icons.arrow_upward_rounded),
                  ),
                ],
              ),
            ],
          ),
        ),
        const SectionTitle('Recent calls'),
        if (f != null && f.views.isEmpty)
          const GlassCard(
            child: Text(
              'No reviews yet. They appear when the bot finds a candidate.',
            ),
          ),
        if (f != null)
          for (final v in f.views)
            Padding(
              padding: const EdgeInsets.only(bottom: 10),
              child: _viewCard(v),
            ),
      ],
    );
  }

  Widget _score(String big, String small) => Expanded(
    child: Column(
      children: [
        Text(
          big,
          style: const TextStyle(fontSize: 24, fontWeight: FontWeight.w800),
        ),
        Text(
          small,
          style: Theme.of(context).textTheme.bodySmall
              ?.copyWith(color: AppColors.muted),
        ),
      ],
    ),
  );

  Widget _bubble(bool fromUser, String text) => Align(
    alignment: fromUser ? Alignment.centerRight : Alignment.centerLeft,
    child: Container(
      margin: const EdgeInsets.symmetric(vertical: 4),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      constraints: const BoxConstraints(maxWidth: 520),
      decoration: BoxDecoration(
        color: fromUser
            ? AppColors.accent
            : Colors.grey.withValues(alpha: 0.14),
        borderRadius: BorderRadius.circular(16),
      ),
      child: Text(
        text,
        style: TextStyle(color: fromUser ? Colors.white : null),
      ),
    ),
  );

  Widget _viewCard(AnalystView v) {
    final color = v.isBuy ? AppColors.gain : AppColors.warn;
    return GlassCard(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Text(
                v.symbol,
                style: const TextStyle(
                  fontWeight: FontWeight.w800,
                  fontSize: 16,
                ),
              ),
              const SizedBox(width: 8),
              Text(
                money.format(v.price),
                style: Theme.of(context).textTheme.bodySmall
                    ?.copyWith(color: AppColors.muted),
              ),
              const Spacer(),
              Pill(
                v.isBuy ? 'Buy' : 'Skip',
                color: color,
                icon: v.isBuy
                    ? Icons.thumb_up_alt_rounded
                    : Icons.block_rounded,
              ),
            ],
          ),
          const SizedBox(height: 10),
          Row(
            children: [
              Expanded(
                child: ClipRRect(
                  borderRadius: BorderRadius.circular(99),
                  child: LinearProgressIndicator(
                    value: v.confidence,
                    minHeight: 6,
                    color: color,
                    backgroundColor: Colors.grey.withValues(alpha: 0.18),
                  ),
                ),
              ),
              const SizedBox(width: 10),
              Text(
                '${(v.confidence * 100).round()}% confident',
                style: Theme.of(context).textTheme.bodySmall,
              ),
            ],
          ),
          const SizedBox(height: 10),
          Text(v.reasoning),
          const SizedBox(height: 6),
          Text(
            v.day,
            style: Theme.of(context).textTheme.bodySmall
                ?.copyWith(color: AppColors.muted),
          ),
        ],
      ),
    );
  }
}
