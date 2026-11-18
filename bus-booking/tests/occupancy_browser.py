"""Browser checks use a disposable booking database and synthetic credentials."""
import os,socket,sqlite3,subprocess,sys,tempfile,time
from pathlib import Path
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
import requests
from cryptography.fernet import Fernet
root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="bus-occupancy-browser-") as temp:
    with socket.socket() as sock:sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
    origin="http://127.0.0.1:"+str(port)
    env={**os.environ,"BUS_DB":str(Path(temp)/"fixture.sqlite"),"BUS_ADMIN_PASSWORD":"synthetic-occupancy","BUS_FERNET_KEY":Fernet.generate_key().decode(),"BUS_PUBLIC_URL":origin,"BUS_INITIAL_CARDS":"999001"}
    with open(Path(temp)/"server.log","w") as log:
        server=subprocess.Popen([sys.executable,"-m","uvicorn","app:app","--host","127.0.0.1","--port",str(port),"--no-access-log"],cwd=root,env=env,stdout=log,stderr=log)
        try:
            for _ in range(100):
                try:
                    if requests.get(origin+"/bus/",timeout=1).ok:break
                except requests.RequestException:pass
                time.sleep(.1)
            auth=("admin","synthetic-occupancy")
            current=datetime.now(ZoneInfo("Europe/Istanbul"))
            today=current.date().isoformat();tomorrow=(current+timedelta(days=1)).date().isoformat()
            def add(**changes):
                payload=dict(name="Today fixture",origin="Камп",destination="Магазин",direction="forward",forward_time="07:00",reverse_time="08:00",capacity=3,service_date=today)
                payload.update(changes)
                requests.post(origin+"/bus/api/admin/routes",auth=auth,json=payload,timeout=5).raise_for_status()
            add()
            add(name="Tomorrow <fixture> & route",direction="both",forward_time="09:00",reverse_time="10:00",capacity=4,service_date=tomorrow)
            routes=requests.get(origin+"/bus/api/admin/routes",auth=auth,timeout=5).json()
            rid=next(route["id"] for route in routes if route["service_date"]==tomorrow)
            result=requests.post(origin+"/bus/api/book",json={"card":"999001","date":tomorrow,"forward":rid,"reverse":rid},timeout=5);result.raise_for_status()
            with sqlite3.connect(env["BUS_DB"]) as connection:forward_leg=connection.execute("SELECT id FROM legs WHERE direction='forward'").fetchone()[0]
            subprocess.run(["node",str(root/"tests/occupancy_browser.js"),origin,today,tomorrow,str(forward_leg)],cwd=root,env=env,check=True,timeout=110)
        finally:
            server.terminate();server.wait(timeout=10)
