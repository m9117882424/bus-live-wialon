"""Disposable full booking+analytics servers: authorized upload, privacy and real browser."""
import io,json,os,socket,sqlite3,subprocess,sys,tempfile,time
from datetime import datetime,timedelta
from pathlib import Path
import requests
from cryptography.fernet import Fernet
from openpyxl import load_workbook
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root))
from scud import TZ,MAX_BYTES
from test_scud import workbook,SENSITIVE
def port():
 with socket.socket() as s:s.bind(("127.0.0.1",0));return s.getsockname()[1]
with tempfile.TemporaryDirectory(prefix="bus-scud-browser-") as tmp:
 temp=Path(tmp);database=temp/"bus.sqlite";cache=temp/"gps.sqlite";admin="http://127.0.0.1:"+str(port());analytics="http://127.0.0.1:"+str(port())
 day=(datetime.now(TZ)-timedelta(days=1)).date().isoformat();plan=datetime.fromisoformat(day+"T09:00:00")
 auth=("admin","synthetic-scud-only");env={**os.environ,"BUS_DB":str(database),"BUS_ADMIN_PASSWORD":auth[1],"BUS_FERNET_KEY":Fernet.generate_key().decode(),"BUS_PUBLIC_URL":admin,"BUS_INITIAL_CARDS":"999001","BUS_ANALYTICS_DB":str(database),"BUS_ANALYTICS_CACHE":str(cache),"BUS_ANALYTICS_COLLECTOR":"0"}
 logs=[];servers=[]
 try:
  for module,origin in [("app:app",admin),("public_analytics.app:app",analytics)]:
   log=open(temp/(str(len(logs))+".log"),"w");logs.append(log)
   servers.append(subprocess.Popen([sys.executable,"-m","uvicorn",module,"--host","127.0.0.1","--port",origin.rsplit(":",1)[1],"--no-access-log"],cwd=root,env=env,stdout=log,stderr=log))
   for _ in range(100):
    try:
     if requests.get(origin+("/bus/" if module=="app:app" else "/analytics/api/health"),timeout=1).ok:break
    except requests.RequestException:pass
    time.sleep(.1)
  for direction,bus in [("forward","33KY555"),("reverse","70ACB112")]:
   payload=dict(name="SCUD "+direction,origin="Камп" if direction=="forward" else "Магазин",destination="Магазин" if direction=="forward" else "Камп",direction=direction,forward_time="09:00",reverse_time="09:00",capacity=4,comment=bus,service_date=day)
   requests.post(admin+"/bus/api/admin/routes",auth=auth,json=payload,timeout=10).raise_for_status()
  for path in ["/bus/api/admin/scud/status","/bus/admin-scud.js","/bus/admin-scud.css"]:
   assert requests.get(admin+path,timeout=5).status_code==401
  assert requests.post(admin+"/bus/api/admin/scud/import",data=b"bad",timeout=5).status_code==401
  assert requests.post(admin+"/bus/api/admin/scud/import",auth=auth,data=b"not xlsx",timeout=5).status_code==400
  assert requests.post(admin+"/bus/api/admin/scud/import",auth=auth,data=b"x"*(MAX_BYTES+1),timeout=10).status_code==413
  assert requests.post(admin+"/bus/api/admin/scud/import",auth=auth,headers={"Origin":"https://other.invalid"},data=b"bad",timeout=5).status_code==403
  assert requests.get(admin+"/bus/api/admin/scud/status",auth=auth,timeout=5).json()["records"]==0
  rows=[("33KY555",card,plan+timedelta(seconds=delta)) for card,delta in [("900001",-1800),("900002",-60),("900003",300),("900004",301),("900005",-1801),("900002",-60)]]
  rows += [("70ACB112","900006",plan),("99AB111","900007",plan)]
  one=temp/"one.xlsx";two=temp/"two.xlsx";one.write_bytes(workbook(rows));two.write_bytes(workbook([rows[1],("33KY555","900003",plan+timedelta(minutes=1))]))
  subprocess.run(["node",str(root/"tests/scud_browser.js"),admin,analytics,day,str(one),str(two)],cwd=root,env=env,check=True,timeout=100)
  status=requests.get(admin+"/bus/api/admin/scud/status",auth=auth,timeout=5).json();assert status["records"]==8 and len(status["imports"])==3
  content=requests.get(analytics+"/analytics/api/export.xlsx",params={"start":day,"end":day},timeout=10).content
  book=load_workbook(io.BytesIO(content));assert book["Рейсы"].cell(1,22).value=="Факт по СКУД"
  assert [row[21].value for row in list(book["Рейсы"])[1:]]==[3,1]
  assert [row[22].value for row in list(book["Рейсы"])[1:]]==[1,3]
  assert not any(secret.encode() in database.read_bytes() for secret in SENSITIVE)
  with sqlite3.connect(database) as c:
   assert [row[1] for row in c.execute("PRAGMA table_info(scud_events)")]==["plate","card","stamp"]
   assert c.execute("SELECT COUNT(*) FROM allowed_cards").fetchone()[0]==1
  print("PASS: SCUD admin auth/CSRF/size/errors, allow-list storage, unchanged whitelist, idempotent import and matching XLSX")
 finally:
  for server in servers:server.terminate()
  for server in servers:server.wait(timeout=10)
  for log in logs:log.close()
