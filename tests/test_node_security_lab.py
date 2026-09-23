"""Namespace guard tests: no addresses, routes, sockets or core are changed."""
import pytest
import node_security_lab as lab


@pytest.mark.parametrize('parent,current,interfaces', [
    ('', 'net:[2]', {'lo'}),
    ('not-a-namespace', 'net:[2]', {'lo'}),
    ('net:[1]', 'net:[1]', {'lo'}),
    ('net:[1]', 'invalid', {'lo'}),
    ('net:[1]', 'net:[2]', {'lo', 'eth0'}),
    ('net:[1]', 'net:[2]', set()),
])
def test_guard_refuses_invalid_or_host_topology(parent, current, interfaces):
    with pytest.raises(RuntimeError): lab.require_isolated(parent, current, interfaces)


def test_guard_accepts_only_distinct_loopback_namespace():
    lab.require_isolated('net:[1]', 'net:[2]', {'lo'})


def test_bootstrap_refusal_happens_before_any_command(monkeypatch):
    calls=[]
    monkeypatch.setattr(lab, 'check_namespace', lambda: (_ for _ in ()).throw(RuntimeError('refused')))
    monkeypatch.setattr(lab.subprocess, 'run', lambda *a, **kw: calls.append(a))
    with pytest.raises(RuntimeError, match='refused'): lab.main()
    assert calls == []
