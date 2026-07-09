# uefn-python-tools

Python tools and a Claude Code skill for working with **Verse-exposed variables ("Verse fields")** and **MVVM bindings** inside UEFN Widget Blueprints.

## Contents

```
skills/uefn-verse-fields/     Claude Code skill: create / delete / read / MVVM-bind Verse fields
  SKILL.md                    The map — mental model, rules, quick facts
  reference/                  Trap-annotated authoritative reference
tools/verse_field_tool.py     Standalone PySide6 GUI: "Verse Fields + Binding Tool"
```

## `tools/verse_field_tool.py`

A self-contained in-editor GUI for creating, managing, and MVVM-binding Verse fields on
Widget Blueprints. Run it inside UEFN via `execute_python` (needs only `unreal`, the
standard library, and PySide6). Three tabs:

- **Bind** — bulk-bind parent fields to engine widget properties or embedded child-widget Verse fields.
- **Create Fields** — add Verse fields (float / int / logic / string / message / color / material / texture) with categories.
- **Manage Fields** — edit a field's category or delete it (crash-safe deletion).

## `skills/uefn-verse-fields`

A Claude Code skill documenting the create → patch Verse metadata → compile → verify
workflow, pin-type mapping, safe deletion, and which MVVM bindings can and cannot be made
from Python. These operations memory-patch the editor and **will crash UEFN if done
wrong** — the reference file spells out every trap.

## ⚠️ Warning

These tools memory-patch live UEFN editor state via `ctypes`. Read the skill's reference
before doing create / delete / patch / bind operations by hand. Use at your own risk.
