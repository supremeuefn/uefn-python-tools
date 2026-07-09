# UEFN Widget Blueprint Verse Field Tools

> **IMPORTANT: Read this document IN FULL before performing any variable creation, deletion, or binding operation on a Widget Blueprint. Failure to follow these procedures WILL crash UEFN.**

This document is the authoritative reference for programmatically managing Verse-exposed variables and MVVM bindings inside UEFN Widget Blueprints.

---

## Table of Contents

1. [Core Concepts](#core-concepts)
2. [Creating Variables — Correct Method](#creating-variables--correct-method)
3. [Type Mapping — Pin Types](#type-mapping--pin-types)
4. [Patching Verse Metadata](#patching-verse-metadata)
5. [Deleting Variables — Safe Method](#deleting-variables--safe-method)
6. [Reading Verse Fields](#reading-verse-fields)
7. [MVVM Bindings](#mvvm-bindings)
8. [Memory Layout Reference](#memory-layout-reference)
9. [Critical Warnings — What NOT To Do](#critical-warnings--what-not-to-do)
10. [Complete Working Examples](#complete-working-examples)

---

## Core Concepts

Verse fields are **normal Blueprint member variables** with specific metadata that makes them appear in the UEFN Verse class interface.

The workflow is always:

1. **Create** the variable with the correct pin type using the public API.
2. **Patch** only the Verse metadata (4 entries at offset 200) in memory.
3. **Compile and save** so the metadata is serialized to disk.
4. **Verify** via `VerseClassFields` asset registry tag.

There are no dedicated MCP tools for these operations. All operations must be performed via `execute_python` (running arbitrary Python inside the editor with access to the `unreal` module).

---

## Use a helper module — do not hand-roll this

The recommended approach is to wrap everything below into a single `verse_fields.py`
helper module (a reference implementation of `create_verse_fields` /
`list_verse_fields` / `verify_verse_fields` / `delete_verse_fields`) that handles the
traps for you. Prefer it over writing the create/patch sequence by hand:

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

verse_fields.list_verse_fields(path)      # name / type / ue5_class / category
verse_fields.verify_verse_fields(path, names)
verse_fields.delete_verse_fields(path, names)
```

`create_verse_fields` validates every type **before** mutating the asset, patches
Verse metadata for each created variable, and raises if any field fails to appear
in `VerseClassFields`. It cannot leave a plain Blueprint variable behind.

> **The failure this prevents:** `add_member_variable` on its own creates a *plain
> Blueprint variable*. It compiles, saves, and shows up in the details panel looking
> completely normal — but it is invisible to Verse, because it has no `VerseVariable`
> metadata. In UEFN there is no Blueprint graph to consume such a variable, so it is
> pure dead weight. Never call `add_member_variable` directly; always go through
> `create_verse_fields`, which guarantees create-and-patch happen together or the
> call raises.

---

## Creating Variables — Correct Method

### Step 1: Build the correct EdGraphPinType

For **basic types**, use the built-in helper:

```python
import unreal
bel = unreal.BlueprintEditorLibrary

pt_real = bel.get_basic_type_by_name("real")     # float/double
pt_text = bel.get_basic_type_by_name("text")     # message/string display
pt_int  = bel.get_basic_type_by_name("int64")    # integer
pt_bool = bel.get_basic_type_by_name("bool")     # logic
pt_str  = bel.get_basic_type_by_name("string")   # string
```

For **softobject types** (material, texture), use `import_text`:

```python
pt_material = unreal.EdGraphPinType()
pt_material.import_text(
    '(PinCategory="softobject",'
    'PinSubCategoryObject="/Script/CoreUObject.Class\'/Script/Engine.MaterialInterface\'")'
)

pt_texture = unreal.EdGraphPinType()
pt_texture.import_text(
    '(PinCategory="softobject",'
    'PinSubCategoryObject="/Script/CoreUObject.Class\'/Script/Engine.Texture2D\'")'
)
```

> **CRITICAL:** The `import_text` method is the ONLY reliable way to construct softobject pin types in UEFN Python. Do NOT try `set_editor_property("pin_category", ...)` — it will throw `Failed to find property` errors. Do NOT create variables as "real" and then try to memory-patch their type — this corrupts the FName and causes crashes.

### Step 2: Add the member variable

```python
wbp = unreal.EditorAssetLibrary.load_asset("/YourProject/UI/WBP_Example.WBP_Example")
bel.add_member_variable(wbp, "VF_MyVariable", pt_material)
```

### Step 3: Compile and save

```python
bel.compile_blueprint(wbp)
unreal.EditorAssetLibrary.save_asset("/YourProject/UI/WBP_Example")
```

At this point the variable exists with the correct type but is NOT yet a Verse field. It needs metadata patching (see below).

---

## Type Mapping — Pin Types

| Friendly Name | Pin Construction Method | PinCategory | PinSubCategoryObject |
|---|---|---|---|
| `float` | `get_basic_type_by_name("real")` | `real` | — |
| `int` | `get_basic_type_by_name("int64")` | `int64` | — |
| `logic` | `get_basic_type_by_name("bool")` | `bool` | — |
| `string` | `get_basic_type_by_name("string")` | `string` | — |
| `message` | `get_basic_type_by_name("text")` | `text` | — |
| `color` | `import_text(...)` | `struct` | `VerseColors.Colors_color` |
| `color_alpha` | `import_text(...)` | `struct` | `VerseColors.Colors_color_alpha` |
| `material` | `import_text(...)` | `softobject` | `MaterialInterface` |
| `texture` | `import_text(...)` | `softobject` | `Texture2D` |

### Color types

Verse colors are **not** `FLinearColor`. They are Verse-native structs in the
`/VerseColors` package, with name-mangled members (`__verse_0x35184040_R`):

```python
COLOR       = "/Script/CoreUObject.VerseStruct'/VerseColors/_Verse/VNI/VerseColors.Colors_color'"
COLOR_ALPHA = "/Script/CoreUObject.VerseStruct'/VerseColors/_Verse/VNI/VerseColors.Colors_color_alpha'"

pt = unreal.EdGraphPinType()
pt.import_text('(PinCategory="struct",PinSubCategoryObject="%s")' % COLOR)
```

`color` holds R/G/B; `color_alpha` wraps a `color` plus `A`.

> **TRAP:** `get_basic_type_by_name("color")` does **not** fail — it silently
> returns a `PinCategory="int"` pin. So do `"linearcolor"` and `"linear_color"`.
> Always use `import_text` for colors and verify the resulting pin category.

### The `message` type needs a fifth metadata key

A `text`-pinned Verse field carries an extra `DisableDefaultValue` metadata entry
that no other type has. Omit it and the field misbehaves in the Verse interface.
See "Patching Verse Metadata" below.

---

## Patching Verse Metadata

After creating variables with correct types, you must patch the `MetaDataArray` in the `NewVariables` descriptor to make them appear as Verse fields.

### Constants

```python
DESC_SIZE            = 232   # Size of each variable descriptor
GUID_OFFSET          = 12    # Offset to VarGuid within descriptor
METADATA_OFFSET      = 200   # Offset to MetaDataArray TArray within descriptor
PROPERTY_FLAGS_OFFSET = 176  # Offset to PropertyFlags within descriptor
ENTRY_SIZE           = 32    # Size of each metadata entry (FName + FString)
VERSE_PROPERTY_FLAGS = 65541
```

> **`NEWVARS_OFFSET` is deliberately absent.** It is build-specific — it has been
> observed as both 448 and 384 on different builds. Do not hardcode it — locate the
> `NewVariables` TArray by probing for a `VarGuid` you already know from the T3D
> export (see `find_newvars_offset` below). A wrong offset reads zero variables,
> or worse, writes into unrelated memory.

### FName Key Bytes — derive them, never hardcode them

The four (five, for `message`) metadata keys are `FName`s. An `FName`'s
comparison index is an allocation into the engine's global name pool, so its
numeric value is **only valid within one editor session**. Any hardcoded byte
string is wrong the moment the pool differs.

Layout of a metadata `DataKey` (16 bytes): `[ComparisonIndex u32][Number u32][DisplayIndex u32][slack u32]`,
where `DisplayIndex == ComparisonIndex` and the trailing 4 bytes are **uninitialized
heap garbage** the engine never reads. Copying all 16 bytes from a donor variable
happens to work, but bakes in that garbage.

Intern the name yourself and read its real index back:

```python
def fname_key16(s):
    """Intern FName `s`; return the 16 bytes UE stores for a metadata DataKey."""
    pt = unreal.EdGraphPinType()
    pt.import_text('(PinCategory="struct",PinSubCategory="%s")' % s)
    _keepalive.append(pt)                      # pt owns the FName; must outlive use
    p   = ctypes.c_uint64.from_address(id(pt) + 40).value   # -> raw struct
    ci  = ctypes.c_uint32.from_address(p + 20).value        # ComparisonIndex
    num = ctypes.c_uint32.from_address(p + 24).value        # Number
    return struct.pack('<IIII', ci, num, ci, 0)

K_FIELD_NOTIFY = fname_key16("FieldNotify")
K_VERSE_VAR    = fname_key16("VerseVariable")
K_DISPLAY_NAME = fname_key16("DisplayName")
K_VISIBILITY   = fname_key16("VisibilityAccess")
K_DISABLE_DEF  = fname_key16("DisableDefaultValue")   # message/text fields only
```

Verified against live descriptors: all five derived keys match byte-for-byte.

### Locating NewVariables safely

```python
def find_newvars_offset(uobj, known_guid_bytes, expected_count):
    for off in range(0, 1400, 8):
        dptr = ctypes.c_uint64.from_address(uobj + off).value
        cnt  = ctypes.c_uint32.from_address(uobj + off + 8).value
        if cnt != expected_count or not (0x10000 < dptr < 0x7fffffffffff):
            continue
        head = bytes((ctypes.c_uint8 * 64).from_address(dptr))
        if head.find(known_guid_bytes) == GUID_OFFSET:
            return off, dptr
    raise RuntimeError("NewVariables not found — layout changed")
```

### Metadata Patching Procedure

```python
import ctypes

# Keep buffers alive globally to prevent dangling pointers
if not hasattr(unreal, "_verse_field_buffers"):
    unreal._verse_field_buffers = []

def build_fstring(text):
    """Allocate a persistent FString buffer. create_unicode_buffer already adds
    the NUL terminator; FString Num counts it, so Num == len(text) + 1."""
    buf = ctypes.create_unicode_buffer(text)
    unreal._verse_field_buffers.append(buf)
    return ctypes.addressof(buf), len(text) + 1

def build_metadata_block(display_name, is_message, visibility="<public>"):
    """Build the Verse metadata block. `message`/text fields take a 5th key."""
    entries = [
        (K_FIELD_NOTIFY, None),         # FieldNotify        — no value
        (K_VERSE_VAR,    None),         # VerseVariable      — no value
        (K_DISPLAY_NAME, display_name),
        (K_VISIBILITY,   visibility),
    ]
    if is_message:
        entries.append((K_DISABLE_DEF, None))   # text pins only

    block = ctypes.create_string_buffer(len(entries) * ENTRY_SIZE)
    unreal._verse_field_buffers.append(block)
    base = ctypes.addressof(block)

    for i, (key, val) in enumerate(entries):
        ctypes.memmove(base + i * ENTRY_SIZE, key, 16)
        if val is not None:
            p, n = build_fstring(val)
            ctypes.c_uint64.from_address(base + i * ENTRY_SIZE + 16).value = p
            ctypes.c_uint32.from_address(base + i * ENTRY_SIZE + 24).value = n
            ctypes.c_uint32.from_address(base + i * ENTRY_SIZE + 28).value = n

    return base, len(entries)
```

Write the resulting `(ptr, count, capacity)` with `count == capacity == len(entries)`,
then set `PropertyFlags = 65541`.

### Applying Metadata to Variables

```python
import struct, re, os, tempfile

# 1. Export T3D to get VarName -> VarGuid mapping
export_path = os.path.join(tempfile.gettempdir(), "wbp_export.t3d")
task = unreal.AssetExportTask()
task.object = wbp
task.filename = export_path
task.automated = True
unreal.Exporter.run_asset_export_task(task)

with open(export_path, "r", encoding="utf-8") as f:
    t3d = f.read()

name_to_guid = {}
for line in t3d.splitlines():
    if "NewVariables(" in line and "VarName=" in line:
        m_name = re.search(r'VarName="([^"]+)"', line)
        m_guid = re.search(r'VarGuid=([A-F0-9]{32})', line)
        if m_name and m_guid:
            name_to_guid[m_name.group(1)] = m_guid.group(1)

def guid_str_to_bytes(gs):
    return struct.pack('<4I', *struct.unpack('>4I', bytes.fromhex(gs)))

# 2. Locate variable descriptors in memory
wbp = unreal.EditorAssetLibrary.load_asset(wbp_path)  # reload after export
uobj_ptr = ctypes.c_uint64.from_address(id(wbp) + 16).value
tarray_ptr = uobj_ptr + NEWVARS_OFFSET
data_ptr = ctypes.c_uint64.from_address(tarray_ptr).value
count = ctypes.c_uint32.from_address(tarray_ptr + 8).value

name_to_addr = {}
for i in range(count):
    desc_addr = data_ptr + i * DESC_SIZE
    guid_bytes = bytes((ctypes.c_uint8 * 16).from_address(desc_addr + GUID_OFFSET))
    for name, gs in name_to_guid.items():
        if guid_bytes == guid_str_to_bytes(gs):
            name_to_addr[name] = desc_addr
            break

# 3. Patch each variable
for name, addr in name_to_addr.items():
    if name == "BackPointer":
        continue

    block_addr = build_metadata_block(name)

    # Write MetaDataArray TArray (ptr, count, capacity)
    ctypes.c_uint64.from_address(addr + METADATA_OFFSET).value = block_addr
    ctypes.c_uint32.from_address(addr + METADATA_OFFSET + 8).value = 4
    ctypes.c_uint32.from_address(addr + METADATA_OFFSET + 12).value = 4

    # Set PropertyFlags = 65541 (required for Verse visibility)
    ctypes.c_uint64.from_address(addr + PROPERTY_FLAGS_OFFSET).value = 65541

# 4. Compile, save, verify
bel.compile_blueprint(wbp)
unreal.EditorAssetLibrary.save_asset(wbp_path)

asset_data = unreal.EditorAssetLibrary.find_asset_data(wbp_path)
fields = asset_data.get_tag_value("VerseClassFields") or ""
assert len(fields) > 0, "VerseClassFields is empty — metadata patch failed"
```

### Reading FName Keys from a Donor Widget

If the hardcoded FName key bytes stop working (e.g. after an engine update), read them from any widget that already has working Verse variables:

```python
donor_wbp = unreal.EditorAssetLibrary.load_asset("/YourProject/UI/WBP_Donor.WBP_Donor")
uobj_ptr = ctypes.c_uint64.from_address(id(donor_wbp) + 16).value
data_ptr = ctypes.c_uint64.from_address(uobj_ptr + NEWVARS_OFFSET).value

# Pick a variable index that has Verse metadata (check T3D export first)
desc_addr = data_ptr + INDEX * DESC_SIZE
meta_ptr = ctypes.c_uint64.from_address(desc_addr + METADATA_OFFSET).value
meta_count = ctypes.c_uint32.from_address(desc_addr + METADATA_OFFSET + 8).value

for i in range(meta_count):
    entry = meta_ptr + i * ENTRY_SIZE
    key_bytes = bytes((ctypes.c_uint8 * 16).from_address(entry))
    print(f"Entry {i}: {key_bytes.hex()}")
    # Order is: FieldNotify, VerseVariable, DisplayName, VisibilityAccess
```

---

## Deleting Variables — Safe Method

### The ONLY safe way to delete variables:

```python
wbp = unreal.EditorAssetLibrary.load_asset(wbp_path)
graph_editor = unreal.BlueprintGraphEditor.get_graph_editor_by_name(wbp, "EventGraph")
graph_editor.remove_member_variable("VF_VariableName")

unreal.BlueprintEditorLibrary.compile_blueprint(wbp)
unreal.EditorAssetLibrary.save_asset(wbp_path)
```

> **For a field created THIS session, DETACH its MetaDataArray before deleting** — or
> deletion crashes UEFN. A memory-patch-created field's MetaDataArray data pointer aims
> at YOUR ctypes buffer; when `remove_member_variable` destroys the descriptor, UE's
> `TArray` destructor calls `FMemory::Free()` on that pointer — memory UE's allocator
> never allocated → heap-corruption crash. `reload_packages` is the SAME crash (it frees
> every descriptor's array too), so it is not an escape hatch.
>
> **The fix (verified):** null the doomed descriptor's MetaDataArray so its destructor
> is a no-op (`Free(nullptr)` is safe), then delete normally. The metadata is already on
> disk from the create step's compile+save, and the variable is being removed anyway.
>
> ```python
> # descriptor located exactly like the patch step (probe VarGuid -> NewVariables)
> METADATA_OFFSET = 200
> ctypes.c_uint64.from_address(descriptor + METADATA_OFFSET).value      = 0  # data ptr
> ctypes.c_uint32.from_address(descriptor + METADATA_OFFSET + 8).value  = 0  # count
> ctypes.c_uint32.from_address(descriptor + METADATA_OFFSET + 12).value = 0  # max
> # ...then: graph_editor.remove_member_variable(name); compile; save
> ```
>
> Do this uniformly for every name you delete — nulling a disk-deserialized field's
> array is harmless (it just discards an engine-owned copy that's about to be freed
> anyway). No editor restart needed. To confirm a pointer is one of yours before
> nulling: compare it against `ctypes.addressof(b)` for `b in unreal._verse_field_buffers`.
>
> When deleting a bound field, also drop MVVM bindings whose **source** is the deleted
> field (rebuild the `bindings` array without them, same session) — they dangle
> otherwise.

> **Signature:** it is `get_graph_editor_by_name(blueprint, graph_name)`.
> `get_graph_editor(graph)` takes an **`EdGraph` object**, not `(blueprint, name)` —
> calling it the latter way raises `takes at most 1 argument (2 given)`.

### Batch deletion:

```python
wbp = unreal.EditorAssetLibrary.load_asset(wbp_path)
graph_editor = unreal.BlueprintGraphEditor.get_graph_editor_by_name(wbp, "EventGraph")

names_to_delete = ["VF_Var1", "VF_Var2", "VF_Var3"]
for name in names_to_delete:
    try:
        graph_editor.remove_member_variable(name)
    except Exception as e:
        print(f"Failed to remove {name}: {e}")

unreal.BlueprintEditorLibrary.compile_blueprint(wbp)
unreal.EditorAssetLibrary.save_asset(wbp_path)
```

### `remove_unused_variables` — USE WITH EXTREME CAUTION

```python
unreal.BlueprintEditorLibrary.remove_unused_variables(wbp)
```

> **WARNING:** This removes ALL variables that have NO references (no bindings, no graph nodes). This WILL delete Verse variables that haven't been bound yet. Only use this if you intentionally want to remove all unbound variables. NEVER use this as a cleanup step after creating variables — the new variables won't have bindings yet and will be immediately deleted.

---

## Reading Verse Fields

Reading is safe and does not modify memory:

```python
asset_data = unreal.EditorAssetLibrary.find_asset_data(wbp_path)
fields_blob = asset_data.get_tag_value("VerseClassFields") or ""
```

The blob is a parenthesized record format. Each field is wrapped in `(Name="...",InternalName="...",Type=...,...)`.

---

## MVVM Bindings

### Reading existing bindings:

```python
subsystem = unreal.get_editor_subsystem(unreal.MVVMEditorSubsystem)
view = subsystem.get_view(wbp)          # read-only; returns None if no view exists
bindings = list(view.get_editor_property("bindings"))
for b in bindings:
    print(b.export_text())
```

> **Use `get_view()` to read, not `request_view()`.** `request_view` *creates* an
> MVVM view if the widget doesn't have one — a mutation. Only call it when you
> intend to add the first binding.

### Bindings that use a conversion function have an EMPTY SourcePath

This is the single most misleading part of the format. A binding with a conversion
function serializes with **no source**:

```
SourcePath=(Paths=,WidgetName="",Source=None,...)          <- looks sourceless!
Conversion=(SourceToDestinationConversion="...MVVMBlueprintViewConversionFunction_0")
```

The real source moved onto a **pin of the conversion node**, inside a generated
`EdGraph`. Parsing `SourcePath` alone will report these bindings as having no source.
Reach the graph via the subsystem:

```python
graph = subsystem.get_conversion_function_graph(wbp, binding, True)  # True = source->dest
editor = unreal.BlueprintGraphEditor.get_graph_editor(graph)         # takes an EdGraph
for node in editor.list_all_nodes():
    print(node.get_class().get_name())
```

`EdGraph.nodes` and `K2Node.pins` are **protected** — unreadable from Python. To see
pin wiring and defaults, export T3D and parse the `CustomProperties Pin (...)` lines
(`LinkedTo=` present means the pin is wired to a variable; otherwise `DefaultValue=`).

### Two kinds of conversion

| | Serialized as | `get_conversion_function()` |
|---|---|---|
| Library function | `ConversionFunction=(FunctionReference=(...),Type=Function)` | returns the `Function` |
| Dedicated MVVM node | node class only, no `FunctionReference` | returns `None` |

Observed node classes: `MVVMK2Node_MakeBrushFromSoftTexture`,
`MVVMK2Node_MakeBrushFromSoftMaterial`. Both are **async** (their graph name ends
`_SourceToDest_Async`) because soft references must load first, and both are gated by
an `MVVMK2Node_AreSourcesValidForBinding` node.

### UEFN's conversion whitelist

Conversions must come from `/Script/VerseFortniteUI.VerseFortniteUIAllowedConversionLibrary`:

```
Conv_BoolToSlateVisibility          Conv_ObjectComparisonToSlateVisibility
Conv_DoubleToBoolInterval           Conv_DoubleToBoolSimple
Conv_IntegerToBoolInterval          Conv_IntegerToBoolSimple
Conv_DoubleToText                   Conv_IntToText
Conv_LinearColorToSlateColor        Conv_SlateColorToLinearColor
InvertBool                          MakeTransform
AddDoubles / AddIntegers / AddIntDouble
MultiplyDoubles / MultiplyIntegers / MultiplyIntDouble
MakeImageBrushFromTexture           MakeImageBrushFromMaterial
```

### Verse color structs bind DIRECTLY — no conversion needed

`VF_ColorVar` (`Colors_color`) → `TextBlock.ColorAndOpacity` (`FSlateColor`) and
`VF_ColorAlphaVar` (`Colors_color_alpha`) → `Image.ColorAndOpacity` both serialize
with `SourceToDestinationConversion=None`. The engine converts implicitly. Do not add
`Conv_LinearColorToSlateColor` for these.

Likewise `message`/`text` → `TextBlock.Text` and `float` → `RenderOpacity` need no
conversion. Conversion is required for: `logic` → `Visibility`,
`texture` → `Brush`, `material` → `Brush`.

### Creating a binding — VERIFIED WORKING

Only for bindings that need **no conversion function** (see the conversion section:
`message`→`Text`, `color`→`ColorAndOpacity`, `float`→`RenderOpacity`, …). Bindings
that require a conversion *node* cannot be created this way — see below.

```python
import unreal, uuid

subsystem = unreal.get_editor_subsystem(unreal.MVVMEditorSubsystem)
view = subsystem.get_view(wbp)          # request_view() if the widget has no view yet

binding_str = (
  '(SourcePath=(Paths=((BindingReference=(MemberName="VF_StatusMessage",'
  'MemberGuid=%s,bSelfContext=True),BindingKind=Property)),'
  'WidgetName="",ContextId=00000000000000000000000000000000,Source=SelfContext,'
  'bIsComponent=False,bDeprecatedSource=True),'
  'DestinationPath=(Paths=((BindingReference=('
  'MemberParent="/Script/CoreUObject.Class\\'/Script/UMG.Widget\\'",'
  'MemberName="ToolTipText"),BindingKind=Property)),'
  'WidgetName="Text1",ContextId=00000000000000000000000000000000,Source=Widget,'
  'bIsComponent=False,bDeprecatedSource=True),'
  'BindingType=OneWayToDestination,bOverrideExecutionMode=False,'
  'OverrideExecutionMode=Immediate,'
  'Conversion=(DestinationToSourceConversion=None,SourceToDestinationConversion=None),'
  'BindingId=%s,bEnabled=True,bCompile=True)'
  % (source_var_guid, uuid.uuid4().hex.upper()))

subsystem.add_binding(wbp)                       # append an empty shell
arr = list(view.get_editor_property('bindings'))
arr[-1].import_text(binding_str)                 # fill it
view.set_editor_property('bindings', arr)        # write the ARRAY back

unreal.BlueprintEditorLibrary.compile_blueprint(wbp)
unreal.EditorAssetLibrary.save_asset(wbp_path)
```

> **`set_editor_property('source_path', ...)` does NOT work** — `SourcePath` is
> read-only (`Property 'SourcePath' ... is read-only and cannot be set`). The
> read-only guard is per-property; `import_text()` on the whole binding struct
> bypasses it. That is the only way in.
>
> Bindings in the array are **struct copies**. Mutating one does nothing until you
> `set_editor_property('bindings', arr)` the whole array back.

### Field-to-field bindings (parent field → embedded child widget's field) — VERIFIED WORKING

A binding's destination does **not** have to be an engine widget property (`Text`,
`ColorAndOpacity`, …). It can be a **Verse field on an embedded child widget instance**.
Example: `WBP_Slots` holds five `WC_SlotWidget` instances named `Slot1`–`Slot5`; each
child exposes `VF_SlotImage` (`texture`) and `VF_SlotName` (`message`). The parent gets
matching `VF_SlotNImage`/`VF_SlotNName` fields, each bound to the corresponding child.

The destination `BindingReference` names the child's **generated class** as `MemberParent`
and the child's field as `MemberName`; `WidgetName` is the instance name; `Source=Widget`:

```
DestinationPath=(Paths=((BindingReference=(
  MemberParent="/Script/UMG.WidgetBlueprintGeneratedClass'/NewTesting/WC_SlotWidget.WC_SlotWidget_C'",
  MemberName="VF_SlotImage"),BindingKind=Property)),
  WidgetName="Slot2",...,Source=Widget,...)
```

**A `texture`→`texture` field binding needs NO conversion** (`SourceToDestinationConversion=None`).
This does not contradict the "`texture`→`Brush` needs a conversion node" rule below: the
Brush conversion node lives **inside the child widget** (the child binds its own
`VF_SlotImage` → its `Image.Brush`). The parent→child hop is same-type property-to-property,
so it is fully Python-scriptable — none of the un-scriptable Brush-node machinery is involved.

### Cloning an existing binding across N instances — the reliable pattern

When one instance is already wired in the editor UI (e.g. `Slot1`), replicate it to the
others by **cloning the export text**, not by hand-authoring paths. Two facts make this safe:

* **Zero the source `MemberGuid`** (`MemberGuid=00000000000000000000000000000000`). The
  compiler re-resolves it from `MemberName` on `compile_blueprint` — verified: a zeroed
  GUID comes back fully populated after reload. This avoids needing the source field's real
  GUID, which lives in the **protected** `NewVariables` array (unreadable from Python).
* **Give each clone a unique `BindingId`.** Duplicate ids collide.

```python
subsystem = unreal.get_editor_subsystem(unreal.MVVMEditorSubsystem)
view = subsystem.get_view(wbp)
existing = list(view.get_editor_property("bindings"))
template = next(b.export_text() for b in existing if 'VF_Slot1Image' in b.export_text())

ZERO = "0" * 32
new_structs = []
for i in range(2, 6):
    t = template
    t = t.replace('MemberName="VF_Slot1Image",MemberGuid=<slot1_src_guid>',
                  f'MemberName="VF_Slot{i}Image",MemberGuid={ZERO}')  # source field + zero GUID
    t = t.replace('WidgetName="Slot1"', f'WidgetName="Slot{i}"')      # dest instance
    t = t.replace('BindingId=<slot1_bid>', f'BindingId=<unique_hex_{i}>')
    nb = unreal.MVVMBlueprintViewBinding()   # standalone struct, NOT from add_binding()
    nb.import_text(t)
    new_structs.append(nb)

view.set_editor_property("bindings", existing + new_structs)   # write whole array back
unreal.BlueprintEditorLibrary.compile_blueprint(wbp)
unreal.EditorAssetLibrary.save_asset(wbp_path)
```

> **Build standalone `unreal.MVVMBlueprintViewBinding()` structs and append them** — do
> **not** use the `add_binding(wbp)` + `import_text` on the returned handle route. In
> testing, `import_text` on the `add_binding` result left an **empty** binding in the array
> (`SourcePath=(Paths=,Source=None)`); the append-standalone-structs pattern round-trips
> correctly. If you do end up with an empty binding, `remove_binding` no-ops on it — rebuild
> the array filtered to bindings whose text contains a real `VF_` source (see *Removing a
> binding*).
>
> **Always verify after reload**, not just after the write: re-`get_view()`, and confirm
> each source field maps to the expected `WidgetName`+dest field, every source `MemberGuid`
> is non-zero (proves the compiler resolved it), and there are no duplicate `BindingId`s.

### Finding embedded child-widget instance names from Python

`WidgetTree` exposes almost nothing (`get_all_widgets`/`root_widget` are absent/protected).
To confirm instance names + classes, probe named subobjects of the tree:

```python
tree = unreal.find_object(wbp, "WidgetTree")
for i in range(1, 11):
    obj = unreal.find_object(tree, f"Slot{i}")
    if obj:
        print(f"Slot{i}", obj.get_class().get_name())   # -> WC_SlotWidget_C
```

### Removing a binding

`subsystem.remove_binding(wbp, binding)` **silently no-ops** when handed a struct you
pulled out of the array earlier (it's a copy, not the live element). Rebuild the array
instead:

```python
keep = [b for b in view.get_editor_property('bindings') if <predicate>]
view.set_editor_property('bindings', keep)
```

### Bindings needing a conversion NODE cannot be created from Python

`texture`→`Brush` and `material`→`Brush` use `MVVMK2Node_MakeBrushFromSoftTexture` /
`...SoftMaterial`, not conversion *functions*. Evidence:

* `get_available_conversion_functions(texture, Brush)` returns `[]`, while
  `(logic, Visibility)` correctly returns 10 including `Conv_BoolToSlateVisibility`.
* The existing binding's `conversion_function` reads
  `(FunctionReference=(...empty...),Node="...MVVMK2Node_MakeBrushFromSoftMaterial'",Type=Node)`
  — versus `Type=Function` + populated `FunctionReference` for library conversions.
* `MVVMEditorSubsystem` exposes `get_conversion_function{,_node,_graph}` — **getters
  only**. There is no setter.

Creating one means synthesizing a 7-node `EdGraph` (bound event, the MVVM node, an
`MVVMK2Node_AreSourcesValidForBinding` gate, variable get/set, self) with wired pins.
Do it in the editor UI.

### Legacy recipe below — DOES NOT WORK, kept for reference

```python
import uuid

subsystem = unreal.get_editor_subsystem(unreal.MVVMEditorSubsystem)
view = subsystem.request_view(wbp)

binding_id = uuid.uuid4().hex.upper()

binding_str = (
    '(SourcePath=(Paths=((BindingReference='
    '(MemberName="{src_var_name}",'
    'MemberGuid={src_var_guid},'
    'bSelfContext=True),'
    'BindingKind=Property)),'
    'WidgetName="",'
    'ContextId=00000000000000000000000000000000,'
    'Source=SelfContext,'
    'bIsComponent=False,'
    'bDeprecatedSource=True),'

    'DestinationPath=(Paths=((BindingReference='
    '(MemberParent="/Script/UMG.WidgetBlueprintGeneratedClass'
    '\'/YourProject/UI/WBP_ChildWidget.WBP_ChildWidget_C\'",'
    'MemberName="{dest_var_name}",'
    'MemberGuid={dest_var_guid}),'
    'BindingKind=Property)),'
    'WidgetName="{dest_widget_name}",'
    'ContextId=00000000000000000000000000000000,'
    'Source=Widget,'
    'bIsComponent=False,'
    'bDeprecatedSource=True),'

    'BindingType=OneWayToDestination,'
    'bOverrideExecutionMode=False,'
    'OverrideExecutionMode=Immediate,'
    'Conversion=(DestinationToSourceConversion=None,'
    'SourceToDestinationConversion=None),'
    'BindingId={binding_id},'
    'bEnabled=True,'
    'bCompile=True)'
)

# Fill in the template values, then:
subsystem.add_binding(wbp)
bindings_list = list(view.get_editor_property("bindings"))
bindings_list[-1].import_text(formatted_binding_str)
view.set_editor_property("bindings", bindings_list)

bel.compile_blueprint(wbp)
unreal.EditorAssetLibrary.save_asset(wbp_path)
```

### Key binding fields:

| Field | Description |
|---|---|
| `MemberName` | Internal variable name (e.g. `VF_Slot1CharacterIcon`) |
| `MemberGuid` | 32-char hex GUID from T3D export. **Source side only** |
| `WidgetName` | Name of the widget instance (e.g. `Image2`, `Text1`) |
| `MemberParent` | Class path of the property's owner (e.g. `/Script/UMG.Image`) |
| `BindingId` | Unique UUID for this binding (`uuid.uuid4().hex.upper()`) |

### Observed source/destination shapes

The source is the Verse field on the widget itself, so it always uses
`Source=SelfContext`, `bSelfContext=True`, `WidgetName=""`, and carries a `MemberGuid`:

```
SourcePath=(Paths=((BindingReference=(MemberName="VF_MessageVar",
    MemberGuid=16FD67FE425A13353CC26991AE380126,bSelfContext=True),
    BindingKind=Property)),WidgetName="",Source=SelfContext,...)
```

The destination is a property on a child widget: `Source=Widget`, `WidgetName` set,
and **no `MemberGuid`** — native properties are identified by name + `MemberParent`:

```
DestinationPath=(Paths=((BindingReference=(
    MemberParent="/Script/CoreUObject.Class'/Script/UMG.TextBlock'",
    MemberName="Text"),BindingKind=Property)),WidgetName="Text1",Source=Widget,...)
```

Two exceptions seen in the wild:
* Properties inherited from `UWidget` (e.g. `RenderOpacity`, `Visibility`) omit
  `MemberParent` entirely and use `bSelfContext=True` on the destination reference.
* Every binding observed had `BindingType=OneWayToDestination`,
  `bDeprecatedSource=True`, `bEnabled=True`, `bCompile=True`.

### Clearing all bindings:

```python
view.set_editor_property("bindings", [])
```

### Getting source GUIDs:

Export the parent widget to T3D and parse `NewVariables` entries for `VarGuid`.

### Getting destination GUIDs:

Export the **child widget** (e.g. `WBP_Slot`) to T3D and parse its `NewVariables` entries.

---

## Memory Layout Reference

```
Variable Descriptor (232 bytes):
┌─────────────────────────────────────┐
│ Offset 0:   FName VarName (12 bytes)│
│ Offset 12:  FGuid VarGuid (16 bytes)│
│ Offset 28:  VarType / EdGraphPinType│
│             (116 bytes)             │
│ Offset 144: Category FText          │
│             (32 bytes)              │
│ Offset 176: PropertyFlags (8 bytes) │
│ Offset 184: (padding/other, 16 b)   │
│ Offset 200: MetaDataArray TArray    │
│             ptr(8) + count(4) +     │
│             capacity(4) = 16 bytes  │
│ Offset 216: (remaining 16 bytes)    │
└─────────────────────────────────────┘

MetaDataArray Entry (32 bytes):
┌─────────────────────────────────────┐
│ Offset 0:  FName Key (16 bytes)     │
│ Offset 16: FString Value            │
│            ptr(8) + count(4) +      │
│            capacity(4) = 16 bytes   │
└─────────────────────────────────────┘

NewVariables TArray (at UObject + 448):
┌─────────────────────────────────────┐
│ ptr to data (8 bytes)               │
│ count (4 bytes)                     │
│ capacity (4 bytes)                  │
└─────────────────────────────────────┘
```

---

## Critical Warnings — What NOT To Do

### 1. NEVER create variables as "real" then try to memory-patch their VarType

This was the root cause of multiple UEFN crashes. When you create a variable as `real` and then `memmove` the entire 232-byte descriptor body from a donor, you corrupt the FName at offset 0. Even if you try to restore the FName bytes, the engine's FName table gets confused and crashes on the next compile or save.

**Instead:** Use `import_text` on `EdGraphPinType` to construct the correct softobject pin type, then pass it to `add_member_variable`. The variable is created with the correct type from the start. Only patch the metadata at offset 200.

### 2. NEVER use `remove_unused_variables` right after creating variables

Newly created variables have zero references. `remove_unused_variables` will delete them immediately.

### 3. NEVER wipe MetaDataArray pointers then call `remove_unused_variables`

Setting metadata pointers to zero creates variables with corrupted internal state. When `remove_unused_variables` tries to iterate them, it reads invalid memory and crashes UEFN.

### 4. NEVER make HTTP calls from inside `execute_python` to the listener's own port

The listener runs on a single thread. If `execute_python` code makes an HTTP request back to the listener, it deadlocks and the connection times out, killing the listener.

### 5. NEVER use `set_editor_property("pin_category", ...)` on EdGraphPinType

UEFN's Python bindings don't expose `pin_category` as a settable property. Use `import_text` instead.

### 6. ALWAYS keep ctypes buffers alive

Store all `ctypes.create_string_buffer` and `ctypes.create_unicode_buffer` references in `unreal._verse_field_buffers` (or similar persistent list). If Python garbage-collects them before the asset is serialized, Unreal holds dangling pointers and crashes.

### 7. ALWAYS verify with VerseClassFields after patching

```python
asset_data = unreal.EditorAssetLibrary.find_asset_data(wbp_path)
fields = asset_data.get_tag_value("VerseClassFields") or ""
assert len(fields) > 0, "Patch failed"
```

### 8. ALWAYS reload the asset after T3D export

The `unreal.Exporter.run_asset_export_task` can invalidate internal pointers. Always call `unreal.EditorAssetLibrary.load_asset(path)` again before reading memory.

### 9. NEVER hardcode `NEWVARS_OFFSET` or the FName key bytes

Both are build- and session-specific. `NEWVARS_OFFSET` has been observed as both 448
and 384 on different builds. Probe for the offset using a known `VarGuid`, and derive
FName keys with `fname_key16()`. A stale offset silently reads 0 variables (best case)
or corrupts unrelated memory (worst case).

### 10. NEVER patch `CategorySorting` — it crashes the editor

Protected `TArray<FName>`, no Python access. Patching it crashes UEFN on asset
reload and changes nothing (the editor rebuilds it). A T3D export of your own bad
write reads back as correct, so **T3D cannot verify this patch**. See the
"Variable Category" section.

### 11. A T3D round-trip does NOT validate a memory patch

T3D re-serializes whatever is in memory — including bytes you just wrote wrong. It
confirms *your write happened*, not that the layout was right. Validate a patch by
an independent signal (`VerseClassFields` for Verse metadata, a fresh editor reload
for anything the UI consumes), never by reading back your own write.

### 12. `get_basic_type_by_name` fails silently on unknown names

`"color"`, `"linearcolor"`, `"linear_color"` all return a `PinCategory="int"` pin
rather than raising. Anything not in the basic-type table must go through
`import_text`, and you should assert on the resulting `PinCategory`.

---

## Variable Category (no memory patching needed)

`Category` is an `FText` at descriptor offset 144 — **not** a `MetaDataArray` entry.
It has a supported public API, so never patch it by hand:

```python
unreal.BlueprintEditorLibrary.set_blueprint_variable_category(wbp, "VF_MyVar", "TestCategory")
```

Uncategorized variables serialize as `Category=NSLOCTEXT("KismetSchema","Default","Default")`.
A category set **in the editor UI** gets a generated localization key, e.g.
`Category=NSLOCTEXT("","0405C955...","TestCategory")`.

### Two `FText` flavors — both work

`set_blueprint_variable_category` writes a culture-invariant `FText`:
`Category=INVTEXT("TestCategory")`. The editor UI writes a localized one:
`Category=NSLOCTEXT("", "<generated-key>", "TestCategory")`.

They serialize differently but share a display string, so the details panel groups
them into the same category. Don't "fix" an `INVTEXT` category to match `NSLOCTEXT`.
Note this if you parse T3D: a regex for `Category=NSLOCTEXT\(...\)` silently misses
every category written via Python.

### Category ordering — DO NOT patch it

There **is** a `CategorySorting` array persisted on the blueprint (a protected
`TArray<FName>`, not exposed to Python). It holds display-formatted names, with the
widget's own name at index 0:

```
CategorySorting(0)="WBP Example"       <- the widget, not a variable category
CategorySorting(1)="Default"
CategorySorting(2)="Test Category"
```

Headers render in the order each category was **first used**, recorded once and
persisted. `Default` is not pinned to the top — it merely tends to be used first.
A category alphabetically before `Default` (e.g. `AAA_First`) still renders *below*
it if created later. Variable order *within* a category follows declaration order.

> **WARNING — this crashed the editor.** `CategorySorting` can be located in memory
> and appears patchable. Writing it **crashes UEFN on asset reload**. The element
> stride cannot be trusted: one inferred from a read that "decodes correctly" may
> still be wrong, and **a T3D export of your own bad write reads back as correct**,
> so T3D does not verify this patch.
>
> To influence header order, create categories in the order you want them.
> There is no supported API. The payoff is cosmetic. Leave it alone.

The array is **append-only and never pruned**. Deleting the last variable in a
category leaves a ghost entry behind; `compile_blueprint` does not remove it. Ghosts
are harmless — an empty category renders no header — but they persist in the asset,
so `CategorySorting` is not a rebuildable cache. Do not try to clean them by patching
(see the warning above). It survives a crash-reload because the editor *repairs* a
corrupted array, not because it regenerates the contents from scratch.

---

## Complete Working Examples

### Example: Create 5 slots of Verse variables with bindings

```python
import unreal
import ctypes
import struct
import re
import uuid

wbp_path = "/YourProject/UI/WBP_Example.WBP_Example"
bel = unreal.BlueprintEditorLibrary

# --- Pin Types ---
pt_text = bel.get_basic_type_by_name("text")
pt_real = bel.get_basic_type_by_name("real")

pt_mat = unreal.EdGraphPinType()
pt_mat.import_text(
    '(PinCategory="softobject",'
    'PinSubCategoryObject="/Script/CoreUObject.Class\'/Script/Engine.MaterialInterface\'")'
)

pt_tex = unreal.EdGraphPinType()
pt_tex.import_text(
    '(PinCategory="softobject",'
    'PinSubCategoryObject="/Script/CoreUObject.Class\'/Script/Engine.Texture2D\'")'
)

var_defs = [
    ("CharacterIcon", pt_mat),
    ("CharacterTrait1", pt_tex),
    ("CharacterTrait2", pt_tex),
    ("CharacterTrait3", pt_tex),
    ("CharacterLevel", pt_text),
    ("CharacterName", pt_text),
    ("CharacterValue", pt_text),
    ("CharacterRarityIndex", pt_real),
]

# --- Step 1: Create Variables ---
wbp = unreal.EditorAssetLibrary.load_asset(wbp_path)
for slot in range(1, 6):
    for suffix, pt in var_defs:
        bel.add_member_variable(wbp, f"VF_Slot{slot}{suffix}", pt)

bel.compile_blueprint(wbp)
unreal.EditorAssetLibrary.save_asset(wbp_path)

# --- Step 2: Patch Verse Metadata ---
# (use the patching procedure from the "Patching Verse Metadata" section above)

# --- Step 3: Create MVVM Bindings ---
# (use the binding procedure from the "MVVM Bindings" section above)
```

### Example: Safely delete specific variables

```python
import unreal

wbp_path = "/YourProject/UI/WBP_Example.WBP_Example"
wbp = unreal.EditorAssetLibrary.load_asset(wbp_path)
graph_editor = unreal.BlueprintGraphEditor.get_graph_editor_by_name(wbp, "EventGraph")

to_delete = ["VF_Slot2CharacterIcon", "VF_Slot3CharacterIcon"]
for name in to_delete:
    graph_editor.remove_member_variable(name)

unreal.BlueprintEditorLibrary.compile_blueprint(wbp)
unreal.EditorAssetLibrary.save_asset(wbp_path)
```
