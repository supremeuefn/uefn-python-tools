# Changelog

Changelog for the **Verse Field Tool** (`tools/verse_field_tool.py`). Versioning
is per-tool: this file and `VERSION.txt` live alongside the tool and track only
it. The in-tool **Check for updates** button shows this file so you can see what
a release changes before installing anything.

## Unreleased (v1.2 draft)

**Batch rename Verse fields in Manage Fields.** Rename many fields at once,
right where you already manage them:

- **Three rename modes** — **Find & Replace** (swap a substring across every
  matching name), **Prefix/Suffix** (add or strip a prefix or suffix), and
  **Renumber** (re-sequence numbered fields, so `VF_Slot2…VF_Slot10` renumber
  cleanly).
- **Live preview with conflict detection.** The new names are shown before you
  commit, and any collision — with an existing field or with another rename in
  the batch — is flagged so nothing is applied until it's resolved.
- **Bindings are repointed automatically** — both property bindings and event
  bindings that reference a renamed field follow it, no manual re-wiring.
- **Plain fields only.** Event fields can't be renamed in place (their public
  name is tied to internal machinery), so they're refused with a note to
  **delete and recreate** instead.

**Event bindings now reach buttons inside sub-widgets.**

- A button nested one level down in an embedded sub-widget instance (e.g. the
  button inside each `Slot1`…`Slot5`) can now source a parent Verse **event**
  field — previously only top-level buttons showed up. They appear in the event
  target list as `Slot1 · Button`, in both the single/pair binding flow and the
  bulk `#`-numbered flow.
- **Bulk numbering keys on the sub-widget instance** (`VF_ClickEvent#` ×
  `Slot#`), mirroring how sub-widget *field* bulk binding already works — so
  `VF_ClickEvent1` → Slot1's button, `VF_ClickEvent2` → Slot2's, and so on.
- **Multi-button sub-widgets are disambiguated in the Target dropdown.** When a
  sub-widget holds more than one button, the Target list adds per-button entries
  named for the button (e.g. `Event (NewCustomButton) · On Clicked`), so `Slot#`
  resolves to exactly one button per slot and the whole batch binds cleanly. (The
  plain `Event · On Clicked` stays for single-button slots.)
- Works for **both** button shapes (the UEFN Loud/Quiet/Regular buttons and the
  Custom Button) and **all three** events (On Clicked, On Highlight, On
  Unhighlight). Parameterised events (Param 0) are supported in the single and
  Bind-Selected-Pairs flows — the bulk-by-number flow binds the event only.

**Search boxes.** Filter long lists as you type:

- **Verse Fields** and **Manage Fields** — search by field name, type (so `int`
  finds an int event field) or category.
- **Bindable Targets** — search by widget name, class, or event (delegate label);
  e.g. `custom`, `highlight`, `slot3`.

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
