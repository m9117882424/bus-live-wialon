'use strict';
const assert=require('node:assert/strict'),puppeteer=require('puppeteer');
const origin=process.argv[2],privatePath=process.argv[3],publicPath=process.argv[4],errors=[];
const han=/[\u3400-\u9fff]/;
(async()=>{const browser=await puppeteer.launch({headless:true,args:['--no-sandbox','--disable-dev-shm-usage']});try{
 const page=await browser.newPage();page.on('pageerror',e=>errors.push(e.message));await page.setViewport({width:390,height:844});await page.goto(origin+'/bus/',{waitUntil:'domcontentloaded'});await page.waitForSelector('.flag-picker summary');await page.click('.flag-picker summary');await page.click('.flag-picker-menu button[data-lang="zh"]');
 await page.waitForFunction(()=>localStorage.getItem('bus-lang')==='zh');assert(han.test(await page.$eval('h1',e=>e.textContent)));assert.equal(await page.$$eval('.flag-picker-menu button',a=>a.length),9);
 const flag=await page.$eval('.flag-picker summary img',e=>({ok:e.complete&&e.naturalWidth>0,src:e.src}));assert(flag.ok&&flag.src.startsWith('data:image/png;base64,'));
 await page.reload({waitUntil:'domcontentloaded'});await page.waitForFunction(()=>document.getElementById('languageSelect').value==='zh');assert(han.test(await page.$eval('h1',e=>e.textContent)));
 await page.goto(origin+privatePath,{waitUntil:'domcontentloaded'});await page.waitForFunction(()=>/[\u3400-\u9fff]/.test(document.getElementById('fortuneText').textContent));assert(han.test(await page.$eval('#ok',e=>e.textContent)));assert.match(await page.$eval('#legs',e=>e.textContent),/Камп/);
 await page.goto(origin+publicPath,{waitUntil:'domcontentloaded'});await page.waitForFunction(()=>/[\u3400-\u9fff]/.test(document.getElementById('checkFortuneText').textContent));assert(han.test(await page.$eval('#checkOk',e=>e.textContent)));await page.select('#fileLang','zh');assert.equal(await page.$eval('#fileLang',e=>e.value),'zh');
 await page.goto(origin+'/bus/',{waitUntil:'domcontentloaded'});await page.waitForSelector('#trips .route button');await page.click('#trips .route button');await page.type('#card','999002');await page.click('#submit');await page.waitForFunction(()=>location.pathname.startsWith('/bus/ticket/')&&typeof data!=='undefined'&&data.lang==='zh');assert(han.test(await page.$eval('#fortuneText',e=>e.textContent)));assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true);
 assert.deepEqual(errors,[]);console.log('PASS: real Chrome Chinese flag, saved selection, existing RU ticket wishes, public confirmation, file language and Chinese passenger booking');
}finally{await browser.close()}})().catch(e=>{console.error(e.stack,errors);process.exitCode=1});
