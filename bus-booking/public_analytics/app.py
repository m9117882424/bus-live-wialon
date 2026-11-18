from __future__ import annotations
import io,os,sqlite3
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI,HTTPException
from fastapi.responses import FileResponse,RedirectResponse,Response
from fastapi.staticfiles import StaticFiles
from openpyxl import Workbook
from openpyxl.styles import Alignment,Font,PatternFill
from openpyxl.utils import get_column_letter
from .data import TZ,build_report,default_period,load_trips,validate_period,quick_period
from .wialon import Collector
from .execution import EXECUTION_LABELS,EXECUTION_REASONS
DB=Path(os.environ.get('BUS_ANALYTICS_DB','bus.db'))
STATIC=Path(__file__).parent/'static'
collector=None
@asynccontextmanager
async def lifespan(app):
    global collector
    collector=Collector(os.environ.get('BUS_ANALYTICS_CACHE','analytics-cache.sqlite'),enabled=os.environ.get('BUS_ANALYTICS_COLLECTOR','1')=='1')
    yield
    collector.close()
app=FastAPI(lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
@app.middleware('http')
async def headers(request,call_next):
    response=await call_next(request)
    response.headers.update({'X-Content-Type-Options':'nosniff','X-Frame-Options':'DENY','Referrer-Policy':'no-referrer',
        'X-Robots-Tag':'noindex, nofollow','Cache-Control':'no-store'})
    return response
app.mount('/analytics/assets',StaticFiles(directory=STATIC),name='analytics-assets')
@app.get('/analytics',include_in_schema=False)
def slash():return RedirectResponse('/analytics/',status_code=308)
@app.get('/analytics/')
def page():return FileResponse(STATIC/'index.html',media_type='text/html')
@app.get('/analytics/logo.png')
def logo():return FileResponse(Path(__file__).resolve().parents[1]/'static'/'tsm-logo.png',media_type='image/png')
@app.get('/analytics/api/health')
def health():
    try:
        start,end=default_period(DB);load_trips(DB,start,end)
    except (sqlite3.Error,ValueError,OSError):raise HTTPException(503,'Источник бронирований временно недоступен.') from None
    return {'ok':True}
def report(start=None,end=None,direction='all',preset=None):
    try:
        current=datetime.now(TZ);defaults=default_period(DB,current);start=start or defaults[0];end=end or defaults[1]
        if preset is not None:start,end=quick_period(preset,current)
        validate_period(start,end,current)
        if direction not in ('all','forward','reverse'):raise ValueError('Выберите направление из списка.')
        trips,fleet=load_trips(DB,start,end,current)
        if not trips:fleet=[]
        events,readiness,metadata,telemetry=collector.request(start,end,fleet,current,{r['date'] for r in trips})
        result=build_report(DB,start,end,direction,events,readiness,current,telemetry,fleet);result['wialon']=metadata
        return result
    except ValueError as error:raise HTTPException(400,str(error)) from None
    except (sqlite3.Error,OSError):raise HTTPException(503,'Источник бронирований временно недоступен.') from None
@app.get('/analytics/api/report')
def get_report(start:str|None=None,end:str|None=None,direction:str='all',preset:str|None=None):return report(start,end,direction,preset)
STATUS={'planned':'Предстоящий','on_time':'Вовремя','early':'Рано','late':'С опозданием','unknown':'Нет подтверждённого выезда'}
REASONS={'not_due':'Рейс ещё не начался','wialon_loading':'Загрузка Wialon','wialon_error':'Wialon временно недоступен',
    'no_exit':'Нет достоверного выхода из геозоны','ambiguous':'Несколько подходящих рейсов или автобусов','wialon_zone_exit':'Подтверждённый выход Wialon'}
def safe(v):return "'"+v if isinstance(v,str) and v.startswith(('=','+','-','@')) else v
def excel(result):
    wb=Workbook();s=wb.active;s.title='Сводка'
    s.append(['Аналитика автобусов — Камп ↔ ШОКмаркет'])
    s.append(['Период',result['period']['start'],result['period']['end']])
    s.append(['Сформировано (Турция)',result['generated_at']])
    s.append(['Wialon обновлён (Турция)',result['wialon']['last_updated'] or 'Нет данных'])
    headings=['Направление','Рейсы','Брони','Посадка подтверждена','Посадка не подтверждена','Вместимость','Свободно','Ранних','Опозданий','Вовремя','Выезд не определён','Предстоящих','Занято по броням, %','Состоялись','Не состоялись','В пути','Ожидают проверки','Недостаточно GPS','Неоднозначно']
    s.append(headings)
    def values(t):return [t[k] for k in ('trips','bookings','confirmed','unconfirmed','capacity','free','early','late','on_time','unknown','planned','occupancy_percent','performed','not_performed','in_progress','execution_pending','execution_unknown','execution_ambiguous')]
    for g in result['directions']:s.append([g['label']]+values(g['totals']))
    s.append(['Итого']+values(result['totals']))
    days=wb.create_sheet('По датам');days.append(['Дата / направление']+headings[1:])
    for day in result['days']:
        days.append([datetime.fromisoformat(day['date'])]+values(day['totals']))
        days.cell(days.max_row,1).number_format='DD.MM.YYYY'
        for g in day['directions']:
            days.append([g['label']]+values(g['totals']));days.row_dimensions[days.max_row].outlineLevel=1
    trips=wb.create_sheet('Рейсы')
    trips.append(['Дата','Направление','Рейс','Автобус','Откуда','Куда','План','Факт Wialon','Отклонение, мин','Статус','Пояснение','Вместимость','Брони','Посадка подтверждена','Посадка не подтверждена','Свободно','Факт рейса','Прибытие на КПП','В пути, мин','Проверка до','Пояснение проверки'])
    for r in result['trips']:
        trips.append([datetime.fromisoformat(r['date']),'В ШОКмаркет' if r['direction']=='forward' else 'В Камп',safe(r['name']),
            r['vehicle'],safe(r['origin']),safe(r['destination']),datetime.fromisoformat(r['planned_at']).replace(tzinfo=None),
            datetime.fromisoformat(r['actual_at']).replace(tzinfo=None) if r['actual_at'] else None,r['delta_minutes'],
            STATUS[r['status']],REASONS[r['reason']],r['capacity'],r['bookings'],r['confirmed'],r['unconfirmed'],r['free'],EXECUTION_LABELS[r['execution_status']],
            datetime.fromisoformat(r['arrival_at']).replace(tzinfo=None) if r['arrival_at'] else None,r['duration_minutes'],
            datetime.fromisoformat(r['execution_deadline']).replace(tzinfo=None),EXECUTION_REASONS[r['execution_reason']]])
        for col,fmt in [(1,'DD.MM.YYYY'),(7,'HH:MM'),(8,'HH:MM:SS'),(9,'0.0'),(18,'DD.MM.YYYY HH:MM:SS'),(19,'0.0'),(20,'DD.MM.YYYY HH:MM:SS')]:trips.cell(trips.max_row,col).number_format=fmt
    rules=wb.create_sheet('Правила');rules.append(['Правила расчёта'])
    for note in result['notes']:rules.append([note])
    rules.append(['В ШОКмаркет: выход из геозоны «İspanyol kamp / Испанский камп».'])
    rules.append(['В Камп: выход из геозоны «Шок маркет Бюкеджели».'])
    rules.append(['Окно сопоставления: ±45 минут. Один выход не используется повторно.'])
    rules.append(['Привязанный к рейсу автобус имеет приоритет. Неоднозначные совпадения не считаются выездом вовремя.'])
    rules.append(['Факт рейса: начальная остановка → геозона «8 - КПП АККУЮ» тем же автобусом. На КПП пассажиры пересаживаются; прибытие этого автобуса в конечную остановку не требуется.'])
    rules.append(['Нет GPS или неоднозначность не означают несостоявшийся рейс. На стоянке допускается интервал до 10 минут при смещении не более 20 м.'])
    rules.append(['Персональные данные пассажиров в отчёт не включены.'])
    for sheet,header in [(s,5),(days,1),(trips,1),(rules,1)]:
        sheet.freeze_panes=f'B{header+1}';sheet.sheet_view.showGridLines=False
        for cell in sheet[header]:
            cell.fill=PatternFill('solid',fgColor='243449');cell.font=Font(color='FFFFFF',bold=True)
            cell.alignment=Alignment(wrap_text=True,vertical='center')
        sheet.row_dimensions[header].height=34
        if sheet!=rules:sheet.auto_filter.ref=f'A{header}:{get_column_letter(sheet.max_column)}{sheet.max_row}'
        for col in sheet.columns:sheet.column_dimensions[get_column_letter(col[0].column)].width=min(42,max(13,max(len(str(c.value or '')) for c in col)+2))
        sheet.sheet_properties.pageSetUpPr.fitToPage=True
        sheet.page_setup.orientation='landscape';sheet.page_setup.paperSize=sheet.PAPERSIZE_A3
        sheet.page_setup.fitToWidth=1;sheet.page_setup.fitToHeight=0
    rules.column_dimensions['A'].width=115
    for row in rules:row[0].alignment=Alignment(wrap_text=True);rules.row_dimensions[row[0].row].height=31
    out=io.BytesIO();wb.save(out);return out.getvalue()
@app.get('/analytics/api/export.xlsx')
def export(start:str|None=None,end:str|None=None,direction:str='all',preset:str|None=None):
    r=report(start,end,direction,preset);name=f'bus-analytics-{r["period"]["start"]}-{r["period"]["end"]}.xlsx'
    return Response(excel(r),media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Content-Disposition':f'attachment; filename="{name}"'})
