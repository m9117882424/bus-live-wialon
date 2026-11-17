'use strict';
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').resolve(__dirname,'../../static/scanner.html'),'utf8');
const match=html.match(/<script>\s*([\s\S]*?)<\/script>/);
assert.ok(match,'Driver scanner JS missing');
new vm.Script(match[1]);

const events={},nodes={};
function element(id){
 if(nodes[id])return nodes[id];
 const item={id,value:'',textContent:'',title:'',hidden:['result','pinPanel','pinFeedback','installHint','camera'].includes(id),
   disabled:false,style:{},listeners:{},classList:{toggle(){}},
   addEventListener(type,callback){this.listeners[type]=callback},
   setAttribute(name,value){this[name]=value},focus(){},replaceChildren(){},scrollIntoView(){}};
 nodes[id]=item;return item;
}
const document={documentElement:{lang:'ru'},hidden:false,getElementById:element,addEventListener(){}};
const window={addEventListener(type,callback){events[type]=callback}};
const memory=new Map();
const localStorage={getItem:k=>memory.get(k)||null,setItem:(k,v)=>memory.set(k,v),removeItem:k=>memory.delete(k)};
let standalone=false, mediaChange;
const navigator={userAgent:'Mozilla/5.0 (Linux; Android 16) Chrome/157.0.0.0'};
const context=vm.createContext({window,document,navigator,localStorage,matchMedia:()=>({
 get matches(){return standalone},addEventListener(type,fn){mediaChange=fn}
}),console,Intl,Date,URL,AbortController,setTimeout:()=>1,clearTimeout:()=>{},setInterval:()=>1,clearInterval:()=>{},
 performance:{now:()=>0},fetch:async()=>({ok:true,json:async()=>({})})});
vm.runInContext(match[1],context);
assert.equal(nodes.installPanel.hidden,false,'Install entry visible in browser');
assert.ok(html.indexOf('id="installBtn"')<html.indexOf('id="pinBtn"'),'Small install icon next to lock in header');
assert.ok(html.includes('class="secondary install-btn"'),'Compact header style');
assert.equal((html.match(/id="installPanel"/g)||[]).length,1,'No duplicate installation panel');
assert.match(nodes.installBtn.title,/Chrome/);
assert.equal(nodes.installHint.hidden,true);
assert.equal(typeof events.beforeinstallprompt,'function');
assert.equal(typeof events.appinstalled,'function');
assert.equal(typeof mediaChange,'function');
assert.ok(html.includes('/bus/scanner/manifest.webmanifest'));
assert.ok(html.includes('/bus/scanner/sw.js'));

(async()=>{
 await nodes.installBtn.listeners.click();
 assert.equal(nodes.installHint.hidden,false,'Fallback installation guide shown');
 assert.match(nodes.installHint.textContent,/⋮/,'Android Chrome instructions');
 nodes.langBtn.listeners.click();
 assert.match(nodes.installBtn.title,/Chrome/);
 assert.match(nodes.installHint.textContent,/Chrome/);
 assert.match(nodes.installHint.textContent,/Uygulamayı yükle/);
 let prevented=0,prompts=0;
 events.beforeinstallprompt({preventDefault(){prevented++},async prompt(){prompts++}});
 assert.equal(prevented,1,'Native Chrome prompt captured');
 await nodes.installBtn.listeners.click();
 assert.equal(prompts,1,'Native install dialog opened on click');
 events.appinstalled();
 assert.equal(nodes.installPanel.hidden,true,'CTA hidden after install');
 standalone=true;
 vm.runInContext('updateInstallUI()',context);
 assert.equal(nodes.installPanel.hidden,true,'CTA hidden in standalone mode');
 standalone=false;
 mediaChange();
 assert.equal(nodes.installPanel.hidden,false,'CTA visible in browser again');
 navigator.userAgent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/157.0.0.0';
 await nodes.installBtn.listeners.click();
 assert.match(nodes.installHint.textContent,/адресной строке|adres çubuğundaki/,'Desktop Chrome fallback');
 for(const marker of ['pinBtn','manualBtn','scanBtn'])assert.ok(nodes[marker],'Existing scanner control '+marker);
 console.log('PASS: Chrome driver install CTA, native prompt, Android RU/TR fallback, installed/standalone detection, scanner controls');
})().catch(e=>{console.error(e);process.exitCode=1});
