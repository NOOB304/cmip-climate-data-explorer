# Windows Release

## Build

1. Install Python 3.12 and Inno Setup 6.
2. Create `.venv` and install `.[dev]`.
3. Run `tools\build_windows.ps1`.

The script runs Ruff and pytest, builds a PyInstaller `onedir`, creates a portable ZIP, compiles the
current-user Inno Setup installer when `ISCC.exe` is available, and prints SHA-256 values.

## Acceptance

Run the frozen self-test from `dist\CMIP Climate Explorer`, then silently install into a temporary
directory and run the installed self-test. Verify:

- `status=passed` and current Alembic revision;
- official variable count is nonzero;
- NetCDF/HDF5 write and read succeed;
- vector import succeeds;
- COG layout and EPSG:4326 are reported;
- uninstall removes application files but leaves user-generated data untouched.

Release signing requires an organization-owned Windows code-signing certificate. Unsigned local
builds are suitable for functional acceptance but can trigger Microsoft SmartScreen warnings.
