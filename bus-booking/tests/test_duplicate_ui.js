// Exercise the QR/lookup confirmation without disclosing the private booking token.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync(process.argv[2],'utf8');
const script=html.split('<script>')[1].split('</script>')[0];
function setup(stored,ok=true){
 let sent=[],reloads=0;
 const nodes={cancelOpen:{style:{},disabled:false},cancelForm:{style:{}},cancelLink:{value:'',focus(){}},cancelMsg:{textContent:''}};
 const context={...nodes,URL,document:{hidden:false,getElementById:()=>({value:'tr'})},setInterval(){},confirm:()=>true,
   localStorage:{getItem:()=>stored,removeItem(){}},location:{href:'https://example.test/bus/check/code/signature',reload:()=>reloads++},
   fetch:async(url,options)=>{sent.push({url,body:JSON.parse(options.body)});return {ok,json:async()=>ok?{ok:true}:{detail:'Not found'}}}};
 vm.createContext(context);vm.runInContext(script,context);return {context,nodes,sent,get reloads(){return reloads}};
}
(async()=>{
 const manual=setup('');manual.context.openCancel();assert.strictEqual(manual.nodes.cancelForm.style.display,'block');assert.strictEqual(manual.sent.length,0);
 manual.context.downloadCheck('pdf');assert(manual.context.location.href.includes('/download/pdf?lang=tr'));
 manual.context.location.href='https://example.test/bus/check/code/signature';
 manual.nodes.cancelLink.value='https://old.example/bus/ticket/abcdefghijklmnopqrstuv1234567890';await manual.context.cancelExisting();
 assert.strictEqual(manual.sent.length,1);assert(manual.sent[0].url.endsWith('/abcdefghijklmnopqrstuv1234567890/cancel-for-card'));
 assert.strictEqual(manual.sent[0].body.card,'123456');assert.strictEqual(manual.sent[0].body.code,vm.runInContext('checkCode',manual.context));assert.strictEqual(manual.reloads,1);
 const mismatch=setup('abcdefghijklmnopqrstuv1234567890',false);await mismatch.context.openCancel();
 assert.strictEqual(mismatch.reloads,0);assert.strictEqual(mismatch.nodes.cancelForm.style.display,'block');
 console.log('PASS: public confirmation requests private link for cancellation and validates its booking');
})().catch(e=>{console.error(e);process.exit(1)});
