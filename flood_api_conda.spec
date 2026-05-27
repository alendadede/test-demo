# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os
import sys

from PyInstaller.utils.hooks import collect_all, copy_metadata


PROJECT_DIR = Path.cwd()
CONDA_PREFIX = Path(os.environ.get("CONDA_PREFIX", sys.prefix))

if not (CONDA_PREFIX / "conda-meta").exists():
    raise SystemExit(
        "This spec must be built from an activated conda environment. "
        f"Current prefix: {CONDA_PREFIX}"
    )


def dedupe_toc_entries(entries):
    seen = set()
    unique = []
    for entry in entries:
        if entry not in seen:
            seen.add(entry)
            unique.append(entry)
    return unique


datas = []
binaries = []
hiddenimports = [
    "xml.parsers.expat",
    "pkg_resources",
    "pkg_resources._vendor.packaging",
    "pkg_resources.py2_warn",
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
]
excludes = [
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "PIL",
    "matplotlib",
    "lxml",
    "zmq",
    "IPython",
    "jupyter_client",
    "jupyter_core",
    "notebook",
    "qtconsole",
    "tkinter",
]

grid_outputs = PROJECT_DIR / "grid_outputs"
if grid_outputs.exists():
    datas.append((str(grid_outputs), "grid_outputs"))

stations_json = PROJECT_DIR / "stations.json"
if stations_json.exists():
    datas.append((str(stations_json), "."))

for package_name in ("fiona", "shapely", "pyproj"):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(package_name)
    datas.extend(pkg_datas)
    binaries.extend(pkg_binaries)
    hiddenimports.extend(pkg_hiddenimports)

for package_name in ("fastapi", "starlette", "uvicorn", "pydantic", "fiona", "shapely", "pyproj"):
    datas.extend(copy_metadata(package_name))

conda_library_bin = CONDA_PREFIX / "Library" / "bin"
if conda_library_bin.exists():
    for dll_path in conda_library_bin.glob("*.dll"):
        binaries.append((str(dll_path), "."))

conda_share_proj = CONDA_PREFIX / "Library" / "share" / "proj"
if conda_share_proj.exists():
    datas.append((str(conda_share_proj), "proj"))

conda_share_gdal = CONDA_PREFIX / "Library" / "share" / "gdal"
if conda_share_gdal.exists():
    datas.append((str(conda_share_gdal), "gdal"))

datas = dedupe_toc_entries(datas)
binaries = dedupe_toc_entries(binaries)
hiddenimports = sorted(set(hiddenimports))

a = Analysis(
    ["api_server.py"],
    pathex=[str(PROJECT_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=["pyi_rth_conda_gis.py"],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="flood-api",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="flood-api",
)
