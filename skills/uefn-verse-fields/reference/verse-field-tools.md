# UEFN Widget Blueprint Verse Field Tools

> **IMPORTANT: Read this document IN FULL before performing any variable creation, deletion, or binding operation on a Widget Blueprint. Failure to follow these procedures WILL crash UEFN.**

This document is the authoritative reference for programmatically managing Verse-exposed variables and MVVM bindings inside UEFN Widget Blueprints.

---

## Table of Contents

1. [Core Concepts](#core-concepts)
2. [Saving — `save_asset` demotes your fields](#saving--save_asset-demotes-your-fields)
3. [Creating Variables — Correct Method](#creating-variables--correct-method)
4. [Type Mapping — Pin Types](#type-mapping--pin-types)
5. [Patching Verse Metadata](#patching-verse-metadata)
6. [Verse Event Fields](#verse-event-fields)
7. [The crash vice](#the-crash-vice--create-then-unload)
8. [Deleting Variables — Safe Method](#deleting-variables--safe-method)
9. [Renaming a Verse field](#renaming-a-verse-field)
10. [Reading Verse Fields](#reading-verse-fields)
11. [MVVM Bindings](#mvvm-bindings)
12. [Event Bindings — the disk layer](#event-bindings--the-disk-layer)
13. [Memory Layout Reference](#memory-layout-reference)
14. [Critical Warnings — What NOT To Do](#critical-warnings--what-not-to-do)
15. [Complete Working Examples](#complete-working-examples)

---

## Core Concepts

Verse fields are **normal Blueprint member variables** with specific metadata that makes them appear in the UEFN Verse class interface.

The workflow is always:

1. **Create** the variable with the correct pin type using the public API.
2. **Patch** only the Verse metadata (4 entries at offset 200) in memory.
3. **Compile and save through the tag-regenerating path** so the metadata is serialized to disk.
4. **Verify** via `VerseClassFields` asset registry tag.

There are no dedicated MCP tools for these operations. All operations must be performed via `execute_python` (running arbitrary Python inside the editor with access to the `unreal` module).

---

## Saving — `save_asset` demotes your fields

> **`EditorAssetLibrary.save_asset` does NOT regenerate the asset-registry tags.** It
> rewrites the package but leaves `VerseClassFields` exactly as it was. A freshly created
> and patched Verse field is therefore written out as an **ordinary Blueprint variable**,
> and is gone the next time the asset is read from disk.

Decisive measurement: live object's tag = 23 fields, asset registry = 23, but the **saved
file** = 22. Switching to `save_packages` took the file's tag from **22 → 23** immediately.

```python
def save_regenerating_tags(wbp_path):
    """The ONLY save that keeps a Verse field a Verse field."""
    wbp = unreal.EditorAssetLibrary.load_asset(wbp_path)
    pkg = wbp.get_outermost()
    pkg.modify()                    # dirty it, or the save is skipped as a no-op
    unreal.EditorLoadingAndSavingUtils.save_packages([pkg], False)
```

Use this at **every** save that finishes a create or produces a snapshot for the disk
patcher. Everywhere this document shows `save_asset` after a metadata patch, read it as
`save_regenerating_tags`.

**Why this hid for so long:** a *normal editor shutdown* rewrites the tags on the way out.
Fields created in earlier sessions really were in the saved tag — but only because those
sessions were closed cleanly. That masks the bug completely and makes a correct diagnosis
look like a false alarm.

**Verify persistence by reading the tag out of the saved file**, from a *separate* Python
process (asset-registry blob → decode latin-1 → regex the field names). Do **not** reload
the package to check (that is the crash vice, below), and do **not** build a proximity
heuristic ("metadata keys within N bytes of the field name") — it false-negatives on real
UI-made fields.

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
save_regenerating_tags(wbp_path)     # NOT save_asset — see "Saving", above
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
    if name == "BackPointer":       # shared by every event field — NEVER touch or delete
        continue

    block_addr = build_metadata_block(name)

    # Write MetaDataArray TArray (ptr, count, capacity)
    ctypes.c_uint64.from_address(addr + METADATA_OFFSET).value = block_addr
    ctypes.c_uint32.from_address(addr + METADATA_OFFSET + 8).value = 4
    ctypes.c_uint32.from_address(addr + METADATA_OFFSET + 12).value = 4

    # PropertyFlags: 65541 for a plain field, 65557 for an EVENT field
    ctypes.c_uint64.from_address(addr + PROPERTY_FLAGS_OFFSET).value = 65541

# 4. Compile, save (tag-regenerating path — NOT save_asset), verify
bel.compile_blueprint(wbp)
save_regenerating_tags(wbp_path)        # see "Saving" — save_asset silently demotes fields

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

## Verse Event Fields

A Verse **event** field (`Type=Event` in `VerseClassFields`) is what a `button.OnClick`
binding fires into. It is created by the same descriptor patch, with three differences.

**An event field is THREE things:**

1. A member variable named **`VerseFieldInternalVariable_<PublicName>`** — *not* the public
   name. (This is the single biggest reason people fail to find it.) Its type is an object
   of class `/Script/VerseTypeEditorRuntime.VerseEvent`.
2. A **public function graph** named `<PublicName>` (`add_function_graph`).
3. A shared hidden **`BackPointer`** member of type `/Script/VerseUI.VerseUIUserWidget` —
   created once per widget, **shared by every event field on it**.

Then the same ctypes metadata patch, but:

| | Plain field | Event field |
|---|---|---|
| `PropertyFlags` | `65541` | **`65557`** |
| Metadata entries | FieldNotify, VerseVariable, DisplayName, VisibilityAccess | + **`Hidden`**, **`EventParameters`** |

> **Remove the function graph before the final save.** `add_function_graph` creates a *bare
> `FunctionEntry` stub*, and UEFN's `ValkyrieValidator_Blueprints` rejects that as
> **restricted content** — the asset fails validation and won't cook. The working sequence
> is: create the graph → let the metadata patch land → **`remove_function_graph` before the
> final save**. The graph is only needed transiently, to make the engine mint the right
> descriptor; the field stays a real event without it, and Verse regenerates the graph.
>
> Symptom of getting this wrong: the field looks fine in the editor, but the widget fails
> Data Validation, and the offending member shows up in the details panel under its raw
> internal name (`Verse Field Internal Variable VF_Foo`) instead of as a proper field.
>
> ⚠ **This is a workaround for the stub, NOT a rule that Verse event fields are graphless.**
> A genuine *editor-made* event field **does** keep a function graph — a real 7-node Verse
> payload graph (`FunctionEntry → CallFunction → MakeArray/MakeStruct →
> VariableGet(BackPointer) → FunctionResult`) — and the widget validates fine with it. What
> the validator rejects is the empty stub, not the concept. (An earlier version of this doc
> claimed the opposite; measured and corrected.)

### Event parameters (int / float / logic)

An event field can carry **one parameter**, exactly as the MVVM panel offers. It is **not a
different kind of field**: same `VerseEvent` member, same `65557` flags, same six metadata
keys. The *only* difference is that `EventParameters` — which a parameterless event leaves
**empty** — holds a **serialized `EdGraphPinType`**:

| Param | `EventParameters` metadata value |
|---|---|
| *(none)* | `""` |
| int | `(PinCategory="int64",PinSubCategoryMemberReference=(MemberGuid=0…0),PinValueType=())` |
| float | `(PinCategory="real",PinSubCategory="double",PinSubCategoryMemberReference=(MemberGuid=0…0),PinValueType=())` |
| logic | `(PinCategory="bool",PinSubCategoryMemberReference=(MemberGuid=0…0),PinValueType=())` |

Write that string as the value of the `EventParameters` entry and the engine does the rest —
on save it regenerates the tag as `EventParameters=((Type=Integer))` and the field is
indistinguishable from a UI-made one. **No graph surgery, no payload struct, no new
descriptor layout.** Verified: a tool-made `event (int)` reads back identical to an editor-made
one, compiles `BS_UP_TO_DATE`, validates `VALID`, persists to disk, and **binds to a button**.

The pin categories are the same ones used for plain `int`/`float`/`logic` fields, and the
GUID is zeroed (the compiler resolves it).

**Reading it back:** parse the parameter out of the `VerseClassFields` tag —
`EventParameters=((Type=Integer))` → `int`. The engine spells the parameter with the same
Verse type names it uses for a field's own `Type`.

> **Model the parameter as `(name, "event", param)`, not as an `"event_int"` kind.** A
> codebase that branches on `kind == "event"` (the tool does so in ~20 places: internal-name
> mapping, BackPointer creation, graph removal, binding, deletion) would need every one of
> those updated for a sibling kind, and missing one is silent. Keeping `"event"` as the kind
> and carrying the parameter alongside means all of them stay correct by construction.

### The per-binding parameter VALUE (`Param 0`) — and the crash it caused

The field's `EventParameters` declares the parameter's **type**. The **value each button
passes** ("this button sends 1, that one sends 2") is stored per *binding*, in the event
export's `SavedPins` array. Ten buttons can share one `event (int)` field and each pass a
different number — that is the point of a parameter.

```
MVVMBlueprintViewEvent_17                        <- one binding (one button)
  SavedPins  (ArrayProperty)          size @ tag+56    <- 645
    [0] MVVMBlueprintPin (StructProperty)
          Id -> PinNames: ["Param0"]
          Path, DefaultText, DefaultObject, bSplit, Status
          DefaultString (StrProperty)  size @ tag+20   <- 6
            = "0"                      count in FString <- 2
  GraphName
```

The value is **plain text** — `"7"`, `"2.5"`, `"true"` — which the engine parses back into the
pin's type. Defaults are `"0"` / `"0.0"` / `"false"`. A parameterless binding has **no
`SavedPins` at all**, so `SavedPins`' presence is what tells you whether a binding takes a value.

#### The pin is a TEMPLATE — build it, never demand a donor

A clone inherits its template's `SavedPins`, so cloning a plain event onto a parameterised
field leaves it with nowhere to put the value. The obvious response — "refuse, and tell the
user to hand-make one parameterised binding in the MVVM panel first" — is a **cop-out**: it
sends them to do by hand exactly what this tool exists to automate.

**Measured: the `SavedPins` block is identical for every widget AND every param type.** Rewrite
one type's value into another's block, fix the two sizes, and you reproduce that other
binding's bytes *exactly*. It encodes nothing about the widget, the field, or even the
parameter's type — the FIELD's `EventParameters` declares that; the pin only holds a value.

So **synthesize it** (`_build_pin_block` / `_add_pin`). Store it as ops, not a byte blob: the
block is riddled with FNames, which are *indices into the package's own name map*, so a literal
copy would point at whatever those indices happen to mean in the target asset. Resolve each
name through `add_name` (which reparses — so re-locate every offset afterwards).

Verified: a synthesized pin is **byte-identical to the editor's own**, for int, float and logic,
built onto a plain event that never had one.

#### Two engine behaviours that silently undo the work

Both write correctly to disk and are then reverted by the reload. Neither errors.

**1. The create's compile regenerates EVERY pin's value from the field.** Write a value in the
same `_patch_on_disk` pass that creates the event — cloned pin or synthesized, it makes no
difference — and the reload flattens it back to the default (measured: patcher wrote `1..7`, all
seven came back `'0'`). Values only stick if they are written in a **second pass**, after the
create's compile has already run.

> ⚠️ **A wrong measurement here shipped a bug.** An earlier pass concluded that a *cloned* pin's
> value survives the compile and only a *synthesized* one needs the second pass — so the second
> pass was gated on "did we build a pin", which took a 4-button bind from 4.55s to 2.82s. It was
> wrong. Cloned values are regenerated too. The symptom was maximally deceptive: in a real bind
> exactly ONE binding synthesizes its pin (the rest clone from it), so exactly one button kept its
> number and every other one silently read `0`. **Do not re-derive this "optimization".** The
> second pass is unconditional for any binding carrying a value; that is what it costs.

**2. A clone's GRAPH is typed, and the engine trusts it over the patched FName.** Clone an
`int` binding onto a `float` field and the asset comes back with the field silently re-pointed
to the int one — the binding is wrong, and nothing complains. So the donor's parameter must
**match** the field's. A parameterless event is always a safe donor (its graph carries no
parameter), which is why `_pick_template` prefers a same-typed binding, else a plain one, and
lets `_add_pin` build the missing pin.

**3. A clone with no delegate inherits the DONOR's — which may be the wrong spelling.** The two
button shapes name the same event differently (`OnButtonBaseClicked` on a UEFN button,
`OnButtonClicked` on a Custom Button). Omit the delegate and a UEFN-button donor hands its name
to a Custom Button that does not declare it; the compile then fails, loudly this time:

```
Event 'Custom Button.On Clicked -> Self.VF_Click()': the property path
'UIFrameworkCustomButtonWidget_297.OnButtonBaseClicked' is invalid.
```

`prepare()`'s guard does not catch it — that only vets a delegate the caller *passed*. Resolve the
inherited name through the `_EVENT_DELEGATES` group against what the target widget really declares
(`resolve_delegate`). The GUI always sends an explicit delegate, so this only bites API callers.

#### Changing the value's LENGTH resizes four fields, not one

`"0"` → `"7"` is the same byte length: a safe in-place overwrite. `"0"` → `"10"` is **one byte
longer**, and then *all* of these must grow together:

| Field | Where |
|---|---|
| the FString's own char count | in the value itself |
| `DefaultString`'s payload size | its tag **+20** |
| **`SavedPins`' payload size** | its tag **+56** ← the one that bites |
| the export's `SerialSize` | export entry +28 |

…plus every file offset past the insertion (`_shift_offsets`).

**`SavedPins` is an ArrayProperty *of structs*, so its size is NOT at +20.** The tag also names
its inner type (`StructProperty`) and the struct (`MVVMBlueprintPin`) before the size lands at
**+56**. Reading +20 returns the inner type's bytes decoded as an int (`249`) — plausible-looking
garbage.

I missed the array size and it **crashed UEFN** on the next reload:

```
Failed loading tagged ArrayProperty ...MVVMBlueprintViewEvent:SavedPins.
    Read 646B, expected 645B.
Serial size mismatch: Got 1880, Expected 1984
Assertion failed: LinkerLoad.cpp [Line: 5745]
```

There is **no quiet failure mode** — the package still parses in Python and then kills the
editor's loader. So verify the enclosure (`array_payload <= default_string < array_payload +
array_size`) before resizing, rather than trusting that the offsets landed right.

> **Two traps that made this worse, both worth internalising:**
>
> 1. **`find_fname` scans for an FName's 8 bytes and gets false positives** inside other
>    properties' payloads. It is only safe for the in-place FName retargeting it was built
>    for. My "SavedPins tag" hit decoded as `size=249` with a *package path* as its inner type
>    — visibly wrong, had I checked.
> 2. **A self-written parser confirming its own writes proves nothing.** My scratchpad test
>    passed all six cases — including grow *and* shrink — because it never validated container
>    sizes. It was the writer grading its own homework. **Only the engine is an honest check.**

#### How to get this right: use UAssetAPI as an oracle (not as a dependency)

`D:\Dev\uassetgui-uefn` is a real UE serializer (.NET, `dotnet build`). A no-op read+write of
the asset is **byte-identical** to the engine's own output, which makes it a trustworthy
reference. Use it to *measure*, then keep the shipped tool pure-Python:

1. Have UAssetAPI write the same edit (e.g. `"0"` → `"10"`).
2. Diff its output against its *unchanged* output — every differing int32 is exactly the set of
   size/offset fields the edit must touch. That diff is what revealed `SavedPins` +56.
3. Gate the Python writer on producing **byte-identical** output to UAssetAPI across values that
   fit, grow (+1, +6), shrink, and go negative.

The tool's writer passes that gate on all nine cases and the editor loads the result
(`BS_UP_TO_DATE`, `VALID`). UAssetAPI ships nothing — it was the measuring instrument.

### Operations close the widget's editor tab — guard ALL of them

`remove_function_graph` (and the disk patcher's unload) tears down the widget's open editor
tab. So does **deleting** an event field, not just creating one.

> **Whether the tab closes depends on the DATA, not on which function you called.**
> `delete_verse_fields` calls `remove_function_graph` *only for event fields* — so deleting
> a plain field leaves the tab alone while deleting an event field closes it. Testing one
> case and concluding "delete is fine" is exactly the trap (I fell in it). Deleting a
> **bound** event field is worse still: it also drops that field's bindings, which runs the
> disk patcher, closing the tab by a *second* independent route.

> **Reopen ONCE, at the end of the operation — not once per reload.** One click can reload the
> asset several times (a create that synthesizes pins patches twice; `delete_verse_fields` nests
> a whole `remove_event_bindings` inside itself). If the reload path reopens the tab each time,
> the widget visibly slams shut and springs open, twice, for one action. Hold a **counter**
> (not a flag — guarded calls nest) while an operation is in flight; the reload declines to
> reopen while it is non-zero, and the outer guard reopens exactly once in its `finally`. The
> declining reload must **leave** the "was it open" state in place rather than consuming it, or
> the outer guard no longer knows the tab needs restoring. Likewise, the open/close probe used
> to *detect* the tab must not spring it back open when the caller is about to close it anyway.

Guard **every** mutating entry point uniformly — create, delete, set-category, and all the
binding calls — rather than the ones you think need it:

```python
def keeps_editor_open(operation):
    @functools.wraps(operation)
    def wrapper(wbp_path, *args, **kwargs):
        note_editor_open(wbp_path)
        was_open = _WAS_EDITOR_OPEN.get(wbp_path, False)
        try:
            return operation(wbp_path, *args, **kwargs)
        finally:
            _WAS_EDITOR_OPEN[wbp_path] = was_open   # outer guard is authoritative
            reopen_editor(wbp_path)
    return wrapper
```

Two details that matter:

* **The outer guard must keep its own answer.** Nested operations (delete → remove bindings
  → the patcher's unload/reload) *consume* the shared "was it open" flag, and whether a
  nested call runs at all depends on the data. Re-asserting it means the tab is restored
  every time. Reopening an already-open tab is harmless; leaving it closed is not.
* **Restore on failure too.** A rollback must not cost the user their open widget.

**Reading the tab state:** `AssetEditorSubsystem` has no `find_editor_for_asset` binding.
The only call that reveals it — `close_all_editors_for_asset`, which **returns the count it
closed** — also closes it. So probe by closing and immediately reopening.

**Focus:** reopening the widget raises the UEFN window over your tool. Reclaim focus from
the same wrapper's `finally`, **not** from each call site — handlers `return` early on an
exception, so per-site calls get skipped exactly when the engine has stolen the foreground.

**Validation check:**

```python
sub = unreal.get_editor_subsystem(unreal.EditorValidatorSubsystem)
result, warnings, errors = sub.is_object_valid(wbp, unreal.DataValidationUsecase.MANUAL)
# result == unreal.DataValidationResult.VALID
```

---

## The crash vice — create, then unload

`create_verse_fields` ctypes-patches each new variable's `MetaDataArray` to point at
**your Python buffers**. That leaves the session in a vice, and **both jaws are real**
(this cost 4 editor crashes and one silently-demoted-field bug to pin down):

- **Don't detach → the GC frees your buffer → the editor dies.** Any later
  `unload_packages()` / `reload_packages()` / `collect_garbage()` destroys the descriptors,
  and `TArray`'s destructor calls `FMemory::Free()` on memory UE never allocated →
  `EXCEPTION_ACCESS_VIOLATION`. It dies *even though* `_KEEP` holds the buffer alive,
  because UE's allocator asserts on an unknown block. Log signature: `LogGarbage: Collecting
  garbage` immediately followed by the access violation, with engine frames sitting directly
  above `python311.dll`.
- **Detach, then let anything SAVE → the field is silently demoted.** The engine serializes
  a descriptor's metadata *from the array it points at*, so an emptied array written to disk
  strips the field. It keeps its name and its `65557` flags but loses its metadata, drops out
  of `VerseClassFields`, and becomes a plain BP variable. And `unload_packages` **flushes
  dirty packages** — detaching dirties the package, so the unload itself performs the fatal
  save. There is **no dirty-flag API** from Python (`Package` has no `is_dirty` /
  `set_dirty_flag`).

> **The only reliable test for a demoted field is the descriptor's metadata-entry count:
> a working event field has 6, a demoted one has 0.** The names (`Hidden`,
> `EventParameters`, the field name) all still linger in the package name map, so grepping
> for them proves nothing.

**The three rules, and all three must hold:**

1. `create_verse_fields` patches, saves, and **never detaches**. Buffers stay attached for
   the session; that is safe on its own.
2. Any `_unload` **must** detach (the crash is not optional) and therefore **will** flush
   emptied metadata over the asset file. Accept it.
3. So a disk patcher must **snapshot the file immediately after the last good save, BEFORE
   the unload, and patch the snapshot** — writing the result over whatever the unload left
   behind. The naive order (save → unload → read the file) reads an already-stripped file.

> **This bites ad-hoc test and probe scripts just as hard as the tool.** Any script that
> calls `reload_packages()` / `unload_packages()` / `collect_garbage()` after a create,
> without detaching first, kills the editor instantly. **To verify persistence, do not
> reload anything** — read the `VerseClassFields` tag straight out of the saved `.uasset`
> from a separate Python process. It needs no editor and cannot crash one.

---

## Deleting Variables — Safe Method

### The ONLY safe way to delete variables:

```python
wbp = unreal.EditorAssetLibrary.load_asset(wbp_path)
graph_editor = unreal.BlueprintGraphEditor.get_graph_editor_by_name(wbp, "EventGraph")
graph_editor.remove_member_variable("VF_VariableName")

unreal.BlueprintEditorLibrary.compile_blueprint(wbp)
save_regenerating_tags(wbp_path)     # NOT save_asset — see "Saving", above
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
save_regenerating_tags(wbp_path)     # NOT save_asset — see "Saving", above
```

### `remove_unused_variables` — USE WITH EXTREME CAUTION

```python
unreal.BlueprintEditorLibrary.remove_unused_variables(wbp)
```

> **WARNING:** This removes ALL variables that have NO references (no bindings, no graph nodes). This WILL delete Verse variables that haven't been bound yet. Only use this if you intentionally want to remove all unbound variables. NEVER use this as a cleanup step after creating variables — the new variables won't have bindings yet and will be immediately deleted.

---

## Renaming a Verse field

A **plain** Verse field can be renamed in place — no delete + recreate — because its public
name lives in metadata, not in the member's FName. `validate_renames(path, pairs)` is the
dry-run (name conflicts, event-field refusals); `rename_verse_fields(path, pairs)` mutates.

### Rename a plain field by rewriting ONLY its `DisplayName` value

The public Verse name of a plain field **is** its `DisplayName` metadata value — the third
metadata entry patched at create time (see [*Patching Verse Metadata*](#patching-verse-metadata)).
Rewrite just that FString value and the field is renamed. Everything else on the descriptor
is left alone.

**Verified on disk:** after the rewrite the saved `VerseClassFields` tag reads
`(Name="<new>",InternalName="<old>",...)` — `Name` flips to the new public name while
`InternalName` (the member's `VarName`) stays the old name. A `.verse` reference resolves
against the **public** name (`Name`), so the rename takes effect; the stale `InternalName` is
**cosmetic only** for plain fields. (MVVM **property** bindings do NOT resolve against the
public name — they reference the member's *internal* name + GUID, which is unchanged; see
*No binding repoint*, below.)

> **The member `VarName` FName at descriptor offset 0 is NEVER patched.** That is the
> forbidden edit — `memmove`-ing or otherwise rewriting the FName at offset 0 corrupts the
> engine's name table and crashes on the next compile/save (see
> [*Critical Warnings #1*](#1-never-create-variables-as-real-then-try-to-memory-patch-their-vartype)).
> A `DisplayName`-only rewrite sidesteps it entirely — the value FString is heap data you own,
> not an interned name index.

Reuse the metadata machinery from *Patching Verse Metadata*: rebuild the descriptor's
`MetaDataArray` block with the same keys and the new `DisplayName` string, keeping the field's
`PropertyFlags` (`65541`) unchanged.

### Event fields are REFUSED for in-place rename

An **event** field's public name is structurally tied to two things a `DisplayName` rewrite
does not touch — the member named `VerseFieldInternalVariable_<name>` and the function graph
named `<name>` (see [*Verse Event Fields*](#verse-event-fields)). Neither is safely renameable
from Python, so `validate_renames` flags any event field in the batch and
`rename_verse_fields` refuses it, telling the user to **delete + recreate** instead.

### No binding repoint is needed (verified)

A plain-field rename touches **no** bindings, because nothing a binding references changes:

- **Property bindings** reference their source field by the member's **internal name + GUID**
  (the `SourcePath` `MemberName` is the *internal* name — for a plain field the `VarName`,
  which the rename leaves untouched — plus a real `MemberGuid`). A DisplayName-only rename does
  not change either, so the binding keeps resolving. Measured: after renaming `VF_Slot1Name` →
  `VF_TitleText`, the Slot binding still reads `MemberName="VF_Slot1Name"` and compiles clean.
  **Do NOT rewrite it to the new public name — there is no member by that name, and the binding
  would break.**
- **Event bindings** reference the field by its **public** name (the `DestinationPath`
  `MemberName`) — so a rename *would* need to repoint them. But only **event** fields are
  event-bound, and event fields are refused for rename (above), so no event binding ever points
  at a renamed (plain) field. Nothing to do.

`rename_verse_fields` therefore rewrites `DisplayName` and nothing else.

### ⚠ Crash trap — do NOT detach the MetaDataArray before saving

Counter-intuitive but measured: **detaching before the save strips the rename.** The engine
serializes each field's metadata **from the array its descriptor points at**, so if you null
the `MetaDataArray` (offset 200) before `_save_regenerating_tags`, the emptied array is written
and the field keeps its **old** `DisplayName` — the rename silently doesn't take. Mirror
CREATE, which patches metadata and saves **without** detaching: rebuild the block, leave the
ctypes buffer **attached** (it lives in `_KEEP` for the session — safe on its own), compile,
and `_save_regenerating_tags` (**never** `save_asset`, which silently demotes the field — see
[*Saving*](#saving--save_asset-demotes-your-fields)).

Detach is only mandatory before a later **unload / `reload_packages` / GC** (the crash vice —
those free the attached buffer → `EXCEPTION_ACCESS_VIOLATION`; see
[*The crash vice*](#the-crash-vice--create-then-unload)). `rename_verse_fields` does none of
those, so it never detaches. **This also bites TEST scripts:** never call `reload_packages`
after a rename/create to "verify" — read the saved `.uasset` from a separate process instead.

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

> **Scope:** this section covers **property** bindings (Verse field → widget property),
> which are authored through the `unreal` API. **Event** bindings (button → Verse event
> field) are a different mechanism entirely and cannot be authored this way — see
> [*Event Bindings — the disk layer*](#event-bindings--the-disk-layer).

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

### The UEFN buttons bind `Text` — and they are NOT UMG widgets

The **Loud / Quiet / Regular** buttons (`UEFN_Button_Loud_C`, …) expose their label as a
bindable `Text`, so `message` → `Text` works on them exactly as on a `TextBlock`, with no
conversion. Easy to miss, because a button is not one of the UMG widget types:

```
UEFN_Button_{Loud,Quiet,Regular}_C
  └─ FortCTAButton      <- declares Text; lives in /Script/FortniteUI
       └─ UIKitModularButton -> CommonButtonBase -> CommonUserWidget -> UserWidget -> Widget
```

Resolve the base with `isinstance(cdo, unreal.FortCTAButton)` — the CDO's Python MRO
follows the real C++ ancestry. Two traps follow from that ancestry:

- **`FortCTAButton` is in `/Script/FortniteUI`, not `/Script/UMG`.** The `MemberParent`
  class path is *not* the usual UMG one:
  ```
  MemberParent="/Script/CoreUObject.Class'/Script/FortniteUI.FortCTAButton'",MemberName="Text"
  ```
  Hardcoding a `/Script/UMG.%s` prefix (the obvious shortcut, since every other bindable
  widget is UMG) silently produces a `MemberParent` that will not resolve.

- **A button is a `UserWidget`, so most of its properties are INHERITED.** It declares
  `Text` itself, but reflects `ColorAndOpacity`, `RenderOpacity`, `Visibility` and
  `IsEnabled` from `UUserWidget`. Naming `FortCTAButton` as the `MemberParent` of a
  property it does not declare will not resolve. The editor offers **only `Text`** on a
  button — mirror that and don't offer the rest.

The plain **Custom Button** (`UIFrameworkCustomButtonWidget`) is a different lineage
(`Button` → `ContentWidget` → `PanelWidget`), is **not** a `FortCTAButton`, and has **no
`Text`** at all — reflecting `text` off its CDO raises. It correctly has no Text target.

Verify a hand-built binding by diffing it against one made in the editor UI: for the same
field/widget/property they should be **byte-identical**, and the blueprint must compile
`BS_UP_TO_DATE`. A `MemberParent` that fails to resolve is exactly the kind of error that
still serializes into the bindings array and only surfaces at compile.

> Same trap as `EventPath.MemberParent` in
> [*`MemberParent` — the delegate's DECLARING class*](#-memberparent--the-delegates-declaring-class):
> `MemberParent` always names the class that **declares** the member, and on these buttons the
> declaring class is rarely the leaf. There it's the class declaring the *delegate*; here, the
> *property*.

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
save_regenerating_tags(wbp_path)     # NOT save_asset — see "Saving", above
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

#### A SUBCLASSED UEFN widget is both native-derived *and* a Verse-field carrier

Discovering child widgets by "is a `_C` class **and** has no native base" is **wrong**, and the
failure is silent. A widget that subclasses a UEFN one — e.g. `WC_CustomQuietButton`, a
Blueprint subclass of the Quiet Button, deriving `FortCTAButton` — satisfies *both* halves:

* it is native-derived, so a native-base test excludes it from **child** discovery, hiding its own
  Verse fields (`VF_Text`) entirely; and
* it is a user widget, so the property lister claims it and offers only the **inherited** `Text`.

Net effect: the only bindable target offered is `Text`, its Verse field is unreachable, and an
*existing* editor-made binding to that field reads back as unbound. Decide membership on **"does its
own WBP declare Verse fields?"** alone. Such a widget legitimately appears in **both** lists — the
two describe different destinations on the same instance (its Verse field vs. its native property),
and both are valid bindings.

An asset path's tail is already `Name.Name`, so deriving a display name with `rsplit("/")[-1]`
prints it twice (`WC_SlotWidget.WC_SlotWidget`); split on `.` as well.

Because such a widget yields rows from **two** sources, presentation needs care or it reads as two
unrelated widgets: label both rows with the **asset** name (the native-base label would call the
subclass "UEFN Button", indistinguishable from a stock one) and **group rows by widget**, since
appending natives-then-children puts one widget's two rows at opposite ends of the table.

**Nested-into-a-widget destinations work too** (parent field → a widget *inside* a child instance,
e.g. `Slot1`'s inner `UEFN_TextBlock_C_73.Text`): the `DestinationPath` takes the same **two-segment**
shape as a sub-widget *event* — segment 1 the inner widget on the child's generated class **with its
`VarGuid`**, segment 2 the property on its declaring class. Editor-verified: `import_text` normalizes
the second segment's `MemberParent` to the wrapped form, the blueprint compiles `BS_UP_TO_DATE`, and
a zeroed source `MemberGuid` comes back resolved.

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
save_regenerating_tags(wbp_path)     # NOT save_asset — see "Saving", above
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

## Conversion bindings — SOLVED, creatable from Python (2026-07-15)

> Earlier versions of this file said conversion-*node* bindings (`texture`/`material` →
> `Brush`) "cannot be created from Python — do them in the editor UI." **That was wrong.**
> All conversion bindings — node AND function — are now creatable, verified end-to-end
> (`BS_UP_TO_DATE`, persisted, re-read after reload). Use
> `verse_fields.create_conversion_bindings()`.

The claim rested on a false premise: that you would have to synthesize the 7-node
`EdGraph`. **You don't. The graph is never serialized.** Its node classes
(`MVVMK2Node_AreSourcesValidForBinding`, `K2Node_GeneratedBoundEvent`, `K2Node_VariableGet`)
do not appear anywhere in the saved `.uasset` — not even in the name table. **The engine
REGENERATES the graph on compile** from one small data object. Author the object; the graph
builds itself.

### The object

A conversion binding is an ordinary binding with an **empty `SourcePath`** whose
`Conversion.SourceToDestinationConversion` names an `MVVMBlueprintViewConversionFunction`
(a real UObject outered to the WidgetBlueprint — `unreal.new_object(..., outer=wbp)`).
The **real source lives on one of its `SavedPins`**, not in `SourcePath`.

All six of its properties are `[Read-Only]`, but three take `import_text` on the **live**
struct handed back by `get_editor_property` (the same door `MVVMBlueprintViewBinding.SourcePath`
uses), and `saved_pins` is a **live array that accepts `.append()`**:

| Field | How to write it |
|---|---|
| `conversion_function` | `import_text` — `Type=Function` + `FunctionReference`, or `Type=Node` + `Node=` |
| `destination_path` | `import_text` — same shape as a binding's DestinationPath |
| `saved_pins` | live array; build `unreal.MVVMBlueprintPin()`, `import_text`, `.append()` |
| **`graph_name`** | **ctypes — see below. This is the whole difficulty.** |
| `is_ubergraph_page` / `wrapper_graph_transient` | **don't** — the engine derives both from the conversion type, correctly |

### ⚠ GraphName: read-only, and the engine HARD-ASSERTS on it

Leave it `None` (what `new_object` gives you) and the engine trips

```
Ensure condition failed: !GraphName.IsNone()
MVVMBlueprintViewConversionFunction.cpp:388
```

then dereferences null on the next UI redraw — `EXCEPTION_ACCESS_VIOLATION reading 0xe0`,
**editor gone**. It is *not* an output the engine backfills; it must be valid **before
anything reads the object**. (It does get auto-filled if you compile, but by then the ensure
has already fired and the editor dies on the next repaint. Do not rely on that.)

The name is **derived from the binding's own id**, not arbitrary:

```
__<BindingId as a lowercase dashed GUID>_SourceToDest[_Async]
```

`_Async` iff the conversion is a **K2Node** (every Brush/Texture node); a library `Conv_*`
**function** gets the plain suffix. E.g. `BindingId=6EA1372D4D7C2CC70997C68B58D5C6EE` →
`__6ea1372d-4d7c-2cc7-0997-c68b58d5c6ee_SourceToDest_Async`.

Writing it needs a **real FName index** — you cannot invent one, it must exist in the
engine's global name table. Get the engine to allocate it: `MVVMBlueprintPin.import_text()`
interns any string, and the index is then readable out of that pin's `PinNames` TArray
(ptr/count/max at struct offset 0). Then ctypes it into the object at **offsets 256 and 264**
(GraphName + a cached copy; write both). Verify by reading `graph_name` back through the
reflection API — it will read your string.

```python
def _intern_fname(text):                      # -> (index, number)
    pin = unreal.MVVMBlueprintPin()
    pin.import_text('(Id=(PinNames=("%s")),DefaultString="",Status=Valid)' % text)
    ptr, count, _ = struct.unpack_from("<qii", bytes(
        (ctypes.c_ubyte * 16).from_address(_obj_address(pin))), 0)
    return struct.unpack_from("<II", bytes((ctypes.c_ubyte * 8).from_address(ptr)), 0)
```

`_obj_address` just parses the `(0x…)` that UObject/struct `repr()` already prints.
Offsets 256/264 are **build-specific** — re-derive them (diff a fresh object against a real
one) if a UEFN update breaks the read-back assert.

### The four conversion kinds

| Kind | `conversion_function` | Pins (in order) |
|---|---|---|
| Make Brush From Soft **Material** | `Type=Node`, `MVVMK2Node_MakeBrushFromSoftMaterial` | `Material`\*, `Width`, `Height` |
| Make Brush From Soft **Texture** | `Type=Node`, `MVVMK2Node_MakeBrushFromSoftTexture` | `Texture`\*, `Width`, `Height` |
| **Set Soft Texture Parameter** | `Type=Node`, `MVVMK2Node_SetSoftTextureParameter` | `TargetBrush`†, `ParameterName`, `Texture`\* |
| **To Visibility (Boolean)** | `Type=Function`, `VerseFortniteUIAllowedConversionLibrary.Conv_BoolToSlateVisibility` | `bIsVisible`\*, `TrueVisibility`, `FalseVisibility` |

\* the source field's pin. † see below.

**Every non-source pin takes EITHER a literal (`DefaultString`) OR a Verse field (a `Path`)** —
the exact same structure as the source pin. So `Width`/`Height` can be bound to `int` fields,
`ParameterName` to a string field, `TrueVisibility`/`FalseVisibility` to fields, and so on.
That is what the chain-link icon beside each row in the panel does.

`TrueVisibility`/`FalseVisibility` accept: `Visible`, `Collapsed`, `Hidden`,
`HitTestInvisible`, `SelfHitTestInvisible`.

**† `TargetBrush` is NOT engine-filled, despite being greyed out in the panel.** It is an
explicitly serialized `Path` that is a **verbatim copy of the `destination_path`**
(`Image3.Brush`, `Source=Widget`). Omit it and you get a binding that compiles clean
(`BS_UP_TO_DATE`) and **silently does nothing**. `ParameterName` must also match a texture
parameter that actually exists **in the material** — a typo fails at runtime, not at compile.

### Other facts

* The source pin's `MemberGuid` can be **zeroed** — the compiler resolves it by name and
  writes the real GUID back, exactly as for ordinary bindings.
* `get_available_conversion_functions(texture, Brush)` returns `[]` while `(logic, Visibility)`
  returns 10. That is real, but it only means Brush conversions aren't library *functions* —
  it never implied they were uncreatable.
* `MVVMEditorSubsystem` has `get_conversion_function{,_node,_graph}` and **no setter**. Also
  real, and also not a wall: you never go through the subsystem, you author the object.
* A binding whose destination is already taken must be **replaced**, not appended — one
  destination holds at most one binding.

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
save_regenerating_tags(wbp_path)     # NOT save_asset — see "Saving", above
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

## Event Bindings — the disk layer

A **property** binding (Verse field → widget property) is authored through the `unreal` API,
above. An **event** binding (a button's `OnClicked` → a Verse event field) cannot be:
generation runs entirely in the editor's internal C++ `FMVVMBlueprintViewEvent` path, which
populates a compiled `MVVMViewClass` table that has **no Python binding at all**. Proven
exhaustively: a Python-authored event with **byte-identical** `EventPath`/`DestinationPath`
to a working UI-made one still gets `event_key=(Index=-1)` and
`[Compiler] Event '...': The event could not be generated.`

**But the generated result is just serialized data in the `.uasset`.** So you don't
generate — you patch the file on disk. Pure Python, no .NET, no UAssetGUI, no `.usmap`.
Create, retarget, and remove all work and are editor-verified (`BS_UP_TO_DATE`, `VALID`).

### Package format (UEFN = UE 5.8)

- `FileVersionUE4=522`, `FileVersionUE5=1018`, `LegacyFileVersion=-9`.
- Header order: tag, legacy, UE3, UE4ver, UE5ver, licensee, **SavedHash (20 bytes)**,
  **SectionSixOffset**, then custom versions, FolderName… (SavedHash replaced PackageGuid;
  TotalHeaderSize folded into SectionSixOffset). SavedHash is **not verified** on load.
- Export table stride = **112 bytes**. `FObjectImport` is a **fixed 40-byte** record with
  ObjectName at **+20** — do *not* "derive" the stride by testing whether name indices look
  plausible; a smaller stride also passes and silently reads half the table.
- Package indices: imports are negative — the Nth import is `-(N+1)`. **Import indices are
  not stable** (adding a name or import renumbers them). Never hardcode; resolve by class
  name every time.
- **The property stream is NOT int32-aligned.** Variable-length tag data puts FNames at
  arbitrary byte offsets, so a strided scan cannot find them. Locate the *tag*, then derive:
  a **NameProperty's value sits exactly 25 bytes past its name tag**.
- All event values (widget, delegate, Verse field, graph name) are FNames = 8 bytes, so
  **retargeting never changes file length** — no offsets, sizes or hashes need fixing.

### Growing the package (creating an event) — everything you must maintain

Cloning an event export means maintaining every derived structure. Each of these was found
via a distinct engine failure:

1. **Summary table offsets** — all of import/export/depends/cell_export/cell_import/metadata/
   soft_package_refs/searchable_names/thumbnail_table/import_type_hierarchies/asset_registry/
   preload_dependency, plus SectionSix (int32) and BulkDataStart + PayloadToc (int64).
   Zero means *absent* — never shift a zero.
2. **`AssetRegistryDependencyDataOffset`** — an int64 **inside** the registry blob (its first
   8 bytes). Miss → *"Package is unloadable. Reason: SerializeAssetRegistryDependencyData"*.
3. **Thumbnail table per-entry data offsets** — each entry is (FString class, FString path,
   int32 absolute offset). Miss → *"Requested read of 33554432 bytes"* at a fixed old address.
4. **Depends table** — one entry per export; a new export needs an appended `0`.
5. **`FGenerationInfo`** — must bump with export_count / name_count.
6. **ArrayProperty tag Size** when growing the `Events` array — sits at `count_pos − 5`
   (count at `tag + 37`); must equal `4 + count × 4`. Miss → the loader reads one element short.
7. Export `SerialSize`/`SerialOffset`; export bodies must tile exactly to BulkDataStart.
8. **Reparse between successive shifts** — parsed Export objects go stale after a shift; a
   second shift comparing stale offsets corrupts the file.
9. `"Events"` occurs as an FName in **both** `MVVMBlueprintView` (editable) and
   `MVVMViewClass` (compiled). Name the owner explicitly; never scan.

### ⚠ `MemberParent` — the delegate's DECLARING class

The one that costs hours. `EventPath`'s **`MemberParent`** is an ObjectProperty holding the
package index of the class that **declares the delegate** — and that differs *per delegate,
not per widget*:

- A UEFN button (`UEFN_Button_Quiet/Regular/Loud`, Blueprints under `/Game/Valkyrie/UMG/`)
  **inherits** `OnButtonBaseClicked` from **`CommonButtonBase`**, but **declares**
  `OnButtonCTAHighlight`/`Unhighlight` on **itself**.
- The Custom Button (`/Script/UIFramework.UIFrameworkCustomButtonWidget`, a **native** class)
  declares its own `OnButtonClicked` / `OnButtonHighlight` / `OnButtonUnhighlight`.

A clone keeps its template's `MemberParent`, so a UEFN-button template retargeted onto a
Custom Button tells the engine to look for `OnButtonClicked` on `CommonButtonBase` → not
found → `<None>`:

```
[Compiler] Event 'Custom Button.<None> => Self.VF Event Field()': The event could not be generated.
```

- The value sits at **`MemberParent` tag + 25** (same `_NAME_VALUE_GAP` as an FName's).
- The **first** `MemberParent` is EventPath's; the **second** is DestinationPath's — do not touch it.
- **The two button shapes spell the same event differently.** Pairing the wrong spelling with
  a widget silently writes a broken event whose only symptom is a log-only warning — the
  compile still reports `BS_UP_TO_DATE`. **Validate the `(widget, delegate)` pair against what
  the widget actually declares, and refuse before writing anything.**

| Panel label | UEFN button | Custom Button |
|---|---|---|
| On Clicked | `OnButtonBaseClicked` | `OnButtonClicked` |
| On Hovered | `OnButtonBaseHovered` | `OnHovered` |
| On Highlight | `OnButtonCTAHighlight` | `OnButtonHighlight` |

### Which widgets can source an event — gate on the DELEGATE, never on a class list

**Only buttons can.** An Image or a TextBlock declares no delegate — and offering one a
delegate is **not a cosmetic bug: binding it crashes the editor outright**
(`EXCEPTION_ACCESS_VIOLATION reading 0xffffffffffffffff` — the engine dereferences a delegate
the class does not have).

Keep a widget only if its **CDO really declares** the delegate. Delegates are reflected as
snake_case on the CDO (`on_button_base_clicked`); a **real** delegate reads back as a delegate
object, while an unbound `UWidget` python method reads back as
`builtin_function_or_method_with_closure` — that is the test.

MVVM offers exactly **seven** events (Clicked, Hovered, Selected, Unhovered, Unselected,
Highlight, Unhighlight) even though the class declares ~19 (Double Clicked, Focused, Lock
Clicked, drag/drop…). Discovery alone over-lists — intersect an allowlist with what the class
declares. The Custom Button genuinely has no Selected/Unselected.

### Seeding the first event — no UI-made template needed

Cloning needs a template, so this used to require the user to hand-author the first event
binding in the MVVM panel. **That limitation is gone** — the engine will build the seed for you:

1. **`MVVMEditorSubsystem.add_event(wbp)`** appends a real `MVVMBlueprintViewEvent` **export**
   *and* registers it in the view's `Events` array. This is the part that cannot be hand-rolled
   cheaply: a virgin `MVVMBlueprintView` **omits the `Events` property entirely** (UE never
   serializes a property at its default), so there is no array for the byte patcher to grow.
   (`add_event` cannot *generate* an event — but it does persist and register the shell, which
   is all the patcher needs.)
2. **`import_text` on `event_path` / `destination_path`.** Both are `[Read-Only]` and
   `set_editor_property` is refused — but **`get_editor_property` on a UObject hands back the
   LIVE struct**, so `import_text` mutates the event in place and the values *do* reach disk.
   (Same door as `MVVMBlueprintViewBinding.SourcePath`.) Point them at placeholders; the
   patcher retargets every FName before the engine ever reads the file.
3. **`graph_name` is `[Read-Only]` with no such door.** It stays `None`, and an unset FName is
   **not serialized at all**. Inject the tag on disk — exactly **33 bytes**:
   `[FName "GraphName"][FName "NameProperty"][int32 ArrayIndex=0][int32 size=8][byte flags=0][FName value]`.
   - **Where:** the event's property stream ends with an FName `None` followed by a **4-byte
     trailer**. Write the tag *at* that terminator (so `None` still terminates). Locate it via
     `n + 8 + 4 == end`. Anchor on the terminator, **not** on `EventKey` — the shell has no
     `EventKey` tag either (also default/unset).

The seed carries placeholder names, so **retarget it in place** into the first requested
binding rather than cloning-then-deleting it — deleting an export would mean shrinking the
export table *and* the Events array, a whole class of surgery avoided for nothing.

**`add_event` and the declaring-class lookup must both run BEFORE the unload** — they load the
asset, and doing that afterwards re-pins the file the patcher is about to write.

### ⚠ The file lock: never load the asset after the unload

`pkg.save()` → `PermissionError`. The file is **not** read-only (`os.access` says writable) —
the engine holds an **exclusive handle** whenever the package is loaded, so the only meaningful
test is trying to `open(path, "r+b")`. Anything that loads the asset *after* the unload re-pins
it and breaks the patch. **Resolve everything the patcher needs before unloading.**

Beware: a failed patch leaves the package dirty **and** the file locked, so the *next* attempt
fails too and it looks like a different bug.

### Two snapshots, and don't confuse them

Because seeding *mutates the asset*, a disk patcher needs **two** copies:

- **`backup`** — taken **before** the seed. The rollback target, so a failure undoes the seed
  too rather than stranding a broken placeholder event.
- **`working`** — taken **after** the seed. This is what gets parsed and patched, so the patcher
  sees what the seed produced.

Parsing the pre-seed `backup` fails with a bare `StopIteration` — the seed export simply is not
in that file.

### Offline verification

**A pristine engine file round-trips byte-identical** through a correct parser. So: patch a file,
re-parse it, re-serialize it, and require byte-identity. This catches whole classes of
table-maintenance bugs (it caught the thumbnail-offset and depends-table bugs above as a 3-byte
diff). Run it from a **separate process** — no editor needed, and it cannot crash one.

### Sub-widget button events — the two-segment EventPath

A parent Verse event field can bind a button nested **one level down** inside an embedded
sub-widget instance (e.g. the button inside each `Slot1…Slot5`). The parent's widget tree only
lists the *instances* — the inner button is **not** addressable by a bare `WidgetName`, so a
single-segment path fails (`Index=-1`). The editor encodes it as a **two-segment `Paths` array**:

```
Paths=(
  (BindingReference=(MemberParent="/Script/UMG.WidgetBlueprintGeneratedClass'/Proj/WC_Slot.WC_Slot_C'",
                     MemberName="ButtonQuiet", MemberGuid=6C0C0E39…), BindingKind=Property),   # segment 1: the inner button
  (BindingReference=(MemberParent="/Script/CoreUObject.Class'/Script/CommonUI.CommonButtonBase'",
                     MemberName="OnButtonBaseClicked"), BindingKind=Property)                  # segment 2: the delegate
),
WidgetName="Slot1"                                                                              # the INSTANCE in the parent tree
```

The moving parts, all editor-verified (real `event_key`, survives fresh reload + recompile):

- **`WidgetName`** = the sub-widget **instance** name (`Slot1`), same as a sub-widget *field* binding.
- **Segment 1** = `MemberParent` the child's **generated class** (`WidgetBlueprintGeneratedClass'…_C'`),
  `MemberName` the button's own **variable name inside the child**, and `MemberGuid` the button's
  **`VarGuid`**. That GUID is **REQUIRED** — a zeroed one fails to generate (unlike a *destination*
  field GUID, which the compiler re-resolves by name). Read it offline from the child WBP's
  **`WidgetVariableNameToGuidMap`** in its T3D export — no mutation needed.
- **Segment 2** = the delegate, with `MemberParent` its **declaring class** (`CommonButtonBase` for
  the UEFN buttons' `OnButtonBaseClicked`; **`FortCTAButton`** for `OnButtonCTAHighlight`/`Unhighlight`;
  the **Custom Button** for its own `OnButtonClicked`/`OnButtonHighlight`/`OnButtonUnhighlight`).

**Don't hand-build the nested path in raw bytes.** A flat single-segment template cannot be
FName-retargeted into it (the extra segment carries more bytes *and* a GUID). Instead **seed each
sub-widget event through the engine** — `add_event` + `import_text` with the full two-segment
`event_path` — which serializes the nested structure correctly, then finish `GraphName` (and any
param pin) on disk exactly as for a flat event. Discover candidates (with their button `VarGuid`)
via `list_sub_widget_event_buttons(path)`.

Parameterised events work unchanged (the Param 0 value rides in `SavedPins` as always; verified
sticking on a sub-widget button).

#### Reading one back: the delegate is NOT `MemberName[0]`

How many `MemberName` entries an event export carries depends on the button, and the **Verse field
is always last**:

| button | `MemberName` entries | count |
|---|---|---|
| flat | `delegate`, `field` | 2 |
| sub-widget | **`button_var`**, `delegate`, `field` | 3 |

So the delegate is **`members[-2]`**, never `members[0]`. Reading slot 0 as the delegate yields the
inner button's *variable name* (`CustomButtonQuiet`) on every sub-widget event — which silently
breaks read-back (the UI matches on `(widget, delegate)`, finds nothing, and shows the binding as
unbound even though it was created correctly) and **corrupts a retarget**, which would write the
delegate name over the button segment. `MemberParent` is written once per segment too, so pair it
with the delegate positionally, not at index 0.

When a sub-widget holds **more than one button**, the instance name alone is ambiguous — and so is
the class (a slot can hold two buttons of the *same* class). Key on the **button variable name**
(`(WidgetName, button_var, delegate)`); that is the only part guaranteed unique within an instance.
Keying on the instance alone makes one button's binding light up all of its siblings' rows and makes
an unbind remove their events too.

A clone template must also match the donor's **shape**: a flat button cannot clone a sub-widget event
(a retarget only rewrites widget/delegate/field, leaving the extra segment stale), so a widget whose
only events are sub-widget ones still needs a fresh seed.

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

NewVariables TArray (at UObject + NEWVARS_OFFSET — PROBE IT, build-specific):
┌─────────────────────────────────────┐
│ ptr to data (8 bytes)               │
│ count (4 bytes)                     │
│ capacity (4 bytes)                  │
└─────────────────────────────────────┘
```

`PropertyFlags` (offset 176): **65541** = plain Verse field, **65557** = Verse *event* field.
A demoted field keeps its flags but has **0** metadata entries — a healthy event field has **6**.

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

### 13. NEVER finish a create with `save_asset` — it demotes the field

It does not regenerate the `VerseClassFields` tag, so the field is written out as a plain
Blueprint variable and vanishes on the next read from disk. Use `save_regenerating_tags`
(`pkg.modify()` + `EditorLoadingAndSavingUtils.save_packages`). See [*Saving*](#saving--save_asset-demotes-your-fields).

### 14. NEVER delete `BackPointer`

It is **shared by every Verse event field** on the widget. Deleting it breaks *all* of them
(`BS_ERROR`: "This blueprint (self) is not a VerseUIUserWidget, therefore ' Target ' must have
a connection"), and **recreating the variable does not repair it** — the graphs' connections
are severed, not the variable. The only fix is restoring the `.uasset` from a backup. It sits
in the member list looking like a leftover; it is not.

### 15. NEVER let an event field persist its function graph

`add_function_graph` creates a genuine *saved* BP function, which UEFN's
`ValkyrieValidator_Blueprints` rejects as **restricted content**. Create the graph, let the
metadata patch land, then `remove_function_graph` **before the final save**.

### 16. NEVER unload/reload/GC after a create without detaching first

Instant `EXCEPTION_ACCESS_VIOLATION`. And detaching then saving silently demotes the field.
Both jaws are real — see [*The crash vice*](#the-crash-vice--create-then-unload). This applies
to your throwaway probe scripts too, not just to the tool.

### 17. NEVER offer a delegate to a widget that doesn't declare it

Binding an event to an Image or TextBlock **crashes the editor outright**
(`EXCEPTION_ACCESS_VIOLATION reading 0xffffffffffffffff`). And pairing a button with the *other*
button shape's spelling of an event (`OnButtonCTAHighlight` vs `OnButtonHighlight`) silently
writes a broken binding — the compile still says `BS_UP_TO_DATE`, with only a log-line warning.
Gate on what the CDO actually declares, and validate the pair before writing.

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
save_regenerating_tags(wbp_path)     # NOT save_asset — see "Saving", above

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
save_regenerating_tags(wbp_path)     # NOT save_asset — see "Saving", above
```
