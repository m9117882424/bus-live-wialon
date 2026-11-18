"""Reuse the existing BDD bus Wialon client and geometry, with an isolated session and cache."""
from __future__ import annotations
import importlib.util,json,math,os,queue,sqlite3,sys,threading,time
from contextlib import contextmanager
from datetime import date,datetime,timedelta
from pathlib import Path
from .data import TZ
from .execution import DEPARTURE_WINDOW, MAX_TRAVEL, MAX_GPS_GAP, MAX_IDLE_GAP, MIN_ZONE_SECONDS, GPS_VERSION
# '8' is the display prefix; the verified Wialon zone ID is 55 in resource 600148718.
ZONE_IDS={'forward':(600894911,30),'reverse':(601276657,12),'kpp':(600148718,55)}
ZONE_LABELS={'forward':'İspanyol kamp / Испанский камп','reverse':'Шок маркет Бюкеджели','kpp':'8 - КПП АККУЮ'}
def load_adapter(path=None):
    path=Path(path or os.environ.get('BUS_WIALON_ADAPTER','/opt/bdd-bus-report/current/geo_api.py'))
    spec=importlib.util.spec_from_file_location('_booking_shared_wialon_geo',path)
    if not spec or not spec.loader:raise RuntimeError('Shared Wialon adapter unavailable')
    m=importlib.util.module_from_spec(spec);sys.modules[spec.name]=m;spec.loader.exec_module(m)
    return m
def select_zones(adapter,raw):
    zones={}
    for resource,z in raw:
        direction=next((k for k,v in ZONE_IDS.items() if v==(resource,z['id'])),None)
        if not direction:continue
        pts=z.get('p') or []
        if not pts or len(pts)>5000 or any(not isinstance(p,dict) or not adapter.valid_coordinates(p.get('y'),p.get('x')) for p in pts):
            raise ValueError('Invalid configured geofence')
        coords=tuple((p['y'],p['x']) for p in pts);radius=0
        if z.get('t')==3 and len(coords)==1:
            radius=pts[0].get('r') or z.get('w')
            if not isinstance(radius,(float,int)) or not 0<radius<=30000:raise ValueError('Invalid geofence radius')
            lat,lon=coords[0];a=radius/111000;b=a/max(.01,math.cos(math.radians(abs(lat)+a)))
            bounds=(lat-a,lat+a,lon-b,lon+b)
        elif z.get('t')==2 and len(coords)>=3:
            lat,lon=zip(*coords);bounds=(min(lat),max(lat),min(lon),max(lon))
        else:raise ValueError('Unsupported configured geofence')
        zones[direction]=adapter.Zone('stop',z['n'],bounds,coords,radius)
    if set(zones)!=set(ZONE_IDS):raise ValueError('Required origin or KPP geofence is missing')
    return zones

def continuous_edge(previous, point):
    gap=point[0]-previous[0]
    if 0<gap<=MAX_GPS_GAP:return True
    if gap<=0 or gap>MAX_IDLE_GAP:return False
    a,b=previous[1],point[1]
    distance=math.hypot((a[0]-b[0])*111000,(a[1]-b[1])*111000*math.cos(math.radians((a[0]+b[0])/2)))
    return distance<=20

def track_evidence(points,zones,day,plate,start=None,end=None):
    points=sorted(dict(points).items())
    projected=[];coverage=[]
    for i,point in enumerate(points):
        stamp,(lat,lon)=point
        continuous=bool(i and continuous_edge(points[i-1],point))
        mask=int(zones['forward'].contains(lat,lon))+2*int(zones['reverse'].contains(lat,lon))+4*int(zones['kpp'].contains(lat,lon))
        projected.append((stamp,mask,continuous))
        if continuous:coverage[-1][1]=stamp
        else:coverage.append([stamp,stamp])
    events=[]
    midnight=datetime.combine(date.fromisoformat(day),datetime.min.time(),TZ).timestamp()
    for direction,origin_bit,destination_bit in [('forward',1,4),('reverse',2,4)]:
        inside_since=last_inside=first_out=None
        first_index=None
        for i,(stamp,mask,continuous) in enumerate(projected):
            if i and not continuous:inside_since=last_inside=first_out=first_index=None
            if mask&origin_bit:
                if inside_since is None:inside_since=stamp
                last_inside=stamp;first_out=first_index=None;continue
            if last_inside is None:continue
            if first_out is None:
                if last_inside-inside_since<MIN_ZONE_SECONDS:inside_since=last_inside=None
                else:first_out=stamp;first_index=i
                continue
            if stamp-first_out<MIN_ZONE_SECONDS:continue
            if midnight-DEPARTURE_WINDOW<=first_out<=midnight+86400+DEPARTURE_WINDOW:
                arrived=confirmed=None
                for t,state,edge in projected[first_index:]:
                    if not edge or state&origin_bit:break
                    if arrived is None and t>first_out+MAX_TRAVEL:break
                    if state&destination_bit:
                        if arrived is None:arrived=t
                        if t-arrived>=MIN_ZONE_SECONDS:
                            confirmed=t;break
                    else:
                        arrived=None
                if confirmed is None:arrived=None
                events.append(dict(date=day,plate=plate,direction=direction,stamp=first_out,
                    arrived_at=arrived,arrival_confirmed_at=confirmed,route_complete=confirmed is not None))
            inside_since=last_inside=first_out=first_index=None
    return dict(version=GPS_VERSION,events=events,coverage=coverage,point_count=len(points),
        query_from=start if start is not None else points[0][0] if points else None,
        query_to=end if end is not None else points[-1][0] if points else None,
        collected_at=time.time())

def detect_exits(points,zones,day,plate):
    return track_evidence(points,zones,day,plate)['events']

class Collector:
    def __init__(self,path,enabled=True):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connection() as c:
            c.execute('PRAGMA journal_mode=WAL')
            # Separate derived cache preserves safe rollback to the previous collector.
            c.execute('CREATE TABLE IF NOT EXISTS gps_tracks_v3(day TEXT,plate TEXT,updated REAL,status TEXT,payload TEXT,PRIMARY KEY(day,plate))')
        self.enabled=enabled;self.pending=set();self.lock=threading.Lock();self.jobs=queue.Queue(maxsize=256)
        self.stopped=threading.Event();self.adapter=None;self.catalog_at=0;self.units={};self.zones={}
        if enabled:
            self.thread=threading.Thread(target=self.worker,name='bus-analytics-wialon',daemon=True);self.thread.start()
    @contextmanager
    def connection(self):
        c=sqlite3.connect(self.path,timeout=3)
        try:
            with c:yield c
        finally:c.close()
    def request(self,start,end,plates,current=None,active_days=None):
        current=current or datetime.now(TZ);last=min(date.fromisoformat(end),current.date());first=date.fromisoformat(start)
        with self.connection() as c:
            cache={(r[0],r[1]):r for r in c.execute('SELECT day,plate,updated,status,payload FROM gps_tracks_v3 WHERE day BETWEEN ? AND ?',(start,end))}
        events=[];readiness={};telemetry={};updates=[];days=[]
        while first<=last:
            if active_days is None or first.isoformat() in active_days:days.append(first.isoformat())
            first+=timedelta(days=1)
        for day in reversed(days):
            states=[]
            for bus in plates:
                row=cache.get((day,bus))
                try:payload=json.loads(row[4]) if row else None
                except (TypeError,ValueError):payload=None
                if payload is not None and (not isinstance(payload,dict) or payload.get('version')!=GPS_VERSION):payload=None
                state=row[3] if row and payload else 'loading'
                states.append(state)
                if payload:
                    events.extend(payload.get('events',[]))
                    telemetry[(day,bus)]={**payload,'status':state}
                    if payload.get('collected_at'):updates.append(payload['collected_at'])
                else:telemetry[(day,bus)]={'status':'loading','version':GPS_VERSION}
                ttl=300 if day>=str(current.date()-timedelta(days=1)) else 21600
                if row and row[3]=='error':ttl=60
                if self.enabled and (not row or payload is None or time.time()-row[2]>ttl):
                    key=(day,bus)
                    with self.lock:
                        if key not in self.pending:
                            try:self.jobs.put_nowait(key);self.pending.add(key)
                            except queue.Full:pass
            readiness[day]='loading' if plates and (not states or 'loading' in states) else 'error' if 'error' in states else 'ready'
        metadata=dict(enabled=self.enabled,pending_days=sum(s=='loading' for s in readiness.values()),
            error_days=sum(s=='error' for s in readiness.values()),fleet_count=len(plates),
            last_updated=datetime.fromtimestamp(max(updates),TZ).isoformat() if updates else None,geofences=ZONE_LABELS,
            departure_window_minutes=DEPARTURE_WINDOW//60,max_journey_minutes=MAX_TRAVEL//60,
            gps_gap_seconds=MAX_GPS_GAP,stationary_gap_seconds=MAX_IDLE_GAP)
        return events,readiness,metadata,telemetry
    def refresh_catalog(self,client):
        if time.time()-self.catalog_at>600:
            units=client.units();zones=select_zones(self.adapter,list(client.zones()))
            self.units=units;self.zones=zones;self.catalog_at=time.time()
    def fetch(self,day,plate):
        if self.adapter is None:self.adapter=load_adapter()
        client=self.adapter.WialonClient()
        try:
            client.login();self.refresh_catalog(client);units=self.units.get(plate,[])
            if len(units)!=1:raise ValueError('Missing or duplicate bus unit')
            midnight=datetime.combine(date.fromisoformat(day),datetime.min.time(),TZ)
            start=int((midnight-timedelta(hours=1)).timestamp())
            end=min(int((midnight+timedelta(days=1,hours=3)).timestamp()),int(time.time()))
            return track_evidence(client.positions(units[0],start,end),self.zones,day,plate,start,end)
        finally:client.close()
    def worker(self):
        while not self.stopped.is_set():
            try:day,plate=self.jobs.get(timeout=1)
            except queue.Empty:continue
            try:
                try:payload=self.fetch(day,plate);state='ready'
                except Exception:
                    state='error'
                    with self.connection() as c:
                        old=c.execute('SELECT payload FROM gps_tracks_v3 WHERE day=? AND plate=?',(day,plate)).fetchone()
                    try:payload=json.loads(old[0]) if old else None
                    except (TypeError,ValueError):payload=None
                    if not isinstance(payload,dict) or payload.get('version')!=GPS_VERSION:
                        payload=dict(version=GPS_VERSION,events=[],coverage=[])
                with self.connection() as c:
                    c.execute('INSERT OR REPLACE INTO gps_tracks_v3 VALUES(?,?,?,?,?)',(day,plate,time.time(),state,json.dumps(payload,separators=(',',':'))))
            finally:
                with self.lock:self.pending.discard((day,plate))
                self.jobs.task_done()
    def close(self):self.stopped.set()
