// Simulate scanner UI decisions without connecting to real bookings or a camera.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(process.argv[2],'utf8');
const script=html.split('<script>')[1].split('</script>')[0];
const ids=['video','canvas','result','resultTitle','resultCard','resultDetail','resultNote','scanBtn','camera','title','hint','installBtn','installHint','manualTitle','manualInput','manualBtn','langBtn'];
const nodes={};for(const id of ids)nodes[id]={style:{},hidden:false,textContent:'',value:'',disabled:false,events:{},addEventListener(name,fn){this.events[name]=fn}};
let answer={allowed:true,card:'123456',remaining_ms:5000,legs:[{origin:'Камп',destination:'Магазин',origin_tr:'Kamp',destination_tr:'Mağaza',date:'2026-11-14',departure:'14:30',window_start:'2026-11-14T14:10:00+03:00',window_end:'2026-11-14T14:50:00+03:00'}]};
let timers=[],offline=false,visibility=null,stopped=false,installPrompt=null,registered=null;
nodes.video.readyState=0;nodes.video.play=async()=>{assert.strictEqual(nodes.camera.hidden,false)};
const context={document:{hidden:false,documentElement:{},getElementById:id=>nodes[id],addEventListener:(name,fn)=>{if(name==='visibilitychange')visibility=fn}},window:{addEventListener:(name,fn)=>{if(name==='beforeinstallprompt')installPrompt=fn}},
 navigator:{serviceWorker:{register:async(url,opts)=>{registered={url,scope:opts.scope}}},mediaDevices:{getUserMedia:async()=>({getTracks:()=>[{stop:()=>{stopped=true}}]})}},localStorage:{getItem:()=>null,setItem(){}},matchMedia:()=>({matches:false}),performance:{now:()=>0},
 Intl,Date,AbortController,setTimeout:(fn,ms)=>{timers.push({fn,ms});return timers.length},clearTimeout(){},
 fetch:async()=>{if(offline)throw Error('offline');return {ok:true,json:async()=>answer}}};
vm.createContext(context);vm.runInContext(script,context);
(async()=>{
 assert.deepStrictEqual(registered,{url:'/bus/scanner/sw.js',scope:'/bus/scanner/'});
 let prompted=false;installPrompt({preventDefault(){},prompt:async()=>{prompted=true}});await nodes.installBtn.events.click();assert(prompted);
 await context.verifyCode('valid-fixture');assert(nodes.result.className.includes('good'));assert(nodes.resultTitle.textContent.includes('Посадка разрешена'));assert(nodes.resultCard.textContent.includes('123456'));
 nodes.langBtn.events.click();assert(nodes.resultTitle.textContent.includes('Binişe izin verildi'));assert(nodes.resultDetail.textContent.includes('Kamp → Mağaza'));
 const expiry=timers.find(x=>x.ms===5000);assert(expiry);expiry.fn();assert(nodes.result.className.includes('bad'));
 answer={allowed:false,reason:'outside_window',card:'123456',legs:[]};await context.verifyCode('too-early');assert(nodes.result.className.includes('bad'));assert(!nodes.resultTitle.textContent.includes('izin verildi'));
 offline=true;await context.verifyCode('offline');assert(nodes.result.className.includes('bad'));assert(nodes.resultTitle.textContent.includes('bağlantısı yok'));
 await context.startCamera();assert.strictEqual(nodes.camera.hidden,false);assert(timers.some(x=>x.ms===250));assert(nodes.scanBtn.textContent.includes('durdur'));
 nodes.scanBtn.events.click();assert(stopped);assert(nodes.camera.hidden);
 await context.startCamera();context.document.hidden=true;visibility();assert(stopped);assert.strictEqual(nodes.camera.hidden,true);assert(nodes.result.hidden);
 context.document.hidden=false;let resolve;context.fetch=()=>new Promise(r=>{resolve=r});let pending=context.verifyCode('pending');context.document.hidden=true;visibility();resolve({ok:true,json:async()=>({allowed:true,card:'123456',remaining_ms:10000,legs:[]})});await pending;assert(nodes.result.hidden);
 context.document.hidden=false;stopped=false;let cameraResolve;context.navigator.mediaDevices.getUserMedia=()=>new Promise(r=>{cameraResolve=r});let opening=context.startCamera();context.document.hidden=true;visibility();cameraResolve({getTracks:()=>[{stop:()=>{stopped=true}}]});await opening;assert(stopped&&nodes.camera.hidden);
 console.log('PASS: scanner UI, bilingual green/red results, expiry and offline handling');
})().catch(e=>{console.error(e);process.exit(1)});
