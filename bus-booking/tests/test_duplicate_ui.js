// Verify cancellation is offered only with the private credential for this booking.
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(process.argv[2],'utf8');
const script=html.split('<script>')[1].split('</script>')[0];
assert(!html.includes('id="cancelLink"'));
function setup(storage,matching=true){
 const read=[],sent=[],elements=new Map();
 let reloads=0;
 function element(id){
  if(!elements.has(id))elements.set(id,{hidden:true,textContent:'',value:'tr'});
  return elements.get(id);
 }
 const dialog=element('bookingCancelDialog');
 dialog.showModal=()=>{dialog.open=true};
 let close;
 dialog.addEventListener=(name,handler)=>{assert.equal(name,'close');close=handler};
 const context={
  window:{},cancelOpen:element('cancelOpen'),cancelMsg:element('cancelMsg'),
  document:{getElementById:element,querySelectorAll:()=>[],hidden:false},setInterval(){},
  localStorage:{getItem:key=>storage[key]??null},location:{reload:()=>reloads++},
  fetch:async(url,options)=>{
   if(!options){
    read.push(url);
    return {ok:true,json:async()=>({code:matching?vm.runInContext('checkCode',context):'BUS-OTHER',can_cancel:true})};
   }
   sent.push({url,body:JSON.parse(options.body)});
   return {ok:true,json:async()=>({ok:true})};
  }
 };
 vm.createContext(context);
 vm.runInContext(script,context);
 return {context,element,dialog,read,sent,get reloads(){return reloads},answer(value){dialog.returnValue=value;dialog.open=false;close()}};
}
(async()=>{
 const open=async test=>{
  await new Promise(resolve=>setImmediate(resolve));
  return test;
 };
 const stranger=await open(setup({}));
 assert(stranger.element('cancelOpen').hidden);
 await stranger.context.openCancel();
 assert.equal(stranger.sent.length,0);
 const mismatch=await open(setup({'bus-ticket:123456':'stale-token'},false));
 assert(mismatch.element('cancelOpen').hidden);
 assert.equal(mismatch.read.length,1);
 assert.equal(mismatch.sent.length,0);
 const legacy=await open(setup({'bus-ticket:123456':'legacy-private-token'}));
 assert(!legacy.element('cancelOpen').hidden);
 const dismissed=legacy.context.openCancel(42);
 assert(legacy.dialog.open);
 assert.equal(legacy.element('bookingCancelQuestion').textContent,'Вы уверены?');
 assert.equal(legacy.element('bookingCancelYes').textContent,'Да');
 assert.equal(legacy.element('bookingCancelNo').textContent,'Нет');
 legacy.answer('no');
 await dismissed;
 assert.equal(legacy.sent.length,0);
 const confirmed=legacy.context.openCancel(42);
 legacy.answer('yes');
 await confirmed;
 assert.equal(legacy.sent.length,1);
 assert(legacy.sent[0].url.endsWith('/legacy-private-token/cancel-for-card'));
 assert.deepEqual(legacy.sent[0].body,{card:'123456',code:vm.runInContext('checkCode',legacy.context),confirm:true,leg_id:42});
 assert.equal(legacy.reloads,1);
 console.log('PASS: public confirmation requires a matching private ticket and cancels only after Yes');
})().catch(error=>{console.error(error);process.exitCode=1});
