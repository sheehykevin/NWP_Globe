"""Make sure GeoVista's map data (background picture, coastlines, base
mesh) is in its cache, so the build can bundle it."""
import geovista as gv

gv.natural_earth_1()
pl = gv.GeoPlotter(off_screen=True)
pl.add_base_layer(texture=gv.natural_earth_1())
pl.add_coastlines()
pl.close()
print("GeoVista data cached")
