---
name: uefn-verse-fields
description: >-
  Programmatically create, delete, read, and MVVM-bind Verse-exposed variables ("Verse
  fields") inside UEFN Widget Blueprints via execute_python. Covers the create → patch
  Verse metadata → compile → verify workflow, pin-type mapping (float/int/logic/string/
  message/color/color_alpha/material/texture), safe deletion, and MVVM binding creation
  including which bindings can and cannot be made from Python. Trigger whenever the task
  involves adding/removing Verse variables on a WBP (Widget Blueprint), patching
  VerseVariable/FieldNotify metadata, VerseClassFields, or wiring MVVM bindings for a UEFN
  UI widget. These operations WILL crash UEFN if done wrong — follow the procedures exactly.
---

# UEFN Widget Blueprint Verse Fields

Managing Verse-exposed variables and MVVM bindings inside UEFN Widget Blueprints. All
operations run inside the editor via the `execute_python` MCP tool — there are no
dedicated MCP tools for this.

> **These operations crash UEFN when done wrong.** Memory patching, wrong offsets, and
> dangling ctypes buffers each have a documented crash mode. Before performing any
> create / delete / patch / bind operation, read the relevant part of
> [`reference/verse-field-tools.md`](reference/verse-field-tools.md) — it is the
> authoritative, trap-annotated reference. This SKILL.md is only the map.

## The one rule that saves you: use a `verse_fields.py` helper

The recommended approach is a `verse_fields.py` helper module that implements the entire
create → patch → compile → save → verify cycle with every trap already handled (the
reference file contains the full implementation to build it from). **Prefer it over
hand-rolling the create/patch sequence.**

```python
import sys; sys.path.insert(0, r'C:\path\to\your\tools')  # folder containing verse_fields.py
import verse_fields

verse_fields.create_verse_fields(
    "/YourProject/UI/WBP_Example.WBP_Example",
    [("VF_StatusMessage", "message"),
     ("VF_PrimaryColor",  "color"),
     ("VF_AccentColor",   "color_alpha")],
    category="Style",              # optional
)
verse_fields.list_verse_fields(path)         # name / type / ue5_class / category
verse_fields.verify_verse_fields(path, names)
verse_fields.delete_verse_fields(path, names)
```

`create_verse_fields` validates every type **before** mutating the asset, patches Verse
metadata for each variable, and raises if any field fails to appear in `VerseClassFields`.
It cannot leave a plain (Verse-invisible) Blueprint variable behind.

Only drop to raw `execute_python` (the procedures in the reference) when `verse_fields.py`
doesn't cover the case — e.g. reading/creating MVVM bindings, or an engine update broke a
probe and you need to re-derive an offset.

## Mental model

A Verse field is a **normal Blueprint member variable** carrying specific metadata
(`VerseVariable` + `FieldNotify` + `DisplayName` + `VisibilityAccess`, plus
`DisableDefaultValue` for `message`/text) and `PropertyFlags = 65541`. Without that
metadata the variable exists and looks normal in the details panel but is **invisible to
Verse** — dead weight. `add_member_variable` alone produces exactly this broken state;
never call it without the metadata patch. Creation is always:

1. **Create** with the correct pin type (`get_basic_type_by_name` for basics, `import_text`
   for color/material/texture).
2. **Patch** the Verse metadata in memory (offset 200 of each descriptor).
3. **Compile + save** to serialize.
4. **Verify** via the `VerseClassFields` asset-registry tag (never via T3D round-trip).

## What to read before each task

| Task | Read in `reference/verse-field-tools.md` |
|---|---|
| Create variables (any type) | *Creating Variables*, *Type Mapping*, *Patching Verse Metadata* |
| Delete variables | *Deleting Variables — Safe Method* |
| Read what fields exist | *Reading Verse Fields* |
| Read or create MVVM bindings | *MVVM Bindings* (note: some bindings **cannot** be made from Python) |
| Set a variable's category | *Variable Category* |
| Debugging a crash / stale offsets | *Critical Warnings*, *Memory Layout Reference* |

## Non-negotiable rules (full list in the reference)

- **Never** create a variable as `real` then memory-patch its `VarType` — corrupts the
  FName, crashes on next compile/save. Build the correct pin type up front with `import_text`.
- **Never** hardcode `NEWVARS_OFFSET` or FName key bytes — both are build/session-specific.
  Probe the offset with a known `VarGuid`; derive FName keys with `fname_key16()`.
  (`NEWVARS_OFFSET` has been observed as both 448 and 384 on different builds.)
- **Never** use `remove_unused_variables` after creating variables — they have no
  references yet and get deleted immediately. Also never wipe metadata pointers then call
  it — it reads invalid memory and crashes.
- **Before deleting a same-session memory-patch-created field, DETACH its MetaDataArray.**
  A patched field's MetaDataArray data pointer aims at YOUR ctypes buffer; when the
  descriptor is destroyed (delete, `reload_packages`), UE's `TArray` destructor calls
  `FMemory::Free()` on that non-UE memory → heap-corruption crash (confirmed twice).
  The fix: null the descriptor's MetaDataArray (`data=0, count=0, max=0` at offset 200)
  so its destructor is a no-op, THEN `remove_member_variable`. The metadata is already
  serialized to disk and the variable is being removed anyway, so nothing is lost. This
  makes same-session delete safe with no editor restart — verified. (Do it uniformly;
  nulling a disk-deserialized field's array is harmless.) Also drop MVVM bindings whose
  source is a deleted field, or they dangle.
- **Never** make HTTP calls from inside `execute_python` back to the listener's own port —
  single-threaded, it deadlocks and kills the listener.
- **Never** patch `CategorySorting` — protected `TArray<FName>`, crashes on reload, and a
  T3D export of your own bad write reads back as "correct". Category *order* is cosmetic;
  leave it alone. Set a variable's category with `set_blueprint_variable_category` (public API).
- **Always** keep ctypes buffers alive in a persistent list (`unreal._verse_field_buffers`)
  or Unreal reads dangling pointers and crashes.
- **Always** verify with `VerseClassFields`, never with a T3D round-trip — T3D re-serializes
  whatever bytes are in memory, including wrong ones.
- **Always** reload the asset after a T3D export before reading memory — the export can
  invalidate internal pointers.
- `get_basic_type_by_name` **fails silently** on unknown names (`"color"`, `"linearcolor"`
  all return an `int` pin). Colors go through `import_text`; assert on the resulting `PinCategory`.

## MVVM binding quick facts

- Read bindings with `get_view()` (read-only); `request_view()` **creates** a view (mutation).
- Bindings that use a conversion function serialize with an **empty `SourcePath`** — the real
  source lives on a pin of the conversion node's `EdGraph`. Don't report them as sourceless.
- **No conversion needed** (bindable directly from Python): `message`→`Text`,
  `color`/`color_alpha`→`ColorAndOpacity`, `float`→`RenderOpacity`.
- **Conversion required**: `logic`→`Visibility`, `texture`→`Brush`, `material`→`Brush`.
- `texture`/`material`→`Brush` use MVVM conversion **nodes** (`MVVMK2Node_MakeBrushFrom…`),
  not functions, and **cannot be created from Python** — do them in the editor UI.
- **Field-to-field bindings ARE Python-scriptable, including `texture`→`texture`.** A parent
  field bound to an **embedded child widget's** same-type Verse field needs no conversion —
  the Brush conversion node lives inside the child, so the parent→child hop is plain
  property-to-property. Destination `BindingReference` names the child's generated class as
  `MemberParent` and the child field as `MemberName`, with `WidgetName="<InstanceName>"`,
  `Source=Widget`. See *Field-to-field bindings* in the reference.
- **To replicate a UI-made binding across N instances**: clone the existing binding's
  `export_text()`, swap source `MemberName` + dest `WidgetName`, **zero the source
  `MemberGuid`** (compiler re-resolves it by name on compile — avoids the protected
  `NewVariables` GUID), and give each a unique `BindingId`.
- Bindings in the array are struct **copies**; mutate, then
  `set_editor_property('bindings', arr)` the whole array back. `SourcePath` is read-only —
  only `import_text()` on the whole binding struct sets it. Build clones as **standalone
  `unreal.MVVMBlueprintViewBinding()` structs and append** — `import_text` on an
  `add_binding()` handle leaves an empty binding.
