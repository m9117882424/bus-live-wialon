'use strict';
const assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
const fs=require('node:fs');
const vm=require('node:vm');
const root=require('node:path').resolve(__dirname,'../..');
const py=[
  'import ast,json,pathlib',
  "a=ast.parse(pathlib.Path("+JSON.stringify(root+"/app.py")+").read_text())",
  'def pieces(name):',
  '  n=next(x.value for x in a.body if isinstance(x,ast.Assign) and any(isinstance(t,ast.Name) and t.id==name for t in x.targets))',
  '  return [s.value for s in ast.walk(n) if isinstance(s,ast.Constant) and isinstance(s.value,str)]',
  "print(json.dumps({k:pieces(k) for k in ['HTML','ADMIN']}))"
].join('\n');
const p=JSON.parse(execFileSync('python3',['-c',py],{encoding:'utf8',stdio:['ignore','pipe','pipe']}));
const home=p.HTML.find(s=>s.includes("const B='/bus';"));
const admin=p.ADMIN.find(s=>s.includes("let lang=['ru','tr']"));
const js=s=>s.split('<script>').pop().split('</script>')[0];
const homeJs=js(home);
const adminJs=js(admin);
new vm.Script(homeJs);
new vm.Script(adminJs);
assert.ok(home.includes('id="horizonHint"')&&homeJs.includes('day.max=limit'));
assert.ok(homeJs.includes("'/api/booking-window'"));
assert.ok(homeJs.includes("OUTSIDE_BOOKING_WINDOW"));
assert.ok(admin.includes('id="settingsTab"'));
assert.ok(admin.includes('id="bookingHorizonHours"'));
for(const marker of ['id="driverLogin"','id="driverPassword"','id="driverCount"'])assert.ok(admin.includes(marker));
assert.ok(adminJs.includes("'/api/admin/settings'"));

const dictStart=adminJs.indexOf('const T=');
const dictEnd=adminJs.indexOf(';function e(v)',dictStart);
assert.ok(dictStart>=0&&dictEnd>dictStart,'Admin translation map found');
const translations=vm.runInNewContext(adminJs.slice(dictStart,dictEnd)+';T',{});
for(const code of ['ru','tr']){
  for(const key of ['settingsTab','settingsTitle','settingsHours','settingsHelp','settingsSave','settingsSaved','settingsInvalid','driversTitle','driverLoginLabel','driverPasswordHint']){
    assert.ok(translations[code][key],code+' '+key+' must be translated');
  }
}

const tabCode=adminJs.slice(adminJs.indexOf('function showTab('),adminJs.indexOf('function weekdayLabel('));
const panels={};
for(const id of ['managePanel','whitePanel','qrPanel','stopsPanel','settingsPanel']){
  panels[id]={style:{}};
}
for(const id of ['scheduleTab','whiteTab','qrTab','stopsTab','settingsTab']){
  panels[id]={className:''};
}
let loaded=0;
const tabContext={...panels,loadQr:()=>{},loadSettings:()=>{loaded++}};
vm.createContext(tabContext);
vm.runInContext(tabCode+';showTab("settings")',tabContext);
assert.equal(panels.settingsPanel.style.display,'block');
assert.equal(panels.managePanel.style.display,'none');
assert.equal(panels.settingsTab.className,'');
assert.equal(panels.scheduleTab.className,'secondary');
assert.equal(loaded,1,'Opening Settings refreshes saved configuration');

const methods=adminJs.slice(adminJs.indexOf('async function loadSettings()'),adminJs.indexOf('let driversData=',adminJs.indexOf('async function loadSettings()')));
assert.ok(methods.startsWith('async function loadSettings()'));
const calls=[];
const context={
  bookingHorizonHours:{value:48},settingsStatus:{textContent:''},settingsSaveButton:{disabled:false},
  loadDrivers:async()=>{},
  lang:'ru',T:translations,
  api:async(path,opt)=>{
    calls.push({path,opt});
    if(!opt)return {hours:72};
    return {hours:JSON.parse(opt.body).hours};
  },
  JSON,Number
};
vm.createContext(context);
vm.runInContext(methods,context);
(async()=>{
  await vm.runInContext('loadSettings()',context);
  assert.equal(context.bookingHorizonHours.value,72,'Saved 72h value shown in admin');
  context.bookingHorizonHours.value='0';
  await vm.runInContext('saveSettings()',context);
  assert.equal(calls.length,1,'Invalid 0-hour value rejected client-side');
  assert.equal(context.settingsStatus.textContent,translations.ru.settingsInvalid);
  context.bookingHorizonHours.value='48';
  await vm.runInContext('saveSettings()',context);
  assert.equal(calls.length,2);
  assert.equal(calls[1].path,'/api/admin/settings');
  assert.equal(calls[1].opt.method,'PUT');
  assert.equal(JSON.parse(calls[1].opt.body).hours,48);
  assert.equal(context.settingsStatus.textContent,translations.ru.settingsSaved);
  console.log('PASS: booking horizon and driver settings entry, RU/TR, validation, PUT and JS syntax');
})().catch(err=>{console.error(err);process.exitCode=1;});
