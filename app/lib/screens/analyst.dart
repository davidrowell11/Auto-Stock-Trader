import 'package:flutter/material.dart';

import '../api.dart';
import '../models.dart';
import '../state.dart';
import '../theme.dart';
import '../widgets.dart';
import 'lab_card.dart';
import 'learning_card.dart';
import 'scorecard_card.dart';

/// The AI analyst: its scorecard, its recent calls, and a chat box.
class AnalystScreen extends StatefulWidget {
  const AnalystScreen({super.key});

  @override
  State<AnalystScreen> createState() => _AnalystScreenState();
}

class _AnalystScreenState extends State<AnalystScreen> {
  String mode = 'test';
  AnalystFeed? feed;
  Scorecard? scorecard;
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
    try {
      final l = await api.learning(mode);
      if (mounted) setState(() => scorecard = l.scorecard);
    } on ApiException {
      // The scorecard is extra; the learning card below shows its own error.
    }
  }

  Future<void> send() async {
    final q = question.text.trim();
    if (q.isEmpty || thinking) return;
    setState(() {
      chat.add((true, q));
      chat.add((false, ''));
      thinking = true;
      question.clear();
    });
    final at = chat.length - 1;
    var answer = '';
    try {
      await for (final piece in api.askStream(q, mode)) {
        answer += piece;
        if (mounted) setState(() => chat[at] = (false, answer));
      }
      if (answer.trim().isEmpty && mounted) {
        setState(
          () => chat[at] = (
            false,
            "Sorry, I couldn't come up with an answer. Try asking another way.",
          ),
        );
      }
    } on ApiException catch (e) {
      final text = answer.isEmpty ? e.message : '$answer\n\n(${e.message})';
      if (mounted) setState(() => chat[at] = (false, text));
    } finally {
      if (mounted) setState(() => thinking = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final f = feed;
    // Wide screens keep the chat beside everything else; phones show it above the recent calls.
    final wide = MediaQuery.sizeOf(context).width >= PageFrame.wideBreakpoint;
    return PageFrame(
      onRefresh: load,
      side: [if (wide) _chatBox(t)],
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
        if (f == null && error == null) const SkeletonCard(height: 96),
        if (f != null)
          GlassCard(
            glow: AppColors.accent,
            child: Row(
              children: [
                _score(
                  '${f.total}',
                  'stocks reviewed',
                  Icons.manage_search_rounded,
                  AppColors.accent,
                ),
                _score(
                  '${f.approved}',
                  'approved',
                  Icons.thumb_up_alt_rounded,
                  AppColors.gain,
                ),
                _score(
                  '${f.total - f.approved}',
                  'blocked',
                  Icons.block_rounded,
                  AppColors.warn,
                ),
              ],
            ),
          ),
        if (f != null && f.quick > 0)
          Padding(
            padding: const EdgeInsets.only(top: 8, left: 4),
            child: Text(
              '${f.total == 0 ? 0 : (100 * f.approved / f.total).round()}% approved · '
              '${f.quick} of ${f.total} settled by the cheaper quick review',
              style: t.bodySmall?.copyWith(color: AppColors.muted),
            ),
          ),
        if (scorecard != null) ScorecardCard(scorecard!),
        LearningCard(key: ValueKey(mode), mode: mode),
        LabCard(key: ValueKey('lab-$mode'), mode: mode),
        if (!wide) _chatBox(t),
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

  Widget _chatBox(TextTheme t) => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      const SectionTitle('Ask the analyst'),
      GlassCard(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (chat.isEmpty) ...[
              Text(
                'Ask about your trades or any stock. It sees your positions and can search today\'s news.',
                style: t.bodySmall?.copyWith(color: AppColors.muted),
              ),
              const SizedBox(height: 10),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  for (final s in const [
                    'How am I doing this week?',
                    'What do you think of NVDA?',
                    'Why did you skip the last stock?',
                  ])
                    ActionChip(
                      label: Text(s),
                      onPressed: thinking
                          ? null
                          : () {
                              question.text = s;
                              send();
                            },
                    ),
                ],
              ),
            ],
            for (final (i, (fromUser, text)) in chat.indexed)
              _bubble(
                fromUser,
                text,
                writing: thinking && i == chat.length - 1,
              ),
            const SizedBox(height: 10),
            Row(
              children: [
                Expanded(
                  child: TextField(
                    controller: question,
                    onSubmitted: (_) => send(),
                    textInputAction: TextInputAction.send,
                    minLines: 1,
                    maxLines: 4,
                    decoration: InputDecoration(
                      hintText: 'Ask about a stock or a trade',
                      filled: true,
                      contentPadding: const EdgeInsets.symmetric(
                        horizontal: 16,
                        vertical: 12,
                      ),
                      border: OutlineInputBorder(
                        borderRadius: BorderRadius.circular(22),
                        borderSide: BorderSide.none,
                      ),
                    ),
                  ),
                ),
                const SizedBox(width: 8),
                IconButton.filled(
                  tooltip: 'Send question',
                  onPressed: thinking ? null : send,
                  icon: const Icon(Icons.arrow_upward_rounded),
                ),
              ],
            ),
          ],
        ),
      ),
    ],
  );

  Widget _score(String big, String small, IconData icon, Color color) =>
      Expanded(
        child: Column(
          children: [
            Icon(icon, size: 18, color: color),
            const SizedBox(height: 6),
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

  Widget _bubble(bool fromUser, String text, {bool writing = false}) {
    final bubble = Container(
      margin: const EdgeInsets.symmetric(vertical: 4),
      padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
      constraints: const BoxConstraints(maxWidth: 520),
      decoration: BoxDecoration(
        color: fromUser
            ? AppColors.accent
            : Colors.grey.withValues(alpha: 0.14),
        borderRadius: BorderRadius.only(
          topLeft: const Radius.circular(18),
          topRight: const Radius.circular(18),
          bottomLeft: Radius.circular(fromUser ? 18 : 4),
          bottomRight: Radius.circular(fromUser ? 4 : 18),
        ),
      ),
      child: text.isEmpty && writing
          ? const TypingDots()
          : SelectableText(
              text,
              style: TextStyle(
                color: fromUser ? AppColors.onColor : null,
                height: 1.4,
              ),
            ),
    );
    if (fromUser) {
      return Align(alignment: Alignment.centerRight, child: bubble);
    }
    return Row(
      crossAxisAlignment: CrossAxisAlignment.end,
      children: [
        Padding(
          padding: const EdgeInsets.only(right: 8, bottom: 6),
          child: CircleAvatar(
            radius: 13,
            backgroundColor: AppColors.accent.withValues(alpha: 0.16),
            child: Icon(
              Icons.auto_awesome_rounded,
              size: 14,
              color: AppColors.accent,
            ),
          ),
        ),
        Flexible(child: bubble),
      ],
    );
  }

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
              if (v.isQuick) ...[
                Pill('Quick review', color: AppColors.muted),
                const SizedBox(width: 6),
              ],
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
