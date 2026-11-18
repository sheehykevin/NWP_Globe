# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build of the globe: one folder, two programs.

Build with ``build.bat`` (repository root), or:
    python -m PyInstaller packaging/NWP_Globe.spec --noconfirm

Result: dist/NWP_Globe/ with
    NWP_Globe.exe        the globe window (no console)
    nwp-globe-fetch.exe  console program the window runs for HARMONIE,
                         IBI and NWS downloads (it reports progress on
                         its output, which a windowed program cannot)
sharing one _internal folder.
"""

import glob
import os
import sys

from PyInstaller.utils.hooks import collect_all

root = os.path.abspath(os.path.join(SPECPATH, ".."))
lib = os.path.join(sys.prefix, "Library")

datas = [(os.path.join(root, "docs"), "docs"),
         (os.path.join(root, "README.md"), ".")]
# GeoVista's background picture and coastlines, so the globe needs no
# download for them (the runtime hook points GEOVISTA_CACHEDIR here).
_gv = os.path.join(os.environ["LOCALAPPDATA"], "geovista")
if not os.path.isdir(_gv):
    raise SystemExit("GeoVista data not cached yet: build.bat prepares it")
datas.append((_gv, "geovista_data"))
# CA certificates for downloads (conda-forge certifi points at
# <prefix>/Library/ssl/cacert.pem; in the bundle that is _internal).
_cacert = os.path.join(lib, "ssl", "cacert.pem")
if os.path.isfile(_cacert):
    datas.append((_cacert, os.path.join("Library", "ssl")))

# eccodes: the library (its code tables are built into eccodes_memfs.dll)
# in eccodes/lib, where findlibs looks when ECCODES_DIR is set.
binaries = [(p, os.path.join("eccodes", "lib"))
            for p in glob.glob(os.path.join(lib, "bin", "eccodes*.dll"))]
hiddenimports = ["globe_core", "globe_data", "globe_fetch", "globe_icon",
                 "globe_ecmwf", "globe_gfs", "globe_meteofrance",
                 "globe_pressure", "globe_paths", "xarray.backends.netCDF4_",
                 "scipy.ndimage", "contourpy", "geovista.qt", "pyvistaqt"]

for package in ("geovista", "pyvista", "pyvistaqt", "vtkmodules", "pyproj",
                "netCDF4", "cftime", "eccodes", "gribapi", "copernicusmarine",
                "certifi", "contourpy"):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [os.path.join(root, "globe.py")],
    pathex=[root],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[os.path.join(SPECPATH, "rthook_globe.py")],
    excludes=["PyQt6", "PySide2", "PySide6", "IPython", "jupyter",
              "notebook", "pytest", "matplotlib.tests", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)
window = EXE(pyz, a.scripts, [], exclude_binaries=True, name="NWP_Globe",
             debug=False, strip=False, upx=False, console=False)
fetch = EXE(pyz, a.scripts, [], exclude_binaries=True,
            name="nwp-globe-fetch", debug=False, strip=False, upx=False,
            console=True)
coll = COLLECT(window, fetch, a.binaries, a.datas, strip=False, upx=False,
               name="NWP_Globe")
