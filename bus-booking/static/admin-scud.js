(() => {
'use strict';
const $=id=>document.getElementById(id);
const words={
 ru:{title:'Импорт отметок СКУД',privacy:'Сохраняются только автобус, карта и время. ФИО, кимлик, паспорт и исходный файл не сохраняются.',rule:'Привязка к рейсу: 30 минут до отправления и 30 минут после, по времени Турции. Повторные отметки одной карты на рейсе считаются один раз.',files:'Выгрузки СКУД (XLSX, можно несколько)',upload:'Загрузить СКУД',refresh:'Обновить',loading:'Загрузка…',choose:'Выберите файл XLSX.',large:'Файл XLSX: не более 6 МБ.',error:'Не удалось загрузить данные. Повторите попытку.',saved:'Загружено файлов',added:'Новых отметок',duplicates:'Повторных отметок',invalid:'Некорректных строк',ignored:'Строк без номера автобуса',records:'Сохранено отметок',days:'Дней с данными',period:'Даты отметок',imported:'Время загрузки',new:'Новых',repeat:'Повторов',empty:'Выгрузок СКУД пока нет.',partial:'Предыдущие успешно загруженные файлы сохранены.'},
 tr:{title:'SKUD geçiş kayıtlarını yükle',privacy:'Yalnızca otobüs plakası, kart numarası ve zaman saklanır. Ad, soyad, kimlik, pasaport ve kaynak dosya saklanmaz.',rule:'Sefer eşleştirmesi: Türkiye saatine göre planlanan kalkıştan 30 dakika önce ile 30 dakika sonrası. Aynı kart bir seferde bir kez sayılır.',files:'SKUD dosyaları (XLSX, birden fazla seçilebilir)',upload:'SKUD yükle',refresh:'Yenile',loading:'Yükleniyor…',choose:'Bir XLSX dosyası seçin.',large:'XLSX dosyası en fazla 6 MB olabilir.',error:'Veri yüklenemedi. Tekrar deneyin.',saved:'Yüklenen dosya',added:'Yeni kayıt',duplicates:'Tekrarlanan kayıt',invalid:'Geçersiz satır',ignored:'Plakasız satır',records:'Saklanan kayıt',days:'Veri bulunan gün',period:'Kayıt tarihleri',imported:'Yükleme zamanı',new:'Yeni',repeat:'Tekrar',empty:'Henüz SKUD dosyası yüklenmedi.',partial:'Önceki başarıyla yüklenen dosyalar kaydedildi.'}
};
const t=()=>words[lang]||words.ru;
const date=v=>v.split('-').reverse().join('.');
const stamp=v=>new Date(v).toLocaleString(lang==='tr'?'tr-TR':'ru-RU',{timeZone:'Europe/Istanbul'});
let latest=null,busy=false,results=[],visible=false,request=0;
function render(){
 const w=t();
 for(const item of $('scudBox').querySelectorAll('[data-scud-i]'))item.textContent=w[item.dataset.scudI];
 $('scudImport').disabled=busy;$('scudRefresh').disabled=busy;$('scudFiles').disabled=busy;
 $('scudResult').textContent=busy?w.loading:results.length?w.saved+': '+results.length+' · '+w.added+': '+results.reduce((n,r)=>n+r.inserted,0)+' · '+w.duplicates+': '+results.reduce((n,r)=>n+r.duplicates,0)+' · '+w.invalid+': '+results.reduce((n,r)=>n+r.invalid,0)+' · '+w.ignored+': '+results.reduce((n,r)=>n+r.ignored,0):'';
 if(!latest)return;
 $('scudTotal').textContent=w.records+': '+latest.records+' · '+w.days+': '+latest.days.length;
 $('scudHistory').innerHTML=latest.imports.length?'<table><thead><tr><th>'+e(w.period)+'</th><th>'+e(w.imported)+'</th><th>'+e(w.new)+'</th><th>'+e(w.repeat)+'</th></tr></thead><tbody>'+latest.imports.map(row=>'<tr><td>'+e(date(row.first_day))+' — '+e(date(row.last_day))+'</td><td>'+e(stamp(row.imported_at))+'</td><td>'+row.inserted+'</td><td>'+row.duplicates+'</td></tr>').join('')+'</tbody></table>':'<p class="muted">'+e(w.empty)+'</p>';
}
async function refresh(){
 const sequence=++request;
 try{
  const response=await fetch(B+'/api/admin/scud/status',{credentials:'same-origin',cache:'no-store'});
  if(!response.ok)throw Error(t().error);
  const data=await response.json();if(sequence!==request)return;latest=data;render();
 }catch(error){if(sequence===request){$('scudError').textContent=t().error;$('scudError').hidden=false;}}
}
async function upload(){
 const files=Array.from($('scudFiles').files||[]);
 $('scudError').hidden=true;
 if(!files.length){$('scudError').textContent=t().choose;$('scudError').hidden=false;return;}
 if(files.some(file=>file.size>6*1024*1024)){ $('scudError').textContent=t().large;$('scudError').hidden=false;return; }
 busy=true;results=[];render();
 try{
  for(const file of files){
   const response=await fetch(B+'/api/admin/scud/import',{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},body:file});
   let data;try{data=await response.json()}catch{throw Error(t().error)}
   if(!response.ok)throw Error(typeof data.detail==='string'?data.detail:t().error);
   results.push(data);
  }
  $('scudFiles').value='';
 }catch(error){
  $('scudError').textContent=error.message+(results.length?' '+t().partial:'');$('scudError').hidden=false;
 }finally{busy=false;render();await refresh();}
}
$('scudImport').addEventListener('click',()=>{if(!busy)upload()});
$('scudRefresh').addEventListener('click',()=>{if(!busy){$('scudError').hidden=true;refresh()}});
window.BusScud={show(which){visible=which==='white';if(visible&&!busy)refresh()},renderLanguage:render};
render();
})();