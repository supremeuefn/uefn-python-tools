# uefn-python-tools

A growing collection of Python tools for UEFN. More will be added over time.

## Verse Field Tool

`tools/verse_field_tool.py` — a standalone in-editor GUI (PySide6) for creating, managing,
and MVVM-binding **Verse-exposed variables ("Verse fields")** on UEFN Widget Blueprints.

It's fully self-contained: it only needs `unreal`, the Python standard library, and
PySide6. Run it inside UEFN via `execute_python`.

### What it does

- **Bind** — bulk-bind a widget's Verse fields to engine widget properties (Text,
  ColorAndOpacity, RenderOpacity, …) or to the Verse fields of embedded child-widget
  instances, all at once.
- **Create Fields** — add Verse fields of any supported type (float, int, logic, string,
  message, color, color_alpha, material, texture), organized into categories.
- **Manage Fields** — edit a field's category or delete it. Deletion is crash-safe (a
  naive delete of a freshly-created field will crash UEFN; this tool handles it).

### Why

Creating and binding Verse fields on a Widget Blueprint normally means memory-patching the
editor by hand — error-prone and easy to crash UEFN. This tool wraps the whole
create → patch → compile → verify → bind workflow behind a UI.

### Limitations

**Bindings that need a conversion function or node can't be created — do those in the
editor UI.** The tool only creates bindings where the source and destination types match
directly, so anything requiring a conversion is either hidden from the target list or
shown locked.

- ❌ `texture` → `Brush` and `material` → `Brush` use MVVM conversion **nodes**
  (`MVVMK2Node_MakeBrushFromSoftTexture` / `...SoftMaterial`), which have no Python setter —
  creating one means synthesizing a wired multi-node `EdGraph`. These are the one case shown
  **locked** in the target list, as a signpost to go do them in the editor.
- ❌ `logic` → `Visibility` needs a conversion **function**
  (`Conv_BoolToSlateVisibility`) — `Visibility` is an enum, not a bool.
- ❌ `int` → `float` (e.g. `RenderOpacity`) is **not** implicit. It fails to compile with
  *"a conversion function is required"*. Same-type only.
- ✅ Works without conversion: `message`/`string` → `Text`, `color`/`color_alpha` →
  `ColorAndOpacity`, `float` → `RenderOpacity`, `logic` → `IsEnabled`.
- ✅ **Field-to-field bindings work for every type, including `texture`.** A parent field
  bound to an embedded child widget's same-type Verse field is plain property-to-property.
  The usual pattern: do the Brush conversion *inside* the child widget once, in the editor
  UI, then bulk-bind parent → child fields with this tool.

Other limits:

- Only Widget Blueprints. Bind targets cover the widget types UEFN actually exposes
  (`TextBlock`, `Image`); unbindable engine properties like `ToolTipText` are hidden.
- **Category order can't be changed.** It follows the order categories were first used, and
  the underlying array can't be safely patched. The order is cosmetic.
- Deleting a field also drops MVVM bindings that use it as a source.
- Memory offsets are probed at runtime rather than hardcoded, but a large engine update
  could still break them.

## Reference

`skills/uefn-verse-fields/` is a Claude Code skill documenting the underlying workflow,
pin-type mapping, safe deletion, and which MVVM bindings can and cannot be scripted from
Python — including every trap that will crash UEFN if done wrong.

## ⚠️ Warning

These tools memory-patch live UEFN editor state via `ctypes`. Use at your own risk, and
save your work first.
