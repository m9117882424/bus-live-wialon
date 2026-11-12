// Test the passenger UI against a simulated DOM and synthetic server responses.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(process.argv[2],'utf8');
const js=html.split('<script>')[1].split('</script>')[0];
function setup(script,serverReply){
 const names=['ok','qrTools','cancelTicketButton','cancelError','cancelNote','expiry','direction','cardLabel','cardVal','codeLabel','codeVal','fortuneLabel','fortuneText','legs','qr','copy'];
 const nodes={};for(const name of names)nodes[name]={style:{},classList:{toggle(){}},textContent:'',innerHTML:'',disabled:false};
 const context={...nodes,document:{getElementById:id=>nodes[id],addEventListener(){}},confirm:()=>true,
  fetch:async()=>({ok:true,status:200,json:async()=>serverReply}),setInterval(){},navigator:{clipboard:{}},location:{href:''},prompt(){}};
 vm.createContext(context);vm.runInContext(script,context);return {context,nodes};
}
(async()=>{
 let a=setup(js,{ok:true,cancelled:true});
 assert.strictEqual(a.nodes.cancelTicketButton.style.display,'block');
 assert.strictEqual(a.nodes.fortuneText.textContent,vm.runInContext('data.fortune',a.context));
 a.context.toggle();assert.strictEqual(a.nodes.fortuneText.textContent,vm.runInContext('data.fortune_tr',a.context));a.context.toggle();
 await a.context.cancelTicket();
 assert.strictEqual(a.nodes.ok.textContent,'БРОНЬ ОТМЕНЕНА');
 assert.strictEqual(a.nodes.qrTools.style.display,'none');
 assert.strictEqual(a.nodes.cancelTicketButton.style.display,'none');
 let b=setup(js,{cancelled:true,can_cancel:false,comments:[]});
 await b.context.refreshComments();
 assert.strictEqual(b.nodes.ok.textContent,'БРОНЬ ОТМЕНЕНА');
 assert.strictEqual(b.nodes.qrTools.style.display,'none');
 const started=js.replace('"can_cancel": true','"can_cancel": false');
 assert.notStrictEqual(started,js);
 let c=setup(started,{});
 assert.strictEqual(c.nodes.cancelTicketButton.style.display,'none');
 assert(c.nodes.cancelNote.textContent.includes('Все рейсы уже отправились'));
 console.log('PASS: passenger cancellation UI, dispatcher cancellation refresh and departure lock');
})().catch(e=>{console.error(e);process.exit(1)});
