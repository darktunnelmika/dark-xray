#!/usr/bin/env python3
import base64,os,sys
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
p=Path(sys.argv[1] if len(sys.argv)>1 else '/etc/dark-license/signing.key');p.parent.mkdir(parents=True,exist_ok=True);k=Ed25519PrivateKey.generate();p.write_bytes(k.private_bytes(serialization.Encoding.Raw,serialization.PrivateFormat.Raw,serialization.NoEncryption()));os.chmod(p,0o600);print(base64.urlsafe_b64encode(k.public_key().public_bytes(serialization.Encoding.Raw,serialization.PublicFormat.Raw)).decode().rstrip('='))