import importlib.util
import stat
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('dark_update', ROOT / 'tools' / 'update.py')
assert SPEC and SPEC.loader
UPDATE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(UPDATE)


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_normalize_source_permissions_repairs_umask_077_checkout(tmp_path):
    app = tmp_path / 'dark-xray'
    app.mkdir(mode=0o700)
    for name in UPDATE.COPY_DIRS:
        d = app / name
        d.mkdir(mode=0o700)
        f = d / ('sample.py' if name in {'backend', 'tools'} else 'sample.txt')
        f.write_text('x', encoding='utf-8')
        f.chmod(0o600)
        nested = d / 'nested'
        nested.mkdir(mode=0o700)
        nf = nested / 'asset.txt'
        nf.write_text('x', encoding='utf-8')
        nf.chmod(0o600)
    for name in UPDATE.COPY_FILES:
        f = app / name
        f.write_text('x', encoding='utf-8')
        f.chmod(0o600)

    UPDATE.normalize_source_permissions(app)

    assert mode(app) == 0o755
    for name in UPDATE.COPY_DIRS:
        d = app / name
        assert mode(d) == 0o755
        assert mode(d / 'nested') == 0o755
        for f in [p for p in d.rglob('*') if p.is_file()]:
            assert mode(f) == 0o644
    for name in UPDATE.COPY_FILES:
        assert mode(app / name) == (0o755 if name == 'darkxray' else 0o644)


def test_normalize_source_permissions_refuses_symlinked_source_dir(tmp_path):
    app = tmp_path / 'dark-xray'
    app.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (app / 'backend').symlink_to(outside, target_is_directory=True)
    try:
        UPDATE.normalize_source_permissions(app)
        assert False, 'unsafe symlinked source directory was accepted'
    except RuntimeError as exc:
        assert 'unsafe shape' in str(exc)
