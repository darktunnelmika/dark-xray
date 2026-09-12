/*
THIRD-PARTY NOTICE — QR structural tables adapted from python-qrcode 8.2.
Copyright (c) 2011, Lincoln Loop
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

    * Redistributions of source code must retain the above copyright notice,
      this list of conditions and the following disclaimer.
    * Redistributions in binary form must reproduce the above copyright notice,
      this list of conditions and the following disclaimer in the documentation
      and/or other materials provided with the distribution.
    * Neither the package name nor the names of its contributors may be
      used to endorse or promote products derived from this software without
      specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE 
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON
ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.


-------------------------------------------------------------------------------


Original text and license from the pyqrnative package where this was forked
from (http://code.google.com/p/pyqrnative):

#Ported from the Javascript library by Sam Curren
#
#QRCode for Javascript
#http://d-project.googlecode.com/svn/trunk/misc/qrcode/js/qrcode.js
#
#Copyright (c) 2009 Kazuhiko Arase
#
#URL: http://www.d-project.com/
#
#Licensed under the MIT license:
#   http://www.opensource.org/licenses/mit-license.php
#
# The word "QR Code" is registered trademark of
# DENSO WAVE INCORPORATED
#   http://www.denso-wave.com/qrcode/faqpatent-e.html

*/
/* Offline byte-mode QR encoder. QR versions 1–40, ECC L, mask 0.
 * Pattern and Reed–Solomon block constants derived from python-qrcode (BSD-3-Clause).
 * See THIRD-PARTY-NOTICES.md. No network calls. Tested against an independent
 * Python qrcode encoder with forced byte mode and the same mask.
 */
const DARK_QR_BLOCKS=[[[26,19]],[[44,34]],[[70,55]],[[100,80]],[[134,108]],[[86,68],[86,68]],[[98,78],[98,78]],[[121,97],[121,97]],[[146,116],[146,116]],[[86,68],[86,68],[87,69],[87,69]],[[101,81],[101,81],[101,81],[101,81]],[[116,92],[116,92],[117,93],[117,93]],[[133,107],[133,107],[133,107],[133,107]],[[145,115],[145,115],[145,115],[146,116]],[[109,87],[109,87],[109,87],[109,87],[109,87],[110,88]],[[122,98],[122,98],[122,98],[122,98],[122,98],[123,99]],[[135,107],[136,108],[136,108],[136,108],[136,108],[136,108]],[[150,120],[150,120],[150,120],[150,120],[150,120],[151,121]],[[141,113],[141,113],[141,113],[142,114],[142,114],[142,114],[142,114]],[[135,107],[135,107],[135,107],[136,108],[136,108],[136,108],[136,108],[136,108]],[[144,116],[144,116],[144,116],[144,116],[145,117],[145,117],[145,117],[145,117]],[[139,111],[139,111],[140,112],[140,112],[140,112],[140,112],[140,112],[140,112],[140,112]],[[151,121],[151,121],[151,121],[151,121],[152,122],[152,122],[152,122],[152,122],[152,122]],[[147,117],[147,117],[147,117],[147,117],[147,117],[147,117],[148,118],[148,118],[148,118],[148,118]],[[132,106],[132,106],[132,106],[132,106],[132,106],[132,106],[132,106],[132,106],[133,107],[133,107],[133,107],[133,107]],[[142,114],[142,114],[142,114],[142,114],[142,114],[142,114],[142,114],[142,114],[142,114],[142,114],[143,115],[143,115]],[[152,122],[152,122],[152,122],[152,122],[152,122],[152,122],[152,122],[152,122],[153,123],[153,123],[153,123],[153,123]],[[147,117],[147,117],[147,117],[148,118],[148,118],[148,118],[148,118],[148,118],[148,118],[148,118],[148,118],[148,118],[148,118]],[[146,116],[146,116],[146,116],[146,116],[146,116],[146,116],[146,116],[147,117],[147,117],[147,117],[147,117],[147,117],[147,117],[147,117]],[[145,115],[145,115],[145,115],[145,115],[145,115],[146,116],[146,116],[146,116],[146,116],[146,116],[146,116],[146,116],[146,116],[146,116],[146,116]],[[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[146,116],[146,116],[146,116]],[[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115]],[[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[146,116]],[[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[145,115],[146,116],[146,116],[146,116],[146,116],[146,116],[146,116]],[[151,121],[151,121],[151,121],[151,121],[151,121],[151,121],[151,121],[151,121],[151,121],[151,121],[151,121],[151,121],[152,122],[152,122],[152,122],[152,122],[152,122],[152,122],[152,122]],[[151,121],[151,121],[151,121],[151,121],[151,121],[151,121],[152,122],[152,122],[152,122],[152,122],[152,122],[152,122],[152,122],[152,122],[152,122],[152,122],[152,122],[152,122],[152,122],[152,122]],[[152,122],[152,122],[152,122],[152,122],[152,122],[152,122],[152,122],[152,122],[152,122],[152,122],[152,122],[152,122],[152,122],[152,122],[152,122],[152,122],[152,122],[153,123],[153,123],[153,123],[153,123]],[[152,122],[152,122],[152,122],[152,122],[153,123],[153,123],[153,123],[153,123],[153,123],[153,123],[153,123],[153,123],[153,123],[153,123],[153,123],[153,123],[153,123],[153,123],[153,123],[153,123],[153,123],[153,123]],[[147,117],[147,117],[147,117],[147,117],[147,117],[147,117],[147,117],[147,117],[147,117],[147,117],[147,117],[147,117],[147,117],[147,117],[147,117],[147,117],[147,117],[147,117],[147,117],[147,117],[148,118],[148,118],[148,118],[148,118]],[[148,118],[148,118],[148,118],[148,118],[148,118],[148,118],[148,118],[148,118],[148,118],[148,118],[148,118],[148,118],[148,118],[148,118],[148,118],[148,118],[148,118],[148,118],[148,118],[149,119],[149,119],[149,119],[149,119],[149,119],[149,119]]];
const DARK_QR_POSITIONS=[[],[6,18],[6,22],[6,26],[6,30],[6,34],[6,22,38],[6,24,42],[6,26,46],[6,28,50],[6,30,54],[6,32,58],[6,34,62],[6,26,46,66],[6,26,48,70],[6,26,50,74],[6,30,54,78],[6,30,56,82],[6,30,58,86],[6,34,62,90],[6,28,50,72,94],[6,26,50,74,98],[6,30,54,78,102],[6,28,54,80,106],[6,32,58,84,110],[6,30,58,86,114],[6,34,62,90,118],[6,26,50,74,98,122],[6,30,54,78,102,126],[6,26,52,78,104,130],[6,30,56,82,108,134],[6,34,60,86,112,138],[6,30,58,86,114,142],[6,34,62,90,118,146],[6,30,54,78,102,126,150],[6,24,50,76,102,128,154],[6,28,54,80,106,132,158],[6,32,58,84,110,136,162],[6,26,54,82,110,138,166],[6,30,58,86,114,142,170]];

function qrcode(requestedVersion=0, level='L'){
 if(level!=='L')throw Error('این مولد QR فقط تصحیح خطای L را ارائه می‌کند.');
 let message='',matrix,size,version;
 function mul(a,b){let r=0;while(b){if(b&1)r^=a;b>>>=1;a<<=1;if(a&256)a^=0x11d;}return r;}
 function rs(data,count){let generator=[1],root=1;for(let i=0;i<count;i++){const next=Array(generator.length+1).fill(0);for(let j=0;j<generator.length;j++){next[j]^=generator[j];next[j+1]^=mul(generator[j],root);}generator=next;root=mul(root,2);}const remainder=data.concat(Array(count).fill(0));for(let i=0;i<data.length;i++){const coefficient=remainder[i];if(coefficient)for(let j=0;j<generator.length;j++)remainder[i+j]^=mul(generator[j],coefficient);}return remainder.slice(-count);}
 function make(){
  const bytes=[...new TextEncoder().encode(message)];version=Number(requestedVersion)||1;
  if(!Number.isInteger(version)||version<1||version>40)throw Error('نسخهٔ QR نامعتبر است.');
  while(version<=40){const capacity=DARK_QR_BLOCKS[version-1].reduce((n,b)=>n+b[1],0)*8;if(4+(version<10?8:16)+bytes.length*8<=capacity)break;if(requestedVersion)throw Error('داده برای نسخهٔ QR انتخاب‌شده بزرگ است.');version++;}
  if(version>40)throw Error('داده بیش از ظرفیت QR است؛ خروجی متن را دریافت کن.');
  size=version*4+17;const blocks=DARK_QR_BLOCKS[version-1],dataCount=blocks.reduce((n,b)=>n+b[1],0),bits=[];
  const append=(v,n)=>{for(let i=n-1;i>=0;i--)bits.push((v>>>i)&1);};
  append(4,4);append(bytes.length,version<10?8:16);bytes.forEach(b=>append(b,8));append(0,Math.min(4,dataCount*8-bits.length));while(bits.length%8)bits.push(0);
  const data=[];for(let i=0;i<bits.length;i+=8)data.push(bits.slice(i,i+8).reduce((a,b)=>(a<<1)|b,0));for(let j=0;data.length<dataCount;j++)data.push(j%2?0x11:0xec);
  let offset=0;const ds=[],es=[];for(const [total,count]of blocks){const d=data.slice(offset,offset+count);offset+=count;ds.push(d);es.push(rs(d,total-count));}
  const words=[];for(let k=0;k<Math.max(...ds.map(d=>d.length));k++)for(const d of ds)if(k<d.length)words.push(d[k]);for(let k=0;k<Math.max(...es.map(e=>e.length));k++)for(const e of es)if(k<e.length)words.push(e[k]);
  const stream=[];for(const w of words)for(let i=7;i>=0;i--)stream.push((w>>>i)&1);
  matrix=Array.from({length:size},()=>Array(size).fill(false));const fixed=Array.from({length:size},()=>Array(size).fill(false));
  const set=(x,y,value)=>{if(x<0||y<0||x>=size||y>=size)return;matrix[y][x]=!!value;fixed[y][x]=true;};
  const finder=(cx,cy)=>{for(let dy=-4;dy<=4;dy++)for(let dx=-4;dx<=4;dx++){const d=Math.max(Math.abs(dx),Math.abs(dy));set(cx+dx,cy+dy,d!==2&&d!==4);}};
  finder(3,3);finder(size-4,3);finder(3,size-4);
  for(const y of DARK_QR_POSITIONS[version-1])for(const x of DARK_QR_POSITIONS[version-1])if(!fixed[y][x])for(let dy=-2;dy<=2;dy++)for(let dx=-2;dx<=2;dx++)set(x+dx,y+dy,Math.max(Math.abs(dx),Math.abs(dy))!==1);
  for(let i=8;i<size-8;i++){if(!fixed[6][i])set(i,6,i%2===0);if(!fixed[i][6])set(6,i,i%2===0);}
  const format=0x77c4,bit=i=>(format>>>i)&1;
  for(let i=0;i<=5;i++)set(8,i,bit(i));set(8,7,bit(6));set(8,8,bit(7));set(7,8,bit(8));for(let i=9;i<15;i++)set(14-i,8,bit(i));for(let i=0;i<8;i++)set(size-1-i,8,bit(i));for(let i=8;i<15;i++)set(8,size-15+i,bit(i));set(8,size-8,true);
  if(version>=7){let v=version<<12;const degree=x=>31-Math.clz32(x);while(degree(v)>=12)v^=0x1f25<<(degree(v)-12);const info=(version<<12)|v;for(let i=0;i<18;i++){const b=(info>>>i)&1;set(size-11+i%3,Math.floor(i/3),b);set(Math.floor(i/3),size-11+i%3,b);}}
  let pointer=0,up=true;for(let right=size-1;right>=1;right-=2){if(right===6)right=5;for(let v=0;v<size;v++){const y=up?size-1-v:v;for(let j=0;j<2;j++){const x=right-j;if(!fixed[y][x]){let dark=pointer<stream.length?stream[pointer]===1:false;pointer++;if((x+y)%2===0)dark=!dark;matrix[y][x]=dark;}}}up=!up;}
 }
 return{addData(text){message+=text;},make,getModuleCount:()=>size,getVersion:()=>version,isDark:(r,c)=>matrix[r][c],createSvgTag(){if(!matrix)make();let path='';for(let y=0;y<size;y++)for(let x=0;x<size;x++)if(matrix[y][x])path+=`M${x+4} ${y+4}h1v1h-1z`;return `<svg xmlns="http://www.w3.org/2000/svg" role="img" aria-label="QR اولین لینک پیکربندی" viewBox="0 0 ${size+8} ${size+8}" shape-rendering="crispEdges"><rect width="${size+8}" height="${size+8}" fill="white"/><path d="${path}" fill="black"/></svg>`;}};
}
if(typeof module!=='undefined')module.exports=qrcode;
