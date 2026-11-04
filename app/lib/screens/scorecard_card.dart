import 'package:flutter/material.dart';

import '../models.dart';
import '../theme.dart';
import '../widgets.dart';

/// "Were its calls right?": how the stocks the analyst approved and blocked
/// did over the month after each review, compared with the market.
class ScorecardCard extends StatelessWidget {
  const ScorecardCard(this.card, {super.key});
  final Scorecard card;

  @override
  Widget build(BuildContext context) {
    final t = Theme.of(context).textTheme;
    final muted = t.bodySmall?.copyWith(color: AppColors.muted);
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const SectionTitle('Were its calls right?'),
        GlassCard(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Text(
                'About a month (${card.horizonDays} trading days) after each review, '
                'compared with the market (SPY) over the same days.',
                style: muted,
              ),
              const SizedBox(height: 14),
              Row(
                children: [
                  Expanded(
                    child: _side(
                      context,
                      'It approved',
                      card.approved,
                      Icons.thumb_up_alt_rounded,
                      AppColors.gain,
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: _side(
                      context,
                      'It blocked',
                      card.blocked,
                      Icons.block_rounded,
                      AppColors.warn,
                    ),
                  ),
                ],
              ),
              const SizedBox(height: 14),
              Text(card.verdict, style: t.bodyMedium),
              if (card.missed.isNotEmpty || card.letThrough.isNotEmpty) ...[
                const SizedBox(height: 10),
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    for (final c in card.missed)
                      Pill(
                        'Blocked ${c.symbol}: ${pct(c.vsMarket)} vs market',
                        color: AppColors.muted,
                        icon: Icons.block_rounded,
                      ),
                    for (final c in card.letThrough)
                      Pill(
                        'Approved ${c.symbol}: ${pct(c.vsMarket)} vs market',
                        color: AppColors.muted,
                        icon: Icons.thumb_up_alt_rounded,
                      ),
                  ],
                ),
              ],
              if (card.waiting > 0) ...[
                const SizedBox(height: 10),
                Text(
                  '${card.waiting} newer call${card.waiting == 1 ? '' : 's'} will be graded once '
                  'a month has passed.',
                  style: muted,
                ),
              ],
            ],
          ),
        ),
      ],
    );
  }

  Widget _side(
    BuildContext context,
    String title,
    ScoreSide s,
    IconData icon,
    Color color,
  ) {
    final t = Theme.of(context).textTheme;
    final vs = s.avgVsMarket;
    return Container(
      padding: const EdgeInsets.all(12),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.08),
        borderRadius: BorderRadius.circular(14),
      ),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              Icon(icon, size: 15, color: color),
              const SizedBox(width: 6),
              Flexible(
                child: Text(
                  title,
                  style: t.labelMedium?.copyWith(
                    color: color,
                    fontWeight: FontWeight.w700,
                  ),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Text(
            vs == null ? '—' : pct(vs),
            style: TextStyle(
              fontSize: 22,
              fontWeight: FontWeight.w800,
              color: vs == null ? null : AppColors.change(vs),
            ),
          ),
          Text(
            vs == null
                ? 'no graded calls yet'
                : 'vs market · ${s.calls} stock${s.calls == 1 ? '' : 's'}',
            style: t.bodySmall?.copyWith(color: AppColors.muted),
          ),
        ],
      ),
    );
  }
}
