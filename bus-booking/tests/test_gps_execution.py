"""GPS execution is positive route evidence, or negative evidence from every bus."""
import copy,io,json,sqlite3,sys,tempfile,threading,time,unittest
from datetime import datetime,timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from public_analytics.execution import attach_execution,DEPARTURE_WINDOW,MAX_TRAVEL,MAX_GPS_GAP,MAX_IDLE_GAP,MIN_ZONE_SECONDS,GPS_VERSION
from public_analytics.wialon import track_evidence,Collector
from public_analytics.data import TZ,build_report
from public_analytics.app import excel
from openpyxl import load_workbook
from test_public_analytics import fixture

DAY="2026-11-17"
PLAN=datetime(2026,11,17,9,tzinfo=TZ)
P=int(PLAN.timestamp())
FLEET=["33KY555","70ACB112"]


def trip(key="one",planned=PLAN,direction="forward",hints=None):
    return dict(key=key,date=planned.date().isoformat(),direction=direction,
                planned_at=planned.isoformat(),_hints=hints or [],status="unknown")


def event(stamp=P,plate="33KY555",direction="forward",complete=True):
    return dict(date=DAY,plate=plate,direction=direction,stamp=stamp,
                arrived_at=stamp+600 if complete else None,
                arrival_confirmed_at=stamp+630 if complete else None,route_complete=complete)


def coverage(end=P+20000):
    return {(DAY,bus):dict(status="ready",version=GPS_VERSION,coverage=[[P-10000,end]]) for bus in FLEET}


class ExecutionTests(unittest.TestCase):
    def check(self,rows,events=None,telemetry=None,now=None,readiness=None,fleet=None):
        attach_execution(rows,events or [],readiness or {DAY:"ready"},
                         coverage() if telemetry is None else telemetry,
                         FLEET if fleet is None else fleet,now or PLAN+timedelta(hours=4))
        return rows

    def test_complete_same_bus_route_confirms_even_if_another_bus_has_no_gps(self):
        row=trip()
        self.check([row],[event()],telemetry={},readiness={DAY:"error"})
        self.assertEqual(row["execution_status"],"performed")
        self.assertEqual(row["duration_minutes"],10)
        self.assertEqual(row["execution_vehicle"],"33KY555")
        self.assertEqual(row["arrival_at"],datetime.fromtimestamp(P+600,TZ).isoformat())

    def test_exit_alone_is_in_progress_then_not_performed_only_with_full_coverage(self):
        row=trip()
        self.check([row],[event(complete=False)],now=PLAN+timedelta(minutes=50))
        self.assertEqual(row["execution_status"],"in_progress")
        self.check([row],[event(complete=False)])
        self.assertEqual((row["execution_status"],row["execution_reason"]),("not_performed","no_complete_route"))

    def test_no_departure_and_full_fleet_observation_marks_not_performed(self):
        row=trip()
        self.check([row],now=PLAN+timedelta(hours=1))
        self.assertEqual((row["execution_status"],row["execution_reason"]),("not_performed","no_departure"))

    def test_one_unobserved_bus_or_track_gap_prevents_negative_claim(self):
        for data in ({}, {(DAY,FLEET[0]):coverage()[(DAY,FLEET[0])]},
                     {**coverage(),(DAY,FLEET[1]):dict(status="ready",version=GPS_VERSION,coverage=[[P-10000,P-10],[P+10,P+20000]])}):
            row=trip();self.check([row],telemetry=data)
            self.assertEqual(row["execution_status"],"unknown")
        row=trip();self.check([row],fleet=[]);self.assertEqual(row["execution_reason"],"no_fleet")

    def test_future_open_window_and_stale_observation_never_mean_failure(self):
        row=trip();self.check([row],now=PLAN-timedelta(minutes=1));self.assertEqual(row["execution_status"],"planned")
        self.check([row],now=PLAN+timedelta(minutes=10));self.assertEqual(row["execution_status"],"checking")
        self.check([row],telemetry=coverage(P+60));self.assertEqual(row["execution_status"],"unknown")

    def test_duplicate_event_is_used_once_parallel_ties_remain_ambiguous(self):
        rows=[trip("one"),trip("two")]
        self.check(rows,[event(),copy.deepcopy(event())])
        self.assertEqual([r["execution_status"] for r in rows],["ambiguous","ambiguous"])
        single=trip();self.check([single],[event(),copy.deepcopy(event())])
        self.assertEqual(single["execution_status"],"performed")

    def test_any_bus_can_confirm_hint_is_preference_not_a_veto(self):
        row=trip(hints=["70ACB112"]);self.check([row],[event()])
        self.assertEqual(row["execution_status"],"performed")
        row=trip();self.check([row],[event(plate="70ACB112"),event(stamp=P+60)])
        self.assertEqual(row["execution_vehicle"],"70ACB112")

    def test_one_journey_cannot_confirm_another_nearby_schedule_slot(self):
        rows=[trip("first"),trip("second",PLAN+timedelta(minutes=30))]
        self.check(rows,[event(P+60)])
        self.assertEqual(rows[0]["execution_status"],"performed")
        self.assertEqual(rows[1]["execution_status"],"ambiguous")

    def test_wrong_direction_unconfirmed_arrival_and_future_evidence_not_accepted(self):
        for evidence in ([event(direction="reverse")],
                         [{**event(),"arrival_confirmed_at":P+601}],
                         [event(stamp=P+50000)]):
            row=trip();self.check([row],evidence)
            self.assertNotEqual(row["execution_status"],"performed")

    def test_early_completed_route_can_be_confirmed_before_scheduled_time(self):
        row=trip()
        self.check([row],[event(P-1200)],now=PLAN-timedelta(minutes=5))
        self.assertEqual(row["execution_status"],"performed");self.assertEqual(row["delta_minutes"],-20)

    def test_cross_midnight_journey_is_not_lost(self):
        planned=datetime(2026,11,17,23,55,tzinfo=TZ)
        row=trip(planned=planned)
        self.check([row],[event(int(planned.timestamp()))],now=planned+timedelta(hours=1))
        self.assertEqual(row["execution_status"],"performed")
        self.assertTrue(row["arrival_at"].startswith("2026-11-18"))


class Zone:
    def __init__(self,low,high):self.low=low;self.high=high
    def contains(self,y,x):return self.low<=y<=self.high and self.low<=x<=self.high


class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.zones={"forward":Zone(0,1),"reverse":Zone(5,6),"kpp":Zone(10,11)}
        self.points=[(P,(.5,.5)),(P+30,(.5,.5)),(P+60,(2,2)),(P+90,(2,2)),
                     (P+120,(10.5,10.5)),(P+150,(10.5,10.5))]

    def evidence(self,points):
        return track_evidence(points,self.zones,DAY,"33KY555")

    def test_both_geofences_and_two_destination_points_are_required(self):
        result=self.evidence(self.points)
        self.assertEqual(len(result["events"]),1)
        e=result["events"][0]
        self.assertEqual((e["stamp"],e["arrived_at"],e["arrival_confirmed_at"]),(P+60,P+120,P+150))
        self.assertTrue(e["route_complete"])
        self.assertFalse(self.evidence(self.points[:-1])["events"][0]["route_complete"])

    def test_seventeen_second_origin_visit_confirms_both_directions(self):
        for point,direction in [((.5,.5),"forward"),((5.5,5.5),"reverse")]:
            points=[(P,point),(P+7,point),(P+17,point),(P+20,(2,2)),
                    (P+47,(2,2)),(P+150,(2,2)),(P+300,(10.5,10.5)),(P+330,(10.5,10.5))]
            evidence=self.evidence(points)
            self.assertEqual(len(evidence["events"]),1)
            e=evidence["events"][0]
            self.assertEqual((e["direction"],e["stamp"],e["arrived_at"]),(direction,P+20,P+300))
            self.assertTrue(e["route_complete"])
            row=trip(direction=direction)
            attach_execution([row],evidence["events"],{DAY:"ready"},{},FLEET,PLAN+timedelta(hours=1))
            self.assertEqual(row["execution_status"],"performed")

    def test_one_origin_point_or_duplicate_timestamp_does_not_confirm(self):
        tail=[(P+20,(2,2)),(P+47,(2,2)),(P+150,(2,2)),(P+300,(10.5,10.5)),(P+330,(10.5,10.5))]
        for origin in [[(P,(.5,.5))],[(P,(.5,.5)),(P,(.5,.5))]]:
            self.assertEqual(self.evidence(origin+tail)["events"],[])

    def test_seventeen_second_destination_visit_still_does_not_confirm(self):
        result=self.evidence(self.points[:-1]+[(P+137,(10.5,10.5))])
        self.assertFalse(result["events"][0]["route_complete"])

    def test_other_scheduled_stop_does_not_complete_a_journey_without_kpp(self):
        for origin,destination,direction in [((.5,.5),(5.5,5.5),"forward"),((5.5,5.5),(.5,.5),"reverse")]:
            points=[(P,origin),(P+30,origin),(P+60,(2,2)),(P+90,(2,2)),(P+120,destination),(P+150,destination)]
            events=self.evidence(points)["events"]
            self.assertEqual(events[0]["direction"],direction)
            self.assertFalse(events[0]["route_complete"])

    def test_kpp_visit_without_origin_departure_does_not_complete_a_journey(self):
        self.assertEqual(self.evidence([(P,(10.5,10.5)),(P+30,(10.5,10.5)),(P+60,(2,2)),(P+90,(2,2))])["events"],[])

    def test_first_external_point_can_already_be_in_destination(self):
        result=self.evidence(self.points[:2]+[(P+150,(10.5,10.5)),(P+180,(10.5,10.5))])
        self.assertTrue(result["events"][0]["route_complete"])
        self.assertEqual(result["events"][0]["arrived_at"],P+150)

    def test_return_to_origin_does_not_reuse_a_later_arrival(self):
        points=self.points[:4]+[(P+110,(.5,.5)),(P+140,(.5,.5)),(P+170,(2,2)),
                              (P+200,(2,2)),(P+230,(10.5,10.5)),(P+260,(10.5,10.5))]
        events=self.evidence(points)["events"]
        self.assertEqual([e["route_complete"] for e in events],[False,True])

    def test_moving_gap_invalidates_route_and_coverage(self):
        result=self.evidence(self.points[:4]+[(P+400,(10.5,10.5)),(P+430,(10.5,10.5))])
        self.assertFalse(result["events"][0]["route_complete"])
        self.assertEqual(len(result["coverage"]),2)

    def test_stationary_heartbeats_are_bridged_only_within_ten_minutes_and_twenty_metres(self):
        stationary=self.evidence([(P,(.5,.5)),(P+300,(.5,.5)),(P+600,(.50005,.50005))])
        self.assertEqual(stationary["coverage"],[[P,P+600]])
        for points in ([(P,(.5,.5)),(P+601,(.5,.5))],[(P,(.5,.5)),(P+300,(.51,.51))]):
            self.assertEqual(len(self.evidence(points)["coverage"]),2)

    def test_wrong_direction_and_initially_outside_do_not_create_departure(self):
        self.assertEqual(self.evidence(self.points[2:])["events"],[])
        reverse=[(P,(5.5,5.5)),(P+30,(5.5,5.5))]+self.points[2:]
        events=self.evidence(reverse)["events"]
        self.assertEqual(events[0]["direction"],"reverse");self.assertTrue(events[0]["route_complete"])


class CacheAndReportTests(unittest.TestCase):
    def test_legacy_cache_is_ignored_and_only_active_days_are_requested(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"cache.sqlite"
            with sqlite3.connect(path) as c:
                c.execute("CREATE TABLE tracks(day,plate,updated,status,events)")
                c.execute("INSERT INTO tracks VALUES(?,?,?,?,?)",(DAY,FLEET[0],0,"ready",json.dumps([event()])))
                c.execute("CREATE TABLE gps_tracks_v3(day,plate,updated,status,payload)")
                c.execute("INSERT INTO gps_tracks_v3 VALUES(?,?,?,?,?)",(DAY,FLEET[0],time.time(),"ready",json.dumps(dict(version=3,events=[event()],coverage=[[P-10000,P+20000]]))))
                c.execute("CREATE TABLE gps_tracks_v2(day,plate,updated,status,payload)")
                c.execute("INSERT INTO gps_tracks_v2 VALUES(?,?,?,?,?)",(DAY,FLEET[0],time.time(),"ready",json.dumps(dict(version=2,events=[event()],coverage=[[P-10000,P+20000]]))))
            collector=Collector(path,enabled=False)
            events,ready,metadata,telemetry=collector.request("2026-11-16",DAY,FLEET,PLAN+timedelta(hours=4),{DAY})
            self.assertEqual(events,[]);self.assertEqual(ready,{DAY:"loading"})
            self.assertEqual(set(telemetry),{(DAY,bus) for bus in FLEET})
            with sqlite3.connect(path) as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM tracks").fetchone()[0],1)
            with sqlite3.connect(path) as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM gps_tracks_v2").fetchone()[0],1)
            with sqlite3.connect(path) as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM gps_tracks_v3").fetchone()[0],1)

    def test_fetch_error_preserves_previously_proven_complete_route(self):
        with tempfile.TemporaryDirectory() as temp:
            collector=Collector(Path(temp)/"cache.sqlite",enabled=False)
            payload=dict(version=GPS_VERSION,events=[event()],coverage=[[P-10000,P+20000]],collected_at=P+1000)
            with collector.connection() as c:c.execute("INSERT INTO gps_tracks_v4 VALUES(?,?,?,?,?)",(DAY,FLEET[0],0,"ready",json.dumps(payload)))
            def fail(*args):raise OSError("synthetic network failure")
            collector.fetch=fail;collector.pending.add((DAY,FLEET[0]));collector.jobs.put((DAY,FLEET[0]))
            thread=threading.Thread(target=collector.worker);thread.start();collector.jobs.join();collector.close();thread.join(timeout=3)
            with collector.connection() as c:state,text=c.execute("SELECT status,payload FROM gps_tracks_v4").fetchone()
            self.assertEqual(state,"error");self.assertTrue(json.loads(text)["events"][0]["route_complete"])

    def test_report_totals_filtering_and_excel_include_execution_without_passenger_data(self):
        with tempfile.TemporaryDirectory() as temp:
            db=Path(temp)/"fixture.sqlite";fixture(db)
            report=build_report(db,DAY,DAY,"all",[event()],{DAY:"ready"},PLAN+timedelta(hours=5),coverage(),FLEET)
            self.assertEqual(report["totals"]["performed"],1)
            self.assertEqual(sum(report["totals"][key] for key in ("performed","not_performed","in_progress","execution_pending","execution_unknown","execution_ambiguous")),report["totals"]["trips"])
            report["wialon"]={"last_updated":None}
            book=load_workbook(io.BytesIO(excel(report)))
            self.assertIn("Факт рейса",[cell.value for cell in book["Рейсы"][1]])
            self.assertIn("Состоялся",[row[16].value for row in list(book["Рейсы"])[1:]])
            self.assertNotIn("private-card",str([cell.value for sheet in book for row in sheet for cell in row]))
            filtered=build_report(db,DAY,DAY,"reverse",[event()],{DAY:"ready"},PLAN+timedelta(hours=5),coverage(),FLEET)
            self.assertEqual(filtered["totals"]["performed"],0)
            self.assertEqual([t["execution_status"] for t in filtered["trips"]],[t["execution_status"] for t in report["trips"] if t["direction"]=="reverse"])


if __name__=="__main__":unittest.main(verbosity=2)
