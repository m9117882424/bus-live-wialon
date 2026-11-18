(() => {
'use strict';
const $ = id => document.getElementById(id);
const words = {
 ru: {tab:'Заполняемость',date:'Дата рейсов',refresh:'Обновить',loading:'Загрузка…',forward:'В ШОКмаркет',reverse:'В Камп',time:'Время',trip:'Рейс / маршрут',booked:'Записано',free:'Свободно',capacity:'Мест',trips:'Рейсов',empty:'Нет активных рейсов в пределах горизонта бронирования.',noDirection:'В этом направлении рейсов нет.',updated:'Обновлено',auto:'Автообновление каждые 30 секунд',past:'Рейс отправился',error:'Не удалось обновить. Показаны предыдущие данные.'},
 tr: {tab:'Doluluk',date:'Sefer tarihi',refresh:'Yenile',loading:'Yükleniyor…',forward:'ŞOKmarkete',reverse:'Kampa',time:'Saat',trip:'Sefer / güzergâh',booked:'Kayıtlı',free:'Boş',capacity:'Koltuk',trips:'Sefer',empty:'Rezervasyon süresi içinde aktif sefer yok.',noDirection:'Bu yönde sefer yok.',updated:'Güncellendi',auto:'Her 30 saniyede yenilenir',past:'Sefer hareket etti',error:'Yenilenemedi. Önceki veriler gösteriliyor.'}
};
let latest=null,visible=false,timer=null,controller=null,sequence=0;
const text=()=>words[lang]||words.ru;
const label=value=>new Date(value+'T12:00:00Z').toLocaleDateString(lang==='tr'?'tr-TR':'ru-RU',{timeZone:'Europe/Istanbul',day:'2-digit',month:'2-digit',year:'numeric',weekday:'short'});
function render() {
 const t=text();
 if(latest&&!$('occupancyError').hidden)$('occupancyError').textContent=t.error;
 $('occupancyTab').textContent=t.tab;
 $('occupancyPanel').setAttribute('aria-label',t.tab);
 for(const element of $('occupancyPanel').querySelectorAll('[data-occupancy-i]'))element.textContent=t[element.dataset.occupancyI];
 if(!latest)return;
 $('occupancyDate').innerHTML=latest.dates.map(day=>'<option value="'+e(day)+'">'+e(label(day))+'</option>').join('');
 $('occupancyDate').value=latest.date||'';
 $('occupancyDate').disabled=!latest.dates.length;
 $('occupancySummary').innerHTML=['trips','booked','free','capacity'].map(key=>'<div class="occupancy-stat"><span>'+e(t[key])+'</span><strong>'+latest.totals[key]+'</strong></div>').join('');
 $('occupancyTrips').innerHTML=latest.dates.length?['forward','reverse'].map(direction=>{
  const rows=latest.trips.filter(item=>item.direction===direction);
  return '<div class="occupancy-direction"><h3>'+e(t[direction])+'</h3><div class="occupancy-table-wrap"><table><thead><tr><th>'+e(t.time)+'</th><th>'+e(t.trip)+'</th><th>'+e(t.booked)+'</th><th>'+e(t.free)+'</th></tr></thead><tbody>'+
   rows.map(item=>'<tr class="occupancy-trip" data-direction="'+direction+'" data-route="'+item.route_id+'"><td>'+e(item.time.slice(0,5))+(item.past?'<span class="small muted occupancy-past">'+e(t.past)+'</span>':'')+'</td><td>'+e(item.name)+'<span class="small muted occupancy-route">'+e(item.origin)+' → '+e(item.destination)+'</span></td><td class="occupancy-booked">'+item.booked+'</td><td class="occupancy-free'+(item.free===0?' occupancy-full':'')+'">'+item.free+'</td></tr>').join('')+
   (rows.length?'':'<tr><td colspan="4" class="muted">'+e(t.noDirection)+'</td></tr>')+'</tbody></table></div></div>';
 }).join(''):'<p class="muted">'+e(t.empty)+'</p>';
 $('occupancyUpdated').textContent=t.updated+' '+new Date(latest.now).toLocaleTimeString(lang==='tr'?'tr-TR':'ru-RU',{timeZone:'Europe/Istanbul',hour:'2-digit',minute:'2-digit',second:'2-digit'})+' · '+t.auto;
}
async function load() {
 if(!visible||document.hidden)return;
 const request=++sequence;
 controller?.abort();controller=new AbortController();clearTimeout(timer);
 $('occupancyRefresh').disabled=true;
 $('occupancyError').hidden=true;
 if(!latest)$('occupancyUpdated').textContent=text().loading;
 const date=$('occupancyDate').value;
 $('occupancyPanel').setAttribute('aria-busy','true');
 $('occupancyUpdated').textContent=text().loading;
 if(latest&&date!==latest.date){$('occupancySummary').innerHTML='';$('occupancyTrips').textContent=text().loading;}
 try {
  const response=await fetch(B+'/api/admin/occupancy'+(date?'?date='+encodeURIComponent(date):''),{signal:controller.signal,credentials:'same-origin',cache:'no-store'});
  const result=await response.json();
  if(!response.ok)throw Error(typeof result.detail==='string'?result.detail:'HTTP '+response.status);
  if(request!==sequence||!visible)return;
  latest=result;render();
 } catch(error) {
  if(error.name==='AbortError'||request!==sequence)return;
  if(latest)render();
  $('occupancyError').textContent=latest?text().error:error.message;
  $('occupancyError').hidden=false;
  if(!latest)$('occupancyUpdated').textContent='';
 } finally {
  if(request===sequence){$('occupancyPanel').setAttribute('aria-busy','false');$('occupancyRefresh').disabled=false;if(visible)timer=setTimeout(load,30000);}
 }
}
function show(which) {
 visible=which==='occupancy';
 $('occupancyPanel').style.display=visible?'block':'none';
 $('occupancyTab').className=visible?'':'secondary';
 clearTimeout(timer);
 if(visible)load();
 else {++sequence;controller?.abort();$('occupancyPanel').setAttribute('aria-busy','false');$('occupancyRefresh').disabled=false;}
}
$('occupancyDate').addEventListener('change',load);
$('occupancyRefresh').addEventListener('click',load);
document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden&&visible)load();});
window.BusOccupancy={show,renderLanguage:render};
render();
})();
