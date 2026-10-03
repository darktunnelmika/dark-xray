from __future__ import annotations
from datetime import datetime
from zoneinfo import ZoneInfo

def money(value:int,currency:str='IRT')->str:
    code=str(currency or 'IRT').upper()
    unit={'IRT':'تومان','IRR':'ریال'}.get(code,code)
    return f'{int(value):,} {unit}'

def timezone(name:str='UTC')->ZoneInfo:
    try:return ZoneInfo(str(name or 'UTC'))
    except (ValueError,KeyError):return ZoneInfo('UTC')

def date_time(timestamp:float,name:str='UTC')->str:
    tz=timezone(name)
    return datetime.fromtimestamp(float(timestamp),tz).strftime('%Y/%m/%d · %H:%M')+' ('+str(tz)+')'

ORDER_STATES={
    'pending':'منتظر پرداخت','awaiting_payment':'منتظر واریز',
    'payment_review':'رسید در حال بررسی','payment_rejected':'پرداخت ردشده',
    'paid':'پرداخت‌شده؛ در انتظار تحویل','provisioned':'تحویل‌شده و فعال',
    'provisioned_waiting_activation':'تحویل‌شده؛ منتظر اولین اتصال',
    'renewed':'تمدیدشده','failed_refunded':'ناموفق؛ وجه برگشت داده شد','cancelled':'لغوشده',
}
