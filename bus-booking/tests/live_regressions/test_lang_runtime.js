'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {execFileSync}=require('node:child_process');

const root=require('node:path').resolve(__dirname,'../..');
const py=[
  'import ast,json,pathlib',
  "a=ast.parse(pathlib.Path("+JSON.stringify(root+"/app.py")+").read_text())",
  'def pieces(name):',
  '  n=next(x.value for x in a.body if isinstance(x,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in x.targets))',
  '  return [s.value for s in ast.walk(n) if isinstance(s,ast.Constant) and isinstance(s.value,str)]',
  "print(json.dumps({k:pieces(k) for k in ['HTML','TICKET','CHECK_SCRIPT','CANCEL_CONFIRM_SCRIPT']}))"
].join('\n');
const p=JSON.parse(execFileSync('python3',['-c',py],{encoding:'utf8',stdio:['ignore','pipe','pipe']}));
const script=s=>s.split('<script>').pop().split('</script>')[0];
const home=script(p.HTML.find(s=>s.includes("const B='/bus';")));
const ticket=p.CANCEL_CONFIRM_SCRIPT[0]+script(p.TICKET.find(s=>s.includes('let data=/*DATA*/')));
const check=p.CANCEL_CONFIRM_SCRIPT[0]+script(p.CHECK_SCRIPT.find(s=>s.includes('const checkCard=/*CARD*/')))
  .replace('/*CARD*/','"001234"').replace('/*CODE*/','"BUS-0123456789"')
  .replace('/*SIGNATURE*/','"abcdefghijklmnopqrstuv"').replace('/*CAN_CANCEL*/','true')
  .replace('/*BOOKING_LANG*/','"ky"').replace('/*LEG_COUNT*/','1').replace('/*FORTUNES*/',JSON.stringify({ru:'wish',tr:'wish-tr',kk:'wish-kk',tk:'wish-tk',zh:'愿您一路顺畅！'}));
const window={};
vm.runInNewContext(fs.readFileSync(root+'/static/passenger-i18n.js','utf8'),{window});

function domContext(stored='ky'){
  const elems={};
  function el(id){
    if(!elems[id])elems[id]={
      id,style:{},textContent:'',innerHTML:'',value:'',checked:false,disabled:false,
      classList:{toggle(){}},options:[{textContent:''},{textContent:''},{textContent:''}],
      scrollIntoView(){},focus(){},dataset:{}
    };
    return elems[id];
  }
  for(const id of ['day','returnDay','dir','round','booking','selected','returnWrap','reverse',
    'lookupResult','lookupCard','card','err','submit','trips','horizonHint','languageSelect','ok','direction','legs',
    'cardLabel','cardVal','codeLabel','codeVal','fortuneLabel','fortuneText','qrTools',
    'cancelTicketButton','cancelNote','expiry','copy','qr','autoReturn','autoReturnText',
    'autoReturnSeconds','autoReturnUnit','returnNow','cancelError','cancelOpen','cancelMsg'])el(id);
  const trans=[{dataset:{i:'title'},textContent:''},{dataset:{i:'findButton'},textContent:''}];
  let preference=stored;
  const context={
    ...elems,window:{...window},document:{
      documentElement:{lang:'ru'},hidden:false,addEventListener(){},
      getElementById:el,querySelectorAll:selector=>selector==='[data-i]'?trans:[]
    },
    localStorage:{getItem:()=>preference,setItem:(key,value)=>{if(key==='bus-lang')preference=value}},
    location:{href:'https://bus.tsmenerji.online/bus/ticket/test',search:'',pathname:'/bus/ticket/test',hash:'',replace(){}},
    history:{replaceState(){}},navigator:{clipboard:{writeText:async()=>{}}},
    URLSearchParams,Date,console,confirm:()=>true,prompt:()=>{},
    setTimeout:()=>1,clearTimeout(){},setInterval:()=>1,clearInterval(){},
    fetch:async url=>({ok:true,status:200,json:async()=>url.includes('booking-window')?{hours:72,now:'2026-11-16T18:00:00+03:00',cutoff:'2026-11-19T18:00:00+03:00'}:[]})
  };
  vm.createContext(context);
  return {context,el,trans,preference:()=>preference};
}

async function run(){
  for(const code of ['ky','uz','tg','kk','tk','en','zh']){
    const h=domContext(code);vm.runInContext(home,h.context);
    await vm.runInContext('refreshBookingWindow()',h.context);
    assert.equal(h.el('day').max,'2026-11-19');
    assert.equal(h.el('returnDay').max,'2026-11-19');
    assert.equal(h.el('horizonHint').textContent,window.BusPassengerL10n.home[code].horizon.replace('{hours}',72));
    assert.equal(h.context.document.documentElement.lang,code);
    assert.equal(h.el('languageSelect').value,code);
    assert.equal(h.trans[0].textContent,window.BusPassengerL10n.home[code].title);
    assert.equal(vm.runInContext("point('İSPANYOL KAMP')",h.context),'İSPANYOL KAMP');
    vm.runInContext("setLang('tr')",h.context);
    assert.equal(h.preference(),'tr');
    assert.equal(vm.runInContext("point('Камп')",h.context),'Kamp');

    const t=domContext(code);
    const data={token:'test-token',lang:code,card:'001234',code:'BUS-TEST',fortune:'Тест',
      fortune_tr:'Test',fortune_kk:'Тілек',fortune_tk:'Arzuw',fortune_zh:'愿您一路顺畅！',cancelled:false,expired:false,can_cancel:true,stop_translations:{'Камп':'Kamp'},
      legs:[{id:1,origin:'Камп',destination:'Магазин',route_name:'Маршрут ŞOK',date:'2099-11-14',
        departure:'10:40:00',sequence:1,capacity:41,cancelled:false,comment:''}]};
    vm.runInContext(ticket.replace('/*DATA*/',JSON.stringify(data)),t.context);
    assert.equal(t.el('languageSelect').value,code);
    assert.equal(t.el('ok').textContent,window.BusPassengerL10n.ticket[code].ok);
    assert.equal(vm.runInContext("point('Камп')",t.context),'Камп');
    vm.runInContext("setTicketLang('kk')",t.context);
    assert.equal(t.el('languageSelect').value,'kk');
    assert.equal(t.el('fortuneText').textContent,'Тілек');
    assert.equal(t.preference(),'kk');
    vm.runInContext("setTicketLang('tk')",t.context);
    assert.equal(t.el('fortuneText').textContent,'Arzuw');
    vm.runInContext("setTicketLang('ru');setTicketLang('zh')",t.context);
    assert.equal(t.el('fortuneText').textContent,'愿您一路顺畅！');
    assert.equal(t.el('cancelTicketButton').textContent,'取消预订');
    assert.ok(t.el('legs').innerHTML.includes('取消此班次'));
    assert.ok(t.el('legs').innerHTML.includes('Камп → Магазин'));
    assert.ok(t.el('legs').innerHTML.includes('Маршрут ŞOK'));
    assert.equal(t.preference(),'zh');
    const reloaded=domContext(t.preference());
    vm.runInContext(ticket.replace('/*DATA*/',JSON.stringify({...data,lang:'ru'})),reloaded.context);
    assert.equal(reloaded.el('fortuneText').textContent,'愿您一路顺畅！');
    vm.runInContext("dl('pdf')",reloaded.context);
    assert.ok(reloaded.context.location.href.endsWith('/download/pdf?lang=zh'));
  }
  const c=domContext('ky');
  const ids=['checkOk','checkDirection','checkCardLabel','checkCodeLabel','checkFortuneLabel',
    'fileLangLabel'];
  for(const id of ids){const e=c.el(id);c.context[id]=e}
  c.context.document.querySelectorAll=selector=>selector==='.check-seat-label'?[c.el('seatLabel')]:[];
  vm.runInContext(check,c.context);
  assert.equal(c.el('checkOk').textContent,window.BusPassengerL10n.check.ky.ok);
  assert.equal(c.el('seatLabel').textContent,window.BusPassengerL10n.check.ky.seat);
  vm.runInContext("setCheckLang('kk')",c.context);
  assert.equal(c.el('checkFortuneText').textContent,'wish-kk');
  vm.runInContext("setCheckLang('tk')",c.context);
  assert.equal(c.el('checkFortuneText').textContent,'wish-tk');
  vm.runInContext("setCheckLang('zh')",c.context);
  assert.equal(c.el('checkFortuneText').textContent,'愿您一路顺畅！');
  assert.equal(c.el('cancelOpen').textContent,'取消预订');
  assert.equal(c.el('fileLangLabel').textContent,'文件语言');
  assert.equal(c.preference(),'zh');
  const reloadedCheck=domContext(c.preference());vm.runInContext(check,reloadedCheck.context);
  assert.equal(reloadedCheck.el('languageSelect').value,'zh');
  assert.equal(reloadedCheck.el('checkFortuneText').textContent,'愿您一路顺畅！');
  reloadedCheck.el('fileLang').value='zh';vm.runInContext("downloadCheck('jpg')",reloadedCheck.context);
  assert.ok(reloadedCheck.context.location.href.endsWith('/download/jpg?lang=zh'));
  vm.runInContext("setCheckLang('en')",c.context);
  assert.equal(c.el('checkOk').textContent,window.BusPassengerL10n.check.en.ok);
  assert.equal(c.preference(),'en');
  const old=domContext('fr'); vm.runInContext(home,old.context);
  assert.equal(old.el('languageSelect').value,'ru');
  const oldCheck=domContext('fr'); vm.runInContext(check,oldCheck.context);
  assert.equal(oldCheck.el('languageSelect').value,'ru');
  const zh=domContext('ru');vm.runInContext(home,zh.context);
  vm.runInContext("setLang('zh');chosen={id:1};card.value='001234'",zh.context);
  assert.equal(zh.preference(),'zh');
  const reloadHome=domContext(zh.preference());vm.runInContext(home,reloadHome.context);
  assert.equal(reloadHome.el('languageSelect').value,'zh');
  let submitted;
  zh.context.fetch=async (_url,options)=>{submitted=JSON.parse(options.body);return {ok:true,json:async()=>({url:'/bus/ticket/abcdefghijklmnopqrstuv'})}};
  await vm.runInContext('book()',zh.context);
  assert.equal(submitted.lang,'zh');
  zh.context.fetch=async()=>({ok:false,json:async()=>({detail:{code:'ACTIVE_BOOKING_EXISTS'}})});
  await vm.runInContext('book()',zh.context);
  assert.ok(zh.el('err').innerHTML.includes(window.BusPassengerL10n.home.zh.duplicate));
  assert.ok(zh.el('err').innerHTML.includes('查看或取消预订'));
  zh.context.fetch=async()=>({ok:false,json:async()=>({detail:{code:'OUTSIDE_BOOKING_WINDOW',hours:72}})});
  await vm.runInContext('book()',zh.context);
  assert.ok(zh.el('err').innerHTML.includes('未来 72 小时'));
  c.context.fetch=async()=>({ok:false,json:async()=>({detail:{code:'LEG_NOT_CANCELLABLE'}})});
  vm.runInContext("setCheckLang('zh')",c.context);
  vm.runInContext("checkCancelToken='synthetic-private-token'",c.context);
  const dialog=c.el('bookingCancelDialog');
  dialog.showModal=()=>{};
  let close;
  dialog.addEventListener=(event,handler)=>{assert.equal(event,'close');close=handler};
  const pending=vm.runInContext('openCancel()',c.context);
  assert.deepEqual(['Question','Yes','No'].map(id=>c.el('bookingCancel'+id).textContent),['您确定吗？','是','否']);
  dialog.returnValue='yes';
  close();
  await pending;
  assert.equal(c.el('cancelMsg').textContent,window.BusPassengerL10n.errors.zh.LEG_NOT_CANCELLABLE);
  console.log('PASS: 7 browser locale switches on home/ticket, stop names unchanged, shared preference, check page controls');
}
run().catch(e=>{console.error(e);process.exitCode=1});
