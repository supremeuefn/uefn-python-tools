# uefn-python-tools

A growing collection of Python tools for UEFN. More will be added over time — for now it
holds the **Verse Field Tool**.

## Verse Field Tool

`tools/verse_field_tool.py` — a standalone in-editor GUI (PySide6) for creating, managing,
and MVVM-binding **Verse-exposed variables ("Verse fields")** on UEFN Widget Blueprints.

It's fully self-contained: it only needs `unreal`, the Python standard library, and
PySide6. Run it inside UEFN via `execute_python`.

### What it does

- **Bind** — bulk-bind a widget's Verse fields to engine widget properties (Text, Brush,
  ColorAndOpacity, …) or to the Verse fields of embedded child-widget instances, all at once.
- **Create Fields** — add Verse fields of any supported type (float, int, logic, string,
  message, color, color_alpha, material, texture), organized into categories.
- **Manage Fields** — edit a field's category or delete it. Deletion is crash-safe (a
  naive delete of a freshly-created field will crash UEFN; this tool handles it).

### Why

Creating and binding Verse fields on a Widget Blueprint normally means memory-patching the
editor by hand — error-prone and easy to crash UEFN. This tool wraps the whole
create → patch → compile → verify → bind workflow behind a UI.

## Reference

`skills/uefn-verse-fields/` is a Claude Code skill documenting the underlying workflow,
pin-type mapping, safe deletion, and which MVVM bindings can and cannot be scripted from
Python — including every trap that will crash UEFN if done wrong.

## ⚠️ Warning

These tools memory-patch live UEFN editor state via `ctypes`. Use at your own risk, and
save your work first.
