# FMAP Visibility Fix

Small Python utility for inspecting and patching Falcon BMS `.fmap` weather files without relying on a fixed file size.

This script was written to handle newer `F4Wx` output after the move to FMAP v8, where each cell gained an extra `fogLayerZ` float. Older fixers that hard-coded the previous file size can skip or corrupt these newer files.

## What It Does

- Detects FMAP layout from the header and total cell count instead of assuming one file size
- Supports both older FMAP layout and newer v8-style layout with `fogLayerZ`
- Prints a quick summary of weather cell counts, visibility, cumulus base, and fog layer data
- Can sync `fogLayerZ` to `cumulusBase`
- Can clamp visibility to minimum values by weather type

## Default Visibility Limits

These are the defaults currently used by the script:

- Sunny: `60.0 km`
- Fair: `40.0 km`
- Poor: `30.0 km`
- Inclement: `20.0 km`

Those thresholds were copied from the older `UOAF/f4bms_fix_wx` project, but this script applies them to newer FMAP layouts too.

## Requirements

- Python 3.10+ recommended
- No third-party packages required

## Usage

Inspect a file:

```powershell
python .\fmap_visibility_fix.py inspect .\10100.fmap
```

Inspect every `.fmap` in a folder:

```powershell
python .\fmap_visibility_fix.py inspect .\WeatherMapsUpdates
```

Dry-run the visibility clamp:

```powershell
python .\fmap_visibility_fix.py enforce-min-visibility .\10100.fmap --dry-run
```

Write a fixed copy:

```powershell
python .\fmap_visibility_fix.py enforce-min-visibility .\10100.fmap
```

Process a whole directory of `.fmap` files:

```powershell
python .\fmap_visibility_fix.py enforce-min-visibility .\WeatherMapsUpdates
```

Overwrite in place and keep a backup:

```powershell
python .\fmap_visibility_fix.py enforce-min-visibility .\10100.fmap --in-place --backup
```

Sync `fogLayerZ` from `cumulusBase`:

```powershell
python .\fmap_visibility_fix.py sync-fog-layer-z .\10100.fmap
```

## Output Behavior

By default, the script does not overwrite the source file. It writes a sibling file named like:

```text
10100.fixed.fmap
```

Use `--in-place` if you want to modify the original file directly.

When the input is a directory, the default output is a sibling directory named like:

```text
WeatherMapsUpdates.fixed
```

## Notes

- The script preserves the FMAP layout it reads. If the input has a `fogLayerZ` block, the output keeps it.
- `F4Wx` itself appears to treat `.fmap` as output, not as an editable input format. This tool exists to patch the binary directly.
- Sample file inspection during development showed that some FMAPs already have valid `fogLayerZ` data but still benefit from visibility clamping.

## Files

- `fmap_visibility_fix.py`
