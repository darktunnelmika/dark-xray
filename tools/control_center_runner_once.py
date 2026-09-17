#!/usr/bin/env python3
from pathlib import Path

path=Path('tools/control_center_menu_once.py')
src=path.read_text(encoding='utf-8')
needle='def must(old,new,label):\n'
insert="""def replace_main(new):
    global s
    start=s.find('def main():')
    end=s.find('\\n\\nif __name__',start)
    if start<0 or end<0:raise SystemExit('main boundary mismatch')
    s=s[:start]+new.rstrip()+'\\n'+s[end:]

"""
if src.count(needle)!=1:raise SystemExit('runner insert anchor mismatch')
src=src.replace(needle,insert+needle,1)
old="replace_func('main','__main__',"
if src.count(old)!=1:raise SystemExit('runner main call anchor mismatch')
src=src.replace(old,'replace_main(',1)
exec(compile(src,str(path),'exec'),{'__name__':'__main__','__file__':str(path)})
