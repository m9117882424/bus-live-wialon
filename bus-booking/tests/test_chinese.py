"""Focused Chinese regression: temporary SQLite and real renderers, no sockets."""
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from datetime import timedelta
from unittest.mock import patch

from cryptography.fernet import Fernet
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fortunes import FORTUNES, _LEGACY, _OPENINGS, _ENDINGS
from fortune_localizations import fortune_localized, _LEGACY_ZH, _OPEN_ZH, _END_ZH

assert (len(_LEGACY_ZH), len(_OPEN_ZH), len(_END_ZH)) == (8, 32, 31)
wishes = [fortune_localized(value, 'zh') for value in FORTUNES]
assert len(wishes) == len(set(wishes)) == 1000
assert all(re.search(r'[\u4e00-\u9fff]', value) and not re.search(r'[А-Яа-яЁё]', value) for value in wishes)
assert [fortune_localized(pair[0], 'zh') for pair in _LEGACY] == _LEGACY_ZH
for i, (opening, _) in enumerate(_OPENINGS):
    for j, (ending, _) in enumerate(_ENDINGS):
        assert fortune_localized(opening + ' ' + ending, 'zh') == _OPEN_ZH[i] + ' ' + _END_ZH[j]
assert fortune_localized('Пусть дорога будет лёгкой!', 'zh') == '愿您一路顺畅！'
assert fortune_localized('Неизвестное пожелание', 'zh') == 'Неизвестное пожелание'

with tempfile.TemporaryDirectory(prefix='bus-zh-') as temporary:
    os.environ.update(BUS_DB=str(Path(temporary) / 'test.sqlite'), BUS_ADMIN_PASSWORD='test-only',
                      BUS_FERNET_KEY=Fernet.generate_key().decode(), BUS_INITIAL_CARDS='001234',
                      BUS_PUBLIC_URL='http://127.0.0.1:18399')
    import app as bus
    bus.init()
    day = (bus.now() + timedelta(days=1)).date().isoformat()
    with bus.db() as connection:
        route = connection.execute(
            'INSERT INTO routes(name,origin,destination,forward_time,reverse_time,capacity) VALUES(?,?,?,?,?,?)',
            ('Маршрут ŞOK', 'Камп', 'ŞOK MARKET', '12:00:00', '18:00:00', 41),
        ).lastrowid
        connection.commit()
    request = bus.Booking(card='001234', date=day, forward=route, reverse=route, reverse_date=day, lang='zh')
    token = bus.book(request)['url'].rsplit('/', 1)[1]
    booking, legs = bus.ticketdata(token)
    assert booking['lang'] == 'zh' and len(legs) == 2
    assert (legs[0]['origin'], legs[0]['destination'], legs[0]['route_name']) == ('Камп', 'ŞOK MARKET', 'Маршрут ŞOK')
    page = bus.ticket(token)
    data = json.JSONDecoder().raw_decode(page.split('let data=', 1)[1])[0]
    assert data['fortune_zh'] == fortune_localized(booking['fortune'], 'zh')
    assert data['fortune'] == booking['fortune'] in FORTUNES
    checked = bus.public_check(booking['code'], bus.qr_signature(booking['code']), bus.Request({'type':'http','headers':[]}))
    assert 'value="zh" selected>🇨🇳 简体中文' in checked
    assert data['fortune_zh'] in checked and 'Камп / Kamp → ŞOK MARKET' in checked
    # Switching an old Russian booking must retain its original wish identity.
    with bus.db() as connection:
        connection.execute("UPDATE bookings SET lang='ru' WHERE token=?", (token,))
        connection.commit()
    old = json.JSONDecoder().raw_decode(bus.ticket(token).split('let data=', 1)[1])[0]
    assert old['lang'] == 'ru' and old['fortune_zh'] == data['fortune_zh']
    assert old['fortune'] == data['fortune']

    http_request = bus.Request({'type':'http', 'scheme':'http', 'server':('127.0.0.1',18399), 'path':'/', 'headers':[]})
    drawn = []
    real_text = ImageDraw.ImageDraw.text
    def capture(canvas, xy, text, *args, **kwargs):
        drawn.append((text, kwargs['font'].path))
        return real_text(canvas, xy, text, *args, **kwargs)
    with patch.object(ImageDraw.ImageDraw, 'text', capture):
        for fmt in ('jpg', 'pdf'):
            private = bus.download(token, fmt, http_request, 'zh')
            public = bus.check_download(booking['code'], bus.qr_signature(booking['code']), fmt, http_request, 'zh')
            for response in (private, public):
                assert response.media_type == ('image/jpeg' if fmt == 'jpg' else 'application/pdf')
                if fmt == 'jpg':
                    image = Image.open(io.BytesIO(response.body))
                    assert image.size == (1080, 1920)
                else:
                    assert response.body.startswith(b'%PDF-') and response.body.rstrip().endswith(b'%%EOF')
                    assert b'/Subtype /Image' in response.body and b'/Width 1080' in response.body
                    assert b'/Height 1920' in response.body and b'/MediaBox [ 0 0 324 576 ]' in response.body
    texts = [text for text, _ in drawn]
    for expected in ('可以乘车', '往返', '通行证:', '预订编号:', '送给您的祝福', '请出示二维码',
                     'Камп → ŞOK MARKET', 'ŞOK MARKET → Камп', 'Маршрут ŞOK', data['fortune_zh']):
        assert expected in texts, expected
    font_path = '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc'
    assert all(path == font_path for text, path in drawn if re.search(r'[\u4e00-\u9fff]', text))
    assert all('dejavu' in path for text, path in drawn if 'ŞOK' in text)
    font = ImageFont.truetype(font_path, 30)
    missing = bytes(font.getmask('\uffff'))
    for char in set(''.join(wishes) + 'КампМагазин') - {' '}:
        assert font.getmask(char).getbbox() and bytes(font.getmask(char)) != missing, repr(char)
    assert bus.ticketdata(token)[0]['lang'] == 'ru', 'Download language must not rewrite stored booking'
    # The pending return leg still blocks duplicate bookings after a per-leg cancellation.
    result = bus.passenger_cancel(token, bus.CancelRequest(confirm=True, leg_id=legs[0]['id']))
    assert result['cancelled_legs'] == [legs[0]['id']] and not result['cancelled']
    try:
        bus.book(request)
        raise AssertionError('Duplicate booking was accepted')
    except bus.HTTPException as error:
        assert error.detail['code'] == 'ACTIVE_BOOKING_EXISTS'
    assert bus.ticketdata(token)[0]['fortune'] == booking['fortune']
print('PASS: Chinese booking persistence, old RU wish identity, unchanged stops, private/public JPG/PDF, CJK glyph coverage, 1000 wishes, pending-leg duplicate guard')
