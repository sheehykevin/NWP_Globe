# PyInstaller runtime hook: runs before the globe's own code.
#
# - A conda installation on the target PC exports PROJ_DATA / PROJ_LIB;
#   inherited, they make pyproj load that installation's proj.db instead
#   of the bundled one (wrong or failing projections). Remove them.
# - GeoVista's background picture and coastlines are bundled
#   (geovista_data); point GeoVista at them so nothing is downloaded.
# - eccodes is found through ECCODES_DIR (bundled in eccodes/lib).
import os
import sys

for _name in ("PROJ_DATA", "PROJ_LIB", "GDAL_DATA", "GDAL_DRIVER_PATH"):
    os.environ.pop(_name, None)

_base = getattr(sys, "_MEIPASS", "")
if os.path.isdir(os.path.join(_base, "geovista_data")):
    os.environ["GEOVISTA_CACHEDIR"] = os.path.join(_base, "geovista_data")
if os.path.isdir(os.path.join(_base, "eccodes", "lib")):
    os.environ["ECCODES_DIR"] = os.path.join(_base, "eccodes")
    try:
        os.add_dll_directory(os.path.join(_base, "eccodes", "lib"))
        os.add_dll_directory(_base)
    except (AttributeError, OSError):
        pass
