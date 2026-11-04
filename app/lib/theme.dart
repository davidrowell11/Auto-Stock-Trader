import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

/// Brand colors. Gains are green, losses red, one blue accent.
///
/// Each color has a dark-theme and a light-theme shade, both picked to pass
/// WCAG AA contrast (4.5:1) as text on that theme's background.
class AppColors {
  /// Set by the app whenever the theme changes.
  static bool dark = true;

  static Color _pick(int d, int l) => Color(dark ? d : l);

  static Color get gain => _pick(0xFF22D3A0, 0xFF047857);
  static Color get loss => _pick(0xFFFF6B6B, 0xFFC62828);
  static Color get accent => _pick(0xFF4C8DFF, 0xFF2563EB);
  static Color get warn => _pick(0xFFFFB547, 0xFFA15C00);
  static Color get test =>
      _pick(0xFF9B8CFF, 0xFF5B4BC4); // Test (fake money) badge
  static Color get live =>
      _pick(0xFFFF8A3D, 0xFFC2410C); // Live (real money) badge
  /// Secondary text (labels, captions).
  static Color get muted => _pick(0xFF9AA3B2, 0xFF5B6475);

  /// Text on a gain, loss or accent background.
  static Color get onColor => dark ? const Color(0xFF0B1120) : Colors.white;

  static const darkBg = Color(0xFF070B14);
  static const darkSurface = Color(0xFF111827);
  static const lightBg = Color(0xFFF4F6FB);
  static const lightSurface = Colors.white;

  static Color change(num value) => value >= 0 ? gain : loss;
}

ThemeData buildTheme(Brightness brightness) {
  final dark = brightness == Brightness.dark;
  final accent = Color(dark ? 0xFF4C8DFF : 0xFF2563EB);
  final scheme = ColorScheme.fromSeed(
    seedColor: accent,
    brightness: brightness,
    primary: accent,
    onPrimary: dark ? const Color(0xFF0B1120) : Colors.white,
    surface: dark ? AppColors.darkSurface : AppColors.lightSurface,
  );
  final base = ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    brightness: brightness,
  );
  final text = _steadyDigits(
    GoogleFonts.interTextTheme(base.textTheme).apply(
      bodyColor: dark ? Colors.white : const Color(0xFF0E1525),
      displayColor: dark ? Colors.white : const Color(0xFF0E1525),
    ),
  );
  return base.copyWith(
    scaffoldBackgroundColor: dark ? AppColors.darkBg : AppColors.lightBg,
    textTheme: text,
    appBarTheme: AppBarTheme(
      backgroundColor: Colors.transparent,
      elevation: 0,
      scrolledUnderElevation: 0,
      titleTextStyle: text.titleLarge?.copyWith(fontWeight: FontWeight.w700),
    ),
    navigationBarTheme: NavigationBarThemeData(
      backgroundColor: dark ? const Color(0xFF0B1120) : Colors.white,
      indicatorColor: accent.withValues(alpha: 0.18),
      labelTextStyle: WidgetStatePropertyAll(
        text.labelSmall?.copyWith(fontWeight: FontWeight.w600),
      ),
    ),
    navigationRailTheme: NavigationRailThemeData(
      backgroundColor: dark ? const Color(0xFF0B1120) : Colors.white,
      indicatorColor: accent.withValues(alpha: 0.18),
    ),
    sliderTheme: SliderThemeData(activeTrackColor: accent, thumbColor: accent),
    dividerColor: dark ? Colors.white10 : Colors.black12,
  );
}

/// Every digit the same width, so prices and totals line up in columns and
/// don't jiggle as they update.
TextTheme _steadyDigits(TextTheme t) {
  const f = [FontFeature.tabularFigures()];
  TextStyle? s(TextStyle? x) => x?.copyWith(fontFeatures: f);
  return t.copyWith(
    displayLarge: s(t.displayLarge),
    displayMedium: s(t.displayMedium),
    displaySmall: s(t.displaySmall),
    headlineLarge: s(t.headlineLarge),
    headlineMedium: s(t.headlineMedium),
    headlineSmall: s(t.headlineSmall),
    titleLarge: s(t.titleLarge),
    titleMedium: s(t.titleMedium),
    titleSmall: s(t.titleSmall),
    bodyLarge: s(t.bodyLarge),
    bodyMedium: s(t.bodyMedium),
    bodySmall: s(t.bodySmall),
    labelLarge: s(t.labelLarge),
    labelMedium: s(t.labelMedium),
    labelSmall: s(t.labelSmall),
  );
}
