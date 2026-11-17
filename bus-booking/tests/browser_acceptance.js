'use strict';
const assert=require('node:assert/strict'),puppeteer=require('puppeteer');
const origin=process.argv[2],errors=[];
async function textButton(page,re){for(const b of await page.$$('button')){if(await b.boundingBox()&&re.test((await b.evaluate(e=>e.textContent)).trim())){await b.click();return}}throw Error('Visible button not found: '+re)}
async function setInput(page,selector,value){await page.$eval(selector,(e,v)=>{e.value=v;e.dispatchEvent(new Event('input',{bubbles:true}))},value)}
async function login(page,password){await page.waitForSelector('input[autocomplete="username"]',{visible:true});await setInput(page,'input[autocomplete="username"]','e2edriver');await setInput(page,'input[autocomplete="current-password"]',password);await textButton(page,/^(Войти|Giriş yap)$/);await page.waitForFunction(()=>document.getElementById('pinBtn').classList.contains('ready'))}
(async()=>{
 const browser=await puppeteer.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  const admin=await browser.newPage();admin.on('pageerror',e=>errors.push('admin: '+e.message));await admin.authenticate({username:'admin',password:'synthetic-admin-only'});await admin.setViewport({width:1250,height:900});await admin.goto(origin+'/bus/admin',{waitUntil:'domcontentloaded'});await admin.click('#settingsTab');await admin.waitForFunction(()=>document.getElementById('driverCount').textContent==='0/10');
  await setInput(admin,'#driverLogin','e2edriver');await setInput(admin,'#driverName','BUS 01');await setInput(admin,'#driverPassword','SyntheticTest_2026');await admin.click('#driverSaveButton');await admin.waitForFunction(()=>document.getElementById('driverCount').textContent==='1/10');
  console.log('Admin create passed'); const context=await browser.createBrowserContext(),scanner=await context.newPage();scanner.on('pageerror',e=>errors.push('scanner: '+e.message));await scanner.setViewport({width:390,height:844});await scanner.goto(origin+'/bus/scanner/',{waitUntil:'domcontentloaded'});await login(scanner,'SyntheticTest_2026');console.log('Scanner login passed');
  await scanner.reload({waitUntil:'domcontentloaded'});await scanner.waitForFunction(()=>document.getElementById('pinBtn').classList.contains('ready'));
  assert.equal(await scanner.evaluate(()=>localStorage.getItem('bus-scanner-code')),null);
  await setInput(scanner,'#manualInput','999001');await scanner.click('#manualBtn');await scanner.waitForFunction(()=>document.getElementById('result').classList.contains('good'));
  assert.match(await scanner.$eval('#resultCard',e=>e.textContent),/999001/);console.log('Manual boarding passed');
  await admin.click('#driverList .mini-route button');assert.equal(await admin.$eval('#driverPassword',e=>e.value),'');await setInput(admin,'#driverName','BUS 01 updated');await admin.click('#driverSaveButton');await admin.waitForFunction(()=>document.getElementById('driverList').textContent.includes('BUS 01 updated'));
  await scanner.reload({waitUntil:'domcontentloaded'});await scanner.waitForFunction(()=>document.getElementById('pinBtn').classList.contains('ready'));
  await admin.click('#driverList .mini-route button');await setInput(admin,'#driverPassword','SyntheticReset_2026');await admin.click('#driverSaveButton');await admin.waitForFunction(()=>document.getElementById('driverPassword').value==='');
  await setInput(scanner,'#manualInput','999001');await scanner.click('#manualBtn');await scanner.waitForFunction(()=>!document.getElementById('pinBtn').classList.contains('ready'));
  await login(scanner,'SyntheticReset_2026');
  await scanner.click('#langBtn');assert.equal(await scanner.$eval('html',e=>e.lang),'tr');assert.equal(await scanner.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'Mobile scanner overflows horizontally');
  await scanner.click('#pinBtn');await textButton(scanner,/^(Выйти|Çıkış yap)$/);await scanner.waitForFunction(()=>!document.getElementById('pinBtn').classList.contains('ready'));
  assert.equal(await scanner.evaluate(async()=>{const r=await fetch('/bus/api/scanner/session');return r.status}),401);
  await scanner.reload({waitUntil:'domcontentloaded'});await scanner.waitForSelector('input[autocomplete="username"]',{visible:true});
  assert.deepEqual(errors,[]);console.log('PASS: headless Chrome real admin/driver workflow and mobile layout');
 }finally{await browser.close()}
})().catch(e=>{console.error(e.stack,errors);process.exitCode=1});
