"""Simplified Chinese acceptance on an isolated temporary booking database."""
import io,os,socket,sqlite3,subprocess,sys,tempfile,time
from pathlib import Path
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import requests
from PIL import Image
from cryptography.fernet import Fernet
source=Path(sys.argv[1]).resolve() if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='bus-chinese-browser-') as temp:
 database=Path(temp)/'test.sqlite'
 with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
 origin=f'http://127.0.0.1:{port}'
 env=dict(os.environ,BUS_DB=str(database),BUS_ADMIN_PASSWORD='synthetic-admin',BUS_FERNET_KEY=Fernet.generate_key().decode(),BUS_PUBLIC_URL=origin,BUS_INITIAL_CARDS='999001,999002')
 process=subprocess.Popen([sys.executable,'-m','uvicorn','app:app','--host','127.0.0.1','--port',str(port),'--no-access-log'],cwd=source,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
 try:
  for _ in range(100):
   try:
    if requests.get(origin+'/bus/',timeout=1).ok:break
   except requests.RequestException:pass
   time.sleep(.1)
  else:raise AssertionError('Isolated app failed to start')
  dep=datetime.now(ZoneInfo('Europe/Istanbul'))+timedelta(minutes=10);auth=('admin','synthetic-admin')
  r=requests.post(origin+'/bus/api/admin/routes',auth=auth,json=dict(name='Synthetic route',origin='Камп',destination='Магазин',direction='forward',forward_time=dep.strftime('%H:%M:%S'),reverse_time=dep.strftime('%H:%M:%S'),capacity=41,service_date=dep.date().isoformat()),timeout=5);r.raise_for_status()
  rid=requests.get(origin+'/bus/api/admin/routes',auth=auth,timeout=5).json()[0]['id']
  booking=requests.post(origin+'/bus/api/book',json=dict(card='999001',date=dep.date().isoformat(),forward=rid,direction='forward',lang='ru'),timeout=5);booking.raise_for_status();private=booking.json()['url']
  public=requests.post(origin+'/bus/api/find-booking',json={'card':'999001'},timeout=5).json()['items'][0]['url']
  subprocess.run(['node',str(Path(__file__).with_suffix('.js')),origin,private,public],check=True,timeout=120)
  with sqlite3.connect(database) as c:assert c.execute("SELECT lang FROM bookings WHERE card='999002'").fetchone()[0]=='zh'
  for route in [private,public]:
   jpg=requests.get(origin+route+'/download/jpg?lang=zh',timeout=15);jpg.raise_for_status();assert Image.open(io.BytesIO(jpg.content)).size==(1080,1920)
   pdf=requests.get(origin+route+'/download/pdf?lang=zh',timeout=15);pdf.raise_for_status();assert pdf.content.startswith(b'%PDF')
  if os.environ.get('BUS_QA_IMAGE'):Path(os.environ['BUS_QA_IMAGE']).write_bytes(jpg.content)
  print('PASS: persisted Chinese passenger booking and private/public JPG/PDF exports')
 finally:
  process.terminate()
  try:process.wait(timeout=5)
  except subprocess.TimeoutExpired:process.kill();process.wait()
