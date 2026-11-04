import 'package:auto_stock_trader/api.dart';
import 'package:flutter_test/flutter_test.dart';

void main() {
  test('server address without https:// still works', () {
    expect(
      HttpApi.normalizeUrl('auto-stock-trader.replit.app'),
      'https://auto-stock-trader.replit.app',
    );
    expect(
      HttpApi.normalizeUrl(' https://x.replit.app/ '),
      'https://x.replit.app',
    );
    expect(
      HttpApi.normalizeUrl('192.168.1.20:8000'),
      'http://192.168.1.20:8000',
    );
    expect(HttpApi.normalizeUrl('localhost:8000'), 'http://localhost:8000');
    expect(
      HttpApi.normalizeUrl('http://my-mac.local:8000'),
      'http://my-mac.local:8000',
    );
  });
}
