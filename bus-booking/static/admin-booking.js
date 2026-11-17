(() => {
'use strict';
const el = id => document.getElementById(id);
const copy = {
 ru: {title:'Создать бронь для пассажира',date:'Дата поездки',mode:'Направление',forward:'İSPANYOL KAMP → ŞOK MARKET',reverse:'ŞOK MARKET → İSPANYOL KAMP',both:'Туда и обратно',trip:'Рейс',returnDate:'Дата обратного рейса',returnTrip:'Рейс обратно',card:'Красный номер внизу пропуска',submit:'Создать бронь',success:'Бронь создана',openTicket:'Открыть подтверждение',ticketNote:'Если у пассажира нет телефона, водитель может проверить номер пропуска вручную.',choose:'Выберите рейс',noTrips:'Нет доступных рейсов',free:'свободно',loading:'Загрузка рейсов…',network:'Не удалось загрузить рейсы.',invalid:'Выберите дату и рейс.',returnInvalid:'Выберите обратный рейс.',failed:'Не удалось создать бронь.'},
 tr: {title:'Yolcu için rezervasyon oluştur',date:'Yolculuk tarihi',mode:'Yön',forward:'İSPANYOL KAMP → ŞOK MARKET',reverse:'ŞOK MARKET → İSPANYOL KAMP',both:'Gidiş - dönüş',trip:'Sefer',returnDate:'Dönüş tarihi',returnTrip:'Dönüş seferi',card:'Kartın altındaki kırmızı numara',submit:'Rezervasyon oluştur',success:'Rezervasyon oluşturuldu',openTicket:'Onay belgesini aç',ticketNote:'Yolcunun telefonu yoksa şoför kart numarasını elle kontrol edebilir.',choose:'Sefer seçin',noTrips:'Uygun sefer yok',free:'boş',loading:'Seferler yükleniyor…',network:'Seferler yüklenemedi.',invalid:'Tarih ve sefer seçin.',returnInvalid:'Dönüş seferini seçin.',failed:'Rezervasyon oluşturulamadı.'}
};
const t = () => copy[lang] || copy.ru;
let primary=[],primarySerial=0,returnSerial=0,resultToken='';
function todayTurkey(){const p=new Intl.DateTimeFormat('en-GB',{timeZone:'Europe/Istanbul',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date());const o=Object.fromEntries(p.map(x=>[x.type,x.value]));return `${o.year}-${o.month}-${o.day}`}
function makeOptions(select,rows,previous){select.replaceChildren();select.add(new Option(rows.length?t().choose:t().noTrips,''));for(const x of rows){let text=`${x.time.slice(0,5)} · ${x.origin} → ${x.destination} · ${x.name} · ${x.free}/${x.capacity} ${t().free}`;select.add(new Option(text,String(x.id)))}if(previous && rows.some(x=>String(x.id)===previous))select.value=previous;else select.value='';select.disabled=!rows.length}
function updateResultLinks(){if(!resultToken)return;const path='/bus/ticket/'+resultToken;el('abTicket').href=path;el('abQr').src=path+'/qr';el('abPdf').href=path+'/download/pdf?lang='+encodeURIComponent(lang);el('abJpg').href=path+'/download/jpg?lang='+encodeURIComponent(lang)}
function language(){document.querySelectorAll('[data-ab]').forEach(x=>x.textContent=t()[x.dataset.ab]);updateResultLinks()}
function roundControls(){const round=el('abMode').value==='both';el('abReturnDateBox').hidden=!round;el('abReturnTripBox').hidden=!round;el('abReturnDate').required=round;el('abReturnTrip').required=round;return round}
async function loadPrimary(){
 const serial=++primarySerial; ++returnSerial;const day=el('abDate').value,mode=el('abMode').value,round=roundControls();primary=[];makeOptions(el('abTrip'),[], '');makeOptions(el('abReturnTrip'),[], '');el('abStatus').textContent='';if(!day)return;
 try{const rows=await api('/api/trips?'+new URLSearchParams({date:day,direction:mode==='reverse'?'reverse':'forward'}));if(serial!==primarySerial)return;primary=rows.filter(x=>!x.past&&x.free>0);makeOptions(el('abTrip'),primary,'');if(round && !el('abReturnDate').value)el('abReturnDate').value=day;}
 catch(err){if(serial===primarySerial)el('abStatus').textContent=t().network}
}
async function loadReturn(){
 const serial=++returnSerial;const selected=primary.find(x=>String(x.id)===el('abTrip').value);const day=el('abReturnDate').value;makeOptions(el('abReturnTrip'),[],'');if(!roundControls()||!selected||!day)return;
 try{const rows=await api('/api/trips?'+new URLSearchParams({date:day,direction:'reverse'}));if(serial!==returnSerial)return;const start=el('abDate').value+'T'+selected.time.slice(0,5);let available=rows.filter(x=>!x.past&&x.free>0&&x.origin===selected.destination&&x.destination===selected.origin&&(day+'T'+x.time.slice(0,5)>start));makeOptions(el('abReturnTrip'),available,'')}
 catch(err){if(serial===returnSerial)el('abStatus').textContent=t().network}
}
async function submit(event){
 event.preventDefault();const status=el('abStatus'),button=el('abSubmit');status.textContent='';const date=el('abDate').value,mode=el('abMode').value,first=Number(el('abTrip').value),round=mode==='both',back=Number(el('abReturnTrip').value),card=el('abCard').value.trim();
 if(!date||!first){status.textContent=t().invalid;return}if(round&&(!el('abReturnDate').value||!back)){status.textContent=t().returnInvalid;return}
 const body={card,date,forward:first,direction:mode==='reverse'?'reverse':'forward',lang:lang==='tr'?'tr':'ru'};if(round){body.reverse=back;body.reverse_date=el('abReturnDate').value}
 button.disabled=true;el('abResult').hidden=true;resultToken='';
 try{const response=await fetch('/bus/api/admin/book',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const data=await response.json();if(!response.ok){let d=data.detail;throw Error(typeof d==='string'?d:(d&&d.message)||t().failed)}const m=String(data.url||'').match(/^\/bus\/ticket\/([A-Za-z0-9_-]{20,100})$/);if(!m)throw Error(t().failed);resultToken=m[1];updateResultLinks();el('abResult').hidden=false;el('abCard').value='';await Promise.allSettled([loadQr(0),historyLoad(),loadPrimary()]);}
 catch(err){status.textContent=err.message||t().failed}
 finally{button.disabled=false}
}
const originalLanguage=renderLanguage;renderLanguage=function(){originalLanguage();language();if(!el('abResult').hidden)updateResultLinks();};
const originalShowTab=showTab;showTab=function(which){originalShowTab(which);if(which==='qr'){if(!el('abDate').value)el('abDate').value=todayTurkey();if(!el('abReturnDate').value)el('abReturnDate').value=el('abDate').value;loadPrimary()}};
el('abDate').addEventListener('change',()=>{el('abReturnDate').value=el('abDate').value;loadPrimary()});el('abMode').addEventListener('change',loadPrimary);el('abTrip').addEventListener('change',loadReturn);el('abReturnDate').addEventListener('change',loadReturn);el('abForm').addEventListener('submit',submit);language();roundControls();
})();
