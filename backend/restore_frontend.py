"""Read-only network readiness for Restore domains; never fetch a customer URL."""
from __future__ import annotations
import http.client, ipaddress, socket, ssl, time
from urllib.parse import urlsplit
from dark_policy import PolicyError

READY_PATH='/.well-known/dark-restore-ready'


def probe(address:str,domain:str,port:int,scheme:str)->bool:
    # DNS is resolved and checked by the caller. Pin that IP for the entire probe.
    with socket.create_connection((address,port),timeout=3) as tcp:
        sock=ssl.create_default_context().wrap_socket(tcp,server_hostname=domain) if scheme=='https' else tcp
        with sock:
            request=f'GET {READY_PATH} HTTP/1.1\r\nHost: {domain}:{port}\r\nConnection: close\r\n\r\n'
            sock.sendall(request.encode('ascii'))
            response=http.client.HTTPResponse(sock);response.begin()
            return response.status==204 and response.getheader('X-Dark-Restore')=='proxy-v1'


def inspect_domain(restore,domain:str)->dict:
    # Reuse the importer validation before any DB write, filename or shell hint.
    _,domain,_,_=restore._safe_url('https://'+domain.strip().lower().rstrip('.')+'/')
    restore.ensure_domain(domain)
    with restore.store.lock:
        urls=[row[0] for row in restore.store.db.execute('SELECT legacy_url FROM restore_subscriptions WHERE legacy_host=? AND deleted_at=0',(domain,))]
    endpoint_set=set()
    for value in urls:
        u=urlsplit(value)
        try:port=u.port or (443 if u.scheme=='https' else 80)
        except ValueError:raise PolicyError('Invalid persisted Restore port')
        endpoint_set.add((u.scheme,port))
    if len(endpoint_set)>32:raise PolicyError('Too many original Restore ports')
    error='';dns='ok';addresses=[]
    try:
        addresses=restore._public_addresses(domain)
        public=str(restore.engine.config.public_address or '')
        try:expected={str(ipaddress.ip_address(public))}
        except ValueError:expected=set(restore._public_addresses(public))
        if not set(addresses)<=expected:
            dns='mismatch';error='Domain A/AAAA records do not all match the HUB public address'
    except (PolicyError,OSError,ValueError) as ex:dns='error';error=str(ex)[:200]
    endpoints=[]
    for scheme,port in sorted(endpoint_set):
        ready=False
        if dns=='ok':
            try:ready=all(probe(ip,domain,port,scheme) for ip in addresses)
            except (OSError,ValueError,http.client.HTTPException):pass
        endpoints.append({'scheme':scheme,'port':port,'ready':ready})
    ready=bool(endpoints) and all(ep['ready'] for ep in endpoints)
    ssl_status='ready' if ready else 'missing'
    if not ready and not error:error='Original subscription port or TLS frontend is not ready'
    now=time.time()
    # The web worker need not be able to read the certificate private key.
    cert=f'/etc/letsencrypt/live/{domain}/fullchain.pem';key=f'/etc/letsencrypt/live/{domain}/privkey.pem'
    with restore.store.transaction() as db:
        db.execute('UPDATE restore_domains SET dns_status=?,ssl_status=?,cert_path=?,key_path=?,last_checked=?,updated_at=? WHERE domain=?',
            (dns,ssl_status,cert if ready else '',key if ready else '',now,now,domain))
    return {'domain':domain,'dns_status':dns,'addresses':addresses,'ssl_status':ssl_status,
        'endpoints':endpoints,'original_ports':[ep['port'] for ep in endpoints],'ready':ready,
        'error':error,'requires_root_apply':not ready,
        'apply_command':f'sudo darkxray restore-tls {domain}' if not ready else ''}
