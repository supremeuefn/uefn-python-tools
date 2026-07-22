# Changelog

Changelog for the **Verse Field Tool** (`tools/verse_field_tool.py`). Versioning
is per-tool: this file and `VERSION.txt` live alongside the tool and track only
it. The in-tool **Check for updates** button shows this file so you can see what
a release changes before installing anything.

## v1.2.1

- **Verse fields on subclassed widgets are bindable again** — a custom button
  offered only its inherited `Text`.
- **Sub-widget button events now show as bound**, on the right button when a slot
  holds several. Unbinding no longer hits the others.
- Tidier target list: rows grouped by widget, one class name each, no doubled
  names.

## v1.2

- **Batch rename fields** in Manage Fields — Find & Replace, Prefix/Suffix, or
  Renumber, with a live preview and conflict checks. Bindings keep working;
  plain fields only.
- **Event bindings reach buttons inside sub-widgets** — single, paired, or bulk
  by number, for both button types and all three events. Multi-button slots pick
  the button in the Target dropdown.
- **Search boxes** on the Verse Fields, Bindable Targets, and Manage Fields lists.

## v1.1

Auto-update is now **opt-in** and safer, based on user feedback.

- **Auto-update is OFF by default.** A tool that downloads and runs code from the
  internet on every launch is a supply-chain risk if the repo is ever
  compromised, so it no longer does that unless you turn it on.
- **New ⚙ Settings dialog** (bottom-right) with an *Auto-update on launch* toggle
  and a **Check for updates now** button.
- **Manual check is check-only.** It fetches the version and these patch notes and
  downloads nothing until you review the changes and click **Install** — with a
  link to the commit diff so you can see exactly what's coming.

[Review commits: v1.0 → v1.1](https://github.com/supremeuefn/uefn-python-tools/compare/verse-field/v1.0...verse-field/v1.1)

## v1.0

- Added **versioning** and **auto-update** (later made opt-in in v1.1).
- You can now bind a `message` field to the **Text** field of UEFN buttons
  (Loud / Quiet / Regular).
- Click the **Name** column header to sort fields A–Z / Z–A (numbered fields
  order naturally, so `Slot2` comes before `Slot10`).
- Smoother first-time setup: PySide6 installs behind a small progress window and
  the tool opens automatically when it's done — no re-running the script.

[Commits](https://github.com/supremeuefn/uefn-python-tools/commits/main)
