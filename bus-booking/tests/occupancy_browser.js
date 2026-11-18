'use strict';
const assert=require('node:assert/strict');
let puppeteer;try{puppeteer=require('puppeteer')}catch{puppeteer=require('puppeteer-core')}
const [origin,today,tomorrow,forwardLegId]=process.argv.slice(2);
const authorization='Basic '+Buffer.from('admin:synthetic-occupancy').toString('base64');
(async()=>{
 const browser=await puppeteer.launch({headless:true,executablePath:process.env.ANALYTICS_BROWSER_EXECUTABLE||undefined,args:['--no-sandbox','--disable-dev-shm-usage']});
 const errors=[];
 try{
  const page=await browser.newPage();page.on('pageerror',error=>errors.push(error.message));
  await page.emulateTimezone('Pacific/Kiritimati');
  await page.authenticate({username:'admin',password:'synthetic-occupancy'});
  await page.evaluateOnNewDocument(()=>localStorage.setItem('bus-lang','ru'));
  await page.setViewport({width:1500,height:1000});
  await page.goto(origin+'/bus/admin',{waitUntil:'networkidle0'});
  await page.click('#occupancyTab');
  await page.waitForFunction(()=>document.querySelectorAll('.occupancy-trip').length===1);
  assert.deepEqual(await page.$$eval('#occupancyDate option',items=>items.map(item=>item.value)),[today,tomorrow]);
  assert((await page.$eval('#occupancyDate option',item=>item.textContent)).includes(today.split('-').reverse().join('.')));
  await page.select('#occupancyDate',tomorrow);
  await page.waitForFunction(()=>document.querySelectorAll('.occupancy-trip').length===2);
  assert.deepEqual(await page.$$eval('.occupancy-booked',items=>items.map(item=>item.textContent)),['1','1']);
  assert.deepEqual(await page.$$eval('.occupancy-free',items=>items.map(item=>item.textContent)),['3','3']);
  assert((await page.$eval('#occupancyTrips',element=>element.textContent)).includes('<fixture>'));
  const response=await fetch(origin+'/bus/api/admin/history?date_from='+tomorrow+'&date_to='+tomorrow,{headers:{Authorization:authorization}});
  const history=await response.json(),leg=history.find(item=>item.direction==='forward');
  assert(leg&&typeof leg.code==='string'&&Number.isInteger(Number(forwardLegId)));
  const cancelled=await fetch(origin+'/bus/api/admin/bookings/'+encodeURIComponent(leg.code)+'/cancel',{method:'POST',headers:{Authorization:authorization,'Content-Type':'application/json'},body:JSON.stringify({confirm:true,leg_id:Number(forwardLegId)})});
  assert.equal(cancelled.status,200);
  await page.waitForFunction(()=>document.querySelector('.occupancy-trip[data-direction="forward"] .occupancy-booked').textContent==='0',{timeout:40000});
  assert.deepEqual(await page.$$eval('.occupancy-free',items=>items.map(item=>item.textContent)),['4','3']);
  await page.setRequestInterception(true);
  let fail=true;
  page.on('request',request=>{
   if(fail&&request.url().includes('/api/admin/occupancy')){fail=false;request.respond({status:503,contentType:'application/json',body:'{"detail":"Synthetic refresh failure"}'});}
   else request.continue();
  });
  await page.select('#occupancyDate',today);
  await page.waitForFunction(()=>!document.getElementById('occupancyError').hidden);
  assert.equal(await page.$eval('#occupancyDate',element=>element.value),tomorrow,'A failed date change must restore the date of displayed data');
  assert.equal(await page.$$eval('.occupancy-trip',items=>items.length),2);
  await page.click('button.headbutton');
  assert.equal(await page.$eval('#occupancyTab',element=>element.textContent),'Doluluk');
  assert((await page.$eval('#occupancyTrips',element=>element.textContent)).includes('Kayıtlı'));
  assert.equal(await page.$eval('#occupancyError',element=>element.hidden),false,'Language changes must retain refresh failure warning');
  for(const width of [1500,390,320]){
   await page.setViewport({width,height:1000});
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Occupancy page overflows at '+width);
  }
  await page.click('#scheduleTab');
  assert.equal(await page.$eval('#occupancyPanel',element=>getComputedStyle(element).display),'none');
  assert.equal(await page.$eval('#managePanel',element=>getComputedStyle(element).display),'block');
  assert.deepEqual(errors,[]);
  console.log('PASS: admin occupancy dates, separate seat counts, automatic cancellation refresh, failed-date warning, RU/TR, mobile layout and tab isolation');
 }finally{await browser.close()}
})().catch(error=>{console.error(error.stack);process.exitCode=1});
