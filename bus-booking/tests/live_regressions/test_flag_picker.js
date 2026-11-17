'use strict';
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
class Element {
 constructor(tag){this.tag=tag;this.children=[];this.listeners={};this.dataset={};this.style={};this.open=false;this.textContent='';}
 append(...x){this.children.push(...x)}
 appendChild(x){this.append(x)}
 setAttribute(k,v){this[k]=v}
 addEventListener(n,fn){(this.listeners[n]??=[]).push(fn)}
 dispatchEvent(e){for(const fn of this.listeners[e.type]||[])fn(e)}
 insertAdjacentElement(_where,element){this.inserted=element}
 contains(target){return this===target||this.children.some(x=>x.contains(target))}
 focus(){this.focused=true}
}
const sel=new Element('select');
const codes=['tr','ru','ky','uz','tg','kk','tk','en','zh'];
const labels=['🇹🇷 Türkçe','🇷🇺 Русский','🇰🇬 Кыргызча','🇺🇿 O‘zbekcha','🇹🇯 Тоҷикӣ','🇰🇿 Қазақша','🇹🇲 Türkmençe','🇬🇧 English','🇨🇳 简体中文'];
sel.options=codes.map((code,i)=>({value:code,textContent:labels[i]}));sel.value='tr';let changes=[];
sel.addEventListener('change',()=>changes.push(sel.value));
const doc=new Element('document');doc.head=new Element('head');doc.readyState='loading';doc.getElementById=id=>id==='languageSelect'?sel:null;doc.createElement=tag=>new Element(tag);
const ctx={window:{},document:doc,Event:class {constructor(type){this.type=type}},console};vm.createContext(ctx);
vm.runInContext(fs.readFileSync(require('node:path').resolve(__dirname,'../../static/passenger-i18n.js'),'utf8'),ctx);
doc.dispatchEvent({type:'DOMContentLoaded'});
assert.equal(sel.hidden,true);const d=sel.inserted;assert(d);
const [summary,menu]=d.children;
assert.equal(menu.children.length,9);
assert.equal(summary.children[1].textContent,'Türkçe');
for (let i=0;i<9;i++){
 const b=menu.children[i];assert.equal(b.dataset.lang,codes[i]);
 assert.ok(b.children[0].src.startsWith('data:image/png;base64,'));
 assert(!/[🇦-🇿]/u.test(b.children[1].textContent));
 d.open=true;b.dispatchEvent({type:'click'});
 assert.equal(sel.value,codes[i]);assert.equal(summary.children[1].textContent,b.children[1].textContent);
 assert.equal(summary.children[0].src,b.children[0].src);assert.equal(d.open,false);
}
assert.equal(changes.length,9);
d.open=true;d.dispatchEvent({type:'keydown',key:'Escape'});assert.equal(d.open,false);assert(summary.focused);
d.open=true;doc.dispatchEvent({type:'click',target:new Element('outside')});assert.equal(d.open,false);
console.log('PASS: 9 flag images, menu and language changes, keyboard Escape and outside click');
