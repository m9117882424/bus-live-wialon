'use strict';
const assert=require('node:assert/strict');
let puppeteer;try{puppeteer=require('puppeteer')}catch{puppeteer=require('puppeteer-core')}
(async()=>{
 const origin=process.argv[2],errors=[];
 const browser=await puppeteer.launch({headless:true,executablePath:process.env.ANALYTICS_BROWSER_EXECUTABLE||undefined,args:['--no-sandbox','--disable-dev-shm-usage']});
 try{
  const page=await browser.newPage();page.on('pageerror',error=>errors.push(error.message));
  await page.setViewport({width:1500,height:1100});
  await page.goto(origin+'/analytics/?start=2026-11-16&end=2026-11-17',{waitUntil:'networkidle0'});
  await page.waitForSelector('#metrics-execution .stat-value');
  assert((await page.$eval('#methodology',item=>item.textContent)).includes('8 - КПП АККУЮ'));
  assert.equal(await page.$eval('.details thead th:last-child',item=>item.textContent),'Прибытие на КПП');
  assert.deepEqual(await page.$$eval('#metrics-execution .stat-value',items=>items.map(item=>item.textContent)),['3','4']);
  assert((await page.$eval('#execution-state',item=>item.textContent)).includes('недостаточно GPS: 1'));
  await page.click('#expand');
  assert.equal(await page.$$eval('.execution-status.performed',items=>items.length),3);
  assert.equal(await page.$$eval('.execution-status.not_performed',items=>items.length),4);
  assert.equal(await page.$$eval('.execution-status.unknown',items=>items.length),1);
  assert((await page.$eval('.execution-status.not_performed',item=>item.getAttribute('title'))).includes('GPS'));
  const report=await (await fetch(origin+'/analytics/api/report?start=2026-11-16&end=2026-11-17')).json();
  assert.equal(report.wialon.geofences.kpp,'8 - КПП АККУЮ');
  assert.equal(report.wialon.origin_min_points,2);
  for(const secret of ['coverage','query_from','query_to','point_count','private-card','private-token'])assert(!JSON.stringify(report).includes(secret));
  for(const width of [1500,1024,390,320]){
   await page.setViewport({width,height:1100});
   assert(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'Execution layout overflows at '+width);
  }
  await page.setViewport({width:1500,height:1100});
  if(process.env.GPS_SCREENSHOT)await page.screenshot({path:process.env.GPS_SCREENSHOT,fullPage:true});
  assert.deepEqual(errors,[]);
  console.log('PASS: performed / not performed / GPS unknown cards, row statuses, privacy and desktop/mobile layout');
 }finally{await browser.close()}
})().catch(error=>{console.error(error.stack);process.exitCode=1});
