#!/usr/bin/env python3
"""Compatibility entrypoint for DARK policy/accounting development API.

Implementation is split into policy_auth and policy_routes to keep authentication
and HTTP routing independently reviewable. This is not the Xray panel runtime.
"""
import argparse, sqlite3
from pathlib import Path
from dark_policy import PolicyError, Store
from policy_auth import (CAPABILITIES, ALLOWED_PERMISSIONS, DEFAULT_PERMISSIONS, PASSWORD_MIN_LENGTH, password_hash, verify_password, permissions_for, create_admin, bootstrap, StrictModel, Login, AdminCreate, AdminEdit, OwnerEdit, ClientCreate, ClientEdit, ResourceCredit, Usage)
from policy_routes import create_app

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,default=Path('./data/policy.sqlite3'))
    parser.add_argument('--init-owner',metavar='USERNAME',help='Initialize once; password is entered interactively, never in process arguments')
    parser.add_argument('--port',type=int,default=2087)
    args=parser.parse_args();store=Store(args.database)
    try:
        if args.init_owner:
            from getpass import getpass
            password=getpass(f'New owner password ({PASSWORD_MIN_LENGTH}+ characters): ')
            if password!=getpass('Repeat password: '):raise PolicyError('Passwords do not match')
            bootstrap(store,args.init_owner,password);print('Owner created. No default credentials exist.');return
        if not 1024<=args.port<=65535:raise PolicyError('Choose a nonprivileged port, 1024..65535')
        with store.lock:
            if not store.db.execute('SELECT 1 FROM api_admins').fetchone():raise PolicyError('Initialize with --init-owner first')
        import uvicorn
        # This development API is deliberately not exposed on all public interfaces.
        # For remote access use an SSH port forward; do not open the port globally.
        uvicorn.run(create_app(store),host='127.0.0.1',port=args.port,proxy_headers=False,access_log=False)
    except (PolicyError,OSError,sqlite3.Error) as exc:
        print('ERROR:',exc);raise SystemExit(2)
    finally:store.close()

if __name__=='__main__':main()
