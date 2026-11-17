"""Individual-driver acceptance against temporary SQLite and loopback HTTP."""
import concurrent.futures,hashlib,os,socket,sqlite3,subprocess,sys,tempfile,time
from pathlib import Path
import requests
from cryptography.fernet import Fernet
ROOT=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='driver-acceptance-') as temp:
 db=Path(temp)/'drivers.sqlite'
 with socket.socket() as s:s.bind(('127.0.0.1',0));port=s.getsockname()[1]
 base=f'http://127.0.0.1:{port}/bus'
 env=dict(os.environ,BUS_DB=str(db),BUS_ADMIN_PASSWORD='synthetic-admin',BUS_FERNET_KEY=Fernet.generate_key().decode(),BUS_PUBLIC_URL=base[:-4])
 auth=('admin','synthetic-admin')
 def start():
  p=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(port),'--no-access-log'],cwd=ROOT,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  for _ in range(100):
   try:
    if requests.get(base+'/',timeout=1).ok:return p
   except requests.RequestException:pass
   time.sleep(.1)
  raise AssertionError('Isolated server not ready')
 def stop(p):
  p.terminate()
  try:p.wait(timeout=5)
  except subprocess.TimeoutExpired:p.kill();p.wait()
 def get(path,**kw):return requests.get(base+path,timeout=10,**kw)
 def post(path,payload=None,**kw):return requests.post(base+path,json=payload,timeout=20,**kw)
 def put(i,payload):return requests.put(base+'/api/admin/drivers/'+str(i),json=payload,auth=auth,timeout=10)
 def delete(i):return requests.delete(base+'/api/admin/drivers/'+str(i),auth=auth,timeout=10)
 password='Synthetic_123456'
 p=start()
 try:
  path='/api/admin/drivers'
  for method,url in [('get',path),('post',path),('put',path+'/1'),('delete',path+'/1')]:
   assert requests.request(method,base+url,json={'login':'unauth','display_name':'','password':password},timeout=5).status_code==401
  for login in ('ab','bad login','Кирилл','K'*41,'Kkk'):
   assert post(path,{'login':login,'display_name':'','password':password},auth=auth).status_code==422,login
  def create(i):return post(path,{'login':f'race{i:02}','display_name':f'BUS {i}','password':password},auth=auth).status_code
  with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:results=list(pool.map(create,range(16)))
  assert results.count(201)==10 and results.count(409)==6,results
  items=get(path,auth=auth).json()['items'];assert len(items)==10
  keeper=items[0]
  for item in items[1:]:assert delete(item['id']).ok
  assert post(path,{'login':' '+keeper['login'].upper()+' ','display_name':'duplicate','password':password},auth=auth).status_code==409
  assert post(path,{'login':'shortpass','display_name':'','password':'1234567'},auth=auth).status_code==422
  d=requests.Session();r=d.post(base+'/api/scanner/login',json={'login':keeper['login'].upper(),'password':password},timeout=10);assert r.ok,r.status_code
  cookie=r.headers['Set-Cookie'];assert 'HttpOnly' in cookie and 'SameSite=strict' in cookie and 'Path=/bus/api/scanner' in cookie
  token=d.cookies.get('bus_driver_session')
  with sqlite3.connect(db) as c:
   row=c.execute('SELECT password_salt,password_hash,password_iterations FROM driver_accounts WHERE id=?',(keeper['id'],)).fetchone()
   assert row[1]!=password and row[2]>=310000
   assert hashlib.pbkdf2_hmac('sha256',password.encode(),bytes.fromhex(row[0]),row[2]).hex()==row[1]
   assert c.execute('SELECT token_hash FROM driver_sessions').fetchone()[0]==hashlib.sha256(token.encode()).hexdigest()
   assert token not in ' '.join(c.iterdump())
  assert d.get(base+'/api/scanner/session',timeout=5).json()['id']==keeper['id']
  stop(p);p=start()
  assert d.get(base+'/api/scanner/session',timeout=5).ok,'Session must survive process restart'
  assert put(keeper['id'],{'display_name':'Renamed','password':''}).ok
  assert d.get(base+'/api/scanner/session',timeout=5).ok,'Blank password must preserve session'
  assert put(keeper['id'],{'password':'Reset_12345678'}).ok
  assert d.get(base+'/api/scanner/session',timeout=5).status_code==401
  assert d.post(base+'/api/scanner/login',json={'login':keeper['login'],'password':'Reset_12345678'},timeout=10).ok
  assert put(keeper['id'],{'active':False}).ok
  assert d.get(base+'/api/scanner/session',timeout=5).status_code==401
  assert put(keeper['id'],{'active':True}).ok
  assert d.post(base+'/api/scanner/login',json={'login':keeper['login'],'password':'Reset_12345678'},timeout=10).ok
  assert put(keeper['id'],{'login':'renamed_driver'}).ok
  assert d.get(base+'/api/scanner/session',timeout=5).status_code==401
  assert d.post(base+'/api/scanner/login',json={'login':'renamed_driver','password':'Reset_12345678'},timeout=10).ok
  with sqlite3.connect(db) as c:c.execute('UPDATE driver_sessions SET expires_at=0');c.commit()
  assert d.get(base+'/api/scanner/session',timeout=5).status_code==401
  assert d.post(base+'/api/scanner/login',json={'login':'renamed_driver','password':'Reset_12345678'},timeout=10).ok
  assert d.post(base+'/api/scanner/logout',timeout=5).ok
  assert d.get(base+'/api/scanner/session',timeout=5).status_code==401
  assert d.post(base+'/api/scanner/login',json={'login':'renamed_driver','password':'Reset_12345678'},timeout=10).ok
  assert delete(keeper['id']).ok
  assert d.get(base+'/api/scanner/session',timeout=5).status_code==401
  assert 'scanner_access_code' not in get('/api/admin/settings',auth=auth).json()
  assert post('/api/scanner/unlock',{'code':'OLDKEY'}).status_code==410
  assert post('/api/scanner/verify',{'token':'anything','record':True},headers={'X-Scanner-Code':'OLDKEY'}).status_code==401
  for _ in range(5):assert post('/api/scanner/login',{'login':'absent','password':'wrong'}).status_code==401
  assert post('/api/scanner/login',{'login':'absent','password':'wrong'}).status_code==429
  print('PASS: admin authorization, concurrent 10-account limit, validation, hashing, cookie sessions, restart persistence, reset/disable/rename/expiry/logout/delete revocation, legacy-key rejection, login throttle')
 finally:stop(p)
