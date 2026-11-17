'use strict';
const assert=require('node:assert/strict');
let puppeteer;try{puppeteer=require('puppeteer')}catch{puppeteer=require('puppeteer-core')}
const origin=process.argv[2],errors=[];
(async()=>{
 const browser=await puppeteer.launch({headless:true,executablePath:process.env.ANALYTICS_BROWSER_EXECUTABLE||undefined,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  const page=await browser.newPage();page.on('pageerror',e=>errors.push(e.message));
  await page.setViewport({width:1600,height:1000});
  await page.goto(origin+'/analytics/?start=2026-11-16&end=2026-11-17',{waitUntil:'networkidle0'});
  await page.waitForSelector('#metrics-primary .stat-value');
  assert.deepEqual(await page.$$eval('#metrics-primary .stat-value',e=>e.map(x=>x.textContent)),['8','4','4']);
  assert.equal(await page.$$eval('#bookings-chart .bar-group',e=>e.length),2);
  assert.equal(await page.$$eval('#bookings-chart .bar-group .bar',e=>e.length),4);
  assert.equal(await page.$$eval('#bookings-chart .bar-group',e=>e.every(g=>g.children[0].classList.contains('bar')&&g.children[1].classList.contains('bar'))),true,'Bookings and boardings must be sibling columns');
  assert.equal(await page.$$eval('.trip',e=>e.length),4);
  await page.click('[data-date="2026-11-17"]');assert.equal(await page.$$eval('.trip',e=>e.length),0);
  await page.click('#expand');assert.equal(await page.$$eval('.trip',e=>e.length),8);
  await page.click('[data-group="2026-11-17forward"]');assert.equal(await page.$$eval('.trip',e=>e.length),6);
  await page.select('#direction','reverse');await page.click('#apply');
  await page.waitForFunction(()=>document.querySelector('#metrics-primary .stat-value').textContent==='4');
  assert.equal(await page.$eval('#metrics-secondary .stat-value',e=>e.textContent),'2');
  await page.select('#direction','all');
  await page.$eval('#start',e=>e.value='2026-11-16');await page.$eval('#end',e=>e.value='2026-11-16');
  await page.click('#apply');
  await page.waitForFunction(()=>document.querySelector('#metrics-secondary .stat-value').textContent==='4');
  const href=await page.$eval('#export',e=>e.href),query=new URL(href).searchParams;
  assert.equal(query.get('start'),'2026-11-16');assert.equal(query.get('end'),'2026-11-16');assert.equal(query.get('direction'),'all');
  const download=await fetch(href);assert.equal(download.status,200);assert.match(download.headers.get('content-disposition'),/attachment/);assert.equal(Buffer.from(await download.arrayBuffer()).subarray(0,2).toString(),'PK');
  for(const width of [1600,1024,390,320]){
   await page.setViewport({width,height:1000});
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Page overflows at '+width);
  }
  await page.setViewport({width:1600,height:1000});
  if(process.env.ANALYTICS_SCREENSHOT)await page.screenshot({path:process.env.ANALYTICS_SCREENSHOT,fullPage:true});
  assert.deepEqual(errors,[]);
  console.log('PASS: anonymous page, paired columns, date/group drilldown, filters, XLSX and desktop/mobile layout');
 }finally{await browser.close()}
})().catch(e=>{console.error(e.stack,errors);process.exitCode=1});
