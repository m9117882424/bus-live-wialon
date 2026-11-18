"""Synthetic full-route and negative-GPS evidence; no Wialon connection."""
import io,json,os,runpy,socket,sqlite3,subprocess,sys,tempfile,time,urllib.request
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from openpyxl import load_workbook
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root))
from public_analytics.wialon import Collector
from public_analytics.execution import GPS_VERSION
TZ=ZoneInfo("Europe/Istanbul")
with tempfile.TemporaryDirectory(prefix="bus-gps-browser-") as tmp:
    db=Path(tmp)/"fixture.sqlite";cache=Path(tmp)/"cache.sqlite"
    runpy.run_path(str(root/"tests/test_public_analytics.py"))["fixture"](db)
    collector=Collector(cache,enabled=False)
    buses=["33KY555","70ACB112"]
    for day in ("2026-11-16","2026-11-17"):
        midnight=datetime.fromisoformat(day).replace(tzinfo=TZ)
        start=int((midnight-timedelta(hours=1)).timestamp())
        end=int((midnight+timedelta(days=1,hours=3)).timestamp())
        for bus in buses:
            events=[]
            if day=="2026-11-16":
                for direction,hour,minute in ([("forward",9,5),("reverse",10,0)] if bus==buses[0] else [("reverse",10,57)]):
                    stamp=int(midnight.replace(hour=hour,minute=minute).timestamp())
                    events.append(dict(date=day,plate=bus,direction=direction,stamp=stamp,
                        arrived_at=stamp+600,arrival_confirmed_at=stamp+630,route_complete=True))
            coverage=[[start,end]]
            if day=="2026-11-17" and bus==buses[1]:
                coverage=[[start,int(midnight.replace(hour=17).timestamp())],
                          [int(midnight.replace(hour=20).timestamp()),end]]
            payload=dict(version=GPS_VERSION,events=events,coverage=coverage,collected_at=end,point_count=200)
            with collector.connection() as c:c.execute("INSERT INTO gps_tracks_v4 VALUES(?,?,?,?,?)",(day,bus,time.time(),"ready",json.dumps(payload)))
    with socket.socket() as sock:sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
    origin="http://127.0.0.1:"+str(port)
    env={**os.environ,"BUS_ANALYTICS_DB":str(db),"BUS_ANALYTICS_CACHE":str(cache),"BUS_ANALYTICS_COLLECTOR":"0"}
    with open(Path(tmp)/"server.log","w") as log:
        server=subprocess.Popen([sys.executable,"-m","uvicorn","public_analytics.app:app","--host","127.0.0.1","--port",str(port),"--no-access-log"],cwd=root,env=env,stdout=log,stderr=log)
        try:
            for _ in range(100):
                try:urllib.request.urlopen(origin+"/analytics/api/health",timeout=1);break
                except OSError:time.sleep(.1)
            subprocess.run(["node",str(root/"tests/gps_execution_browser.js"),origin],cwd=root,env=env,check=True,timeout=100)
            url=origin+"/analytics/api/export.xlsx?start=2026-11-16&end=2026-11-17"
            book=load_workbook(io.BytesIO(urllib.request.urlopen(url).read()))
            assert (book["Сводка"].cell(8,14).value,book["Сводка"].cell(8,15).value)==(3,4)
            assert book["Рейсы"].cell(1,18).value=="Прибытие на КПП"
            rows=list(book["Рейсы"])[1:]
            assert sum(row[16].value=="Состоялся" for row in rows)==3
            assert all(isinstance(row[17].value,datetime) for row in rows if row[16].value=="Состоялся")
            print("PASS: execution XLSX counters and typed GPS arrivals")
        finally:server.terminate();server.wait(timeout=10)
