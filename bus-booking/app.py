import os, sqlite3, secrets, json, io, base64, html, calendar, threading, textwrap
from datetime import datetime, date, timedelta, time
from zoneinfo import ZoneInfo
from contextlib import contextmanager
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, Response, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel
from cryptography.fernet import Fernet, InvalidToken
import qrcode
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

BASE='/bus'; DB=os.environ.get('BUS_DB','bus.db'); TZ=ZoneInfo('Europe/Istanbul')
PASS=os.environ['BUS_ADMIN_PASSWORD']; cipher=Fernet(os.environ['BUS_FERNET_KEY'].encode()); app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None); auth=HTTPBasic()
@contextmanager
def db():
 c=sqlite3.connect(DB,timeout=20); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON');
 try: yield c
 finally: c.close()
def now(): return datetime.now(TZ)
def init():
 with db() as c:
  c.executescript('''PRAGMA journal_mode=WAL;
 CREATE TABLE IF NOT EXISTS routes(id INTEGER PRIMARY KEY, name TEXT NOT NULL, origin TEXT NOT NULL, destination TEXT NOT NULL, forward_time TEXT NOT NULL, reverse_time TEXT NOT NULL, capacity INTEGER NOT NULL CHECK(capacity BETWEEN 1 AND 500), active INTEGER NOT NULL DEFAULT 1);
 CREATE TABLE IF NOT EXISTS bookings(id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, card TEXT NOT NULL, fortune TEXT NOT NULL, token TEXT UNIQUE, expires TEXT NOT NULL, created TEXT NOT NULL, lang TEXT NOT NULL);
 CREATE TABLE IF NOT EXISTS legs(id INTEGER PRIMARY KEY, booking_id INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE, route_id INTEGER NOT NULL, direction TEXT NOT NULL, date TEXT NOT NULL, departure TEXT NOT NULL, origin TEXT NOT NULL, destination TEXT NOT NULL, sequence INTEGER NOT NULL, capacity INTEGER NOT NULL, route_name TEXT NOT NULL, card TEXT NOT NULL, UNIQUE(route_id,direction,date,card), UNIQUE(route_id,direction,date,sequence));
 CREATE INDEX IF NOT EXISTS legs_trip ON legs(route_id,direction,date);''')
  stops_exist=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='stops'").fetchone()
  c.execute('CREATE TABLE IF NOT EXISTS stops(id INTEGER PRIMARY KEY, name_ru TEXT NOT NULL UNIQUE, name_tr TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1)')
  c.execute('CREATE TABLE IF NOT EXISTS stop_aliases(alias TEXT PRIMARY KEY, name_tr TEXT NOT NULL, stop_id INTEGER NOT NULL REFERENCES stops(id))')
  if not stops_exist: c.executemany('INSERT INTO stops(name_ru,name_tr,active) VALUES(?,?,1)',[('Камп','Kamp'),('Магазин','Mağaza')])
  whitelist_exists=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='allowed_cards'").fetchone()
  c.execute('CREATE TABLE IF NOT EXISTS allowed_cards(card TEXT PRIMARY KEY)')
  if not whitelist_exists:
   initial_cards=[x.strip() for x in os.environ.get('BUS_INITIAL_CARDS','').split(',') if x.strip()]
   if any(not x.isascii() or not x.isdigit() or not 4<=len(x)<=12 for x in initial_cards): raise RuntimeError('Invalid initial card list')
   c.executemany('INSERT OR IGNORE INTO allowed_cards(card) VALUES(?)',[(x,) for x in initial_cards])
  if 'cancelled_at' not in {x['name'] for x in c.execute('PRAGMA table_info(bookings)')}: c.execute('ALTER TABLE bookings ADD COLUMN cancelled_at TEXT')
  c.execute('''CREATE TABLE IF NOT EXISTS cancelled_legs(archive_id INTEGER PRIMARY KEY, original_leg_id INTEGER NOT NULL, booking_id INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE, route_id INTEGER NOT NULL, direction TEXT NOT NULL, date TEXT NOT NULL, departure TEXT NOT NULL, origin TEXT NOT NULL, destination TEXT NOT NULL, sequence INTEGER NOT NULL, capacity INTEGER NOT NULL, route_name TEXT NOT NULL, card TEXT NOT NULL, comment TEXT NOT NULL DEFAULT '')''')
  c.execute('CREATE INDEX IF NOT EXISTS cancelled_legs_booking ON cancelled_legs(booking_id)')
  if 'deleted_at' not in {x['name'] for x in c.execute('PRAGMA table_info(routes)')}: c.execute('ALTER TABLE routes ADD COLUMN deleted_at TEXT')
  if 'direction' not in {x['name'] for x in c.execute('PRAGMA table_info(routes)')}: c.execute("ALTER TABLE routes ADD COLUMN direction TEXT NOT NULL DEFAULT 'both'")
  if 'service_date' not in {x['name'] for x in c.execute('PRAGMA table_info(routes)')}: c.execute("ALTER TABLE routes ADD COLUMN service_date TEXT NOT NULL DEFAULT ''")
  for table in ('routes','legs'):
   if 'comment' not in {x['name'] for x in c.execute('PRAGMA table_info('+table+')')}: c.execute("ALTER TABLE "+table+" ADD COLUMN comment TEXT NOT NULL DEFAULT ''")
  c.commit()
def cleanup(c):
 c.execute('UPDATE bookings SET token=NULL WHERE token IS NOT NULL AND expires<=?',(now().isoformat(),))
 t=now().date(); m=t.month-6; y=t.year
 if m<=0: m+=12; y-=1
 cutoff=date(y,m,min(t.day,calendar.monthrange(y,m)[1])).isoformat()
 c.execute('DELETE FROM bookings WHERE NOT EXISTS (SELECT 1 FROM legs WHERE legs.booking_id=bookings.id AND legs.date>=?) AND NOT EXISTS (SELECT 1 FROM cancelled_legs WHERE cancelled_legs.booking_id=bookings.id AND cancelled_legs.date>=?)',(cutoff,cutoff)); c.commit()
def admin(credentials:HTTPBasicCredentials=Depends(auth)):
 if not secrets.compare_digest(credentials.username.encode(),b'admin') or not secrets.compare_digest(credentials.password.encode(),PASS.encode()): raise HTTPException(status_code=401,headers={'WWW-Authenticate':'Basic realm="Bus"'})
def validdate(v):
 try: return date.fromisoformat(v)
 except: raise HTTPException(400,'Некорректная дата / Geçersiz tarih')
def dt(d,t): return datetime.combine(validdate(d),time.fromisoformat(t),TZ)
def trip(c,r,d,direction):
 tm=r['forward_time'] if direction=='forward' else r['reverse_time']; dep=dt(d,tm)
 n=c.execute('SELECT COUNT(*) n FROM legs WHERE route_id=? AND direction=? AND date=?',(r['id'],direction,d)).fetchone()['n']; return {'id':r['id'],'name':r['name'],'origin':r['destination'] if direction=='reverse' and r['direction']=='both' else r['origin'],'destination':r['origin'] if direction=='reverse' and r['direction']=='both' else r['destination'],'time':tm,'capacity':r['capacity'],'free':max(0,r['capacity']-n),'past':dep<=now()}
class Booking(BaseModel):
 card:str; date:str; forward:int; reverse:int|None=None; reverse_date:str|None=None; lang:str='ru'; direction:str='forward'
class CancelRequest(BaseModel):
 confirm:bool
class Whitelist(BaseModel):
 cards:list[str]
class Stop(BaseModel):
 name_ru:str; name_tr:str; active:bool=True
class Route(BaseModel):
 name:str; origin:str; destination:str; forward_time:str; reverse_time:str; capacity:int; active:bool=True; comment:str=''; service_date:str=''; direction:str='both'
@app.on_event('startup')
def startup():
 init()
 def janitor():
  while True:
   try:
    with db() as c: cleanup(c)
   except Exception: pass
   threading.Event().wait(60)
 threading.Thread(target=janitor,daemon=True).start()
@app.middleware('http')
async def protect(req, call_next):
 path=req.url.path
 if path=='/bus-test' or path.startswith('/bus-test/'):
  suffix=path[len('/bus-test'):] or '/'
  target=BASE+suffix+('?' + req.url.query if req.url.query else '')
  res=RedirectResponse(target,status_code=308)
 else: res=await call_next(req)
 res.headers['Cache-Control']='no-store'; res.headers['Referrer-Policy']='no-referrer'; res.headers['X-Content-Type-Options']='nosniff'; res.headers['X-Frame-Options']='DENY'; return res
@app.get(BASE,include_in_schema=False)
def slash(): return RedirectResponse(BASE+'/')
@app.get(BASE+'/',response_class=HTMLResponse)
def home(): return HTML
@app.get(BASE+'/admin',response_class=HTMLResponse,dependencies=[Depends(admin)])
def admin_page(): return ADMIN
@app.get(BASE+'/api/trips')
def trips(date:str,direction:str='forward'):
 validdate(date)
 if direction not in ['forward','reverse']: raise HTTPException(400)
 with db() as c:
  cleanup(c); return [trip(c,r,date,direction) for r in c.execute("SELECT * FROM routes WHERE active=1 AND deleted_at IS NULL AND (service_date='' OR service_date=?) AND direction IN ('both',?) ORDER BY CASE WHEN ?='forward' THEN forward_time ELSE reverse_time END,id",(date,direction,direction)).fetchall()]
FORTUNES_TR=['Bugün şans sizinle!','Sizi güzel bir gün bekliyor!','İyi haberler yolda!','Yolunuz açık olsun!','Bir gülümseme yeni fırsatlar açar!','Güzel olaylar çok yakın!','Bugün yeni fikirler için harika bir gün!','Her şey gönlünüzce olsun!']
FORTUNES=['Сегодня удача едет с вами!','Вас ждёт приятный день!','Хорошие новости уже в пути!','Пусть дорога будет лёгкой!','Улыбка откроет новые возможности!','Счастливые события совсем рядом!','Сегодня отличный день для новых идей!','Пусть всё сложится наилучшим образом!']
@app.post(BASE+'/api/book')
def book(b:Booking):
 card=b.card.strip(); day=validdate(b.date); rd=validdate(b.reverse_date or b.date)
 if b.direction not in ['forward','reverse']: raise HTTPException(400,'Некорректное направление / Geçersiz yön')
 if not card.isascii() or not card.isdigit() or not 4<=len(card)<=12: raise HTTPException(400,'Номер пропуска: от 4 до 12 цифр / Kart numarası 4–12 rakam olmalı')
 if b.reverse and rd<day: raise HTTPException(400,'Обратный рейс раньше прямого / Dönüş tarihi gidişten önce olamaz')
 with db() as c:
  try:
   c.execute('BEGIN IMMEDIATE'); selected=[]
   if not c.execute('SELECT 1 FROM allowed_cards WHERE card=?',(card,)).fetchone(): raise HTTPException(403,'Номер пропуска отсутствует в разрешённом списке. Обратитесь к администратору. / Kart numarası izinli listesinde bulunmuyor. Yöneticiye başvurun.')
   for rid,d,direction in [(b.forward,b.date,b.direction)]+([(b.reverse,b.reverse_date or b.date,'reverse' if b.direction=='forward' else 'forward')] if b.reverse else []):
    r=c.execute('SELECT * FROM routes WHERE id=? AND active=1 AND deleted_at IS NULL',(rid,)).fetchone()
    if not r or r['direction'] not in ('both',direction) or (r['service_date'] and r['service_date']!=d): raise HTTPException(400,'Рейс недоступен / Sefer mevcut değil')
    t=trip(c,r,d,direction)
    if t['past'] or t['free']<=0: raise HTTPException(409,'Рейс отправился или мест нет / Sefer kalktı veya yer kalmadı')
    if c.execute('SELECT 1 FROM legs WHERE route_id=? AND direction=? AND date=? AND card=?',(rid,direction,d,card)).fetchone(): raise HTTPException(409,'Пропуск уже записан на рейс / Kart bu sefere zaten kayıtlı')
    selected.append((r,d,direction,t))
   if len(selected)==2 and (selected[0][3]['destination']!=selected[1][3]['origin'] or selected[0][3]['origin']!=selected[1][3]['destination']): raise HTTPException(400,'Обратный рейс должен следовать между теми же остановками / Dönüş aynı duraklar arasında olmalı')
   if len(selected)==2 and dt(selected[1][1],selected[1][3]['time'])<=dt(selected[0][1],selected[0][3]['time']): raise HTTPException(400,'Обратный рейс должен быть после прямого / Dönüş gidişten sonra olmalı')
   token=secrets.token_urlsafe(32); code='BUS-'+secrets.token_hex(5).upper(); fortune=secrets.choice(FORTUNES); expires=max(dt(d,t['time']) for _,d,_,t in selected)+timedelta(minutes=30)
   bid=c.execute('INSERT INTO bookings(code,card,fortune,token,expires,created,lang) VALUES(?,?,?,?,?,?,?)',(code,card,fortune,token,expires.isoformat(),now().isoformat(),b.lang if b.lang in ['ru','tr'] else 'ru')).lastrowid
   for r,d,direction,t in selected:
    used={x[0] for x in c.execute('SELECT sequence FROM legs WHERE route_id=? AND direction=? AND date=?',(r['id'],direction,d))}
    seq=next((n for n in range(1,r['capacity']+1) if n not in used),None)
    if seq is None: raise HTTPException(409,'Свободных мест нет / Boş yer yok')
    c.execute('INSERT INTO legs(booking_id,route_id,direction,date,departure,origin,destination,sequence,capacity,route_name,card,comment) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(bid,r['id'],direction,d,t['time'],t['origin'],t['destination'],seq,r['capacity'],r['name'],card,r['comment']))
   c.commit(); return {'url':BASE+'/ticket/'+token}
  except: c.rollback(); raise
def booking_legs(c,b):
 if b['cancelled_at']:
  return [dict(x) for x in c.execute('SELECT original_leg_id AS id,booking_id,route_id,direction,date,departure,origin,destination,sequence,capacity,route_name,card,comment FROM cancelled_legs WHERE booking_id=? ORDER BY date,departure',(b['id'],))]
 return [dict(x) for x in c.execute('SELECT * FROM legs WHERE booking_id=? ORDER BY date,departure',(b['id'],))]
def is_cancellable(b,legs,current=None):
 current=current or now()
 return not b['cancelled_at'] and bool(legs) and all(dt(x['date'],x['departure'])>current for x in legs)
def ticketdata(token,allow_cancelled=False):
 with db() as c:
  cleanup(c); c.execute('BEGIN'); b=c.execute('SELECT * FROM bookings WHERE token=?',(token,)).fetchone()
  if not b: raise HTTPException(404,'Ссылка истекла / Bağlantının süresi doldu')
  if b['cancelled_at'] and not allow_cancelled: raise HTTPException(410,'Бронь отменена / Rezervasyon iptal edildi')
  return dict(b),booking_legs(c,b)
def cancel_booking(*,token=None,code=None):
 with db() as c:
  try:
   c.execute('BEGIN IMMEDIATE')
   b=c.execute('SELECT * FROM bookings WHERE token=?',(token,)).fetchone() if token is not None else c.execute('SELECT * FROM bookings WHERE code=?',(code,)).fetchone()
   if not b: raise HTTPException(404,'Бронь не найдена / Rezervasyon bulunamadı')
   if b['cancelled_at']: c.commit();return {'ok':True,'cancelled':True}
   legs=booking_legs(c,b);current=now()
   if not is_cancellable(b,legs,current):raise HTTPException(409,'Отмена недоступна после отправления первого рейса. / İlk seferin kalkışından sonra iptal edilemez.')
   if datetime.fromisoformat(b['expires'])<=current: raise HTTPException(404,'Срок действия ссылки истёк / Bağlantının süresi doldu')
   c.execute('''INSERT INTO cancelled_legs(original_leg_id,booking_id,route_id,direction,date,departure,origin,destination,sequence,capacity,route_name,card,comment)
                SELECT id,booking_id,route_id,direction,date,departure,origin,destination,sequence,capacity,route_name,card,comment FROM legs WHERE booking_id=?''',(b['id'],))
   c.execute('DELETE FROM legs WHERE booking_id=?',(b['id'],))
   c.execute('UPDATE bookings SET cancelled_at=? WHERE id=?',(current.isoformat(),b['id']))
   c.commit();return {'ok':True,'cancelled':True}
  except: c.rollback();raise
@app.post(BASE+'/api/ticket/{token}/cancel')
def passenger_cancel(token:str,body:CancelRequest):
 if not body.confirm:raise HTTPException(400,'Необходимо подтверждение / Onay gerekli')
 return cancel_booking(token=token)
@app.post(BASE+'/api/admin/bookings/{code}/cancel',dependencies=[Depends(admin)])
def dispatcher_cancel(code:str,body:CancelRequest):
 if not body.confirm:raise HTTPException(400,'Необходимо подтверждение / Onay gerekli')
 return cancel_booking(code=code)
def qrdata(b,legs):
 return {'booking':b['code'],'card':b['card'],'fortune':b['fortune'],'legs':[{'date':x['date'],'time':x['departure'],'number':x['sequence'],'capacity':x['capacity']} for x in legs]}
def stop_translations():
 with db() as c:
  translated={x['alias']:x['name_tr'] for x in c.execute('SELECT alias,name_tr FROM stop_aliases')}
  translated.update({x['name_ru']:x['name_tr'] for x in c.execute('SELECT name_ru,name_tr FROM stops')})
  return translated
def qrtoken(b,legs):
 return cipher.encrypt(json.dumps(qrdata(b,legs),ensure_ascii=False,separators=(',',':')).encode()).decode()
def qr_image(b,legs,max_pixels=None):
 q=qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L,box_size=5,border=4); q.add_data(qrtoken(b,legs)); q.make(fit=True)
 if max_pixels: q.box_size=max(1,max_pixels//(q.modules_count+q.border*2))
 return q.make_image(fill_color='black',back_color='white').convert('RGB')
@app.get(BASE+'/ticket/{token}',response_class=HTMLResponse)
def ticket(token:str):
 b,legs=ticketdata(token,allow_cancelled=True); data={'code':b['code'],'card':b['card'],'legs':legs,'lang':b['lang'],'expires':b['expires'],'token':token,'cancelled':bool(b['cancelled_at']),'can_cancel':is_cancellable(b,legs),'stop_translations':stop_translations()}; return TICKET.replace('/*DATA*/',json.dumps(data,ensure_ascii=False).replace('<','\\u003c'))
@app.get(BASE+'/api/ticket/{token}/comments')
def ticket_comments(token:str):
 b,legs=ticketdata(token,allow_cancelled=True); return {'comments':[{"id":x['id'],"comment":x['comment']} for x in legs],'cancelled':bool(b['cancelled_at']),'can_cancel':is_cancellable(b,legs)}
@app.get(BASE+'/ticket/{token}/qr')
def qr(token:str):
 b,legs=ticketdata(token); out=io.BytesIO(); qr_image(b,legs).save(out,format='PNG'); return Response(out.getvalue(),media_type='image/png')
def ticket_image(b,legs,stop_map=None):
 # Fixed 9:16 canvas; the text area shrinks as needed, while the QR remains large.
 W,H=1080,1920
 im=Image.new('RGB',(W,H),'#f3f7fb'); draw=ImageDraw.Draw(im)
 regular='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'; bold='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
 F=lambda n:ImageFont.truetype(regular,n); B=lambda n:ImageFont.truetype(bold,n)
 tr=b['lang']=='tr'; stop_map=stop_map or {'Камп':'Kamp','Магазин':'Mağaza'}; point=lambda x: (stop_map.get(x,x) if tr else x)
 def fit(text,font_factory,size,width):
  while size>20 and draw.textlength(text,font=font_factory(size))>width:size-=1
  return font_factory(size)
 def lines(text,font,width,canvas):
  result=[]
  for para in str(text).split('\n'):
   words=para.split() or [''];current=''
   for word in words:
    for ch in word:
     candidate=current+ch
     if current and canvas.textlength(candidate,font=font)>width:result.append(current);current=ch
     else:current=candidate
    if canvas.textlength(current+' ',font=font)<=width:current+=' '
   result.append(current.rstrip())
  return result
 draw.text((55,34),'TSM BUS',font=B(32),fill='#07549b')
 draw.rounded_rectangle((45,90,1035,195),radius=22,fill='#e2f3e6')
 title='BİNİŞ ONAYLANDI' if tr else 'ПОСАДКА РАЗРЕШЕНА'
 draw.text((78,114),title,font=fit(title,B,49,915),fill='#17662f')
 direction=('⇄  GİDİŞ - DÖNÜŞ' if tr else '⇄  В ОБЕ СТОРОНЫ') if len(legs)>1 else ('→  TEK YÖN' if tr else '→  В ОДНУ СТОРОНУ')
 draw.text((55,225),direction,font=fit(direction,B,46,970),fill='#07549b')
 body=Image.new('RGBA',(970,5000),(0,0,0,0));bd=ImageDraw.Draw(body);y=0
 for leg in legs:
  title=point(leg['origin'])+' → '+point(leg['destination'])
  route_lines=lines(title,B(40),880,bd)
  note_lines=lines(leg.get('comment',''),F(28),880,bd) if leg.get('comment') else []
  h=45+len(route_lines)*52+54+47+len(note_lines)*38+28
  bd.rounded_rectangle((0,y,969,y+h),radius=20,fill='white',outline='#dce6ef',width=2)
  cy=y+20
  for line in route_lines:bd.text((30,cy),line,font=B(40),fill='#143350');cy+=52
  bd.text((30,cy),leg['date']+'  •  '+leg['departure'][:5],font=B(36),fill='#07549b');cy+=54
  seat=('Kayıt: ' if tr else 'Запись: ')+str(leg['sequence'])+' / '+str(leg['capacity'])
  bd.text((30,cy),seat,font=F(30),fill='#556e81');cy+=47
  for line in note_lines:bd.text((30,cy),line,font=F(28),fill='#354f62');cy+=38
  y+=h+16
 for label,value in [(('Kart' if tr else 'Пропуск'),b['card']),(('Rezervasyon' if tr else 'Бронь'),b['code'])]:
  bd.text((12,y+5),label+':',font=F(30),fill='#556e81'); bd.text((270,y+2),value,font=B(31),fill='#143350'); y+=56
 body=body.crop((0,0,970,max(1,y)))
 if body.height>830:
  body=body.resize((int(body.width*830/body.height),830),Image.Resampling.LANCZOS)
 im.paste(body,((W-body.width)//2,295),body)
 qr_label='QR KODU GÖSTERİN' if tr else 'ПОКАЖИТЕ QR-КОД'
 draw.text((W/2,1130),qr_label,font=B(30),fill='#456478',anchor='ma')
 q=qr_image(b,legs,max_pixels=615)
 im.paste(q,((W-q.width)//2,1185))
 return im
@app.get(BASE+'/ticket/{token}/download/{fmt}')
def download(token:str,fmt:str,lang:str=''):
 if fmt not in ['jpg','pdf']: raise HTTPException(404)
 b,legs=ticketdata(token); b['lang']=lang if lang in ['ru','tr'] else b['lang']; im=ticket_image(b,legs,stop_translations()); out=io.BytesIO()
 if fmt=='jpg': im.save(out,'JPEG',quality=92); typ='image/jpeg'
 else:
  cv=canvas.Canvas(out,pagesize=(324,576)); cv.drawImage(ImageReader(im),0,0,width=324,height=576); cv.showPage(); cv.save(); typ='application/pdf'
 return Response(out.getvalue(),media_type=typ,headers={'Content-Disposition':f'attachment; filename="{b["code"]}.{fmt}"'})
@app.get(BASE+'/api/admin/whitelist',dependencies=[Depends(admin)])
def whitelist_get():
 with db() as c: return {'cards':[r['card'] for r in c.execute('SELECT card FROM allowed_cards ORDER BY card')]}
@app.put(BASE+'/api/admin/whitelist',dependencies=[Depends(admin)])
def whitelist_set(payload:Whitelist):
 cards=[x.strip() for x in payload.cards]
 if len(cards)>10000 or any(not card.isascii() or not card.isdigit() or not 4<=len(card)<=12 for card in cards): raise HTTPException(400,'Каждый номер должен содержать от 4 до 12 цифр / Her kart numarası 4–12 rakam olmalıdır')
 cards=sorted(set(cards))
 with db() as c:
  try:
   c.execute('BEGIN IMMEDIATE');c.execute('DELETE FROM allowed_cards');c.executemany('INSERT INTO allowed_cards(card) VALUES(?)',[(v,) for v in cards]);c.commit()
  except: c.rollback();raise
 return {'count':len(cards)}
MAX_XLSX_BYTES=5*1024*1024

def read_workbook(data):
 from openpyxl import load_workbook
 import zipfile
 if not data or len(data)>MAX_XLSX_BYTES: raise HTTPException(400,'Файл должен быть .xlsx и не больше 5 МБ / Dosya .xlsx olmalı ve 5 MB geçmemeli')
 try:
  with zipfile.ZipFile(io.BytesIO(data)) as archive:
   entries=archive.infolist()
   if len(entries)>500 or sum(e.file_size for e in entries)>60*1024*1024 or any('vbaproject.bin' in e.filename.lower() for e in entries): raise ValueError('invalid archive')
  wb=load_workbook(io.BytesIO(data),read_only=True,data_only=False,keep_links=False)
  if len(wb.worksheets)>20: raise ValueError('too many sheets')
  return wb
 except HTTPException: raise
 except Exception: raise HTTPException(400,'Не удалось открыть файл .xlsx / .xlsx dosyası açılamadı')

def card_value(cell):
 v=cell.value
 if v is None or (isinstance(v,str) and not v.strip()): return None
 if isinstance(v,bool) or cell.data_type in ('f','e'): return False
 if isinstance(v,(int,float)):
  if not float(v).is_integer() or v<0 or v>=10**12: return False
  value=str(int(v)); fmt=cell.number_format or ''
  if fmt and set(fmt)=={'0'} and len(fmt)<=12: value=value.zfill(len(fmt))
 elif isinstance(v,str): value=v.strip()
 else: return False
 return value if value.isascii() and value.isdigit() and 4<=len(value)<=12 else False

def xlsx_sheet(wb,index):
 if index<0 or index>=len(wb.worksheets): raise HTTPException(400,'Лист не найден / Sayfa bulunamadı')
 sheet=wb.worksheets[index]
 if sheet.max_row>50000 or sheet.max_column>100: raise HTTPException(400,'Превышен лимит: 50 000 строк или 100 столбцов / En fazla 50.000 satır ve 100 sütun')
 return sheet

@app.post(BASE+'/api/admin/whitelist/excel/inspect',dependencies=[Depends(admin)])
async def inspect_xlsx(request:Request):
 from openpyxl.utils import get_column_letter
 data=await request.body();wb=read_workbook(data)
 try:
  result=[]
  for i in range(len(wb.worksheets)):
   sh=xlsx_sheet(wb,i);rows=list(sh.iter_rows(min_row=1,max_row=min(sh.max_row,5),max_col=sh.max_column))
   cols=[]
   for j in range(sh.max_column):
    examples=[]
    for row in rows:
     v=row[j].value
     if v is not None and str(v).strip():examples.append(str(v)[:45])
    if examples:cols.append({'index':j+1,'letter':get_column_letter(j+1),'samples':examples[:3],'first_is_header':bool(rows and card_value(rows[0][j]) is False)})
   result.append({'index':i,'name':sh.title,'columns':cols})
  return {'sheets':result}
 finally: wb.close()

def parse_card_column(data,sheet_index,column,first_row):
 wb=read_workbook(data)
 try:
  sh=xlsx_sheet(wb,sheet_index)
  if column<1 or column>sh.max_column or first_row<1 or first_row>50000: raise HTTPException(400,'Неверный столбец или строка / Geçersiz sütun veya satır')
  cards=set();valid=0;invalid=[];blanks=0
  for row in sh.iter_rows(min_row=first_row,min_col=column,max_col=column):
   cell=row[0];value=card_value(cell)
   if value is None:blanks+=1
   elif value is False:invalid.append(cell.row)
   else:valid+=1;cards.add(value)
  return {'cards':cards,'valid_rows':valid,'unique':len(cards),'duplicates':valid-len(cards),'invalid':len(invalid),'invalid_rows':invalid[:10],'empty':blanks}
 finally: wb.close()

async def excel_parse_request(request,sheet,column,first_row):
 return parse_card_column(await request.body(),sheet,column,first_row)

@app.post(BASE+'/api/admin/whitelist/excel/preview',dependencies=[Depends(admin)])
async def preview_xlsx(request:Request,sheet:int=0,column:int=1,first_row:int=1):
 result=await excel_parse_request(request,sheet,column,first_row)
 with db() as c:
  existing={x['card'] for x in c.execute('SELECT card FROM allowed_cards')}
 result['already_present']=len(result['cards'] & existing)
 result['new']=len(result['cards']-existing)
 del result['cards'];return result

@app.post(BASE+'/api/admin/whitelist/excel/import',dependencies=[Depends(admin)])
async def import_xlsx(request:Request,sheet:int=0,column:int=1,first_row:int=1,mode:str='append'):
 if mode not in ('append','replace'):raise HTTPException(400,'Неверный режим / Geçersiz mod')
 result=await excel_parse_request(request,sheet,column,first_row);cards=result.pop('cards')
 if not cards:raise HTTPException(400,'Нет корректных номеров / Geçerli kart numarası yok')
 with db() as c:
  try:
   c.execute('BEGIN IMMEDIATE');old={x['card'] for x in c.execute('SELECT card FROM allowed_cards')}
   final=cards if mode=='replace' else old|cards
   if len(final)>10000:raise HTTPException(400,'В списке может быть не больше 10 000 номеров / Listede en fazla 10.000 kart olabilir')
   if mode=='replace':c.execute('DELETE FROM allowed_cards')
   c.executemany('INSERT OR IGNORE INTO allowed_cards(card) VALUES(?)',[(x,) for x in sorted(cards)])
   c.commit()
  except: c.rollback();raise
 return {**result,'added':len(cards-old),'removed':len(old-cards) if mode=='replace' else 0,'total':len(final)}

@app.get(BASE+'/api/admin/qrcodes',dependencies=[Depends(admin)])
def admin_qrcodes(q:str='',trip_date:str='',trip_time:str='',status:str='all',offset:int=0):
 if status not in ('all','active','expired','cancelled') or offset<0 or offset>100000: raise HTTPException(400)
 if trip_date: validdate(trip_date)
 if trip_time:
  try: time.fromisoformat(trip_time)
  except: raise HTTPException(400,'Некорректное время / Geçersiz saat')
 leg_view='(SELECT booking_id,date,departure,origin,destination,route_name FROM legs UNION ALL SELECT booking_id,date,departure,origin,destination,route_name FROM cancelled_legs)'
 where=' WHERE 1=1';params=[]
 if q:
  where+=" AND (b.code LIKE ? OR b.card LIKE ? OR b.fortune LIKE ? OR EXISTS (SELECT 1 FROM "+leg_view+" l WHERE l.booking_id=b.id AND (l.origin LIKE ? OR l.destination LIKE ? OR l.route_name LIKE ?)))"
  params+=['%'+q[:100]+'%']*6
 if trip_date or trip_time:
  leg_filters=[]
  if trip_date:leg_filters.append('l.date=?');params.append(trip_date)
  if trip_time:leg_filters.append('l.departure=?');params.append(trip_time)
  where+=' AND EXISTS (SELECT 1 FROM '+leg_view+' l WHERE l.booking_id=b.id AND '+' AND '.join(leg_filters)+')'
 if status=='active':where+=' AND b.token IS NOT NULL AND b.cancelled_at IS NULL'
 if status=='expired':where+=' AND b.token IS NULL AND b.cancelled_at IS NULL'
 if status=='cancelled':where+=' AND b.cancelled_at IS NOT NULL'
 with db() as c:
  cleanup(c)
  c.execute('BEGIN')
  total=c.execute('SELECT COUNT(*) FROM bookings b'+where,params).fetchone()[0]
  bookings=c.execute('SELECT b.* FROM bookings b'+where+' ORDER BY b.created DESC,b.id DESC LIMIT 30 OFFSET ?',params+[offset]).fetchall()
  items=[]
  for b in bookings:
   legs=booking_legs(c,b)
   items.append({'decoded':qrdata(b,legs),'fortune_tr':FORTUNES_TR[FORTUNES.index(b['fortune'])] if b['fortune'] in FORTUNES else b['fortune'],'active':b['token'] is not None and not b['cancelled_at'],'cancelled':bool(b['cancelled_at']),'cancelled_at':b['cancelled_at'],'can_cancel':is_cancellable(b,legs),'expires':b['expires'],'routes':[{'origin':x['origin'],'destination':x['destination']} for x in legs]})
  return {'items':items,'total':total,'offset':offset,'limit':30}

@app.get(BASE+'/api/admin/qrcodes/{code}/image',dependencies=[Depends(admin)])
def admin_qr_image(code:str):
 with db() as c:
  cleanup(c);b=c.execute('SELECT * FROM bookings WHERE code=? AND token IS NOT NULL AND cancelled_at IS NULL',(code,)).fetchone()
  if not b:raise HTTPException(404,'Срок действия истёк / Süresi doldu')
  legs=[dict(x) for x in c.execute('SELECT * FROM legs WHERE booking_id=? ORDER BY date,departure',(b['id'],))]
  out=io.BytesIO();qr_image(b,legs).save(out,format='PNG');return Response(out.getvalue(),media_type='image/png')

def validated_stop_values(data):
 ru=data.name_ru.strip();tr=data.name_tr.strip()
 if not ru or not tr or len(ru)>80 or len(tr)>80 or not ru.isprintable() or not tr.isprintable():
  raise HTTPException(400,'Укажите названия остановки на русском и турецком / Durak adlarını Rusça ve Türkçe girin')
 return ru,tr

def ensure_stop_available(c,name,exempt_id=None):
 target=name.casefold()
 for x in c.execute('SELECT id,name_ru FROM stops'):
  if x['id']!=exempt_id and x['name_ru'].casefold()==target:raise HTTPException(409,'Остановка уже существует / Durak zaten mevcut')
 for x in c.execute('SELECT stop_id,alias FROM stop_aliases'):
  if x['stop_id']!=exempt_id and x['alias'].casefold()==target:raise HTTPException(409,'Название использовалось другой остановкой / Bu ad başka bir durakta kullanılmış')

@app.get(BASE+'/api/stops')
def public_stops():
 with db() as c:return [dict(x) for x in c.execute('SELECT id,name_ru,name_tr FROM stops WHERE active=1 ORDER BY id')]

@app.get(BASE+'/api/admin/stops',dependencies=[Depends(admin)])
def admin_stops():
 with db() as c:return [dict(x) for x in c.execute('SELECT id,name_ru,name_tr,active FROM stops ORDER BY id')]

@app.post(BASE+'/api/admin/stops',dependencies=[Depends(admin)])
def create_stop(data:Stop):
 ru,tr=validated_stop_values(data)
 with db() as c:
  try:
   c.execute('BEGIN IMMEDIATE');ensure_stop_available(c,ru)
   if c.execute('SELECT COUNT(*) FROM stops').fetchone()[0]>=1000:raise HTTPException(400,'Не больше 1000 остановок / En fazla 1000 durak')
   new_id=c.execute('INSERT INTO stops(name_ru,name_tr,active) VALUES(?,?,?)',(ru,tr,int(data.active))).lastrowid;c.commit();return {'id':new_id}
  except: c.rollback();raise

@app.put(BASE+'/api/admin/stops/{stop_id}',dependencies=[Depends(admin)])
def edit_stop(stop_id:int,data:Stop):
 ru,tr=validated_stop_values(data)
 with db() as c:
  try:
   c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT * FROM stops WHERE id=?',(stop_id,)).fetchone()
   if not old:raise HTTPException(404,'Остановка не найдена / Durak bulunamadı')
   ensure_stop_available(c,ru,stop_id)
   if old['name_ru']!=ru:
    c.execute('DELETE FROM stop_aliases WHERE alias=? AND stop_id=?',(ru,stop_id))
    c.execute('INSERT OR REPLACE INTO stop_aliases(alias,name_tr,stop_id) VALUES(?,?,?)',(old['name_ru'],old['name_tr'],stop_id))
    c.execute('UPDATE routes SET origin=? WHERE origin=?',(ru,old['name_ru']))
    c.execute('UPDATE routes SET destination=? WHERE destination=?',(ru,old['name_ru']))
   c.execute('UPDATE stops SET name_ru=?,name_tr=?,active=? WHERE id=?',(ru,tr,int(data.active),stop_id));c.commit();return {'ok':True}
  except: c.rollback();raise

def ensure_route_stops(c,origin,destination,old=None):
 if origin.casefold()==destination.casefold():raise HTTPException(400,'Пункты отправления и назначения должны различаться / Kalkış ve varış farklı olmalı')
 available={x['name_ru'] for x in c.execute('SELECT name_ru FROM stops WHERE active=1')}
 for name,key in ((origin,'origin'),(destination,'destination')):
  if name not in available and not (old is not None and name==old[key]):raise HTTPException(400,'Остановка отсутствует в активном справочнике / Durak aktif listede bulunmuyor')

@app.get(BASE+'/api/admin/routes',dependencies=[Depends(admin)])
def admin_routes():
 with db() as c: return [dict(x) for x in c.execute('SELECT * FROM routes WHERE deleted_at IS NULL ORDER BY id')]
@app.get(BASE+'/api/admin/route-options',dependencies=[Depends(admin)])
def route_options():
 with db() as c:
  return [dict(x) for x in c.execute('SELECT r.* FROM routes r WHERE r.deleted_at IS NULL OR EXISTS (SELECT 1 FROM legs l WHERE l.route_id=r.id) OR EXISTS (SELECT 1 FROM cancelled_legs l WHERE l.route_id=r.id) ORDER BY r.id')]

@app.delete(BASE+'/api/admin/routes/{rid}',dependencies=[Depends(admin)])
def route_delete(rid:int):
 with db() as c:
  try:
   c.execute('BEGIN IMMEDIATE')
   route=c.execute('SELECT id FROM routes WHERE id=? AND deleted_at IS NULL',(rid,)).fetchone()
   if not route: raise HTTPException(404,'Рейс не найден / Sefer bulunamadı')
   current=now(); day=current.date().isoformat();clock=current.strftime('%H:%M:%S')
   future=c.execute('SELECT COUNT(DISTINCT booking_id) FROM legs WHERE route_id=? AND (date>? OR (date=? AND departure>?))',(rid,day,day,clock)).fetchone()[0]
   if future: raise HTTPException(409,f'Нельзя удалить рейс: есть предстоящие брони ({future}). / Sefer silinemez: gelecekte {future} rezervasyon var.')
   c.execute('UPDATE routes SET deleted_at=?,active=0 WHERE id=?',(current.isoformat(),rid));c.commit();return {'ok':True}
  except: c.rollback();raise

@app.post(BASE+'/api/admin/routes',dependencies=[Depends(admin)])
def route_create(r:Route):
 clean=[r.name.strip(),r.origin.strip(),r.destination.strip()]
 if r.service_date: validdate(r.service_date)
 if r.direction not in ('forward','reverse','both'): raise HTTPException(400,'Некорректное направление / Geçersiz yön')
 if any(not x or len(x)>90 for x in clean) or len(r.comment)>160 or not 1<=r.capacity<=500: raise HTTPException(400,'Некорректные данные / Geçersiz bilgiler')
 try: time.fromisoformat(r.forward_time); time.fromisoformat(r.reverse_time)
 except: raise HTTPException(400,'Некорректное время / Geçersiz saat')
 with db() as c:
  c.execute('BEGIN IMMEDIATE');ensure_route_stops(c,clean[1],clean[2])
  if c.execute('SELECT COUNT(*) FROM routes WHERE deleted_at IS NULL').fetchone()[0]>=10: raise HTTPException(400,'Максимум 10 рейсов / En fazla 10 sefer')
  c.execute('INSERT INTO routes(name,origin,destination,forward_time,reverse_time,capacity,active,comment,service_date,direction) VALUES(?,?,?,?,?,?,?,?,?,?)',(*clean,r.forward_time,r.reverse_time,r.capacity,int(r.active),r.comment.strip(),r.service_date,r.direction)); c.commit(); return {'ok':True}
@app.put(BASE+'/api/admin/routes/{rid}',dependencies=[Depends(admin)])
def route_edit(rid:int,r:Route):
 clean=[r.name.strip(),r.origin.strip(),r.destination.strip()]
 if r.service_date: validdate(r.service_date)
 if r.direction not in ('forward','reverse','both'): raise HTTPException(400,'Некорректное направление / Geçersiz yön')
 if any(not x or len(x)>90 for x in clean) or len(r.comment)>160 or not 1<=r.capacity<=500: raise HTTPException(400)
 try: time.fromisoformat(r.forward_time);time.fromisoformat(r.reverse_time)
 except: raise HTTPException(400)
 with db() as c:
  c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT * FROM routes WHERE id=? AND deleted_at IS NULL',(rid,)).fetchone()
  if not old: raise HTTPException(404)
  ensure_route_stops(c,clean[1],clean[2],old)
  future=c.execute('SELECT 1 FROM legs WHERE route_id=? AND date>=? LIMIT 1',(rid,now().date().isoformat())).fetchone()
  if future and (clean!=[old['name'],old['origin'],old['destination']] or r.forward_time!=old['forward_time'] or r.reverse_time!=old['reverse_time'] or r.capacity!=old['capacity'] or r.service_date!=old['service_date'] or r.direction!=old['direction']): raise HTTPException(409,'На рейс уже есть записи. Можно изменить только статус. / Kayıt olan seferde sadece durum değiştirilebilir.')
  c.execute('UPDATE routes SET name=?,origin=?,destination=?,forward_time=?,reverse_time=?,capacity=?,active=?,comment=?,service_date=?,direction=? WHERE id=?',(*clean,r.forward_time,r.reverse_time,r.capacity,int(r.active),r.comment.strip(),r.service_date,r.direction,rid)); c.execute('UPDATE legs SET comment=? WHERE route_id=? AND date>=?',(r.comment.strip(),rid,now().date().isoformat())); c.commit(); return {'ok':True}
@app.get(BASE+'/api/admin/history',dependencies=[Depends(admin)])
def history(date_from:str='',date_to:str='',route_id:str='',card:str='',time_from:str='',time_to:str='',origin:str='',status:str='all'):
 if status not in ('all','booked','cancelled'):raise HTTPException(400)
 with db() as c:
  cleanup(c); leg_view='(SELECT booking_id,route_id,date,departure,origin,destination,route_name,direction,sequence,capacity,card FROM legs UNION ALL SELECT booking_id,route_id,date,departure,origin,destination,route_name,direction,sequence,capacity,card FROM cancelled_legs)'; sql='SELECT b.code,b.created,b.cancelled_at,l.date,l.departure,l.origin,l.destination,l.route_name,l.direction,l.sequence,l.capacity,l.card FROM '+leg_view+' l JOIN bookings b ON b.id=l.booking_id WHERE 1=1'; params=[]
  if status=='booked':sql+=' AND b.cancelled_at IS NULL'
  if status=='cancelled':sql+=' AND b.cancelled_at IS NOT NULL'
  for val,fragment in [(date_from,' AND l.date>=?'),(date_to,' AND l.date<=?'),(route_id,' AND l.route_id=?'),(card,' AND l.card LIKE ?'),(time_from,' AND l.departure>=?'),(time_to,' AND l.departure<=?'),(origin,' AND l.origin LIKE ?')]:
   if val: sql+=fragment; params.append('%'+val+'%' if fragment.endswith('LIKE ?') else val)
  return [dict(x) for x in c.execute(sql+' ORDER BY l.date DESC,l.departure DESC LIMIT 1000',params)]
class Verify(BaseModel): token:str
@app.post(BASE+'/api/admin/verify',dependencies=[Depends(admin)])
def verify(v:Verify):
 try: data=json.loads(cipher.decrypt(v.token.strip().encode()).decode())
 except (InvalidToken,ValueError): raise HTTPException(400,'Недействительный QR / Geçersiz QR')
 with db() as c:
  cleanup(c); b=c.execute('SELECT * FROM bookings WHERE code=?',(data.get('booking'),)).fetchone()
  if not b or b['token'] is None: raise HTTPException(404,'Бронь не найдена или срок истёк / Rezervasyon bulunamadı veya süresi doldu')
  if b['cancelled_at']: raise HTTPException(410,'Бронь отменена / Rezervasyon iptal edildi')
  legs=[dict(x) for x in c.execute('SELECT * FROM legs WHERE booking_id=? ORDER BY date,departure',(b['id'],))]; expected=qrdata(b,legs)
  if data!=expected: raise HTTPException(400,'Данные не совпадают / Bilgiler uyuşmuyor')
  return {'code':b['code'],'card':b['card'],'fortune':b['fortune'],'fortune_tr':FORTUNES_TR[FORTUNES.index(b['fortune'])] if b['fortune'] in FORTUNES else b['fortune'],'legs':legs}
STYLE='''<meta name="viewport" content="width=device-width, initial-scale=1"><meta charset="utf-8"><style>*{box-sizing:border-box}body{margin:0;background:#f2f6fa;color:#142d48;font:16px system-ui,-apple-system,sans-serif}header{background:#fff;border-bottom:3px solid #13a6a0;padding:16px 20px;display:flex;align-items:center;justify-content:space-between;font-weight:800;color:#075099}main{max-width:740px;margin:auto;padding:20px 16px 50px}h1{font-size:25px;margin:6px 0 20px}h2{font-size:20px}.card,.box{background:white;border:1px solid #dae5ef;border-radius:15px;padding:17px;margin:12px 0;box-shadow:0 2px 9px #183b5810}.route{display:flex;align-items:center;justify-content:space-between;gap:8px}.muted{color:#668095;font-size:14px}.big{font-size:24px;font-weight:800;color:#064e96}.btn,button{border:0;border-radius:9px;padding:12px 18px;background:#07549b;color:white;font:inherit;font-weight:700;cursor:pointer}button:disabled{background:#bfcbd5;cursor:not-allowed}.secondary{background:#e8f1f9;color:#07549b}input,select,textarea{font:inherit;padding:11px;border:1px solid #b7c8d6;border-radius:8px;width:100%;background:#fff}label{display:block;font-weight:600;margin:13px 0 6px}input[type=checkbox]{width:auto;transform:scale(1.2);margin-right:9px}.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}.small{font-size:13px}.alert{padding:12px;color:#a72828;background:#fff0ee;border-radius:8px;margin:10px 0}.cancelled-banner{background:#ffe5e2!important;color:#a72828!important}.success{background:#e4f5e7;color:#126728;padding:15px;border-radius:10px;font-weight:800;text-align:center;font-size:23px}.direction{font-size:25px;font-weight:900;color:#07549b;text-align:center;margin:16px 0}.qr{display:block;max-width:320px;width:85%;margin:15px auto}table{border-collapse:collapse;width:100%;font-size:14px}td,th{text-align:left;border-bottom:1px solid #e4ebf0;padding:8px}.scroll{overflow:auto}.headbutton{background:#eef5fc;color:#07549b;padding:7px 12px}.status{border-radius:6px;background:#e3f5eb;color:#147047;padding:4px 7px;font-size:12px}a{color:#07549b}video{max-width:100%;border-radius:10px}</style>'''
HTML='''<!doctype html><html><head>'''+STYLE+'''<title>Автобус / Otobüs</title></head><body><header><span>🚌 TSM BUS</span><button class="headbutton" onclick="toggleLang()">RU / TR</button></header><main><h1 data-i="title"></h1><div class="box"><label data-i="date"></label><input type="date" id="day"><label data-i="dir"></label><select id="dir" onchange="directionChanged()"><option value="forward" data-i="direct"></option><option value="reverse" data-i="backward"></option><option value="both" data-i="round"></option></select><div class="small muted" style="margin-top:8px" data-i="choose"></div></div><div id="trips"></div><section id="booking" class="box" style="display:none"><h2 data-i="book"></h2><div id="selected" class="big"></div><input type="checkbox" id="round" hidden><div id="returnWrap" style="display:none"><label data-i="returnDate"></label><input type="date" id="returnDay"><label data-i="returnTrip"></label><select id="reverse"></select></div><label data-i="card"></label><input id="card" inputmode="numeric" maxlength="12" placeholder="123456"><div id="err"></div><button id="submit" style="width:100%;margin-top:16px" onclick="book()" data-i="submit"></button></section></main><script>
const B='/bus'; let lang=localStorage.getItem('bus-lang')||'ru', chosen=null, trips=[],stopMap={'Камп':'Kamp','Магазин':'Mağaza'};const t={ru:{title:'Бронирование автобуса',date:'Дата поездки',dir:'Вариант поездки',direct:'Только туда',backward:'Только обратно',choose:'Выберите удобное время отправления',select:'Выбрать',free:'свободно из',none:'Мест нет',past:'Рейс отправился',book:'Запись на рейс',round:'В обе стороны',returnDate:'Дата обратного рейса',returnTrip:'Обратный рейс',card:'Номер пропуска',submit:'ЗАПИСАТЬСЯ',empty:'На эту дату активных рейсов нет',chooseReturn:'Выберите обратный рейс',loading:'Подождите…'},tr:{title:'Otobüs rezervasyonu',date:'Yolculuk tarihi',dir:'Yolculuk seçeneği',direct:'Sadece gidiş',backward:'Sadece dönüş',choose:'Uygun kalkış saatini seçin',select:'Seç',free:'boş / toplam',none:'Yer yok',past:'Sefer kalktı',book:'Sefer kaydı',round:'Gidiş - dönüş',returnDate:'Dönüş tarihi',returnTrip:'Dönüş seferi',card:'Kart numarası',submit:'KAYIT OL',empty:'Bu tarihte aktif sefer yok',chooseReturn:'Dönüş seferini seçin',loading:'Lütfen bekleyin…'}};function esc(x){return String(x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function translate(){document.querySelectorAll('[data-i]').forEach(e=>e.textContent=t[lang][e.dataset.i])}function toggleLang(){lang=lang==='ru'?'tr':'ru';localStorage.setItem('bus-lang',lang);translate();updateDirectionLabels(trips);render();if(document.getElementById('round').checked)loadReverse()};function localdate(){let d=new Date(Date.now()+3*3600000);return d.toISOString().slice(0,10)};day.value=localdate();returnDay.value=day.value;day.onchange=()=>{chosen=null;booking.style.display='none';returnDay.value=day.value;load()};returnDay.onchange=loadReverse;function directionChanged(){chosen=null;round.checked=dir.value==='both';booking.style.display='none';returnWrap.style.display=round.checked?'block':'none';trips=[];render();load()}
function point(x){return lang==='tr'?(stopMap[x]||x):x}async function loadStopNames(){try{let r=await fetch(B+'/api/stops');let data=await r.json();for(let item of data)stopMap[item.name_ru]=item.name_tr;updateDirectionLabels(trips);render()}catch(e){}}function updateDirectionLabels(items){if(!Array.isArray(items)||!items.length)return;let first=items[0],origin=dir.value==='reverse'?first.destination:first.origin,destination=dir.value==='reverse'?first.origin:first.destination;if(!items.every(x=>(dir.value==='reverse'?x.destination:x.origin)===origin&&(dir.value==='reverse'?x.origin:x.destination)===destination))return;dir.options[0].textContent=point(origin)+' → '+point(destination);dir.options[1].textContent=point(destination)+' → '+point(origin)}async function load(){try{let direction=dir.value==='reverse'?'reverse':'forward';let r=await fetch(B+'/api/trips?date='+day.value+'&direction='+direction);let data=await r.json();if(direction!==(dir.value==='reverse'?'reverse':'forward'))return;trips=data;updateDirectionLabels(trips);render()}catch(e){trips=[];render()}}function render(){let h=trips.map(x=>`<div class="card route"><div><div class="big">${esc(x.time.slice(0,5))} <span class="muted">${esc(x.name)}</span></div><div>${esc(point(x.origin))} → ${esc(point(x.destination))}</div><div class="muted">${x.past?t[lang].past:x.free>0?x.free+' '+t[lang].free+' '+x.capacity:t[lang].none}</div></div><button ${x.past||!x.free?'disabled':''} onclick="choose(${x.id})">${t[lang].select}</button></div>`).join('');document.getElementById('trips').innerHTML=h||'<p class="muted">'+t[lang].empty+'</p>'}function choose(id){chosen=trips.find(x=>x.id===id);selected.textContent=point(chosen.origin)+' → '+point(chosen.destination)+' · '+chosen.time.slice(0,5);booking.style.display='block';booking.scrollIntoView({behavior:'smooth'});if(round.checked)loadReverse()}function roundChanged(){returnWrap.style.display=round.checked?'block':'none';if(round.checked)loadReverse()}async function loadReverse(){if(!returnDay.value)return;let r=await fetch(B+'/api/trips?date='+returnDay.value+'&direction='+(dir.value==='reverse'?'forward':'reverse'));let data=await r.json();reverse.innerHTML='<option value="">'+t[lang].chooseReturn+'</option>'+data.filter(x=>!x.past&&x.free>0&&(!chosen||(x.origin===chosen.destination&&x.destination===chosen.origin))).map(x=>`<option value="${x.id}">${esc(x.time.slice(0,5))} · ${esc(point(x.origin))} → ${esc(point(x.destination))} · ${x.free}/${x.capacity}</option>`).join('')}
async function book(){err.innerHTML='';if(!chosen||!day.value||(round.checked&&!reverse.value)){err.innerHTML='<div class="alert">'+t[lang].chooseReturn+'</div>';return}submit.disabled=true;try{let r=await fetch(B+'/api/book',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({card:card.value,date:day.value,forward:chosen.id,reverse:round.checked?Number(reverse.value):null,reverse_date:round.checked?returnDay.value:null,lang,direction:dir.value==='reverse'?'reverse':'forward'})});let x=await r.json();if(!r.ok)throw Error(x.detail||'Error');location.href=x.url}catch(e){err.innerHTML='<div class="alert">'+esc(e.message)+'</div>';load()}finally{submit.disabled=false}}translate();loadStopNames();load();</script></body></html>'''
TICKET='''<!doctype html><html><head>'''+STYLE+'''<title>Бронь / Rezervasyon</title></head><body><header><span>🚌 TSM BUS</span><button class="headbutton" onclick="toggle()">RU / TR</button></header><main><div class="box"><div class="success" id="ok"></div><div class="direction" id="direction"></div><div id="legs"></div><hr><p><b id="cardLabel"></b> <span id="cardVal"></span></p><p><b id="codeLabel"></b> <span id="codeVal"></span></p><div id="qrTools"><img class="qr" id="qr" alt="QR"><div class="row"><button onclick="dl('pdf')">↓ PDF</button><button onclick="dl('jpg')">↓ JPG</button></div></div><button class="secondary" style="width:100%;margin-top:10px" onclick="copy()" id="copy"></button><button id="cancelTicketButton" style="width:100%;margin-top:10px;background:#a72828" onclick="cancelTicket()"></button><div id="cancelError"></div><p class="muted small" id="cancelNote"></p><p class="muted small" id="expiry"></p></div></main><script>let data=/*DATA*/,L=data.lang;let d={ru:{ok:'✓ ПОСАДКА РАЗРЕШЕНА',one:'→ В ОДНУ СТОРОНУ',both:'⇄ В ОБЕ СТОРОНЫ',card:'Пропуск:',code:'Бронь:',seat:'Запись',copy:'Скопировать ссылку',exp:'Ссылка действует 30 минут после последнего отправления.',cancel:'Отменить бронь',cancelled:'БРОНЬ ОТМЕНЕНА',expired:'СРОК ДЕЙСТВИЯ ССЫЛКИ ИСТЁК',cancelConfirm:'Отменить бронь? При поездке в обе стороны отменятся оба рейса.',started:'После отправления первого рейса отмена недоступна.'},tr:{ok:'✓ BİNİŞ ONAYLANDI',one:'→ TEK YÖN',both:'⇄ GİDİŞ - DÖNÜŞ',card:'Kart:',code:'Rezervasyon:',seat:'Kayıt',copy:'Bağlantıyı kopyala',exp:'Bağlantı son kalkıştan sonra 30 dakika geçerlidir.',cancel:'Rezervasyonu iptal et',cancelled:'REZERVASYON İPTAL EDİLDİ',expired:'BAĞLANTI SÜRESİ DOLDU',cancelConfirm:'Rezervasyon iptal edilsin mi? Gidiş-dönüşte iki sefer de iptal edilir.',started:'İlk seferin kalkışından sonra iptal edilemez.'}};function e(v){return String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function point(v){return L==='tr'?(data.stop_translations?.[v]||v):v}function render(){let x=d[L],invalid=data.cancelled||data.expired;ok.textContent=data.expired?x.expired:data.cancelled?x.cancelled:x.ok;ok.classList.toggle('cancelled-banner',!!invalid);qrTools.style.display=invalid?'none':'block';document.getElementById('copy').style.display=data.expired?'none':'block';cancelTicketButton.style.display=!invalid&&data.can_cancel?'block':'none';cancelTicketButton.textContent=x.cancel;cancelNote.textContent=!invalid&&!data.can_cancel?x.started:'';direction.textContent=data.legs.length>1?x.both:x.one;cardLabel.textContent=x.card;cardVal.textContent=data.card;codeLabel.textContent=x.code;codeVal.textContent=data.code;document.getElementById('copy').textContent=x.copy;expiry.textContent=x.exp;legs.innerHTML=data.legs.map(a=>`<div class="card"><b>${e(point(a.origin))} → ${e(point(a.destination))}</b><br>${e(a.date)} · <strong>${e(a.departure.slice(0,5))}</strong><br><span class="muted">${x.seat}: ${a.sequence} / ${a.capacity}</span>${a.comment?'<p>'+e(a.comment)+'</p>':''}</div>`).join('')}async function cancelTicket(){if(!confirm(d[L].cancelConfirm))return;cancelTicketButton.disabled=true;cancelError.innerHTML='';try{let r=await fetch('/bus/api/ticket/'+data.token+'/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:true})});let x=await r.json();if(!r.ok)throw Error(x.detail||'Error');data.cancelled=true;data.can_cancel=false;render()}catch(x){cancelError.innerHTML='<p class="alert">'+e(x.message)+'</p>';await refreshComments()}finally{cancelTicketButton.disabled=false}}function toggle(){L=L==='ru'?'tr':'ru';render()}function dl(f){location.href='/bus/ticket/'+data.token+'/download/'+f+'?lang='+L}async function copy(){try{await navigator.clipboard.writeText(location.href)}catch(e){prompt('URL',location.href)}}async function refreshComments(){try{let r=await fetch('/bus/api/ticket/'+data.token+'/comments');if(r.status===404){data.expired=true;data.can_cancel=false;render();return}if(!r.ok)return;let v=await r.json();let changed=data.cancelled!==v.cancelled||data.can_cancel!==v.can_cancel;data.cancelled=v.cancelled;data.can_cancel=v.can_cancel;for(let item of v.comments){let leg=data.legs.find(x=>x.id===item.id);if(leg&&leg.comment!==item.comment){leg.comment=item.comment;changed=true}}if(changed)render()}catch(e){}}document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshComments()});setInterval(refreshComments,10000);if(!data.cancelled)qr.src='/bus/ticket/'+data.token+'/qr';render();</script></body></html>'''
ADMIN='''<!doctype html><html><head>'''+STYLE+'''<title>Админ / Yönetim</title></head><body><header><span>🚌 TSM BUS · ADMIN</span><button class="headbutton" onclick="lang=lang==='ru'?'tr':'ru';renderLanguage()">RU / TR</button></header><style>main.admin-main{max-width:1100px;padding:12px 12px 40px}.admin-main h1{font-size:21px;margin:3px 0 10px}.admin-main h2{font-size:16px;margin:0 0 8px}.admin-main .box{padding:10px;margin:7px 0}.admin-main label{font-size:12px;margin:5px 0 3px}.admin-main input,.admin-main select,.admin-main textarea{min-width:0;padding:7px;font-size:13px}.admin-main button{padding:7px 9px;font-size:13px}.admin-main .row{gap:7px}.admin-tabs{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:6px;margin-bottom:7px}.admin-tabs button{min-width:0;overflow-wrap:anywhere}@media(max-width:650px){.admin-tabs{grid-template-columns:repeat(2,minmax(0,1fr))}}.qr-table{table-layout:fixed;word-break:break-word}.qr-table th:first-child,.qr-table td:first-child{width:34%}.qr-table td{vertical-align:top}.qr-table .status{display:inline-block;margin:4px 0}.qr-table .cancel-booking{background:#fff0ed;color:#a63129;margin-top:4px}.qr-small{max-width:180px;width:100%;display:block}.route-search{margin-bottom:7px}.schedule-columns{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}.schedule-columns h3{font-size:13px;margin:1px 0 5px;color:#07549b}.mini-route{background:#f6f9fc;border:1px solid #dce5ee;border-radius:7px;padding:6px;margin:0 0 5px;font-size:12px;overflow-wrap:anywhere}.mini-route.forward-route{background:#edf5ff;border-color:#bbd8f3}.mini-route.reverse-route{background:#edf8f3;border-color:#bce1cf}.mini-route strong{font-size:15px}.mini-route .route-actions{display:flex;gap:4px;margin-top:4px}.mini-route .route-actions button{padding:4px 6px;font-size:11px}.mini-route .delete-route{background:#fff0ed;color:#a63129}.edit-box{background:#edf6ff;border:1px solid #bbd9f0;border-left:4px solid #438dc6}.edit-box.is-editing{background:#e5f1fd;border-color:#72aad5;border-left-color:#07549b}.admin-main .edit-box h2{color:#07549b;margin:0 0 6px}.compact-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px 7px}.compact-form>div{min-width:0}.full{grid-column:1/-1}.admin-main details{margin-top:7px}.admin-main summary{cursor:pointer;color:#07549b;font-weight:600}.admin-main .alert{padding:8px;margin:6px 0}.admin-main hr{border:0;border-top:1px solid #e1e8ef;margin:8px 0}.admin-main .status{font-size:10px}.admin-main .status.inactive{background:#ffe5e2;color:#b32922}.admin-main .small{font-size:12px}</style><main class="admin-main"><h1 data-i="admin"></h1><nav class="admin-tabs"><button id="scheduleTab" onclick="showTab('schedule')" data-i="scheduleTab"></button><button id="whiteTab" class="secondary" onclick="showTab('white')" data-i="whitelist"></button><button id="qrTab" class="secondary" onclick="showTab('qr')" data-i="qrList"></button><button id="stopsTab" class="secondary" onclick="showTab('stops')" data-i="stops"></button></nav><div id="managePanel"><div class="box"><h2 data-i="schedule"></h2><input id="routeSearch" class="route-search" type="search" data-i-placeholder="findRoute" oninput="renderRoutes()"><div id="routeActionError"></div><div id="routes"></div></div><section class="box edit-box" id="routeForm"><h2 id="formTitle"></h2><div class="compact-form"><div><label data-i="name"></label><input id="name"></div><div><label data-i="direction"></label><select id="route_direction" onchange="syncRouteFromDirection()"><option value="forward" data-i="directRoute"></option><option value="reverse" data-i="reverseRoute"></option></select></div><div><label data-i="origin"></label><select id="origin" onchange="syncRouteFromOrigin()"><option value="" data-i="chooseOrigin"></option><option value="Камп" data-i="camp"></option><option value="Магазин" data-i="shop"></option></select></div><div><label data-i="destination"></label><select id="destination" onchange="syncRouteFromDestination()"><option value="" data-i="chooseOrigin"></option><option value="Камп" data-i="camp"></option><option value="Магазин" data-i="shop"></option></select></div><div><label data-i="departureTime"></label><input type="time" id="departure_time"></div><div><label data-i="serviceDate"></label><input type="date" id="service_date"></div><div><label data-i="capacity"></label><input type="number" id="capacity" value="50" min="1" max="500"></div><div><label><input type="checkbox" id="active" checked><span data-i="active"></span></label></div><div class="full"><label data-i="comment"></label><input id="comment" maxlength="160" data-i-placeholder="commentHint"></div><div class="full"><button onclick="save()" data-i="save"></button> <button id="resetRoute" class="secondary" onclick="resetForm()"></button><div id="formErr"></div></div></div></section><div class="box"><h2 data-i="history"></h2><div class="row"><div><label data-i="from"></label><input type="date" id="date_from"></div><div><label data-i="to"></label><input type="date" id="date_to"></div><div><label data-i="route"></label><select id="route_id"><option value="">Все / Tümü</option></select></div><div><label data-i="card"></label><input id="filter_card"></div><div><label data-i="timeFrom"></label><input type="time" id="time_from"></div><div><label data-i="timeTo"></label><input type="time" id="time_to"></div><div><label data-i="origin"></label><input id="filter_origin"></div><div><label data-i="historyStatus"></label><select id="histStatus"><option value="all" data-i="allStatuses"></option><option value="booked" data-i="bookedHistory"></option><option value="cancelled" data-i="cancelledQr"></option></select></div></div><button style="margin-top:14px" onclick="historyLoad()" data-i="filter"></button><p class="muted small" data-i="retention"></p><div id="hist" class="scroll"></div></div></div><section id="whitePanel" style="display:none"><div class="row"><div class="box"><h2 data-i="excelTitle"></h2><p class="muted small" data-i="excelHint"></p><input type="file" id="excelFile" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onchange="inspectExcel()"><div id="excelChoices" style="display:none"><label data-i="sheet"></label><select id="excelSheet" onchange="selectExcelSheet()"></select><label data-i="column"></label><select id="excelColumn" onchange="selectExcelColumn()"></select><label data-i="firstRow"></label><input id="excelFirstRow" type="number" value="1" min="1" max="50000" oninput="invalidatePreview()"><label data-i="mode"></label><select id="excelMode"><option value="append" data-i="append"></option><option value="replace" data-i="replace"></option></select><button style="margin-top:8px" onclick="previewExcel()" data-i="preview"></button><div id="excelStats" class="small"></div><button id="excelImport" disabled onclick="importExcel()" data-i="importExcel"></button></div><div id="excelError"></div></div><div class="box"><h2 data-i="whitelist"></h2><div id="whiteCount" class="muted small"></div><label data-i="search"></label><input id="whiteSearch" inputmode="numeric" oninput="searchWhitelist()"><div id="whiteResults" class="small"></div><details><summary data-i="editList"></summary><p class="muted small" data-i="whitelistHint"></p><textarea id="allowed" rows="10" spellcheck="false"></textarea><button onclick="saveWhitelist()" data-i="saveWhitelist"></button></details><div id="whiteStatus" class="small"></div></div></div></section><section id="qrPanel" style="display:none"><div class="box"><h2 data-i="qrList"></h2><div class="compact-form"><div class="full"><label data-i="qrSearch"></label><input id="qrSearchInput" type="search" onkeydown="if(event.key==='Enter')loadQr(0)"></div><div><label data-i="qrDate"></label><input type="date" id="qrDate"></div><div><label data-i="qrTime"></label><input type="time" id="qrTime"></div><div><label data-i="qrStatus"></label><select id="qrStatus"><option value="all" data-i="allStatuses"></option><option value="active" data-i="validQr"></option><option value="expired" data-i="expiredQr"></option><option value="cancelled" data-i="cancelledQr"></option></select></div><div><label>&nbsp;</label><button onclick="loadQr(0)" data-i="filter"></button></div></div><div id="qrCount" class="muted small"></div><div id="qrActionError"></div><div id="qrResults" class="scroll"></div><div class="row"><button id="qrPrev" class="secondary" onclick="loadQr(qrOffset-30)" data-i="prevPage"></button><button id="qrNext" class="secondary" onclick="loadQr(qrOffset+30)" data-i="nextPage"></button></div></div><div class="box"><h2 data-i="verify"></h2><p class="muted small" data-i="verifyHint"></p><button onclick="scan()" data-i="camera"></button> <button class="secondary" onclick="stopScan()" data-i="stop"></button><video id="video" playsinline></video><label data-i="qr"></label><input id="qrtext"><button style="margin-top:10px" onclick="verify()" data-i="check"></button><div id="result"></div></div></section><section id="stopsPanel" style="display:none"><div class="row"><div class="box"><h2 data-i="stopList"></h2><input id="stopSearchInput" type="search" data-i-placeholder="stopSearch" oninput="renderStops()"><div id="stopListItems"></div><p class="muted small" data-i="stopHint"></p></div><div class="box edit-box"><h2 id="stopFormTitle"></h2><label data-i="stopRu"></label><input id="stopNameRu" maxlength="80"><label data-i="stopTr"></label><input id="stopNameTr" maxlength="80"><label><input id="stopActive" type="checkbox" checked><span data-i="active"></span></label><button onclick="saveStop()" data-i="save"></button> <button id="resetStopButton" class="secondary" onclick="resetStop()"></button><div id="stopError"></div></div></div></section></main><script>
const B='/bus';let lang=localStorage.getItem('bus-lang')||'ru',editId=null,routesData=[],historyRoutes=[],stream=null,whiteCards=[],whiteFile=null,whiteSheets=[],qrOffset=0,qrTotal=0,stopsData=[],stopEditId=null;const T={ru:{admin:'Управление автобусами',scheduleTab:'Расписание',stops:'Остановки',stopList:'Справочник остановок',stopSearch:'Найти остановку',stopNew:'Новая остановка',stopEdit:'Редактирование остановки',stopRu:'Название на русском',stopTr:'Название на турецком',stopHint:'Неактивные остановки не предлагаются для новых рейсов.',qrList:'Бронирования',qrSearch:'Номер брони, пропуска или маршрут',qrDate:'Дата рейса',qrTime:'Время',qrStatus:'Статус',allStatuses:'Все',validQr:'Действует',expiredQr:'Не действует',cancelledQr:'Отменена',bookedHistory:'Забронирована',cancelBooking:'Отменить бронь',cancelBookingConfirm:'Отменить бронь целиком? После начала первого рейса отмена недоступна.',historyStatus:'Статус брони',qrApplication:'Заявка',qrDecoded:'Расшифровка QR',qrCard:'Пропуск',qrPosition:'Запись',showQr:'Показать QR',hideQr:'Скрыть QR',prevPage:'← Назад',nextPage:'Далее →',findRoute:'Поиск по рейсу, направлению или времени',forwardList:'В магазин',reverseList:'В камп',excelTitle:'Загрузка Excel',excelHint:'Файл .xlsx до 5 МБ. Выберите лист и столбец с номерами.',sheet:'Лист',column:'Столбец с номерами',firstRow:'Начать со строки',mode:'Режим загрузки',append:'Добавить к списку',replace:'Заменить список',preview:'Проверить файл',importExcel:'Загрузить номера',search:'Найти номер',editList:'Редактировать список вручную',found:'Найдено: ',totalCards:'Всего номеров: ',imported:'Сохранено. Всего номеров: ',previewValid:'Корректных строк: ',previewUnique:'Уникальных: ',previewDuplicate:'Повторов: ',previewInvalid:'Ошибочных строк (не будут добавлены): ',previewRows:'Номера строк с ошибками: ',previewExisting:'Уже в списке: ',previewNew:'Новых: ',replaceConfirm:'Заменить весь белый список номерами из Excel?',schedule:'Расписание · до 10 рейсов',name:'Название рейса',origin:'Пункт отправления',destination:'Пункт назначения',direction:'Направление рейса',chooseOrigin:'Выберите пункт',camp:'Камп',shop:'Магазин',directRoute:'Прямой',reverseRoute:'Обратный',departureTime:'Время отправления',newForm:'Новый рейс',editForm:'Редактирование рейса',clearForm:'Очистить',cancelEdit:'Отменить',capacity:'Мест',serviceDate:'Дата рейса (необязательно; без даты — ежедневно)',comment:'Комментарий к рейсу (необязательно)',commentHint:'Например: автобус 33 ABC 123, парковка № 2',active:'Активен',save:'Сохранить',new:'Новый рейс',whitelist:'Разрешённые пропуска',whitelistHint:'По одному номеру в строке. Записаться могут только номера из этого списка.',saveWhitelist:'Сохранить список',whiteSaved:'Список сохранён. Номеров: ',history:'История поездок',from:'Дата с',to:'Дата по',route:'Рейс',card:'Номер пропуска',timeFrom:'Время с',timeTo:'Время по',filter:'Показать',retention:'История хранится 6 месяцев.',verify:'Проверка QR',verifyHint:'Отсканируйте QR камерой или вставьте его содержимое.',camera:'Открыть камеру',stop:'Остановить',qr:'Содержимое QR',check:'Проверить',edit:'Изменить',deleteRoute:'Удалить',deleteConfirm:'Удалить рейс?',deleted:'Удалён',inactive:'Не действует',on:'Активен',fortune:'Пожелание',noScan:'Камера или сканирование QR не поддерживается этим браузером.'},tr:{admin:'Otobüs yönetimi',scheduleTab:'Seferler',stops:'Duraklar',stopList:'Durak listesi',stopSearch:'Durak ara',stopNew:'Yeni durak',stopEdit:'Durağı düzenle',stopRu:'Rusça adı',stopTr:'Türkçe adı',stopHint:'Pasif duraklar yeni seferlerde gösterilmez.',qrList:'Rezervasyonlar',qrSearch:'Rezervasyon, kart veya güzergâh',qrDate:'Sefer tarihi',qrTime:'Saat',qrStatus:'Durum',allStatuses:'Tümü',validQr:'Geçerli',expiredQr:'Süresi doldu',cancelledQr:'İptal edildi',bookedHistory:'Rezerve edildi',cancelBooking:'Rezervasyonu iptal et',cancelBookingConfirm:'Rezervasyon tamamen iptal edilsin mi? İlk seferden sonra iptal edilemez.',historyStatus:'Rezervasyon durumu',qrApplication:'Rezervasyon',qrDecoded:'QR içeriği',qrCard:'Kart',qrPosition:'Kayıt',showQr:'QR göster',hideQr:'QR gizle',prevPage:'← Önceki',nextPage:'Sonraki →',findRoute:'Sefer, yön veya saat ara',forwardList:'Mağazaya',reverseList:'Kampa',excelTitle:'Excel yükle',excelHint:'En fazla 5 MB .xlsx dosyası. Kart sütununu ve sayfayı seçin.',sheet:'Sayfa',column:'Kart numarası sütunu',firstRow:'Başlangıç satırı',mode:'Yükleme modu',append:'Listeye ekle',replace:'Listeyi değiştir',preview:'Dosyayı kontrol et',importExcel:'Kartları yükle',search:'Kart numarası ara',editList:'Listeyi elle düzenle',found:'Bulunan: ',totalCards:'Toplam kart: ',imported:'Kaydedildi. Toplam kart: ',previewValid:'Geçerli satır: ',previewUnique:'Benzersiz: ',previewDuplicate:'Tekrar: ',previewInvalid:'Hatalı satır (eklenmeyecek): ',previewRows:'Hatalı satır numaraları: ',previewExisting:'Listede mevcut: ',previewNew:'Yeni: ',replaceConfirm:'İzin listesinin tamamı Excel içeriğiyle değiştirilsin mi?',schedule:'Seferler · en fazla 10',name:'Sefer adı',origin:'Kalkış noktası',destination:'Varış noktası',direction:'Sefer yönü',chooseOrigin:'Nokta seçin',camp:'Kamp',shop:'Mağaza',directRoute:'Gidiş',reverseRoute:'Dönüş',departureTime:'Kalkış saati',newForm:'Yeni sefer',editForm:'Seferi düzenle',clearForm:'Temizle',cancelEdit:'İptal',capacity:'Koltuk',serviceDate:'Sefer tarihi (isteğe bağlı; boşsa her gün)',comment:'Sefer açıklaması (isteğe bağlı)',commentHint:'Örneğin: 33 ABC 123, 2 numaralı otopark',active:'Aktif',save:'Kaydet',new:'Yeni sefer',whitelist:'İzin verilen kartlar',whitelistHint:'Her satıra bir kart numarası yazın. Yalnızca listedeki kartlar rezervasyon yapabilir.',saveWhitelist:'Listeyi kaydet',whiteSaved:'Liste kaydedildi. Kart sayısı: ',history:'Yolculuk geçmişi',from:'Başlangıç tarihi',to:'Bitiş tarihi',route:'Sefer',card:'Kart numarası',timeFrom:'Başlangıç saati',timeTo:'Bitiş saati',filter:'Göster',retention:'Geçmiş 6 ay saklanır.',verify:'QR doğrulama',verifyHint:'QR kodunu kamerayla tarayın veya içeriğini yapıştırın.',camera:'Kamerayı aç',stop:'Durdur',qr:'QR içeriği',check:'Doğrula',edit:'Düzenle',deleteRoute:'Sil',deleteConfirm:'Sefer silinsin mi?',deleted:'Silindi',inactive:'Pasif',on:'Aktif',fortune:'Dilek',noScan:'Bu tarayıcı kamera veya QR taramayı desteklemiyor.'}};function e(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function renderLanguage(){localStorage.setItem('bus-lang',lang);document.querySelectorAll('[data-i]').forEach(x=>x.textContent=T[lang][x.dataset.i]);document.querySelectorAll('[data-i-placeholder]').forEach(x=>x.placeholder=T[lang][x.dataset.iPlaceholder]);renderRoutes();searchWhitelist();updateFormTitle();renderStops();populateStopChoices();updateStopFormTitle();if(qrPanel.style.display!=='none')loadQr(qrOffset)}function updateFormTitle(){formTitle.textContent=editId===null?T[lang].newForm:T[lang].editForm;resetRoute.textContent=editId===null?T[lang].clearForm:T[lang].cancelEdit;routeForm.classList.toggle('is-editing',editId!==null)}async function api(path,opts){let r=await fetch(B+path,opts);let x=await r.json();if(!r.ok)throw Error(x.detail||'Error');return x}function setRouteStop(id,value){let el=document.getElementById(id),old=el.querySelector('[data-legacy]');if(old)old.remove();if(value&&!Array.from(el.options).some(o=>o.value===value)){let opt=document.createElement('option');opt.value=value;opt.textContent=value;opt.dataset.legacy='1';el.appendChild(opt)}el.value=value||''}
function populateStopChoices(){if(!stopsData.length)return;for(let id of ['origin','destination']){let el=document.getElementById(id),current=el.value;el.innerHTML='<option value="">'+e(T[lang].chooseOrigin)+'</option>'+stopsData.filter(x=>x.active).map(x=>`<option value="${e(x.name_ru)}">${e(lang==='tr'?x.name_tr:x.name_ru)}</option>`).join('');setRouteStop(id,current)}}
async function loadStops(){stopsData=await api('/api/admin/stops');populateStopChoices();renderStops()}
function updateStopFormTitle(){stopFormTitle.textContent=stopEditId===null?T[lang].stopNew:T[lang].stopEdit;resetStopButton.textContent=stopEditId===null?T[lang].clearForm:T[lang].cancelEdit}
function renderStops(){let term=stopSearchInput.value.trim().toLocaleLowerCase();let list=stopsData.filter(x=>!term||[x.name_ru,x.name_tr].some(y=>y.toLocaleLowerCase().includes(term)));stopListItems.innerHTML=list.map(x=>`<div class="mini-route"><button class="secondary" onclick="editStop(${x.id})">${T[lang].edit}</button><b>${e(lang==='tr'?x.name_tr:x.name_ru)}</b> <span class="status ${x.active?'':'inactive'}">${x.active?T[lang].on:T[lang].inactive}</span><div class="muted">${e(lang==='tr'?x.name_ru:x.name_tr)}</div></div>`).join('')}
function editStop(id){let x=stopsData.find(z=>z.id===id);if(!x)return;stopEditId=id;stopNameRu.value=x.name_ru;stopNameTr.value=x.name_tr;stopActive.checked=!!x.active;stopError.innerHTML='';updateStopFormTitle();stopNameRu.focus()}
function resetStop(){stopEditId=null;stopNameRu.value='';stopNameTr.value='';stopActive.checked=true;stopError.innerHTML='';updateStopFormTitle()}
async function saveStop(){stopError.innerHTML='';let id=stopEditId,old=stopsData.find(z=>z.id===id),data={name_ru:stopNameRu.value.trim(),name_tr:stopNameTr.value.trim(),active:stopActive.checked};let oldOrigin=document.getElementById('origin').value,oldDestination=document.getElementById('destination').value;try{await api('/api/admin/stops'+(id?'/'+id:''),{method:id?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});resetStop();await loadStops();if(old&&old.name_ru!==data.name_ru){if(oldOrigin===old.name_ru)setRouteStop('origin',data.name_ru);if(oldDestination===old.name_ru)setRouteStop('destination',data.name_ru);await loadRoutes()}}catch(x){stopError.innerHTML='<p class="alert">'+e(x.message)+'</p>'}}
async function loadRoutes(){[routesData,historyRoutes]=await Promise.all([api('/api/admin/routes'),api('/api/admin/route-options')]);renderRoutes()}function showTab(which){managePanel.style.display=which==='schedule'?'block':'none';whitePanel.style.display=which==='white'?'block':'none';qrPanel.style.display=which==='qr'?'block':'none';stopsPanel.style.display=which==='stops'?'block':'none';scheduleTab.className=which==='schedule'?'':'secondary';whiteTab.className=which==='white'?'':'secondary';qrTab.className=which==='qr'?'':'secondary';stopsTab.className=which==='stops'?'':'secondary';if(which==='qr')loadQr(0)}function renderRoutes(){let selectedRoute=route_id.value;let term=routeSearch.value.trim().toLocaleLowerCase();let visible=x=>!term||[x.name,x.origin,x.destination,x.forward_time,x.reverse_time,x.service_date].some(v=>String(v||'').toLocaleLowerCase().includes(term));let item=x=>`<div class="mini-route ${x.direction==='reverse'?'reverse-route':'forward-route'}"><strong>${e((x.direction==='reverse'?x.reverse_time:x.forward_time).slice(0,5))}</strong> <span class="status ${x.active?'':'inactive'}">${x.active?T[lang].on:T[lang].inactive}</span><div>${e(x.origin)} → ${e(x.destination)}</div><div>${e(x.name)} · ${x.capacity}${x.service_date?' · '+e(x.service_date):''}</div><div class="route-actions"><button class="secondary" onclick="edit(${x.id})">${T[lang].edit}</button><button class="delete-route" onclick="deleteRoute(${x.id})">${T[lang].deleteRoute}</button></div></div>`;let forward=routesData.filter(x=>x.direction!=='reverse'&&visible(x)).sort((a,b)=>a.forward_time.localeCompare(b.forward_time));let backward=routesData.filter(x=>x.direction==='reverse'&&visible(x)).sort((a,b)=>a.reverse_time.localeCompare(b.reverse_time));routes.innerHTML=`<div class="schedule-columns"><div><h3>${T[lang].forwardList}</h3>${forward.map(item).join('')}</div><div><h3>${T[lang].reverseList}</h3>${backward.map(item).join('')}</div></div>`;route_id.innerHTML='<option value="">Все / Tümü</option>'+historyRoutes.map(x=>`<option value="${x.id}">${e(x.name)} · ${e(x.origin)} → ${e(x.destination)} · ${e((x.direction==='reverse'?x.reverse_time:x.forward_time).slice(0,5))}${x.service_date?' · '+e(x.service_date):''}${x.deleted_at?' · '+T[lang].deleted:''}</option>`).join('');route_id.value=selectedRoute}async function deleteRoute(id){let x=routesData.find(r=>r.id===id);if(!x)return;let when=(x.direction==='reverse'?x.reverse_time:x.forward_time).slice(0,5);if(!confirm(T[lang].deleteConfirm+' '+when+' · '+x.origin+' → '+x.destination))return;routeActionError.innerHTML='';try{await api('/api/admin/routes/'+id,{method:'DELETE'});if(editId===id)resetForm();await loadRoutes()}catch(x){routeActionError.innerHTML='<p class="alert">'+e(x.message)+'</p>'}}function syncRouteFromOrigin(){let v=document.getElementById('origin').value,d=document.getElementById('destination');if(!v)return;if(v==='Камп'||v==='Магазин'){route_direction.value=v==='Камп'?'forward':'reverse';if(!d.value||['Камп','Магазин'].includes(d.value))d.value=v==='Камп'?'Магазин':'Камп'}else if(d.value===v)d.value=''}function syncRouteFromDestination(){let v=document.getElementById('destination').value,o=document.getElementById('origin');if(!v)return;if((v==='Камп'||v==='Магазин')&&(!o.value||['Камп','Магазин'].includes(o.value))){let direct=v==='Магазин';o.value=direct?'Камп':'Магазин';route_direction.value=direct?'forward':'reverse'}else if(o.value===v)o.value=''}function syncRouteFromDirection(){let o=document.getElementById('origin'),d=document.getElementById('destination');if((!o.value||['Камп','Магазин'].includes(o.value))&&(!d.value||['Камп','Магазин'].includes(d.value))){let direct=route_direction.value==='forward';o.value=direct?'Камп':'Магазин';d.value=direct?'Магазин':'Камп'}}function edit(id){let x=routesData.find(r=>r.id===id);editId=id;updateFormTitle();setRouteStop('origin',x.origin);setRouteStop('destination',x.destination);for(let k of ['name','origin','destination','capacity','comment','service_date'])document.getElementById(k).value=x[k];route_direction.value=x.direction==='reverse'?'reverse':'forward';departure_time.value=x.direction==='reverse'?x.reverse_time:x.forward_time;active.checked=!!x.active;document.getElementById('name').focus()}function resetForm(){editId=null;updateFormTitle();for(let id of ['origin','destination']){let opt=document.getElementById(id).querySelector('[data-legacy]');if(opt)opt.remove()}for(let k of ['name','origin','destination','departure_time','comment','service_date'])document.getElementById(k).value='';route_direction.value='forward';capacity.value=50;active.checked=true;formErr.innerHTML=''}async function save(){formErr.innerHTML='';let o={};for(let k of ['name','origin','destination','comment','service_date'])o[k]=document.getElementById(k).value;o.direction=route_direction.value;o.forward_time=departure_time.value;o.reverse_time=departure_time.value;o.capacity=Number(capacity.value);o.active=active.checked;try{await api('/api/admin/routes'+(editId?'/'+editId:''),{method:editId?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(o)});resetForm();loadRoutes()}catch(x){formErr.innerHTML='<p class="alert">'+e(x.message)+'</p>'}}async function loadWhitelist(){try{let x=await api('/api/admin/whitelist');whiteCards=x.cards;allowed.value=x.cards.join('\\n');searchWhitelist()}catch(x){whiteStatus.innerHTML='<p class="alert">'+e(x.message)+'</p>'}}async function saveWhitelist(){try{let list=allowed.value.split(/[\\s,;]+/).filter(Boolean);let x=await api('/api/admin/whitelist',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({cards:list})});whiteStatus.textContent=T[lang].whiteSaved+x.count;await loadWhitelist()}catch(x){whiteStatus.innerHTML='<p class="alert">'+e(x.message)+'</p>'}}function searchWhitelist(){whiteCount.textContent=T[lang].totalCards+whiteCards.length;let term=whiteSearch.value.trim();let matches=term?whiteCards.filter(x=>x.includes(term)):[];whiteResults.textContent=term?T[lang].found+matches.length+(matches.length?' · '+matches.slice(0,40).join(', ')+(matches.length>40?' …':''):''):''}
function invalidatePreview(){excelImport.disabled=true;excelStats.textContent=''}
async function excelCall(path,params){let r=await fetch(B+path+(params?'?'+new URLSearchParams(params):''),{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:whiteFile});let x=await r.json();if(!r.ok)throw Error(x.detail||'Error');return x}
function excelErrorShow(x){excelError.innerHTML='<p class="alert">'+e(x.message)+'</p>'}
async function inspectExcel(){whiteFile=excelFile.files[0];excelChoices.style.display='none';excelError.innerHTML='';invalidatePreview();if(!whiteFile)return;try{let x=await excelCall('/api/admin/whitelist/excel/inspect');whiteSheets=x.sheets;excelSheet.innerHTML=whiteSheets.map(z=>`<option value="${z.index}">${e(z.name)}</option>`).join('');if(!whiteSheets.length)throw Error('Нет листов / Sayfa yok');excelChoices.style.display='block';selectExcelSheet()}catch(x){excelErrorShow(x)}}
function selectExcelSheet(){let sheet=whiteSheets.find(z=>z.index===Number(excelSheet.value));excelColumn.innerHTML=(sheet?.columns||[]).map(z=>`<option value="${z.index}">${e(z.letter)} · ${e(z.samples.join(' / '))}</option>`).join('');selectExcelColumn()}
function selectExcelColumn(){let sheet=whiteSheets.find(z=>z.index===Number(excelSheet.value));let col=sheet?.columns.find(z=>z.index===Number(excelColumn.value));excelFirstRow.value=col?.first_is_header?2:1;invalidatePreview()}
function excelParams(){return {sheet:excelSheet.value,column:excelColumn.value,first_row:excelFirstRow.value}}
async function previewExcel(){excelError.innerHTML='';invalidatePreview();let selectedFile=whiteFile,selectedParams=excelParams(),signature=JSON.stringify(selectedParams);try{let x=await excelCall('/api/admin/whitelist/excel/preview',selectedParams);if(whiteFile!==selectedFile||JSON.stringify(excelParams())!==signature)return;excelStats.innerHTML=`<p>${T[lang].previewValid}${x.valid_rows}<br>${T[lang].previewUnique}${x.unique}<br>${T[lang].previewDuplicate}${x.duplicates}<br>${T[lang].previewExisting}${x.already_present}<br>${T[lang].previewNew}${x.new}<br>${T[lang].previewInvalid}${x.invalid}${x.invalid?'<br>'+T[lang].previewRows+e(x.invalid_rows.join(', ')):''}</p>`;excelImport.disabled=x.unique===0}catch(x){excelErrorShow(x)}}
async function importExcel(){if(excelImport.disabled)return;if(excelMode.value==='replace'&&!confirm(T[lang].replaceConfirm))return;excelImport.disabled=true;excelError.innerHTML='';try{let x=await excelCall('/api/admin/whitelist/excel/import',{...excelParams(),mode:excelMode.value});excelStats.textContent=T[lang].imported+x.total;await loadWhitelist()}catch(x){excelErrorShow(x)}}
async function loadQr(offset=0){qrOffset=Math.max(0,offset);let p=new URLSearchParams({q:qrSearchInput.value.trim(),trip_date:qrDate.value,trip_time:qrTime.value,status:qrStatus.value,offset:String(qrOffset)});try{let result=await api('/api/admin/qrcodes?'+p);qrTotal=result.total;qrCount.textContent=T[lang].found+qrTotal;let rows=result.items.map(x=>{let d=x.decoded;let code=e(d.booking);let trips=d.legs.map((l,i)=>`<div>${e(l.date)} ${e(l.time)} · ${e(x.routes[i]?.origin||'')} → ${e(x.routes[i]?.destination||'')}<br>${T[lang].qrPosition}: ${l.number} / ${l.capacity}</div>`).join('');let pic=x.active?`<details><summary>${T[lang].showQr}</summary><img class="qr-small" loading="lazy" src="${B}/api/admin/qrcodes/${encodeURIComponent(d.booking)}/image" alt="QR"></details>`:'';let action=x.can_cancel?`<button class="cancel-booking" onclick="cancelAdmin('${code}')">${T[lang].cancelBooking}</button>`:'';let state=x.cancelled?T[lang].cancelledQr:(x.active?T[lang].validQr:T[lang].expiredQr);return `<tr><td><b>${code}</b><br>${T[lang].qrCard}: ${e(d.card)}<br><span class="status ${x.active?'':'inactive'}">${state}</span>${pic}${action}</td><td>${trips}<div><b>${T[lang].fortune}:</b> ${e(lang==='tr'&&x.fortune_tr?x.fortune_tr:d.fortune)}</div></td></tr>`}).join('');qrResults.innerHTML=`<table class="qr-table"><thead><tr><th>${T[lang].qrApplication}</th><th>${T[lang].qrDecoded}</th></tr></thead><tbody>${rows}</tbody></table>`;qrPrev.disabled=qrOffset===0;qrNext.disabled=qrOffset+result.limit>=qrTotal}catch(x){qrResults.innerHTML='<p class="alert">'+e(x.message)+'</p>'}}
async function cancelAdmin(code){if(!confirm(T[lang].cancelBookingConfirm))return;qrActionError.innerHTML='';try{await api('/api/admin/bookings/'+encodeURIComponent(code)+'/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:true})});await loadQr(qrOffset);await historyLoad()}catch(x){qrActionError.innerHTML='<p class="alert">'+e(x.message)+'</p>';await loadQr(qrOffset)}}
async function historyLoad(){let p=new URLSearchParams();for(let k of ['date_from','date_to','route_id','time_from','time_to'])p.set(k,document.getElementById(k).value);p.set('card',filter_card.value);p.set('origin',filter_origin.value);p.set('status',histStatus.value);let a=await api('/api/admin/history?'+p);hist.innerHTML='<table><thead><tr><th>Дата / Tarih</th><th>Время / Saat</th><th>Рейс / Sefer</th><th>Маршрут / Güzergâh</th><th>Карта / Kart</th><th>№</th><th>Бронь / Rez.</th><th>'+T[lang].historyStatus+'</th></tr></thead><tbody>'+a.map(x=>`<tr><td>${e(x.date)}</td><td>${e(x.departure)}</td><td>${e(x.route_name)}</td><td>${e(x.origin)} → ${e(x.destination)}</td><td>${e(x.card)}</td><td>${x.sequence}/${x.capacity}</td><td>${e(x.code)}</td><td><span class="status ${x.cancelled_at?'inactive':''}">${x.cancelled_at?T[lang].cancelledQr:T[lang].bookedHistory}</span></td></tr>`).join('')+'</tbody></table>'}async function verify(){try{let x=await api('/api/admin/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:qrtext.value})});result.innerHTML='<div class="card"><b>✓ '+e(x.code)+'</b><p>'+e(x.card)+'</p>'+x.legs.map(a=>'<p>'+e(a.date)+' '+e(a.departure)+' · '+e(a.origin)+' → '+e(a.destination)+' · '+a.sequence+'/'+a.capacity+'</p>').join('')+'<p><b>'+T[lang].fortune+':</b> '+e(lang==='tr'?x.fortune_tr:x.fortune)+'</p></div>'}catch(x){result.innerHTML='<p class="alert">'+e(x.message)+'</p>'}}async function scan(){try{if(!('BarcodeDetector'in window))throw Error(T[lang].noScan);let detector=new BarcodeDetector({formats:['qr_code']});stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'}});video.srcObject=stream;await video.play();async function loop(){if(!stream)return;try{let a=await detector.detect(video);if(a.length){qrtext.value=a[0].rawValue;stopScan();verify();return}}catch(e){}requestAnimationFrame(loop)}loop()}catch(x){result.innerHTML='<p class="alert">'+e(x.message)+'</p>'}}function stopScan(){if(stream){stream.getTracks().forEach(x=>x.stop());stream=null;video.srcObject=null}}renderLanguage();loadStops();loadRoutes();loadWhitelist();historyLoad();</script></body></html>'''
