#!/usr/bin/env python3
"""Compatibility entrypoint for the installed-host load gate."""
from pathlib import Path
import runpy

ROOT=Path(__file__).resolve().parents[1]
runpy.run_path(str(ROOT/'tools/load-scale-gate.py'),run_name='__main__')
