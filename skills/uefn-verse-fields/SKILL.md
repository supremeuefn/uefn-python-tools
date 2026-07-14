---
name: uefn-verse-fields
description: >-
  Programmatically create, delete, read, and MVVM-bind Verse-exposed variables ("Verse
  fields") inside UEFN Widget Blueprints via execute_python. Covers the create → patch
  Verse metadata → compile → verify workflow, pin-type mapping (float/int/logic/string/
  message/color/color_alpha/material/texture), Verse EVENT fields, safe deletion, MVVM
  property-binding creation, and MVVM EVENT bindings (button → Verse event field) via
  on-disk .uasset patching. Trigger whenever the task involves adding/removing Verse
  variables or event fields on a WBP (Widget Blueprint), patching VerseVariable/FieldNotify
  metadata, VerseClassFields, or wiring MVVM bindings for a UEFN UI widget. These
  operations WILL crash UEFN if done wrong — follow the procedures exactly.
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
     ("VF_AccentColor",   "color_alpha"),
     ("VF_OnConfirm",     "event"),            # event field
     ("VF_OnScore",       "event", "int")],    # ...with an int/float/logic param
    category="Style",              # optional
)
verse_fields.list_verse_fields(path)         # name / type / ue5_class / category
verse_fields.verify_verse_fields(path, names)
verse_fields.delete_verse_fields(path, names)

# Event bindings (button -> Verse event field) — patched on disk, see the reference
verse_fields.list_event_widgets(path)        # only widgets whose CDO declares a delegate
verse_fields.list_event_bindings(path)
verse_fields.create_event_bindings(path, [("MyButton", "VF_OnConfirm", "OnButtonClicked")])
verse_fields.retarget_event_bindings(path, ...)
verse_fields.remove_event_bindings(path, ...)

# A parameterised event takes a 4th element: the value THAT button passes.
# Many buttons can share one event field, each sending a different value.
verse_fields.create_event_bindings(path, [
    ("Slot1", "VF_OnPick", "OnButtonClicked", "1"),
    ("Slot2", "VF_OnPick", "OnButtonClicked", "2"),
])
```

`create_verse_fields` validates every type **before** mutating the asset, patches Verse
metadata for each variable, and raises if any field fails to appear in `VerseClassFields`.
It cannot leave a plain (Verse-invisible) Blueprint variable behind.

`create_event_bindings` seeds the first event itself, so it works on a widget that has
**never** had one, and refuses a `(widget, delegate)` pair the widget does not declare
before writing anything.

A binding's parameter **value** lives in the event export's `SavedPins`, and changing its
length resizes **four** nested size fields. Getting that wrong does not fail loudly — it
**crashes the editor's loader on reload**. Read the reference before touching it.

Only drop to raw `execute_python` (the procedures in the reference) when `verse_fields.py`
doesn't cover the case — e.g. an engine update broke a probe and you need to re-derive an
offset. **A working implementation of all of the above lives in
`tools/verse_field_tool.py` in this repo** — read it rather than re-deriving the byte
layouts by hand.

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
3. **Compile + save through the tag-regenerating path** (see below) to serialize.
4. **Verify** via the `VerseClassFields` asset-registry tag (never via T3D round-trip).

## ⚠ `save_asset` silently demotes every field you just created

**`EditorAssetLibrary.save_asset` rewrites the package but does NOT regenerate the
asset-registry tags.** It leaves `VerseClassFields` exactly as it was, so a freshly
patched field is written out as an **ordinary Blueprint variable** and is simply gone the
next time the asset is read from disk. It looks fine in the live editor, which is what
makes this so nasty — and a *normal editor shutdown* rewrites the tags on the way out, so
old sessions' fields can appear to have persisted correctly and mask the bug entirely.

Always save through the engine's full save path, which rebuilds the tags from the live object:

```python
def save_regenerating_tags(wbp_path):
    wbp = unreal.EditorAssetLibrary.load_asset(wbp_path)
    pkg = wbp.get_outermost()                       # get_package_for_object
    pkg.modify()                                    # dirty it, or the save no-ops
    unreal.EditorLoadingAndSavingUtils.save_packages([pkg], False)
```

Use it at **every** save that finishes a create or feeds a pre-unload snapshot. Verify by
reading the `VerseClassFields` tag **out of the saved file**, not out of memory.

## Event fields and event bindings ARE possible (2026-07)

Earlier versions of this skill said Verse **event** fields and MVVM **event** bindings
could not be made from Python. That was true only of the in-editor `unreal` API. Both are
now solved and verified end-to-end:

- **Verse event fields** — creatable via the same descriptor patch, with different flags
  and metadata. **Including parameterised events** (`int`/`float`/`logic`): a parameter is
  nothing more than a serialized `EdGraphPinType` in the `EventParameters` metadata value,
  which a plain event leaves empty. Same member, same flags, same keys. See *Verse Event
  Fields* → *Event parameters* in the reference.
- **Event bindings** (button → Verse event field) — the *generation* step really is
  editor-C++-only, but the generated result is just serialized data, so you patch the
  `.uasset` **on disk** with pure Python (no .NET, no UAssetGUI). Create, retarget, and
  remove all work, including on a widget that has never had an event.
  See *Event Bindings — the disk layer* in the reference.

What genuinely remains UI-only: **`texture`/`material` → `Brush`** property bindings,
which need an MVVM conversion *node*.

## What to read before each task

| Task | Read in `reference/verse-field-tools.md` |
|---|---|
| Create variables (any type) | *Creating Variables*, *Type Mapping*, *Patching Verse Metadata* |
| Create an **event** field | *Verse Event Fields* |
| Delete variables | *Deleting Variables — Safe Method* |
| Read what fields exist | *Reading Verse Fields* |
| Read or create **property** bindings | *MVVM Bindings* (note: Brush bindings **cannot** be made from Python) |
| Create/retarget/remove **event** bindings | *Event Bindings — the disk layer* |
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
- **Never** finish a create with `save_asset` — it does not regenerate `VerseClassFields`
  and the field is silently demoted to a plain BP variable. Use `save_packages` (above).
- **Never** delete `BackPointer`. It is **shared by every Verse event field** on the widget
  — their function graphs wire it to the `Target` pin. Deleting it breaks *all* of them,
  and only surfaces at the next compile (`BS_ERROR`: "This blueprint (self) is not a
  VerseUIUserWidget, therefore ' Target ' must have a connection"). **Recreating the
  variable does NOT repair it** — the graphs' *connections* are severed, not the variable;
  the only fix is restoring the `.uasset` from a backup. It sits in the member list looking
  like a leftover. It is not.
- **Never** call `unload_packages` / `reload_packages` / `collect_garbage` in a session
  where you created a field, **without detaching the metadata arrays first** — instant
  `EXCEPTION_ACCESS_VIOLATION`. This is a **vice with two real jaws**; see *The crash vice*
  in the reference. It bites ad-hoc test/probe scripts just as hard as the tool.
- **Always** guard *every* mutating call for the editor tab, not the ones you think need it.
  `remove_function_graph` and the disk patcher close the widget's open tab — and **whether
  that happens depends on the DATA, not the function**: deleting a plain field leaves the
  tab alone, deleting an *event* field closes it. Wrap create/delete/category/bind uniformly
  and restore on the failure path too. Same for focus: reclaim it in the wrapper, never per
  call site (handlers `return` early on error, which is exactly when the engine has taken
  the foreground). See *Operations close the widget's editor tab* in the reference.
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

**Property bindings** (Verse field → widget property) go through the `unreal` API below.
**Event bindings** (button → Verse event field) do NOT — they are patched on disk; see
*Event Bindings — the disk layer* in the reference.

- Read bindings with `get_view()` (read-only); `request_view()` **creates** a view (mutation).
- Bindings that use a conversion function serialize with an **empty `SourcePath`** — the real
  source lives on a pin of the conversion node's `EdGraph`. Don't report them as sourceless.
- **No conversion needed** (bindable directly from Python): `message`→`Text`,
  `color`/`color_alpha`→`ColorAndOpacity`, `float`→`RenderOpacity`.
- **The UEFN buttons (Loud/Quiet/Regular) bind `Text`** — their label, no conversion. They
  are **not** UMG widgets: they derive from `FortCTAButton`, which lives in
  **`/Script/FortniteUI`**, so their `MemberParent` is not the usual `/Script/UMG.…` — a
  hardcoded UMG prefix silently produces a parent that won't resolve. And because a button
  is a `UserWidget`, it *declares* `Text` but *inherits*
  `ColorAndOpacity`/`RenderOpacity`/`Visibility`/`IsEnabled` from `UUserWidget`; naming
  `FortCTAButton` as the parent of a property it does not declare won't resolve either.
  `Text` is the only property the editor offers on a button — offer only that. The plain
  **Custom Button** is a different lineage and has no `Text` at all. See *The UEFN buttons
  bind `Text`* in the reference.
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
