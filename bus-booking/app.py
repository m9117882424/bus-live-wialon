import os, sqlite3, secrets, json, io, base64, html, calendar, threading, textwrap, hmac, hashlib, re
import time as clock
from collections import OrderedDict
from datetime import datetime, date, timedelta, time
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo
from contextlib import contextmanager
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse, JSONResponse, Response, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, StrictStr, StrictBool, Field
from cryptography.fernet import Fernet, InvalidToken
import qrcode
from PIL import Image, ImageDraw, ImageFont
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from fortunes import FORTUNES, fortune_tr, choose_fortune
from fortune_localizations import fortune_localized
from occupancy import occupancy_report
from scud import init_schema as init_scud,parse_workbook as parse_scud,save_import as save_scud,import_status as scud_status,ScudError,MAX_BYTES as SCUD_MAX_BYTES
from starlette.concurrency import run_in_threadpool

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
  c.execute('CREATE TABLE IF NOT EXISTS app_settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)')
  c.execute("INSERT OR IGNORE INTO app_settings(key,value) VALUES('booking_horizon_hours','48')")
  if not whitelist_exists:
   initial_cards=[x.strip() for x in os.environ.get('BUS_INITIAL_CARDS','').split(',') if x.strip()]
   if any(not x.isascii() or not x.isdigit() or not 4<=len(x)<=12 for x in initial_cards): raise RuntimeError('Invalid initial card list')
   c.executemany('INSERT OR IGNORE INTO allowed_cards(card) VALUES(?)',[(x,) for x in initial_cards])
  if 'cancelled_at' not in {x['name'] for x in c.execute('PRAGMA table_info(bookings)')}: c.execute('ALTER TABLE bookings ADD COLUMN cancelled_at TEXT')
  c.execute('CREATE INDEX IF NOT EXISTS bookings_active_card ON bookings(card,cancelled_at,expires)')
  c.execute('''CREATE TABLE IF NOT EXISTS cancelled_legs(archive_id INTEGER PRIMARY KEY, original_leg_id INTEGER NOT NULL, booking_id INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE, route_id INTEGER NOT NULL, direction TEXT NOT NULL, date TEXT NOT NULL, departure TEXT NOT NULL, origin TEXT NOT NULL, destination TEXT NOT NULL, sequence INTEGER NOT NULL, capacity INTEGER NOT NULL, route_name TEXT NOT NULL, card TEXT NOT NULL, comment TEXT NOT NULL DEFAULT '')''')
  c.execute('CREATE INDEX IF NOT EXISTS cancelled_legs_booking ON cancelled_legs(booking_id)')
  c.execute('''CREATE TABLE IF NOT EXISTS boardings(leg_id INTEGER PRIMARY KEY REFERENCES legs(id) ON DELETE CASCADE,booking_id INTEGER NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,card TEXT NOT NULL,boarded_at TEXT NOT NULL,source TEXT NOT NULL)''')
  c.execute('CREATE INDEX IF NOT EXISTS boardings_booking ON boardings(booking_id)')
  c.execute('''CREATE TABLE IF NOT EXISTS driver_accounts(
   id INTEGER PRIMARY KEY AUTOINCREMENT, login TEXT NOT NULL UNIQUE COLLATE NOCASE,
   display_name TEXT NOT NULL, password_salt TEXT NOT NULL, password_hash TEXT NOT NULL,
   password_iterations INTEGER NOT NULL, active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
   created_at TEXT NOT NULL, updated_at TEXT NOT NULL)''')
  c.execute('''CREATE TABLE IF NOT EXISTS driver_sessions(
   token_hash TEXT PRIMARY KEY, driver_id INTEGER NOT NULL REFERENCES driver_accounts(id) ON DELETE CASCADE,
   created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL)''')
  c.execute('CREATE INDEX IF NOT EXISTS driver_sessions_driver ON driver_sessions(driver_id)')
  c.execute('CREATE INDEX IF NOT EXISTS driver_sessions_expiry ON driver_sessions(expires_at)')
  # Keep historical attribution after deleting an account; IDs are never reused.
  boarding_columns={x['name'] for x in c.execute('PRAGMA table_info(boardings)')}
  for column,kind in [('driver_id','INTEGER'),('driver_login','TEXT')]:
   if column not in boarding_columns:c.execute('ALTER TABLE boardings ADD COLUMN '+column+' '+kind)
  c.execute("CREATE TABLE IF NOT EXISTS route_service_periods(route_id INTEGER PRIMARY KEY REFERENCES routes(id), starts_on TEXT NOT NULL, ends_on TEXT NOT NULL, CHECK(starts_on <= ends_on))")
  if 'deleted_at' not in {x['name'] for x in c.execute('PRAGMA table_info(routes)')}: c.execute('ALTER TABLE routes ADD COLUMN deleted_at TEXT')
  if 'direction' not in {x['name'] for x in c.execute('PRAGMA table_info(routes)')}: c.execute("ALTER TABLE routes ADD COLUMN direction TEXT NOT NULL DEFAULT 'both'")
  if 'service_date' not in {x['name'] for x in c.execute('PRAGMA table_info(routes)')}: c.execute("ALTER TABLE routes ADD COLUMN service_date TEXT NOT NULL DEFAULT ''")
  if 'weekday_mask' not in {x['name'] for x in c.execute('PRAGMA table_info(routes)')}: c.execute('ALTER TABLE routes ADD COLUMN weekday_mask INTEGER NOT NULL DEFAULT 127')
  for table in ('routes','legs'):
   if 'comment' not in {x['name'] for x in c.execute('PRAGMA table_info('+table+')')}: c.execute("ALTER TABLE "+table+" ADD COLUMN comment TEXT NOT NULL DEFAULT ''")
  init_scud(c)
  c.commit()
def cleanup(c):
 c.execute('UPDATE bookings SET token=NULL WHERE token IS NOT NULL AND expires<=?',(now().isoformat(),))
 t=now().date(); m=t.month-6; y=t.year
 if m<=0: m+=12; y-=1
 cutoff=date(y,m,min(t.day,calendar.monthrange(y,m)[1])).isoformat()
 c.execute('DELETE FROM bookings WHERE NOT EXISTS (SELECT 1 FROM legs WHERE legs.booking_id=bookings.id AND legs.date>=?) AND NOT EXISTS (SELECT 1 FROM cancelled_legs WHERE cancelled_legs.booking_id=bookings.id AND cancelled_legs.date>=?)',(cutoff,cutoff)); c.execute('DELETE FROM scud_events WHERE stamp<?',(int(datetime.combine(date.fromisoformat(cutoff),time.min,TZ).timestamp()*1000),)); c.execute('DELETE FROM scud_days WHERE day<?',(cutoff,)); c.execute('DELETE FROM scud_imports WHERE last_day<?',(cutoff,)); c.commit()
def admin(credentials:HTTPBasicCredentials=Depends(auth)):
 username=credentials.username.encode(); password=credentials.password.encode()
 valid=secrets.compare_digest(username,b'admin') and secrets.compare_digest(password,PASS.encode())
 extra_user=os.environ.get('BUS_EXTRA_ADMIN_USER','')
 if not valid and extra_user and secrets.compare_digest(username,extra_user.encode()):
  try:
   salt=bytes.fromhex(os.environ['BUS_EXTRA_ADMIN_SALT'])
   expected=bytes.fromhex(os.environ['BUS_EXTRA_ADMIN_HASH'])
   actual=hashlib.pbkdf2_hmac('sha256',password,salt,310000)
   valid=secrets.compare_digest(actual,expected)
  except (KeyError,ValueError): valid=False
 if not valid: raise HTTPException(status_code=401,headers={'WWW-Authenticate':'Basic realm="Bus"'})
def validdate(v):
 try: return date.fromisoformat(v)
 except: raise HTTPException(400,'Некорректная дата / Geçersiz tarih')
def dt(d,t): return datetime.combine(validdate(d),time.fromisoformat(t),TZ)
BOOKING_HORIZON_DEFAULT=48
BOOKING_HORIZON_MAX=720

def booking_horizon_hours(c):
 row=c.execute("SELECT value FROM app_settings WHERE key='booking_horizon_hours'").fetchone()
 try: value=int(row['value']) if row else BOOKING_HORIZON_DEFAULT
 except (TypeError,ValueError): return BOOKING_HORIZON_DEFAULT
 return value if 1<=value<=BOOKING_HORIZON_MAX else BOOKING_HORIZON_DEFAULT

def booking_window(c,current=None):
 current=current or now()
 hours=booking_horizon_hours(c)
 return {'hours':hours,'now':current.isoformat(),'cutoff':(current+timedelta(hours=hours)).isoformat()}

def trip(c,r,d,direction):
 tm=r['forward_time'] if direction=='forward' else r['reverse_time']; dep=dt(d,tm)
 n=c.execute('SELECT COUNT(*) n FROM legs WHERE route_id=? AND direction=? AND date=?',(r['id'],direction,d)).fetchone()['n']; return {'id':r['id'],'name':r['name'],'origin':r['destination'] if direction=='reverse' and r['direction']=='both' else r['origin'],'destination':r['origin'] if direction=='reverse' and r['direction']=='both' else r['destination'],'time':tm,'capacity':r['capacity'],'free':max(0,r['capacity']-n),'past':dep<=now()}
class Booking(BaseModel):
 card:str; date:str; forward:int; reverse:int|None=None; reverse_date:str|None=None; lang:str='ru'; direction:str='forward'
class BookingHorizonUpdate(BaseModel):
 hours:int
class FindBooking(BaseModel):
 card:str
class CancelRequest(BaseModel):
 confirm:bool
 leg_id:int|None=None
class CancelForCard(BaseModel):
 confirm:bool; card:str; code:str; leg_id:int|None=None
class Whitelist(BaseModel):
 cards:list[str]
class Stop(BaseModel):
 name_ru:str; name_tr:str; active:bool=True
class Route(BaseModel):
 name:str; origin:str; destination:str; forward_time:str; reverse_time:str; capacity:int; active:bool=True; comment:str=''; service_date:str=''; direction:str='both'; weekday_mask:int=127
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
 origin=req.headers.get('origin')
 forbidden=False
 if req.method in ('POST','PUT','PATCH','DELETE') and (origin or req.headers.get('sec-fetch-site')=='cross-site'):
  allowed={str(req.base_url).rstrip('/'),os.environ.get('BUS_PUBLIC_URL','').rstrip('/')}
  allowed.update(x.strip().rstrip('/') for x in os.environ.get('BUS_QR_ALLOWED_ORIGINS','').split(','))
  forbidden=not origin or origin.rstrip('/') not in allowed
 if forbidden: res=JSONResponse({'detail':'Запрос с другого сайта запрещён / Başka siteden isteğe izin verilmiyor'},status_code=403)
 elif path=='/bus-test' or path.startswith('/bus-test/'):
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
@app.get(BASE+'/logo.png')
def logo(): return Response((Path(__file__).resolve().parent/'static'/'tsm-logo.png').read_bytes(),media_type='image/png')
@app.get(BASE+'/passenger-i18n.js')
def passenger_i18n(): return Response((Path(__file__).resolve().parent/'static'/'passenger-i18n.js').read_bytes(),media_type='text/javascript')
@app.get(BASE+'/api/booking-window')
def public_booking_window():
 with db() as c:return booking_window(c)

@app.get(BASE+'/api/admin/settings',dependencies=[Depends(admin)])
def get_admin_settings():
 with db() as c:
  return booking_window(c)

@app.put(BASE+'/api/admin/settings',dependencies=[Depends(admin)])
def set_admin_settings(data:BookingHorizonUpdate):
 if not 1<=data.hours<=BOOKING_HORIZON_MAX:
  raise HTTPException(422,'Горизонт: от 1 до 720 часов / Rezervasyon süresi: 1–720 saat')
 with db() as c:
  c.execute("INSERT INTO app_settings(key,value) VALUES('booking_horizon_hours',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(str(data.hours),))
  c.commit()
  return booking_window(c)

@app.put(BASE+'/api/admin/scanner-code',dependencies=[Depends(admin)])
def set_admin_scanner_code():
 raise HTTPException(410,DRIVER_SESSION_ERROR)

DRIVER_LIMIT=10
DRIVER_PASSWORD_ITERATIONS=310000
DRIVER_SESSION_SECONDS=30*24*60*60
DRIVER_COOKIE='bus_driver_session'
DRIVER_COOKIE_PATH=BASE+'/api/scanner'
DRIVER_FIELDS='id,login,display_name,active,created_at,updated_at'
DRIVER_LOGIN_ERROR={'code':'INVALID_CREDENTIALS','message':'Неверный логин или пароль / Kullanıcı adı veya parola yanlış'}
DRIVER_SESSION_ERROR={'code':'DRIVER_LOGIN_REQUIRED','message':'Войдите в аккаунт водителя / Sürücü hesabına giriş yapın'}

class DriverCreate(BaseModel):
 login:StrictStr
 display_name:StrictStr
 password:StrictStr
 active:StrictBool=True
class DriverUpdate(BaseModel):
 login:StrictStr|None=None
 display_name:StrictStr|None=None
 password:StrictStr|None=None
 active:StrictBool|None=None
class DriverLogin(BaseModel):
 login:StrictStr=Field(max_length=256)
 password:StrictStr=Field(max_length=128)

@app.exception_handler(RequestValidationError)
async def validation_error(request:Request,exc:RequestValidationError):
 # Never echo a submitted password in validation errors (including Pydantic v2 input fields).
 if request.url.path.startswith(BASE+'/api/admin/drivers') or request.url.path==BASE+'/api/scanner/login':
  return JSONResponse({'detail':'Проверьте логин, пароль и имя / Kullanıcı adı, parola ve adı kontrol edin'},status_code=422)
 from fastapi.exception_handlers import request_validation_exception_handler
 return await request_validation_exception_handler(request,exc)

def driver_values(data,creating=False):
 values=data.dict(exclude_unset=True)
 if creating:values.setdefault('active',True)
 if any(value is None for value in values.values()):raise HTTPException(422,'Поля не могут быть null / Alanlar null olamaz')
 if 'login' in values:
  login=values['login'].strip()
  if not re.fullmatch(r'[a-zA-Z0-9_.-]{3,40}',login,flags=re.ASCII):
   raise HTTPException(422,'Логин: 3–40 символов A–Z, 0–9, _, . или - / Kullanıcı adı: 3–40 karakter A–Z, 0–9, _, . veya -')
  values['login']=login.lower()
 if 'display_name' in values and len(values['display_name'])>80:
  raise HTTPException(422,'Имя: не более 80 символов / Ad: en fazla 80 karakter')
 password=values.pop('password',None)
 if creating or password:
  if password is None or not 8<=len(password)<=128:
   raise HTTPException(422,'Пароль: 8–128 символов / Parola: 8–128 karakter')
  salt=secrets.token_bytes(32)
  values.update(password_salt=salt.hex(),password_hash=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,DRIVER_PASSWORD_ITERATIONS).hex(),password_iterations=DRIVER_PASSWORD_ITERATIONS)
 return values

@app.get(BASE+'/api/admin/drivers',dependencies=[Depends(admin)])
def list_drivers():
 with db() as c:return {'items':[dict(x) for x in c.execute('SELECT '+DRIVER_FIELDS+' FROM driver_accounts ORDER BY id')],'limit':DRIVER_LIMIT}

@app.post(BASE+'/api/admin/drivers',dependencies=[Depends(admin)],status_code=201)
def create_driver(data:DriverCreate):
 values=driver_values(data,True);stamp=now().isoformat()
 with db() as c:
  c.execute('BEGIN IMMEDIATE')
  if c.execute('SELECT COUNT(*) FROM driver_accounts').fetchone()[0]>=DRIVER_LIMIT:
   raise HTTPException(409,'Лимит 10 аккаунтов. Удалите ненужный / En fazla 10 hesap. Kullanılmayan hesabı silin')
  try:
   row=c.execute('INSERT INTO driver_accounts(login,display_name,password_salt,password_hash,password_iterations,active,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)',
    tuple(values[x] for x in ('login','display_name','password_salt','password_hash','password_iterations','active'))+(stamp,stamp))
  except sqlite3.IntegrityError:raise HTTPException(409,'Логин уже занят / Kullanıcı adı zaten var')
  result=dict(c.execute('SELECT '+DRIVER_FIELDS+' FROM driver_accounts WHERE id=?',(row.lastrowid,)).fetchone());c.commit();return result

@app.put(BASE+'/api/admin/drivers/{driver_id}',dependencies=[Depends(admin)])
def update_driver(driver_id:int,data:DriverUpdate):
 values=driver_values(data)
 with db() as c:
  c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT * FROM driver_accounts WHERE id=?',(driver_id,)).fetchone()
  if not old:raise HTTPException(404,'Аккаунт не найден / Hesap bulunamadı')
  revoke=('password_hash' in values or ('login' in values and values['login']!=old['login']) or values.get('active') is False)
  values['updated_at']=now().isoformat()
  try:c.execute('UPDATE driver_accounts SET '+','.join(key+'=?' for key in values)+' WHERE id=?',tuple(values.values())+(driver_id,))
  except sqlite3.IntegrityError:raise HTTPException(409,'Логин уже занят / Kullanıcı adı zaten var')
  if revoke:c.execute('DELETE FROM driver_sessions WHERE driver_id=?',(driver_id,))
  result=dict(c.execute('SELECT '+DRIVER_FIELDS+' FROM driver_accounts WHERE id=?',(driver_id,)).fetchone());c.commit();return result

@app.delete(BASE+'/api/admin/drivers/{driver_id}',dependencies=[Depends(admin)])
def delete_driver(driver_id:int):
 with db() as c:
  c.execute('BEGIN IMMEDIATE')
  if not c.execute('DELETE FROM driver_accounts WHERE id=?',(driver_id,)).rowcount:raise HTTPException(404,'Аккаунт не найден / Hesap bulunamadı')
  c.commit()
 return {'ok':True}

class DriverLoginLimiter:
 # Reserve attempts before hashing, including concurrent attempts. Failed attempts
 # expire after 60 seconds; refuse new clients at capacity instead of evicting bans.
 def __init__(self):
  self.clients=OrderedDict();self.lock=threading.Lock();self.limit=5;self.capacity=2048;self.window=60
 def reserve(self,client):
  with self.lock:
   stamp=clock.monotonic()
   while self.clients and next(iter(self.clients.values()))[0]<=stamp-self.window:self.clients.popitem(last=False)
   entry=self.clients.get(client)
   if entry is None:
    if len(self.clients)>=self.capacity:raise HTTPException(429,DRIVER_LOGIN_ERROR,headers={'Retry-After':str(self.window)})
    entry=[stamp,0];self.clients[client]=entry
   if entry[1]>=self.limit:raise HTTPException(429,DRIVER_LOGIN_ERROR,headers={'Retry-After':str(self.window)})
   entry[1]+=1
   return entry
 def success(self,client,entry):
  with self.lock:
   if self.clients.get(client) is entry:
    entry[1]-=1
    if not entry[1]:del self.clients[client]

driver_login_limiter=DriverLoginLimiter()
DRIVER_DUMMY_SALT=secrets.token_bytes(32)

def driver_token_hash(request):
 token=request.cookies.get(DRIVER_COOKIE,'')
 return hashlib.sha256(token.encode()).hexdigest() if re.fullmatch(r'[A-Za-z0-9_-]{43}',token) else ''

def current_driver(c,request):
 return c.execute('''SELECT d.id,d.login,d.display_name FROM driver_sessions s
  JOIN driver_accounts d ON d.id=s.driver_id WHERE s.token_hash=? AND s.expires_at>? AND d.active=1''',
  (driver_token_hash(request),int(now().timestamp()))).fetchone()

def require_driver(c,request):
 driver=current_driver(c,request)
 if not driver:raise HTTPException(401,DRIVER_SESSION_ERROR)
 return driver

@app.post(BASE+'/api/scanner/login')
def driver_login(data:DriverLogin,request:Request):
 client=request.client.host if request.client else 'unknown'
 attempt=driver_login_limiter.reserve(client)
 with db() as c:account=c.execute('SELECT * FROM driver_accounts WHERE login=?',(data.login.strip().lower(),)).fetchone()
 salt=bytes.fromhex(account['password_salt']) if account else DRIVER_DUMMY_SALT
 actual=hashlib.pbkdf2_hmac('sha256',data.password.encode(),salt,account['password_iterations'] if account else DRIVER_PASSWORD_ITERATIONS).hex()
 if not account or not secrets.compare_digest(actual,account['password_hash']) or not account['active']:raise HTTPException(401,DRIVER_LOGIN_ERROR)
 with db() as c:
  c.execute('BEGIN IMMEDIATE')
  # Password hashing runs outside the write lock. Revalidate against resets/deletion.
  row=c.execute('SELECT * FROM driver_accounts WHERE id=?',(account['id'],)).fetchone()
  if not row or not row['active'] or row['login']!=account['login'] or row['password_hash']!=account['password_hash']:raise HTTPException(401,DRIVER_LOGIN_ERROR)
  stamp=int(now().timestamp());token=secrets.token_urlsafe(32)
  c.execute('DELETE FROM driver_sessions WHERE expires_at<=? OR token_hash=?',(stamp,driver_token_hash(request)))
  c.execute('INSERT INTO driver_sessions(token_hash,driver_id,created_at,expires_at) VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),row['id'],stamp,stamp+DRIVER_SESSION_SECONDS))
  c.commit()
 driver_login_limiter.success(client,attempt)
 response=JSONResponse({key:row[key] for key in ('id','login','display_name')})
 response.set_cookie(DRIVER_COOKIE,token,max_age=DRIVER_SESSION_SECONDS,httponly=True,secure=request.url.scheme=='https',samesite='strict',path=DRIVER_COOKIE_PATH)
 return response

@app.get(BASE+'/api/scanner/session')
def driver_session(request:Request):
 with db() as c:return dict(require_driver(c,request))

@app.post(BASE+'/api/scanner/logout')
def driver_logout(request:Request):
 with db() as c:
  c.execute('BEGIN IMMEDIATE');c.execute('DELETE FROM driver_sessions WHERE token_hash=?',(driver_token_hash(request),));c.commit()
 response=JSONResponse({'ok':True})
 response.delete_cookie(DRIVER_COOKIE,path=DRIVER_COOKIE_PATH,httponly=True,secure=request.url.scheme=='https',samesite='strict')
 return response

@app.get(BASE+'/api/trips')
def trips(date:str,direction:str='forward'):
 day=validdate(date)
 if direction not in ['forward','reverse']: raise HTTPException(400)
 with db() as c:
  cleanup(c); current=now(); cutoff=current+timedelta(hours=booking_horizon_hours(c)); result=[]
  if day<current.date() or day>cutoff.date():return []
  for r in c.execute("SELECT * FROM routes WHERE active=1 AND deleted_at IS NULL AND (service_date='' OR service_date=?) AND (weekday_mask & ?) != 0 AND direction IN ('both',?) AND NOT EXISTS (SELECT 1 FROM route_service_periods p WHERE p.route_id=routes.id AND (p.starts_on>? OR p.ends_on<?)) ORDER BY CASE WHEN ?='forward' THEN forward_time ELSE reverse_time END,id",(date,1<<day.weekday(),direction,date,date,direction)).fetchall():
   departure=dt(date,r['forward_time'] if direction=='forward' else r['reverse_time']); hide_at=departure+timedelta(minutes=30)
   if hide_at<=current or departure>cutoff:continue
   item=trip(c,r,date,direction);item['past']=departure<=current
   next_change=departure if departure>current else hide_at
   item['refresh_in_ms']=max(1,int((next_change-current).total_seconds()*1000)+250)
   result.append(item)
  return result
def active_bookings(c,card,current):
 rows=c.execute('''SELECT b.id,b.code,l.date,l.departure,l.origin,l.destination FROM bookings b
  JOIN legs l ON l.booking_id=b.id WHERE b.card=? AND b.cancelled_at IS NULL AND b.token IS NOT NULL
  AND b.expires>? AND l.date>=? ORDER BY l.date,l.departure,b.id''',(card,current.isoformat(),current.date().isoformat())).fetchall()
 results={}
 for row in rows:
  if dt(row['date'],row['departure'])>current:
   results.setdefault(row['id'],{'code':row['code'],'legs':[]})['legs'].append(dict(row))
 return list(results.values())
def visible_bookings(c,card,current):
 rows=c.execute('''SELECT b.id,b.code,l.date,l.departure,l.origin,l.destination FROM bookings b
  JOIN legs l ON l.booking_id=b.id WHERE b.card=? AND b.cancelled_at IS NULL AND b.token IS NOT NULL
  AND b.expires>? ORDER BY l.date,l.departure,b.id''',(card,current.isoformat())).fetchall()
 results={}
 for row in rows:results.setdefault(row['id'],{'code':row['code'],'legs':[]})['legs'].append(dict(row))
 return list(results.values())
@app.post(BASE+'/api/find-booking')
def find_booking(body:FindBooking):
 card=body.card.strip()
 if not card.isascii() or not card.isdigit() or not 4<=len(card)<=12:raise HTTPException(400,'Номер пропуска: от 4 до 12 цифр / Kart numarası 4–12 rakam olmalı')
 with db() as c:
  current=now(); items=visible_bookings(c,card,current)
  return {'items':[{'code':x['code'],'url':BASE+'/check/'+x['code']+'/'+qr_signature(x['code']),
                    'legs':[{k:leg[k] for k in ('date','departure','origin','destination')} for leg in x['legs']]} for x in items]}
@app.post(BASE+'/api/book')
def book(b:Booking):
 card=b.card.strip(); day=validdate(b.date); rd=validdate(b.reverse_date or b.date)
 if b.direction not in ['forward','reverse']: raise HTTPException(400,{'code':'DIRECTION_INVALID','message':'Выберите направление / Yön seçin'})
 if not card.isascii() or not card.isdigit() or not 4<=len(card)<=12: raise HTTPException(400,{'code':'CARD_INVALID','message':'Пропуск: 4–12 цифр / Kart: 4–12 rakam'})
 if b.reverse and rd<day: raise HTTPException(400,{'code':'RETURN_BEFORE_OUTBOUND','message':'Дата обратного рейса раньше прямого / Dönüş tarihi gidişten önce'})
 with db() as c:
  try:
   c.execute('BEGIN IMMEDIATE'); selected=[]
   if not c.execute('SELECT 1 FROM allowed_cards WHERE card=?',(card,)).fetchone(): raise HTTPException(403,{'code':'CARD_NOT_ALLOWED','message':'Пропуска нет в списке. Обратитесь к администратору / Kart listede yok. Yöneticiye başvurun'})
   current=now(); horizon_hours=booking_horizon_hours(c); cutoff=current+timedelta(hours=horizon_hours)
   if active_bookings(c,card,current):raise HTTPException(409,{'code':'ACTIVE_BOOKING_EXISTS','message':'У вас уже есть предстоящий рейс. Завершите поездку или отмените оставшийся рейс / Gelecek seferiniz var. Yolculuğu tamamlayın veya kalan seferi iptal edin'})
   for rid,d,direction in [(b.forward,b.date,b.direction)]+([(b.reverse,b.reverse_date or b.date,'reverse' if b.direction=='forward' else 'forward')] if b.reverse else []):
    r=c.execute('SELECT * FROM routes WHERE id=? AND active=1 AND deleted_at IS NULL',(rid,)).fetchone()
    if not r or r['direction'] not in ('both',direction) or (r['service_date'] and r['service_date']!=d) or not (r['weekday_mask'] & (1<<validdate(d).weekday())): raise HTTPException(400,{'code':'TRIP_UNAVAILABLE','message':'Этот рейс недоступен / Bu sefer kullanılamıyor'})
    period=c.execute('SELECT starts_on,ends_on FROM route_service_periods WHERE route_id=?',(rid,)).fetchone()
    if period and not period['starts_on']<=d<=period['ends_on']: raise HTTPException(400,{'code':'TRIP_UNAVAILABLE','message':'Этот рейс недоступен / Bu sefer kullanılamıyor'})
    t=trip(c,r,d,direction)
    if dt(d,t['time'])>cutoff:
     raise HTTPException(409,{'code':'OUTSIDE_BOOKING_WINDOW','hours':horizon_hours,'message':f'Бронирование доступно только на {horizon_hours} часов вперёд / Rezervasyon sadece {horizon_hours} saat öncesinden yapılabilir'})
    if t['past']: raise HTTPException(409,{'code':'TRIP_DEPARTED','message':'Рейс уже отправился / Sefer kalktı'})
    if t['free']<=0: raise HTTPException(409,{'code':'TRIP_FULL','message':'Свободных мест нет / Boş yer kalmadı'})
    if c.execute('SELECT 1 FROM legs WHERE route_id=? AND direction=? AND date=? AND card=?',(rid,direction,d,card)).fetchone(): raise HTTPException(409,{'code':'ALREADY_ON_TRIP','message':'Вы уже записаны на этот рейс / Bu sefere kayıtlısınız'})
    selected.append((r,d,direction,t))
   if len(selected)==2 and (selected[0][3]['destination']!=selected[1][3]['origin'] or selected[0][3]['origin']!=selected[1][3]['destination']): raise HTTPException(400,{'code':'RETURN_STOPS_MISMATCH','message':'Обратный рейс должен быть между теми же остановками / Dönüş aynı duraklar arasında olmalı'})
   if len(selected)==2 and dt(selected[1][1],selected[1][3]['time'])<=dt(selected[0][1],selected[0][3]['time']): raise HTTPException(400,{'code':'RETURN_TOO_EARLY','message':'Обратный рейс должен быть позже / Dönüş daha sonra olmalı'})
   in_use={x['fortune'] for x in c.execute('SELECT fortune FROM bookings WHERE token IS NOT NULL AND cancelled_at IS NULL AND expires>?',(now().isoformat(),))}
   token=secrets.token_urlsafe(32); code='BUS-'+secrets.token_hex(5).upper(); fortune=choose_fortune(in_use); expires=max(dt(d,t['time']) for _,d,_,t in selected)+timedelta(minutes=30)
   bid=c.execute('INSERT INTO bookings(code,card,fortune,token,expires,created,lang) VALUES(?,?,?,?,?,?,?)',(code,card,fortune,token,expires.isoformat(),now().isoformat(),b.lang if b.lang in ['tr','ru','ky','uz','tg','kk','tk','en','zh'] else 'ru')).lastrowid
   for r,d,direction,t in selected:
    used={x[0] for x in c.execute('SELECT sequence FROM legs WHERE route_id=? AND direction=? AND date=?',(r['id'],direction,d))}
    seq=next((n for n in range(1,r['capacity']+1) if n not in used),None)
    if seq is None: raise HTTPException(409,{'code':'TRIP_FULL','message':'Свободных мест нет / Boş yer kalmadı'})
    c.execute('INSERT INTO legs(booking_id,route_id,direction,date,departure,origin,destination,sequence,capacity,route_name,card,comment) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(bid,r['id'],direction,d,t['time'],t['origin'],t['destination'],seq,r['capacity'],r['name'],card,r['comment']))
   c.commit(); return {'url':BASE+'/ticket/'+token}
  except: c.rollback(); raise
def booking_legs(c,b):
 return [dict(x) for x in c.execute('''SELECT l.*,0 AS cancelled,bd.boarded_at FROM legs l LEFT JOIN boardings bd ON bd.leg_id=l.id WHERE l.booking_id=?
  UNION ALL SELECT cl.original_leg_id AS id,cl.booking_id,cl.route_id,cl.direction,cl.date,cl.departure,cl.origin,cl.destination,cl.sequence,cl.capacity,cl.route_name,cl.card,cl.comment,1 AS cancelled,NULL AS boarded_at FROM cancelled_legs cl WHERE cl.booking_id=? ORDER BY date,departure''',(b['id'],b['id']))]
def is_cancellable(b,legs,current=None):
 current=current or now()
 return any(not x['cancelled'] and not x.get('boarded_at') and dt(x['date'],x['departure'])>current for x in legs)
def ticketdata(token,allow_cancelled=False):
 with db() as c:
  cleanup(c); c.execute('BEGIN'); b=c.execute('SELECT * FROM bookings WHERE token=?',(token,)).fetchone()
  if not b: raise HTTPException(404,'Ссылка истекла / Bağlantının süresi doldu')
  if b['cancelled_at'] and not allow_cancelled: raise HTTPException(410,'Бронь отменена / Rezervasyon iptal edildi')
  return dict(b),booking_legs(c,b)
def cancel_booking(*,token=None,code=None,card=None,expected_code=None,leg_id=None):
 with db() as c:
  try:
   c.execute('BEGIN IMMEDIATE')
   b=c.execute('SELECT * FROM bookings WHERE token=?',(token,)).fetchone() if token is not None else c.execute('SELECT * FROM bookings WHERE code=?',(code,)).fetchone()
   if not b: raise HTTPException(404,'Бронь не найдена / Rezervasyon bulunamadı')
   if (card is not None and b['card']!=card) or (expected_code is not None and b['code']!=expected_code):raise HTTPException(404,'Бронь не найдена / Rezervasyon bulunamadı')
   if b['cancelled_at']: c.commit();return {'ok':True,'cancelled':True}
   legs=booking_legs(c,b);current=now()
   if datetime.fromisoformat(b['expires'])<=current: raise HTTPException(404,{'code':'LINK_EXPIRED'})
   eligible=[x for x in legs if not x['cancelled'] and not x.get('boarded_at') and dt(x['date'],x['departure'])>current]
   if leg_id is not None:
    if not any(x['id']==leg_id and not x['cancelled'] for x in legs):raise HTTPException(404,{'code':'LEG_NOT_FOUND'})
    if not any(x['id']==leg_id for x in eligible):raise HTTPException(409,{'code':'LEG_NOT_CANCELLABLE'})
    future_ids=[leg_id]
   else:
    if not eligible:raise HTTPException(409,{'code':'NO_CANCELLABLE_LEGS'})
    future_ids=[x['id'] for x in eligible]
   markers=','.join('?' for _ in future_ids)
   c.execute('''INSERT INTO cancelled_legs(original_leg_id,booking_id,route_id,direction,date,departure,origin,destination,sequence,capacity,route_name,card,comment)
                SELECT id,booking_id,route_id,direction,date,departure,origin,destination,sequence,capacity,route_name,card,comment FROM legs WHERE booking_id=? AND id IN ('''+markers+')',[b['id'],*future_ids])
   c.execute('DELETE FROM legs WHERE booking_id=? AND id IN ('+markers+')',[b['id'],*future_ids])
   remaining=c.execute('SELECT COUNT(*) FROM legs WHERE booking_id=?',(b['id'],)).fetchone()[0]
   if not remaining:c.execute('UPDATE bookings SET cancelled_at=? WHERE id=?',(current.isoformat(),b['id']))
   c.commit();return {'ok':True,'cancelled':not bool(remaining),'cancelled_legs':future_ids}
  except: c.rollback();raise
@app.post(BASE+'/api/ticket/{token}/cancel')
def passenger_cancel(token:str,body:CancelRequest):
 if not body.confirm:raise HTTPException(400,'Необходимо подтверждение / Onay gerekli')
 return cancel_booking(token=token,leg_id=body.leg_id)
@app.post(BASE+'/api/ticket/{token}/cancel-for-card')
def passenger_cancel_for_card(token:str,body:CancelForCard):
 if not body.confirm:raise HTTPException(400,'Необходимо подтверждение / Onay gerekli')
 return cancel_booking(token=token,card=body.card.strip(),expected_code=body.code.strip(),leg_id=body.leg_id)
@app.post(BASE+'/api/admin/bookings/{code}/cancel',dependencies=[Depends(admin)])
def dispatcher_cancel(code:str,body:CancelRequest):
 if not body.confirm:raise HTTPException(400,'Необходимо подтверждение / Onay gerekli')
 return cancel_booking(code=code,leg_id=body.leg_id)
def qrdata(b,legs):
 return {'booking':b['code'],'card':b['card'],'fortune':b['fortune'],'legs':[{'date':x['date'],'time':x['departure'],'number':x['sequence'],'capacity':x['capacity']} for x in legs]}
def stop_translations():
 with db() as c:
  translated={x['alias']:x['name_tr'] for x in c.execute('SELECT alias,name_tr FROM stop_aliases')}
  translated.update({x['name_ru']:x['name_tr'] for x in c.execute('SELECT name_ru,name_tr FROM stops')})
  return translated
def qr_signature(code):
 digest=hmac.new(os.environ['BUS_FERNET_KEY'].encode(),b'bus-qr-v1:'+code.encode(),hashlib.sha256).digest()[:16]
 return base64.urlsafe_b64encode(digest).decode().rstrip('=')
def qr_origin(request):
 return os.environ.get('BUS_PUBLIC_URL',str(request.base_url).rstrip('/')).rstrip('/')
def qr_url(request,b):
 return qr_origin(request)+BASE+'/check/'+b['code']+'/'+qr_signature(b['code'])
def checked_booking(code,signature):
 if not re.fullmatch(r'BUS-[0-9A-F]{10}',code) or not re.fullmatch(r'[A-Za-z0-9_-]{22}',signature) or not secrets.compare_digest(signature,qr_signature(code)):
  raise HTTPException(404,'Подтверждение не найдено / Onay bulunamadı')
 with db() as c:
  cleanup(c); c.execute('BEGIN'); b=c.execute('SELECT * FROM bookings WHERE code=?',(code,)).fetchone()
  if not b or b['token'] is None: raise HTTPException(404,'Срок подтверждения истёк / Onay süresi doldu')
  if b['cancelled_at']: raise HTTPException(410,'Бронь отменена / Rezervasyon iptal edildi')
  return dict(b),booking_legs(c,b)
@app.get(BASE+'/check/{code}/{signature}',response_class=HTMLResponse)
def public_check(code:str,signature:str):
 try: b,legs=checked_booking(code,signature)
 except HTTPException as exc:
  heading='БРОНЬ ОТМЕНЕНА / REZERVASYON İPTAL EDİLDİ' if exc.status_code==410 else 'ПОДТВЕРЖДЕНИЕ НЕДОСТУПНО / ONAY KULLANILAMIYOR'
  return HTMLResponse('<!doctype html><html><head>'+STYLE+'</head><body><main><div class="alert">'+heading+'</div></main></body></html>',status_code=exc.status_code)
 translations=stop_translations()
 def point(value):
  ru=html.escape(value); tr=html.escape(translations.get(value,value))
  return ru if ru==tr else ru+' / '+tr
 route_html=''.join('<div class="card"><b>'+point(x['origin'])+' → '+point(x['destination'])+'</b><p>'+html.escape(x['date'])+' · <strong>'+html.escape(x['departure'][:5])+'</strong></p><span class="muted">'+html.escape(x['route_name'])+'<br><span class="check-seat-label">Запись / Kayıt</span>: '+str(x['sequence'])+' / '+str(x['capacity'])+'</span>'+('<p class="check-cancelled-leg">Отменён / İptal edildi</p>' if x['cancelled'] else '')+('<p class="check-boarded-leg">Посадка отмечена / Biniş kaydedildi</p>' if x.get('boarded_at') else '')+('<button class="secondary check-cancel-one" onclick="openCancel('+str(x['id'])+')">Отменить рейс</button>' if not x['cancelled'] and not x.get('boarded_at') and dt(x['date'],x['departure'])>now() else '')+('<p>'+html.escape(x['comment'])+'</p>' if x['comment'] else '')+'</div>' for x in legs)
 direction='⇄ В ОБЕ СТОРОНЫ / GİDİŞ-DÖNÜŞ' if len(legs)>1 else '→ В ОДНУ СТОРОНУ / TEK YÖN'
 path=BASE+'/check/'+code+'/'+signature
 lang=b['lang'] if b['lang'] in ('ru','tr','kk','tk','zh') else 'ru'
 page='<!doctype html><html><head>'+STYLE+'<title>Подтверждение / Onay</title></head><body><header><span class="brand"><img src="/bus/logo.png" alt="TSM ENERJİ"><span class="brand-label">🚌 BUS</span></span><select id="languageSelect" class="headbutton language-select" aria-label="Language / Язык" onchange="setCheckLang(this.value)"><option value="tr">🇹🇷 Türkçe</option><option value="ru">🇷🇺 Русский</option><option value="ky">🇰🇬 Кыргызча</option><option value="uz">🇺🇿 O‘zbekcha</option><option value="tg">🇹🇯 Тоҷикӣ</option><option value="kk">🇰🇿 Қазақша</option><option value="tk">🇹🇲 Türkmençe</option><option value="en">🇬🇧 English</option><option value="zh">🇨🇳 简体中文</option></select></header><main><div class="box"><div class="success" id="checkOk">✓ ПОСАДКА РАЗРЕШЕНА<br>BİNİŞ ONAYLANDI</div><div class="direction" id="checkDirection">'+direction+'</div>'+route_html+'<p><b id="checkCardLabel">Пропуск / Kart:</b> '+html.escape(b['card'])+'</p><p><b id="checkCodeLabel">Бронь / Rezervasyon:</b> '+html.escape(b['code'])+'</p><div class="fortune"><b id="checkFortuneLabel">Пожелание / Dilek</b><br><span id="checkFortuneText">'+html.escape(b['fortune'])+'<br>'+html.escape(fortune_tr(b['fortune']))+'</span></div><img class="qr" src="'+path+'/qr" alt="QR"><label id="fileLangLabel" for="fileLang">Язык файла / Dosya dili</label><select id="fileLang"><option value="ru"'+(' selected' if lang=='ru' else '')+'>Русский</option><option value="tr"'+(' selected' if lang=='tr' else '')+'>Türkçe</option><option value="kk"'+(' selected' if lang=='kk' else '')+'>Қазақша</option><option value="tk"'+(' selected' if lang=='tk' else '')+'>Türkmençe</option><option value="zh"'+(' selected' if lang=='zh' else '')+'>🇨🇳 简体中文</option></select><div class="row" style="margin:10px 0"><button onclick="downloadCheck(\'pdf\')">↓ PDF</button><button onclick="downloadCheck(\'jpg\')">↓ JPG</button></div><button class="secondary" id="cancelOpen" onclick="openCancel()">Отменить бронь / Rezervasyonu iptal et</button><div id="cancelForm" style="display:none"><p class="muted" id="cancelInstruction">Для отмены вставьте вашу личную ссылку на подтверждение / İptal için kişisel onay bağlantınızı girin.</p><input id="cancelLink" type="url" autocomplete="off"><button style="margin-top:10px" id="cancelSubmit" onclick="cancelExisting()">Подтвердить отмену / İptali onayla</button></div><div id="cancelMsg"></div></div></main>'
 return page+'<script src="/bus/passenger-i18n.js?v=20261117-zh"></script>'+CHECK_SCRIPT.replace('/*CARD*/',json.dumps(b['card']).replace('<','\\u003c')).replace('/*CODE*/',json.dumps(b['code'])).replace('/*SIGNATURE*/',json.dumps(signature)).replace('/*CAN_CANCEL*/',json.dumps(is_cancellable(b,legs))).replace('/*BOOKING_LANG*/',json.dumps(b['lang'])).replace('/*LEG_COUNT*/',str(len(legs))).replace('/*FORTUNES*/',json.dumps({'ru':b['fortune'],'tr':fortune_tr(b['fortune']),'kk':fortune_localized(b['fortune'],'kk'),'tk':fortune_localized(b['fortune'],'tk'),'zh':fortune_localized(b['fortune'],'zh')},ensure_ascii=False).replace('<','\\u003c'))+'</body></html>'
def qr_image(b,legs,url,max_pixels=None):
 q=qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_L,box_size=5,border=4); q.add_data(url); q.make(fit=True)
 if max_pixels: q.box_size=max(1,max_pixels//(q.modules_count+q.border*2))
 return q.make_image(fill_color='black',back_color='white').convert('RGB')
@app.get(BASE+'/ticket/{token}',response_class=HTMLResponse)
def ticket(token:str):
 b,legs=ticketdata(token,allow_cancelled=True); data={'code':b['code'],'card':b['card'],'fortune':b['fortune'],'fortune_tr':fortune_tr(b['fortune']),'fortune_kk':fortune_localized(b['fortune'],'kk'),'fortune_tk':fortune_localized(b['fortune'],'tk'),'fortune_zh':fortune_localized(b['fortune'],'zh'),'legs':legs,'lang':b['lang'],'expires':b['expires'],'token':token,'cancelled':bool(b['cancelled_at']),'can_cancel':is_cancellable(b,legs),'stop_translations':stop_translations()}; return TICKET.replace('/*DATA*/',json.dumps(data,ensure_ascii=False).replace('<','\\u003c'))
@app.get(BASE+'/api/ticket/{token}/comments')
def ticket_comments(token:str):
 b,legs=ticketdata(token,allow_cancelled=True); return {'comments':[{"id":x['id'],"comment":x['comment'],"cancelled":x['cancelled'],"boarded_at":x.get('boarded_at')} for x in legs],'cancelled':bool(b['cancelled_at']),'can_cancel':is_cancellable(b,legs)}
@app.get(BASE+'/ticket/{token}/qr')
def qr(token:str,request:Request):
 b,legs=ticketdata(token); out=io.BytesIO(); qr_image(b,legs,qr_url(request,b)).save(out,format='PNG'); return Response(out.getvalue(),media_type='image/png')
def ticket_image(b,legs,url,stop_map=None):
 # Fixed 9:16 canvas; the text area shrinks as needed, while the QR remains large.
 W,H=1080,1920
 im=Image.new('RGB',(W,H),'#f3f7fb'); draw=ImageDraw.Draw(im)
 regular='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'; bold='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'
 if b['lang']=='zh': regular=bold='/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc'
 F=lambda n:ImageFont.truetype(regular,n); B=lambda n:ImageFont.truetype(bold,n)
 # Preserve Turkish/Cyrillic stop and route glyphs; WenQuanYi lacks some Turkish letters.
 def name_font(text,size,strong=False):
  path=bold if strong else regular
  if b['lang']=='zh' and not re.search(r'[\u3400-\u9fff]',text):
   path='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if strong else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
  return ImageFont.truetype(path,size)
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
 logo=Image.open(Path(__file__).resolve().parent/'static'/'tsm-logo.png').convert('RGBA');logo.thumbnail((150,60),Image.Resampling.LANCZOS);im.paste(logo,(55,24),logo)
 draw.text((220,34),'BUS',font=B(32),fill='#07549b')
 draw.rounded_rectangle((45,90,1035,195),radius=22,fill='#e2f3e6')
 title={'kk':'ОТЫРУҒА РҰҚСАТ','tk':'MÜNMÄGE RUGSAT','zh':'可以乘车'}.get(b['lang'],'BİNİŞ ONAYLANDI' if tr else 'ПОСАДКА РАЗРЕШЕНА')
 draw.text((78,114),title,font=fit(title,B,49,915),fill='#17662f')
 direction=({'kk':'⇄  БАРУ ЖӘНЕ ҚАЙТУ','tk':'⇄  GIDIŞ WE GAÝDYŞ','zh':'往返'}.get(b['lang'],'⇄  GİDİŞ - DÖNÜŞ' if tr else '⇄  В ОБЕ СТОРОНЫ')) if len(legs)>1 else ({'kk':'→  БІР БАҒЫТҚА','tk':'→  BIR TARAPA','zh':'→  单程'}.get(b['lang'],'→  TEK YÖN' if tr else '→  В ОДНУ СТОРОНУ'))
 draw.text((55,225),direction,font=fit(direction,B,46,970),fill='#07549b')
 body=Image.new('RGBA',(970,5000),(0,0,0,0));bd=ImageDraw.Draw(body);y=0
 for leg in legs:
  title=point(leg['origin'])+' → '+point(leg['destination'])
  route_font=name_font(title,40,True);route_lines=lines(title,route_font,880,bd)
  note_lines=[]
  for note in (leg.get('route_name'),leg.get('comment')):
   if note:
    font=name_font(note,28);note_lines.extend((line,font) for line in lines(note,font,880,bd))
  h=45+len(route_lines)*52+54+47+len(note_lines)*38+28
  bd.rounded_rectangle((0,y,969,y+h),radius=20,fill='white',outline='#dce6ef',width=2)
  cy=y+20
  for line in route_lines:bd.text((30,cy),line,font=route_font,fill='#143350');cy+=52
  bd.text((30,cy),leg['date']+'  •  '+leg['departure'][:5],font=B(36),fill='#07549b');cy+=54
  seat=({'kk':'Орын: ','tk':'Ýer: ','zh':'预订序号：'}.get(b['lang'],'Kayıt: ' if tr else 'Запись: '))+str(leg['sequence'])+' / '+str(leg['capacity'])
  bd.text((30,cy),seat,font=F(30),fill='#556e81');cy+=47
  for line,font in note_lines:bd.text((30,cy),line,font=font,fill='#354f62');cy+=38
  y+=h+16
 for label,value in [(({'kk':'Рұқсатнама','tk':'Rugsatnama','zh':'通行证'}.get(b['lang'],'Kart' if tr else 'Пропуск')),b['card']),(({'kk':'Бронь','tk':'Bron','zh':'预订编号'}.get(b['lang'],'Rezervasyon' if tr else 'Бронь')),b['code'])]:
  bd.text((12,y+5),label+':',font=F(30),fill='#556e81'); bd.text((270,y+2),value,font=B(31),fill='#143350'); y+=56
 wish=fortune_tr(b['fortune']) if tr else fortune_localized(b['fortune'],b['lang'])
 wish_lines=lines(wish,F(30),900,bd)
 h=70+len(wish_lines)*42
 bd.rounded_rectangle((0,y+8,969,y+h),radius=18,fill='#e8f6f4')
 bd.text((24,y+20),{'kk':'Сізге тілек','tk':'Size arzuw','zh':'送给您的祝福'}.get(b['lang'],'Dileğiniz' if tr else 'Ваше пожелание'),font=B(27),fill='#087d78')
 for i,line in enumerate(wish_lines):bd.text((24,y+58+i*42),line,font=F(30),fill='#143350')
 y+=h+18
 body=body.crop((0,0,970,max(1,y)))
 if body.height>830:
  body=body.resize((int(body.width*830/body.height),830),Image.Resampling.LANCZOS)
 im.paste(body,((W-body.width)//2,295),body)
 qr_label={'kk':'QR-КОДТЫ КӨРСЕТІҢІЗ','tk':'QR KODY GÖRKEZIŇ','zh':'请出示二维码'}.get(b['lang'],'QR KODU GÖSTERİN' if tr else 'ПОКАЖИТЕ QR-КОД')
 draw.text((W/2,1130),qr_label,font=B(30),fill='#456478',anchor='ma')
 q=qr_image(b,legs,url,max_pixels=615)
 im.paste(q,((W-q.width)//2,1185))
 return im
@app.get(BASE+'/ticket/{token}/download/{fmt}')
def download(token:str,fmt:str,request:Request,lang:str=''):
 if fmt not in ['jpg','pdf']: raise HTTPException(404)
 b,legs=ticketdata(token);return render_download(b,[x for x in legs if not x.get('cancelled')],fmt,request,lang)
def render_download(b,legs,fmt,request,lang=''):
 b['lang']=lang if lang in ['ru','tr','kk','tk','zh'] else b['lang']; im=ticket_image(b,legs,qr_url(request,b),stop_translations()); out=io.BytesIO()
 if fmt=='jpg': im.save(out,'JPEG',quality=92); typ='image/jpeg'
 else:
  cv=canvas.Canvas(out,pagesize=(324,576)); cv.drawImage(ImageReader(im),0,0,width=324,height=576); cv.showPage(); cv.save(); typ='application/pdf'
 return Response(out.getvalue(),media_type=typ,headers={'Content-Disposition':f'attachment; filename="{b["code"]}.{fmt}"'})
@app.get(BASE+'/check/{code}/{signature}/qr')
def check_qr(code:str,signature:str,request:Request):
 b,legs=checked_booking(code,signature); out=io.BytesIO();qr_image(b,legs,qr_url(request,b)).save(out,format='PNG');return Response(out.getvalue(),media_type='image/png')
@app.get(BASE+'/check/{code}/{signature}/download/{fmt}')
def check_download(code:str,signature:str,fmt:str,request:Request,lang:str=''):
 if fmt not in ('jpg','pdf'):raise HTTPException(404)
 b,legs=checked_booking(code,signature);return render_download(b,[x for x in legs if not x.get('cancelled')],fmt,request,lang)
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
   items.append({'decoded':qrdata(b,legs),'fortune_tr':fortune_tr(b['fortune']),'active':b['token'] is not None and not b['cancelled_at'],'cancelled':bool(b['cancelled_at']),'cancelled_at':b['cancelled_at'],'can_cancel':is_cancellable(b,legs),'expires':b['expires'],'routes':[{'id':x['id'],'origin':x['origin'],'destination':x['destination'],'cancelled':bool(x['cancelled']),'can_cancel':not x['cancelled'] and not x.get('boarded_at') and dt(x['date'],x['departure'])>now()} for x in legs]})
  return {'items':items,'total':total,'offset':offset,'limit':30}

@app.get(BASE+'/api/admin/qrcodes/{code}/image',dependencies=[Depends(admin)])
def admin_qr_image(code:str,request:Request):
 with db() as c:
  cleanup(c);b=c.execute('SELECT * FROM bookings WHERE code=? AND token IS NOT NULL AND cancelled_at IS NULL',(code,)).fetchone()
  if not b:raise HTTPException(404,'Срок действия истёк / Süresi doldu')
  legs=[dict(x) for x in c.execute('SELECT * FROM legs WHERE booking_id=? ORDER BY date,departure',(b['id'],))]
  out=io.BytesIO();qr_image(b,legs,qr_url(request,b)).save(out,format='PNG');return Response(out.getvalue(),media_type='image/png')

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
 with db() as c: return [dict(x) for x in c.execute("SELECT r.*,COALESCE(p.starts_on,'') AS period_start,COALESCE(p.ends_on,'') AS period_end FROM routes r LEFT JOIN route_service_periods p ON p.route_id=r.id WHERE r.deleted_at IS NULL ORDER BY r.id")]
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
 if any(not x or len(x)>90 for x in clean) or len(r.comment)>160 or not 1<=r.capacity<=500 or not 1<=r.weekday_mask<=127: raise HTTPException(400,'Некорректные данные / Geçersiz bilgiler')
 if r.service_date and not (r.weekday_mask & (1<<validdate(r.service_date).weekday())): raise HTTPException(400,'Дата не совпадает с выбранными днями недели / Tarih seçilen haftanın günleriyle eşleşmiyor')
 try: time.fromisoformat(r.forward_time); time.fromisoformat(r.reverse_time)
 except: raise HTTPException(400,'Некорректное время / Geçersiz saat')
 with db() as c:
  c.execute('BEGIN IMMEDIATE');ensure_route_stops(c,clean[1],clean[2])
  if c.execute('SELECT COUNT(*) FROM routes WHERE deleted_at IS NULL').fetchone()[0]>=200: raise HTTPException(400,'Максимум 200 рейсов / En fazla 200 sefer')
  c.execute('INSERT INTO routes(name,origin,destination,forward_time,reverse_time,capacity,active,comment,service_date,direction,weekday_mask) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(*clean,r.forward_time,r.reverse_time,r.capacity,int(r.active),r.comment.strip(),r.service_date,r.direction,r.weekday_mask)); c.commit(); return {'ok':True}
@app.put(BASE+'/api/admin/routes/{rid}',dependencies=[Depends(admin)])
def route_edit(rid:int,r:Route):
 clean=[r.name.strip(),r.origin.strip(),r.destination.strip()]
 if r.service_date: validdate(r.service_date)
 if r.direction not in ('forward','reverse','both'): raise HTTPException(400,'Некорректное направление / Geçersiz yön')
 if any(not x or len(x)>90 for x in clean) or len(r.comment)>160 or not 1<=r.capacity<=500 or not 1<=r.weekday_mask<=127: raise HTTPException(400)
 if r.service_date and not (r.weekday_mask & (1<<validdate(r.service_date).weekday())): raise HTTPException(400,'Дата не совпадает с выбранными днями недели / Tarih seçilen haftanın günleriyle eşleşmiyor')
 try: time.fromisoformat(r.forward_time);time.fromisoformat(r.reverse_time)
 except: raise HTTPException(400)
 with db() as c:
  c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT * FROM routes WHERE id=? AND deleted_at IS NULL',(rid,)).fetchone()
  if not old: raise HTTPException(404)
  ensure_route_stops(c,clean[1],clean[2],old)
  future=c.execute('SELECT 1 FROM legs WHERE route_id=? AND date>=? LIMIT 1',(rid,now().date().isoformat())).fetchone()
  if future and (clean!=[old['name'],old['origin'],old['destination']] or r.forward_time!=old['forward_time'] or r.reverse_time!=old['reverse_time'] or r.capacity!=old['capacity'] or r.service_date!=old['service_date'] or r.direction!=old['direction'] or r.weekday_mask!=old['weekday_mask']): raise HTTPException(409,'На рейс уже есть записи. Можно изменить только статус. / Kayıt olan seferde sadece durum değiştirilebilir.')
  c.execute('UPDATE routes SET name=?,origin=?,destination=?,forward_time=?,reverse_time=?,capacity=?,active=?,comment=?,service_date=?,direction=?,weekday_mask=? WHERE id=?',(*clean,r.forward_time,r.reverse_time,r.capacity,int(r.active),r.comment.strip(),r.service_date,r.direction,r.weekday_mask,rid)); c.execute('UPDATE legs SET comment=? WHERE route_id=? AND date>=?',(r.comment.strip(),rid,now().date().isoformat())); c.commit(); return {'ok':True}
@app.get(BASE+'/api/admin/history',dependencies=[Depends(admin)])
def history(date_from:str='',date_to:str='',route_id:str='',card:str='',time_from:str='',time_to:str='',origin:str='',status:str='all'):
 if status not in ('all','booked','cancelled'):raise HTTPException(400)
 with db() as c:
  cleanup(c); leg_view='(SELECT id AS leg_id,booking_id,route_id,date,departure,origin,destination,route_name,direction,sequence,capacity,card,0 AS cancelled FROM legs UNION ALL SELECT original_leg_id AS leg_id,booking_id,route_id,date,departure,origin,destination,route_name,direction,sequence,capacity,card,1 AS cancelled FROM cancelled_legs)'; sql='SELECT b.code,b.created,l.cancelled,b.cancelled_at,l.date,l.departure,l.origin,l.destination,l.route_name,l.direction,l.sequence,l.capacity,l.card,bd.boarded_at,bd.driver_id,bd.driver_login FROM '+leg_view+' l JOIN bookings b ON b.id=l.booking_id LEFT JOIN boardings bd ON bd.leg_id=l.leg_id AND l.cancelled=0 WHERE 1=1'; params=[]
  if status=='booked':sql+=' AND l.cancelled=0'
  if status=='cancelled':sql+=' AND l.cancelled=1'
  for val,fragment in [(date_from,' AND l.date>=?'),(date_to,' AND l.date<=?'),(route_id,' AND l.route_id=?'),(card,' AND l.card LIKE ?'),(time_from,' AND l.departure>=?'),(time_to,' AND l.departure<=?'),(origin,' AND l.origin LIKE ?')]:
   if val: sql+=fragment; params.append('%'+val+'%' if fragment.endswith('LIKE ?') else val)
  return [dict(x) for x in c.execute(sql+' ORDER BY l.date DESC,l.departure DESC LIMIT 1000',params)]
class Verify(BaseModel):
 token:str
 record:bool=True
 leg_id:int|None=None
 source:str='qr'
@app.post(BASE+'/api/scanner/unlock')
def scanner_unlock():
 raise HTTPException(410,DRIVER_SESSION_ERROR)
@app.post(BASE+'/api/admin/verify',dependencies=[Depends(admin)])
def verify(v:Verify,request:Request):
 value=v.token.strip()
 try:
  parsed=urlsplit(value)
  origins=[urlsplit(x.strip().rstrip('/')) for x in [qr_origin(request)]+os.environ.get('BUS_QR_ALLOWED_ORIGINS','').split(',') if x.strip()]
 except ValueError: raise HTTPException(400,'Недействительный QR / Geçersiz QR')
 match=re.fullmatch(re.escape(BASE)+r'/check/(BUS-[0-9A-F]{10})/([A-Za-z0-9_-]{22})',parsed.path)
 if parsed.scheme in ('https','http') and any(parsed.scheme==o.scheme and parsed.netloc.lower()==o.netloc.lower() for o in origins) and not parsed.query and not parsed.fragment and match:
  b,legs=checked_booking(*match.groups())
  return {'code':b['code'],'card':b['card'],'fortune':b['fortune'],'fortune_tr':fortune_tr(b['fortune']),'legs':legs}
 try: data=json.loads(cipher.decrypt(value.encode()).decode())
 except (InvalidToken,ValueError): raise HTTPException(400,'Недействительный QR / Geçersiz QR')
 if not isinstance(data,dict):raise HTTPException(400,'Недействительный QR / Geçersiz QR')
 with db() as c:
  cleanup(c); b=c.execute('SELECT * FROM bookings WHERE code=?',(data.get('booking'),)).fetchone()
  if not b or b['token'] is None: raise HTTPException(404,'Бронь не найдена или срок истёк / Rezervasyon bulunamadı veya süresi doldu')
  if b['cancelled_at']: raise HTTPException(410,'Бронь отменена / Rezervasyon iptal edildi')
  legs=[dict(x) for x in c.execute('SELECT * FROM legs WHERE booking_id=? ORDER BY date,departure',(b['id'],))]; expected=qrdata(b,legs)
  if data!=expected: raise HTTPException(400,'Данные не совпадают / Bilgiler uyuşmuyor')
  return {'code':b['code'],'card':b['card'],'fortune':b['fortune'],'fortune_tr':fortune_tr(b['fortune']),'legs':legs}
@app.post(BASE+'/api/scanner/verify')
def scanner_verify(v:Verify,request:Request):
 if v.record:
  with db() as c:require_driver(c,request)
 if not v.token or len(v.token)>8192:return {'allowed':False,'reason':'invalid'}
 try:
  parsed=urlsplit(v.token.strip());signed=re.fullmatch(re.escape(BASE)+r'/check/(BUS-[0-9A-F]{10})/([A-Za-z0-9_-]{22})',parsed.path)
  if signed and not secrets.compare_digest(signed.group(2),qr_signature(signed.group(1))):return {'allowed':False,'reason':'invalid'}
 except ValueError:return {'allowed':False,'reason':'invalid'}
 try: booking=verify(v,request)
 except HTTPException as exc:
  return {'allowed':False,'reason':'cancelled' if exc.status_code==410 else 'expired' if exc.status_code==404 else 'invalid'}
 current=now(); trips=[]; permitted=[];stop_map=stop_translations()
 for leg in booking['legs']:
  if leg.get('cancelled'):continue
  departure=dt(leg['date'],leg['departure']);start=departure-timedelta(minutes=20);end=departure+timedelta(minutes=20)
  item={'id':leg['id'],'date':leg['date'],'departure':leg['departure'],'origin':leg['origin'],'destination':leg['destination'],
        'origin_tr':stop_map.get(leg['origin'],leg['origin']),'destination_tr':stop_map.get(leg['destination'],leg['destination']),
        'window_start':start.isoformat(),'window_end':end.isoformat()}
  trips.append(item)
  if start<=current<=end:permitted.append((item,end))
 if not permitted:return {'allowed':False,'reason':'outside_window','card':booking['card'],'legs':trips,'checked_at':current.isoformat()}
 if v.leg_id is not None:
  permitted=[item for item in permitted if item[0]['id']==v.leg_id]
  if not permitted:return {'allowed':False,'reason':'outside_window','card':booking['card'],'legs':trips}
 if len(permitted)>1:return {'allowed':False,'reason':'choose_trip','card':booking['card'],'choices':[x for x,_ in permitted]}
 selected,ends_at=permitted[0];remaining=max(0,int((ends_at-current).total_seconds()*1000))
 result={'allowed':True,'card':booking['card'],'legs':[selected],'checked_at':current.isoformat(),'remaining_ms':remaining}
 if not v.record:return result
 with db() as c:
  c.execute('BEGIN IMMEDIATE')
  driver=require_driver(c,request)
  existing=c.execute('SELECT boarded_at FROM boardings WHERE leg_id=?',(selected['id'],)).fetchone()
  if existing:
   c.commit();return {**result,'allowed':False,'reason':'already_boarded','boarded_at':existing['boarded_at']}
  if not c.execute('SELECT 1 FROM legs WHERE id=? AND booking_id=(SELECT id FROM bookings WHERE code=?)',(selected['id'],booking['code'])).fetchone():
   c.rollback();return {'allowed':False,'reason':'cancelled','card':booking['card']}
  c.execute('INSERT INTO boardings(leg_id,booking_id,card,boarded_at,source,driver_id,driver_login) SELECT id,booking_id,card,?,?,?,? FROM legs WHERE id=?',(current.isoformat(),'manual' if v.source=='manual' else 'qr',driver['id'],driver['login'],selected['id']))
  c.commit()
 return result
@app.get(BASE+'/scanner',include_in_schema=False)
def scanner_slash():return RedirectResponse(BASE+'/scanner/')
@app.get(BASE+'/scanner/',response_class=HTMLResponse)
def scanner_page():return HTMLResponse((Path(__file__).resolve().parent/'static'/'scanner.html').read_text(encoding='utf-8'))
@app.get(BASE+'/scanner/manifest.webmanifest')
def scanner_manifest():
 return JSONResponse({'id':BASE+'/scanner/','name':'TSM BUS — Контроль посадки','short_name':'TSM Посадка','description':'Приложение водителя для проверки QR-кодов и отметки посадки / Sürücü QR biniş kontrolü','start_url':BASE+'/scanner/',
  'scope':BASE+'/scanner/','display':'standalone','background_color':'#f2f6fa','theme_color':'#07549b',
  'icons':[{'src':BASE+'/scanner/icon-'+str(n)+'.png','sizes':str(n)+'x'+str(n),'type':'image/png'} for n in (192,512)]},media_type='application/manifest+json')
@app.get(BASE+'/scanner/icon-{size}.png')
def scanner_icon(size:int):
 if size not in (192,512):raise HTTPException(404)
 return Response((Path(__file__).resolve().parent/'static'/('scanner-icon-'+str(size)+'.png')).read_bytes(),media_type='image/png')
@app.get(BASE+'/scanner/jsQR.js')
def scanner_library():
 return Response((Path(__file__).resolve().parent/'static'/'vendor'/'jsQR.js').read_bytes(),media_type='text/javascript')
@app.get(BASE+'/scanner/sw.js')
def scanner_worker():
 return Response("self.addEventListener('install',e=>self.skipWaiting());self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));self.addEventListener('fetch',e=>e.respondWith(fetch(e.request)));",media_type='text/javascript')
CHECK_SCRIPT='''<script>
const checkCard=/*CARD*/,checkCode=/*CODE*/,checkSignature=/*SIGNATURE*/,checkCanCancel=/*CAN_CANCEL*/,checkBookingLang=/*BOOKING_LANG*/,checkLegCount=/*LEG_COUNT*/,checkFortunes=/*FORTUNES*/;
let checkLang;try{checkLang=localStorage.getItem('bus-lang')||checkBookingLang}catch(e){checkLang=checkBookingLang}
if(!window.BusPassengerL10n?.check?.[checkLang])checkLang='ru';function checkText(){let m=window.BusPassengerL10n?.check||{};return m[checkLang]||m.ru}
function translateCheck(){let x=checkText();if(!x)return;document.documentElement.lang=checkLang;document.getElementById('languageSelect').value=checkLang;
 let set=(id,value)=>{let e=document.getElementById(id);if(e)e.textContent=value};
 set('checkOk',x.ok);set('checkDirection',checkLegCount>1?x.both:x.one);set('checkCardLabel',x.card);set('checkCodeLabel',x.code);set('checkFortuneLabel',x.fortune);set('checkFortuneText',checkFortunes[checkLang]||checkFortunes.ru);set('fileLangLabel',x.fileLang);set('cancelOpen',x.cancel);set('cancelInstruction',x.cancelHelp);set('cancelSubmit',x.cancelSubmit);document.querySelectorAll('.check-cancel-one').forEach(e=>e.textContent=x.cancelOne||'Отменить рейс');
 document.querySelectorAll('.check-seat-label').forEach(e=>e.textContent=x.seat);let status=window.BusPassengerL10n?.ticket?.[checkLang];if(status){document.querySelectorAll('.check-cancelled-leg').forEach(e=>e.textContent=status.cancelledLeg);document.querySelectorAll('.check-boarded-leg').forEach(e=>e.textContent=status.boardedLeg)}}
function setCheckLang(next){if(!window.BusPassengerL10n?.check?.[next])return;checkLang=next;try{localStorage.setItem('bus-lang',checkLang)}catch(e){}translateCheck()}
function downloadCheck(fmt){location.href='/bus/check/'+encodeURIComponent(checkCode)+'/'+encodeURIComponent(checkSignature)+'/download/'+fmt+'?lang='+encodeURIComponent(document.getElementById('fileLang').value)}
function storedToken(){try{return localStorage.getItem('bus-ticket:'+checkCard)||''}catch(e){return ''}}
let desiredLegId=null;async function openCancel(legId=null){desiredLegId=legId;let t=storedToken();if(t){await cancelExisting(t)}else{cancelForm.style.display='block';cancelLink.focus()}}
async function cancelExisting(raw){let token=typeof raw==='string'?raw:'';if(!token){try{let u=new URL(cancelLink.value.trim(),location.href);token=(u.pathname.match(/^\\/bus\\/ticket\\/([A-Za-z0-9_-]{20,100})\\/?$/)||[])[1]||''}catch(e){}}
 if(!token){cancelMsg.textContent=checkText()?.tokenHint||'Укажите личную ссылку на подтверждение / Kişisel onay bağlantısını girin.';return}
 if(!confirm(desiredLegId!==null?(checkText()?.cancelOneConfirm||'Отменить выбранный рейс? / Seçilen sefer iptal edilsin mi?'):(checkText()?.cancelConfirmPrompt||'Отменить все рейсы, которые ещё не отправились? / Henüz kalkmamış seferler iptal edilsin mi?')))return;
 cancelOpen.disabled=true;cancelMsg.textContent='';try{let r=await fetch('/bus/api/ticket/'+encodeURIComponent(token)+'/cancel-for-card',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:true,card:checkCard,code:checkCode,leg_id:desiredLegId})});let v=await r.json();if(!r.ok)throw Error((window.BusPassengerL10n?.errors?.[checkLang]||{})[v.detail?.code]||v.detail?.message||(typeof v.detail==='string'?v.detail:'Ошибка / Hata'));try{localStorage.removeItem('bus-ticket:'+checkCard)}catch(e){}location.reload()}catch(e){cancelMsg.textContent=e.message;cancelForm.style.display='block'}finally{cancelOpen.disabled=false}}
if(!checkCanCancel){cancelOpen.style.display='none';document.querySelectorAll('.check-cancel-one').forEach(e=>e.style.display='none')}setInterval(()=>{if(!document.hidden&&cancelForm.style.display!=='block')location.reload()},30000);translateCheck();
</script>'''
STYLE='''<meta name="viewport" content="width=device-width, initial-scale=1"><meta charset="utf-8"><style>*{box-sizing:border-box}body{margin:0;background:#f2f6fa;color:#142d48;font:16px system-ui,-apple-system,sans-serif}.brand{display:flex;align-items:center;gap:12px}.brand img{display:block;width:auto;height:42px}.brand-label{font-size:15px;white-space:nowrap;color:#075099}header{background:#fff;border-bottom:3px solid #13a6a0;padding:16px 20px;display:flex;align-items:center;justify-content:space-between;font-weight:800;color:#075099}main{max-width:740px;margin:auto;padding:20px 16px 50px}h1{font-size:25px;margin:6px 0 20px}h2{font-size:20px}.card,.box{background:white;border:1px solid #dae5ef;border-radius:15px;padding:17px;margin:12px 0;box-shadow:0 2px 9px #183b5810}.route{display:flex;align-items:center;justify-content:space-between;gap:8px}.muted{color:#668095;font-size:14px}.big{font-size:24px;font-weight:800;color:#064e96}.btn,button{border:0;border-radius:9px;padding:12px 18px;background:#07549b;color:white;font:inherit;font-weight:700;cursor:pointer}button:disabled{background:#bfcbd5;cursor:not-allowed}.secondary{background:#e8f1f9;color:#07549b}input,select,textarea{font:inherit;padding:11px;border:1px solid #b7c8d6;border-radius:8px;width:100%;background:#fff}label{display:block;font-weight:600;margin:13px 0 6px}input[type=checkbox]{width:auto;transform:scale(1.2);margin-right:9px}.row{display:grid;grid-template-columns:1fr 1fr;gap:10px}.small{font-size:13px}.alert{padding:12px;color:#a72828;background:#fff0ee;border-radius:8px;margin:10px 0}.fortune{margin:16px 0;padding:14px 16px;border-radius:12px;background:#e8f6f4;color:#145a58;line-height:1.5}.cancelled-banner{background:#ffe5e2!important;color:#a72828!important}.success{background:#e4f5e7;color:#126728;padding:15px;border-radius:10px;font-weight:800;text-align:center;font-size:23px}.direction{font-size:25px;font-weight:900;color:#07549b;text-align:center;margin:16px 0}.qr{display:block;max-width:320px;width:85%;margin:15px auto}table{border-collapse:collapse;width:100%;font-size:14px}td,th{text-align:left;border-bottom:1px solid #e4ebf0;padding:8px}.scroll{overflow:auto}.headbutton{background:#eef5fc;color:#07549b;padding:7px 12px}.language-select{width:auto;min-width:100px;max-width:160px;font-size:14px;font-weight:700;border:0;padding:7px 8px;cursor:pointer}.status{border-radius:6px;background:#e3f5eb;color:#147047;padding:4px 7px;font-size:12px}a{color:#07549b}video{max-width:100%;border-radius:10px}</style>'''
HTML='''<!doctype html><html><head>'''+STYLE+'''<title>Автобус / Otobüs</title><style>
.passenger header{padding:8px 12px}.passenger .brand img{height:34px}.passenger .headbutton{min-height:40px}
.passenger main{padding:10px 12px 28px}.passenger h1{font-size:20px;line-height:1.2;margin:4px 0 8px}
.passenger h2{font-size:16px;line-height:1.2;margin:0 0 7px}.passenger .box{padding:10px 12px;margin:8px 0;border-radius:12px}
.passenger label{font-size:13px;line-height:1.2;margin:0 0 4px}.passenger input,.passenger select{min-height:44px;min-width:0;max-width:100%;padding:8px;font-size:16px}
.passenger .lookup-row{display:grid;grid-template-columns:minmax(90px,1fr) auto;gap:8px;align-items:end}.passenger .lookup-row>div{min-width:0}
.passenger .lookup-row button,.passenger .route button{min-height:44px;padding:8px 12px;font-size:14px;white-space:nowrap}
.passenger .filters{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.15fr);gap:8px}.passenger .filters>div{min-width:0}
.passenger .route{padding:10px 12px;margin:6px 0;border-radius:12px}.passenger .route>div{min-width:0;overflow-wrap:anywhere}.passenger .route .big{font-size:20px;line-height:1.2}.passenger .route .muted{font-size:13px;line-height:1.25}
.passenger .alert{padding:8px 10px;margin:8px 0 0;font-size:14px}.passenger #booking label{margin-top:10px}
@media(max-width:340px){.passenger .lookup-row button{font-size:13px;padding:8px}.passenger .filters{grid-template-columns:1fr;gap:7px}.passenger .route button{padding:8px}}
</style></head><body class="passenger"><header><span class="brand"><img src="/bus/logo.png" alt="TSM ENERJİ"><span class="brand-label">🚌 BUS</span></span><select id="languageSelect" class="headbutton language-select" aria-label="Language / Язык" onchange="setLang(this.value)"><option value="tr">🇹🇷 Türkçe</option><option value="ru">🇷🇺 Русский</option><option value="ky">🇰🇬 Кыргызча</option><option value="uz">🇺🇿 O‘zbekcha</option><option value="tg">🇹🇯 Тоҷикӣ</option><option value="kk">🇰🇿 Қазақша</option><option value="tk">🇹🇲 Türkmençe</option><option value="en">🇬🇧 English</option><option value="zh">🇨🇳 简体中文</option></select></header><main><h1 data-i="title"></h1><div class="box"><h2 data-i="findTitle"></h2><div class="lookup-row"><div><label for="lookupCard" data-i="card"></label><input id="lookupCard" inputmode="numeric" maxlength="12" placeholder="123456"></div><button class="secondary" onclick="findBooking()" data-i="findButton"></button></div><div id="lookupResult"></div></div><div class="box filters"><div><label for="day" data-i="date"></label><input type="date" id="day"><p id="horizonHint" class="muted small" style="margin:4px 0 0"></p></div><div><label for="dir" data-i="dir"></label><select id="dir" onchange="directionChanged()"><option value="forward" data-i="direct"></option><option value="reverse" data-i="backward"></option><option value="both" data-i="round"></option></select></div></div><div id="trips"></div><section id="booking" class="box" style="display:none"><h2 data-i="book"></h2><div id="selected" class="big"></div><input type="checkbox" id="round" hidden><div id="returnWrap" style="display:none"><label data-i="returnDate"></label><input type="date" id="returnDay"><label data-i="returnTrip"></label><select id="reverse"></select></div><label data-i="card"></label><input id="card" inputmode="numeric" maxlength="12" placeholder="123456"><div id="err"></div><button id="submit" style="width:100%;margin-top:16px" onclick="book()" data-i="submit"></button></section></main><script src="/bus/passenger-i18n.js?v=20261117-zh"></script><script>
const B='/bus'; let lang=localStorage.getItem('bus-lang')||'ru', chosen=null, trips=[],tripTimer=null,tripsRequest=0,stopMap={'Камп':'Kamp','Магазин':'Mağaza'};let bookingHours=48;const t={ru:{title:'Бронирование автобуса',date:'Дата поездки',dir:'Вариант поездки',direct:'Только туда',backward:'Только обратно',choose:'Выберите удобное время отправления',select:'Выбрать',free:'свободно из',none:'Мест нет',past:'Рейс отправился',book:'Запись на рейс',round:'В обе стороны',returnDate:'Дата обратного рейса',returnTrip:'Обратный рейс',card:'Номер пропуска',submit:'ЗАПИСАТЬСЯ',empty:'На эту дату активных рейсов нет',chooseReturn:'Выберите обратный рейс',loading:'Подождите…',findTitle:'Моя бронь',findButton:'Посмотреть бронь',findNone:'Действующая бронь не найдена.',findMultiple:'Найдено несколько броней — выберите нужную:',duplicate:'У вас уже есть предстоящий рейс. После поездки или отмены оставшегося направления можно записаться снова.',findExisting:'Посмотреть бронь и отменить',horizon:'Бронирование на ближайшие {hours} ч',horizonError:'Записаться можно только на ближайшие {hours} ч.'},tr:{title:'Otobüs rezervasyonu',date:'Yolculuk tarihi',dir:'Yolculuk seçeneği',direct:'Sadece gidiş',backward:'Sadece dönüş',choose:'Uygun kalkış saatini seçin',select:'Seç',free:'boş / toplam',none:'Yer yok',past:'Sefer kalktı',book:'Sefer kaydı',round:'Gidiş - dönüş',returnDate:'Dönüş tarihi',returnTrip:'Dönüş seferi',card:'Kart numarası',submit:'KAYIT OL',empty:'Bu tarihte aktif sefer yok',chooseReturn:'Dönüş seferini seçin',loading:'Lütfen bekleyin…',findTitle:'Rezervasyonum',findButton:'Rezervasyonu görüntüle',findNone:'Aktif rezervasyon bulunamadı.',findMultiple:'Birden fazla rezervasyon bulundu; lütfen seçin:',duplicate:'Yaklaşan seferiniz var. Yolculuktan veya kalan seferi iptal ettikten sonra tekrar rezervasyon yapabilirsiniz.',findExisting:'Rezervasyonu görüntüle ve iptal et',horizon:'Önümüzdeki {hours} saat için rezervasyon',horizonError:'Yalnızca önümüzdeki {hours} saat için rezervasyon yapılabilir.'}};Object.assign(t,window.BusPassengerL10n?.home||{});if(!t[lang])lang='ru';function esc(x){return String(x).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function renderHorizonHint(){horizonHint.textContent=t[lang].horizon.replace('{hours}',bookingHours)}function translate(){document.documentElement.lang=lang;document.querySelectorAll('[data-i]').forEach(e=>e.textContent=t[lang][e.dataset.i]);languageSelect.value=lang;renderHorizonHint()}function setLang(next){if(!t[next])return;lang=next;localStorage.setItem('bus-lang',lang);translate();updateDirectionLabels(trips);render();if(document.getElementById('round').checked)loadReverse()};function localdate(){let d=new Date(Date.now()+3*3600000);return d.toISOString().slice(0,10)};day.value=localdate();returnDay.value=day.value;day.onchange=()=>{chosen=null;booking.style.display='none';returnDay.value=day.value;load()};returnDay.onchange=loadReverse;function directionChanged(){chosen=null;round.checked=dir.value==='both';booking.style.display='none';returnWrap.style.display=round.checked?'block':'none';trips=[];render();load()}
function point(x){return lang==='tr'?(stopMap[x]||x):x}async function loadStopNames(){try{let r=await fetch(B+'/api/stops');let data=await r.json();for(let item of data)stopMap[item.name_ru]=item.name_tr;updateDirectionLabels(trips);render()}catch(e){}}function updateDirectionLabels(items){if(!Array.isArray(items)||!items.length)return;let first=items[0],origin=dir.value==='reverse'?first.destination:first.origin,destination=dir.value==='reverse'?first.origin:first.destination;if(!items.every(x=>(dir.value==='reverse'?x.destination:x.origin)===origin&&(dir.value==='reverse'?x.origin:x.destination)===destination))return;dir.options[0].textContent=point(origin)+' → '+point(destination);dir.options[1].textContent=point(destination)+' → '+point(origin)}function scheduleTripRefresh(items){if(tripTimer!==null)clearTimeout(tripTimer);let delays=items.map(x=>x.refresh_in_ms).filter(x=>Number.isFinite(x)&&x>0);tripTimer=setTimeout(()=>{tripTimer=null;if(!document.hidden)load()},Math.min(delays.length?Math.min(...delays):300000,300000))}async function refreshBookingWindow(){try{let r=await fetch(B+'/api/booking-window');if(!r.ok)return;let v=await r.json();if(!Number.isInteger(v.hours)||!/^\d{4}-\d\d-\d\d/.test(v.now)||!/^\d{4}-\d\d-\d\d/.test(v.cutoff))return;bookingHours=v.hours;let today=v.now.slice(0,10),limit=v.cutoff.slice(0,10);day.min=today;day.max=limit;returnDay.min=today;returnDay.max=limit;if(day.value<today||day.value>limit){day.value=today;chosen=null;booking.style.display='none'}if(returnDay.value<today||returnDay.value>limit)returnDay.value=day.value;renderHorizonHint()}catch(e){}}async function load(){let request=++tripsRequest;await refreshBookingWindow();let selectedDate=day.value,direction=dir.value==='reverse'?'reverse':'forward';try{let r=await fetch(B+'/api/trips?date='+selectedDate+'&direction='+direction);let data=await r.json();if(request!==tripsRequest||selectedDate!==day.value||direction!==(dir.value==='reverse'?'reverse':'forward'))return;trips=data;updateDirectionLabels(trips);render();scheduleTripRefresh(trips)}catch(e){if(request===tripsRequest){trips=[];render();scheduleTripRefresh([])}}}function render(){let h=trips.map(x=>`<div class="card route"><div><div class="big">${esc(x.time.slice(0,5))} <span class="muted">${esc(x.name)}</span></div><div>${esc(point(x.origin))} → ${esc(point(x.destination))}</div><div class="muted">${x.past?t[lang].past:x.free>0?x.free+' '+t[lang].free+' '+x.capacity:t[lang].none}</div></div><button ${x.past||!x.free?'disabled':''} onclick="choose(${x.id})">${t[lang].select}</button></div>`).join('');document.getElementById('trips').innerHTML=h||'<p class="muted">'+t[lang].empty+'</p>'}function choose(id){chosen=trips.find(x=>x.id===id);selected.textContent=point(chosen.origin)+' → '+point(chosen.destination)+' · '+chosen.time.slice(0,5)+' · '+chosen.name;booking.style.display='block';booking.scrollIntoView({behavior:'smooth'});if(round.checked)loadReverse()}function roundChanged(){returnWrap.style.display=round.checked?'block':'none';if(round.checked)loadReverse()}async function loadReverse(){if(!returnDay.value)return;let r=await fetch(B+'/api/trips?date='+returnDay.value+'&direction='+(dir.value==='reverse'?'forward':'reverse'));let data=await r.json();reverse.innerHTML='<option value="">'+t[lang].chooseReturn+'</option>'+data.filter(x=>!x.past&&x.free>0&&(!chosen||(x.origin===chosen.destination&&x.destination===chosen.origin))).map(x=>`<option value="${x.id}">${esc(x.time.slice(0,5))} · ${esc(x.name)} · ${esc(point(x.origin))} → ${esc(point(x.destination))} · ${x.free}/${x.capacity}</option>`).join('')}
async function findBooking(){lookupResult.innerHTML='';let value=lookupCard.value.trim();try{let r=await fetch(B+'/api/find-booking',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({card:value})});let x=await r.json();if(!r.ok)throw Error(typeof x.detail==='string'?x.detail:'Error');if(!x.items.length){lookupResult.innerHTML='<p class="alert">'+t[lang].findNone+'</p>';return}if(x.items.length===1){location.href=x.items[0].url;return}lookupResult.innerHTML='<p>'+t[lang].findMultiple+'</p>'+x.items.map(a=>'<p><a href="'+esc(a.url)+'">'+esc(a.legs.map(l=>l.date+' '+l.departure+' · '+point(l.origin)+' → '+point(l.destination)).join(' / '))+'</a></p>').join('')}catch(e){lookupResult.innerHTML='<p class="alert">'+esc(e.message)+'</p>'}}function viewExisting(){lookupCard.value=card.value;findBooking()}async function book(){err.innerHTML='';let problem=!day.value?t[lang].date:!chosen?t[lang].choose:round.checked&&!reverse.value?t[lang].chooseReturn:'';if(problem){err.innerHTML='<div class="alert">'+esc(problem)+'</div>';return}submit.disabled=true;try{let r=await fetch(B+'/api/book',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({card:card.value,date:day.value,forward:chosen.id,reverse:round.checked?Number(reverse.value):null,reverse_date:round.checked?returnDay.value:null,lang,direction:dir.value==='reverse'?'reverse':'forward'})});let x=await r.json();if(!r.ok){if(x.detail&&x.detail.code==='OUTSIDE_BOOKING_WINDOW'){err.innerHTML='<div class="alert">'+t[lang].horizonError.replace('{hours}',x.detail.hours||bookingHours)+'</div>';return}if(x.detail&&x.detail.code==='ACTIVE_BOOKING_EXISTS'){err.innerHTML='<div class="alert">'+t[lang].duplicate+'<br><button class="secondary" style="margin-top:10px" onclick="viewExisting()">'+t[lang].findExisting+'</button></div>';return}throw Error((window.BusPassengerL10n?.errors?.[lang]||{})[x.detail?.code]||x.detail?.message||(typeof x.detail==='string'?x.detail:(lang==='tr'?'Rezervasyon yapılamadı':'Не удалось забронировать')))}try{let token=x.url.match(/\\/ticket\\/([A-Za-z0-9_-]{20,100})$/);if(token)localStorage.setItem('bus-ticket:'+card.value.trim(),token[1])}catch(e){}location.href=x.url+(x.url.includes('?')?'&':'?')+'completed=1'}catch(e){err.innerHTML='<div class="alert">'+esc(e.message)+'</div>';load()}finally{submit.disabled=false}}document.addEventListener('visibilitychange',()=>{if(!document.hidden)load()});translate();loadStopNames();load();</script></body></html>'''
TICKET='''<!doctype html><html><head>'''+STYLE+'''<title>Бронь / Rezervasyon</title></head><body><header><span class="brand"><img src="/bus/logo.png" alt="TSM ENERJİ"><span class="brand-label">🚌 BUS</span></span><select id="languageSelect" class="headbutton language-select" aria-label="Language / Язык" onchange="setTicketLang(this.value)"><option value="tr">🇹🇷 Türkçe</option><option value="ru">🇷🇺 Русский</option><option value="ky">🇰🇬 Кыргызча</option><option value="uz">🇺🇿 O‘zbekcha</option><option value="tg">🇹🇯 Тоҷикӣ</option><option value="kk">🇰🇿 Қазақша</option><option value="tk">🇹🇲 Türkmençe</option><option value="en">🇬🇧 English</option><option value="zh">🇨🇳 简体中文</option></select></header><main><div class="box"><div class="success" id="ok"></div><div class="direction" id="direction"></div><div id="legs"></div><hr><p><b id="cardLabel"></b> <span id="cardVal"></span></p><p><b id="codeLabel"></b> <span id="codeVal"></span></p><div class="fortune"><b id="fortuneLabel"></b><br><span id="fortuneText"></span></div><div id="qrTools"><img class="qr" id="qr" alt="QR"><div class="row"><button onclick="dl('pdf')">↓ PDF</button><button onclick="dl('jpg')">↓ JPG</button></div></div><button class="secondary" style="width:100%;margin-top:10px" onclick="copy()" id="copy"></button><button id="cancelTicketButton" style="width:100%;margin-top:10px;background:#a72828" onclick="cancelTicket()"></button><div id="cancelError"></div><p class="muted small" id="cancelNote"></p><p class="muted small" id="expiry"></p><div id="autoReturn" class="muted" style="display:none;text-align:center;padding:12px;background:#eef5fc;border-radius:9px"><span id="autoReturnText"></span> <b id="autoReturnSeconds">15</b> <span id="autoReturnUnit"></span><br><a href="/bus/" id="returnNow"></a></div></div></main><script src="/bus/passenger-i18n.js?v=20261117-zh"></script><script>let data=/*DATA*/,L=localStorage.getItem('bus-lang')||data.lang;try{localStorage.setItem('bus-ticket:'+data.card,data.token)}catch(e){}let d={ru:{ok:'✓ ПОСАДКА РАЗРЕШЕНА',one:'→ В ОДНУ СТОРОНУ',both:'⇄ В ОБЕ СТОРОНЫ',card:'Пропуск:',code:'Бронь:',seat:'Запись',fortune:'Ваше пожелание',copy:'Скопировать ссылку',exp:'Ссылка действует 30 минут после последнего отправления.',cancel:'Отменить бронь',cancelled:'БРОНЬ ОТМЕНЕНА',expired:'СРОК ДЕЙСТВИЯ ССЫЛКИ ИСТЁК',cancelConfirm:'Отменить все рейсы, которые ещё не отправились? Уже начавшиеся рейсы сохранятся в истории.',started:'Все рейсы уже отправились.',cancelledLeg:'Рейс отменён',cancelOne:'Отменить этот рейс',cancelOneConfirm:'Отменить только этот рейс?',boardedLeg:'Посадка отмечена',returnText:'На страницу бронирования через',returnUnit:'сек.',returnNow:'Вернуться сейчас'},tr:{ok:'✓ BİNİŞ ONAYLANDI',one:'→ TEK YÖN',both:'⇄ GİDİŞ - DÖNÜŞ',card:'Kart:',code:'Rezervasyon:',seat:'Kayıt',fortune:'Dileğiniz',copy:'Bağlantıyı kopyala',exp:'Bağlantı son kalkıştan sonra 30 dakika geçerlidir.',cancel:'Rezervasyonu iptal et',cancelled:'REZERVASYON İPTAL EDİLDİ',expired:'BAĞLANTI SÜRESİ DOLDU',cancelConfirm:'Henüz kalkmamış tüm seferler iptal edilsin mi? Başlayan seferler geçmişte korunur.',started:'Tüm seferler hareket etti.',cancelledLeg:'Sefer iptal edildi',cancelOne:'Bu seferi iptal et',cancelOneConfirm:'Yalnızca bu sefer iptal edilsin mi?',boardedLeg:'Biniş kaydedildi',returnText:'Rezervasyon sayfasına dönüş:',returnUnit:'sn.',returnNow:'Şimdi dön'}};Object.assign(d,window.BusPassengerL10n?.ticket||{});if(!d[L])L='ru';let returnTimer=null,returnDeadline=0;function startAutoReturn(){if(returnTimer!==null)clearInterval(returnTimer);returnDeadline=Date.now()+15000;autoReturn.style.display='block';function tick(){let left=Math.max(0,Math.ceil((returnDeadline-Date.now())/1000));autoReturnSeconds.textContent=left;if(!left){clearInterval(returnTimer);location.replace('/bus/')}}tick();returnTimer=setInterval(tick,250)}function e(v){return String(v).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function point(v){return L==='tr'?(data.stop_translations?.[v]||v):v}function render(){let x=d[L],invalid=data.cancelled||data.expired;document.documentElement.lang=L;languageSelect.value=L;ok.textContent=data.expired?x.expired:data.cancelled?x.cancelled:x.ok;ok.classList.toggle('cancelled-banner',!!invalid);qrTools.style.display=invalid?'none':'block';document.getElementById('copy').style.display=data.expired?'none':'block';cancelTicketButton.style.display=!invalid&&data.can_cancel?'block':'none';cancelTicketButton.textContent=x.cancel;cancelNote.textContent=!invalid&&!data.can_cancel?x.started:'';direction.textContent=data.legs.length>1?x.both:x.one;cardLabel.textContent=x.card;cardVal.textContent=data.card;codeLabel.textContent=x.code;codeVal.textContent=data.code;fortuneLabel.textContent=x.fortune;fortuneText.textContent=L==='tr'?data.fortune_tr:L==='kk'?(data.fortune_kk||data.fortune):L==='tk'?(data.fortune_tk||data.fortune):L==='zh'?(data.fortune_zh||data.fortune):data.fortune;document.getElementById('copy').textContent=x.copy;expiry.textContent=x.exp;autoReturnText.textContent=x.returnText;autoReturnUnit.textContent=x.returnUnit;returnNow.textContent=x.returnNow;legs.innerHTML=data.legs.map(a=>`<div class="card"><b>${e(point(a.origin))} → ${e(point(a.destination))}</b><br>${e(a.date)} · <strong>${e(a.departure.slice(0,5))}</strong><br><span class="muted">${e(a.route_name)}<br>${x.seat}: ${a.sequence} / ${a.capacity}</span>${a.cancelled?'<p class="alert">'+x.cancelledLeg+'</p>':''}${a.boarded_at?'<p class="muted">'+x.boardedLeg+'</p>':''}${!a.cancelled&&!a.boarded_at&&new Date(a.date+'T'+a.departure+'+03:00')>new Date()?'<button class="secondary" onclick="cancelLeg('+a.id+')">'+x.cancelOne+'</button>':''}${a.comment?'<p>'+e(a.comment)+'</p>':''}</div>`).join('')}async function cancelTicket(){if(!confirm(d[L].cancelConfirm))return;cancelTicketButton.disabled=true;cancelError.innerHTML='';try{let r=await fetch('/bus/api/ticket/'+data.token+'/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:true})});let x=await r.json();if(!r.ok)throw Error((window.BusPassengerL10n?.errors?.[L]||{})[x.detail?.code]||x.detail?.message||(typeof x.detail==='string'?x.detail:'Error'));data.cancelled=!!x.cancelled;data.can_cancel=false;for(let leg of data.legs)if(x.cancelled_legs?.includes(leg.id))leg.cancelled=true;render();startAutoReturn();await refreshComments()}catch(x){cancelError.innerHTML='<p class="alert">'+e(x.message)+'</p>';await refreshComments()}finally{cancelTicketButton.disabled=false}}async function cancelLeg(id){if(!confirm(d[L].cancelOneConfirm))return;cancelError.innerHTML='';try{let r=await fetch('/bus/api/ticket/'+data.token+'/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:true,leg_id:id})});let x=await r.json();if(!r.ok)throw Error((window.BusPassengerL10n?.errors?.[L]||{})[x.detail?.code]||x.detail?.message||x.detail?.code||x.detail||'Error');let leg=data.legs.find(a=>a.id===id);if(leg)leg.cancelled=true;data.cancelled=!!x.cancelled;await refreshComments();render()}catch(x){cancelError.innerHTML='<p class="alert">'+e(x.message)+'</p>'}}function setTicketLang(next){if(!d[next])return;L=next;localStorage.setItem('bus-lang',L);render()}function dl(f){location.href='/bus/ticket/'+data.token+'/download/'+f+'?lang='+L}async function copy(){try{await navigator.clipboard.writeText(location.href)}catch(e){prompt('URL',location.href)}}async function refreshComments(){try{let r=await fetch('/bus/api/ticket/'+data.token+'/comments');if(r.status===404){data.expired=true;data.can_cancel=false;render();return}if(!r.ok)return;let v=await r.json();let newlyCancelled=!data.cancelled&&v.cancelled;let changed=data.cancelled!==v.cancelled||data.can_cancel!==v.can_cancel;data.cancelled=v.cancelled;if(newlyCancelled)startAutoReturn();data.can_cancel=v.can_cancel;for(let item of v.comments){let leg=data.legs.find(x=>x.id===item.id);if(leg&&(leg.comment!==item.comment||!!leg.cancelled!==!!item.cancelled||leg.boarded_at!==item.boarded_at)){leg.comment=item.comment;leg.cancelled=item.cancelled;leg.boarded_at=item.boarded_at;changed=true}}if(changed)render()}catch(e){}}document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshComments()});setInterval(refreshComments,10000);if(!data.cancelled)qr.src='/bus/ticket/'+data.token+'/qr';render();let params=new URLSearchParams(location.search),completed=params.get('completed')==='1';if(completed){params.delete('completed');history.replaceState(null,'',location.pathname+(params.toString()?'?'+params.toString():'')+location.hash)}if(data.cancelled||completed)startAutoReturn();</script></body></html>'''
ADMIN='''<!doctype html><html><head>'''+STYLE+'''<title>Админ / Yönetim</title></head><body><header><span class="brand"><img src="/bus/logo.png" alt="TSM ENERJİ"><span class="brand-label">🚌 BUS · ADMIN</span></span><button class="headbutton" onclick="lang=lang==='ru'?'tr':'ru';renderLanguage()">RU / TR</button></header><style>main.admin-main{max-width:1100px;padding:12px 12px 40px}.admin-main h1{font-size:21px;margin:3px 0 10px}.admin-main h2{font-size:16px;margin:0 0 8px}.admin-main .box{padding:10px;margin:7px 0}.admin-main label{font-size:12px;margin:5px 0 3px}.admin-main input,.admin-main select,.admin-main textarea{min-width:0;padding:7px;font-size:13px}.admin-main button{padding:7px 9px;font-size:13px}.admin-main .row{gap:7px}.admin-tabs{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:6px;margin-bottom:7px}.admin-tabs button{min-width:0;overflow-wrap:anywhere}@media(max-width:650px){.admin-tabs{grid-template-columns:repeat(2,minmax(0,1fr))}}.qr-table{table-layout:fixed;word-break:break-word}.qr-table th:first-child,.qr-table td:first-child{width:34%}.qr-table td{vertical-align:top}.qr-table .status{display:inline-block;margin:4px 0}.qr-table .cancel-booking{background:#fff0ed;color:#a63129;margin-top:4px}.qr-small{max-width:180px;width:100%;display:block}.route-search{margin-bottom:7px}.schedule-columns{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}.schedule-columns h3{font-size:13px;margin:1px 0 5px;color:#07549b}.mini-route{background:#f6f9fc;border:1px solid #dce5ee;border-radius:7px;padding:6px;margin:0 0 5px;font-size:12px;overflow-wrap:anywhere}.mini-route.forward-route{background:#edf5ff;border-color:#bbd8f3}.mini-route.reverse-route{background:#edf8f3;border-color:#bce1cf}.mini-route strong{font-size:15px}.mini-route .route-actions{display:flex;gap:4px;margin-top:4px}.mini-route .route-actions button{padding:4px 6px;font-size:11px}.mini-route .delete-route{background:#fff0ed;color:#a63129}.edit-box{background:#edf6ff;border:1px solid #bbd9f0;border-left:4px solid #438dc6}.edit-box.is-editing{background:#e5f1fd;border-color:#72aad5;border-left-color:#07549b}.admin-main .edit-box h2{color:#07549b;margin:0 0 6px}.compact-form{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:4px 7px}.compact-form>div{min-width:0}.weekday-options{display:flex;flex-wrap:wrap;gap:4px 12px}.admin-main .weekday-options label{display:flex;align-items:center;gap:3px;margin:0}.weekday-options input{width:auto;margin:0}.full{grid-column:1/-1}.admin-main details{margin-top:7px}.admin-main summary{cursor:pointer;color:#07549b;font-weight:600}.admin-main .alert{padding:8px;margin:6px 0}.admin-main hr{border:0;border-top:1px solid #e1e8ef;margin:8px 0}.admin-main .status{font-size:10px}.admin-main .status.inactive{background:#ffe5e2;color:#b32922}.admin-main .small{font-size:12px}</style><main class="admin-main"><h1 data-i="admin"></h1><nav class="admin-tabs"><button id="scheduleTab" onclick="showTab('schedule')" data-i="scheduleTab"></button><button id="whiteTab" class="secondary" onclick="showTab('white')" data-i="whitelist"></button><button id="qrTab" class="secondary" onclick="showTab('qr')" data-i="qrList"></button><button id="stopsTab" class="secondary" onclick="showTab('stops')" data-i="stops"></button><button id="settingsTab" class="secondary" onclick="showTab('settings')" data-i="settingsTab"></button></nav><div id="managePanel"><div class="box"><h2 data-i="schedule"></h2><input id="routeSearch" class="route-search" type="search" data-i-placeholder="findRoute" oninput="renderRoutes()"><div id="routeActionError"></div><div id="routes"></div></div><section class="box edit-box" id="routeForm"><h2 id="formTitle"></h2><div class="compact-form"><div><label data-i="name"></label><input id="name"></div><div><label data-i="direction"></label><select id="route_direction" onchange="syncRouteFromDirection()"><option value="forward" data-i="directRoute"></option><option value="reverse" data-i="reverseRoute"></option></select></div><div><label data-i="origin"></label><select id="origin" onchange="syncRouteFromOrigin()"><option value="" data-i="chooseOrigin"></option><option value="Камп" data-i="camp"></option><option value="Магазин" data-i="shop"></option></select></div><div><label data-i="destination"></label><select id="destination" onchange="syncRouteFromDestination()"><option value="" data-i="chooseOrigin"></option><option value="Камп" data-i="camp"></option><option value="Магазин" data-i="shop"></option></select></div><div><label data-i="departureTime"></label><input type="time" id="departure_time"></div><div><label data-i="serviceDate"></label><input type="date" id="service_date"></div><div class="full"><label data-i="weekdays"></label><div class="weekday-options"><label><input type="checkbox" class="weekday" value="1" checked><span data-i="day0"></span></label><label><input type="checkbox" class="weekday" value="2" checked><span data-i="day1"></span></label><label><input type="checkbox" class="weekday" value="4" checked><span data-i="day2"></span></label><label><input type="checkbox" class="weekday" value="8" checked><span data-i="day3"></span></label><label><input type="checkbox" class="weekday" value="16" checked><span data-i="day4"></span></label><label><input type="checkbox" class="weekday" value="32" checked><span data-i="day5"></span></label><label><input type="checkbox" class="weekday" value="64" checked><span data-i="day6"></span></label></div></div><div><label data-i="capacity"></label><input type="number" id="capacity" value="50" min="1" max="500"></div><div><label><input type="checkbox" id="active" checked><span data-i="active"></span></label></div><div class="full"><label data-i="comment"></label><input id="comment" maxlength="160" data-i-placeholder="commentHint"></div><div class="full"><button onclick="save()" data-i="save"></button> <button id="resetRoute" class="secondary" onclick="resetForm()"></button><div id="formErr"></div></div></div></section><div class="box"><h2 data-i="history"></h2><div class="row"><div><label data-i="from"></label><input type="date" id="date_from"></div><div><label data-i="to"></label><input type="date" id="date_to"></div><div><label data-i="route"></label><select id="route_id"><option value="">Все / Tümü</option></select></div><div><label data-i="card"></label><input id="filter_card"></div><div><label data-i="timeFrom"></label><input type="time" id="time_from"></div><div><label data-i="timeTo"></label><input type="time" id="time_to"></div><div><label data-i="origin"></label><input id="filter_origin"></div><div><label data-i="historyStatus"></label><select id="histStatus"><option value="all" data-i="allStatuses"></option><option value="booked" data-i="bookedHistory"></option><option value="cancelled" data-i="cancelledQr"></option></select></div></div><button style="margin-top:14px" onclick="historyLoad()" data-i="filter"></button><p class="muted small" data-i="retention"></p><div id="hist" class="scroll"></div></div></div><section id="whitePanel" style="display:none"><div class="row"><div class="box"><h2 data-i="excelTitle"></h2><p class="muted small" data-i="excelHint"></p><input type="file" id="excelFile" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" onchange="inspectExcel()"><div id="excelChoices" style="display:none"><label data-i="sheet"></label><select id="excelSheet" onchange="selectExcelSheet()"></select><label data-i="column"></label><select id="excelColumn" onchange="selectExcelColumn()"></select><label data-i="firstRow"></label><input id="excelFirstRow" type="number" value="1" min="1" max="50000" oninput="invalidatePreview()"><label data-i="mode"></label><select id="excelMode"><option value="append" data-i="append"></option><option value="replace" data-i="replace"></option></select><button style="margin-top:8px" onclick="previewExcel()" data-i="preview"></button><div id="excelStats" class="small"></div><button id="excelImport" disabled onclick="importExcel()" data-i="importExcel"></button></div><div id="excelError"></div></div><div class="box"><h2 data-i="whitelist"></h2><div id="whiteCount" class="muted small"></div><label data-i="search"></label><input id="whiteSearch" inputmode="numeric" oninput="searchWhitelist()"><div id="whiteResults" class="small"></div><details><summary data-i="editList"></summary><p class="muted small" data-i="whitelistHint"></p><textarea id="allowed" rows="10" spellcheck="false"></textarea><button onclick="saveWhitelist()" data-i="saveWhitelist"></button></details><div id="whiteStatus" class="small"></div></div></div></section><section id="qrPanel" style="display:none"><div class="box"><h2 data-i="qrList"></h2><div class="compact-form"><div class="full"><label data-i="qrSearch"></label><input id="qrSearchInput" type="search" onkeydown="if(event.key==='Enter')loadQr(0)"></div><div><label data-i="qrDate"></label><input type="date" id="qrDate"></div><div><label data-i="qrTime"></label><input type="time" id="qrTime"></div><div><label data-i="qrStatus"></label><select id="qrStatus"><option value="all" data-i="allStatuses"></option><option value="active" data-i="validQr"></option><option value="expired" data-i="expiredQr"></option><option value="cancelled" data-i="cancelledQr"></option></select></div><div><label>&nbsp;</label><button onclick="loadQr(0)" data-i="filter"></button></div></div><div id="qrCount" class="muted small"></div><div id="qrActionError"></div><div id="qrResults" class="scroll"></div><div class="row"><button id="qrPrev" class="secondary" onclick="loadQr(qrOffset-30)" data-i="prevPage"></button><button id="qrNext" class="secondary" onclick="loadQr(qrOffset+30)" data-i="nextPage"></button></div></div><div class="box"><h2 data-i="verify"></h2><p class="muted small" data-i="verifyHint"></p><button onclick="scan()" data-i="camera"></button> <button class="secondary" onclick="stopScan()" data-i="stop"></button><video id="video" playsinline></video><label data-i="qr"></label><input id="qrtext"><button style="margin-top:10px" onclick="verify()" data-i="check"></button><div id="result"></div></div></section><section id="stopsPanel" style="display:none"><div class="row"><div class="box"><h2 data-i="stopList"></h2><input id="stopSearchInput" type="search" data-i-placeholder="stopSearch" oninput="renderStops()"><div id="stopListItems"></div><p class="muted small" data-i="stopHint"></p></div><div class="box edit-box"><h2 id="stopFormTitle"></h2><label data-i="stopRu"></label><input id="stopNameRu" maxlength="80"><label data-i="stopTr"></label><input id="stopNameTr" maxlength="80"><label><input id="stopActive" type="checkbox" checked><span data-i="active"></span></label><button onclick="saveStop()" data-i="save"></button> <button id="resetStopButton" class="secondary" onclick="resetStop()"></button><div id="stopError"></div></div></div></section><section id="settingsPanel" style="display:none"><div class="box"><h2 data-i="settingsTitle"></h2><p class="muted small" data-i="settingsHelp"></p><label for="bookingHorizonHours" data-i="settingsHours"></label><input type="number" id="bookingHorizonHours" min="1" max="720" step="1" value="48" style="max-width:180px"><button id="settingsSaveButton" onclick="saveSettings()" data-i="settingsSave" style="margin-left:8px"></button><p id="settingsStatus" class="muted small"></p><hr><h2><span data-i="driversTitle"></span> <span id="driverCount">0/10</span></h2><p class="small muted" data-i="driversHelp"></p><div id="driverList"></div><button id="driverAddButton" class="secondary" onclick="resetDriver()" data-i="driverNew"></button><form id="driverForm" class="compact-form" onsubmit="event.preventDefault();saveDriver()"><h2 class="full" id="driverFormTitle"></h2><div><label for="driverLogin" data-i="driverLoginLabel"></label><input id="driverLogin" minlength="3" maxlength="40" pattern="[a-zA-Z0-9_.-]{3,40}" autocomplete="off" required></div><div><label for="driverName" data-i="driverNameLabel"></label><input id="driverName" maxlength="80"></div><div><label for="driverPassword" data-i="driverPasswordLabel"></label><input id="driverPassword" type="password" maxlength="128" autocomplete="new-password"><p class="small muted" data-i="driverPasswordHint"></p></div><div><label><input type="checkbox" id="driverActive" checked><span data-i="active"></span></label></div><div class="full"><button id="driverSaveButton" type="submit" data-i="save"></button> <button type="button" class="secondary" onclick="resetDriver()" data-i="cancelEdit"></button><p id="driverStatus" class="small" role="status"></p></div></form></div></section></main><script>
const B='/bus';let lang=['ru','tr'].includes(localStorage.getItem('bus-lang'))?localStorage.getItem('bus-lang'):'ru',editId=null,routesData=[],historyRoutes=[],stream=null,whiteCards=[],whiteFile=null,whiteSheets=[],qrOffset=0,qrTotal=0,stopsData=[],stopEditId=null;const T={ru:{admin:'Управление автобусами',scheduleTab:'Расписание',stops:'Остановки',stopList:'Справочник остановок',stopSearch:'Найти остановку',stopNew:'Новая остановка',stopEdit:'Редактирование остановки',stopRu:'Название на русском',stopTr:'Название на турецком',stopHint:'Неактивные остановки не предлагаются для новых рейсов.',qrList:'Бронирования',qrSearch:'Номер брони, пропуска или маршрут',qrDate:'Дата рейса',qrTime:'Время',qrStatus:'Статус',allStatuses:'Все',validQr:'Действует',expiredQr:'Не действует',cancelledQr:'Отменена',bookedHistory:'Забронирована',cancelBooking:'Отменить бронь',cancelBookingConfirm:'Отменить все рейсы, которые ещё не отправились? Уже начавшиеся рейсы сохранятся в истории.',historyStatus:'Статус брони',qrApplication:'Заявка',qrDecoded:'Расшифровка QR',qrCard:'Пропуск',qrPosition:'Запись',showQr:'Показать QR',hideQr:'Скрыть QR',prevPage:'← Назад',nextPage:'Далее →',findRoute:'Поиск по рейсу, направлению или времени',forwardList:'В магазин',reverseList:'В камп',excelTitle:'Загрузка Excel',excelHint:'Файл .xlsx до 5 МБ. Выберите лист и столбец с номерами.',sheet:'Лист',column:'Столбец с номерами',firstRow:'Начать со строки',mode:'Режим загрузки',append:'Добавить к списку',replace:'Заменить список',preview:'Проверить файл',importExcel:'Загрузить номера',search:'Найти номер',editList:'Редактировать список вручную',found:'Найдено: ',totalCards:'Всего номеров: ',imported:'Сохранено. Всего номеров: ',previewValid:'Корректных строк: ',previewUnique:'Уникальных: ',previewDuplicate:'Повторов: ',previewInvalid:'Ошибочных строк (не будут добавлены): ',previewRows:'Номера строк с ошибками: ',previewExisting:'Уже в списке: ',previewNew:'Новых: ',replaceConfirm:'Заменить весь белый список номерами из Excel?',schedule:'Расписание',weekdays:'Дни недели',day0:'Пн',day1:'Вт',day2:'Ср',day3:'Чт',day4:'Пт',day5:'Сб',day6:'Вс',daily:'Ежедневно',weekdaysOnly:'Будни',name:'Название рейса',origin:'Пункт отправления',destination:'Пункт назначения',direction:'Направление рейса',chooseOrigin:'Выберите пункт',camp:'Камп',shop:'Магазин',directRoute:'Прямой',reverseRoute:'Обратный',departureTime:'Время отправления',newForm:'Новый рейс',editForm:'Редактирование рейса',clearForm:'Очистить',cancelEdit:'Отменить',capacity:'Мест',serviceDate:'Дата рейса (необязательно)',comment:'Комментарий к рейсу (необязательно)',commentHint:'Например: автобус 33 ABC 123, парковка № 2',active:'Активен',save:'Сохранить',new:'Новый рейс',whitelist:'Разрешённые пропуска',whitelistHint:'По одному номеру в строке. Записаться могут только номера из этого списка.',saveWhitelist:'Сохранить список',whiteSaved:'Список сохранён. Номеров: ',history:'История поездок',from:'Дата с',to:'Дата по',route:'Рейс',card:'Номер пропуска',timeFrom:'Время с',timeTo:'Время по',filter:'Показать',retention:'История хранится 6 месяцев.',verify:'Проверка QR',verifyHint:'Отсканируйте QR камерой или вставьте его содержимое.',camera:'Открыть камеру',stop:'Остановить',qr:'Содержимое QR',check:'Проверить',edit:'Изменить',deleteRoute:'Удалить',deleteConfirm:'Удалить рейс?',deleted:'Удалён',inactive:'Не действует',on:'Активен',fortune:'Пожелание',noScan:'Камера или сканирование QR не поддерживается этим браузером.',settingsTab:'Настройки',settingsTitle:'Настройки бронирования',settingsHours:'Горизонт бронирования, часов',settingsHelp:'Бронирование разрешено только на указанное число часов вперёд от текущего времени (1–720). Уже оформленные брони сохраняются.',settingsSave:'Сохранить',settingsSaved:'Сохранено.',settingsInvalid:'Введите целое число от 1 до 720.',driversTitle:'Аккаунты водителей',driversHelp:'До 10 аккаунтов, включая отключённые. Удалите ненужный, чтобы освободить место.',driverNew:'Добавить водителя',driverEdit:'Изменить аккаунт',driverLoginLabel:'Логин',driverNameLabel:'Имя водителя / автобус',driverPasswordLabel:'Пароль / сброс пароля',driverPasswordHint:'8–128 символов. При изменении оставьте пустым, чтобы сохранить текущий пароль.',driverDeleteConfirm:'Удалить аккаунт водителя? Вход будет закрыт сразу.',driverInvalid:'Логин: 3–40 латинских букв, цифр, _, . или -. Имя: до 80 символов. Пароль: 8–128 символов.'},tr:{admin:'Otobüs yönetimi',scheduleTab:'Seferler',stops:'Duraklar',stopList:'Durak listesi',stopSearch:'Durak ara',stopNew:'Yeni durak',stopEdit:'Durağı düzenle',stopRu:'Rusça adı',stopTr:'Türkçe adı',stopHint:'Pasif duraklar yeni seferlerde gösterilmez.',qrList:'Rezervasyonlar',qrSearch:'Rezervasyon, kart veya güzergâh',qrDate:'Sefer tarihi',qrTime:'Saat',qrStatus:'Durum',allStatuses:'Tümü',validQr:'Geçerli',expiredQr:'Süresi doldu',cancelledQr:'İptal edildi',bookedHistory:'Rezerve edildi',cancelBooking:'Rezervasyonu iptal et',cancelBookingConfirm:'Henüz kalkmamış tüm seferler iptal edilsin mi? Başlayan seferler geçmişte korunur.',historyStatus:'Rezervasyon durumu',qrApplication:'Rezervasyon',qrDecoded:'QR içeriği',qrCard:'Kart',qrPosition:'Kayıt',showQr:'QR göster',hideQr:'QR gizle',prevPage:'← Önceki',nextPage:'Sonraki →',findRoute:'Sefer, yön veya saat ara',forwardList:'Mağazaya',reverseList:'Kampa',excelTitle:'Excel yükle',excelHint:'En fazla 5 MB .xlsx dosyası. Kart sütununu ve sayfayı seçin.',sheet:'Sayfa',column:'Kart numarası sütunu',firstRow:'Başlangıç satırı',mode:'Yükleme modu',append:'Listeye ekle',replace:'Listeyi değiştir',preview:'Dosyayı kontrol et',importExcel:'Kartları yükle',search:'Kart numarası ara',editList:'Listeyi elle düzenle',found:'Bulunan: ',totalCards:'Toplam kart: ',imported:'Kaydedildi. Toplam kart: ',previewValid:'Geçerli satır: ',previewUnique:'Benzersiz: ',previewDuplicate:'Tekrar: ',previewInvalid:'Hatalı satır (eklenmeyecek): ',previewRows:'Hatalı satır numaraları: ',previewExisting:'Listede mevcut: ',previewNew:'Yeni: ',replaceConfirm:'İzin listesinin tamamı Excel içeriğiyle değiştirilsin mi?',schedule:'Seferler',weekdays:'Haftanın günleri',day0:'Pzt',day1:'Sal',day2:'Çar',day3:'Per',day4:'Cum',day5:'Cmt',day6:'Paz',daily:'Her gün',weekdaysOnly:'Hafta içi',name:'Sefer adı',origin:'Kalkış noktası',destination:'Varış noktası',direction:'Sefer yönü',chooseOrigin:'Nokta seçin',camp:'Kamp',shop:'Mağaza',directRoute:'Gidiş',reverseRoute:'Dönüş',departureTime:'Kalkış saati',newForm:'Yeni sefer',editForm:'Seferi düzenle',clearForm:'Temizle',cancelEdit:'İptal',capacity:'Koltuk',serviceDate:'Sefer tarihi (isteğe bağlı)',comment:'Sefer açıklaması (isteğe bağlı)',commentHint:'Örneğin: 33 ABC 123, 2 numaralı otopark',active:'Aktif',save:'Kaydet',new:'Yeni sefer',whitelist:'İzin verilen kartlar',whitelistHint:'Her satıra bir kart numarası yazın. Yalnızca listedeki kartlar rezervasyon yapabilir.',saveWhitelist:'Listeyi kaydet',whiteSaved:'Liste kaydedildi. Kart sayısı: ',history:'Yolculuk geçmişi',from:'Başlangıç tarihi',to:'Bitiş tarihi',route:'Sefer',card:'Kart numarası',timeFrom:'Başlangıç saati',timeTo:'Bitiş saati',filter:'Göster',retention:'Geçmiş 6 ay saklanır.',verify:'QR doğrulama',verifyHint:'QR kodunu kamerayla tarayın veya içeriğini yapıştırın.',camera:'Kamerayı aç',stop:'Durdur',qr:'QR içeriği',check:'Doğrula',edit:'Düzenle',deleteRoute:'Sil',deleteConfirm:'Sefer silinsin mi?',deleted:'Silindi',inactive:'Pasif',on:'Aktif',fortune:'Dilek',noScan:'Bu tarayıcı kamera veya QR taramayı desteklemiyor.',settingsTab:'Ayarlar',settingsTitle:'Rezervasyon ayarları',settingsHours:'Rezervasyon süresi, saat',settingsHelp:'Rezervasyon yalnızca şu andan itibaren belirtilen saat sayısı içinde yapılabilir (1–720). Mevcut rezervasyonlar korunur.',settingsSave:'Kaydet',settingsSaved:'Kaydedildi.',settingsInvalid:'1 ile 720 arasında tam sayı girin.',driversTitle:'Sürücü hesapları',driversHelp:'Pasif hesaplar dahil en fazla 10 hesap. Yer açmak için kullanılmayan hesabı silin.',driverNew:'Sürücü ekle',driverEdit:'Hesabı düzenle',driverLoginLabel:'Kullanıcı adı',driverNameLabel:'Sürücü adı / otobüs',driverPasswordLabel:'Parola / parola sıfırlama',driverPasswordHint:'8–128 karakter. Düzenlerken mevcut parolayı korumak için boş bırakın.',driverDeleteConfirm:'Sürücü hesabı silinsin mi? Erişim hemen kapanır.',driverInvalid:'Kullanıcı adı: 3–40 Latin harfi, rakam, _, . veya -. Ad: en fazla 80 karakter. Parola: 8–128 karakter.'}};function e(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}function renderLanguage(){localStorage.setItem('bus-lang',lang);document.querySelectorAll('[data-i]').forEach(x=>x.textContent=T[lang][x.dataset.i]);document.querySelectorAll('[data-i-placeholder]').forEach(x=>x.placeholder=T[lang][x.dataset.iPlaceholder]);renderRoutes();searchWhitelist();renderDrivers();updateFormTitle();renderStops();populateStopChoices();updateStopFormTitle();if(qrPanel.style.display!=='none')loadQr(qrOffset)}async function loadSettings(){try{let v=await api('/api/admin/settings');bookingHorizonHours.value=v.hours;settingsStatus.textContent=''}catch(x){settingsStatus.textContent=x.message}await loadDrivers()}async function saveSettings(){settingsStatus.textContent='';let hours=Number(bookingHorizonHours.value);if(!Number.isInteger(hours)||hours<1||hours>720){settingsStatus.textContent=T[lang].settingsInvalid;return}settingsSaveButton.disabled=true;try{let v=await api('/api/admin/settings',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({hours})});bookingHorizonHours.value=v.hours;settingsStatus.textContent=T[lang].settingsSaved}catch(x){settingsStatus.textContent=x.message}finally{settingsSaveButton.disabled=false}}let driversData=[],driverEditId=null,driverBusy=false;
function renderDrivers(){
 driverCount.textContent=driversData.length+'/10';
 driverAddButton.disabled=driverBusy||driversData.length>=10;
 driverSaveButton.disabled=driverBusy||(driverEditId===null&&driversData.length>=10);
 driverFormTitle.textContent=T[lang][driverEditId===null?'driverNew':'driverEdit'];
 driverList.replaceChildren();
 for(const item of driversData){
  const row=document.createElement('div');row.className='mini-route';
  const label=document.createElement('span');label.textContent=item.display_name+' · '+item.login+' · '+T[lang][item.active?'on':'inactive'];row.appendChild(label);
  const edit=document.createElement('button');edit.className='secondary';edit.textContent=T[lang].edit;edit.disabled=driverBusy;edit.addEventListener('click',()=>editDriver(item.id));row.appendChild(edit);
  const remove=document.createElement('button');remove.className='delete-route';remove.textContent=T[lang].deleteRoute;remove.disabled=driverBusy;remove.addEventListener('click',()=>deleteDriver(item.id));row.appendChild(remove);
  driverList.appendChild(row);
 }
}
async function loadDrivers(){try{const data=await api('/api/admin/drivers');driversData=data.items;renderDrivers()}catch(x){driverStatus.textContent=x.message}}
function editDriver(id){if(driverBusy)return;const item=driversData.find(x=>x.id===id);if(!item)return;driverEditId=id;driverLogin.value=item.login;driverName.value=item.display_name;driverActive.checked=!!item.active;driverPassword.value='';driverStatus.textContent='';renderDrivers();driverLogin.focus()}
function resetDriver(){if(driverBusy)return;driverEditId=null;driverLogin.value='';driverName.value='';driverPassword.value='';driverActive.checked=true;driverStatus.textContent='';renderDrivers()}
async function saveDriver(){
 if(driverBusy)return;
 const login=driverLogin.value.trim(),password=driverPassword.value,display_name=driverName.value;
 driverStatus.textContent='';
 if(!/^[a-zA-Z0-9_.-]{3,40}$/.test(login)||display_name.length>80||((driverEditId===null||password!=='')&&(Array.from(password).length<8||Array.from(password).length>128))){driverStatus.textContent=T[lang].driverInvalid;return}
 const id=driverEditId;driverBusy=true;renderDrivers();
 try{await api('/api/admin/drivers'+(id===null?'':'/'+id),{method:id===null?'POST':'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({login,display_name,password,active:driverActive.checked})});driverPassword.value='';await loadDrivers();driverBusy=false;resetDriver();driverStatus.textContent=T[lang].settingsSaved}
 catch(x){driverStatus.textContent=x.message}finally{driverPassword.value='';driverBusy=false;renderDrivers()}
}
async function deleteDriver(id){
 if(driverBusy||!confirm(T[lang].driverDeleteConfirm))return;
 driverBusy=true;renderDrivers();
 try{await api('/api/admin/drivers/'+id,{method:'DELETE'});await loadDrivers();driverBusy=false;if(driverEditId===id)resetDriver();driverStatus.textContent=T[lang].settingsSaved}
 catch(x){driverStatus.textContent=x.message}finally{driverBusy=false;renderDrivers()}
}
function updateFormTitle(){formTitle.textContent=editId===null?T[lang].newForm:T[lang].editForm;resetRoute.textContent=editId===null?T[lang].clearForm:T[lang].cancelEdit;routeForm.classList.toggle('is-editing',editId!==null)}async function api(path,opts){let r=await fetch(B+path,opts);let x=await r.json();if(!r.ok)throw Error(typeof x.detail==='object'?(x.detail.message||x.detail.code||'Error'):(x.detail||'Error'));return x}function setRouteStop(id,value){let el=document.getElementById(id),old=el.querySelector('[data-legacy]');if(old)old.remove();if(value&&!Array.from(el.options).some(o=>o.value===value)){let opt=document.createElement('option');opt.value=value;opt.textContent=value;opt.dataset.legacy='1';el.appendChild(opt)}el.value=value||''}
function populateStopChoices(){if(!stopsData.length)return;for(let id of ['origin','destination']){let el=document.getElementById(id),current=el.value;el.innerHTML='<option value="">'+e(T[lang].chooseOrigin)+'</option>'+stopsData.filter(x=>x.active).map(x=>`<option value="${e(x.name_ru)}">${e(lang==='tr'?x.name_tr:x.name_ru)}</option>`).join('');setRouteStop(id,current)}}
async function loadStops(){stopsData=await api('/api/admin/stops');populateStopChoices();renderStops()}
function updateStopFormTitle(){stopFormTitle.textContent=stopEditId===null?T[lang].stopNew:T[lang].stopEdit;resetStopButton.textContent=stopEditId===null?T[lang].clearForm:T[lang].cancelEdit}
function renderStops(){let term=stopSearchInput.value.trim().toLocaleLowerCase();let list=stopsData.filter(x=>!term||[x.name_ru,x.name_tr].some(y=>y.toLocaleLowerCase().includes(term)));stopListItems.innerHTML=list.map(x=>`<div class="mini-route"><button class="secondary" onclick="editStop(${x.id})">${T[lang].edit}</button><b>${e(lang==='tr'?x.name_tr:x.name_ru)}</b> <span class="status ${x.active?'':'inactive'}">${x.active?T[lang].on:T[lang].inactive}</span><div class="muted">${e(lang==='tr'?x.name_ru:x.name_tr)}</div></div>`).join('')}
function editStop(id){let x=stopsData.find(z=>z.id===id);if(!x)return;stopEditId=id;stopNameRu.value=x.name_ru;stopNameTr.value=x.name_tr;stopActive.checked=!!x.active;stopError.innerHTML='';updateStopFormTitle();stopNameRu.focus()}
function resetStop(){stopEditId=null;stopNameRu.value='';stopNameTr.value='';stopActive.checked=true;stopError.innerHTML='';updateStopFormTitle()}
async function saveStop(){stopError.innerHTML='';let id=stopEditId,old=stopsData.find(z=>z.id===id),data={name_ru:stopNameRu.value.trim(),name_tr:stopNameTr.value.trim(),active:stopActive.checked};let oldOrigin=document.getElementById('origin').value,oldDestination=document.getElementById('destination').value;try{await api('/api/admin/stops'+(id?'/'+id:''),{method:id?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});resetStop();await loadStops();if(old&&old.name_ru!==data.name_ru){if(oldOrigin===old.name_ru)setRouteStop('origin',data.name_ru);if(oldDestination===old.name_ru)setRouteStop('destination',data.name_ru);await loadRoutes()}}catch(x){stopError.innerHTML='<p class="alert">'+e(x.message)+'</p>'}}
async function loadRoutes(){[routesData,historyRoutes]=await Promise.all([api('/api/admin/routes'),api('/api/admin/route-options')]);renderRoutes()}function showTab(which){managePanel.style.display=which==='schedule'?'block':'none';whitePanel.style.display=which==='white'?'block':'none';qrPanel.style.display=which==='qr'?'block':'none';stopsPanel.style.display=which==='stops'?'block':'none';settingsPanel.style.display=which==='settings'?'block':'none';scheduleTab.className=which==='schedule'?'':'secondary';whiteTab.className=which==='white'?'':'secondary';qrTab.className=which==='qr'?'':'secondary';stopsTab.className=which==='stops'?'':'secondary';settingsTab.className=which==='settings'?'':'secondary';if(which==='qr')loadQr(0);if(which==='settings')loadSettings()}function weekdayLabel(mask){if(mask===127)return T[lang].daily;if(mask===31)return T[lang].weekdaysOnly;return Array.from({length:7},(_,i)=>mask&(1<<i)?T[lang]['day'+i]:null).filter(Boolean).join(', ')}function setWeekdays(mask){document.querySelectorAll('.weekday').forEach(x=>x.checked=!!(mask&Number(x.value)))}function renderRoutes(){let selectedRoute=route_id.value;let term=routeSearch.value.trim().toLocaleLowerCase();let visible=x=>!term||[x.name,x.origin,x.destination,x.forward_time,x.reverse_time,x.service_date].some(v=>String(v||'').toLocaleLowerCase().includes(term));let item=x=>`<div class="mini-route ${x.direction==='reverse'?'reverse-route':'forward-route'}"><strong>${e((x.direction==='reverse'?x.reverse_time:x.forward_time).slice(0,5))}</strong> <span class="status ${x.active?'':'inactive'}">${x.active?T[lang].on:T[lang].inactive}</span><div>${e(x.origin)} → ${e(x.destination)}</div><div>${e(x.name)} · ${x.capacity} · ${e(weekdayLabel(x.weekday_mask))}${x.service_date?' · '+e(x.service_date):''}${x.period_start?' · '+e(x.period_start)+' - '+e(x.period_end):''}</div><div class="route-actions"><button class="secondary" onclick="edit(${x.id})">${T[lang].edit}</button><button class="delete-route" onclick="deleteRoute(${x.id})">${T[lang].deleteRoute}</button></div></div>`;let forward=routesData.filter(x=>x.direction!=='reverse'&&visible(x)).sort((a,b)=>a.forward_time.localeCompare(b.forward_time));let backward=routesData.filter(x=>x.direction==='reverse'&&visible(x)).sort((a,b)=>a.reverse_time.localeCompare(b.reverse_time));routes.innerHTML=`<div class="schedule-columns"><div><h3>${T[lang].forwardList}</h3>${forward.map(item).join('')}</div><div><h3>${T[lang].reverseList}</h3>${backward.map(item).join('')}</div></div>`;route_id.innerHTML='<option value="">Все / Tümü</option>'+historyRoutes.map(x=>`<option value="${x.id}">${e(x.name)} · ${e(x.origin)} → ${e(x.destination)} · ${e((x.direction==='reverse'?x.reverse_time:x.forward_time).slice(0,5))}${x.service_date?' · '+e(x.service_date):''}${x.deleted_at?' · '+T[lang].deleted:''}</option>`).join('');route_id.value=selectedRoute}async function deleteRoute(id){let x=routesData.find(r=>r.id===id);if(!x)return;let when=(x.direction==='reverse'?x.reverse_time:x.forward_time).slice(0,5);if(!confirm(T[lang].deleteConfirm+' '+when+' · '+x.origin+' → '+x.destination))return;routeActionError.innerHTML='';try{await api('/api/admin/routes/'+id,{method:'DELETE'});if(editId===id)resetForm();await loadRoutes()}catch(x){routeActionError.innerHTML='<p class="alert">'+e(x.message)+'</p>'}}function syncRouteFromOrigin(){let v=document.getElementById('origin').value,d=document.getElementById('destination');if(!v)return;if(v==='Камп'||v==='Магазин'){route_direction.value=v==='Камп'?'forward':'reverse';if(!d.value||['Камп','Магазин'].includes(d.value))d.value=v==='Камп'?'Магазин':'Камп'}else if(d.value===v)d.value=''}function syncRouteFromDestination(){let v=document.getElementById('destination').value,o=document.getElementById('origin');if(!v)return;if((v==='Камп'||v==='Магазин')&&(!o.value||['Камп','Магазин'].includes(o.value))){let direct=v==='Магазин';o.value=direct?'Камп':'Магазин';route_direction.value=direct?'forward':'reverse'}else if(o.value===v)o.value=''}function syncRouteFromDirection(){let o=document.getElementById('origin'),d=document.getElementById('destination');if((!o.value||['Камп','Магазин'].includes(o.value))&&(!d.value||['Камп','Магазин'].includes(d.value))){let direct=route_direction.value==='forward';o.value=direct?'Камп':'Магазин';d.value=direct?'Магазин':'Камп'}}function edit(id){let x=routesData.find(r=>r.id===id);editId=id;updateFormTitle();setRouteStop('origin',x.origin);setRouteStop('destination',x.destination);for(let k of ['name','origin','destination','capacity','comment','service_date'])document.getElementById(k).value=x[k];route_direction.value=x.direction==='reverse'?'reverse':'forward';departure_time.value=x.direction==='reverse'?x.reverse_time:x.forward_time;active.checked=!!x.active;setWeekdays(x.weekday_mask);document.getElementById('name').focus()}function resetForm(){editId=null;updateFormTitle();for(let id of ['origin','destination']){let opt=document.getElementById(id).querySelector('[data-legacy]');if(opt)opt.remove()}for(let k of ['name','origin','destination','departure_time','comment','service_date'])document.getElementById(k).value='';route_direction.value='forward';capacity.value=50;active.checked=true;setWeekdays(127);formErr.innerHTML=''}async function save(){formErr.innerHTML='';let o={};for(let k of ['name','origin','destination','comment','service_date'])o[k]=document.getElementById(k).value;o.direction=route_direction.value;o.forward_time=departure_time.value;o.reverse_time=departure_time.value;o.capacity=Number(capacity.value);o.active=active.checked;o.weekday_mask=Array.from(document.querySelectorAll('.weekday:checked')).reduce((mask,x)=>mask|Number(x.value),0);try{await api('/api/admin/routes'+(editId?'/'+editId:''),{method:editId?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(o)});resetForm();loadRoutes()}catch(x){formErr.innerHTML='<p class="alert">'+e(x.message)+'</p>'}}async function loadWhitelist(){try{let x=await api('/api/admin/whitelist');whiteCards=x.cards;allowed.value=x.cards.join('\\n');searchWhitelist()}catch(x){whiteStatus.innerHTML='<p class="alert">'+e(x.message)+'</p>'}}async function saveWhitelist(){try{let list=allowed.value.split(/[\\s,;]+/).filter(Boolean);let x=await api('/api/admin/whitelist',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({cards:list})});whiteStatus.textContent=T[lang].whiteSaved+x.count;await loadWhitelist()}catch(x){whiteStatus.innerHTML='<p class="alert">'+e(x.message)+'</p>'}}function searchWhitelist(){whiteCount.textContent=T[lang].totalCards+whiteCards.length;let term=whiteSearch.value.trim();let matches=term?whiteCards.filter(x=>x.includes(term)):[];whiteResults.textContent=term?T[lang].found+matches.length+(matches.length?' · '+matches.slice(0,40).join(', ')+(matches.length>40?' …':''):''):''}
function invalidatePreview(){excelImport.disabled=true;excelStats.textContent=''}
async function excelCall(path,params){let r=await fetch(B+path+(params?'?'+new URLSearchParams(params):''),{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:whiteFile});let x=await r.json();if(!r.ok)throw Error(x.detail||'Error');return x}
function excelErrorShow(x){excelError.innerHTML='<p class="alert">'+e(x.message)+'</p>'}
async function inspectExcel(){whiteFile=excelFile.files[0];excelChoices.style.display='none';excelError.innerHTML='';invalidatePreview();if(!whiteFile)return;try{let x=await excelCall('/api/admin/whitelist/excel/inspect');whiteSheets=x.sheets;excelSheet.innerHTML=whiteSheets.map(z=>`<option value="${z.index}">${e(z.name)}</option>`).join('');if(!whiteSheets.length)throw Error('Нет листов / Sayfa yok');excelChoices.style.display='block';selectExcelSheet()}catch(x){excelErrorShow(x)}}
function selectExcelSheet(){let sheet=whiteSheets.find(z=>z.index===Number(excelSheet.value));excelColumn.innerHTML=(sheet?.columns||[]).map(z=>`<option value="${z.index}">${e(z.letter)} · ${e(z.samples.join(' / '))}</option>`).join('');selectExcelColumn()}
function selectExcelColumn(){let sheet=whiteSheets.find(z=>z.index===Number(excelSheet.value));let col=sheet?.columns.find(z=>z.index===Number(excelColumn.value));excelFirstRow.value=col?.first_is_header?2:1;invalidatePreview()}
function excelParams(){return {sheet:excelSheet.value,column:excelColumn.value,first_row:excelFirstRow.value}}
async function previewExcel(){excelError.innerHTML='';invalidatePreview();let selectedFile=whiteFile,selectedParams=excelParams(),signature=JSON.stringify(selectedParams);try{let x=await excelCall('/api/admin/whitelist/excel/preview',selectedParams);if(whiteFile!==selectedFile||JSON.stringify(excelParams())!==signature)return;excelStats.innerHTML=`<p>${T[lang].previewValid}${x.valid_rows}<br>${T[lang].previewUnique}${x.unique}<br>${T[lang].previewDuplicate}${x.duplicates}<br>${T[lang].previewExisting}${x.already_present}<br>${T[lang].previewNew}${x.new}<br>${T[lang].previewInvalid}${x.invalid}${x.invalid?'<br>'+T[lang].previewRows+e(x.invalid_rows.join(', ')):''}</p>`;excelImport.disabled=x.unique===0}catch(x){excelErrorShow(x)}}
async function importExcel(){if(excelImport.disabled)return;if(excelMode.value==='replace'&&!confirm(T[lang].replaceConfirm))return;excelImport.disabled=true;excelError.innerHTML='';try{let x=await excelCall('/api/admin/whitelist/excel/import',{...excelParams(),mode:excelMode.value});excelStats.textContent=T[lang].imported+x.total;await loadWhitelist()}catch(x){excelErrorShow(x)}}
async function loadQr(offset=0){qrOffset=Math.max(0,offset);let p=new URLSearchParams({q:qrSearchInput.value.trim(),trip_date:qrDate.value,trip_time:qrTime.value,status:qrStatus.value,offset:String(qrOffset)});try{let result=await api('/api/admin/qrcodes?'+p);qrTotal=result.total;qrCount.textContent=T[lang].found+qrTotal;let rows=result.items.map(x=>{let d=x.decoded;let code=e(d.booking);let trips=d.legs.map((l,i)=>`<div>${e(l.date)} ${e(l.time)} · ${e(x.routes[i]?.origin||'')} → ${e(x.routes[i]?.destination||'')}<br>${T[lang].qrPosition}: ${l.number} / ${l.capacity}${x.routes[i]?.cancelled?' · <span class="status inactive">'+T[lang].cancelledQr+'</span>':''}${x.routes[i]?.can_cancel?`<br><button class="secondary" onclick="cancelAdminLeg('${code}',${x.routes[i].id})">${lang==='tr'?'Seferi iptal et':'Отменить рейс'}</button>`:''}</div>`).join('');let pic=x.active?`<details><summary>${T[lang].showQr}</summary><img class="qr-small" loading="lazy" src="${B}/api/admin/qrcodes/${encodeURIComponent(d.booking)}/image" alt="QR"></details>`:'';let action=x.can_cancel?`<button class="cancel-booking" onclick="cancelAdmin('${code}')">${T[lang].cancelBooking}</button>`:'';let state=x.cancelled?T[lang].cancelledQr:(x.active?T[lang].validQr:T[lang].expiredQr);return `<tr><td><b>${code}</b><br>${T[lang].qrCard}: ${e(d.card)}<br><span class="status ${x.active?'':'inactive'}">${state}</span>${pic}${action}</td><td>${trips}<div><b>${T[lang].fortune}:</b> ${e(lang==='tr'&&x.fortune_tr?x.fortune_tr:d.fortune)}</div></td></tr>`}).join('');qrResults.innerHTML=`<table class="qr-table"><thead><tr><th>${T[lang].qrApplication}</th><th>${T[lang].qrDecoded}</th></tr></thead><tbody>${rows}</tbody></table>`;qrPrev.disabled=qrOffset===0;qrNext.disabled=qrOffset+result.limit>=qrTotal}catch(x){qrResults.innerHTML='<p class="alert">'+e(x.message)+'</p>'}}
async function cancelAdminLeg(code,id){if(!confirm(lang==='tr'?'Yalnızca bu sefer iptal edilsin mi?':'Отменить только этот рейс?'))return;qrActionError.innerHTML='';try{await api('/api/admin/bookings/'+encodeURIComponent(code)+'/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:true,leg_id:id})});await loadQr(qrOffset);await historyLoad()}catch(x){qrActionError.innerHTML='<p class="alert">'+e(x.message)+'</p>'}}async function cancelAdmin(code){if(!confirm(T[lang].cancelBookingConfirm))return;qrActionError.innerHTML='';try{await api('/api/admin/bookings/'+encodeURIComponent(code)+'/cancel',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm:true})});await loadQr(qrOffset);await historyLoad()}catch(x){qrActionError.innerHTML='<p class="alert">'+e(x.message)+'</p>';await loadQr(qrOffset)}}
async function historyLoad(){let p=new URLSearchParams();for(let k of ['date_from','date_to','route_id','time_from','time_to'])p.set(k,document.getElementById(k).value);p.set('card',filter_card.value);p.set('origin',filter_origin.value);p.set('status',histStatus.value);let a=await api('/api/admin/history?'+p);hist.innerHTML='<table><thead><tr><th>Дата / Tarih</th><th>Время / Saat</th><th>Рейс / Sefer</th><th>Маршрут / Güzergâh</th><th>Карта / Kart</th><th>№</th><th>Бронь / Rez.</th><th>Водитель / Sürücü</th><th>'+T[lang].historyStatus+'</th></tr></thead><tbody>'+a.map(x=>`<tr><td>${e(x.date)}</td><td>${e(x.departure)}</td><td>${e(x.route_name)}</td><td>${e(x.origin)} → ${e(x.destination)}</td><td>${e(x.card)}</td><td>${x.sequence}/${x.capacity}</td><td>${e(x.code)}</td><td>${e(x.driver_login||'')}</td><td><span class="status ${x.cancelled?'inactive':''}">${x.cancelled?T[lang].cancelledQr:x.boarded_at?(lang==='tr'?'Bindi':'Посажен'):T[lang].bookedHistory}</span></td></tr>`).join('')+'</tbody></table>'}async function verify(){try{let x=await api('/api/admin/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({token:qrtext.value})});result.innerHTML='<div class="card"><b>✓ '+e(x.code)+'</b><p>'+e(x.card)+'</p>'+x.legs.map(a=>'<p>'+e(a.date)+' '+e(a.departure)+' · '+e(a.origin)+' → '+e(a.destination)+' · '+a.sequence+'/'+a.capacity+'</p>').join('')+'<p><b>'+T[lang].fortune+':</b> '+e(lang==='tr'?x.fortune_tr:x.fortune)+'</p></div>'}catch(x){result.innerHTML='<p class="alert">'+e(x.message)+'</p>'}}async function scan(){try{if(!('BarcodeDetector'in window))throw Error(T[lang].noScan);let detector=new BarcodeDetector({formats:['qr_code']});stream=await navigator.mediaDevices.getUserMedia({video:{facingMode:'environment'}});video.srcObject=stream;await video.play();async function loop(){if(!stream)return;try{let a=await detector.detect(video);if(a.length){qrtext.value=a[0].rawValue;stopScan();verify();return}}catch(e){}requestAnimationFrame(loop)}loop()}catch(x){result.innerHTML='<p class="alert">'+e(x.message)+'</p>'}}function stopScan(){if(stream){stream.getTracks().forEach(x=>x.stop());stream=null;video.srcObject=null}}renderLanguage();loadStops();loadRoutes();loadWhitelist();historyLoad();</script></body></html>'''

DISPATCH_BOOKING_FORM='<div class="box" id="abBox"><h2 data-ab="title"></h2><form id="abForm" autocomplete="off"><div class="compact-form"><div><label for="abDate" data-ab="date"></label><input id="abDate" type="date" required></div><div><label for="abMode" data-ab="mode"></label><select id="abMode"><option value="forward" data-ab="forward"></option><option value="reverse" data-ab="reverse"></option><option value="both" data-ab="both"></option></select></div><div class="full"><label for="abTrip" data-ab="trip"></label><select id="abTrip" required></select></div><div id="abReturnDateBox" hidden><label for="abReturnDate" data-ab="returnDate"></label><input id="abReturnDate" type="date"></div><div id="abReturnTripBox" hidden><label for="abReturnTrip" data-ab="returnTrip"></label><select id="abReturnTrip"></select></div><div class="full"><label for="abCard" data-ab="card"></label><input id="abCard" type="text" inputmode="numeric" pattern="[0-9]{4,12}" minlength="4" maxlength="12" required></div><div class="full"><button id="abSubmit" type="submit" data-ab="submit"></button></div></div></form><div id="abStatus" role="status" aria-live="polite"></div><div id="abResult" hidden><div class="success"><strong data-ab="success"></strong></div><p><a id="abTicket" target="_blank" rel="noopener noreferrer" data-ab="openTicket"></a></p><img id="abQr" class="qr-small" alt="QR"><div class="row"><a id="abPdf" target="_blank" rel="noopener noreferrer">PDF</a><a id="abJpg" target="_blank" rel="noopener noreferrer">JPG</a></div><p class="muted small" data-ab="ticketNote"></p></div></div>'

# Dispatcher booking uses the same transactional rules as passenger booking.
ADMIN=ADMIN.replace('<section id="qrPanel" style="display:none">','<section id="qrPanel" style="display:none">'+DISPATCH_BOOKING_FORM,1)
ADMIN=ADMIN.replace('</script></body></html>','</script><script src="/bus/admin-booking.js"></script></body></html>',1)
@app.get(BASE+'/admin-booking.js',dependencies=[Depends(admin)])
def admin_booking_javascript():
 return Response((Path(__file__).resolve().parent/'static'/'admin-booking.js').read_bytes(),media_type='text/javascript')
@app.post(BASE+'/api/admin/book',dependencies=[Depends(admin)])
def admin_book(b:Booking):
 return book(b)

# Operational counts use a read transaction; booking mutations remain unchanged.
@app.get(BASE+'/api/admin/occupancy',dependencies=[Depends(admin)])
def get_admin_occupancy(date:str=''):
 with db() as c:
  c.execute('PRAGMA query_only=ON');c.execute('BEGIN')
  try:return occupancy_report(c,now(),booking_horizon_hours(c),date)
  except ValueError as error:raise HTTPException(400,str(error)) from None

@app.get(BASE+'/admin-occupancy.js',dependencies=[Depends(admin)])
def admin_occupancy_javascript():
 return Response((Path(__file__).resolve().parent/'static'/'admin-occupancy.js').read_bytes(),media_type='text/javascript')

@app.get(BASE+'/admin-occupancy.css',dependencies=[Depends(admin)])
def admin_occupancy_stylesheet():
 return Response((Path(__file__).resolve().parent/'static'/'admin-occupancy.css').read_bytes(),media_type='text/css')

OCCUPANCY_PANEL="""<section id="occupancyPanel" style="display:none" aria-label="Заполняемость"><div class="box"><div class="occupancy-toolbar"><label><span data-occupancy-i="date">Дата рейсов</span><select id="occupancyDate"></select></label><button type="button" id="occupancyRefresh" data-occupancy-i="refresh">Обновить</button><span id="occupancyUpdated" class="muted" role="status" aria-live="polite"></span></div><p id="occupancyError" class="alert" role="alert" hidden></p><div id="occupancySummary"></div><div id="occupancyTrips" class="schedule-columns"></div></div></section>"""
ADMIN=ADMIN.replace('<button id="whiteTab"','<button id="occupancyTab" class="secondary" onclick="showTab(&quot;occupancy&quot;)">Заполняемость</button><button id="whiteTab"',1)
ADMIN=ADMIN.replace('<section id="whitePanel"',OCCUPANCY_PANEL+'<section id="whitePanel"',1)
ADMIN=ADMIN.replace('function showTab(which){','function showTab(which){window.BusOccupancy?.show(which);',1)
ADMIN=ADMIN.replace('function renderLanguage(){','function renderLanguage(){window.BusOccupancy?.renderLanguage();',1)
ADMIN=ADMIN.replace('</head>','<link rel="stylesheet" href="/bus/admin-occupancy.css"></head>',1)
ADMIN=ADMIN.replace('</body></html>','<script src="/bus/admin-occupancy.js"></script></body></html>',1)

# An inline handler's bare "lang" resolves to HTMLElement.lang, not the admin state.
ADMIN=ADMIN.replace("onclick=\"lang=lang==='ru'?'tr':'ru';renderLanguage()\"",'onclick="toggleAdminLanguage()"',1)
ADMIN=ADMIN.replace('function renderLanguage(){',"function toggleAdminLanguage(){lang=lang==='ru'?'tr':'ru';renderLanguage()}function renderLanguage(){",1)


@app.get(BASE+'/api/admin/scud/status',dependencies=[Depends(admin)])
def get_scud_status():
 with db() as connection:return scud_status(connection)

def import_scud_bytes(content):
 parsed=parse_scud(content)
 with db() as connection:return save_scud(connection,parsed,now())

@app.post(BASE+'/api/admin/scud/import',dependencies=[Depends(admin)])
async def import_scud(request:Request):
 content=bytearray()
 async for chunk in request.stream():
  if len(content)+len(chunk)>SCUD_MAX_BYTES:
   raise HTTPException(413,'Файл XLSX: не более 6 МБ / XLSX en fazla 6 MB')
  content.extend(chunk)
 try:return await run_in_threadpool(import_scud_bytes,bytes(content))
 except ScudError as error:raise HTTPException(400,str(error)) from None
 finally:content.clear()

@app.get(BASE+'/admin-scud.js',dependencies=[Depends(admin)])
def admin_scud_javascript():
 return Response((Path(__file__).resolve().parent/'static'/'admin-scud.js').read_bytes(),media_type='text/javascript')

@app.get(BASE+'/admin-scud.css',dependencies=[Depends(admin)])
def admin_scud_stylesheet():
 return Response((Path(__file__).resolve().parent/'static'/'admin-scud.css').read_bytes(),media_type='text/css')

SCUD_PANEL="""<section id="scudBox" class="box"><h2 data-scud-i="title">Импорт отметок СКУД</h2><p class="muted small" data-scud-i="privacy">Сохраняются только автобус, карта и время. ФИО, кимлик, паспорт и исходный файл не сохраняются.</p><p class="small" data-scud-i="rule">Привязка к рейсу: 30 минут до отправления и 5 минут после, по времени Турции. Повторные отметки одной карты на рейсе считаются один раз.</p><label for="scudFiles" data-scud-i="files">Выгрузки СКУД (XLSX)</label><input type="file" id="scudFiles" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" multiple><div class="row scud-actions"><button type="button" id="scudImport" data-scud-i="upload">Загрузить СКУД</button><button type="button" id="scudRefresh" class="secondary" data-scud-i="refresh">Обновить</button></div><p id="scudResult" role="status" aria-live="polite"></p><p id="scudError" class="alert" role="alert" hidden></p><p id="scudTotal" class="muted small"></p><div id="scudHistory" class="scud-history"></div></section>"""
ADMIN=ADMIN.replace('<section id="whitePanel" style="display:none">','<section id="whitePanel" style="display:none">'+SCUD_PANEL,1)
ADMIN=ADMIN.replace('function showTab(which){','function showTab(which){window.BusScud?.show(which);',1)
ADMIN=ADMIN.replace('function renderLanguage(){','function renderLanguage(){window.BusScud?.renderLanguage();',1)
ADMIN=ADMIN.replace('</head>','<link rel="stylesheet" href="/bus/admin-scud.css"></head>',1)
ADMIN=ADMIN.replace('</body></html>','<script src="/bus/admin-scud.js"></script></body></html>',1)
