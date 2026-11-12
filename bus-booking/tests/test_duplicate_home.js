// Exercise the actual home-page JavaScript with a synthetic server and DOM.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(process.argv[2],'utf8');
const script=html.split('<script>')[1].split('</script>')[0];
const names=['day','returnDay','dir','round','booking','returnWrap','trips','selected','reverse','card','err','submit','lookupCard','lookupResult'];
const nodes={};for(const name of names)nodes[name]={style:{},value:'',checked:false,innerHTML:'',textContent:'',options:[{},{},{}],scrollIntoView(){}};
nodes.dir.value='forward';
let duplicate=true,storage={},scheduled=null,visibility=null;
const trip={id:1,name:'Туда',origin:'Камп',destination:'Магазин',time:'14:30',capacity:50,free:49,past:false,refresh_in_ms:1000};
let availableTrips=[trip];
const context={...nodes,URL,URLSearchParams,Date,document:{hidden:false,querySelectorAll:()=>[],getElementById:id=>nodes[id],addEventListener:(name,fn)=>{if(name==='visibilitychange')visibility=fn}},location:{href:''},
 setTimeout:(fn,delay)=>{scheduled={fn,delay};return 1},clearTimeout:()=>{},
 localStorage:{getItem:key=>storage[key]||null,setItem:(key,value)=>{storage[key]=value}},
 fetch:async(url,options={})=>{
  if(url.includes('/api/stops'))return {ok:true,json:async()=>[]};
  if(url.includes('/api/trips'))return {ok:true,json:async()=>availableTrips};
  if(url.includes('/api/find-booking'))return {ok:true,json:async()=>({items:[{url:'/bus/check/BUS-0000000000/abcdefghijklmnopqrstuv',legs:[]}]})};
  if(url.includes('/api/book'))return duplicate?{ok:false,json:async()=>({detail:{code:'ACTIVE_BOOKING_EXISTS'}})}:{ok:true,json:async()=>({url:'/bus/ticket/abcdefghijklmnopqrstuv1234567890'})};
  throw Error('Unexpected URL '+url);
 }};
vm.createContext(context);vm.runInContext(script,context);
(async()=>{
 await new Promise(resolve=>setImmediate(resolve));
 context.choose(1);nodes.card.value='123456';await context.book();
 assert(nodes.err.innerHTML.includes('уже есть действующая бронь'));assert(nodes.err.innerHTML.includes('viewExisting()'));
 context.viewExisting();await new Promise(resolve=>setImmediate(resolve));assert.strictEqual(context.location.href,'/bus/check/BUS-0000000000/abcdefghijklmnopqrstuv');
 duplicate=false;await context.book();assert.strictEqual(context.location.href,'/bus/ticket/abcdefghijklmnopqrstuv1234567890');assert.strictEqual(storage['bus-ticket:123456'],'abcdefghijklmnopqrstuv1234567890');
 assert(scheduled&&scheduled.delay===1000&&visibility);
 availableTrips=[];scheduled.fn();await new Promise(resolve=>setImmediate(resolve));assert(!nodes.trips.innerHTML.includes('14:30'));
 availableTrips=[trip];visibility();await new Promise(resolve=>setImmediate(resolve));assert(nodes.trips.innerHTML.includes('14:30'));
 console.log('PASS: home lookup, duplicate prompt, saved link and scheduled trip refresh');
})().catch(e=>{console.error(e);process.exit(1)});
