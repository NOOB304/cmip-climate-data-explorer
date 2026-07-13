# Architecture

## Boundaries

- `domain`: immutable Pydantic models, failure codes, and the task state machine.
- `application`: job orchestration and the explicit full-download confirmation branch.
- `infrastructure/search`: backend adapters, normalization, retries, facets, mirror merging.
- `infrastructure/subset`: strict OPeNDAP capability probe, index planning, and hyperslab fetch.
- `infrastructure/download`: resumable HTTP transfer with validators, checksum, and controls.
- `infrastructure/region`: protected vector import, CRS normalization, repair, and selection.
- `infrastructure/processing`: scientific validation, aggregation, conversion, regridding, COG.
- `infrastructure/persistence`: SQLite/WAL repositories and frozen Alembic revisions.
- `ui`: PySide6 workbench; worker threads never read Qt widget state.

## Safety Invariants

1. `DownloadTask(mode=full_file)` is invalid without a confirmation ID.
2. `TaskRepository.create_task` verifies that confirmation against the persisted job and scope.
3. `StrictSubsetService` exhausts qualified remote subset mirrors and raises; it has no full-file
   fallback code path.
4. Full download candidates prefer HTTPS, try HTTP-to-HTTPS upgrades, and exclude plaintext HTTP
   unless the user explicitly enables it.
5. Temporary NetCDF, COG, catalog, settings, and manifest files are atomically replaced.
6. Scientific incompatibility or missing time stops output rather than filling or relabeling data.

## Persistence

SQLite uses foreign keys, WAL and `synchronous=NORMAL`. Alembic `0001` is a frozen base schema;
`0002` stores selected region feature identifiers. Running tasks become `interrupted` at startup.
The confirmation snapshot stores failure details, size, plan hash, scope, and timestamp.

## Packaging

PyInstaller produces an x64 `onedir` runtime containing Qt, GDAL/Fiona/Rasterio, PROJ,
NetCDF/HDF5, the official variable catalog, Alembic revisions, app icon, and Noto Sans CJK SC.
Inno Setup installs per user under `%LOCALAPPDATA%\Programs\CMIPClimateExplorer`.
