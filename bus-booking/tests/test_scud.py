"""SCUD privacy, normalization, matching, boundaries and read-only report regression."""
import copy,hashlib,io,json,sqlite3,sys,tempfile,unittest
from datetime import datetime,timedelta
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from openpyxl import Workbook,load_workbook
from scud import parse_workbook,init_schema,save_import,attach_scud,ScudError,TZ,MAX_BYTES,import_status
from public_analytics.data import build_report
from public_analytics.app import excel
from test_public_analytics import fixture

DAY="2026-11-17";P=datetime(2026,11,17,9,tzinfo=TZ);NOW=P+timedelta(hours=4)
SENSITIVE=["DO_NOT_SAVE_FIRST","DO_NOT_SAVE_LAST","PASSPORT_SECRET","KIMLIK_SECRET"]
HEADERS=["Tip","Sicil No","Pasaport","Kart No","Ad","Soyad","Tarih","Okuyucu Ad","Yönü","Okuyucu Tipi"]
def workbook(rows,headers=HEADERS):
 wb=Workbook();ws=wb.active;ws.append(headers)
 for plate,card,stamp in rows:
  if len(headers)==10:ws.append(["",SENSITIVE[3],SENSITIVE[2],card,SENSITIVE[0],SENSITIVE[1],stamp,plate,"Giriş","BUS"])
  else:ws.append([plate,card,stamp])
 stream=io.BytesIO();wb.save(stream);return stream.getvalue()
def db(path=":memory:"):
 c=sqlite3.connect(path);c.row_factory=sqlite3.Row;init_schema(c);c.commit();return c
def insert(c,rows):
 return save_import(c,parse_workbook(workbook(rows)),NOW)
def trip(key="one",planned=P,bus="33KY555",capacity=5,direction="forward"):
 return dict(key=key,date=planned.date().isoformat(),direction=direction,planned_at=planned.isoformat(),
             capacity=capacity,bookings=1,free=capacity-1,vehicle=bus,execution_vehicle="",_hints=[])

class ImportTests(unittest.TestCase):
 def test_exact_allowlist_and_private_columns_discarded(self):
  parsed=parse_workbook(workbook([("33 KY 555 (DIŞ KAPI)","001234",P.replace(tzinfo=None))]))
  self.assertEqual(parsed["records"],[("33KY555","001234",int(P.timestamp()*1000))])
  self.assertFalse(any(secret in str(parsed) for secret in SENSITIVE))
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/"test.sqlite";c=db(path);save_import(c,parsed,NOW);c.close()
   self.assertFalse(any(secret.encode() in path.read_bytes() for secret in SENSITIVE))
   c=db(path);self.assertEqual([row[1] for row in c.execute("PRAGMA table_info(scud_events)")],["plate","card","stamp"])
 def test_repeat_upload_and_repeat_rows_are_idempotent(self):
  c=db();rows=[("33KY555","1234",P.replace(tzinfo=None))]*2
  first=insert(c,rows);second=insert(c,rows)
  self.assertEqual((first["inserted"],first["duplicates"]),(1,1))
  self.assertEqual((second["inserted"],second["duplicates"]),(0,2))
  self.assertEqual(c.execute("SELECT COUNT(*) FROM scud_events").fetchone()[0],1)
 def test_unicode_headers_and_three_safe_columns(self):
  parsed=parse_workbook(workbook([("70 ACB 112","1234",P.replace(tzinfo=None))],["Автобус","Номер карты","Время отметки"]))
  self.assertEqual(parsed["records"][0][:2],("70ACB112","1234"))
 def test_missing_headers_never_infer_card_from_identity(self):
  with self.assertRaises(ScudError):parse_workbook(workbook([("33KY555","1234",P.replace(tzinfo=None))],["Sicil No","Pasaport","Ad"]))
 def test_formulas_invalid_cards_and_non_bus_readers(self):
  rows=[("33KY555","1234",P.replace(tzinfo=None)),("33KY555","=1234",P.replace(tzinfo=None)),("Entrance door","1234",P.replace(tzinfo=None)),("33KY555",True,P.replace(tzinfo=None))]
  result=parse_workbook(workbook(rows));self.assertEqual((result["invalid"],result["ignored"],len(result["records"])),(2,1,1))
 def test_unknown_or_multiple_bus_plates_are_not_imported(self):
  rows=[("33KY555","1234",P.replace(tzinfo=None)),("33KY555 / 70ACB112","5555",P.replace(tzinfo=None))]
  self.assertEqual(parse_workbook(workbook(rows))["ignored"],1)
 def test_dates_turkey_timezone_and_no_date_only_guess(self):
  rows=[("33KY555","1234","17.11.2026 09:00:00"),("33KY555","1235","2026-11-17T06:00:00Z"),("33KY555","1236","2026-11-17")]
  parsed=parse_workbook(workbook(rows));self.assertEqual(parsed["invalid"],1)
  self.assertEqual({row[2] for row in parsed["records"]},{int(P.timestamp()*1000)})
 def test_empty_and_oversized_or_corrupt_input(self):
  for content in (b"",b"not a workbook",b"x"*(MAX_BYTES+1)):
   with self.assertRaises(ScudError):parse_workbook(content)

class MatchingTests(unittest.TestCase):
 def setUp(self):self.c=db()
 def attach(self,rows,current=NOW):attach_scud(rows,self.c,current);return rows
 def test_exact_window_boundaries_card_dedup_and_separate_free_seats(self):
  marks=[("33KY555",card,(P+timedelta(seconds=delta)).replace(tzinfo=None)) for card,delta in [("1000",-1800),("2000",1800),("2000",0),("3000",-1801),("4000",1801)]]
  insert(self.c,marks);row=trip();self.attach([row])
  self.assertEqual((row["scud_count"],row["scud_free"],row["free"]),(2,3,4))
  self.assertEqual(row["scud_status"],"ready")
 def test_window_status_matches_thirty_minutes(self):
  result=import_status(self.c)
  self.assertEqual((result["window_before_minutes"],result["window_after_minutes"]),(30,30))
 def test_extended_after_window_is_partial_until_thirty_minutes(self):
  insert(self.c,[("33KY555","1000",(P+timedelta(minutes=10)).replace(tzinfo=None))])
  row=trip();self.attach([row],P+timedelta(minutes=6))
  self.assertEqual((row["scud_count"],row["scud_status"]),(0,"partial"))
  self.attach([row],P+timedelta(minutes=10))
  self.assertEqual((row["scud_count"],row["scud_status"]),(1,"partial"))
  self.assertIsNone(row["scud_free"])
  self.attach([row],P+timedelta(minutes=30))
  self.assertEqual((row["scud_count"],row["scud_status"]),(1,"ready"))
 def test_extended_after_window_crosses_midnight(self):
  planned=P.replace(hour=23,minute=50);current=planned+timedelta(hours=1)
  insert(self.c,[("33KY555","1000",planned.replace(tzinfo=None))])
  row=trip(planned=planned);self.attach([row],current)
  self.assertEqual(row["scud_status"],"partial")
  insert(self.c,[("33KY555","2000",(planned+timedelta(minutes=30)).replace(tzinfo=None))])
  self.attach([row],current)
  self.assertEqual((row["scud_count"],row["scud_status"]),(2,"ready"))
 def test_bus_and_direction_attribution(self):
  insert(self.c,[("33KY555","1000",P.replace(tzinfo=None)),("70ACB112","2000",P.replace(tzinfo=None))])
  rows=[trip("forward",bus="33KY555"),trip("reverse",bus="70ACB112",direction="reverse")]
  self.attach(rows);self.assertEqual([r["scud_count"] for r in rows],[1,1])
 def test_no_data_is_none_but_loaded_day_without_marks_is_zero(self):
  row=trip();self.attach([row]);self.assertIsNone(row["scud_count"]);self.assertIsNone(row["scud_free"])
  insert(self.c,[("70ACB112","1000",P.replace(tzinfo=None))]);self.attach([row])
  self.assertEqual((row["scud_count"],row["scud_free"]),(0,5))
 def test_unknown_bus_is_not_guessed(self):
  insert(self.c,[("33KY555","1000",P.replace(tzinfo=None))]);row=trip(bus="");self.attach([row])
  self.assertEqual(row["scud_status"],"no_bus");self.assertIsNone(row["scud_count"])
 def test_one_mark_cannot_count_in_overlapping_trips(self):
  insert(self.c,[("33KY555","1000",P.replace(tzinfo=None))]);rows=[trip("a"),trip("b",planned=P+timedelta(minutes=20))]
  self.attach(rows);self.assertEqual([r["scud_count"] for r in rows],[0,0])
  self.assertEqual([r["scud_ambiguous"] for r in rows],[1,1]);self.assertTrue(all(r["scud_free"] is None for r in rows))
 def test_midnight_window_needs_both_imported_days(self):
  planned=P.replace(hour=0,minute=10)
  insert(self.c,[("33KY555","1000",(planned-timedelta(minutes=30)).replace(tzinfo=None)),("33KY555","2000",(planned+timedelta(minutes=5)).replace(tzinfo=None))])
  row=trip(planned=planned);self.attach([row]);self.assertEqual((row["scud_count"],row["scud_free"]),(2,3))
  self.c.execute("DELETE FROM scud_days WHERE day='2026-11-16'");self.c.commit();self.attach([row])
  self.assertEqual(row["scud_status"],"partial");self.assertIsNone(row["scud_free"])
 def test_future_marks_not_counted_and_over_capacity_not_hidden(self):
  insert(self.c,[("33KY555",str(1000+i),(P+timedelta(seconds=i)).replace(tzinfo=None)) for i in range(7)])
  row=trip();self.attach([row]);self.assertEqual((row["scud_count"],row["scud_free"]),(7,0))
  future=trip(planned=P+timedelta(hours=10));self.attach([future]);self.assertEqual(future["scud_status"],"pending")
 def test_actual_gps_bus_takes_precedence_over_old_hint(self):
  insert(self.c,[("70ACB112","1000",P.replace(tzinfo=None))]);row=trip();row["execution_vehicle"]="70ACB112";self.attach([row])
  self.assertEqual(row["scud_count"],1)

class ReportTests(unittest.TestCase):
 def test_report_read_only_privacy_direction_and_xlsx(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/"booking.sqlite";fixture(path);c=db(path)
   insert(c,[("33KY555","987654",P.replace(tzinfo=None))]);c.close()
   before=hashlib.sha256(path.read_bytes()).hexdigest()
   report=build_report(path,DAY,DAY,"all",[],{},NOW)
   self.assertEqual(report["totals"]["scud_count"],1);self.assertNotIn("987654",json.dumps(report))
   filtered=build_report(path,DAY,DAY,"forward",[],{},NOW)
   self.assertEqual(filtered["totals"]["scud_count"],1)
   report["wialon"]={"last_updated":None};book=load_workbook(io.BytesIO(excel(report)))
   headers=[cell.value for cell in book["Рейсы"][1]]
   self.assertIn("Свободно мест по броням",headers);self.assertIn("Свободно мест по СКУД",headers)
   self.assertEqual(book["Рейсы"].cell(1,22).value,"Факт по СКУД")
   self.assertFalse(any(secret in str([cell.value for sheet in book for row in sheet for cell in row]) for secret in SENSITIVE+["987654"]))
   self.assertEqual(before,hashlib.sha256(path.read_bytes()).hexdigest())

if __name__=="__main__":unittest.main(verbosity=2)
