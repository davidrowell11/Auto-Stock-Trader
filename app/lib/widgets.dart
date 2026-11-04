import 'dart:math';

import 'package:fl_chart/fl_chart.dart';
import 'package:flutter/material.dart';
import 'package:intl/intl.dart';

import 'models.dart';
import 'theme.dart';

final money = NumberFormat.simpleCurrency(decimalDigits: 2);
final moneyRound = NumberFormat.simpleCurrency(decimalDigits: 0);
String pct(num v, {bool sign = true}) =>
    '${sign && v >= 0 ? '+' : ''}${(v * 100).toStringAsFixed(2)}%';
String signedMoney(num v) => '${v >= 0 ? '+' : '-'}${money.format(v.abs())}';

String ago(DateTime t) {
  final d = DateTime.now().difference(t.toLocal());
  if (d.inMinutes < 1) return 'just now';
  if (d.inMinutes < 60) return '${d.inMinutes}m ago';
  if (d.inHours < 24) return '${d.inHours}h ago';
  return '${d.inDays}d ago';
}

bool isDark(BuildContext c) => Theme.of(c).brightness == Brightness.dark;

/// The frosted "glass" card used everywhere. A [glow] card (the main account
/// card, anything waiting for you) gets a lit gradient edge in that color.
class GlassCard extends StatelessWidget {
  const GlassCard({
    super.key,
    required this.child,
    this.padding = const EdgeInsets.all(18),
    this.onTap,
    this.glow,
  });

  final Widget child;
  final EdgeInsets padding;
  final VoidCallback? onTap;
  final Color? glow;

  static const radius = 20.0;

  @override
  Widget build(BuildContext context) {
    final dark = isDark(context);
    final fill = LinearGradient(
      begin: Alignment.topLeft,
      end: Alignment.bottomRight,
      colors: dark
          ? [
              Color.lerp(
                const Color(0xFF151B2E),
                glow,
                glow == null ? 0 : 0.18,
              )!,
              const Color(0xFF0D1220),
            ]
          : [
              Colors.white,
              Color.lerp(Colors.white, glow, glow == null ? 0 : 0.04)!,
            ],
    );
    final card = Container(
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(radius - (glow != null ? 1.2 : 0)),
        gradient: fill,
        border: glow != null
            ? null
            : Border.all(
                color: (dark ? Colors.white : Colors.black).withValues(
                  alpha: dark ? 0.07 : 0.06,
                ),
              ),
      ),
      // A faint highlight along the top edge, like light on glass.
      foregroundDecoration: dark
          ? BoxDecoration(
              borderRadius: BorderRadius.circular(radius),
              gradient: LinearGradient(
                begin: Alignment.topCenter,
                end: Alignment.center,
                colors: [
                  Colors.white.withValues(alpha: 0.05),
                  Colors.white.withValues(alpha: 0),
                ],
              ),
            )
          : null,
      child: Material(
        type: MaterialType.transparency,
        child: InkWell(
          borderRadius: BorderRadius.circular(radius),
          onTap: onTap,
          child: Padding(padding: padding, child: child),
        ),
      ),
    );
    return Container(
      padding: glow != null ? const EdgeInsets.all(1.2) : EdgeInsets.zero,
      decoration: BoxDecoration(
        borderRadius: BorderRadius.circular(radius),
        gradient: glow == null
            ? null
            : LinearGradient(
                begin: Alignment.topLeft,
                end: Alignment.bottomRight,
                colors: [
                  glow!.withValues(alpha: 0.85),
                  (glow == AppColors.accent ? AppColors.accent2 : glow!)
                      .withValues(alpha: 0.25),
                  glow!.withValues(alpha: 0.10),
                ],
              ),
        boxShadow: [
          if (glow != null)
            BoxShadow(
              color: glow!.withValues(alpha: dark ? 0.22 : 0.16),
              blurRadius: 28,
              spreadRadius: -6,
              offset: const Offset(0, 8),
            ),
          if (!dark)
            BoxShadow(
              color: const Color(0xFF1E1B4B).withValues(alpha: 0.06),
              blurRadius: 20,
              offset: const Offset(0, 8),
            ),
        ],
      ),
      child: card,
    );
  }
}

/// The soft colored glow behind every screen: blue top left, violet top right,
/// a hint of green low down. Purely decoration.
class AuroraBackground extends StatelessWidget {
  const AuroraBackground({super.key, required this.child});
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final dark = isDark(context);
    Widget blob(Alignment at, Color c, double alpha, double size) => Align(
      alignment: at,
      child: IgnorePointer(
        child: Container(
          width: size,
          height: size,
          decoration: BoxDecoration(
            shape: BoxShape.circle,
            gradient: RadialGradient(
              colors: [
                c.withValues(alpha: alpha),
                c.withValues(alpha: 0),
              ],
            ),
          ),
        ),
      ),
    );
    final k = dark ? 1.0 : 0.55;
    return Stack(
      fit: StackFit.expand,
      children: [
        ColoredBox(color: dark ? AppColors.darkBg : AppColors.lightBg),
        Positioned.fill(
          child: ClipRect(
            child: OverflowBox(
              maxWidth: double.infinity,
              maxHeight: double.infinity,
              child: SizedBox(
                width: MediaQuery.sizeOf(context).width + 400,
                height: MediaQuery.sizeOf(context).height + 400,
                child: Stack(
                  children: [
                    blob(
                      const Alignment(-1, -1),
                      AppColors.accent,
                      0.34 * k,
                      700,
                    ),
                    blob(
                      const Alignment(1, -0.85),
                      AppColors.accent2,
                      0.28 * k,
                      620,
                    ),
                    blob(
                      const Alignment(0.2, 1),
                      AppColors.gain,
                      0.10 * k,
                      700,
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
        child,
      ],
    );
  }
}

class ModeBadge extends StatelessWidget {
  const ModeBadge(this.mode, {super.key, this.demo = false});
  final String mode;
  final bool demo;

  @override
  Widget build(BuildContext context) {
    final live = mode == 'live';
    final color = live ? AppColors.live : AppColors.test;
    final label =
        '${live ? 'LIVE · real money' : 'TEST · fake money'}${demo ? ' · demo' : ''}';
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 4),
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.16),
        borderRadius: BorderRadius.circular(99),
      ),
      child: Text(
        label,
        style: TextStyle(
          color: color,
          fontSize: 11,
          fontWeight: FontWeight.w700,
          letterSpacing: 0.6,
        ),
      ),
    );
  }
}

class Pill extends StatelessWidget {
  const Pill(this.text, {super.key, required this.color, this.icon});
  final String text;
  final Color color;
  final IconData? icon;

  @override
  Widget build(BuildContext context) => LayoutBuilder(
    // Shrinks with an ellipsis where its width is limited (a Wrap, a narrow card); beside
    // other widgets in a Row it keeps its natural width.
    builder: (context, c) {
      final label = Text(
        text,
        overflow: TextOverflow.ellipsis,
        style: TextStyle(
          color: color,
          fontSize: 12,
          fontWeight: FontWeight.w600,
        ),
      );
      return Container(
        padding: const EdgeInsets.symmetric(horizontal: 9, vertical: 4),
        decoration: BoxDecoration(
          color: color.withValues(alpha: 0.14),
          borderRadius: BorderRadius.circular(99),
        ),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (icon != null) ...[
              Icon(icon, size: 13, color: color),
              const SizedBox(width: 4),
            ],
            c.hasBoundedWidth ? Flexible(child: label) : label,
          ],
        ),
      );
    },
  );
}

class SectionTitle extends StatelessWidget {
  const SectionTitle(this.text, {super.key, this.trailing});
  final String text;
  final Widget? trailing;

  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.fromLTRB(4, 22, 4, 10),
    child: Row(
      children: [
        Container(
          width: 4,
          height: 16,
          margin: const EdgeInsets.only(right: 10),
          decoration: BoxDecoration(
            gradient: AppColors.accentGradient,
            borderRadius: BorderRadius.circular(2),
          ),
        ),
        Expanded(
          child: Text(
            text,
            style: Theme.of(context).textTheme.titleMedium
                ?.copyWith(fontWeight: FontWeight.w700),
          ),
        ),
        ?trailing,
      ],
    ),
  );
}

/// Smooth account-value chart with an optional benchmark (SPY) line.
class EquityChart extends StatelessWidget {
  const EquityChart({
    super.key,
    required this.points,
    this.benchmark = const [],
    this.height = 190,
  });

  final List<EquityPoint> points;
  final List<EquityPoint>
  benchmark; // already scaled to the same starting value
  final double height;

  @override
  Widget build(BuildContext context) {
    if (points.length < 2) {
      return SizedBox(
        height: height,
        child: const Center(child: Text('The chart fills in as the bot runs.')),
      );
    }
    final up = points.last.equity >= points.first.equity;
    final color = up ? AppColors.gain : AppColors.loss;
    final t0 = points.first.ts.millisecondsSinceEpoch.toDouble();
    FlSpot spot(EquityPoint p) =>
        FlSpot((p.ts.millisecondsSinceEpoch - t0) / 8.64e7, p.equity);
    final all = [...points, ...benchmark].map((p) => p.equity);
    final minY = all.reduce(min), maxY = all.reduce(max);
    final pad = (maxY - minY) * 0.12 + 1;
    final change = points.last.equity / points.first.equity - 1;
    final label =
        'Account value chart: \$${points.first.equity.toStringAsFixed(0)} to '
        '\$${points.last.equity.toStringAsFixed(0)}, '
        '${up ? 'up' : 'down'} ${(change.abs() * 100).toStringAsFixed(1)}%';
    return Semantics(
      label: label,
      excludeSemantics: true,
      child: SizedBox(
        height: height,
        child: LineChart(
          LineChartData(
            minY: minY - pad,
            maxY: maxY + pad,
            gridData: const FlGridData(show: false),
            borderData: FlBorderData(show: false),
            titlesData: const FlTitlesData(show: false),
            lineTouchData: LineTouchData(
              touchTooltipData: LineTouchTooltipData(
                getTooltipColor: (_) =>
                    isDark(context) ? const Color(0xFF1C2536) : Colors.white,
                getTooltipItems: (spots) => [
                  for (final s in spots)
                    LineTooltipItem(
                      moneyRound.format(s.y),
                      TextStyle(
                        color: s.barIndex == 0 ? color : AppColors.muted,
                        fontWeight: FontWeight.w700,
                      ),
                    ),
                ],
              ),
            ),
            lineBarsData: [
              LineChartBarData(
                spots: points.map(spot).toList(),
                isCurved: true,
                curveSmoothness: 0.25,
                color: color,
                barWidth: 2.6,
                dotData: const FlDotData(show: false),
                belowBarData: BarAreaData(
                  show: true,
                  gradient: LinearGradient(
                    begin: Alignment.topCenter,
                    end: Alignment.bottomCenter,
                    colors: [
                      color.withValues(alpha: 0.28),
                      color.withValues(alpha: 0.0),
                    ],
                  ),
                ),
              ),
              if (benchmark.length > 1)
                LineChartBarData(
                  spots: benchmark.map(spot).toList(),
                  isCurved: true,
                  color: Colors.grey.withValues(alpha: 0.7),
                  barWidth: 1.4,
                  dashArray: [5, 4],
                  dotData: const FlDotData(show: false),
                ),
            ],
          ),
          duration: const Duration(milliseconds: 600),
        ),
      ),
    );
  }
}

class ChangeText extends StatelessWidget {
  const ChangeText({
    super.key,
    required this.amount,
    required this.percent,
    this.style,
  });
  final double amount, percent;
  final TextStyle? style;

  @override
  Widget build(BuildContext context) {
    final c = AppColors.change(amount);
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: [
        Icon(
          amount >= 0
              ? Icons.arrow_upward_rounded
              : Icons.arrow_downward_rounded,
          size: 16,
          color: c,
        ),
        const SizedBox(width: 2),
        Text(
          '${signedMoney(amount)} (${pct(percent)})',
          style: (style ?? const TextStyle()).copyWith(
            color: c,
            fontWeight: FontWeight.w600,
          ),
        ),
      ],
    );
  }
}

class ErrorBox extends StatelessWidget {
  const ErrorBox(this.message, {super.key, this.onRetry});
  final String message;
  final VoidCallback? onRetry;

  @override
  Widget build(BuildContext context) => GlassCard(
    glow: AppColors.loss,
    child: Row(
      children: [
        Icon(Icons.error_outline_rounded, color: AppColors.loss),
        const SizedBox(width: 12),
        Expanded(child: Text(message)),
        if (onRetry != null)
          TextButton(onPressed: onRetry, child: const Text('Retry')),
      ],
    ),
  );
}

/// Centers content and caps its width so the app looks right on phones and desktops alike.
/// Page layout. One column on phones and tablets; on wide screens, [side]
/// becomes a second column so desktops use their space.
class PageFrame extends StatelessWidget {
  const PageFrame({
    super.key,
    required this.children,
    this.side = const [],
    this.onRefresh,
  });
  final List<Widget> children;
  final List<Widget> side;
  final Future<void> Function()? onRefresh;

  static const wideBreakpoint = 1180.0;

  @override
  Widget build(BuildContext context) {
    final wide =
        side.isNotEmpty && MediaQuery.sizeOf(context).width >= wideBreakpoint;
    Widget column(List<Widget> c) =>
        Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: c);
    final list = ListView(
      padding: const EdgeInsets.fromLTRB(16, 8, 16, 32),
      children: [
        Center(
          child: ConstrainedBox(
            constraints: BoxConstraints(maxWidth: wide ? 1320 : 760),
            child: wide
                ? Row(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Expanded(flex: 3, child: column(children)),
                      const SizedBox(width: 20),
                      Expanded(flex: 2, child: column(side)),
                    ],
                  )
                : column([...children, ...side]),
          ),
        ),
      ],
    );
    return onRefresh == null
        ? list
        : RefreshIndicator(onRefresh: onRefresh!, child: list);
  }
}

/// A soft pulsing placeholder shown while a card's data loads.
class SkeletonCard extends StatefulWidget {
  const SkeletonCard({super.key, this.height = 120, this.lines = 3});
  final double height;
  final int lines;

  @override
  State<SkeletonCard> createState() => _SkeletonCardState();
}

class _SkeletonCardState extends State<SkeletonCard>
    with SingleTickerProviderStateMixin {
  late final AnimationController pulse = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1100),
  )..repeat(reverse: true);

  @override
  void dispose() {
    pulse.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final base = (isDark(context) ? Colors.white : Colors.black);
    Widget bar(double widthFactor) => FractionallySizedBox(
      widthFactor: widthFactor,
      alignment: Alignment.centerLeft,
      child: Container(
        height: 12,
        decoration: BoxDecoration(
          color: base.withValues(alpha: 0.08),
          borderRadius: BorderRadius.circular(6),
        ),
      ),
    );
    return Semantics(
      label: 'Loading',
      child: FadeTransition(
        opacity: Tween(begin: 0.45, end: 1.0).animate(pulse),
        child: GlassCard(
          child: SizedBox(
            height: widget.height - 36,
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisAlignment: MainAxisAlignment.spaceEvenly,
              children: [
                for (var i = 0; i < widget.lines; i++)
                  bar(i == 0 ? 0.45 : (i.isEven ? 0.7 : 0.9)),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

/// Three bouncing dots: the analyst is writing.
class TypingDots extends StatefulWidget {
  const TypingDots({super.key});

  @override
  State<TypingDots> createState() => _TypingDotsState();
}

class _TypingDotsState extends State<TypingDots>
    with SingleTickerProviderStateMixin {
  late final AnimationController c = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1200),
  )..repeat();

  @override
  void dispose() {
    c.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => Semantics(
    label: 'The analyst is writing',
    child: SizedBox(
      height: 20,
      width: 38,
      child: AnimatedBuilder(
        animation: c,
        builder: (context, _) => Row(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          children: [
            for (var i = 0; i < 3; i++)
              Transform.translate(
                offset: Offset(
                  0,
                  -4 * max(0, sin((c.value * 2 * pi) - i * 0.9)).toDouble(),
                ),
                child: Container(
                  width: 8,
                  height: 8,
                  decoration: BoxDecoration(
                    color: AppColors.muted,
                    shape: BoxShape.circle,
                  ),
                ),
              ),
          ],
        ),
      ),
    ),
  );
}
