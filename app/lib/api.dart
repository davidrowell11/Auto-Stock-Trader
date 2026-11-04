import 'dart:convert';

import 'package:http/http.dart' as http;

import 'models.dart';

/// What the kill switch sold, and what it couldn't sell.
typedef KillResult = ({List<String> sold, List<String> failed});

class ApiException implements Exception {
  ApiException(this.message);
  final String message;
  @override
  String toString() => message;
}

/// Everything the app needs from the bot. [HttpApi] talks to the real server;
/// [DemoApi] (in demo.dart) returns sample data so the app can be explored first.
abstract class TraderApi {
  Future<Summary> summary(String mode);
  Future<List<Holding>> positions(String mode);
  Future<List<Trade>> trades(String mode);
  Future<List<EquityPoint>> equity(String mode);
  Future<List<ActivityEvent>> events(String mode);
  Future<LiveStatus> live(String mode);
  Future<AnalystFeed> analyst(String mode);
  Future<List<Approval>> approvals(String mode);
  Future<void> decide(String mode, int id, bool approve);
  Future<BotSettings> settings(String mode);
  Future<BotSettings> updateSettings(String mode, Map<String, dynamic> changes);
  Future<void> setRunning(String mode, bool running);
  Future<KillResult> kill(String mode, {required bool sellAll});
  Future<WeeklyReport> weekly(String mode);
  Future<List<PriceBar>> bars(
    String symbol, {
    int days = 180,
    String mode = 'test',
  });
  Future<String> ask(String question, String mode);
  Future<Readiness> readiness();
  Future<Learning> learning(String mode);

  /// Live only: use (or turn down) the lessons Test learned.
  Future<Learning> decideLearning(bool approve);

  /// Test only: learn from finished trades now instead of waiting for the weekly review.
  Future<Learning> learnNow();
}

class HttpApi implements TraderApi {
  HttpApi(String baseUrl, this.token) : base = normalizeUrl(baseUrl);

  /// "auto-stock-trader.replit.app" works as well as "https://auto-stock-trader.replit.app/".
  static String normalizeUrl(String url) {
    var u = url.trim().replaceAll(RegExp(r'/+$'), '');
    if (u.isNotEmpty && !u.contains('://')) {
      // Local servers (this computer or the home network) usually have no HTTPS.
      final local = RegExp(
        r'^(localhost|127\.|10\.|192\.168\.|172\.(1[6-9]|2\d|3[01])\.)',
      ).hasMatch(u);
      u = '${local ? 'http' : 'https'}://$u';
    }
    return u;
  }

  final String base;
  final String token;

  Map<String, String> get _headers => {
    'Authorization': 'Bearer $token',
    'Content-Type': 'application/json',
  };

  Future<dynamic> _send(
    String method,
    String path, [
    Object? body,
    Duration timeout = const Duration(seconds: 20),
  ]) async {
    final uri = Uri.parse('$base$path');
    final http.Response res;
    try {
      final Future<http.Response> request = switch (method) {
        'POST' => http.post(
          uri,
          headers: _headers,
          body: jsonEncode(body ?? {}),
        ),
        'PUT' => http.put(uri, headers: _headers, body: jsonEncode(body ?? {})),
        _ => http.get(uri, headers: _headers),
      };
      res = await request.timeout(timeout);
    } catch (e) {
      throw ApiException(
        "Can't reach the bot server. Check the address and that it's running.",
      );
    }
    Object? decoded;
    try {
      decoded = res.body.isEmpty ? null : jsonDecode(res.body);
    } on FormatException {
      // Not the bot's JSON (e.g. a hosting error page): report it plainly.
      throw ApiException(
        res.statusCode >= 400
            ? 'Server error ${res.statusCode}'
            : "That address answered, but it isn't the bot server.",
      );
    }
    if (res.statusCode >= 400) {
      final detail = decoded is Map ? decoded['detail'] : null;
      throw ApiException(
        detail?.toString() ?? 'Server error ${res.statusCode}',
      );
    }
    return decoded;
  }

  Future<bool> ping() async {
    await _send('GET', '/api/test/settings');
    return true;
  }

  @override
  Future<Summary> summary(String mode) async =>
      Summary.fromJson(await _send('GET', '/api/$mode/summary'));

  @override
  Future<List<Holding>> positions(String mode) async => [
    for (final j in await _send('GET', '/api/$mode/positions'))
      Holding.fromJson(j),
  ];

  @override
  Future<List<Trade>> trades(String mode) async => [
    for (final j in await _send('GET', '/api/$mode/trades')) Trade.fromJson(j),
  ];

  @override
  Future<List<EquityPoint>> equity(String mode) async => [
    for (final j in await _send('GET', '/api/$mode/equity'))
      EquityPoint.fromJson(j),
  ];

  @override
  Future<List<ActivityEvent>> events(String mode) async => [
    for (final j in await _send('GET', '/api/$mode/events'))
      ActivityEvent.fromJson(j),
  ];

  @override
  Future<LiveStatus> live(String mode) async =>
      LiveStatus.fromJson(await _send('GET', '/api/$mode/live'));

  @override
  Future<AnalystFeed> analyst(String mode) async =>
      AnalystFeed.fromJson(await _send('GET', '/api/$mode/analyst'));

  @override
  Future<List<Approval>> approvals(String mode) async => [
    for (final j in await _send('GET', '/api/$mode/approvals'))
      Approval.fromJson(j),
  ];

  @override
  Future<void> decide(String mode, int id, bool approve) => _send(
    'POST',
    '/api/$mode/approvals/$id',
    {'decision': approve ? 'approve' : 'reject'},
  );

  @override
  Future<BotSettings> settings(String mode) async =>
      BotSettings.fromJson(await _send('GET', '/api/$mode/settings'));

  @override
  Future<BotSettings> updateSettings(
    String mode,
    Map<String, dynamic> changes,
  ) async =>
      BotSettings.fromJson(await _send('PUT', '/api/$mode/settings', changes));

  @override
  Future<void> setRunning(String mode, bool running) =>
      _send('POST', '/api/$mode/${running ? 'start' : 'stop'}');

  @override
  Future<KillResult> kill(String mode, {required bool sellAll}) async {
    final r = await _send('POST', '/api/$mode/kill', {'sell_all': sellAll});
    return (
      sold: [for (final s in r['sold'] as List) s.toString()],
      failed: [for (final s in (r['failed'] as List?) ?? []) s.toString()],
    );
  }

  @override
  Future<WeeklyReport> weekly(String mode) async =>
      WeeklyReport.fromJson(await _send('GET', '/api/$mode/report/weekly'));

  @override
  Future<List<PriceBar>> bars(
    String symbol, {
    int days = 180,
    String mode = 'test',
  }) async => [
    for (final j in await _send(
      'GET',
      '/api/bars/${Uri.encodeComponent(symbol)}?days=$days&mode=$mode',
    ))
      PriceBar.fromJson(j),
  ];

  @override
  Future<Readiness> readiness() async =>
      Readiness.fromJson(await _send('GET', '/api/readiness'));

  @override
  Future<Learning> learning(String mode) async =>
      Learning.fromJson(await _send('GET', '/api/$mode/learning'));

  @override
  Future<Learning> decideLearning(bool approve) async => Learning.fromJson(
    await _send('POST', '/api/live/learning', {
      'decision': approve ? 'approve' : 'reject',
    }),
  );

  @override
  Future<Learning> learnNow() async => Learning.fromJson(
    // Writing the lessons asks Claude, which can take a little while.
    await _send(
      'POST',
      '/api/test/learning/run',
      null,
      const Duration(seconds: 90),
    ),
  );

  @override
  Future<String> ask(String question, String mode) async =>
      // The analyst chat can take a while (it searches the news), so allow 90 seconds.
      (await _send('POST', '/api/ask', {
            'question': question,
            'mode': mode,
          }, const Duration(seconds: 90)))['answer']
          as String;
}
