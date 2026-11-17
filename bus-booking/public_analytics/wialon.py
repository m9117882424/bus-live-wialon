"""Reuse the existing BDD bus Wialon client and geometry, with an isolated session and cache."""
from __future__ import annotations
import importlib.util,json,math,os,queue,sqlite3,sys,threading,time
from contextlib import contextmanager
from datetime import date,datetime,timedelta
from pathlib import Path
from .data import TZ
ZONE_IDS={'forward':(600894911,30),'reverse':(601276657,12)}
ZONE_LABELS={'forward':'İspanyol kamp / Испанский камп','reverse':'Шок маркет Бюкеджели'}
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
    if set(zones)!=set(ZONE_IDS):raise ValueError('Configured departure geofences are missing')
    return zones
def detect_exits(points,zones,day,plate):
    events=[]
    midnight=datetime.combine(date.fromisoformat(day),datetime.min.time(),TZ).timestamp()
    for direction,zone in zones.items():
        inside_since=last_inside=first_out=previous=None
        for stamp,(lat,lon) in points:
            if previous is not None and stamp-previous>180:inside_since=last_inside=first_out=None
            previous=stamp
            if zone.contains(lat,lon):
                if inside_since is None:inside_since=stamp
                last_inside=stamp;first_out=None;continue
            if last_inside is None:continue
            if first_out is None:
                if last_inside-inside_since<20:inside_since=last_inside=None
                else:first_out=stamp
                continue
            if stamp-first_out<20:continue
            if midnight-2700<=first_out<=midnight+86400+2700:
                opposite=zones['reverse' if direction=='forward' else 'forward']
                arrived=next((t for t,(y,x) in points if first_out<t<=first_out+5400 and opposite.contains(y,x)),None)
                events.append(dict(date=day,plate=plate,direction=direction,stamp=first_out,arrived_at=arrived))
            inside_since=last_inside=first_out=None
    return events
class Collector:
    def __init__(self,path,enabled=True):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connection() as c:
            c.execute('PRAGMA journal_mode=WAL')
            c.execute('CREATE TABLE IF NOT EXISTS tracks(day TEXT,plate TEXT,updated REAL,status TEXT,events TEXT,PRIMARY KEY(day,plate))')
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
    def request(self,start,end,plates,current=None):
        current=current or datetime.now(TZ);last=min(date.fromisoformat(end),current.date());first=date.fromisoformat(start)
        with self.connection() as c:
            cache={(r[0],r[1]):r for r in c.execute('SELECT day,plate,updated,status,events FROM tracks WHERE day BETWEEN ? AND ?',(start,end))}
        events=[];readiness={};updates=[];days=[]
        while first<=last:days.append(first.isoformat());first+=timedelta(days=1)
        for day in reversed(days):
            states=[]
            for bus in plates:
                row=cache.get((day,bus));states.append(row[3] if row else 'loading')
                if row:events.extend(json.loads(row[4]));updates.append(row[2])
                ttl=300 if day>=str(current.date()-timedelta(days=1)) else 21600
                if row and row[3]=='error':ttl=60
                if self.enabled and (not row or time.time()-row[2]>ttl):
                    key=(day,bus)
                    with self.lock:
                        if key not in self.pending:
                            try:self.jobs.put_nowait(key);self.pending.add(key)
                            except queue.Full:pass
            readiness[day]='loading' if plates and (not states or 'loading' in states) else 'error' if 'error' in states else 'ready'
        return events,readiness,dict(enabled=self.enabled,pending_days=sum(s=='loading' for s in readiness.values()),
            error_days=sum(s=='error' for s in readiness.values()),fleet_count=len(plates),
            last_updated=datetime.fromtimestamp(max(updates),TZ).isoformat() if updates else None,geofences=ZONE_LABELS)
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
            return detect_exits(client.positions(units[0],start,end),self.zones,day,plate)
        finally:client.close()
    def worker(self):
        while not self.stopped.is_set():
            try:day,plate=self.jobs.get(timeout=1)
            except queue.Empty:continue
            try:
                try:events=self.fetch(day,plate);state='ready'
                except Exception:
                    # No raw API replies, tokens or coordinates in the public cache.
                    events=[];state='error'
                with self.connection() as c:
                    c.execute('INSERT OR REPLACE INTO tracks VALUES(?,?,?,?,?)',(day,plate,time.time(),state,json.dumps(events,separators=(',',':'))))
            finally:
                with self.lock:self.pending.discard((day,plate))
                self.jobs.task_done()
    def close(self):self.stopped.set()
