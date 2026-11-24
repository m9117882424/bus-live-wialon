"""SCUD allow-list import and read-only trip counts. Never retain the source workbook."""
from __future__ import annotations
import io, math, re, sqlite3, unicodedata, zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from openpyxl import load_workbook

TZ=ZoneInfo("Europe/Istanbul")
MAX_BYTES=6*1024*1024
MAX_ROWS=150000
BEFORE_MS=30*60*1000
AFTER_MS=30*60*1000
PLATE=re.compile(r"(?<![A-Z0-9])(\d{2})[ ._-]*([A-Z]{1,3})[ ._-]*(\d{2,4})(?![A-Z0-9])",re.ASCII)
HEADERS={
 "card":{"kartno","kartnumarasi","cardno","cardnumber","номеркарты","карта","номерпропуска","пропуск"},
 "stamp":{"tarih","tarihsaat","date","datetime","timestamp","дата","датавремя","времяотметки"},
 "plate":{"okuyucuad","okuyucuadi","readername","bus","busnumber","plate","plaka","госномер","автобус","номеравтобуса","номеравтобуса"},
}
class ScudError(ValueError):pass

def header(value):
 return "".join(c for c in unicodedata.normalize("NFKD",str(value or "").casefold().replace("ı","i")) if c.isalnum() and not unicodedata.combining(c))

def bus_plate(value):
 matches={"".join(m.groups()) for m in PLATE.finditer(str(value or "").strip().upper())}
 return next(iter(matches)) if len(matches)==1 else None

def card_number(cell):
 value=cell.value
 if value is None or isinstance(value,bool):return None
 if isinstance(value,(int,float)):
  if not math.isfinite(value) or value<0 or int(value)!=value:return None
  value=str(int(value))
  if re.fullmatch(r"0{4,20}",cell.number_format or ""):value=value.zfill(len(cell.number_format))
 else:value=str(value).strip()
 return value if re.fullmatch(r"[0-9]{4,20}",value,re.ASCII) else None

def parse_time(value):
 if isinstance(value,datetime):result=value
 elif isinstance(value,str):
  if not re.search(r"\d{1,2}:\d{2}",value):return None
  result=None
  for fmt in ("%d.%m.%Y %H:%M:%S","%d.%m.%Y %H:%M","%d/%m/%Y %H:%M:%S","%d/%m/%Y %H:%M"):
   try:result=datetime.strptime(value.strip(),fmt);break
   except ValueError:pass
  if result is None:
   try:result=datetime.fromisoformat(value.strip().replace("Z","+00:00"))
   except ValueError:return None
 else:return None
 if not 2000<=result.year<=2100:return None
 return result.replace(tzinfo=TZ) if result.tzinfo is None else result.astimezone(TZ)

def millis(value):return int(round(value.timestamp()*1000))

def parse_workbook(data):
 if not data or len(data)>MAX_BYTES:raise ScudError("Файл XLSX: не более 6 МБ / XLSX en fazla 6 MB")
 try:
  with zipfile.ZipFile(io.BytesIO(data)) as archive:
   if len(archive.infolist())>2000 or sum(i.file_size for i in archive.infolist())>192*1024*1024:raise ScudError("Слишком большой файл XLSX / XLSX çok büyük")
  workbook=load_workbook(io.BytesIO(data),read_only=True,data_only=False,keep_links=False)
 except ScudError:raise
 except Exception:raise ScudError("Не удалось прочитать XLSX / XLSX okunamadı") from None
 records=set();days=set();stats=dict(rows=0,valid_rows=0,invalid=0,ignored=0);recognized=0
 try:
  if len(workbook.worksheets)>12:raise ScudError("Не более 12 листов / En fazla 12 sayfa")
  for sheet in workbook:
   if sheet.max_column>60 or sheet.max_row>MAX_ROWS+12:raise ScudError("Слишком много строк или столбцов / Çok fazla satır veya sütun")
   columns=None;header_row=0
   for number,row in enumerate(sheet.iter_rows(max_row=min(12,sheet.max_row)),1):
    candidate={key:[i for i,cell in enumerate(row) if header(cell.value) in aliases] for key,aliases in HEADERS.items()}
    if all(len(indices)==1 for indices in candidate.values()):
     columns={key:indices[0] for key,indices in candidate.items()};header_row=number;break
   if columns is None:continue
   recognized+=1
   for row in sheet.iter_rows(min_row=header_row+1):
    selected=[row[columns[key]] for key in ("plate","card","stamp")]
    if all(cell.value is None for cell in selected):continue
    stats["rows"]+=1
    if stats["rows"]>MAX_ROWS:raise ScudError("Не более 150000 строк / En fazla 150000 satır")
    if any(cell.data_type=="f" for cell in selected):stats["invalid"]+=1;continue
    plate=bus_plate(selected[0].value)
    if not plate:stats["ignored"]+=1;continue
    card=card_number(selected[1]);stamp=parse_time(selected[2].value)
    if card is None or stamp is None:stats["invalid"]+=1;continue
    stats["valid_rows"]+=1;days.add(stamp.date().isoformat());records.add((plate,card,millis(stamp)))
 except ScudError:raise
 except Exception:raise ScudError("Неверная структура XLSX / Geçersiz XLSX yapısı") from None
 finally:workbook.close()
 if not recognized:raise ScudError("Нужны столбцы Kart No, Tarih и Okuyucu Ad (карта, время, автобус) / Kart No, Tarih ve Okuyucu Ad gerekli")
 if not records:raise ScudError("Нет корректных отметок автобусов / Geçerli otobüs kaydı yok")
 return dict(records=sorted(records),days=sorted(days),**stats)

def init_schema(connection):
 connection.execute("""CREATE TABLE IF NOT EXISTS scud_events(
  plate TEXT NOT NULL,card TEXT NOT NULL,stamp INTEGER NOT NULL,
  PRIMARY KEY(plate,card,stamp)) WITHOUT ROWID""")
 connection.execute("CREATE INDEX IF NOT EXISTS scud_events_bus_time ON scud_events(plate,stamp,card)")
 connection.execute("CREATE INDEX IF NOT EXISTS scud_events_time ON scud_events(stamp)")
 connection.execute("CREATE TABLE IF NOT EXISTS scud_days(day TEXT PRIMARY KEY,imported_at TEXT NOT NULL)")
 connection.execute("""CREATE TABLE IF NOT EXISTS scud_imports(
  id INTEGER PRIMARY KEY,imported_at TEXT NOT NULL,first_day TEXT NOT NULL,last_day TEXT NOT NULL,
  rows INTEGER NOT NULL,inserted INTEGER NOT NULL,duplicates INTEGER NOT NULL,invalid INTEGER NOT NULL,ignored INTEGER NOT NULL)""")

def save_import(connection,parsed,current):
 connection.execute("BEGIN IMMEDIATE")
 before=connection.total_changes
 connection.executemany("INSERT OR IGNORE INTO scud_events(plate,card,stamp) VALUES(?,?,?)",parsed["records"])
 inserted=connection.total_changes-before;imported=current.astimezone(TZ).isoformat()
 connection.executemany("INSERT INTO scud_days VALUES(?,?) ON CONFLICT(day) DO UPDATE SET imported_at=excluded.imported_at",[(day,imported) for day in parsed["days"]])
 result=dict(imported_at=imported,first_day=parsed["days"][0],last_day=parsed["days"][-1],rows=parsed["rows"],
             inserted=inserted,duplicates=parsed["valid_rows"]-inserted,invalid=parsed["invalid"],ignored=parsed["ignored"])
 connection.execute("INSERT INTO scud_imports(imported_at,first_day,last_day,rows,inserted,duplicates,invalid,ignored) VALUES(?,?,?,?,?,?,?,?)",tuple(result.values()))
 connection.commit()
 return result

def import_status(connection):
 return dict(records=connection.execute("SELECT COUNT(*) FROM scud_events").fetchone()[0],
             days=[dict(row) for row in connection.execute("SELECT day,imported_at FROM scud_days ORDER BY day DESC LIMIT 365")],
             imports=[dict(row) for row in connection.execute("SELECT * FROM scud_imports ORDER BY id DESC LIMIT 20")],
             window_before_minutes=BEFORE_MS//60000,window_after_minutes=AFTER_MS//60000)

def attach_scud(trips,connection,current):
 """Assign an event once before direction/date filtering; expose aggregates only."""
 loaded=set()
 exists=connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='scud_events'").fetchone()
 if exists:loaded={row[0] for row in connection.execute("SELECT day FROM scud_days")}
 by_bus=defaultdict(list);cards=defaultdict(set);ambiguous=defaultdict(set)
 now_stamp=millis(current)
 for trip in trips:
  trip.update(scud_count=None,scud_free=None,scud_ambiguous=0,scud_status="no_data")
  planned=datetime.fromisoformat(trip["planned_at"]);start=millis(planned)-BEFORE_MS;end=millis(planned)+AFTER_MS
  trip["scud_window_start"]=datetime.fromtimestamp(start/1000,TZ).isoformat()
  trip["scud_window_end"]=datetime.fromtimestamp(end/1000,TZ).isoformat()
  bus=bus_plate(trip.get("execution_vehicle")) or bus_plate(trip.get("vehicle"))
  if not bus:
   hints=trip.get("_hints",[])
   bus=bus_plate(hints[0]) if len(hints)==1 else None
  day=planned.date();needed={datetime.fromtimestamp(start/1000,TZ).date().isoformat(),datetime.fromtimestamp(end/1000,TZ).date().isoformat()}
  if start>now_stamp:trip["scud_status"]="pending";continue
  if not needed.intersection(loaded):continue
  if not bus:trip["scud_status"]="no_bus";continue
  trip["scud_status"]="ready" if needed<=loaded and end<=now_stamp else "partial"
  by_bus[bus].append((start,end,trip))
 if exists and by_bus:
  for bus,windows in by_bus.items():
   earliest=min(a for a,_,_ in windows);latest=min(now_stamp,max(b for _,b,_ in windows))
   for row in connection.execute("SELECT card,stamp FROM scud_events WHERE plate=? AND stamp BETWEEN ? AND ?",(bus,earliest,latest)):
    card,stamp=row;matches=[trip for a,b,trip in windows if a<=stamp<=b]
    if len(matches)==1:cards[matches[0]["key"]].add(card)
    elif matches:
     for trip in matches:ambiguous[trip["key"]].add(card)
 for trip in trips:
  if trip["scud_status"] not in ("ready","partial"):continue
  key=trip["key"];trip["scud_count"]=len(cards[key]);trip["scud_ambiguous"]=len(ambiguous[key])
  if ambiguous[key]:trip["scud_status"]="partial"
  if trip["scud_status"]=="ready":trip["scud_free"]=max(0,trip["capacity"]-trip["scud_count"])
