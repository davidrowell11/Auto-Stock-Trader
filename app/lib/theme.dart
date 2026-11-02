import 'package:flutter/material.dart';
import 'package:google_fonts/google_fonts.dart';

/// Brand colors. Gains are green, losses coral red, one electric-blue accent.
class AppColors {
  static const gain = Color(0xFF22D3A0);
  static const loss = Color(0xFFFF6B6B);
  static const accent = Color(0xFF4C8DFF);
  static const warn = Color(0xFFFFB547);
  static const test = Color(0xFF9B8CFF); // Test (fake money) badge
  static const live = Color(0xFFFF8A3D); // Live (real money) badge

  static const darkBg = Color(0xFF070B14);
  static const darkSurface = Color(0xFF111827);
  static const lightBg = Color(0xFFF4F6FB);
  static const lightSurface = Colors.white;

  static Color change(num value) => value >= 0 ? gain : loss;
}

ThemeData buildTheme(Brightness brightness) {
  final dark = brightness == Brightness.dark;
  final scheme = ColorScheme.fromSeed(
    seedColor: AppColors.accent,
    brightness: brightness,
    primary: AppColors.accent,
    surface: dark ? AppColors.darkSurface : AppColors.lightSurface,
  );
  final base = ThemeData(
    useMaterial3: true,
    colorScheme: scheme,
    brightness: brightness,
  );
  final text = GoogleFonts.interTextTheme(base.textTheme).apply(
    bodyColor: dark ? Colors.white : const Color(0xFF0E1525),
    displayColor: dark ? Colors.white : const Color(0xFF0E1525),
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
      indicatorColor: AppColors.accent.withValues(alpha: 0.18),
      labelTextStyle: WidgetStatePropertyAll(
        text.labelSmall?.copyWith(fontWeight: FontWeight.w600),
      ),
    ),
    navigationRailTheme: NavigationRailThemeData(
      backgroundColor: dark ? const Color(0xFF0B1120) : Colors.white,
      indicatorColor: AppColors.accent.withValues(alpha: 0.18),
    ),
    sliderTheme: const SliderThemeData(
      activeTrackColor: AppColors.accent,
      thumbColor: AppColors.accent,
    ),
    dividerColor: dark ? Colors.white10 : Colors.black12,
  );
}
