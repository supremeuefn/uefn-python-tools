# Changelog

Changelog for **Thumbnail Creator** (`tools/thumbnail_creator_tool.py`). Versions
and release tags are scoped to this tool only.

## v1.0.0

- Added per-tool semantic versioning and `thumbnail-creator/vX.Y.Z` releases.
- Added native SlateIM update controls, background checks, opt-in automatic
  installation, release notes, and in-process reload without restarting UEFN.
- Tagged downloads are checked for the expected embedded version, compiled before
  installation, and atomically swapped with a rollback copy.
- Pillow setup now uses a native SlateIM progress window with live pip output and
  Retry support, then opens Thumbnail Creator immediately when installation
  succeeds.
- Pillow remains isolated in `Saved/ThumbnailCreator/python_packages` and is
  constrained to the supported `>=10.4,<13` range.
