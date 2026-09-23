import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT=Path(__file__).resolve().parents[1]


def load_gate():
    path=ROOT/'tools'/'public-panel-gate.py'
    spec=importlib.util.spec_from_file_location('dark_public_panel_gate_tests',path)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod


def response(status=200,service='DARK XRAY',mode='standalone',hsts='max-age=31536000'):
    body=json.dumps({'service':service,'mode':mode}).encode()
    headers=[f'HTTP/1.1 {status} OK','Content-Type: application/json',f'Content-Length: {len(body)}']
    if hsts:headers.append('Strict-Transport-Security: '+hsts)
    return ('\r\n'.join(headers)+'\r\n\r\n').encode()+body


def test_health_parser_requires_dark_200_and_positive_hsts():
    gate=load_gate()
    good=gate.parse_health_response(response())
    assert good['status']==200 and good['service']=='DARK XRAY'
    for raw in (response(status=302),response(service='other'),response(hsts=''),response(hsts='max-age=0')):
        with pytest.raises((ValueError,json.JSONDecodeError)):gate.parse_health_response(raw)


def test_exact_dns_requires_the_complete_expected_public_set():
    gate=load_gate()
    assert gate.dns_evidence(['1.1.1.1','8.8.8.8'],['8.8.8.8','1.1.1.1'],True)['ok'] is True
    assert gate.dns_evidence(['1.1.1.1','8.8.8.8'],['1.1.1.1'],True)['ok'] is False
    assert gate.dns_evidence(['1.1.1.1'],['10.0.0.1'],False)['ok'] is False


def test_report_is_private_and_refuses_symlink(tmp_path):
    gate=load_gate();path=tmp_path/'gate.json'
    gate.atomic_report(path,{'public_panel_gate_passed':True})
    assert json.loads(path.read_text())['public_panel_gate_passed'] is True
    assert os.stat(path).st_mode&0o777==0o600
    link=tmp_path/'link.json';link.symlink_to(path)
    with pytest.raises(RuntimeError):gate.atomic_report(link,{'x':1})
