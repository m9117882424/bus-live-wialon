from __future__ import annotations
import re,sqlite3
from collections import defaultdict
from contextlib import contextmanager
from datetime import date,datetime,time,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
from .execution import attach_execution
TZ=ZoneInfo('Europe/Istanbul')
DIRECTIONS={'forward':'В ШОКмаркет','reverse':'В Камп'}
PLATE=re.compile(r'(?<![A-Z0-9])(\d{2})[ ._-]*([A-Z]{1,3})[ ._-]*(\d{2,4})(?![A-Z0-9])',re.ASCII)
def plate(v):
    m=PLATE.fullmatch(str(v or '').strip().upper())
    return ''.join(m.groups()) if m else None
def plate_hints(v):return sorted({''.join(m.groups()) for m in PLATE.finditer(str(v or '').upper())})
@contextmanager
def connect(path):
    c=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True,timeout=3)
    c.row_factory=sqlite3.Row;c.execute('PRAGMA query_only=ON')
    try:yield c
    finally:c.close()
def validate_period(start,end,current=None):
    current=current or datetime.now(TZ)
    try:
        if not all(isinstance(v,str) and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',v) for v in (start,end)):raise ValueError
        a,b=date.fromisoformat(start),date.fromisoformat(end)
    except (ValueError,TypeError):raise ValueError('Укажите даты в формате ГГГГ-ММ-ДД.') from None
    if a>b:raise ValueError('Начало периода должно быть не позже окончания.')
    if (b-a).days>30:raise ValueError('Выберите период не более 31 дня.')
    if a<date(2020,1,1) or b>current.date()+timedelta(days=730):raise ValueError('Период вне допустимого диапазона.')
    return a,b
def quick_period(preset,current=None):
    current=(current or datetime.now(TZ)).astimezone(TZ)
    end=current.date()
    if preset=='yesterday':end-=timedelta(days=1)
    elif preset not in ('today','7days'):raise ValueError('Выберите период: сегодня, вчера или 7 дней.')
    start=end-timedelta(days=6) if preset=='7days' else end
    return start.isoformat(),end.isoformat()
def default_period(path,current=None):
    current=current or datetime.now(TZ)
    with connect(path) as c:row=c.execute('SELECT MIN(starts_on) FROM route_service_periods').fetchone()
    start=current.date()-timedelta(days=6)
    if row and row[0] and date.fromisoformat(row[0])<=current.date():start=max(start,date.fromisoformat(row[0]))
    return start.isoformat(),current.date().isoformat()
def load_trips(path,start,end,current=None,*,pad=False):
    current=current or datetime.now(TZ);first,last=validate_period(start,end,current)
    if pad:
        first-=timedelta(days=1);last+=timedelta(days=1);start,end=first.isoformat(),last.isoformat()
    with connect(path) as c:
        c.execute('BEGIN')
        routes=[dict(r) for r in c.execute('SELECT r.*,p.starts_on,p.ends_on FROM routes r LEFT JOIN route_service_periods p ON p.route_id=r.id')]
        observed=[dict(r) for r in c.execute('''SELECT l.route_id,l.direction,l.date,l.departure,MAX(l.origin) origin,MAX(l.destination) destination,MAX(l.route_name) route_name,
            MAX(l.capacity) capacity,COUNT(*) bookings,COUNT(bd.leg_id) confirmed,
            GROUP_CONCAT(DISTINCT bd.driver_login) driver_logins,MAX(l.comment) comment
            FROM legs l JOIN bookings b ON b.id=l.booking_id LEFT JOIN boardings bd ON bd.leg_id=l.id
            WHERE l.date BETWEEN ? AND ? AND b.cancelled_at IS NULL
            GROUP BY l.route_id,l.direction,l.date,l.departure''',(start,end))]
        fleet={p for r in c.execute('SELECT login FROM driver_accounts') if (p:=plate(r[0]))}
    found={}
    def make(rid,direction,day,dep,name,origin,destination,capacity,hints,inferred):
        key=(rid,direction,day,dep[:5])
        return key,dict(key=':'.join(map(str,key)),route_id=rid,direction=direction,date=day,departure=dep[:5],
            name=name,origin=origin,destination=destination,capacity=int(capacity),bookings=0,confirmed=0,
            unconfirmed=0,free=int(capacity),planned_at=datetime.combine(date.fromisoformat(day),time.fromisoformat(dep),TZ).isoformat(),
            _hints=hints,schedule_inferred=inferred)
    day=first
    while day<=last:
        value=day.isoformat()
        for r in routes:
            if not r['active'] or r['deleted_at']:continue
            if r['service_date'] and r['service_date']!=value:continue
            if not int(r['weekday_mask'])&(1<<day.weekday()):continue
            if r['starts_on'] and not r['starts_on']<=value<=r['ends_on']:continue
            for direction in ('forward','reverse') if r['direction']=='both' else (r['direction'],):
                if direction not in DIRECTIONS:continue
                origin,destination=r['origin'],r['destination']
                if direction=='reverse' and r['direction']=='both':origin,destination=destination,origin
                key,item=make(r['id'],direction,value,r['forward_time'] if direction=='forward' else r['reverse_time'],
                    r['name'],origin,destination,r['capacity'],plate_hints(r['comment']),day<current.date())
                found[key]=item
        day+=timedelta(days=1)
    for r in observed:
        logins=[p for s in (r['driver_logins'] or '').split(',') if (p:=plate(s))]
        key,item=make(r['route_id'],r['direction'],r['date'],r['departure'],r['route_name'],r['origin'],r['destination'],
            r['capacity'],sorted(set(logins)) or plate_hints(r['comment']),False)
        if key in found and r['date']>=current.date().isoformat():
            item['capacity']=found[key]['capacity']
            if not item['_hints']:item['_hints']=found[key]['_hints']
        item.update(bookings=r['bookings'],confirmed=r['confirmed'],unconfirmed=r['bookings']-r['confirmed'],
            free=max(0,item['capacity']-r['bookings']))
        found[key]=item;fleet.update(item['_hints'])
    return sorted(found.values(),key=lambda r:(r['date'],r['departure'],r['direction'],r['route_id'])),sorted(fleet)
def attach_departures(trips,events,readiness,current=None):
    current=current or datetime.now(TZ);eligible=defaultdict(list)
    unique={(e['plate'],e['direction'],e['stamp']):e for e in events}
    for event in unique.values():
        candidates=[]
        for r in trips:
            if r['direction']!=event['direction']:continue
            planned=datetime.fromisoformat(r['planned_at']).timestamp()
            if planned>current.timestamp() or abs(event['stamp']-planned)>2700:continue
            hints=r['_hints']
            if hints and (len(hints)!=1 or event['plate'] not in hints):continue
            candidates.append((0 if hints else 1,abs(event['stamp']-planned),r))
        if candidates:
            best=min((x[0],x[1]) for x in candidates);nearest=[r for priority,distance,r in candidates if (priority,distance)==best]
            for r in nearest:eligible[r['key']].append((event,len(nearest)==1))
    for r in trips:
        planned=datetime.fromisoformat(r['planned_at'])
        r.update(actual_at=None,delta_minutes=None,vehicle=', '.join(r['_hints']),status='unknown',reason='no_exit')
        if planned>current:
            r.update(status='planned',reason='not_due');continue
        if readiness.get(r['date'],'loading')!='ready':
            r['reason']='wialon_error' if readiness.get(r['date'])=='error' else 'wialon_loading';continue
        matches=eligible[r['key']]
        if len(matches)==1 and matches[0][1]:
            event,_=matches[0];actual=datetime.fromtimestamp(event['stamp'],TZ)
            delta=round((actual-planned).total_seconds()/60,1)
            r.update(actual_at=actual.isoformat(),delta_minutes=delta,vehicle=event['plate'],
                status='late' if delta>0 else 'early' if delta<0 else 'on_time',reason='wialon_zone_exit')
        elif matches or len(r['_hints'])>1:r['reason']='ambiguous'
def totals(rows):
    result={k:sum(r[k] for r in rows) for k in ('bookings','confirmed','unconfirmed','capacity','free')}
    result.update(trips=len(rows),forward=sum(r['direction']=='forward' for r in rows),reverse=sum(r['direction']=='reverse' for r in rows))
    for state in ('early','late','on_time','planned','unknown'):result[state]=sum(r['status']==state for r in rows)
    for name,state in [('performed','performed'),('not_performed','not_performed'),('in_progress','in_progress'),('execution_unknown','unknown'),('execution_ambiguous','ambiguous')]:
        result[name]=sum(r.get('execution_status')==state for r in rows)
    result['execution_pending']=sum(r.get('execution_status') in ('planned','checking') for r in rows)
    result['occupancy_percent']=round(100*result['bookings']/result['capacity'],1) if result['capacity'] else None
    late=[r['delta_minutes'] for r in rows if r['status']=='late'];early=[r['delta_minutes'] for r in rows if r['status']=='early']
    result['average_late_minutes']=round(sum(late)/len(late),1) if late else None
    result['max_early_minutes']=abs(min(early)) if early else None
    return result
def build_report(path,start,end,direction,events,readiness,current=None,telemetry=None,fleet=None):
    current=current or datetime.now(TZ)
    if direction not in ('all','forward','reverse'):raise ValueError('Выберите направление из списка.')
    trips,_=load_trips(path,start,end,current,pad=True);attach_departures(trips,events,readiness,current)
    if fleet is None:_,fleet=load_trips(path,start,end,current)
    attach_execution(trips,events,readiness,telemetry,fleet,current)
    filtered=[r for r in trips if start<=r['date']<=end and (direction=='all' or r['direction']==direction)]
    for r in filtered:r.pop('_hints',None)
    days=[]
    for day in sorted({r['date'] for r in filtered},reverse=True):
        rows=[r for r in filtered if r['date']==day]
        groups=[dict(direction=d,label=DIRECTIONS[d],totals=totals([r for r in rows if r['direction']==d]),
            trips=[r for r in rows if r['direction']==d]) for d in DIRECTIONS if any(r['direction']==d for r in rows)]
        days.append(dict(date=day,totals=totals(rows),directions=groups))
    return dict(generated_at=current.isoformat(),timezone='Europe/Istanbul',period=dict(start=start,end=end,direction=direction),
        totals=totals(filtered),days=days,trips=filtered,
        directions=[dict(direction=d,label=DIRECTIONS[d],totals=totals([r for r in filtered if r['direction']==d])) for d in DIRECTIONS],
        notes=['Брони считаются по каждому плечу; отменённые исключены. Посадки — отметки водителей.',
        'Свободно = вместимость − действующие брони. Для будущих рейсов посадка ещё может быть не отмечена.',
        'Неоднозначные и отсутствующие данные Wialon не считаются выездом вовремя.',
        'Время выезда — первая точка вне зоны, подтверждённая следующей. Разрыв более 3 минут в движении или более 10 минут на стоянке прерывает подтверждение.',
        'Рейс состоялся: тот же автобус выехал из исходной зоны и подтверждён в зоне назначения. Окно выезда ±45 минут, дорога до 90 минут.',
        'Не состоялся: окно закрыто, ни одного полного проезда не найдено и GPS-покрытие всех автобусов достаточно. При разрывах GPS выводится отдельный статус.',
        'На стоянке допускается до 10 минут между GPS-точками в пределах 20 м; в движении — до 3 минут. Это допущение используется при оценке покрытия.',
        'Пустые исторические рейсы восстановлены по действующему расписанию; изменения старого расписания без броней не сохранены.'])
