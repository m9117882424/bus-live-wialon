"""Active schedule dates, live reservation counts, cancellation and admin auth."""
import io,json,os,socket,sqlite3,subprocess,sys,tempfile,time,unittest
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from cryptography.fernet import Fernet
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from occupancy import occupancy_report
from public_analytics.data import quick_period
TZ=ZoneInfo("Europe/Istanbul")
NOW=datetime(2026,11,18,9,tzinfo=TZ)


class OccupancyTests(unittest.TestCase):
    def setUp(self):
        self.c=sqlite3.connect(":memory:");self.c.row_factory=sqlite3.Row
        self.c.executescript("""
            CREATE TABLE routes(id INTEGER PRIMARY KEY,name,origin,destination,forward_time,reverse_time,
                                capacity,active,direction,service_date,weekday_mask,deleted_at);
            CREATE TABLE route_service_periods(route_id PRIMARY KEY,starts_on,ends_on);
            CREATE TABLE bookings(id INTEGER PRIMARY KEY,cancelled_at);
            CREATE TABLE legs(booking_id,route_id,direction,date);
        """)
        rows=[
            (1,"Daily both","Камп","Шок","10:00","12:00",4,1,"both","",127,None),
            (2,"Dated reverse","Шок","Камп","14:00","14:00",3,1,"reverse","2026-11-18",127,None),
            (3,"Inactive","Камп","Шок","11:00","12:00",4,0,"forward","",127,None),
            (4,"Deleted","Камп","Шок","11:00","12:00",4,1,"forward","",127,"2026-11-18"),
            (5,"Thursday","Камп","Шок","13:00","14:00",4,1,"forward","",8,None),
            (6,"New period","Камп","Шок","08:00","09:00",4,1,"forward","",127,None),
            (7,"Expired","Камп","Шок","11:00","12:00",4,1,"forward","",127,None),
            (8,"Earlier today","Камп","Шок","07:00","08:00",2,1,"forward","2026-11-18",127,None),
        ]
        self.c.executemany("INSERT INTO routes VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",rows)
        self.c.executemany("INSERT INTO route_service_periods VALUES(?,?,?)",[(1,"2026-11-16","2026-11-20"),(6,"2026-11-20","2026-12-31"),(7,"2026-11-16","2026-11-17")])
        self.c.executemany("INSERT INTO bookings VALUES(?,?)",[(1,None),(2,None),(3,"2026-11-18"),(4,None)])
        self.c.executemany("INSERT INTO legs VALUES(?,?,?,?)",[(1,1,"forward","2026-11-18"),(2,1,"forward","2026-11-18"),(3,1,"forward","2026-11-18"),(1,1,"reverse","2026-11-18"),(1,8,"forward","2026-11-18"),(4,8,"forward","2026-11-18")])
        self.c.commit()

    def tearDown(self):
        self.c.close()

    def report(self,day="2026-11-18",hours=48):
        return occupancy_report(self.c,NOW,hours,day)

    def test_active_dates_weekdays_periods_cutoff_and_directions(self):
        r=self.report()
        self.assertEqual(r["dates"],["2026-11-18","2026-11-19","2026-11-20"])
        self.assertEqual({x["route_id"] for x in r["trips"]},{1,2,8})
        reverse=next(x for x in r["trips"] if x["route_id"]==1 and x["direction"]=="reverse")
        self.assertEqual((reverse["origin"],reverse["destination"]),("Шок","Камп"))
        self.assertEqual({x["route_id"] for x in self.report("2026-11-19")["trips"]},{1,5})
        self.assertEqual([x["route_id"] for x in self.report("2026-11-20")["trips"]],[6])
        self.assertTrue(next(x for x in r["trips"] if x["route_id"]==8)["past"])

    def test_counts_are_reservations_not_boardings_cancelled_excluded_read_only(self):
        before="\n".join(self.c.iterdump())
        r=self.report();row=next(x for x in r["trips"] if x["route_id"]==1 and x["direction"]=="forward")
        self.assertEqual((row["booked"],row["free"]),(2,2))
        full=next(x for x in r["trips"] if x["route_id"]==8)
        self.assertEqual((full["booked"],full["free"]),(2,0))
        self.assertEqual(r["totals"],{"trips":4,"booked":5,"free":8,"capacity":13})
        self.assertEqual(before,"\n".join(self.c.iterdump()))

    def test_refresh_after_leg_cancellation_and_no_negative_free(self):
        self.c.execute("DELETE FROM legs WHERE booking_id=1 AND route_id=1 AND direction='forward'")
        rows=self.report()["trips"]
        self.assertEqual(next(x for x in rows if x["route_id"]==1 and x["direction"]=="forward")["booked"],1)
        self.assertEqual(next(x for x in rows if x["route_id"]==1 and x["direction"]=="reverse")["booked"],1)
        self.c.execute("INSERT INTO legs VALUES(4,8,'forward','2026-11-18')")
        self.assertEqual(next(x for x in self.report()["trips"] if x["route_id"]==8)["free"],0)

    def test_missing_dates_empty_schedules_invalid_input_and_horizon(self):
        self.assertEqual(self.report("2026-11-19",1)["date"],"2026-11-18")
        self.assertEqual(self.report(hours=1)["dates"],["2026-11-18"])
        for bad in ("bad","20261118","2026-02-30"):
            with self.assertRaises(ValueError):self.report(bad)
        self.c.execute("UPDATE routes SET active=0")
        r=self.report()
        self.assertEqual(r["dates"],[]);self.assertIsNone(r["date"]);self.assertEqual(r["totals"]["trips"],0)

    def test_quick_periods_use_turkey_date_across_utc_midnight(self):
        current=datetime.fromisoformat("2026-11-17T21:30:00+00:00")
        self.assertEqual(quick_period("today",current),("2026-11-18","2026-11-18"))
        self.assertEqual(quick_period("yesterday",current),("2026-11-17","2026-11-17"))
        self.assertEqual(quick_period("7days",current),("2026-11-12","2026-11-18"))
        self.assertEqual(quick_period("yesterday",datetime(2027,1,1,tzinfo=TZ)),("2026-12-31","2026-12-31"))
        with self.assertRaises(ValueError):quick_period("unknown",current)


class OccupancyHttpTests(unittest.TestCase):
    def test_authorization_live_counts_and_partial_cancellation(self):
        with tempfile.TemporaryDirectory(prefix="bus-occupancy-http-") as temp:
            database=Path(temp)/"fixture.sqlite"
            with socket.socket() as sock:sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
            origin="http://127.0.0.1:"+str(port);base=origin+"/bus"
            env={**os.environ,"BUS_DB":str(database),"BUS_ADMIN_PASSWORD":"synthetic-occupancy","BUS_FERNET_KEY":Fernet.generate_key().decode(),"BUS_PUBLIC_URL":origin,"BUS_INITIAL_CARDS":"999001"}
            with open(Path(temp)/"server.log","w") as log:
                process=subprocess.Popen([sys.executable,"-m","uvicorn","app:app","--host","127.0.0.1","--port",str(port),"--no-access-log"],cwd=ROOT,env=env,stdout=log,stderr=log)
                try:
                    for _ in range(100):
                        try:
                            if requests.get(base+"/",timeout=1).ok:break
                        except requests.RequestException:pass
                        time.sleep(.1)
                    auth=("admin","synthetic-occupancy")
                    for path in ("/api/admin/occupancy","/admin-occupancy.js","/admin-occupancy.css"):
                        self.assertEqual(requests.get(base+path,timeout=5).status_code,401)
                        self.assertTrue(requests.get(base+path,auth=auth,timeout=5).ok)
                    tomorrow=(datetime.now(TZ)+timedelta(days=1)).date().isoformat()
                    payload=dict(name="HTTP fixture",origin="Камп",destination="Магазин",direction="both",forward_time="09:00",reverse_time="10:00",capacity=4,service_date=tomorrow)
                    requests.post(base+"/api/admin/routes",auth=auth,json=payload,timeout=5).raise_for_status()
                    rid=requests.get(base+"/api/admin/routes",auth=auth,timeout=5).json()[0]["id"]
                    result=requests.post(base+"/api/book",json={"card":"999001","date":tomorrow,"forward":rid,"reverse":rid},timeout=5);result.raise_for_status()
                    token=result.json()["url"].rsplit("/",1)[-1]
                    def report():return requests.get(base+"/api/admin/occupancy",params={"date":tomorrow},auth=auth,timeout=5).json()
                    self.assertEqual(report()["totals"],{"trips":2,"booked":2,"free":6,"capacity":8})
                    with sqlite3.connect(database) as c:leg=c.execute("SELECT id FROM legs WHERE direction='forward'").fetchone()[0]
                    requests.post(base+"/api/ticket/"+token+"/cancel",json={"confirm":True,"leg_id":leg},timeout=5).raise_for_status()
                    self.assertEqual({x["direction"]:x["booked"] for x in report()["trips"]},{"forward":0,"reverse":1})
                    self.assertEqual(requests.get(base+"/api/admin/occupancy?date=bad",auth=auth,timeout=5).status_code,400)
                    self.assertIn('id="occupancyTab"',requests.get(base+"/admin",auth=auth,timeout=5).text)
                finally:
                    process.terminate();process.wait(timeout=10)


if __name__=="__main__":unittest.main(verbosity=2)
