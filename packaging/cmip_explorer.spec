from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent
datas = collect_data_files("cmip_explorer", include_py_files=True)
datas += collect_data_files("rasterio", excludes=["tests/**"])
datas += collect_data_files("fiona", excludes=["tests/**"])
for distribution in ("h5netcdf", "netCDF4", "pydap", "xarray"):
    datas += copy_metadata(distribution)
binaries = []
hiddenimports = [
    "dask.array",
    "h5netcdf",
    "netCDF4",
    "xarray.backends.h5netcdf_",
    "xarray.backends.netCDF4_",
    "xarray.backends.pydap_",
]
hiddenimports += collect_submodules("pydap")
hiddenimports += collect_submodules("xarray.backends")
hiddenimports += collect_submodules("rasterio", filter=lambda name: ".tests" not in name)
hiddenimports += collect_submodules("fiona", filter=lambda name: ".tests" not in name)

analysis = Analysis(
    [str(ROOT / "src" / "cmip_explorer" / "app.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hooksconfig={},
    runtime_hooks=[],
    excludes=["matplotlib", "notebook", "IPython", "tkinter"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="CMIPClimateExplorer",
    icon=str(ROOT / "resources" / "windows" / "app-icon.ico"),
    console=False,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch="x86_64",
)
collect = COLLECT(
    exe,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="CMIP Climate Explorer",
)
