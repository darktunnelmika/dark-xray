/* DARK XRAY public subscription portal — no external dependencies. */
(()=>{"use strict";
const root=document.getElementById("dark-sub-app");if(!root)return;
const $=s=>document.querySelector(s),$$=s=>Array.from(document.querySelectorAll(s));
function decodeState(raw){try{const p=raw.replace(/-/g,"+").replace(/_/g,"/")+"===".slice((raw.length+3)%4);const bin=atob(p);const bytes=Uint8Array.from(bin,c=>c.charCodeAt(0));return JSON.parse(new TextDecoder().decode(bytes));}catch(_){return null}}
const state=decodeState(root.dataset.state||"");if(!state){root.innerHTML='<main style="padding:32px;font-family:monospace;color:#ff646d">INVALID SUBSCRIPTION PORTAL STATE</main>';return}

const dict={
 en:{
  controlCenter:"SECURE SUBSCRIPTION NODE",encrypted:"ENCRYPTED CHANNEL",privateAccess:"PRIVATE ACCESS // LIVE PROFILE",
  copySubscription:"COPY SUBSCRIPTION",share:"SHARE",support:"SUPPORT",scanToImport:"SCAN TO IMPORT",clientReady:"CLIENT READY",
  trafficUsed:"TRAFFIC USED",remaining:"REMAINING",expires:"EXPIRES",activeRoutes:"ACTIVE ROUTES",delivery:"DELIVERY",
  subscriptionLink:"Subscription link",selectedFormat:"SELECTED FORMAT",copy:"COPY",quickSetup:"QUICK SETUP",connectDevice:"Connect this device",
  threeSteps:"3 STEPS",deliveryNote:"Keep this link private. Anyone with it may be able to import your profile.",
  readyCommand:"PROFILE CHANNEL READY // WAITING FOR CLIENT",serviceIntel:"SERVICE INTEL",livePolicy:"Live subscription policy",
  serviceState:"SERVICE STATE",defaultFormat:"DEFAULT FORMAT",updateEvery:"UPDATE EVERY",generated:"GENERATED",securityNotice:"SECURITY NOTICE",
  securityTitle:"Private subscription credential",securityBody:"Do not publish this URL or QR code. If it is exposed, ask support to rotate the subscription.",
  active:"ACTIVE",suspended:"SUSPENDED",provisioning:"PROVISIONING",unlimited:"UNLIMITED",noExpiry:"NO EXPIRY",daysLeft:"days left",
  primary:"primary",failover:"failover",every:"Every",hours:"hours",copied:"Subscription copied",copyFailed:"Copy failed",
  shareUnavailable:"Share is not supported on this device",importDisabled:"Import is disabled while the service is not active",
  qrFailed:"QR generation failed",serviceBlocked:"Service access is currently restricted",reasonExpired:"Subscription has expired",
  reasonQuota:"Traffic quota is exhausted",reasonManual:"Subscription is disabled",reasonDevice:"Device limit is reached",
  reasonIp:"IP limit is reached",reasonOwner:"Account access is temporarily disabled",reasonRuntime:"Runtime access is disabled",
  reasonOther:"Service policy is blocking access",
  android1t:"Install Hiddify or v2rayNG",android1d:"Use a supported Android client.",
  android2t:"Tap Quick Import",android2d:"Use the buttons on this page, or scan the QR code from the app.",
  android3t:"Update the profile",android3d:"Keep automatic subscription updates enabled.",
  ios1t:"Install a compatible client",ios1d:"Use a client that can import VLESS / VMess / Trojan subscriptions.",
  ios2t:"Copy or scan",ios2d:"Copy the subscription link or scan its QR code inside the client.",
  ios3t:"Enable updates",ios3d:"Let the app refresh the profile on the shown interval.",
  desktop1t:"Open your desktop client",desktop1d:"Use a compatible Xray or Clash/Mihomo client.",
  desktop2t:"Add subscription URL",desktop2d:"Paste the selected link into the client's subscription manager.",
  desktop3t:"Refresh profile",desktop3d:"Fetch the subscription and enable periodic updates."
 },
 fa:{
  controlCenter:"گره امن اشتراک",encrypted:"کانال رمزگذاری‌شده",privateAccess:"دسترسی خصوصی // پروفایل زنده",
  copySubscription:"کپی لینک اشتراک",share:"اشتراک‌گذاری",support:"پشتیبانی",scanToImport:"اسکن برای افزودن",clientReady:"آماده اتصال",
  trafficUsed:"مصرف شده",remaining:"باقی‌مانده",expires:"انقضا",activeRoutes:"مسیرهای فعال",delivery:"تحویل",
  subscriptionLink:"لینک اشتراک",selectedFormat:"فرمت انتخابی",copy:"کپی",quickSetup:"راه‌اندازی سریع",connectDevice:"اتصال این دستگاه",
  threeSteps:"۳ مرحله",deliveryNote:"این لینک خصوصی است. هرکس آن را داشته باشد ممکن است بتواند پروفایل را وارد کند.",
  readyCommand:"کانال پروفایل آماده // منتظر کلاینت",serviceIntel:"اطلاعات سرویس",livePolicy:"سیاست زنده اشتراک",
  serviceState:"وضعیت سرویس",defaultFormat:"فرمت پیش‌فرض",updateEvery:"بروزرسانی",generated:"ساخته‌شده",securityNotice:"هشدار امنیتی",
  securityTitle:"اعتبار خصوصی اشتراک",securityBody:"این URL یا QR را عمومی نکنید. در صورت افشا از پشتیبانی بخواهید لینک اشتراک را تعویض کند.",
  active:"فعال",suspended:"متوقف",provisioning:"در حال آماده‌سازی",unlimited:"نامحدود",noExpiry:"بدون انقضا",daysLeft:"روز باقی‌مانده",
  primary:"اصلی",failover:"پشتیبان",every:"هر",hours:"ساعت",copied:"لینک اشتراک کپی شد",copyFailed:"کپی انجام نشد",
  shareUnavailable:"اشتراک‌گذاری روی این دستگاه پشتیبانی نمی‌شود",importDisabled:"تا زمانی که سرویس فعال نیست افزودن مستقیم غیرفعال است",
  qrFailed:"ساخت QR ناموفق بود",serviceBlocked:"دسترسی سرویس فعلاً محدود شده",reasonExpired:"زمان سرویس به پایان رسیده",
  reasonQuota:"حجم سرویس تمام شده",reasonManual:"سرویس غیرفعال شده",reasonDevice:"محدودیت دستگاه تکمیل شده",
  reasonIp:"محدودیت IP تکمیل شده",reasonOwner:"دسترسی حساب موقتاً غیرفعال است",reasonRuntime:"دسترسی Runtime غیرفعال است",
  reasonOther:"سیاست سرویس مانع اتصال شده است",
  android1t:"Hiddify یا v2rayNG را نصب کنید",android1d:"از یک کلاینت سازگار اندروید استفاده کنید.",
  android2t:"Quick Import را بزنید",android2d:"از دکمه‌های همین صفحه استفاده کنید یا QR را داخل برنامه اسکن کنید.",
  android3t:"پروفایل را بروزرسانی کنید",android3d:"بروزرسانی خودکار اشتراک را روشن نگه دارید.",
  ios1t:"یک کلاینت سازگار نصب کنید",ios1d:"کلاینتی انتخاب کنید که اشتراک‌های VLESS / VMess / Trojan را وارد کند.",
  ios2t:"لینک را کپی یا QR را اسکن کنید",ios2d:"لینک اشتراک را کپی کنید یا QR را داخل کلاینت اسکن کنید.",
  ios3t:"بروزرسانی را فعال کنید",ios3d:"اجازه دهید برنامه طبق بازه نمایش‌داده‌شده پروفایل را بروزرسانی کند.",
  desktop1t:"کلاینت دسکتاپ را باز کنید",desktop1d:"از کلاینت سازگار Xray یا Clash/Mihomo استفاده کنید.",
  desktop2t:"لینک اشتراک را اضافه کنید",desktop2d:"لینک انتخاب‌شده را در Subscription Manager برنامه وارد کنید.",
  desktop3t:"پروفایل را Refresh کنید",desktop3d:"اشتراک را دریافت و بروزرسانی دوره‌ای را فعال کنید."
 }
};
let lang=localStorage.getItem("dark-sub-lang")||((navigator.language||"").toLowerCase().startsWith("fa")?"fa":"en");
let format=state.default_format&&state.formats?.[state.default_format]?state.default_format:"base64";
let platform="android";
const t=k=>(dict[lang]&&dict[lang][k])||dict.en[k]||k;
function escText(el,v){if(el)el.textContent=v==null?"":String(v)}
function formatBytes(n){n=Number(n)||0;const units=["B","KB","MB","GB","TB","PB"];let i=0;while(n>=1024&&i<units.length-1){n/=1024;i++}return (i? n.toFixed(n>=100?0:n>=10?1:2):String(Math.round(n)))+" "+units[i]}
function shortDate(ts){if(!ts)return t("noExpiry");try{return new Intl.DateTimeFormat(lang==="fa"?"fa-IR":"en-GB",{year:"numeric",month:"short",day:"2-digit"}).format(new Date(ts*1000))}catch(_){return new Date(ts*1000).toLocaleDateString()}}
function statusLabel(){return t(state.status||"provisioning")}
function reasonLabel(code){
 if(code==="expired")return t("reasonExpired");if(code==="client_quota")return t("reasonQuota");
 if(code==="client_manual")return t("reasonManual");if(code==="global_device_quota")return t("reasonDevice");
 if(code==="global_ip_quota")return t("reasonIp");if(code==="owner_manual"||code==="owner_account_disabled"||code==="missing_owner")return t("reasonOwner");
 if(code==="runtime_disabled"||code==="engine_manual_or_external_disable")return t("reasonRuntime");return t("reasonOther");
}
function selectedUrl(){return state.formats?.[format]||state.subscription_url||""}
let toastTimer=0;function toast(msg,error=false){const el=$("#toast");if(!el)return;clearTimeout(toastTimer);el.textContent=msg;el.classList.toggle("error",!!error);el.classList.add("show");toastTimer=setTimeout(()=>el.classList.remove("show"),1800)}
async function copy(text){try{await navigator.clipboard.writeText(text);toast(t("copied"));return true}catch(_){try{const a=document.createElement("textarea");a.value=text;a.style.position="fixed";a.style.opacity="0";document.body.appendChild(a);a.select();const ok=document.execCommand("copy");a.remove();if(!ok)throw 0;toast(t("copied"));return true}catch(__){toast(t("copyFailed"),true);return false}}}
function renderQr(){const box=$("#qr-code");if(!box)return;box.replaceChildren();try{if(typeof qrcode!=="function")throw new Error("qrcode unavailable");const q=qrcode(0,"L");q.addData(selectedUrl());q.make();box.innerHTML=q.createSvgTag(5,0);escText($("#qr-format"),format.toUpperCase())}catch(_){box.textContent=t("qrFailed");box.classList.add("error")}}
function renderGuide(){
 const steps={
  android:[["android1t","android1d"],["android2t","android2d"],["android3t","android3d"]],
  ios:[["ios1t","ios1d"],["ios2t","ios2d"],["ios3t","ios3d"]],
  desktop:[["desktop1t","desktop1d"],["desktop2t","desktop2d"],["desktop3t","desktop3d"]]
 }[platform]||[];
 const ol=$("#guide-steps");if(ol){ol.replaceChildren(...steps.map(([a,b])=>{const li=document.createElement("li"),strong=document.createElement("b"),span=document.createElement("span");strong.textContent=t(a);span.textContent=t(b);li.append(strong,span);return li}))}
 $$(".dx-platforms button").forEach(b=>b.classList.toggle("active",b.dataset.platform===platform));
}
function renderLanguage(){
 document.documentElement.lang=lang;document.documentElement.dir=lang==="fa"?"rtl":"ltr";
 $$("[data-i18n]").forEach(el=>{const key=el.dataset.i18n;if(dict[lang][key])el.textContent=t(key)});
 const langBtn=$('[data-action="lang"]');if(langBtn)langBtn.textContent=lang==="fa"?"EN":"FA";
 renderGuide();renderDynamic();
}
function renderDynamic(){
 root.dataset.status=state.status||"provisioning";
 const status=$("#service-status");if(status){status.className="dx-status "+(state.status||"provisioning");escText(status.querySelector("span"),statusLabel())}
 escText($("#service-id"),"ID: "+String(state.service_id||"----").toUpperCase());
 escText($("#profile-title"),state.title||"DARK XRAY");
 const announce=$("#announce");if(announce){announce.hidden=!state.announce;announce.textContent=state.announce||""}
 const support=$("#support-link");if(support&&state.support_url){support.hidden=false;support.href=state.support_url}else if(support)support.hidden=true;
 const used=Number(state.traffic?.used||0),total=Number(state.traffic?.total||0),remaining=Math.max(0,total-used);
 escText($("#traffic-used"),formatBytes(used));escText($("#traffic-total"),total?("/ "+formatBytes(total)):t("unlimited"));
 escText($("#traffic-remaining"),total?formatBytes(remaining):"∞");escText($("#traffic-mode"),total?(Math.min(100,Math.max(0,used/total*100)).toFixed(0)+"%"):t("unlimited"));
 const meter=$("#traffic-meter");if(meter)meter.style.width=(total?Math.min(100,Math.max(0,used/total*100)):0)+"%";
 const expiry=Number(state.expiry||0),days=expiry?Math.ceil((expiry-Date.now()/1000)/86400):null;
 escText($("#expiry-main"),expiry?shortDate(expiry):t("noExpiry"));escText($("#expiry-sub"),expiry?(Math.max(0,days)+" "+t("daysLeft")):t("unlimited"));
 const p=Number(state.routes?.primary||0),f=Number(state.routes?.failover||0);escText($("#route-count"),String(p+f));escText($("#route-detail"),p+" "+t("primary")+" · "+f+" "+t("failover"));
 escText($("#subscription-url"),selectedUrl());escText($("#update-interval"),t("every")+" "+state.update_interval_hours+" "+t("hours"));
 escText($("#intel-state"),statusLabel());escText($("#intel-format"),String(state.default_format||"base64").toUpperCase());
 escText($("#intel-update"),state.update_interval_hours+" "+t("hours"));escText($("#intel-generated"),new Date(Number(state.generated_at||0)*1000).toLocaleTimeString(lang==="fa"?"fa-IR":"en-GB"));
 $$("#format-tabs button").forEach(b=>b.classList.toggle("active",b.dataset.format===format));
 const reasons=Array.isArray(state.reasons)?state.reasons:[];
 const reason=$("#reason-box");if(reason){reason.hidden=!reasons.length;reason.textContent=reasons.length?(t("serviceBlocked")+" // "+[...new Set(reasons.map(reasonLabel))].join(" · ")):""}
 const disabled=state.status!=="active";for(const id of ["#import-hiddify","#import-v2rayng"]){const el=$(id);if(el)el.disabled=disabled}
 renderQr();
}
function importAllowed(){if(state.status==="active")return true;toast(t("importDisabled"),true);return false}
function launchHiddify(){if(!importAllowed())return;const url=state.formats?.base64||selectedUrl(),name=encodeURIComponent(state.title||"DARK XRAY");location.href="hiddify://import/"+url+"#"+name}
function launchV2rayNG(){if(!importAllowed())return;const url=encodeURIComponent(state.formats?.base64||selectedUrl()),name=encodeURIComponent(state.title||"DARK XRAY");location.href="v2rayng://install-sub?url="+url+"&name="+name}
document.addEventListener("click",async ev=>{
 const el=ev.target.closest("[data-action],[data-format],[data-platform]");if(!el)return;
 if(el.dataset.format){format=el.dataset.format;renderDynamic();return}
 if(el.dataset.platform){platform=el.dataset.platform;renderGuide();return}
 const act=el.dataset.action;
 if(act==="copy")return void copy(selectedUrl());
 if(act==="lang"){lang=lang==="fa"?"en":"fa";localStorage.setItem("dark-sub-lang",lang);renderLanguage();return}
 if(act==="share"){if(navigator.share){try{await navigator.share({title:state.title||"DARK XRAY",url:selectedUrl()})}catch(_){}}else toast(t("shareUnavailable"),true);return}
 if(act==="hiddify")return launchHiddify();
 if(act==="v2rayng")return launchV2rayNG();
});
setInterval(()=>{const el=$("#footer-clock");if(el)el.textContent=new Date().toLocaleTimeString("en-GB",{hour12:false})},1000);
renderLanguage();root.setAttribute("aria-busy","false");
})();