"""Unprivileged client for DARK XRAY's root-owned update broker."""
from __future__ import annotations
import json,socket

MAX_UPDATE_MESSAGE=65536
DEFAULT_UPDATE_SOCKET='/run/dark-xray-update/control.sock'

class UpdateBrokerError(RuntimeError):pass

class UpdateBrokerClient:
    def __init__(self,path:str=DEFAULT_UPDATE_SOCKET,timeout:float=8):
        self.path,self.timeout=path,timeout
    def request(self,message:dict)->dict:
        raw=json.dumps(message,separators=(',',':')).encode()+b'\n'
        if len(raw)>MAX_UPDATE_MESSAGE:raise UpdateBrokerError('Update request too large')
        try:
            with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout);sock.connect(self.path);sock.sendall(raw)
                data=bytearray()
                while b'\n' not in data:
                    chunk=sock.recv(4096)
                    if not chunk:raise UpdateBrokerError('Update broker closed without response')
                    data.extend(chunk)
                    if len(data)>MAX_UPDATE_MESSAGE:raise UpdateBrokerError('Update response too large')
                out=json.loads(data.split(b'\n',1)[0])
        except UpdateBrokerError:raise
        except (OSError,ValueError) as exc:raise UpdateBrokerError('Update broker unavailable: '+type(exc).__name__) from exc
        if not isinstance(out,dict):raise UpdateBrokerError('Malformed update broker response')
        if out.get('ok') is not True:raise UpdateBrokerError(str(out.get('error','Update request refused'))[:600])
        return out
    def status(self):return self.request({'operation':'status'})
    def check(self,channel:str,ref:str=''):return self.request({'operation':'check','channel':channel,'ref':ref})
    def start(self,commit:str):return self.request({'operation':'start','commit':commit})
