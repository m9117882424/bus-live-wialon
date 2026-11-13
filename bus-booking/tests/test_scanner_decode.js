const fs=require('fs'),assert=require('assert'),jsQR=require('../static/vendor/jsQR.js');
const bytes=fs.readFileSync(process.argv[2]),size=Number(process.argv[3]),expected=process.argv[4];
const decoded=jsQR(new Uint8ClampedArray(bytes),size,size,{inversionAttempts:'attemptBoth'});
assert(decoded&&decoded.data===expected);
console.log('PASS: bundled local QR decoder reads a real QR image');
