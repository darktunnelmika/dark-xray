#!/usr/bin/env python3
"""Compatibility entrypoint for the installed-host load gate."""
from pathlib import Path
import importlib.util

ROOT=Path(__file__).resolve().parents[1]
TARGET=ROOT/'tools/load-scale-gate.py'
SPEC=importlib.util.spec_from_file_location('dark_load_scale_gate',TARGET)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError('Unable to load installed-host load gate')
MODULE=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

# Preserve the historical import surface used by regression tests without
# executing the CLI parser during module import.
for _name,_value in vars(MODULE).items():
    if not _name.startswith('__'):
        globals()[_name]=_value

if __name__=='__main__':
    raise SystemExit(MODULE.main())
