"""Passenger schedule hides trips 30 minutes after departure, including midnight."""
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo
import os,sys,tempfile
from cryptography.fernet import Fernet

ROOT=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='bus-visibility-') as temp:
 os.environ.update(BUS_DB=str(Path(temp)/'data.sqlite'),BUS_ADMIN_PASSWORD='test-only-password',BUS_FERNET_KEY=Fernet.generate_key().decode())
 sys.path.insert(0,str(ROOT))
 import app
 app.init()
 with app.db() as c:
  for name,departure,service_date in [('Boundary','18:00','2026-11-12'),('Recent','18:01','2026-11-12'),('Future','18:31','2026-11-12'),('Late','23:50','2026-11-12')]:
   c.execute("INSERT INTO routes(name,origin,destination,forward_time,reverse_time,capacity,active,direction,service_date) VALUES(?,?,?,?,?,50,1,'forward',?)",(name,'Камп','Магазин',departure,departure,service_date))
  c.commit()
 tz=ZoneInfo('Europe/Istanbul')
 with patch.object(app,'now',return_value=datetime(2026,11,12,18,29,59,tzinfo=tz)):
  items=app.trips('2026-11-12','forward');by_name={x['name']:x for x in items}
  assert by_name['Boundary']['past'] and 1000<=by_name['Boundary']['refresh_in_ms']<=1500
  assert not by_name['Future']['past']
 with patch.object(app,'now',return_value=datetime(2026,11,12,18,30,0,tzinfo=tz)):
  items=app.trips('2026-11-12','forward');by_name={x['name']:x for x in items}
  assert 'Boundary' not in by_name and by_name['Recent']['past'] and not by_name['Future']['past']
  assert any(x['name']=='Boundary' for x in app.admin_routes())
 with patch.object(app,'now',return_value=datetime(2026,11,13,0,10,0,tzinfo=tz)):
  assert [x['name'] for x in app.trips('2026-11-12','forward')]==['Late']
 with patch.object(app,'now',return_value=datetime(2026,11,13,0,20,0,tzinfo=tz)):
  assert app.trips('2026-11-12','forward')==[]
 print('PASS: departure, 30-minute boundary, midnight and retained admin schedule')
