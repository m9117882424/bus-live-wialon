"""Cancellation on isolated data, including races and the departure deadline."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import io
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet
from PIL import Image
import requests
import zxingcpp

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='bus-cancel-test-') as tmp:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    dbpath = str(Path(tmp) / 'test.sqlite')
    env = os.environ.copy()
    env.update(BUS_DB=dbpath, BUS_ADMIN_PASSWORD='test-only-password',
               BUS_FERNET_KEY=Fernet.generate_key().decode(), BUS_INITIAL_CARDS='123456,654321')
    proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app:app', '--host', '127.0.0.1',
                             '--port', str(port), '--no-access-log'], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    host = f'http://127.0.0.1:{port}'
    base = host + '/bus'
    auth = ('admin', 'test-only-password')
    try:
        for _ in range(100):
            try:
                if requests.get(base + '/', timeout=1).status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(.1)
        else:
            raise RuntimeError('Test server did not start')
        tomorrow = (datetime.now(ZoneInfo('Europe/Istanbul')).date() + timedelta(days=1)).isoformat()
        def add_route(name, origin, destination, direction, departure, capacity=2):
            r = requests.post(base + '/api/admin/routes', auth=auth, timeout=5,
                              json={'name':name,'origin':origin,'destination':destination,'direction':direction,
                                    'forward_time':departure,'reverse_time':departure,'capacity':capacity,'service_date':tomorrow})
            r.raise_for_status()
            return max(x['id'] for x in requests.get(base + '/api/admin/routes', auth=auth, timeout=5).json())
        out = add_route('Туда','Камп','Магазин','forward','14:30')
        back = add_route('Обратно','Магазин','Камп','reverse','15:00')
        def book(card, route, reverse=None):
            return requests.post(base + '/api/book', timeout=5,
                                 json={'card':card,'date':tomorrow,'forward':route,'reverse':reverse,
                                       'reverse_date':tomorrow,'direction':'forward','lang':'ru'})
        def passenger_cancel(token, confirm=True):
            return requests.post(base + '/api/ticket/' + token + '/cancel', json={'confirm':confirm}, timeout=5)
        def admin_cancel(code):
            return requests.post(base + '/api/admin/bookings/' + code + '/cancel', json={'confirm':True}, auth=auth, timeout=5)
        def item_for_card(card, status='all'):
            result=requests.get(base + '/api/admin/qrcodes', params={'q':card,'status':status}, auth=auth, timeout=5)
            result.raise_for_status()
            return result.json()['items']
        first=book('123456',out,back);first.raise_for_status();url=first.json()['url'];token=url.rsplit('/',1)[1]
        entry=item_for_card('123456')[0];code=entry['decoded']['booking']
        if node := shutil.which('node'):
            sample=Path(tmp)/'ticket-before-cancellation.html'
            sample.write_text(requests.get(host+url,timeout=5).text,encoding='utf-8')
            subprocess.run([node,str(ROOT/'tests'/'test_cancel_ui.js'),str(sample)],check=True)
        qr_bytes=requests.get(host + url + '/qr',timeout=10).content
        original_qr=zxingcpp.read_barcode(Image.open(io.BytesIO(qr_bytes))).text
        assert original_qr.startswith(host+'/bus/check/') and token not in original_qr
        checked=requests.get(original_qr,timeout=5);checked.raise_for_status()
        assert 'ПОСАДКА РАЗРЕШЕНА' in checked.text and '123456' in checked.text
        assert 'Отменить бронь' not in checked.text
        assert entry['decoded']['fortune'] in checked.text and entry['fortune_tr'] in checked.text
        altered=original_qr[:-1]+('A' if original_qr[-1]!='A' else 'B')
        assert requests.get(altered,timeout=5).status_code==404
        assert requests.post(base+'/api/admin/verify',auth=auth,json={'token':altered},timeout=5).status_code==404
        assert requests.post(base+'/api/admin/verify',auth=auth,json={'token':original_qr.replace(host,'https://elsewhere.example')},timeout=5).status_code==400
        assert requests.post(base + '/api/admin/verify',auth=auth,json={'token':original_qr},timeout=5).status_code==200
        assert passenger_cancel(token,False).status_code==400
        assert requests.post(base + '/api/admin/bookings/' + code + '/cancel',json={'confirm':True},timeout=5).status_code==401
        legacy=requests.post(host+'/bus-test/api/ticket/'+token+'/cancel',json={'confirm':True},timeout=5)
        assert legacy.status_code==200 and '/bus/api/ticket/' in legacy.url
        ticket=requests.get(host+url,timeout=5);ticket.raise_for_status();assert '"cancelled": true' in ticket.text and '"can_cancel": false' in ticket.text
        assert requests.get(host+url+'/qr',timeout=5).status_code==410
        cancelled_check=requests.get(original_qr,timeout=5);assert cancelled_check.status_code==410 and 'БРОНЬ ОТМЕНЕНА' in cancelled_check.text
        for fmt in ('jpg','pdf'):
            assert requests.get(host+url+'/download/'+fmt,timeout=5).status_code==410
        assert requests.post(base+'/api/admin/verify',auth=auth,json={'token':original_qr},timeout=5).status_code==410
        for direction in ('forward','reverse'):
            trips=requests.get(base+'/api/trips',params={'date':tomorrow,'direction':direction},timeout=5).json()
            assert trips[0]['free']==2
        history=requests.get(base+'/api/admin/history',auth=auth,params={'card':'123456','status':'cancelled'},timeout=5).json()
        assert len(history)==2 and all(x['cancelled_at'] for x in history)
        result=requests.get(base+'/api/admin/qrcodes',auth=auth,params={'q':'123456','status':'cancelled','trip_date':tomorrow,'trip_time':'15:00'},timeout=5).json()
        assert result['total']==1 and result['items'][0]['cancelled'] and not result['items'][0]['can_cancel']
        assert passenger_cancel(token).status_code==200
        # Repeat cancellation and rebooking to verify archived leg IDs and positions can be reused safely.
        for i in range(5):
            again=book('123456',out,back);again.raise_for_status();newtoken=again.json()['url'].rsplit('/',1)[1]
            active=item_for_card('123456','active');assert len(active)==1
            assert [leg['number'] for leg in active[0]['decoded']['legs']]==[1,1]
            assert admin_cancel(active[0]['decoded']['booking']).status_code==200
            assert requests.get(base+'/api/ticket/'+newtoken+'/comments',timeout=5).json()['cancelled']
        assert len(requests.get(base+'/api/admin/history',auth=auth,params={'card':'123456','status':'cancelled'},timeout=5).json())==12
        # Once one leg has started, either role can cancel only the remaining leg.
        for who,card in [('passenger','123456'),('dispatcher','654321')]:
            started=book(card,out,back);started.raise_for_status();started_token=started.json()['url'].rsplit('/',1)[1]
            started_code=item_for_card(card,'active')[0]['decoded']['booking']
            with sqlite3.connect(dbpath) as db:
                yesterday=(datetime.now(ZoneInfo('Europe/Istanbul')).date()-timedelta(days=1 if who=='passenger' else 3)).isoformat()
                db.execute('UPDATE legs SET date=? WHERE booking_id=(SELECT id FROM bookings WHERE code=?) AND route_id=?',(yesterday,started_code,out));db.commit()
            qr_bytes=requests.get(host+started.json()['url']+'/qr',timeout=10).content
            old_qr=zxingcpp.read_barcode(Image.open(io.BytesIO(qr_bytes))).text
            assert requests.get(base+'/api/ticket/'+started_token+'/comments',timeout=5).json()['can_cancel']
            response=passenger_cancel(started_token) if who=='passenger' else admin_cancel(started_code)
            assert response.status_code==200,response.text
            assert requests.post(base+'/api/admin/verify',auth=auth,json={'token':old_qr},timeout=5).status_code==410
            status=requests.get(base+'/api/ticket/'+started_token+'/comments',timeout=5).json()
            assert status['cancelled'] and sorted(x['cancelled'] for x in status['comments'])==[0,1]
            ticket=requests.get(host+started.json()['url'],timeout=5);ticket.raise_for_status()
            assert '"cancelled": 1' in ticket.text and '"cancelled": 0' in ticket.text
            all_history=requests.get(base+'/api/admin/history',auth=auth,params={'card':card},timeout=5).json()
            rows=[r for r in all_history if r['code']==started_code]
            assert len(rows)==2 and {r['route_name']:bool(r['cancelled_at']) for r in rows}=={'Туда':False,'Обратно':True}
            for state,name in [('booked','Туда'),('cancelled','Обратно')]:
                filtered=requests.get(base+'/api/admin/history',auth=auth,params={'card':card,'status':state},timeout=5).json()
                assert [r['route_name'] for r in filtered if r['code']==started_code]==[name]
            reverse=requests.get(base+'/api/trips',params={'date':tomorrow,'direction':'reverse'},timeout=5).json()
            assert next(x for x in reverse if x['id']==back)['free']==2
        # If all legs have departed, neither role can cancel.
        finished=book('123456',out,back);finished.raise_for_status();finished_token=finished.json()['url'].rsplit('/',1)[1]
        finished_code=item_for_card('123456','active')[0]['decoded']['booking']
        with sqlite3.connect(dbpath) as db:
            db.execute('UPDATE legs SET date=? WHERE booking_id=(SELECT id FROM bookings WHERE code=?)',((datetime.now(ZoneInfo('Europe/Istanbul')).date()-timedelta(days=2)).isoformat(),finished_code));db.commit()
        assert not requests.get(base+'/api/ticket/'+finished_token+'/comments',timeout=5).json()['can_cancel']
        assert passenger_cancel(finished_token).status_code==409 and admin_cancel(finished_code).status_code==409
        # After cancellation a route may be deleted, and remains available for history filtering.
        isolated=add_route('Удаление','Камп','Магазин','forward','17:00')
        x=book('654321',isolated);x.raise_for_status();x_token=x.json()['url'].rsplit('/',1)[1]
        assert passenger_cancel(x_token).status_code==200
        assert requests.delete(base+f'/api/admin/routes/{isolated}',auth=auth,timeout=5).status_code==200
        opts=requests.get(base+'/api/admin/route-options',auth=auth,timeout=5).json()
        assert any(x['id']==isolated and x['deleted_at'] for x in opts)
        assert len(requests.get(base+'/api/admin/history',auth=auth,params={'route_id':isolated,'status':'cancelled'},timeout=5).json())==1
        # A concurrent new passenger either obtains the released seat or receives a full response; capacity never exceeds one.
        race=add_route('Гонка','Камп','Магазин','forward','18:00',capacity=1)
        current=book('123456',race);current.raise_for_status();race_token=current.json()['url'].rsplit('/',1)[1]
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(passenger_cancel,race_token);b=pool.submit(book,'654321',race)
            cancelled,booked=a.result().status_code,b.result().status_code
        assert cancelled==200 and booked in (200,409),(cancelled,booked)
        with sqlite3.connect(dbpath) as db:
            assert db.execute('SELECT COUNT(*) FROM legs WHERE route_id=?',(race,)).fetchone()[0] <= 1
        # Passenger and dispatcher can cancel simultaneously without duplicate archived rows.
        another=add_route('Двойная отмена','Камп','Магазин','forward','19:00')
        x=book('123456',another);x.raise_for_status();token2=x.json()['url'].rsplit('/',1)[1]
        url2=zxingcpp.read_barcode(Image.open(io.BytesIO(requests.get(base+'/ticket/'+token2+'/qr',timeout=10).content))).text
        entries=item_for_card('123456','active');code2=next(x['decoded']['booking'] for x in entries if x['decoded']['legs'][0]['time']=='19:00')
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(passenger_cancel,token2);b=pool.submit(admin_cancel,code2)
            assert (a.result().status_code,b.result().status_code)==(200,200)
        with sqlite3.connect(dbpath) as db:
            assert db.execute('SELECT COUNT(*) FROM cancelled_legs WHERE booking_id=(SELECT id FROM bookings WHERE code=?)',(code2,)).fetchone()[0]==1
        # Expired confirmation links disappear while the cancellation remains in history.
        with sqlite3.connect(dbpath) as db:
            expired=(datetime.now(ZoneInfo('Europe/Istanbul'))-timedelta(minutes=1)).isoformat()
            db.execute('UPDATE bookings SET expires=? WHERE code=?',(expired,code2));db.commit()
        assert requests.get(base+'/ticket/'+token2,timeout=5).status_code==404
        assert requests.get(url2,timeout=5).status_code==404
        assert requests.post(base+'/api/admin/verify',auth=auth,json={'token':url2},timeout=5).status_code==404
        assert requests.get(base+'/api/admin/qrcodes',auth=auth,params={'q':code2,'status':'cancelled'},timeout=5).json()['total']==1
        # Simultaneous passengers on the same trip receive different active wishes.
        concurrent=add_route('Пожелания','Камп','Магазин','forward','20:00',capacity=2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(book,'123456',concurrent);b=pool.submit(book,'654321',concurrent)
            reservations=[a.result(),b.result()]
        assert all(r.status_code==200 for r in reservations)
        with sqlite3.connect(dbpath) as db:
            wishes=db.execute('SELECT b.fortune FROM bookings b JOIN legs l ON l.booking_id=b.id WHERE l.route_id=?',(concurrent,)).fetchall()
        assert len(wishes)==2 and wishes[0][0]!=wishes[1][0]
        # Both scripts must parse, including the actual dynamically generated passenger confirmation.
        if node := shutil.which('node'):
            admin=requests.get(base+'/admin',auth=auth,timeout=5).text
            assert "qrList:'Бронирования'" in admin and 'cancelAdmin(' in admin
            for i,html in enumerate([admin,requests.get(host+url,timeout=5).text]):
                js=html.split('<script>',1)[1].split('</script>',1)[0];path=Path(tmp)/f'page{i}.js';path.write_text(js,encoding='utf-8');subprocess.run([node,'--check',str(path)],check=True)
        print('PASS: passenger and dispatcher cancellation, remaining-leg cancellation, departure lock, QR invalidation, seats, history, repeat and concurrent operations')
    finally:
        proc.terminate();proc.wait(timeout=10)
