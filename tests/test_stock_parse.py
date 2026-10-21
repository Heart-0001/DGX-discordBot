from datetime import datetime
from zoneinfo import ZoneInfo

from cogs import stock_parse as sp

TP, NY = ZoneInfo('Asia/Taipei'), ZoneInfo('America/New_York')
TW_LIST = sp.build_tw_list(
    [{'Code': '2330', 'Name': '台積電'}, {'Code': '6669', 'Name': '緯穎'}, {'Code': '0050', 'Name': '元大台灣50'},
     {'Code': '00631L', 'Name': '元大台灣50正2'}, {'Code': '030001', 'Name': '某權證'}],
    [{'SecuritiesCompanyCode': '8299', 'CompanyName': '群聯'}, {'SecuritiesCompanyCode': '6669', 'CompanyName': '重複'}])


def test_build_list_drops_warrants_and_duplicates():
    codes = [s['code'] for s in TW_LIST]
    assert '030001' not in codes and codes.count('6669') == 1
    assert next(s for s in TW_LIST if s['code'] == '8299')['ex'] == 'otc'


def test_resolve_tw_code_name_and_fuzzy():
    assert sp.resolve_local('6669', TW_LIST)[0]['name'] == '緯穎'
    assert sp.resolve_local('緯穎', TW_LIST)[0]['code'] == '6669'
    assert sp.resolve_local('台積', TW_LIST)[0]['code'] == '2330'
    assert sp.resolve_local('2330.tw', TW_LIST)[0]['code'] == '2330'
    assert sp.resolve_local('00631l', TW_LIST)[0]['code'] == '00631L'
    t, others = sp.resolve_local('元大台灣50', TW_LIST)
    assert t['code'] == '0050' and [o['code'] for o in others] == ['00631L']


def test_resolve_unknown_tw_code_keeps_suffix_exchange():
    t, _ = sp.resolve_local('7777.TWO', TW_LIST)
    assert t == {'market': 'TW', 'code': '7777', 'name': '', 'ex': 'otc'}
    assert sp.resolve_local('7777', TW_LIST)[0]['ex'] is None


def test_resolve_us_aliases_indices_and_tickers():
    assert sp.resolve_local('蘋果', TW_LIST)[0] == {'market': 'US', 'symbol': 'AAPL', 'zh': '蘋果'}
    assert sp.resolve_local('台積電ADR', TW_LIST)[0]['symbol'] == 'TSM'
    assert sp.resolve_local('大盤', TW_LIST)[0]['code'] == 't00'
    assert sp.resolve_local('費半', TW_LIST)[0]['symbol'] == '^SOX'
    assert sp.resolve_local('VTI', TW_LIST) == (None, [])   # 交給 Yahoo
    assert sp.us_symbol('brk.b') == 'BRK-B'


def test_yahoo_symbol_and_key():
    assert sp.yahoo_symbol({'market': 'TW', 'code': '8299', 'ex': 'otc'}) == '8299.TWO'
    assert sp.yahoo_symbol({'market': 'TW', 'code': '2330', 'ex': 'tse'}) == '2330.TW'
    assert sp.yahoo_symbol(sp.index_target('TW', 't00')) == '^TWII'
    assert sp.track_key({'market': 'US', 'symbol': 'VTI'}) == 'US:VTI'


def test_parse_mis_falls_back_when_no_trade():
    q = sp.parse_mis({'c': '6669', 'n': '緯穎', 'ex': 'tse', 'd': '20261014', 't': '10:00:05', 'z': '-',
                      'pz': '-', 'y': '2195.0000', 'o': '2230.0000', 'h': '2285', 'l': '2085', 'v': '6591',
                      'b': '2110.0000_2105.0000_', 'a': '2115.0000_2120.0000_', 'u': '2410', 'w': '1980'})
    assert q['price'] == 2112.5 and q['prev'] == 2195 and q['volume'] == 6591
    assert q['at'] == datetime(2026, 10, 14, 10, 0, 5, tzinfo=TP)


def test_parse_yahoo_open_is_first_bar_of_last_day():
    day1, day2 = datetime(2026, 10, 12, 15, 58, tzinfo=NY), datetime(2026, 10, 13, 9, 30, tzinfo=NY)
    res = {'meta': {'symbol': 'VTI', 'exchangeTimezoneName': 'America/New_York', 'regularMarketPrice': 381.32,
                    'previousClose': 381.57, 'regularMarketTime': int(day2.timestamp()) + 60,
                    'currentTradingPeriod': {'regular': {'start': int(day2.timestamp()) + 86400}}},
           'timestamp': [int(day1.timestamp()), int(day2.timestamp())],
           'indicators': {'quote': [{'open': [380.0, 381.33], 'close': [380.1, 381.4]}]}}
    q = sp.parse_yahoo(res)
    assert q['market'] == 'US' and q['open'] == 381.33 and q['prev'] == 381.57


def test_format():
    assert sp.fmt_price(2115.0) == '2,115' and sp.fmt_price(39.78) == '39.78' and sp.fmt_price(None) == '—'
    assert sp.fmt_change(-80, -3.644) == '▼ 80.00 (-3.64%)'
    assert sp.fmt_change(0, 0) == '平盤 0.00%'
    assert sp.fmt_volume(6591, '張') == '6,591 張'
    assert sp.fmt_volume(2_460_185, '股') == '246.0 萬股'
    assert sp.limit_tag({'price': 2410, 'limit_up': 2410}) == ' 🔺漲停'


def test_due_events_tw():
    d = datetime(2026, 10, 14, tzinfo=TP)   # 週三
    at = lambda h, m: d.replace(hour=h, minute=m)
    assert sp.due_events('TW', at(8, 59), {}) == []
    assert sp.due_events('TW', at(9, 1), {}) == ['open']
    assert sp.due_events('TW', at(9, 1), {'open': '2026-10-14'}) == []
    assert sp.due_events('TW', at(13, 31), {}) == []
    assert sp.due_events('TW', at(13, 32), {'open': '2026-10-14'}) == ['close']
    assert sp.due_events('TW', at(17, 0), {'open': '2026-10-14'}) == []        # 太晚不補發
    assert sp.due_events('TW', datetime(2026, 10, 17, 10, tzinfo=TP), {}) == []   # 週六


def test_due_events_us_dst_and_early_close():
    # 夏令(EDT)與冬令(EST)都以紐約當地 09:30 為準；輸入是 UTC 也要換算
    summer = datetime(2026, 10, 14, 13, 31, tzinfo=ZoneInfo('UTC'))   # 09:31 EDT
    winter = datetime(2026, 12, 14, 14, 31, tzinfo=ZoneInfo('UTC'))   # 09:31 EST
    assert sp.due_events('US', summer, {}) == ['open']
    assert sp.due_events('US', winter, {}) == ['open']
    early = datetime(2026, 11, 27, 13, 0, tzinfo=NY)
    assert sp.due_events('US', datetime(2026, 11, 27, 13, 3, tzinfo=NY), {'open': '2026-11-27'}, early) == ['close']


def test_already_past_marks_today_events():
    assert sp.already_past('TW', datetime(2026, 10, 14, 8, 0, tzinfo=TP)) == {}
    assert sp.already_past('TW', datetime(2026, 10, 14, 10, 0, tzinfo=TP)) == {'open': '2026-10-14'}
    assert sp.already_past('TW', datetime(2026, 10, 14, 14, 0, tzinfo=TP)) == {'open': '2026-10-14', 'close': '2026-10-14'}


def test_market_state():
    q = {'at': datetime(2026, 10, 14, 10, 0, tzinfo=TP)}
    assert sp.market_state('TW', datetime(2026, 10, 14, 10, 5, tzinfo=TP), q) == '盤中'
    assert sp.market_state('TW', datetime(2026, 10, 14, 14, 0, tzinfo=TP), q) == '已收盤'
    assert sp.market_state('TW', datetime(2026, 10, 15, 8, 0, tzinfo=TP), q) == '盤前'
    assert sp.market_state('TW', datetime(2026, 10, 15, 11, 0, tzinfo=TP), q) == '休市'


def test_render_chart_png():
    start = int(datetime(2026, 10, 14, 9, 0, tzinfo=TP).timestamp())
    pts = [(start + i * 120, 100 + (i % 7)) for i in range(60)]
    png = sp.render_chart(pts, 103, start, start + 16200, TP, last=105)
    assert png and png[:8] == b'\x89PNG\r\n\x1a\n'
    assert sp.render_chart(pts[:1], 103, start, start + 16200, TP) is None


def test_parse_history_ref_and_stats():
    t0 = int(datetime(2026, 10, 13, tzinfo=TP).timestamp())
    res = {'meta': {'exchangeTimezoneName': 'Asia/Taipei', 'chartPreviousClose': 2480.0,
                    'fiftyTwoWeekLow': 1000, 'fiftyTwoWeekHigh': 2590},
           'timestamp': [t0, t0 + 86400, t0 + 172800, t0 + 259200],
           'indicators': {'quote': [{'close': [2450.0, None, 2405.0, 2420.0], 'high': [2500.0, None, 2440.0, 2430.0],
                                     'low': [2440.0, None, 2390.0, None], 'volume': [1000, None, 2000, 0]}]}}
    h = sp.parse_history(res)
    assert h['ref'] == 2480.0 and len(h['points']) == 3   # 沒收盤價的那根跳過
    assert h['high'] == 2500.0 and h['low'] == 2390.0 and h['volume'] == 3000
    assert h['w52'] == (1000, 2590)
    del res['meta']['chartPreviousClose']
    assert sp.parse_history(res)['ref'] == 2450.0          # 沒給就用第一根
    res['indicators']['quote'][0]['close'] = [2450.0, None, None, None]
    assert sp.parse_history(res) is None


def test_render_history_png():
    t0 = int(datetime(2026, 1, 2, tzinfo=TP).timestamp())
    pts = [(t0 + i * 86400, 100 + (i % 11)) for i in range(250)]
    png = sp.render_history(pts, 98, TP, '%Y/%m', last=104)
    assert png and png[:8] == b'\x89PNG\r\n\x1a\n'
    assert sp.render_history(pts[:1], 98, TP, '%Y/%m') is None
    assert set(sp.RANGES) == {'1d', '5d', '1mo', '1y', '5y'}
