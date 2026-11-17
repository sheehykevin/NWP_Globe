# UKMO NWP Globe

A 3D globe for Met Office model output, for the larger-domain models
where a flat map distorts. Prototype: rainfall from UKV (on top) and the
Global 10 km model, with Global MSLP isobars, on one globe you can spin
and zoom. It does not replace the flat viewer in the UKMO NWP tools.

![North Atlantic](docs/screenshots/globe_atlantic.png)

## Principle

Each model is drawn on its own native cells, built from the cell bounds
in its NetCDF files (UKV's Lambert equal-area grid is converted to
longitude/latitude with pyproj). Nothing is resampled; the value on a
cell is the value in the file. Dry cells (< 0.1 mm) are see-through.

## Install and run

```
conda env create -f environment.yml
conda run -n ukmo_globe python globe.py
```

Drag to rotate, scroll to zoom. Options:

| Option | Meaning |
|---|---|
| `--archive PATH` | Download archive of the UKMO NWP tools (default `..\ukmo_nwp\data\archive`) |
| `--ukv-run`, `--ukv-lead` | UKV run (`20261115T2100Z`) and lead hour |
| `--global-run`, `--global-lead` | Global run and lead hour; pick leads with the same valid time |
| `--view ireland` / `atlantic` | Starting view |
| `--screenshot FILE` | Write a PNG and exit |

The archive layout is `<archive>/<model>/<run>/<parameter>/*.nc`, as
written by the UKMO NWP tools.

## Notes

- GeoVista downloads its background picture and coastlines once, into
  `%LOCALAPPDATA%\geovista`; offline use needs these bundled.
- Needs working OpenGL; untested on remote desktops and virtual
  machines.
- Not yet: hover values, colour bar, time slider, wind, MOGREPS, other
  models (ICON, HARMONIE).
