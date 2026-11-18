(() => {
'use strict';
const $=id=>document.getElementById(id);
const fmt=new Intl.NumberFormat('ru-RU',{maximumFractionDigits:1});
const n=v=>v===null||v===undefined?'—':fmt.format(v);
const escape=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const dateLabel=v=>new Date(v+'T12:00:00Z').toLocaleDateString('ru-RU',{timeZone:'Europe/Istanbul',day:'numeric',month:'long'});
const timeLabel=(v,seconds=false)=>v?new Date(v).toLocaleTimeString('ru-RU',{timeZone:'Europe/Istanbul',hour:'2-digit',minute:'2-digit',second:seconds?'2-digit':undefined}):'—';
let latest=null,controller=null,requestId=0,timer=null,activePreset=null;
const openDates=new Set(),closedGroups=new Set();
const reasons={not_due:'Рейс ещё не начался',wialon_loading:'Загрузка Wialon',wialon_error:'Wialon временно недоступен',no_exit:'Нет достоверного выхода из геозоны',ambiguous:'Несколько подходящих рейсов или автобусов'};
function status(r){
 let label=r.status==='early'?'−'+n(-r.delta_minutes)+' мин · рано':r.status==='late'?'+'+n(r.delta_minutes)+' мин · поздно':r.status==='on_time'?'Вовремя':r.status==='planned'?'Предстоящий':r.reason==='wialon_loading'?'Загрузка…':r.reason==='ambiguous'?'Неоднозначно':'Нет данных';
 return '<span class="pill '+r.status+'" title="'+escape(reasons[r.reason]||'Подтверждённый выход Wialon')+'">'+label+'</span>';
}
const executionLabels={performed:'Состоялся',not_performed:'Не состоялся',planned:'Предстоящий',in_progress:'В пути',checking:'Проверяется',unknown:'Недостаточно GPS',ambiguous:'Неоднозначно'};
const executionReasons={complete_route:'Подтверждены выезд и прибытие того же автобуса',no_departure:'Ни один автобус не выехал в окне рейса при полном GPS-покрытии',no_complete_route:'Ни один автобус не завершил маршрут в окне рейса при полном GPS-покрытии',not_due:'Рейс ещё не начался',awaiting_arrival:'Есть выезд; ожидается прибытие',window_open:'Окно проверки ещё не завершилось',gps_loading:'История GPS загружается',gps_error:'Часть истории GPS недоступна',gps_gaps:'Разрывы GPS хотя бы у одного автобуса',no_fleet:'Нет автобусов для проверки',ambiguous:'Проезд подходит нескольким рейсам'};
function executionCells(r){
 const message=(executionReasons[r.execution_reason]||'')+(r.execution_status==='performed'?'':' · проверка до '+dateLabel(r.execution_deadline.slice(0,10))+' '+timeLabel(r.execution_deadline));
 const arrival=r.arrival_at?timeLabel(r.arrival_at,true)+(r.arrival_at.slice(0,10)!==r.date?'<span class="subline">'+dateLabel(r.arrival_at.slice(0,10))+'</span>':''):'—';
 return '<td class="execution-cell"><span class="pill execution-status '+r.execution_status+'" title="'+escape(message)+'">'+escape(executionLabels[r.execution_status]||'Недостаточно GPS')+'</span></td><td>'+arrival+'</td>';
}
function executionSummary(t){
 return '<td class="execution-count"><span class="count-performed">✓ '+n(t.performed)+'</span> · <span class="count-missed">× '+n(t.not_performed)+'</span></td><td class="blank">—</td>';
}
function card(label,value,foot,color,total){
 return '<section class="stat '+(total?'total':'')+'"><div class="stat-label">'+(color?'<b class="swatch '+color+'"></b>':'')+label+'</div><div class="stat-value">'+n(value)+'</div><div class="stat-foot">'+escape(foot)+'</div></section>';
}
function cells(t){
 return '<td>'+n(t.trips)+'</td><td>'+n(t.bookings)+'</td><td>'+n(t.confirmed)+'</td><td>'+n(t.unconfirmed)+'</td><td>'+n(t.free)+'</td><td class="'+(t.early?'count-early':'blank')+'">'+(t.early||'—')+'</td><td class="'+(t.late?'count-late':'blank')+'">'+(t.late||'—')+'</td>';
}
const noTimes='<td class="blank">—</td><td class="blank">—</td><td></td>';
function table(){
 let out='';
 for(const day of latest.days){
  const open=openDates.has(day.date);
  out+='<tr class="date '+(open?'open':'')+'"><td><button type="button" class="disclosure" data-date="'+day.date+'" aria-expanded="'+open+'"><span class="mark" aria-hidden="true">'+(open?'−':'+')+'</span>'+dateLabel(day.date)+'</button></td>' +cells(day.totals)+noTimes+executionSummary(day.totals)+'</tr>';
  if(!open)continue;
  for(const g of day.directions){
   const key=day.date+g.direction,expanded=!closedGroups.has(key);
   out+='<tr class="direction"><td><button type="button" class="disclosure" data-group="'+key+'" aria-expanded="'+expanded+'"><span class="mark" aria-hidden="true">'+(expanded?'−':'+')+'</span><b class="swatch '+(g.direction==='reverse'?'purple':'blue')+'"></b>'+escape(g.label)+'</button></td>' +cells(g.totals)+noTimes+executionSummary(g.totals)+'</tr>';
   if(!expanded)continue;
   for(const r of g.trips){
    const t={...r,trips:1,early:Number(r.status==='early'),late:Number(r.status==='late')};
    out+='<tr class="trip"><td><span class="blank">№'+r.route_id+'</span> '+escape(r.name)+'<span class="subline">'+escape(r.vehicle||'Автобус не определён')+'</span></td>'+cells(t)+'<td>'+escape(r.departure)+'</td><td>'+timeLabel(r.actual_at,true)+'</td><td>'+status(r)+'</td>'+executionCells(r)+'</tr>';
   }
  }
 }
 $('detail-body').innerHTML=out||'<tr><td colspan="13" class="empty">За выбранный период нет рейсов</td></tr>';
 $('expand').textContent=latest.days.length&&latest.days.every(d=>openDates.has(d.date))?'Свернуть все даты':'Развернуть все даты';
}
function grid(max,count){
 let out='<div class="grid">';
 for(let i=0;i<=count;i++)out+='<div class="gridline"><span>'+n(max-i*max/count)+'</span></div>';
 return out+'</div>';
}
function charts(){
 const days=[...latest.days].reverse();
 if(!days.length){for(const id of ['trips-chart','bookings-chart'])$(id).innerHTML='<div class="empty">Нет данных за период</div>';return;}
 const nice=v=>{const scale=Math.pow(10,Math.max(0,Math.floor(Math.log10(v||1))-1));return Math.max(4,Math.ceil(v/4/scale)*4*scale);};
 for(const [id,fields,labels,color] of [['trips-chart',['forward','reverse'],['В ШОКмаркет','В Камп'],'purple'],['bookings-chart',['bookings','confirmed'],['Брони','Посадки'],'green']]){
  const maximum=nice(Math.max(...days.map(d=>Math.max(...fields.map(f=>d.totals[f])))));
  let groups='';
  for(const day of days){
   groups+='<div class="bar-group">';
   fields.forEach((f,i)=>{groups+='<div class="bar '+(i?color:'')+'" style="height:'+day.totals[f]/maximum*100+'%" title="'+dateLabel(day.date)+' · '+labels[i]+': '+n(day.totals[f])+'"><span class="bar-label">'+n(day.totals[f])+'</span></div>';});
   groups+='<span class="x">'+day.date.slice(8)+'.'+day.date.slice(5,7)+'</span></div>';
  }
  $(id).innerHTML='<div class="plot" style="min-width:'+(days.length>7?days.length*58:0)+'px">'+grid(maximum,4)+'<div class="bars">'+groups+'</div></div>';
  $(id).setAttribute('aria-label',days.map(d=>dateLabel(d.date)+': '+fields.map((f,i)=>labels[i]+' '+d.totals[f]).join(', ')).join('; '));
 }
}
function render(result){
 latest=result;const t=result.totals;
 $('metrics-primary').innerHTML=card('Рейсов по расписанию',t.trips,'По расписанию за период',null,true)+card('В ШОКмаркет',t.forward,'Из Испанского кампа','blue')+card('В Камп',t.reverse,'Из ШОКмаркета Бюкеджели','purple');
 $('metrics-secondary').innerHTML=card('Количество броней',t.bookings,'Без отменённых')+card('Посадка подтверждена',t.confirmed,t.bookings?n(t.confirmed/t.bookings*100)+'% от всех броней':'Броней пока нет','green')+card('Посадка не подтверждена',t.unconfirmed,'Нет отметки водителя','amber')+card('Свободных мест',t.free,'Из '+n(t.capacity)+' мест по расписанию')+card('Опозданий',t.late,t.late?'В среднем +'+n(t.average_late_minutes)+' мин':'Нет подтверждённых опозданий','red')+card('Ранних выездов',t.early,t.early?'Максимум −'+n(t.max_early_minutes)+' мин':'Нет подтверждённых ранних выездов','amber');
 $('metrics-execution').innerHTML=card('Рейсы состоялись',t.performed,'Выезд и прибытие подтверждены GPS','green')+card('Рейсы не состоялись',t.not_performed,'Проезд не найден при полном GPS-покрытии','red');
 $('execution-state').textContent='В пути: '+n(t.in_progress)+' · ожидают проверки: '+n(t.execution_pending)+' · недостаточно GPS: '+n(t.execution_unknown)+' · неоднозначно: '+n(t.execution_ambiguous);
 $('gps-note').textContent='Выезд не определён: '+n(t.unknown)+' · предстоящих: '+n(t.planned);
 $('period-label').textContent=result.period.start.split('-').reverse().join('.')+' — '+result.period.end.split('-').reverse().join('.');
 $('punctuality').textContent='Вовремя: '+n(t.on_time)+' · ранних: '+n(t.early)+' · с опозданием: '+n(t.late)+' · выезд не определён: '+n(t.unknown)+' · предстоящих: '+n(t.planned);
 const summary=[...result.directions.map(g=>[g.label,g.totals]),['Итого',t]];
 $('summary-body').innerHTML=summary.map(([label,v],i)=>'<tr class="'+(i===summary.length-1?'total-row':'')+'"><td>'+label+'</td><td>'+n(v.trips)+'</td><td>'+n(v.bookings)+'</td><td>'+n(v.confirmed)+'</td><td>'+n(v.unconfirmed)+'</td><td>'+n(v.free)+'</td><td>'+n(v.occupancy_percent)+(v.occupancy_percent===null?'':'%')+'</td><td>'+n(v.early)+'</td><td>'+n(v.late)+'</td><td>'+n(v.performed)+'</td><td>'+n(v.not_performed)+'</td></tr>').join('');
 $('source-state').textContent='Обновлено '+timeLabel(result.generated_at)+(result.wialon.pending_days?' · Wialon загружается':result.wialon.error_days?' · часть Wialon недоступна':'');
 $('methodology').innerHTML=result.notes.map(s=>'<span>'+escape(s)+'</span>').join('')+'<span>В ШОКмаркет — «İspanyol kamp / Испанский камп». В Камп — «Шок маркет Бюкеджели».</span>';
 $('export').href='/analytics/api/export.xlsx?'+new URLSearchParams(result.period);$('export').removeAttribute('aria-disabled');
 table();charts();
}
async function load(initial=false,preset=null){
 const id=++requestId;controller?.abort();controller=new AbortController();clearTimeout(timer);
 $('apply').disabled=true;$('error').hidden=true;
 const query=initial?new URLSearchParams(location.search):new URLSearchParams({start:$('start').value,end:$('end').value,direction:$('direction').value});
 if(preset)query.set('preset',preset);
 try{
  const response=await fetch('/analytics/api/report?'+query,{signal:controller.signal,credentials:'omit'});const result=await response.json();
  if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'Не удалось получить данные. Попробуйте ещё раз.');
  if(id!==requestId)return;
  if(!latest&&result.days.length)openDates.add(result.days[0].date);
  $('start').value=result.period.start;$('end').value=result.period.end;$('direction').value=result.period.direction;
  activePreset=query.get('preset');
  document.querySelectorAll('[data-period]').forEach(button=>button.setAttribute('aria-pressed',String(button.dataset.period===activePreset)));
  render(result);history.replaceState(null,'','?'+new URLSearchParams(result.period));
 }catch(error){
  if(error.name==='AbortError'||id!==requestId)return;
  $('error').hidden=false;$('error').textContent=error.message;
  $('source-state').textContent=latest?'Не удалось обновить · показаны предыдущие данные':'Не удалось загрузить данные';
 }finally{
  if(id===requestId){$('apply').disabled=false;timer=setTimeout(()=>{if(!document.hidden)load(false,activePreset);},latest?.wialon.pending_days?15000:60000);}
 }
}
document.querySelectorAll('[data-period]').forEach(button=>button.addEventListener('click',()=>load(false,button.dataset.period)));
$('filters').addEventListener('submit',e=>{e.preventDefault();load();});
$('export').addEventListener('click',e=>{if($('export').getAttribute('aria-disabled')==='true')e.preventDefault();});
$('detail-body').addEventListener('click',e=>{
 const d=e.target.closest('[data-date]'),g=e.target.closest('[data-group]');
 if(d){const k=d.dataset.date;openDates.has(k)?openDates.delete(k):openDates.add(k);}
 else if(g){const k=g.dataset.group;closedGroups.has(k)?closedGroups.delete(k):closedGroups.add(k);}
 else return;table();
});
$('expand').addEventListener('click',()=>{if(!latest)return;const close=latest.days.every(d=>openDates.has(d.date));for(const d of latest.days)close?openDates.delete(d.date):openDates.add(d.date);closedGroups.clear();table();});
document.addEventListener('visibilitychange',()=>{if(!document.hidden)load(false,activePreset);});
load(true);
})();
