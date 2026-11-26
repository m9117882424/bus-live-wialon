// Simulate scanner UI decisions without connecting to real bookings or a camera.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(process.argv[2]||require('path').join(__dirname,'../static/scanner.html'),'utf8');
const script=html.split('<script>')[1].split('</script>')[0];
const ids=Array.from(html.matchAll(/id="([^"]+)"/g),x=>x[1]);
const nodes={};for(const id of ids)nodes[id]={style:{},hidden:['camera','result','loginPanel','installHint'].includes(id),textContent:'',value:'',disabled:false,events:{},classList:{toggle(){}},focus(){},blur(){},select(){},replaceChildren(){},scrollIntoView(){},setAttribute(){},addEventListener(name,fn){this.events[name]=fn}};
let answer={allowed:true,card:'123456',remaining_ms:5000,legs:[{origin:'Камп',destination:'Магазин',origin_tr:'Kamp',destination_tr:'Mağaza',date:'2026-11-14',departure:'14:30',window_start:'2026-11-14T14:10:00+03:00',window_end:'2026-11-14T14:50:00+03:00'}]};
let timers=[],offline=false,visibility=null,stopped=false,installPrompt=null,registered=null;
nodes.video.readyState=0;nodes.video.play=async()=>{assert.strictEqual(nodes.camera.hidden,false)};
const context={document:{hidden:false,documentElement:{},getElementById:id=>nodes[id],addEventListener:(name,fn)=>{if(name==='visibilitychange')visibility=fn}},window:{addEventListener:(name,fn)=>{if(name==='beforeinstallprompt')installPrompt=fn}},
 navigator:{serviceWorker:{register:async(url,opts)=>{registered={url,scope:opts.scope}}},mediaDevices:{getUserMedia:async()=>({getTracks:()=>[{stop:()=>{stopped=true}}]})}},localStorage:{getItem:()=>null,setItem(){},removeItem(){}},matchMedia:()=>({matches:false}),performance:{now:()=>0},
 Intl,Date,AbortController,setTimeout:(fn,ms)=>{timers.push({fn,ms});return timers.length},clearTimeout(){},
 fetch:async(url)=>{if(offline)throw Error('offline');return {ok:true,status:200,json:async()=>url.endsWith('/session')?{id:1,login:'driver01',display_name:'BUS 01'}:answer}}};
vm.createContext(context);vm.runInContext(script,context);
(async()=>{
 await context.restoreSession();
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
 context.document.hidden=false;
 context.navigator.mediaDevices.getUserMedia=async()=>({getTracks:()=>[{stop:()=>{stopped=true}}]});
 await context.startCamera('pass');
 assert(!nodes.camera.hidden);
 const firstAttempt=timers.findLast(timer=>timer.ms===350);
 assert(firstAttempt);
 nodes.video.readyState=2;
 nodes.video.videoWidth=640;
 nodes.video.videoHeight=480;
 nodes.video.getBoundingClientRect=()=>({width:320,height:200});
 nodes.scanFrame.getBoundingClientRect=()=>({width:240,height:60});
 nodes.canvas.getContext=()=>({drawImage:(_video,...args)=>assert.deepStrictEqual(args,[80,180,480,120,0,0,480,120])});
 nodes.canvas.toBlob=fn=>fn('cropped-png');
 let attempts=0,boardings=0;
 const location={origin:'https://bus.example'};
 Object.assign(context,{URL,location});
 context.fetch=async(url,options)=>{
   if(url.endsWith('/find-booking')){
     assert.strictEqual(JSON.parse(options.body).card,'012345');
     return {ok:true,json:async()=>({items:[{url:'/bus/check/BUS-0123456789/abcdefghijklmnopqrstuv'}]})};
   }
   if(url.endsWith('/verify')){
     const check=JSON.parse(options.body);
     if(check.record){assert.strictEqual(check.leg_id,42);boardings++}
     return {ok:true,json:async()=>({allowed:true,card:'012345',remaining_ms:5000,legs:[{id:42,departure:'14:30'}]})};
   }
   assert(url.endsWith('/read-pass'));

   assert.strictEqual(options.body,'cropped-png');
   const card=++attempts===1?null:'012345';
   return {ok:true,status:200,json:async()=>({card})};
 };
 await firstAttempt.fn();
 assert(!nodes.camera.hidden&&nodes.manualInput.value==='');
 const retry=timers.findLast(timer=>timer.ms===1000);
 assert(retry);await retry.fn();
 assert.strictEqual(attempts,2);
 assert.strictEqual(nodes.manualInput.value,'012345');
 assert(nodes.camera.hidden&&nodes.result.className.includes('good'));
 assert.strictEqual(boardings,1);
 let resolveLogin;const authCalls=[];
 context.fetch=(url)=>{authCalls.push(url);if(url.endsWith('/login'))return new Promise(r=>{resolveLogin=r});return Promise.resolve({ok:true,status:200,json:async()=>({ok:true})})};
 nodes.loginInput.value='driver02';nodes.loginPassword.value='synthetic-password';const login=context.signIn();
 while(!resolveLogin)await Promise.resolve();
 assert.strictEqual(nodes.loginPassword.value,'');const logout=context.signOut();resolveLogin({ok:true,status:200,json:async()=>({id:2,login:'driver02',display_name:'BUS 02'})});await Promise.all([login,logout]);
 assert.strictEqual(vm.runInContext('currentDriver',context),null);assert(nodes.loginPanel.hidden===false);assert(authCalls.at(-1).endsWith('/logout'));
 console.log('PASS: scanner RU/TR results, expiry, offline, camera/verification races and login/logout ordering');
})().catch(e=>{console.error(e);process.exit(1)});
