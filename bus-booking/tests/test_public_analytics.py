"""Isolated checks: never import booking startup, live data, credentials or network Wialon."""
import copy,hashlib,io,json,os,socket,sqlite3,subprocess,sys,tempfile,time,unittest,urllib.error,urllib.request
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from openpyxl import load_workbook
from public_analytics.data import TZ,attach_departures,build_report,connect,load_trips,validate_period
from public_analytics.wialon import detect_exits,select_zones
from public_analytics.app import excel
NOW=datetime(2026,11,17,12,tzinfo=TZ)
def stamp(day,hour,minute=0):return int(datetime(2026,11,day,hour,minute,tzinfo=TZ).timestamp())
def fixture(path):
 c=sqlite3.connect(path)
 c.executescript('''CREATE TABLE routes(id INTEGER PRIMARY KEY,name TEXT,origin TEXT,destination TEXT,
 forward_time TEXT,reverse_time TEXT,capacity INTEGER,active INTEGER,direction TEXT,service_date TEXT,
 weekday_mask INTEGER,comment TEXT,deleted_at TEXT);
 CREATE TABLE route_service_periods(route_id INTEGER PRIMARY KEY,starts_on TEXT,ends_on TEXT);
 CREATE TABLE bookings(id INTEGER PRIMARY KEY,card TEXT,code TEXT,token TEXT,cancelled_at TEXT);
 CREATE TABLE legs(id INTEGER PRIMARY KEY,booking_id INTEGER,route_id INTEGER,direction TEXT,date TEXT,
 departure TEXT,origin TEXT,destination TEXT,route_name TEXT,capacity INTEGER,comment TEXT);
 CREATE TABLE boardings(leg_id INTEGER PRIMARY KEY,driver_login TEXT);
 CREATE TABLE driver_accounts(login TEXT);
 CREATE TABLE cancelled_legs(leg_id INTEGER,card TEXT);''')
 rows=[(1,'Bus 1','Камп','Шок','09:00','10:00',10,1,'both','',127,'33 KY 555',None),
 (2,'Bus 2','Шок','Камп','11:00','11:00',5,1,'reverse','',127,'70 ACB 112',None),
 (3,'Old bus','Камп','Шок','11:00','11:00',8,0,'forward','',127,'','2026-11-17'),
 (4,'Sunday','Камп','Шок','12:00','12:00',4,1,'forward','',64,'',None),
 (5,'=DANGER()','Камп','Шок','18:00','18:00',4,1,'forward','2026-11-17',127,'',None)]
 c.executemany('INSERT INTO routes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',rows)
 c.executemany('INSERT INTO route_service_periods VALUES(?,?,?)',[(i,'2026-11-16','2026-11-20') for i in range(1,6)])
 c.executemany('INSERT INTO bookings VALUES(?,?,?,?,?)',[(1,'private-card-123','private-code','private-token',None),(2,'private-card-456','private-code-2','private-token-2',None),(3,'cancelled-card','cancelled-code','cancelled-token','2026-11-16'),(4,'historic-card','historic-code','historic-token',None)])
 for lid,bid,rid,direction,dep,cap in [(1,1,1,'forward','09:00',10),(2,1,1,'reverse','10:00',10),(3,2,2,'reverse','11:00',5),(4,3,1,'forward','09:00',10),(5,4,3,'forward','11:00',8)]:
  c.execute('INSERT INTO legs VALUES(?,?,?,?,?,?,?,?,?,?,?)',(lid,bid,rid,direction,'2026-11-16',dep,'Камп','Шок','Historical' if rid==3 else 'Bus '+str(rid),cap,''))
 c.execute("INSERT INTO boardings VALUES(1,'33ky555')")
 c.executemany('INSERT INTO driver_accounts VALUES(?)',[('33ky555',),('70acb112',),('operator',)])
 c.execute("INSERT INTO cancelled_legs VALUES(90,'cancelled-card')");c.commit();c.close()
def events():return [dict(date='2026-11-16',direction=d,plate=p,stamp=t,arrived_at=t+600) for d,p,t in [
 ('forward','33KY555',stamp(16,9,5)),('reverse','33KY555',stamp(16,10)),('reverse','70ACB112',stamp(16,10,57))]]
class AnalyticsTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.db=Path(self.tmp.name)/'booking.db';fixture(self.db)
 def tearDown(self):self.tmp.cleanup()
 def result(self):return build_report(self.db,'2026-11-16','2026-11-17','all',events(),{'2026-11-16':'ready','2026-11-17':'ready'},NOW)
 def test_counts_snapshots_cancellations_privacy_and_read_only(self):
  before=hashlib.sha256(self.db.read_bytes()).hexdigest();r=self.result();t=r['totals']
  self.assertEqual([t[k] for k in ['trips','bookings','confirmed','unconfirmed','capacity','free']],[8,4,1,3,62,58])
  self.assertEqual([t[k] for k in ['early','late','on_time','planned','unknown']],[1,1,1,1,4])
  self.assertTrue(any(x['name']=='Historical' for x in r['trips']))
  for secret in ['private-card','private-token','private-code','cancelled-card','driver_logins','_hints']:self.assertNotIn(secret,json.dumps(r))
  self.assertEqual(before,hashlib.sha256(self.db.read_bytes()).hexdigest())
  with connect(self.db) as c:
   with self.assertRaises(sqlite3.OperationalError):c.execute('DELETE FROM legs')
 def test_direction_filter_keeps_matching(self):
  r=build_report(self.db,'2026-11-16','2026-11-17','reverse',events(),{'2026-11-16':'ready','2026-11-17':'ready'},NOW)
  self.assertEqual(r['totals']['trips'],4);self.assertEqual(r['totals']['bookings'],2)
  self.assertEqual([x['status'] for x in r['trips']],[x['status'] for x in self.result()['trips'] if x['direction']=='reverse'])
 def test_invalid_period_empty_future_and_weekdays(self):
  for a,b in [('bad','2026-11-16'),('2026-11-17','2026-11-16'),('2026-10-01','2026-11-17')]:
   with self.assertRaises(ValueError):validate_period(a,b,NOW)
  self.assertEqual(load_trips(self.db,'2026-11-01','2026-11-02',NOW)[0],[])
  self.assertFalse(any(r['name']=='Sunday' for r in self.result()['trips']))
  r=build_report(self.db,'2026-11-18','2026-11-18','all',events(),{},NOW)
  self.assertEqual(r['totals']['planned'],r['totals']['trips']);self.assertEqual(r['totals']['unknown'],0)
 def test_partial_wialon_does_not_claim_punctuality(self):
  r=build_report(self.db,'2026-11-16','2026-11-17','all',events(),{'2026-11-16':'loading'},NOW)
  self.assertEqual(r['totals']['late'],0);self.assertEqual(r['totals']['on_time'],0)
 def test_parallel_ambiguity_and_deduplication(self):
  rows,_=load_trips(self.db,'2026-11-16','2026-11-16',NOW);a=copy.deepcopy(rows[0]);a['_hints']=[]
  b=copy.deepcopy(a);b['key']='second';b['route_id']=99;ev=events()[0]
  attach_departures([a,b],[ev,copy.deepcopy(ev)],{'2026-11-16':'ready'},NOW)
  self.assertEqual([a['reason'],b['reason']],['ambiguous','ambiguous'])
  attach_departures([a],[ev,copy.deepcopy(ev)],{'2026-11-16':'ready'},NOW);self.assertEqual(a['status'],'late')
  ev['arrived_at']=None;attach_departures([a],[ev],{'2026-11-16':'ready'},NOW);self.assertEqual(a['status'],'late')
 def test_renamed_bookings_are_not_dropped(self):
  with sqlite3.connect(self.db) as c:
   c.execute("INSERT INTO legs VALUES(6,2,1,'forward','2026-11-16','09:00','Changed origin','Шок','Changed name',10,'')")
  r=self.result();self.assertEqual(r['totals']['bookings'],5)
 def test_xlsx_types_totals_formula_safety_and_no_passengers(self):
  r=self.result();r['wialon']={'last_updated':NOW.isoformat()}
  book=load_workbook(io.BytesIO(excel(r)),data_only=False)
  self.assertEqual(book.sheetnames,['Сводка','По датам','Рейсы','Правила'])
  self.assertEqual([book['Сводка'].cell(8,c).value for c in range(2,5)],[8,4,1])
  self.assertIsInstance(book['Рейсы']['A2'].value,datetime);self.assertTrue(book['Рейсы'].auto_filter.ref)
  vals=[cell.value for sheet in book for row in sheet for cell in row if cell.value is not None]
  self.assertIn("'=DANGER()",vals)
  self.assertFalse(any(cell.data_type=='f' for sheet in book for row in sheet for cell in row))
  self.assertNotIn('private-card',str(vals))
class FakeZone:
 def __init__(self,role,label,bounds,points,radius=0):self.bounds=bounds
 def contains(self,y,x):return self.bounds[0]<=y<=self.bounds[1] and self.bounds[2]<=x<=self.bounds[3]
class ExitTests(unittest.TestCase):
 def zones(self):return {'forward':FakeZone('','',(0,1,0,1),()),'reverse':FakeZone('','',(5,6,5,6),())}
 def test_confirmed_exit_destination_gaps_initial_outside_and_jitter(self):
  s=stamp(16,9);points=[(s,(.5,.5)),(s+30,(.5,.5)),(s+60,(2,2)),(s+90,(2,2)),(s+120,(5.5,5.5))]
  r=detect_exits(points,self.zones(),'2026-11-16','33KY555')
  self.assertEqual(len(r),1);self.assertEqual(r[0]['stamp'],s+60);self.assertEqual(r[0]['arrived_at'],s+120)
  self.assertEqual(detect_exits(points[2:],self.zones(),'2026-11-16','33KY555'),[])
  self.assertEqual(detect_exits([points[0],points[1],(s+400,(2,2)),(s+430,(2,2))],self.zones(),'2026-11-16','33KY555'),[])
  self.assertEqual(detect_exits([points[0],points[1],points[2],(s+80,(.5,.5))],self.zones(),'2026-11-16','33KY555'),[])
 def test_exact_existing_geofence_ids_and_shared_geometry(self):
  adapter=SimpleNamespace(Zone=FakeZone,valid_coordinates=lambda y,x:isinstance(y,(int,float)) and isinstance(x,(int,float)))
  raw=[(600894911,{'id':30,'n':'Camp','t':3,'p':[{'x':33,'y':36,'r':100}]}),(601276657,{'id':12,'n':'Shop Buyukeceli','t':2,'p':[{'x':33,'y':36},{'x':34,'y':36},{'x':34,'y':37}]}),(600894911,{'id':39,'n':'Wrong Tasucu shop','t':2,'p':[]})]
  self.assertEqual(set(select_zones(adapter,raw)),{'forward','reverse'})
  with self.assertRaises(ValueError):select_zones(adapter,raw[:1])
class HttpTests(unittest.TestCase):
 def test_public_page_api_xlsx_and_read_only_methods(self):
  with tempfile.TemporaryDirectory() as tmp:
   db=Path(tmp)/'booking.db';fixture(db)
   with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
   env={**os.environ,'BUS_ANALYTICS_DB':str(db),'BUS_ANALYTICS_CACHE':str(Path(tmp)/'cache.sqlite'),'BUS_ANALYTICS_COLLECTOR':'0'}
   with open(Path(tmp)/'server.log','w') as log:
    proc=subprocess.Popen([sys.executable,'-m','uvicorn','public_analytics.app:app','--host','127.0.0.1','--port',str(port),'--no-access-log'],cwd=Path(__file__).resolve().parents[1],env=env,stdout=log,stderr=log)
    try:
     base=f'http://127.0.0.1:{port}/analytics'
     for _ in range(80):
      try:urllib.request.urlopen(base+'/api/health',timeout=.5);break
      except (OSError,urllib.error.URLError):time.sleep(.1)
     html=urllib.request.urlopen(base+'/').read().decode();self.assertIn('Выгрузить в Excel',html);self.assertNotIn('демонстрационные данные',html)
     response=urllib.request.urlopen(base+'/api/report?start=2026-11-16&end=2026-11-17');r=json.load(response)
     self.assertEqual(r['totals']['bookings'],4);self.assertNotIn('private-card',json.dumps(r))
     self.assertEqual(response.headers['X-Robots-Tag'],'noindex, nofollow')
     x=urllib.request.urlopen(base+'/api/export.xlsx?start=2026-11-16&end=2026-11-17');self.assertIn('attachment',x.headers['Content-Disposition']);self.assertTrue(x.read().startswith(b'PK'))
     for suffix in ['/api/report?start=invalid','/api/report?direction=bad']:
      with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(base+suffix)
      self.assertEqual(error.exception.code,400)
     with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(urllib.request.Request(base+'/api/report',data=b'{}',method='POST'))
     self.assertEqual(error.exception.code,405)
    finally:proc.terminate();proc.wait(timeout=10)
if __name__=='__main__':unittest.main(verbosity=2)
