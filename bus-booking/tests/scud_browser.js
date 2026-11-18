'use strict';
const assert=require('node:assert/strict');
let puppeteer;try{puppeteer=require('puppeteer')}catch{puppeteer=require('puppeteer-core')}
const [admin,analytics,day,one,two]=process.argv.slice(2);
(async()=>{
 const browser=await puppeteer.launch({headless:true,executablePath:process.env.ANALYTICS_BROWSER_EXECUTABLE||undefined,args:['--no-sandbox','--disable-dev-shm-usage']});
 const errors=[];
 try{
  const page=await browser.newPage();page.on('pageerror',e=>errors.push(e.message));await page.authenticate({username:'admin',password:'synthetic-scud-only'});
  await page.evaluateOnNewDocument(()=>localStorage.setItem('bus-lang','ru'));
  await page.setViewport({width:1500,height:1000});await page.goto(admin+'/bus/admin',{waitUntil:'networkidle0'});
  assert.equal(await page.$$eval('.admin-tabs button',items=>items.length),6);
  await page.click('#whiteTab');await page.waitForFunction(()=>document.getElementById('scudTotal').textContent.includes('0'));
  assert.equal(await page.$eval('#scudBox',e=>e.closest('section[id="whitePanel"]').id),'whitePanel');
  await (await page.$('#scudFiles')).uploadFile(one,two);await page.click('#scudImport');
  await page.waitForFunction(()=>!document.getElementById('scudImport').disabled&&document.getElementById('scudResult').textContent.includes('Загружено файлов: 2'),{timeout:60000});
  await page.waitForFunction(()=>document.getElementById('scudTotal').textContent.includes('Сохранено отметок: 8'));
  assert.equal(await page.$eval('#scudError',e=>e.hidden),true);
  await (await page.$('#scudFiles')).uploadFile(one);await page.click('#scudImport');
  await page.waitForFunction(()=>!document.getElementById('scudImport').disabled&&document.getElementById('scudResult').textContent.includes('Новых отметок: 0'),{timeout:60000});
  await page.click('button.headbutton');assert((await page.$eval('#scudBox h2',e=>e.textContent)).includes('SKUD'));
  for(const width of [1500,1024,390,320]){
   await page.setViewport({width,height:1000});assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Admin overflow '+width);
   if(width>=1024)assert.equal(await page.$$eval('.admin-tabs button',items=>new Set(items.map(e=>Math.round(e.getBoundingClientRect().top))).size),1);
  }
  const context=await browser.createBrowserContext(),view=await context.newPage();view.on('pageerror',e=>errors.push(e.message));
  await view.setViewport({width:1500,height:1000});await view.goto(analytics+'/analytics/?start='+day+'&end='+day,{waitUntil:'networkidle0'});
  await view.waitForSelector('#metrics-scud .stat-value');assert.deepEqual(await view.$$eval('#metrics-scud .stat-value',items=>items.map(e=>e.textContent)),['4','4']);
  const free=await view.$$eval('#metrics-secondary .stat',items=>items.find(e=>e.textContent.includes('Свободно мест по броням'))?.querySelector('.stat-value').textContent);
  assert.equal(free,'8');assert.deepEqual(await view.$$eval('tr.trip .scud-count',items=>items.map(e=>e.textContent)),['3','1']);
  assert.deepEqual(await view.$$eval('tr.trip .scud-free',items=>items.map(e=>e.textContent)),['1','3']);
  const headers=await view.$$eval('.details thead th',items=>items.map(e=>e.textContent));assert(headers.includes('Свободно мест по броням')&&headers.includes('Свободно мест по СКУД'));
  const report=await (await fetch(analytics+'/analytics/api/report?start='+day+'&end='+day)).json();
  for(const forbidden of ['900001','DO_NOT_SAVE_FIRST','PASSPORT_SECRET','KIMLIK_SECRET','"card":'])assert(!JSON.stringify(report).includes(forbidden));
  for(const width of [1500,1024,390,320]){await view.setViewport({width,height:1000});assert(await view.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Analytics overflow '+width)}
  assert.deepEqual(errors,[]);
  console.log('PASS: SCUD upload in whitelist tab, multi-file+duplicate handling, RU/TR, separate free-seat counts, anonymous privacy and desktop/mobile');
 }finally{await browser.close()}
})().catch(error=>{console.error(error.stack);process.exitCode=1});