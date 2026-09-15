/* DARK XRAY English-first presentation layer.
 * The backend remains language-neutral. Persian can be restored with the EN/FA switch.
 */
(function(){
'use strict';
const KEY='dark_lang';
const selected=localStorage.getItem(KEY)||'en';
const faChars=/[\u0600-\u06FF]/;
const persianDigits='۰۱۲۳۴۵۶۷۸۹';
const latinDigits='0123456789';

const exact=new Map(Object.entries({
 'در حال بررسی نشست امن…':'Checking secure session…',
 'ذخیره‌شده در DARK':'Saved in DARK',
 'در صف':'Queued',
 'خطای اجرا':'Execution error',
 'تعارض':'Conflict',
 'نتیجه نامشخص':'Uncertain result',
 'حذف‌شده':'Deleted',
 'در حال ریست':'Reset in progress',
 'در دیتابیس اجرا یافت نشد':'Missing from runtime database',
 'قطع دستی مشتری':'Client manually disabled',
 'قطع دستی نماینده':'Reseller manually disabled',
 'اتمام سهمیه نماینده':'Reseller quota exhausted',
 'اتمام حجم مشتری':'Client quota exhausted',
 'منقضی':'Expired',
 'حساب نماینده غیرفعال':'Reseller account disabled',
 'قطع دستی در موتور محلی':'Disabled in local engine',
 'مرکز کنترل':'CONTROL CENTER',
 'نمای کلی':'Overview',
 'اینباندها':'Inbounds',
 'کاربران':'Clients',
 'نمایندگان':'Resellers',
 'سطوح دسترسی':'Access Control',
 'محدودیت IP و دستگاه':'IP & Device Guard',
 'دفتر حساب':'Finance Ledger',
 'گزارش عملیات':'Audit Log',
 'همگام‌سازی':'Synchronization',
 'هاست‌ها':'Hosts',
 'اوتباندها':'Outbounds',
 'روتینگ':'Routing',
 'نودها':'Nodes',
 'تنظیمات Xray':'Xray Settings',
 'تنظیمات پنل':'Panel Settings',
 'حساب و امنیت':'Account & Security',
 'پنل مستقل · مدیریت مستقیم Xray-core':'Standalone panel · Direct Xray-core control',
 'مالک اصلی':'Owner',
 'دسترسی محدود':'Limited Access',
 'منو':'Menu',
 'بروزرسانی':'Refresh',
 'خروج':'Logout',
 'ذخیره':'Save',
 'بستن':'Close',
 'فعال':'Active',
 'غیرفعال':'Disabled',
 'محدود شده':'Blocked',
 'نسخهٔ هسته همسان است':'Core is synchronized',
 'ذخیره‌شده؛ هنوز روی هسته اجرا نشده':'Saved; not applied to core yet',
 'کاربر / مالک':'Client / Owner',
 'وضعیت همگام‌سازی':'Sync Status',
 'مصرف / سهمیه':'Usage / Quota',
 'انقضا':'Expiry',
 'عملیات':'Actions',
 'نامحدود':'Unlimited',
 'شروع از اتصال اول':'Start on first connection',
 'بدون انقضا':'No expiry',
 'پیش‌فرض':'Default',
 'ویرایش':'Edit',
 'لینک':'Links',
 'IP / دستگاه':'IP / Device',
 'قطع':'Disable',
 'ریست':'Reset',
 'حذف':'Delete',
 'عملیات انتخاب‌شده':'Bulk actions',
 'حافظه RAM':'RAM',
 'دیسک':'Disk',
 'کاربران مدیریت‌شده':'Managed Clients',
 'ارسال / ثانیه':'Upload / sec',
 'دریافت / ثانیه':'Download / sec',
 'وضعیت ارتباط':'Connection Status',
 'هسته:':'Core:',
 'آپ‌تایم:':'Uptime:',
 'نسخهٔ Xray محلی:':'Local Xray version:',
 'آخرین دریافت:':'Last poll:',
 'آخرین کاربران':'Recent Clients',
 'همه کاربران':'All Clients',
 'اینباند جدید':'New Inbound',
 'کاربر جدید':'New Client',
 'افزودن کاربر':'Add Client',
 'تخصیص مشتری قدیمی':'Adopt Existing Client',
 'افزودن نماینده':'Add Reseller',
 'مدیریت':'Manage',
 'شارژ اعتبار':'Add Credit',
 'دورهٔ جدید ترافیک':'New Traffic Period',
 'مصرف دورهٔ نماینده':'Period Usage',
 'سهمیه':'Quota',
 'سقف تعداد مشتری':'Client Limit',
 'حداکثر IP مجاز هر مشتری':'Max IPs per Client',
 'اعتبار مالی':'Credit',
 'اینباندهای مجاز':'Allowed Inbounds',
 'هیچ':'None',
 'حساب':'Account',
 'نقش':'Role',
 'وضعیت':'Status',
 'مجوزها':'Permissions',
 'حساب جدید':'New Account',
 'ویرایش مجوز':'Edit Permissions',
 'وضعیت سرویس محدودکننده':'Guard Service Status',
 'IP با دستگاه یکی نیست':'IP is not a Device',
 'دریافت اطلاعات ناموفق':'Failed to load data',
 'عدد نامعتبر:':'Invalid number:',
 'نام / شناسهٔ مشتری':'Client Name / ID',
 'مالک مشتری':'Client Owner',
 'حجم (GiB) · صفر نامحدود':'Traffic (GiB) · 0 = unlimited',
 'انقضا (زمان واردشده UTC)':'Expiry (UTC)',
 'حداکثر IP · صفر نامحدود':'Max IPs · 0 = unlimited',
 'حداکثر HWID · صفر بدون محدودیت':'Max HWIDs · 0 = unlimited',
 'وضعیت دستی':'Manual State',
 'قطع دستی':'Manually Disabled',
 'بدون Flow':'No Flow',
 'شناسه تلگرام':'Telegram ID',
 'ریست دوره‌ای حجم (روز) · صفر خاموش':'Periodic traffic reset (days) · 0 = off',
 'Traffic reset schedule':'Traffic reset schedule',
 'Custom reset interval (days)':'Custom reset interval (days)',
 'Monthly reset day (1-31)':'Monthly reset day (1-31)',
 'Maximum resets · 0 unlimited':'Maximum resets · 0 unlimited',
 'گروه':'Group',
 'یادداشت':'Comment',
 'اینباندهای مشتری':'Client Inbounds',
 'افزودن مشتری':'Add Client',
 'پروفایل نماینده جدید':'New Reseller Profile',
 'شناسه (با حساب ورود یکسان)':'ID (same as login account)',
 'نام نمایشی':'Display Name',
 'سهمیه ترافیک (GiB) · صفر نامحدود':'Traffic quota (GiB) · 0 = unlimited',
 'سقف تعداد کاربران · صفر نامحدود':'Client limit · 0 = unlimited',
 'بیشترین IP مجاز برای هر مشتری · صفر آزاد':'Max IPs per client · 0 = unrestricted',
 'وضعیت نماینده':'Reseller State',
 'قطع دستی مشتریان نماینده':'Manually disable reseller clients',
 'نام کاربری':'Username',
 'رمز جدید (خالی = بدون تغییر)':'New password (blank = unchanged)',
 'رمز (حداقل ۱۲ کاراکتر)':'Password (minimum 8 characters)',
 'نقش پایه':'Base Role',
 'نماینده':'Reseller',
 'مشاهده‌گر':'Read-only',
 'وضعیت حساب':'Account Status',
 'بدون دسترسی':'None',
 'فقط خود':'Own only',
 'همه':'All',
 'IPها':'IPs',
 'پاک‌کردن تاریخچه (نه رفع بن)':'Clear history (not unban)',
 'پاک‌کردن دستگاه‌ها':'Clear devices',
 'اشتراک و لینک:':'Subscription & Links:',
 'اشتراک کنترل‌شده توسط DARK':'DARK-managed subscription',
 'خروجی ساخته‌شده توسط DARK':'Generated by DARK',
 'تخصیص صریح مشتری موجود':'Adopt Existing Client',
 'مشتری فعلی':'Existing Client',
 'مالک':'Owner',
 'ورود به مرکز کنترل DARK':'Sign in to DARK Control Center',
 'نام کاربری':'Username',
 'رمز عبور':'Password',
 'کد TOTP یا بازیابی (در صورت فعال‌بودن)':'TOTP / recovery code (if enabled)',
 'ورود امن':'Secure Sign In',
 'غیرفعال‌سازی TOTP':'Disable TOTP',
 'رمز فعلی':'Current Password',
 'کد TOTP یا بازیابی':'TOTP or Recovery Code',
 'کلید API مستقل':'API Key',
 'نام کلید':'Key Name',
 'اعتبار (روز)':'Validity (days)',
 'کلید فقط همین بار نمایش داده می‌شود':'This key is shown only once',
 'عمومی':'General',
 'انتقال':'Transport',
 'پیشرفته':'Advanced',
 'نام اینباند':'Inbound Name',
 'پروتکل':'Protocol',
 'پورت':'Port',
 'Tag (خالی = ساخت خودکار)':'Tag (blank = automatic)',
 'امنیت':'Security',
 'ساخت کلید X25519':'Generate X25519 Key',
 'JSON معتبر نیست.':'Invalid JSON.',
 'تنظیمات مستقل':'Standalone Settings',
 'ذخیره شد؛ وضعیت اعمال هسته را بررسی کن.':'Saved. Check core apply state.',
 'اینباند در DARK ذخیره شد. وضعیت هسته را بررسی کن.':'Inbound saved in DARK. Check core state.',
 'تأیید نشده':'Not verified',
 'رمز حساب':'Account Password',
 'نشست‌های فعال':'Active Sessions',
 'خروج از همه نشست‌های دیگر':'Sign out all other sessions',
 'این نشست':'Current session',
 'نشست دیگر':'Other session',
 'زمان ساخت نامشخص':'Creation time unknown',
 'User-Agent نامشخص':'Unknown User-Agent',
 'ابطال':'Revoke',
 'نشست باطل شد.':'Session revoked.'
}));

const phrases=[
 ['رمز، نشست، ورود دومرحله‌ای و کلید مستقل برای اتصال نرم‌افزارها.','Password, sessions, two-factor authentication, and independent API keys.'],
 ['با تغییر رمز، تمام نشست‌های این حساب باطل می‌شوند.','Changing the password revokes all sessions for this account.'],
 ['نشست فعال برای این حساب ثبت شده است.','active sessions are registered for this account.'],
 ['این نشست فعال باطل شود؟','Revoke this active session?'],
 ['از تمام نشست‌های دیگر این حساب خارج شود؟','Sign out all other sessions for this account?'],
 ['نشست دیگر باطل شد.','other sessions were revoked.'],
 ['ارتباط با سرویس DARK برقرار نیست.','Cannot connect to the DARK service.'],
 ['پاسخ نامعتبر سرور','Invalid server response'],
 ['اطلاعات این بخش دریافت نشده؛ با دادهٔ نمونه جایگزین نشده است.','This section could not be loaded; no sample data was substituted.'],
 ['کاربر تحت مدیریت DARK در این فهرست نیست. مشتری‌های قدیمی باید صریحاً به مالک تخصیص داده شوند.','No DARK-managed clients are in this list. Existing clients must be explicitly assigned to an owner.'],
 ['جست‌وجوی نام کاربر یا مالک','Search client or owner'],
 ['مشتری ثبت‌شده','registered clients'],
 ['منابع هسته و وضعیت مدیریت کاربران؛ داده‌ها از سرویس دریافت می‌شوند.','Core resources and client-management state from the live service.'],
 ['نمونه‌های زندهٔ CPU در این نشست','Live CPU samples in this session'],
 ['مشاهده','samples'],
 ['برای نمودار حداقل دو پاسخ واقعی لازم است.','At least two real samples are required for the chart.'],
 ['فقط پاسخ‌های دریافت‌شده؛ بدون نقاط تصادفی یا تاریخچهٔ ساختگی.','Received samples only; no random or fabricated history.'],
 ['پذیرش درخواست توسط API به معنی تأیید انتقال بسته‌های شبکه نیست.','API acceptance does not prove packet forwarding.'],
 ['شنونده‌های مستقل Xray؛ کاربران زیر همان اینباند مدیریت می‌شوند.','Independent Xray listeners; clients are managed under their inbounds.'],
 ['تنظیمات ذخیره‌شده هنوز با نسخهٔ اجرا همسان نیست','Saved configuration differs from runtime'],
 ['نسخهٔ اجرا همسان است','Runtime is synchronized'],
 ['در حال اجرا','Running'],
 ['متوقف','Stopped'],
 ['فعال در تنظیمات','Enabled in config'],
 ['هنوز اینباندی نساخته‌ای. از «اینباند جدید» شروع کن.','No inbounds yet. Start with “New Inbound”.'],
 ['حجم، زمان، IP و اشتراک؛ ذخیره و اجرا از طریق API.','Traffic, expiry, IP and subscription management through the API.'],
 ['سهمیهٔ ترافیک مستقل از کیف پول؛ حذف مشتری مصرف تاریخی را پاک نمی‌کند.','Traffic quota is independent of wallet balance; deleting a client does not erase historical usage.'],
 ['ابتدا پروفایل و اینباندهای مجاز را تعریف کن؛ حساب ورود با همان شناسه از «سطوح دسترسی» ساخته می‌شود.','Define the profile and allowed inbounds first; create the login with the same ID under Access Control.'],
 ['دسترسی سمت سرور؛ تغییر نقش نشست‌های قبلی را باطل می‌کند.','Server-side access control; role changes revoke previous sessions.'],
 ['سیاست مستقل DARK؛ دریافت IP از لاگ محلی و اعمال شبکه با کارگر Fail2ban جداگانه و تأییدشده.','Independent DARK policy: local IP observations with a separate reviewed enforcement worker.'],
 ['وضعیت سراسری فایروال فقط برای مالک اصلی قابل مشاهده است.','Global firewall state is visible to the owner only.'],
 ['اتصال تانلی باید IP واقعی مشتری را در نقطهٔ اعمال محدودیت حفظ کند. این نسخه سقف مشترک بین چند نود مستقل ایجاد نمی‌کند.','Tunnels must preserve the real client source IP at the enforcement point. This release does not provide a global multi-node IP limit.'],
 ['چند اتصال از یک IP عمومی یک سهم می‌گیرند.','Multiple connections behind one public IP share the same IP identity.'],
 ['این صفحه بدون پاسخ معتبر، دادهٔ ساختگی نشان نمی‌دهد.','This page never substitutes fabricated data when the live API fails.'],
 ['شناسه و رمز خالی در ساخت جدید توسط سرور تولید می‌شوند. هنگام ویرایش فقط فیلدهایی که در اختیار تو هستند تغییر می‌کنند؛ اطلاعات پنهان پاک نمی‌شود.','Blank credentials are generated by the server for new clients. Editing changes only the fields you control; hidden credentials are preserved.'],
 ['در دیتابیس DARK ذخیره شد؛ وضعیت اجرای هسته در جدول جداست.','Saved in the DARK database; core runtime state is tracked separately.'],
 ['درخواست ثبت شد:','Request recorded:'],
 ['پروفایل نماینده در دیتابیس ذخیره شد.','Reseller profile saved.'],
 ['حساب نماینده را با همان شناسهٔ پروفایل بساز. مجوز «همه» عمداً دسترسی بین نماینده‌ها می‌دهد؛ پیش‌فرضِ امن «خود» است.','Create reseller login with the same profile ID. “All” intentionally crosses reseller boundaries; the safe default is “Own”.'],
 ['حساب و مجوزها ذخیره شدند.','Account and permissions saved.'],
 ['این پاسخ از دیتابیس و لاگ محلی DARK دریافت شده است.','This response comes from the local DARK database and logs.'],
 ['این لینک‌ها حاوی اعتبارنامه مشتری هستند. این لینک‌ها همین‌جا ساخته شده‌اند. وضعیت اجرای هسته را جداگانه بررسی کن.','These links contain client credentials and are generated locally. Verify core runtime state separately.'],
 ['مشتری حذف یا دوباره ساخته نمی‌شود. مصرف فعلی به‌عنوان مبنا ثبت می‌شود؛ برای نماینده فقط مصرف مشاهده‌شده از این لحظه حساب می‌شود.','The client is not deleted or recreated. Current usage becomes the baseline; reseller accounting starts from this point.'],
 ['مالکیت ثبت شد.','Ownership recorded.'],
 ['حذف مشتری (دفتر مصرف حفظ می‌شود)','Delete client (usage ledger is preserved)'],
 ['ریست مصرف مشتری (مصرف نماینده کم نمی‌شود)','Reset client usage (reseller history is preserved)'],
 ['فعال‌کردن مشتری با حفظ سایر محدودیت‌ها','Enable client while preserving other limits'],
 ['قطع دستی مشتری','Manually disable client'],
 ['تأیید می‌کنی؟','Confirm?'],
 ['اینباند حذف شود؟ در صورت داشتن مشتری یا هاست، درخواست رد می‌شود.','Delete this inbound? The request is rejected if clients or hosts still depend on it.'],
 ['این عملیات روی هستهٔ مستقل اجرا می‌شود و ممکن است اتصال‌ها قطع شوند. ادامه؟','This operation affects the standalone core and may interrupt connections. Continue?'],
 ['نتیجه از سرویس محلی دریافت شد.','Result received from the local service.'],
 ['وضعیت فعلی Xray محلی پذیرفته شود؟ ریست دوباره اجرا نمی‌شود و مصرف تاریخی برنمی‌گردد.','Accept the current local Xray state? The reset will not be repeated and historical usage is not restored.'],
 ['این کلید باطل شود؟','Revoke this key?'],
 ['این صفحه به سرویس و دیتابیس متصل است. حساب پیش‌فرض یا ورود نمایشی ندارد.','This console is connected to the live service and database. There is no demo or default account.'],
 ['یک پنل، یک ورود و یک دیتابیس؛ هیچ پنل دیگری نصب نمی‌شود. هستهٔ محلی Xray را از همین پروژه مدیریت می‌کنی.','One panel, one login and one database. No other panel is installed; this project controls the local Xray core directly.'],
 ['هر هاست: inboundId، address، port و در صورت نیاز remark، sni، host، path. پورت بیرونی تونل را اینجا قرار بده.','Each host uses inboundId, address and port, with optional remark, sni, host and path. Put the tunnel-facing port here.'],
 ['هر خروجی باید tag یکتا و protocol داشته باشد. قواعد وابسته مانع حذف خروجی می‌شوند.','Every outbound needs a unique tag and protocol. Dependent rules prevent deletion.'],
 ['قواعد باید یکی از outboundTag یا balancerTag داشته باشند. ترتیب آرایهٔ rules حفظ می‌شود.','Rules require outboundTag or balancerTag. Rule order is preserved.'],
 ['پیکربندی این بخش مستقیماً در دیتابیس DARK ذخیره می‌شود.','This configuration is stored directly in the DARK database.'],
 ['فرم سادهٔ بومی برای همهٔ جزئیات این بخش هنوز تکمیل نشده؛ فعلاً ویرایشگر JSON خود DARK است.','Some advanced fields still use DARK’s built-in JSON editor.'],
 ['این فرم متعلق به DARK است. کاربران و مالکیتشان از بخش کاربران مدیریت می‌شوند؛ قرار دادن clients در JSON اینباند مجاز نیست.','This is a DARK-native form. Manage clients and ownership in Clients; embedding clients in inbound JSON is not allowed.'],
 ['Protocol settings — بدون clients','Protocol settings — no clients'],
 ['Stream settings تکمیلی — فیلدهای فرم اولویت دارند','Additional stream settings — form fields take precedence'],
 ['کلید، مقصد و SNI در REALITY لازم‌اند.','REALITY requires a private key, target and SNI.'],
 ['مسیر فایل گواهی و کلید TLS لازم است.','TLS certificate and key file paths are required.']
];
phrases.sort((a,b)=>b[0].length-a[0].length);

const words=[
 ['ذخیره','Save'],['فعال','Active'],['غیرفعال','Disabled'],['کاربر','Client'],['مشتری','Client'],['مالک','Owner'],['نماینده','Reseller'],['اینباند','Inbound'],['حساب','Account'],['مجوز','Permission'],['رمز','Password'],['دستگاه','Device'],['ترافیک','Traffic'],['اعتبار','Credit'],['تنظیمات','Settings'],['هسته','Core'],['ورود','Login'],['امنیت','Security'],['عمومی','General'],['انتقال','Transport'],['پیشرفته','Advanced'],['پورت','Port'],['نام','Name'],['وضعیت','Status'],['مدیریت','Manage'],['حذف','Delete'],['ویرایش','Edit'],['ریست','Reset'],['جدید','New']
];

function digits(s){return s.replace(/[۰-۹]/g,d=>latinDigits[persianDigits.indexOf(d)]);}
function translateRaw(raw){
  if(!raw)return raw;
  let out=digits(raw);
  const lead=out.match(/^\s*/)?.[0]||'',tail=out.match(/\s*$/)?.[0]||'';
  let core=out.slice(lead.length,out.length-tail.length);
  if(!faChars.test(core))return out;
  if(exact.has(core))return lead+exact.get(core)+tail;
  for(const [a,b] of phrases)core=core.split(a).join(b);
  if(faChars.test(core))for(const [a,b] of words)core=core.split(a).join(b);
  return lead+core+tail;
}

function skip(el){
  return !el||el.closest('script,style,pre,code,.json-box,.json-preview,.terminal,.client-name,.owner-label,[data-no-i18n]');
}
function process(root){
  if(selected!=='en'||!root)return;
  const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
  const list=[];while(walker.nextNode())list.push(walker.currentNode);
  for(const node of list){if(skip(node.parentElement))continue;const next=translateRaw(node.nodeValue);if(next!==node.nodeValue)node.nodeValue=next;}
  const els=root.querySelectorAll?root.querySelectorAll('[placeholder],[title],[aria-label],input[minlength="12"]'):[];
  for(const el of els){
    for(const attr of ['placeholder','title','aria-label'])if(el.hasAttribute(attr))el.setAttribute(attr,translateRaw(el.getAttribute(attr)));
    if(el.matches('input[name="password"][minlength="12"],input[type="password"][minlength="12"]'))el.setAttribute('minlength','8');
  }
}
function languageButton(){
  let b=document.querySelector('.cyber-lang-switch');if(b)return;
  b=document.createElement('button');b.type='button';b.className='cyber-lang-switch';
  b.textContent=selected==='en'?'EN · فارسی':'FA · English';
  b.title=selected==='en'?'Switch to Persian':'Switch to English';
  b.addEventListener('click',()=>{localStorage.setItem(KEY,selected==='en'?'fa':'en');location.reload();});
  document.body.appendChild(b);
}
function apply(){
  document.documentElement.lang=selected==='en'?'en':'fa';
  document.documentElement.dir=selected==='en'?'ltr':'rtl';
  if(selected==='en')process(document.body);
  languageButton();
}
const observer=new MutationObserver(records=>{if(selected!=='en')return;for(const r of records){for(const n of r.addedNodes)if(n.nodeType===1)process(n);else if(n.nodeType===3&&!skip(n.parentElement))n.nodeValue=translateRaw(n.nodeValue);}});
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>{apply();observer.observe(document.body,{subtree:true,childList:true});});
else{apply();observer.observe(document.body,{subtree:true,childList:true});}
})();
