"""Operator acceptance: temporary DB and loopback service only, never production."""
import json, os, socket, sqlite3, subprocess, sys, tempfile, time
from pathlib import Path
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import requests
from cryptography.fernet import Fernet
source=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
script=Path(__file__).with_name('browser_acceptance.js')
with tempfile.TemporaryDirectory(prefix='bus-driver-browser-') as temp:
 database=Path(temp)/'acceptance.sqlite'
 with socket.socket() as sock:
  sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 origin=f'http://127.0.0.1:{port}'
 env=dict(os.environ,BUS_DB=str(database),BUS_ADMIN_PASSWORD='synthetic-admin-only',BUS_FERNET_KEY=Fernet.generate_key().decode(),BUS_PUBLIC_URL=origin,BUS_INITIAL_CARDS='999001')
 process=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(port),'--no-access-log'],cwd=source,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
 try:
  for _ in range(100):
   try:
    if requests.get(origin+'/bus/',timeout=1).status_code==200:break
   except requests.RequestException:pass
   time.sleep(.1)
  else:raise RuntimeError('Acceptance service failed to start')
  dep=datetime.now(ZoneInfo('Europe/Istanbul'))+timedelta(minutes=10)
  auth=('admin','synthetic-admin-only')
  r=requests.post(origin+'/bus/api/admin/routes',auth=auth,json=dict(name='Synthetic browser route',origin='Камп',destination='Магазин',direction='forward',forward_time=dep.strftime('%H:%M:%S'),reverse_time=dep.strftime('%H:%M:%S'),capacity=41,service_date=dep.date().isoformat()),timeout=5);r.raise_for_status()
  routes=requests.get(origin+'/bus/api/admin/routes',auth=auth,timeout=5).json()
  r=requests.post(origin+'/bus/api/book',json=dict(card='999001',date=dep.date().isoformat(),forward=routes[0]['id'],direction='forward'),timeout=5);r.raise_for_status()
  subprocess.run(['node',str(script),origin],check=True,timeout=120)
  with sqlite3.connect(database) as c:
   rows=c.execute('SELECT card,driver_login,source FROM boardings').fetchall()
   assert rows==[('999001','e2edriver','manual')],rows
  history=requests.get(origin+'/bus/api/admin/history',auth=auth,timeout=5).json()
  assert history[0]['driver_login']=='e2edriver'
  driver=requests.get(origin+'/bus/api/admin/drivers',auth=auth,timeout=5).json()['items'][0]
  assert requests.delete(origin+'/bus/api/admin/drivers/'+str(driver['id']),auth=auth,timeout=5).ok
  assert requests.get(origin+'/bus/api/admin/history',auth=auth,timeout=5).json()[0]['driver_login']=='e2edriver'
  process.terminate();process.wait(timeout=5)
  with sqlite3.connect(database) as c:
   c.execute('ALTER TABLE boardings DROP COLUMN driver_id');c.execute('ALTER TABLE boardings DROP COLUMN driver_login');c.commit()
  subprocess.run([sys.executable,'-c','import app; app.init(); app.init()'],cwd=source,env=env,check=True,timeout=20)
  with sqlite3.connect(database) as c:
   assert c.execute('SELECT card,driver_id,driver_login FROM boardings').fetchall()==[('999001',None,None)]
  print('PASS: real Chrome workflow, boarding history after account deletion, mobile RU/TR and idempotent legacy-boarding migration')
 finally:
  process.terminate()
  try:process.wait(timeout=5)
  except subprocess.TimeoutExpired:process.kill();process.wait()
