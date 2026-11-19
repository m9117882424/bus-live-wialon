'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const {execFileSync}=require('node:child_process');

const root=require('node:path').resolve(__dirname,'../..');
const i18nSource=fs.readFileSync(root+'/static/passenger-i18n.js','utf8');
const window={};
vm.runInNewContext(i18nSource,{window});
const l=window.BusPassengerL10n;
const codes=['tr','ru','ky','uz','tg','kk','tk','en','zh'];
assert.deepEqual(Array.from(l.languages,x=>x.code),codes);
for(const item of l.languages)assert.match(item.label,/^[🇦-🇿]/u);

const py=[
 'import ast,json,pathlib',
 "a=ast.parse(pathlib.Path("+JSON.stringify(root+"/app.py")+").read_text())",
 'def pieces(name):',
 '  n=next(x.value for x in a.body if isinstance(x,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in x.targets))',
 '  return [s.value for s in ast.walk(n) if isinstance(s,ast.Constant) and isinstance(s.value,str)]',
 "print(json.dumps({k:pieces(k) for k in ['HTML','TICKET','CHECK_SCRIPT','CANCEL_CONFIRM_SCRIPT']}))"
].join('\n');
const pieces=JSON.parse(execFileSync('python3',['-c',py],{encoding:'utf8',stdio:['ignore','pipe','pipe']}));
const main=pieces.HTML.find(s=>s.includes("const B='/bus';"));
const ticket=pieces.TICKET.find(s=>s.includes('let data=/*DATA*/'));
const check=pieces.CHECK_SCRIPT.find(s=>s.includes('const checkCard=/*CARD*/'));
const js=s=>s.split('<script>').pop().split('</script>')[0];
const mainJs=js(main),ticketJs=js(ticket);
const checkJs=js(check)
 .replace('/*CARD*/','"001234"').replace('/*CODE*/','"BUS-0123456789"')
 .replace('/*SIGNATURE*/','"abcdefghijklmnopqrstuv"').replace('/*CAN_CANCEL*/','true')
 .replace('/*BOOKING_LANG*/','"ky"').replace('/*LEG_COUNT*/','2').replace('/*FORTUNES*/','{}');
assert.doesNotThrow(()=>new vm.Script(mainJs));
assert.doesNotThrow(()=>new vm.Script(pieces.CANCEL_CONFIRM_SCRIPT[0]));
assert.doesNotThrow(()=>new vm.Script(ticketJs.replace('/*DATA*/','{}')));
assert.doesNotThrow(()=>new vm.Script(checkJs));

function dictFrom(js,name,next){
 const start=js.indexOf('const '+name+'=')>=0?js.indexOf('const '+name+'='):js.indexOf('let '+name+'=');
 assert.ok(start>=0,name+' declared');
 const end=js.indexOf(';Object.assign('+name,start);
 assert.ok(end>start,name+' extended');
 const code=js.slice(start,end)+';'+name;
 return vm.runInNewContext(code,{});
}
const nativeHome=dictFrom(mainJs,'t');
const nativeTicket=dictFrom(ticketJs,'d');
for(const code of ['ky','uz','tg','kk','tk','en','zh']){
 assert.deepEqual(Object.keys(l.home[code]).sort(),Object.keys(nativeHome.ru).sort(),'home '+code);
 assert.deepEqual(Object.keys(l.ticket[code]).sort(),Object.keys(nativeTicket.ru).sort(),'ticket '+code);
 for(const text of Object.values(l.home[code]))assert.ok(typeof text==='string'&&text.trim());
 for(const text of Object.values(l.ticket[code]))assert.ok(typeof text==='string'&&text.trim());
}
const checkKeys=Object.keys(l.check.ru).sort();
for(const code of codes)assert.deepEqual(Object.keys(l.check[code]).sort(),checkKeys,'check '+code);
for(const section of ['home','ticket','check','errors']){
 for(const [code,dict] of Object.entries(l[section])){
  assert.deepEqual(Object.keys(dict).sort(),Object.keys(l[section].en).sort(),section+' parity '+code);
  for(const [key,text] of Object.entries(dict)){
   assert.ok(typeof text==='string'&&text.trim(),section+' '+code+' '+key);
   assert.deepEqual(text.match(/\{\w+\}/g)||[],l[section].en[key].match(/\{\w+\}/g)||[],section+' placeholders '+code+' '+key);
  }
 }
}
const ticketMarkup=pieces.TICKET.find(s=>s.includes('id="languageSelect"'));
for(const [name,html] of [['home',main],['ticket',ticketMarkup]]) {
 const pos=c=>html.indexOf('<option value="'+c+'">');
 const order=codes.map(pos);
 assert.ok(order.every(x=>x>=0),name+' flags present');
 assert.deepEqual([...order].sort((a,b)=>a-b),order,name+' flag order');
 const scriptLoader=name==='home'?html:pieces.TICKET.find(s=>s.includes('src="/bus/passenger-i18n.js'));
 assert.match(scriptLoader,/<script src="\/bus\/passenger-i18n\.js\?v=[^"]+"><\/script><script>/,name+' loads translation file before inline code');
}
// Expected stop-display expressions from the captured baseline; no production reads.
const staged=fs.readFileSync(root+'/app.py','utf8');
const routePoint="function point(x){return lang==='tr'?(stopMap[x]||x):x}";
const ticketPoint="function point(v){return L==='tr'?(data.stop_translations?.[v]||v):v}";
assert.ok(staged.includes(routePoint),'stop display kept unchanged');
assert.ok(staged.includes(ticketPoint),'ticket stop display kept unchanged');
assert.ok(staged.includes("['tr','ru','ky','uz','tg','kk','tk','en','zh']"),'booking locale persisted');
assert.ok(staged.includes("['ru','tr'].includes(localStorage.getItem('bus-lang'))"),'admin RU/TR fallback');
assert.ok(staged.includes("passenger-i18n.js"),'external translations served');
console.log('PASS: 9 flags/order, 7 complete home/ticket translations, check translations, JS syntax, unchanged stop names, admin fallback');
