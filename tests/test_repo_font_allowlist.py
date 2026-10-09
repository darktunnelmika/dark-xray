"""Exact-path, hash-pinned public fonts must not weaken repository hygiene."""
import importlib.util
from pathlib import Path
import shutil


def _checker():
    source = Path(__file__).resolve().parents[1] / "tools" / "repo-check.py"
    spec = importlib.util.spec_from_file_location("dark_public_fonts_repo_check", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_font_allowlist_accepts_only_pinned_open_license_assets(tmp_path, capsys):
    checker = _checker()
    checker.REQUIRED = []
    checker.ROOT = tmp_path
    source_root = Path(__file__).resolve().parents[1]
    for name, (_, license_name) in checker.APPROVED_PUBLIC_FONTS.items():
        for relative in (name, license_name):
            dest = tmp_path / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source_root / relative, dest)

    assert checker.main() == 0
    assert "Repository checks passed" in capsys.readouterr().out

    allowed_path = tmp_path / "web/fonts/vazirmatn-arabic-variable.woff2"
    allowed_path.write_bytes(allowed_path.read_bytes() + b"modified")
    assert checker.main() == 1
    assert "unapproved public font" in capsys.readouterr().err

    allowed_path.unlink()
    (tmp_path / "web/fonts/unknown-private.woff2").write_bytes(b"wOF2unauthorized")
    assert checker.main() == 1
    assert "unknown-private.woff2" in capsys.readouterr().err

    (tmp_path / "web/fonts/unknown-private.woff2").unlink()
    (tmp_path / "web/fonts/licenses/manrope-OFL.txt").unlink()
    assert checker.main() == 1
    assert "missing OFL license" in capsys.readouterr().err

    (tmp_path / "web/fonts/private.pem").write_text("not-a-key")
    assert checker.main() == 1
    assert "private/runtime/font file must not be published" in capsys.readouterr().err
