#!/usr/bin/env python3
"""Small standalone terminal menu; never prints saved passwords or tokens."""
import os,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
command=Path('/usr/local/bin/darkxray') if root==Path('/opt/dark-xray') else root/'darkxray'

def run(args):
    try:subprocess.run(list(map(str,args)),check=True)
    except subprocess.CalledProcessError as ex:print('Command failed, exit:',ex.returncode)

while True:
    print('\n╔══════════════════════════════════════╗\n║           DARK XRAY  0.6             ║\n║       STANDALONE CONTROL CENTER      ║\n╚══════════════════════════════════════╝')
    print('1) Status    2) Start    3) Stop    4) Restart\n5) Diagnostics    6) Encrypted backup    7) Owner password reset\n8) Certificate help    9) IP Guard help    0) Exit')
    try:choice=input('DARK > ').strip()
    except (EOFError,KeyboardInterrupt):break
    if choice=='0':break
    if choice in {'1','2','3','4'}:
        if choice in {'3','4'} and input('Existing Xray connections will close. Type YES: ')!='YES':continue
        run(['systemctl',{'1':'status','2':'start','3':'stop','4':'restart'}[choice],'dark-xray.service','--no-pager'])
    elif choice=='5':run([command,'doctor'])
    elif choice=='6':
        path=input('New backup file (service-writable, e.g. /var/lib/dark-xray/backups/test.darkbackup): ').strip()
        if path:run([command,'backup','--output',path])
    elif choice=='7':
        print('Stop DARK first. Offline reset revokes the selected account sessions; it does not disable TOTP.')
        user=input('Owner username: ').strip()
        if user:run([command,'reset-password','--username',user])
    elif choice in {'8','9'}:run([command,'domain' if choice=='8' else 'guard-enable','--help'])
