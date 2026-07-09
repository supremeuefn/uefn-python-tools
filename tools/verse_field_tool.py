"""Verse Fields + Binding Tool — create, manage, and bulk-bind Verse fields via MVVM.

Standalone, single-file tool: needs only the editor's `unreal` module, the Python
stdlib, and PySide6 (auto-installed on first run — restart UEFN once after).
Run inside UEFN via:
    Tools > Execute Python Script...  ->  verse_field_tool.py
or from the UEFN Python console:
    exec(open(r"<path to>/verse_field_tool.py").read())

Lists every Verse field on a Widget Blueprint and binds it in one of two modes,
chosen by the "Bind to" toggle:

  ENGINE PROPERTIES   field -> a widget property (Text, ColorAndOpacity, …).
  SUB-WIDGET FIELDS   field -> a Verse field on an EMBEDDED child widget instance
                      (VF_Slot1Image -> Slot1.VF_SlotImage). Same-type only; the
                      child's own bindable fields are read from its VerseClassFields.

Three ways to bind, in either mode:
  * Bind Selected        — one field to every selected target (fan-out).
  * Bind Selected Pairs  — multi-select fields AND targets, zipped 1:1 in row order.
  * Bulk by number       — pattern-match with `#` as the index placeholder:
                           VF_Color#  -> Image#.ColorAndOpacity, or
                           VF_Slot#Image -> Slot#.VF_SlotImage — all indices at once.

WHAT IT CANNOT DO
    Bindings needing a conversion NODE can't be authored (MVVMEditorSubsystem has
    getters but no setters for them). So `texture`/`material`->Brush must be done in
    the editor UI — the tool lists Brush LOCKED rather than omitting it. Note this
    only affects binding to an engine Brush property; binding a parent `texture`
    field to a CHILD `texture` field needs no conversion and works (the child owns
    the Brush conversion internally).

HARD-WON DETAILS (every one cost a crash or a corrupt asset to learn)
    * MVVMBlueprintViewBinding.SourcePath is read-only; import_text() on the whole
      struct bypasses the per-property guard. That is the only way to author one.
    * Bindings pulled from the array are struct COPIES — the whole array must be
      written back. This is also why remove_binding() silently no-ops.
    * MemberParent for an ENGINE prop names the NATIVE class declaring it, not the
      widget's own class. For a CHILD field it names the child's GENERATED class
      (wrapped: /Script/UMG.WidgetBlueprintGeneratedClass'…_C'). Props inherited
      from UWidget omit MemberParent and set bSelfContext=True.
    * A child binding zeroes its source MemberGuid — the compiler re-resolves it by
      name, so we never need the parent's protected NewVariables GUID.
    * UEFN exposes a reduced widget set (no ProgressBar/Button/Border); Text arrives
      via UEFN_TextBlock. ToolTipText is hidden — it type-matches message/string but
      is not a visual bind target.
    * Verse color structs bind DIRECTLY to SlateColor/LinearColor — no conversion.
"""

import sys


# ═══════════════════════════════════════════════════════════════════════════
#  DEPENDENCY BOOTSTRAP
# ═══════════════════════════════════════════════════════════════════════════

def _find_ue_python():
    import os
    candidate = os.path.join(sys.prefix, "python.exe")
    if os.path.isfile(candidate):
        return candidate
    for entry in sys.path:
        candidate = os.path.join(entry, "python.exe")
        if os.path.isfile(candidate):
            return candidate
    return None


def _ensure_deps():
    import subprocess
    import unreal

    try:
        from PySide6 import QtWidgets  # noqa: F401  (probe the submodule, not a stale cache)
        return
    except (ImportError, AttributeError):
        pass

    # A half-imported PySide6 poisons the cache; clear it so the fresh install loads.
    for key in list(sys.modules):
        if key == "PySide6" or key.startswith("PySide6.") \
                or key == "shiboken6" or key.startswith("shiboken6."):
            del sys.modules[key]

    unreal.log_warning("[VerseBinder] PySide6 missing. Installing...")
    python = _find_ue_python()
    if python is None:
        unreal.log_error("[VerseBinder] Cannot find UE python.exe")
        raise SystemExit("[VerseBinder] Cannot find UE python.exe")

    try:
        try:
            subprocess.check_call([python, "-m", "ensurepip"])
        except Exception:
            pass
        subprocess.check_call([python, "-m", "pip", "install", "PySide6"])
    except Exception as exc:
        unreal.log_error("[VerseBinder] Install failed: %s" % exc)
        raise SystemExit("[VerseBinder] Install failed: %s" % exc)

    unreal.log("[VerseBinder] PySide6 installed — RESTART UEFN, then run again.")
    raise SystemExit("[VerseBinder] Restart UEFN to finish setup.")


_needs_restart = False
try:
    _ensure_deps()
except SystemExit:
    _needs_restart = True


if not _needs_restart:
    import ctypes
    import os
    import re
    import struct
    import uuid

    import unreal
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QPushButton, QTableWidget, QTableWidgetItem, QLabel, QLineEdit,
        QHeaderView, QTextEdit, QSplitter, QComboBox, QAbstractItemView,
        QGroupBox, QTabWidget, QSpinBox, QMessageBox, QAbstractSpinBox,
    )
    from PySide6.QtCore import Qt, QPointF
    from PySide6.QtGui import QColor, QPainter, QPen

    # ═══════════════════════════════════════════════════════════════════════
    #  STYLE  (UE5 Slate palette)
    # ═══════════════════════════════════════════════════════════════════════

    C_INPUT, C_RECESS, C_PANEL = "#0f0f0f", "#1a1a1a", "#242424"
    C_HEADER, C_BTN, C_BTNOUT = "#2f2f2f", "#383838", "#4c4c4c"
    C_HOVER, C_HOVER2 = "#575757", "#808080"
    C_TX0, C_TX2 = "#c0c0c0", "#808080"
    C_ACC, C_AH, C_SELI = "#0070e0", "#0e86ff", "#40576f"
    C_OK, C_ERR, C_WARN = "#1fe44b", "#ef3535", "#ffb800"


    STYLE = f"""
    QMainWindow {{ background-color: {C_PANEL}; }}
    QWidget {{
        background-color: {C_PANEL}; color: {C_TX0};
        font-family: 'Segoe UI', sans-serif; font-size: 12px; border: none;
    }}
    QTableWidget {{
        background-color: {C_RECESS}; alternate-background-color: {C_PANEL};
        gridline-color: {C_BTN}; border: 1px solid {C_BTN};
        selection-background-color: {C_SELI}; selection-color: #ffffff; outline: none;
    }}
    QTableWidget::item {{ padding: 4px 6px; border: none; }}
    QTableWidget::item:selected {{ background-color: {C_SELI}; }}
    QHeaderView::section {{
        background-color: {C_HEADER}; color: {C_TX2}; padding: 5px 8px;
        border: none; border-right: 1px solid {C_BTN}; border-bottom: 1px solid {C_BTN};
        font-weight: 600; font-size: 10px; text-transform: uppercase; letter-spacing: 0.5px;
    }}
    QHeaderView {{ background-color: {C_HEADER}; }}
    QPushButton {{
        background-color: {C_BTN}; color: {C_TX0}; border: 1px solid {C_BTNOUT};
        border-radius: 2px; padding: 4px 12px; min-width: 54px; font-size: 11px;
    }}
    QPushButton:hover {{ background-color: {C_HOVER}; border-color: {C_HOVER2}; color: #fff; }}
    QPushButton:pressed {{ background-color: {C_HEADER}; }}
    QPushButton:disabled {{ background-color: {C_HEADER}; color: {C_TX2}; border-color: {C_BTN}; }}
    QPushButton#btn_primary {{
        background-color: {C_ACC}; border: 1px solid {C_AH}; color: #fff; font-weight: 600;
    }}
    QPushButton#btn_primary:hover {{ background-color: {C_AH}; }}
    QPushButton#btn_primary:disabled {{ background-color: {C_HEADER}; color: {C_TX2}; border-color: {C_BTN}; }}
    QPushButton#btn_action {{
        background-color: {C_ACC}; border: 1px solid {C_AH}; color: #fff;
        font-weight: 700; font-size: 13px; border-radius: 0px;
    }}
    QPushButton#btn_action:hover {{ background-color: {C_AH}; }}
    QPushButton#btn_action:disabled {{ background-color: {C_HEADER}; color: {C_TX2}; border-color: {C_BTN}; }}
    QLineEdit, QComboBox, QSpinBox {{
        background-color: {C_INPUT}; border: 1px solid {C_BTN}; padding: 5px 10px;
        border-radius: 4px; color: {C_TX0}; font-size: 11px;
        selection-background-color: {C_SELI};
    }}
    QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {C_ACC}; }}
    /* Native spin arrows are hidden — we use a horizontal [-] value [+] stepper. */
    QSpinBox {{ padding: 5px 8px; }}
    QSpinBox::up-button, QSpinBox::down-button {{ width: 0; border: none; }}
    QPushButton#stepper {{
        min-width: 26px; max-width: 26px; padding: 4px 0; font-size: 15px;
        font-weight: 700; border-radius: 4px;
    }}
    QTabWidget::pane {{ border: none; background-color: {C_PANEL}; }}
    QTabBar {{ background-color: {C_HEADER}; border: none; }}
    QTabBar::tab {{
        background-color: {C_HEADER}; color: {C_TX2};
        border: 1px solid {C_BTNOUT}; border-bottom: 2px solid transparent;
        border-top-left-radius: 4px; border-top-right-radius: 4px;
        padding: 7px 18px; margin-right: 3px;
        font-size: 11px; font-weight: 500; min-width: 90px;
    }}
    QTabBar::tab:hover {{ color: {C_TX0}; background-color: {C_BTN}; }}
    QTabBar::tab:selected {{
        color: #fff; background-color: {C_BTN};
        border: 1px solid {C_BTNOUT}; border-bottom: 2px solid {C_ACC};
        font-weight: 600;
    }}
    /* ComboBox drop-down button. The arrow itself is painted in code (ArrowCombo)
       because this Qt build renders NEITHER CSS-triangle NOR image arrows on the
       down-arrow subcontrol. Hide the native arrow so ours is the only one. */
    QComboBox {{ padding-right: 28px; }}
    QComboBox::drop-down {{
        subcontrol-origin: border; subcontrol-position: center right;
        width: 24px; background-color: {C_BTN};
        border-left: 1px solid {C_BTNOUT};
        border-top-right-radius: 4px; border-bottom-right-radius: 4px;
    }}
    QComboBox::drop-down:hover {{ background-color: {C_HOVER}; }}
    QComboBox::down-arrow {{ width: 0; height: 0; }}
    QComboBox QAbstractItemView {{
        background-color: {C_HEADER}; color: {C_TX0}; padding: 3px;
        selection-background-color: {C_SELI}; border: 1px solid {C_BTNOUT};
        outline: none;
    }}
    QTextEdit {{
        background-color: {C_INPUT}; border: 1px solid {C_BTN}; color: {C_TX0};
        font-family: 'Cascadia Code', 'Consolas', monospace; font-size: 11px;
        padding: 4px 6px; selection-background-color: {C_SELI};
    }}
    QGroupBox {{
        color: {C_TX0}; font-weight: 700; font-size: 12px;
        border: 1px solid {C_BTN}; border-radius: 6px;
        margin-top: 14px; padding-top: 16px;
    }}
    QGroupBox::title {{
        subcontrol-origin: margin; subcontrol-position: top left;
        left: 12px; top: 1px; padding: 0 6px;
        color: {C_TX0}; font-weight: 700;
        text-transform: uppercase; letter-spacing: 0.5px;
    }}
    QSplitter::handle {{ background: {C_BTN}; height: 2px; }}
    QScrollBar:vertical {{ background: {C_RECESS}; width: 12px; border: none; }}
    QScrollBar::handle:vertical {{
        background: {C_BTNOUT}; min-height: 24px; border-radius: 3px; margin: 2px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {C_HOVER}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: none; }}
    QScrollBar:horizontal {{ background: {C_RECESS}; height: 12px; border: none; }}
    QScrollBar::handle:horizontal {{
        background: {C_BTNOUT}; min-width: 24px; border-radius: 3px; margin: 2px;
    }}
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
    QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: none; }}
    """

    class ArrowCombo(QComboBox):
        """QComboBox that PAINTS its own down-chevron.

        This Qt build renders neither CSS-triangle nor image arrows on the
        down-arrow subcontrol, leaving an empty drop-down button. Painting the
        chevron in paintEvent is build-independent and always shows.
        """
        def paintEvent(self, event):
            super().paintEvent(event)
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor(C_TX0))
            pen.setWidthF(1.6)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            # center the chevron inside the 24px drop-down button on the right
            cx = self.width() - 12
            cy = self.height() / 2
            p.drawPolyline([QPointF(cx - 4, cy - 2),
                            QPointF(cx, cy + 2),
                            QPointF(cx + 4, cy - 2)])
            p.end()

    # ═══════════════════════════════════════════════════════════════════════
    #  LOGIC  (no UI — every call verified against a live widget)
    # ═══════════════════════════════════════════════════════════════════════

    _el = unreal.EditorAssetLibrary
    _bel = unreal.BlueprintEditorLibrary

    # Verse field Type (from the VerseClassFields asset tag) -> friendly name.
    _VERSE_TYPE = {
        "Boolean": "logic", "Integer": "int", "Floating": "float",
        "String": "string", "Message": "message",
        "Asset": "asset",       # refined to material/texture via TypeUE5Class
        "Structure": "struct",  # refined to color/color_alpha via TypeUE5Class
    }

    # (verse_type, widget_property_type) pairs that bind with NO conversion.
    # Verified empirically; anything absent needs a conversion we cannot author.
    #
    # NOTE: int -> float is NOT here. The MVVM view *stores* an int->RenderOpacity
    # binding without objecting and it survives reload, but it FAILS at compile:
    #   "source 'VF_IntVar' (int64) does not match destination 'RenderOpacity'
    #    (float). A conversion function is required."
    # int64 -> double needs a Conv_* function (which we can't author from Python),
    # so it must go through the editor UI — never offer it as a direct target.
    _DIRECT = {
        ("message", "Text"), ("string", "Text"),
        ("float", "float"), ("int", "int"), ("logic", "bool"),
        ("color", "SlateColor"), ("color", "LinearColor"),
        ("color_alpha", "SlateColor"), ("color_alpha", "LinearColor"),
    }

    _CLASS_PATH = "/Script/CoreUObject.Class'/Script/UMG.%s'"

    # The native class that DECLARES each property, for MemberParent.
    _DECLARING_CLASS = {
        "Text": "TextBlock", "ShadowColorAndOpacity": "TextBlock",
        "Brush": "Image",
        # ColorAndOpacity is declared on both TextBlock (SlateColor) and Image
        # (LinearColor) — resolved per widget in _declaring_class().
    }

    # Inherited from UWidget: MemberParent is omitted, bSelfContext=True instead.
    _UWIDGET_INHERITED = {"RenderOpacity", "Visibility", "IsEnabled"}

    # Some props expose a FRIENDLY name to Python reflection (IsEnabled) but the MVVM
    # binding serializes the underlying UPROPERTY name (bIsEnabled). The editor
    # silently rewrites IsEnabled -> bIsEnabled on save, which breaks occupancy
    # matching and spawns duplicates. Author (and match) the serialized name directly.
    _SERIALIZED_PROP = {"IsEnabled": "bIsEnabled"}
    _FRIENDLY_PROP = {v: k for k, v in _SERIALIZED_PROP.items()}

    def _serialized_prop(prop_name):
        return _SERIALIZED_PROP.get(prop_name, prop_name)

    def _friendly_prop(prop_name):
        return _FRIENDLY_PROP.get(prop_name, prop_name)

    # UEFN exposes a REDUCED widget set — ProgressBar/Button/Border from vanilla
    # UMG are hidden in the UEFN designer, so we don't offer them. Text comes
    # through UEFN_TextBlock (a Blueprint over TextBlock; _native_base resolves it).
    _NATIVE_WIDGETS = ("TextBlock", "Image")

    # ToolTipText is intentionally omitted — it type-matches `message`/`string`
    # (value type Text) and would clutter the target list with a non-visual prop.
    _BINDABLE_PROPS = (
        "Text", "ColorAndOpacity", "Brush", "RenderOpacity", "Visibility",
        "IsEnabled", "ShadowColorAndOpacity",
    )

    # Props that CANNOT be authored from Python (they need a conversion NODE), mapped
    # to the Verse field types that could legitimately target them. They appear in the
    # target list LOCKED — a signpost to do them in the editor UI — but ONLY for a
    # compatible field type, so a `message` field never sees a (locked) Brush.
    _CONVERSION_PROPS = {
        "Brush": {"texture", "material"},
    }

    _native_base_cache = {}

    def _snake(name):
        return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()

    def _native_base(class_path):
        """Native UMG class a widget derives from, or None if not a bindable widget.

        Resolved via isinstance against the CDO — Python's MRO follows the real C++
        ancestry (UEFNTextBlockBase -> TextBlock -> Widget). Matching on the class
        NAME would also accept EdGraph, K2Node_*, and everything else in the T3D.
        """
        if class_path in _native_base_cache:
            return _native_base_cache[class_path]
        result = None
        try:
            cls = unreal.load_object(None, class_path)
            cdo = unreal.get_default_object(cls) if cls else None
            if cdo is not None:
                for native in _NATIVE_WIDGETS:
                    native_cls = getattr(unreal, native, None)
                    if native_cls is not None and isinstance(cdo, native_cls):
                        result = native
                        break
        except Exception:
            result = None
        _native_base_cache[class_path] = result
        return result

    def _declaring_class(native, prop_name):
        """Class to name in MemberParent, or None if inherited from UWidget."""
        if prop_name in _UWIDGET_INHERITED:
            return None
        if prop_name == "ColorAndOpacity":
            return native
        return _DECLARING_CLASS.get(prop_name, native)

    def widget_bindable_properties(native):
        """Bindable properties on a native widget class -> {name: value_type}."""
        cls = getattr(unreal, native, None) if native else None
        if cls is None:
            return {}
        try:
            cdo = unreal.get_default_object(cls)
        except Exception:
            return {}
        found = {}
        for prop in _BINDABLE_PROPS:
            try:
                value = cdo.get_editor_property(_snake(prop))
            except Exception:
                continue
            found[prop] = type(value).__name__
        return found

    def _resolve_widget_name(name):
        """Find a WidgetBlueprint by bare object name -> its full path, or None.

        Searches the asset registry by class so typing "WBP_Thing" loads it without
        the full /Game/... path. Returns the first match (None if none / ambiguous
        picks the first).
        """
        ar = unreal.AssetRegistryHelpers.get_asset_registry()
        f = unreal.ARFilter(class_names=["WidgetBlueprint"], recursive_classes=True)
        for data in ar.get_assets(f):
            if str(data.asset_name) == name:
                return str(data.package_name) + "." + name
        return None

    def list_verse_fields(wbp_path):
        """[{name, type, ue5_class, category}] — types from the engine's own
        VerseClassFields tag, category from the Blueprint variable itself."""
        asset = _el.find_asset_data(wbp_path)
        blob = str(asset.get_tag_value("VerseClassFields") or "")
        wbp = _el.load_asset(wbp_path)
        fields = []
        pattern = r'\(Name="([^"]+)".*?Type=([A-Za-z]+),TypeUE5Class="([^"]*)"'
        for name, raw_type, ue5_class in re.findall(pattern, blob):
            kind = _VERSE_TYPE.get(raw_type, raw_type.lower())
            if kind == "struct":
                if ue5_class.endswith("Colors_color_alpha"):
                    kind = "color_alpha"
                elif ue5_class.endswith("Colors_color"):
                    kind = "color"
            elif kind == "asset":
                kind = "texture" if "Texture2D" in ue5_class else "material"
            try:
                category = str(_bel.get_blueprint_variable_category(wbp, name))
            except Exception:
                category = "Default"
            fields.append({"name": name, "type": kind,
                           "ue5_class": ue5_class, "category": category})
        return fields

    def _export_t3d(wbp):
        """Export the asset to T3D text (scratch file is deleted after reading)."""
        path = os.path.join(unreal.Paths.project_saved_dir(), "verse_binder_scan.t3d")
        task = unreal.AssetExportTask()
        task.object = wbp
        task.filename = path
        task.automated = True
        task.replace_identical = True
        task.prompt = False
        unreal.Exporter.run_asset_export_task(task)
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            text = handle.read()
        try:
            os.remove(path)
        except OSError:
            pass
        return text

    def _widget_tree_entries(wbp_path):
        """(class_path, name) pairs for every object in the asset's T3D export.

        One export feeds both list_widgets and list_child_widgets — pass the
        result to each so a Load only exports once.
        """
        wbp = _el.load_asset(wbp_path)
        text = _export_t3d(wbp)
        return re.findall(r'Begin Object Class=([\w/\.\']+) Name="([^"]+)"', text)

    def list_widgets(wbp_path, entries=None):
        """[{name, class_path, native}] for bindable native (engine) widgets."""
        widgets, seen = [], set()
        # Full class PATH is required — Blueprint widgets need it to resolve ancestry.
        for class_path, name in (entries if entries is not None
                                 else _widget_tree_entries(wbp_path)):
            if name in seen:
                continue
            native = _native_base(class_path)
            if native is None:   # panels, graph nodes, MVVM objects — nothing to bind
                continue
            seen.add(name)
            widgets.append({"name": name, "class_path": class_path, "native": native})
        return widgets

    def variable_guids(wbp_path):
        """Member VarName -> VarGuid, from the asset's own serialization.

        Anchored to NewVariables(N)= entries: matches every MEMBER variable
        regardless of naming convention (no VF_ prefix required), while excluding
        function-local variables — those also serialize VarName/VarGuid, and
        counting them would break create_verse_fields' NewVariables memory probe,
        which requires an exact element count.
        """
        wbp = _el.load_asset(wbp_path)
        text = _export_t3d(wbp)
        return dict(re.findall(
            r'NewVariables\(\d+\)=\(VarName="([^"]+)",VarGuid=([A-F0-9]{32})', text))

    # ── child sub-widget discovery ───────────────────────────────────────────
    #
    # A parent Verse field can bind to a Verse field on an EMBEDDED child widget
    # instance (VF_Slot1Image -> Slot1.VF_SlotImage). The destination names the
    # child's GENERATED class as MemberParent and the child field as MemberName,
    # with Source=Widget. Same-type field-to-field needs NO conversion — the Brush
    # conversion (if any) lives inside the child. Verified working; see
    # verse_field_tools.md "Field-to-field bindings".

    _child_fields_cache = {}

    def _class_path_to_asset(class_path):
        """Generated-class path -> the child WBP object path it belongs to.

        T3D exports the class bare ("/NewTesting/WC_Slot.WC_Slot_C"); bindings need
        the wrapped form. Both reduce to the same package + short name minus "_C":
        "/NewTesting/WC_Slot.WC_Slot_C" -> "/NewTesting/WC_Slot.WC_Slot".
        """
        inner = re.search(r"'([^']+)'", class_path)
        obj = (inner.group(1) if inner else class_path).strip()
        pkg, _, short = obj.partition(".")
        if short.endswith("_C"):
            short = short[:-2]
        return "%s.%s" % (pkg, short) if short else pkg

    # MemberParent in a child binding always uses the wrapped generated-class form.
    _GEN_CLASS_WRAP = "/Script/UMG.WidgetBlueprintGeneratedClass'%s'"

    def _wrap_generated_class(class_path):
        """Bare or wrapped class path -> the wrapped form used in MemberParent."""
        inner = re.search(r"'([^']+)'", class_path)
        bare = (inner.group(1) if inner else class_path).strip()
        return _GEN_CLASS_WRAP % bare

    def _child_verse_fields(child_wbp_path):
        """Verse fields declared on a child widget's own WBP -> [{name, type}]."""
        if child_wbp_path in _child_fields_cache:
            return _child_fields_cache[child_wbp_path]
        try:
            fields = [{"name": f["name"], "type": f["type"]}
                      for f in list_verse_fields(child_wbp_path)]
        except Exception:
            fields = []
        _child_fields_cache[child_wbp_path] = fields
        return fields

    def list_child_widgets(wbp_path, entries=None):
        """Embedded USER-widget instances that expose Verse fields.

        [{name, class_path, child_wbp_path, fields:[{name,type}]}]. Native widgets
        (Image/TextBlock/…) are excluded — those are handled by list_widgets.
        """
        out, seen = [], set()
        for class_path, name in (entries if entries is not None
                                 else _widget_tree_entries(wbp_path)):
            if name in seen:
                continue
            # A user sub-widget instance: its class is a generated Blueprint class
            # (ends "_C", lives in a content package, NOT /Script/…) and is not a
            # native-derived widget (those are engine props, handled elsewhere).
            bare = class_path.strip().strip("'")
            if bare.startswith("/Script/") or not bare.endswith("_C"):
                continue
            if _native_base(class_path) is not None:
                continue
            child_wbp = _class_path_to_asset(class_path)
            fields = _child_verse_fields(child_wbp)
            if not fields:
                continue  # nothing bindable on it
            seen.add(name)
            out.append({"name": name, "class_path": class_path,
                        "child_wbp_path": child_wbp, "fields": fields})
        return out

    def is_direct_bindable(verse_type, native, prop_name):
        """True iff this pair binds with no conversion function."""
        prop_type = widget_bindable_properties(native).get(prop_name)
        return prop_type is not None and (verse_type, prop_type) in _DIRECT

    _ZERO_GUID = "0" * 32

    def _binding_from_parts(field_name, field_guid, widget_name, dest_ref, binding_id):
        """Assemble a OneWayToDestination, no-conversion binding from its parts.

        `field_guid` may be zeroed — the compiler re-resolves the source by name.
        `dest_ref` is the inner DestinationPath BindingReference body.
        """
        return (
            '(SourcePath=(Paths=((BindingReference=(MemberName="%s",MemberGuid=%s,'
            'bSelfContext=True),BindingKind=Property)),WidgetName="",'
            'ContextId=00000000000000000000000000000000,Source=SelfContext,'
            'bIsComponent=False,bDeprecatedSource=True),'
            'DestinationPath=(Paths=((BindingReference=(%s),BindingKind=Property)),'
            'WidgetName="%s",ContextId=00000000000000000000000000000000,Source=Widget,'
            'bIsComponent=False,bDeprecatedSource=True),'
            'BindingType=OneWayToDestination,bOverrideExecutionMode=False,'
            'OverrideExecutionMode=Immediate,'
            'Conversion=(DestinationToSourceConversion=None,SourceToDestinationConversion=None),'
            'BindingId=%s,bEnabled=True,bCompile=True)'
            % (field_name, field_guid, dest_ref, widget_name, binding_id))

    def _binding_string(field_name, field_guid, widget_name, native, prop_name, binding_id):
        """Parent field -> native engine widget property."""
        declaring = _declaring_class(native, prop_name)
        member = _serialized_prop(prop_name)   # IsEnabled -> bIsEnabled, etc.
        if declaring is None:
            dest_ref = 'MemberName="%s",bSelfContext=True' % member
        else:
            dest_ref = 'MemberParent="%s",MemberName="%s"' % (
                _CLASS_PATH % declaring, member)
        return _binding_from_parts(field_name, field_guid, widget_name, dest_ref, binding_id)

    def _binding_string_child(field_name, widget_name, child_class_path,
                              child_field, binding_id):
        """Parent field -> an embedded child widget instance's Verse field.

        The source GUID is zeroed — resolved by name on compile — so this never
        needs the parent's protected NewVariables GUID. Same-type only; no
        conversion (any Brush conversion lives inside the child widget).
        """
        dest_ref = 'MemberParent="%s",MemberName="%s"' % (
            _wrap_generated_class(child_class_path), child_field)
        return _binding_from_parts(field_name, _ZERO_GUID, widget_name, dest_ref, binding_id)

    _SRC_RE = r'SourcePath=\(Paths=\(\(BindingReference=\(MemberName="([^"]+)"'
    _DST_WIDGET_RE = r'DestinationPath=.*?WidgetName="([^"]*)"'
    _DST_PROP_RE = (r'DestinationPath=\(Paths=\(\(BindingReference=\('
                    r'(?:MemberParent="[^"]*",)?MemberName="([^"]+)"')

    def _destination_of(binding_text):
        widget = re.search(_DST_WIDGET_RE, binding_text)
        prop = re.search(_DST_PROP_RE, binding_text)
        if widget and prop:
            # Normalize the serialized name (bIsEnabled) back to the friendly one
            # (IsEnabled) so display, occupancy and unbind all speak one language.
            return widget.group(1), _friendly_prop(prop.group(1))
        return None

    def existing_bindings(wbp_path):
        """[{index, source, widget, prop, has_conversion}] for the current view."""
        wbp = _el.load_asset(wbp_path)
        subsystem = unreal.get_editor_subsystem(unreal.MVVMEditorSubsystem)
        # get_view() reads; request_view() would CREATE a view (a mutation).
        view = subsystem.get_view(wbp)
        if view is None:
            return []
        rows = []
        for i, binding in enumerate(view.get_editor_property("bindings")):
            text = binding.export_text()
            src = re.search(_SRC_RE, text)
            dest = _destination_of(text)
            rows.append({
                "index": i,
                # A binding with a conversion has an EMPTY SourcePath — its real
                # source lives on a pin inside the generated conversion graph.
                "source": src.group(1) if src else None,
                "widget": dest[0] if dest else "?",
                "prop": dest[1] if dest else "?",
                "has_conversion": "MVVMBlueprintViewConversionFunction" in text,
            })
        return rows

    def _normalize_pair(pair):
        """Accept either a native-prop tuple or a child dict; return a uniform dict.

        native:  (field, widget, native, prop)
                 -> {mode:'native', field, widget, dest:prop, native, prop}
        child:   {mode:'child', field, widget, class_path, child_field}
                 -> {mode:'child', field, widget, dest:child_field, ...}
        `dest` is the destination MemberName used for occupancy matching.
        """
        if isinstance(pair, dict):
            p = dict(pair)
            p.setdefault("mode", "child")
            p["dest"] = p["child_field"]
            return p
        field, widget, native, prop = pair
        return {"mode": "native", "field": field, "widget": widget,
                "native": native, "prop": prop, "dest": prop}

    def _pair_binding_string(p, guid, binding_id):
        if p["mode"] == "child":
            return _binding_string_child(p["field"], p["widget"],
                                         p["class_path"], p["child_field"], binding_id)
        return _binding_string(p["field"], guid, p["widget"],
                               p["native"], p["prop"], binding_id)

    def apply_bindings(wbp_path, pairs, replace=True):
        """Create bindings. Each pair is either:

            native:  (field, widget, native, prop)                       [engine prop]
            child:   {mode:'child', field, widget, class_path, child_field}

        A destination holds at most one binding. With replace=True an existing
        binding on the same destination is retargeted **reusing its BindingId** —
        a fresh GUID would read to the engine as one binding vanishing and another
        appearing. Bindings that use a conversion are never clobbered.

        Returns {created, replaced, skipped} where each entry is
        (field, widget, dest[, reason]).
        """
        guids = variable_guids(wbp_path)
        wbp = _el.load_asset(wbp_path)
        subsystem = unreal.get_editor_subsystem(unreal.MVVMEditorSubsystem)
        view = subsystem.get_view(wbp) or subsystem.request_view(wbp)

        result = {"created": [], "replaced": [], "skipped": []}

        for raw in pairs:
            p = _normalize_pair(raw)
            tag = (p["field"], p["widget"], p["dest"])

            # Child bindings zero their source GUID (resolved on compile). Native
            # bindings still embed the real GUID, so verify it exists first.
            guid = guids.get(p["field"])
            if p["mode"] == "native" and not guid:
                result["skipped"].append((tag, "no VarGuid — is it a Verse field?"))
                continue

            # _destination_of normalizes bIsEnabled -> IsEnabled, so this matches
            # the tool's own IsEnabled binding on reload (no duplicate).
            bindings = list(view.get_editor_property("bindings"))
            occupied = next(
                (i for i, b in enumerate(bindings)
                 if _destination_of(b.export_text()) == (p["widget"], p["dest"])), None)

            if occupied is not None and not replace:
                result["skipped"].append((tag, "destination already bound"))
                continue

            if occupied is not None:
                old = bindings[occupied].export_text()
                if "MVVMBlueprintViewConversionFunction" in old:
                    result["skipped"].append(
                        (tag, "existing binding uses a conversion — refusing to clobber"))
                    continue
                binding_id = re.search(r"BindingId=([A-F0-9]{32})", old).group(1)
                bindings[occupied].import_text(_pair_binding_string(p, guid, binding_id))
                view.set_editor_property("bindings", bindings)
                result["replaced"].append(tag)
            else:
                subsystem.add_binding(wbp)   # append an empty shell
                bindings = list(view.get_editor_property("bindings"))
                # set_editor_property('source_path', ...) is REFUSED (read-only);
                # import_text on the whole struct bypasses the per-property guard.
                bindings[-1].import_text(
                    _pair_binding_string(p, guid, uuid.uuid4().hex.upper()))
                # Bindings are struct COPIES — the array must be written back whole.
                view.set_editor_property("bindings", bindings)
                result["created"].append(tag)

        _bel.compile_blueprint(wbp)
        _el.save_asset(wbp_path)
        return result

    def remove_bindings(wbp_path, destinations):
        """Delete bindings by [(widget, prop)].

        remove_binding() silently no-ops on a struct pulled from the array (a copy).
        Rebuilding the array is the only reliable delete.
        """
        wbp = _el.load_asset(wbp_path)
        subsystem = unreal.get_editor_subsystem(unreal.MVVMEditorSubsystem)
        view = subsystem.get_view(wbp)
        if view is None:
            return 0
        targets = set(destinations)
        keep, dropped = [], 0
        for binding in view.get_editor_property("bindings"):
            if _destination_of(binding.export_text()) in targets:
                dropped += 1
                continue
            keep.append(binding)
        if dropped:   # don't dirty + resave the asset for a no-op
            view.set_editor_property("bindings", keep)
            _bel.compile_blueprint(wbp)
            _el.save_asset(wbp_path)
        return dropped

    def _split_pattern(pattern):
        """Split a name pattern around its `#` (or `{n}`) placeholder -> (pre, post).

        Returns None when the pattern has no placeholder. Shared by bulk matching
        and batch creation so "VF_Slot#Image" means the same thing everywhere.
        """
        token = "#" if "#" in pattern else ("{n}" if "{n}" in pattern else None)
        if token is None:
            return None
        pre, _, post = pattern.partition(token)
        return pre, post

    def detect_patterns(names):
        """Auto-detect numbered families: ["Slot1","Slot2","Img"] -> [("Slot#", 2)].

        Only names with exactly ONE digit run participate — a second run would
        yield a pattern the matchers treat as literal text. A family needs at
        least two members to count. Sorted largest-family first.
        """
        groups = {}
        for name in names:
            if len(re.findall(r"\d+", name)) != 1:
                continue
            pat = re.sub(r"\d+", "#", name)
            groups[pat] = groups.get(pat, 0) + 1
        return sorted(((p, c) for p, c in groups.items() if c >= 2),
                      key=lambda pc: (-pc[1], pc[0]))

    def _index_by_pattern(items, pattern):
        """{n: [items]} where each item's name matches `pattern` with # as the index.

        The placeholder may sit anywhere in the name: "VF_Slot#Image" matches
        VF_Slot1Image→1, "Slot#" matches Slot3→3. A bare pattern with no
        placeholder is treated as prefix# (number right after it). This handles
        EMBEDDED indices, which trailing-number matching cannot, and
        disambiguates VF_Slot#Image from VF_Slot#Name.
        """
        pre, post = _split_pattern(pattern) or (pattern, "")
        rx = re.compile("^%s(\\d+)%s$" % (re.escape(pre), re.escape(post)))
        out = {}
        for item in items:
            m = rx.match(item["name"])
            if m:
                out.setdefault(int(m.group(1)), []).append(item)
        return out

    def match_by_suffix(fields, widgets, field_pattern, widget_pattern, prop_name):
        """Pair fields with engine widgets by a shared index.

        Patterns use `#` as the number placeholder ("VF_Color#" ↔ "Image#"); a bare
        pattern is treated as prefix#. Type mismatches and unmatched indices are
        reported, never silently dropped — a mismatch is usually a typo in a pattern.
        Returns ([(field, widget, native, prop)], [warnings]).
        """
        fields_by_n = _index_by_pattern(fields, field_pattern)
        widgets_by_n = _index_by_pattern(widgets, widget_pattern)

        pairs, warnings = [], []
        for n in sorted(set(fields_by_n) & set(widgets_by_n)):
            fs, ws = fields_by_n[n], widgets_by_n[n]
            if len(fs) > 1 or len(ws) > 1:
                warnings.append("index %d ambiguous: %s / %s"
                                % (n, [f["name"] for f in fs], [w["name"] for w in ws]))
                continue
            field, widget = fs[0], ws[0]
            if not is_direct_bindable(field["type"], widget["native"], prop_name):
                warnings.append("%s (%s) cannot bind directly to %s.%s — needs a conversion"
                                % (field["name"], field["type"], widget["name"], prop_name))
                continue
            pairs.append((field["name"], widget["name"], widget["native"], prop_name))

        for n in sorted(set(fields_by_n) - set(widgets_by_n)):
            warnings.append("%s has no matching widget (index %d)"
                            % (fields_by_n[n][0]["name"], n))
        for n in sorted(set(widgets_by_n) - set(fields_by_n)):
            warnings.append("%s has no matching field (index %d)"
                            % (widgets_by_n[n][0]["name"], n))
        return pairs, warnings

    def match_child_by_suffix(fields, child_widgets, field_pattern, child_pattern,
                              child_field):
        """Pair fields with sub-widget instances by a shared index.

        `field_pattern` and `child_pattern` use `#` as the number placeholder, e.g.
        "VF_Slot#Image" ↔ "Slot#" pairs VF_Slot1Image with Slot1, etc. Same-type
        only: the parent field's type must equal the child field's type.
        Returns ([child-pair dicts], [warnings]).
        """
        fields_by_n = _index_by_pattern(fields, field_pattern)
        children_by_n = _index_by_pattern(child_widgets, child_pattern)

        pairs, warnings = [], []
        for n in sorted(set(fields_by_n) & set(children_by_n)):
            fs, cs = fields_by_n[n], children_by_n[n]
            if len(fs) > 1 or len(cs) > 1:
                warnings.append("index %d ambiguous: %s / %s"
                                % (n, [f["name"] for f in fs], [c["name"] for c in cs]))
                continue
            field, child = fs[0], cs[0]
            cf = next((f for f in child["fields"] if f["name"] == child_field), None)
            if cf is None:
                warnings.append("%s has no field %s" % (child["name"], child_field))
                continue
            if field["type"] != cf["type"]:
                warnings.append("%s (%s) ≠ %s.%s (%s) — types must match"
                                % (field["name"], field["type"], child["name"],
                                   child_field, cf["type"]))
                continue
            pairs.append({"mode": "child", "field": field["name"],
                          "widget": child["name"], "class_path": child["class_path"],
                          "child_field": child_field})

        for n in sorted(set(fields_by_n) - set(children_by_n)):
            warnings.append("%s has no matching sub-widget (index %d)"
                            % (fields_by_n[n][0]["name"], n))
        for n in sorted(set(children_by_n) - set(fields_by_n)):
            warnings.append("%s has no matching field (index %d)"
                            % (children_by_n[n][0]["name"], n))
        return pairs, warnings

    # ═══════════════════════════════════════════════════════════════════════
    #  FIELD CREATION  (memory-patched Verse metadata — see verse_field_tools.md)
    # ═══════════════════════════════════════════════════════════════════════
    #
    # UE exposes NO API for a variable's MetaDataArray. A Verse field is a normal
    # BP member variable carrying 4 metadata keys (5 for `message`) plus
    # PropertyFlags=65541. We create the variable via the public API, then patch
    # only the metadata in memory. Nothing is hardcoded to a build: the
    # NewVariables offset is located by probing for a known VarGuid, and FName key
    # bytes are interned at runtime.

    _DESC_SIZE = 232
    _GUID_OFFSET = 12
    _PROPERTY_FLAGS_OFFSET = 176
    _METADATA_OFFSET = 200
    _ENTRY_SIZE = 32
    _VERSE_PROPERTY_FLAGS = 65541
    _NEWVARS_SEARCH_LIMIT = 1400

    _COLOR_STRUCT = ("/Script/CoreUObject.VerseStruct"
                     "'/VerseColors/_Verse/VNI/VerseColors.Colors_color'")
    _COLOR_ALPHA_STRUCT = ("/Script/CoreUObject.VerseStruct"
                           "'/VerseColors/_Verse/VNI/VerseColors.Colors_color_alpha'")

    _CREATE_TYPES = ("logic", "int", "float", "string", "message",
                     "color", "color_alpha", "material", "texture")

    # ctypes buffers must outlive serialization or Unreal reads dangling pointers.
    if not hasattr(unreal, "_verse_field_buffers"):
        unreal._verse_field_buffers = []
    _KEEP = unreal._verse_field_buffers

    # (wbp_path, name) of fields memory-patch-created THIS editor session — their
    # MetaDataArray points at our ctypes buffers. delete_verse_fields detaches those
    # pointers before removal (see _detach_metadata_arrays), so deletion is safe; this
    # set is just bookkeeping. Stored on the unreal module so it survives re-running
    # this file in a fresh namespace.
    if not hasattr(unreal, "_verse_fields_created_this_session"):
        unreal._verse_fields_created_this_session = set()
    _CREATED_THIS_SESSION = unreal._verse_fields_created_this_session

    def _pin_type(kind):
        """Friendly Verse type name -> EdGraphPinType."""
        basic = {"logic": ("bool", "bool"), "int": ("int64", "int64"),
                 "float": ("real", "real"), "string": ("string", "string"),
                 "message": ("text", "text")}
        if kind in basic:
            name, expect = basic[kind]
            pt = _bel.get_basic_type_by_name(name)
            # get_basic_type_by_name does NOT raise on unknown names -- it silently
            # returns an `int` pin. Assert rather than create a wrongly-typed field.
            actual = re.search(r'PinCategory="([^"]*)"', pt.export_text())
            actual = actual.group(1) if actual else "?"
            if actual != expect:
                raise RuntimeError("basic type %r resolved to %r, expected %r"
                                   % (name, actual, expect))
            return pt
        obj = {"color": ("struct", _COLOR_STRUCT),
               "color_alpha": ("struct", _COLOR_ALPHA_STRUCT),
               "material": ("softobject", "/Script/CoreUObject.Class'/Script/Engine.MaterialInterface'"),
               "texture": ("softobject", "/Script/CoreUObject.Class'/Script/Engine.Texture2D'")}
        if kind in obj:
            category, path = obj[kind]
            pt = unreal.EdGraphPinType()
            pt.import_text('(PinCategory="%s",PinSubCategoryObject="%s")' % (category, path))
            return pt
        raise ValueError("unknown Verse field type: %r" % (kind,))

    def _fname_key16(name):
        """Intern FName `name`; return the 16 bytes UE stores for a metadata DataKey.

        The comparison index is a per-session allocation into the global name pool,
        so it is NEVER hardcoded. Layout: [ci u32][number u32][ci u32][slack u32].
        """
        pt = unreal.EdGraphPinType()
        pt.import_text('(PinCategory="struct",PinSubCategory="%s")' % name)
        _KEEP.append(pt)                         # pt owns the FName; keep it alive
        raw = ctypes.c_uint64.from_address(id(pt) + 40).value
        ci = ctypes.c_uint32.from_address(raw + 20).value
        num = ctypes.c_uint32.from_address(raw + 24).value
        return struct.pack("<IIII", ci, num, ci, 0)

    def _fstring(text):
        buf = ctypes.create_unicode_buffer(text)  # already NUL-terminated
        _KEEP.append(buf)
        return ctypes.addressof(buf), len(text) + 1

    def _build_metadata_block(display_name, disable_default, visibility="<public>"):
        entries = [
            (_fname_key16("FieldNotify"), None),
            (_fname_key16("VerseVariable"), None),
            (_fname_key16("DisplayName"), display_name),
            (_fname_key16("VisibilityAccess"), visibility),
        ]
        if disable_default:                        # `message` fields only
            entries.append((_fname_key16("DisableDefaultValue"), None))
        block = ctypes.create_string_buffer(len(entries) * _ENTRY_SIZE)
        _KEEP.append(block)
        base = ctypes.addressof(block)
        for i, (key, value) in enumerate(entries):
            ctypes.memmove(base + i * _ENTRY_SIZE, key, 16)
            if value is not None:
                ptr, length = _fstring(value)
                ctypes.c_uint64.from_address(base + i * _ENTRY_SIZE + 16).value = ptr
                ctypes.c_uint32.from_address(base + i * _ENTRY_SIZE + 24).value = length
                ctypes.c_uint32.from_address(base + i * _ENTRY_SIZE + 28).value = length
        return base, len(entries)

    def _guid_bytes(guid_hex):
        return struct.pack("<4I", *struct.unpack(">4I", bytes.fromhex(guid_hex)))

    def _find_newvars(uobject, probe_guid, expected_count):
        for offset in range(0, _NEWVARS_SEARCH_LIMIT, 8):
            data = ctypes.c_uint64.from_address(uobject + offset).value
            count = ctypes.c_uint32.from_address(uobject + offset + 8).value
            if count != expected_count or not (0x10000 < data < 0x7FFFFFFFFFFF):
                continue
            head = bytes((ctypes.c_uint8 * 64).from_address(data))
            if head.find(probe_guid) == _GUID_OFFSET:
                return data, count
        raise RuntimeError("NewVariables array not found — descriptor layout changed")

    def create_verse_fields(wbp_path, fields, category=None):
        """Create Verse fields. `fields` = [(name, kind)].

        Every created variable is patched into a real Verse field and verified
        against the VerseClassFields tag. Raises if any field fails to materialize,
        so a half-created plain BP variable never survives silently.
        Returns {created, skipped_existing, verified}.
        """
        for name, kind in fields:
            _pin_type(kind)                        # validate all types up front

        wbp = _el.load_asset(wbp_path)
        existing = set(str(n) for n in _bel.list_member_variable_names(wbp))

        created, skipped = [], []
        for name, kind in fields:
            if name in existing:
                skipped.append(name)
                continue
            _bel.add_member_variable(wbp, name, _pin_type(kind))
            created.append(name)

        if not created:
            return {"created": [], "skipped_existing": skipped, "verified": {}}

        _bel.compile_blueprint(wbp)
        _el.save_asset(wbp_path)

        # T3D export can invalidate pointers — reload before touching memory.
        wbp = _el.load_asset(wbp_path)
        guids = variable_guids(wbp_path)
        wbp = _el.load_asset(wbp_path)

        kinds = dict(fields)
        uobject = ctypes.c_uint64.from_address(id(wbp) + 16).value
        probe = next(iter(guids))
        data, count = _find_newvars(uobject, _guid_bytes(guids[probe]), len(guids))

        patched = []
        for index in range(count):
            descriptor = data + index * _DESC_SIZE
            guid = bytes((ctypes.c_uint8 * 16).from_address(descriptor + _GUID_OFFSET))
            name = next((n for n, g in guids.items() if _guid_bytes(g) == guid), None)
            if name not in created:
                continue
            block, entries = _build_metadata_block(name, kinds[name] == "message")
            ctypes.c_uint64.from_address(descriptor + _METADATA_OFFSET).value = block
            ctypes.c_uint32.from_address(descriptor + _METADATA_OFFSET + 8).value = entries
            ctypes.c_uint32.from_address(descriptor + _METADATA_OFFSET + 12).value = entries
            ctypes.c_uint64.from_address(descriptor + _PROPERTY_FLAGS_OFFSET).value = \
                _VERSE_PROPERTY_FLAGS
            patched.append(name)

        missing = sorted(set(created) - set(patched))
        if missing:
            raise RuntimeError(
                "created but could not patch %r — these are plain BP variables, "
                "not Verse fields. Delete them before retrying." % (missing,))

        if category:
            for name in created:
                _bel.set_blueprint_variable_category(wbp, name, category)

        _bel.compile_blueprint(wbp)
        _el.save_asset(wbp_path)

        # VerseClassFields is regenerated by the engine on save -- the honest check.
        asset = _el.find_asset_data(wbp_path)
        blob = str(asset.get_tag_value("VerseClassFields") or "")
        present = set(re.findall(r'\(Name="([^"]+)"', blob))
        verified = {n: n in present for n in created}
        failed = [n for n, ok in verified.items() if not ok]
        if failed:
            raise RuntimeError("patched but absent from VerseClassFields: %r" % (failed,))

        # Mark as created-this-session — deleting these before an editor restart
        # would make the engine free our ctypes metadata buffers and crash.
        _CREATED_THIS_SESSION.update((wbp_path, n) for n in created)

        return {"created": created, "skipped_existing": skipped, "verified": verified}

    def set_fields_category(wbp_path, names, category):
        """Move member variables to `category` (public API), compile and save."""
        wbp = _el.load_asset(wbp_path)
        for name in names:
            _bel.set_blueprint_variable_category(wbp, name, category)
        _bel.compile_blueprint(wbp)
        _el.save_asset(wbp_path)

    def _detach_metadata_arrays(wbp, wbp_path, names):
        """Null the MetaDataArray TArray of each named descriptor before deletion.

        A field created THIS session has its MetaDataArray data pointer aimed at one
        of OUR ctypes buffers. If the descriptor is destroyed with that pointer live,
        UE's TArray destructor calls FMemory::Free() on memory it never allocated →
        heap-corruption crash. Setting the TArray empty (data=0, count=0, max=0)
        makes its destructor a no-op (Free(nullptr) is safe). The metadata is already
        serialized to disk, and the variable is being removed anyway, so nothing is
        lost. Fields deserialized from disk (prior session) already point at
        engine-owned memory; nulling theirs is harmless too, so we do it uniformly.
        """
        guids = variable_guids(wbp_path)
        doomed = {n for n in names if n in guids}
        if not doomed:
            return
        uobject = ctypes.c_uint64.from_address(id(wbp) + 16).value
        probe = next(iter(guids))
        data, count = _find_newvars(
            uobject, _guid_bytes(guids[probe]), len(guids))
        for i in range(count):
            desc = data + i * _DESC_SIZE
            guid = bytes((ctypes.c_uint8 * 16).from_address(desc + _GUID_OFFSET))
            name = next((n for n, g in guids.items()
                         if _guid_bytes(g) == guid), None)
            if name in doomed:
                ctypes.c_uint64.from_address(desc + _METADATA_OFFSET).value = 0
                ctypes.c_uint32.from_address(desc + _METADATA_OFFSET + 8).value = 0
                ctypes.c_uint32.from_address(desc + _METADATA_OFFSET + 12).value = 0

    def delete_verse_fields(wbp_path, names):
        """Delete member variables — safely, even same-session.

        NEVER use remove_unused_variables here: it deletes EVERY variable with no
        references, which includes every not-yet-bound Verse field. Bindings
        sourced from the deleted fields are dropped too (they would dangle).

        Same-session fields are memory-patched to point their MetaDataArray at our
        ctypes buffers; deleting one lets UE free that buffer → crash. So we first
        DETACH every doomed field's MetaDataArray (null the TArray, no-op destructor)
        and only then remove the variable. No editor restart needed.

        Returns {deleted, failed, bindings_dropped}.
        """
        if not names:
            return {"deleted": [], "failed": [], "bindings_dropped": 0}

        wbp = _el.load_asset(wbp_path)
        # Detach BEFORE removal so the descriptor destructor frees nothing of ours.
        _detach_metadata_arrays(wbp, wbp_path, names)
        editor = unreal.BlueprintGraphEditor.get_graph_editor_by_name(wbp, "EventGraph")
        for name in names:
            editor.remove_member_variable(name)
            _CREATED_THIS_SESSION.discard((wbp_path, name))

        doomed = set(names)
        subsystem = unreal.get_editor_subsystem(unreal.MVVMEditorSubsystem)
        view = subsystem.get_view(wbp)
        dropped = 0
        if view is not None:
            keep = []
            for binding in view.get_editor_property("bindings"):
                src = re.search(_SRC_RE, binding.export_text())
                if src and src.group(1) in doomed:
                    dropped += 1
                    continue
                keep.append(binding)
            if dropped:
                view.set_editor_property("bindings", keep)

        _bel.compile_blueprint(wbp)
        _el.save_asset(wbp_path)

        remaining = set(str(n) for n in _bel.list_member_variable_names(wbp))
        return {"deleted": [n for n in names if n not in remaining],
                "failed": [n for n in names if n in remaining],
                "bindings_dropped": dropped}

    def expand_batch_spec(spec, kind, start, count):
        """Expand a batch spec into [(name, kind)] pairs.

        `spec` is a name pattern. `#` (or `{n}`) marks where the running index
        goes; a bare pattern with count>1 gets the index appended. count==1 with
        no placeholder creates a single field. Returns (pairs, error_or_None).
        """
        spec = spec.strip()
        if not spec:
            return [], "Enter a field name or pattern."
        if kind not in _CREATE_TYPES:
            return [], "Unknown type %r." % kind

        parts = _split_pattern(spec)
        if count <= 1 and parts is None:
            return [(spec, kind)], None
        pre, post = parts or (spec, "")

        pairs = []
        for i in range(start, start + max(count, 1)):
            pairs.append(("%s%d%s" % (pre, i, post), kind))
        return pairs, None

    # ═══════════════════════════════════════════════════════════════════════
    #  UI
    # ═══════════════════════════════════════════════════════════════════════

    _TYPE_COLOR = {
        "logic": "#ef3535", "int": "#1fe44b", "float": "#8bc24a",
        "string": "#ff5ecf", "message": "#ff8fd8",
        "color": "#0e86ff", "color_alpha": "#0e86ff",
        "material": "#00d9d9", "texture": "#00d9d9",
    }

    # Bulk mode only offers DIRECTLY-bindable props (it authors real bindings, so
    # a locked Brush has no place here). Restricted to the widgets UEFN exposes.
    _BULK_PROPS = {
        "TextBlock": ["Text", "ColorAndOpacity", "RenderOpacity"],
        "Image": ["ColorAndOpacity", "RenderOpacity"],
    }

    class VerseBinderWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Verse Fields + Binding Tool")
            self.setMinimumSize(940, 680)
            self.resize(1080, 780)
            self._wbp_path = None
            self._fields = []
            self._visible_fields = []   # _fields after the category filter
            self._widgets = []
            self._child_widgets = []
            self._target_rows = []
            self._pending_pairs = []
            self._build_ui()

        def _build_ui(self):
            central = QWidget()
            self.setCentralWidget(central)
            outer = QVBoxLayout(central)
            outer.setContentsMargins(8, 8, 8, 8)
            outer.setSpacing(6)

            # Shared asset row -- both tabs operate on the same widget. The field
            # shows just the widget NAME once loaded; the full path lives in
            # self._wbp_path. You can still type a name or a full path to load.
            top = QHBoxLayout()
            self.path_edit = QLineEdit()
            self.path_edit.setPlaceholderText("Click From Selection, or type a widget name")
            self.path_edit.returnPressed.connect(self._load)
            btn_sel = QPushButton("From Selection")
            btn_sel.clicked.connect(self._from_selection)
            btn_load = QPushButton("Load")
            btn_load.setObjectName("btn_primary")
            btn_load.clicked.connect(self._load)
            top.addWidget(QLabel("Widget:"))
            top.addWidget(self.path_edit, 1)
            top.addWidget(btn_sel)
            top.addWidget(btn_load)
            outer.addLayout(top)

            self.tabs = QTabWidget()
            outer.addWidget(self.tabs, 1)
            self.tabs.addTab(self._build_bind_tab(), "Bind")
            self.tabs.addTab(self._build_create_tab(), "Create Fields")
            self.tabs.addTab(self._build_manage_tab(), "Manage Fields")

        def _build_bind_tab(self):
            tab = QWidget()
            root = QVBoxLayout(tab)
            root.setContentsMargins(4, 6, 4, 4)
            root.setSpacing(6)

            split = QSplitter(Qt.Orientation.Vertical)
            root.addWidget(split, 1)

            upper = QWidget()
            upper_l = QHBoxLayout(upper)
            upper_l.setContentsMargins(0, 0, 0, 0)

            fbox = QGroupBox("VERSE FIELDS   (multi-select for pairing)")
            fl = QVBoxLayout(fbox)
            frow = QHBoxLayout()
            frow.addWidget(QLabel("Category:"))
            self.category_filter = ArrowCombo()
            self.category_filter.addItem("All")
            self.category_filter.currentIndexChanged.connect(self._refresh_fields)
            frow.addWidget(self.category_filter, 1)
            fl.addLayout(frow)
            self.tbl_fields = QTableWidget(0, 3)
            self.tbl_fields.setHorizontalHeaderLabels(["Field", "Type", "Category"])
            self._setup_table(self.tbl_fields, multi=True)
            self.tbl_fields.itemSelectionChanged.connect(self._refresh_targets)
            fl.addWidget(self.tbl_fields)
            upper_l.addWidget(fbox, 1)

            tbox = QGroupBox("BINDABLE TARGETS   (multi-select to zip with fields)")
            tl = QVBoxLayout(tbox)
            self.tbl_targets = QTableWidget(0, 4)
            self.tbl_targets.setHorizontalHeaderLabels(["Widget", "Class", "Target", "Current"])
            self._setup_table(self.tbl_targets, multi=True)
            tl.addWidget(self.tbl_targets)
            trow = QHBoxLayout()
            btn_unbind = QPushButton("Unbind")
            btn_unbind.clicked.connect(self._unbind_selected)
            btn_zip = QPushButton("Bind Selected Pairs")
            btn_zip.clicked.connect(self._bind_selected_pairs)
            btn_bind = QPushButton("Bind Selected")
            btn_bind.setObjectName("btn_primary")
            btn_bind.clicked.connect(self._bind_selected)
            trow.addStretch(1)
            trow.addWidget(btn_unbind)
            trow.addWidget(btn_zip)
            trow.addWidget(btn_bind)
            tl.addLayout(trow)
            upper_l.addWidget(tbox, 1)
            split.addWidget(upper)

            # ── bulk-by-number ────────────────────────────────────────────────
            self.bulk_box = QGroupBox(
                "BULK BIND BY NUMBER    —    patterns auto-detected from names "
                "(# = index; editable)")
            bl = QVBoxLayout(self.bulk_box)
            row = QHBoxLayout()
            # Editable dropdowns, pre-filled with families detected from the
            # loaded widget's actual field / widget names.
            self.field_pattern = ArrowCombo()
            self.field_pattern.setEditable(True)
            self.field_pattern.lineEdit().setPlaceholderText("VF_Slot#Image")
            self.field_pattern.activated.connect(self._auto_pick_bulk_target)
            self.widget_pattern = ArrowCombo()
            self.widget_pattern.setEditable(True)
            self.widget_pattern.lineEdit().setPlaceholderText("Slot#  or  Image#")
            # One merged target dropdown: engine properties + child Verse fields.
            self.target_combo = ArrowCombo()
            row.addWidget(QLabel("Field pattern:"))
            row.addWidget(self.field_pattern, 1)
            row.addWidget(QLabel("Widget pattern:"))
            row.addWidget(self.widget_pattern, 1)
            row.addWidget(QLabel("Target:"))
            row.addWidget(self.target_combo, 1)
            bl.addLayout(row)
            row2 = QHBoxLayout()
            btn_preview = QPushButton("Preview Matches")
            btn_preview.clicked.connect(self._preview_bulk)
            self.btn_apply_bulk = QPushButton("Bind All")
            self.btn_apply_bulk.setObjectName("btn_action")
            self.btn_apply_bulk.setEnabled(False)
            self.btn_apply_bulk.clicked.connect(self._apply_bulk)
            row2.addStretch(1)
            row2.addWidget(btn_preview)
            row2.addWidget(self.btn_apply_bulk)
            bl.addLayout(row2)
            split.addWidget(self.bulk_box)

            self.log = QTextEdit()
            self.log.setReadOnly(True)
            self.log.setMinimumHeight(110)
            split.addWidget(self.log)
            split.setSizes([380, 130, 150])

            self._refresh_target_combo()
            self._log("Select a Widget Blueprint in the Content Browser, "
                      "then click From Selection.", C_TX2)
            return tab

        def _build_create_tab(self):
            tab = QWidget()
            root = QVBoxLayout(tab)
            root.setContentsMargins(4, 6, 4, 4)
            root.setSpacing(6)

            # Batch spec row
            spec = QGroupBox("BATCH CREATE   —   use  #  as the number placeholder "
                             "(VF_Color#  →  VF_Color1, VF_Color2, …)")
            sl = QVBoxLayout(spec)
            row = QHBoxLayout()
            self.create_name = QLineEdit()
            self.create_name.setPlaceholderText("VF_Color#")
            self.create_type = ArrowCombo()
            self.create_type.addItems(_CREATE_TYPES)
            self.create_type.setCurrentText("color")
            self.create_start = QSpinBox()
            self.create_start.setRange(0, 9999)
            self.create_start.setValue(1)
            self.create_count = QSpinBox()
            self.create_count.setRange(1, 999)
            self.create_count.setValue(5)
            # Editable dropdown pre-filled with the widget's existing categories,
            # so new fields can be created straight into one — or type a new name.
            self.create_category = ArrowCombo()
            self.create_category.setEditable(True)
            self.create_category.lineEdit().setPlaceholderText(
                "(optional) pick existing or type new, e.g. Colors")
            row.addWidget(QLabel("Name / pattern:"))
            row.addWidget(self.create_name, 2)
            row.addWidget(QLabel("Type:"))
            row.addWidget(self.create_type)
            row.addWidget(QLabel("Start:"))
            row.addLayout(self._stepper(self.create_start))
            row.addWidget(QLabel("Count:"))
            row.addLayout(self._stepper(self.create_count))
            sl.addLayout(row)
            row2 = QHBoxLayout()
            row2.addWidget(QLabel("Category:"))
            row2.addWidget(self.create_category, 1)
            self.create_name.textChanged.connect(self._preview_create)
            self.create_category.editTextChanged.connect(self._preview_create)
            self.create_type.currentTextChanged.connect(self._preview_create)
            self.create_start.valueChanged.connect(self._preview_create)
            self.create_count.valueChanged.connect(self._preview_create)
            row2.addStretch(1)
            btn_preview = QPushButton("Preview")
            btn_preview.clicked.connect(self._preview_create)
            self.btn_create = QPushButton("Create Fields")
            self.btn_create.setObjectName("btn_action")
            self.btn_create.clicked.connect(self._run_create)
            row2.addWidget(btn_preview)
            row2.addWidget(self.btn_create)
            sl.addLayout(row2)
            root.addWidget(spec)

            self.create_preview = QTextEdit()
            self.create_preview.setReadOnly(True)
            root.addWidget(self.create_preview, 1)

            self._preview_create()
            return tab

        def _build_manage_tab(self):
            tab = QWidget()
            root = QVBoxLayout(tab)
            root.setContentsMargins(4, 6, 4, 4)
            root.setSpacing(6)

            box = QGroupBox("MANAGE FIELDS   —   recategorize or delete existing "
                            "Verse fields (multi-select)")
            gl = QVBoxLayout(box)
            self.tbl_manage = QTableWidget(0, 3)
            self.tbl_manage.setHorizontalHeaderLabels(["Field", "Type", "Category"])
            self._setup_table(self.tbl_manage, multi=True)
            gl.addWidget(self.tbl_manage)

            row = QHBoxLayout()
            row.addWidget(QLabel("Category:"))
            self.manage_category = ArrowCombo()
            self.manage_category.setEditable(True)
            self.manage_category.lineEdit().setPlaceholderText(
                "pick existing or type new")
            row.addWidget(self.manage_category, 1)
            btn_cat = QPushButton("Apply Category")
            btn_cat.setObjectName("btn_primary")
            btn_cat.clicked.connect(self._apply_category)
            row.addWidget(btn_cat)
            row.addStretch(1)
            btn_del = QPushButton("Delete Selected")
            btn_del.clicked.connect(self._delete_selected)
            row.addWidget(btn_del)
            gl.addLayout(row)
            root.addWidget(box, 1)

            self.manage_log = QTextEdit()
            self.manage_log.setReadOnly(True)
            self.manage_log.setMaximumHeight(120)
            root.addWidget(self.manage_log)
            return tab

        def _mlog(self, msg, color=None):
            self.manage_log.append(
                '<span style="color:%s">%s</span>' % (color, msg) if color else msg)

        def _refresh_manage(self):
            self.tbl_manage.setRowCount(len(self._fields))
            for r, field in enumerate(self._fields):
                self.tbl_manage.setItem(r, 0, QTableWidgetItem(field["name"]))
                type_item = QTableWidgetItem(field["type"])
                type_item.setForeground(QColor(_TYPE_COLOR.get(field["type"], C_TX0)))
                self.tbl_manage.setItem(r, 1, type_item)
                cat_item = QTableWidgetItem(field.get("category", "Default"))
                cat_item.setForeground(QColor(C_TX2))
                self.tbl_manage.setItem(r, 2, cat_item)
            self._fit_columns(self.tbl_manage, stretch_col=0)
            typed = self.manage_category.currentText()
            self.manage_category.clear()
            for cat in sorted({f.get("category", "Default") for f in self._fields}):
                self.manage_category.addItem(cat)
            self.manage_category.setCurrentText(typed)
            if not typed:
                self.manage_category.setCurrentIndex(-1)

        def _manage_selected(self):
            rows = self.tbl_manage.selectionModel().selectedRows()
            return [self._fields[r.row()]["name"]
                    for r in sorted(rows, key=lambda x: x.row())]

        def _apply_category(self):
            names = self._manage_selected()
            cat = self.manage_category.currentText().strip()
            if not self._wbp_path:
                self._mlog("Load a Widget Blueprint first.", C_WARN)
                return
            if not names or not cat:
                self._mlog("Select field(s) and pick/type a category.", C_WARN)
                return
            try:
                set_fields_category(self._wbp_path, names, cat)
            except Exception as exc:
                self._mlog("Failed: %s" % exc, C_ERR)
                return
            self._mlog("Moved %d field(s) to '%s'." % (len(names), cat), C_OK)
            self._load()

        def _delete_selected(self):
            names = self._manage_selected()
            if not self._wbp_path or not names:
                self._mlog("Select field(s) to delete.", C_WARN)
                return
            res = QMessageBox.question(
                self, "Delete Verse fields",
                "Permanently delete %d field(s)?\n\n%s\n\nBindings sourced from "
                "them are removed too, and the asset is compiled and saved."
                % (len(names), "\n".join(names)),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if res != QMessageBox.StandardButton.Yes:
                return
            try:
                r = delete_verse_fields(self._wbp_path, names)
            except Exception as exc:
                self._mlog("Delete failed: %s" % exc, C_ERR)
                return
            for n in r["deleted"]:
                self._mlog("  deleted  %s" % n, C_OK)
            for n in r["failed"]:
                self._mlog("  FAILED   %s (still present)" % n, C_ERR)
            if r["bindings_dropped"]:
                self._mlog("Also removed %d binding(s) sourced from deleted "
                           "fields." % r["bindings_dropped"], C_TX2)
            self._load()

        @staticmethod
        def _stepper(spinbox):
            """Wrap a QSpinBox in a [-] value [+] horizontal stepper layout.

            The native spin arrows are hidden (see stylesheet); these big buttons
            step the value and are easy to hit. Returns a QHBoxLayout to add.
            """
            spinbox.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            spinbox.setAlignment(Qt.AlignmentFlag.AlignCenter)
            spinbox.setMinimumWidth(48)
            lay = QHBoxLayout()
            lay.setSpacing(3)
            minus = QPushButton("−")   # minus sign
            minus.setObjectName("stepper")
            minus.clicked.connect(spinbox.stepDown)
            plus = QPushButton("+")
            plus.setObjectName("stepper")
            plus.clicked.connect(spinbox.stepUp)
            lay.addWidget(minus)
            lay.addWidget(spinbox)
            lay.addWidget(plus)
            return lay

        @staticmethod
        def _setup_table(table, multi=False):
            table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            table.setSelectionMode(
                QAbstractItemView.SelectionMode.ExtendedSelection if multi
                else QAbstractItemView.SelectionMode.SingleSelection)
            table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
            table.setAlternatingRowColors(True)
            table.verticalHeader().setVisible(False)
            table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            # Never scroll sideways: clicking a cell in a too-wide table makes Qt
            # scrollTo() that cell, yanking the view right. Fit columns to the
            # viewport and disable horizontal auto-scroll instead.
            table.setAutoScroll(False)
            table.setHorizontalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
            table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            table.horizontalHeader().setStretchLastSection(True)
            table.setWordWrap(False)

        def _refresh_target_combo(self):
            """Bulk target list: engine properties plus the loaded widget's child fields."""
            self.target_combo.clear()
            for cls in sorted(_BULK_PROPS):
                for prop in _BULK_PROPS[cls]:
                    self.target_combo.addItem("%s · %s" % (cls, prop),
                                              ("native", cls, prop))
            for name in sorted({f["name"] for c in self._child_widgets
                                for f in c["fields"]}):
                self.target_combo.addItem("Sub-widget · %s" % name, ("child", name))

        def _refresh_bulk_suggestions(self):
            """Fill both pattern dropdowns with families detected from real names."""
            self.field_pattern.clear()
            for pat, _n in detect_patterns([f["name"] for f in self._fields]):
                self.field_pattern.addItem(pat)
            self.widget_pattern.clear()
            names = ([w["name"] for w in self._widgets]
                     + [c["name"] for c in self._child_widgets])
            for pat, _n in detect_patterns(names):
                self.widget_pattern.addItem(pat)
            # Walk the detected field families until one actually pairs with a
            # widget pattern + target (the largest family may be un-bulk-bindable,
            # e.g. textures with no direct engine target).
            for i in range(self.field_pattern.count()):
                self.field_pattern.setCurrentIndex(i)
                if self._auto_pick_bulk_target():
                    break
            else:
                if self.field_pattern.count():
                    self.field_pattern.setCurrentIndex(0)

        def _auto_pick_bulk_target(self, _index=None):
            """Auto-select the widget pattern + target that pair best with the
            chosen field pattern — brute force over the few candidates.

            Returns True when a pairing was found. `_index` is the activated()
            signal's payload: an int when the USER picked a pattern (log failures),
            None when called programmatically during load (stay quiet).
            """
            fpat = self.field_pattern.currentText().strip()
            if not fpat:
                return False
            wpats = [self.widget_pattern.itemText(i)
                     for i in range(self.widget_pattern.count())]
            if not wpats:
                wpats = [self.widget_pattern.currentText().strip()]
            best = None   # (pair_count, widget_pattern, target_combo_index)
            for i in range(self.target_combo.count()):
                data = self.target_combo.itemData(i)
                for wpat in wpats:
                    if not wpat:
                        continue
                    if data[0] == "child":
                        pairs, _ = match_child_by_suffix(
                            self._fields, self._child_widgets, fpat, wpat, data[1])
                    else:
                        pairs, _ = match_by_suffix(
                            self._fields, self._widgets, fpat, wpat, data[2])
                    if pairs and (best is None or len(pairs) > best[0]):
                        best = (len(pairs), wpat, i)
            if best:
                self.widget_pattern.setCurrentText(best[1])
                self.target_combo.setCurrentIndex(best[2])
                self._log("Auto-detected: %s  →  %s . %s   (%d pairs — "
                          "Preview Matches to confirm)"
                          % (fpat, best[1], self.target_combo.currentText(), best[0]),
                          C_TX2)
                return True
            if _index is not None:   # user picked this pattern — tell them it's dry
                self._log("No widget pattern/target pairs up with '%s' — "
                          "set them manually." % fpat, C_TX2)
            return False

        def _log(self, msg, color=None):
            self.log.append('<span style="color:%s">%s</span>' % (color, msg) if color else msg)
            bar = self.log.verticalScrollBar()
            bar.setValue(bar.maximum())
            unreal.log("[VerseBinder] %s" % msg)
            QApplication.processEvents()

        # ── data ────────────────────────────────────────────────────────────
        @staticmethod
        def _widget_name(path):
            """/Game/UI/WBP_Thing.WBP_Thing -> WBP_Thing (the object name)."""
            return path.rsplit(".", 1)[-1].rsplit("/", 1)[-1]

        def _from_selection(self):
            for asset in unreal.EditorUtilityLibrary.get_selected_assets():
                if isinstance(asset, unreal.WidgetBlueprint):
                    self._load(asset.get_path_name())
                    return
            self._log("No WidgetBlueprint selected in the Content Browser.", C_WARN)

        def _load(self, path=None):
            # Load `path` if given (From Selection); otherwise resolve whatever is
            # typed in the field — a full path, or a bare widget name we search for.
            typed = self.path_edit.text().strip()
            if path is None:
                path = typed
                if path and "/" not in path:            # bare name -> find the asset
                    found = _resolve_widget_name(path)
                    if found:
                        path = found
                    else:
                        self._log("No Widget Blueprint named '%s' found." % path, C_ERR)
                        return
            if not path:
                return
            # Child fields can change between loads (fields added to the child
            # widget); the cache is only valid within a single load.
            _child_fields_cache.clear()
            try:
                fields = list_verse_fields(path)
                entries = _widget_tree_entries(path)   # one T3D export for both
                widgets = list_widgets(path, entries)
                children = list_child_widgets(path, entries)
            except Exception as exc:
                self._log("Load failed: %s" % exc, C_ERR)
                return
            self._wbp_path, self._fields = path, fields
            self._widgets, self._child_widgets = widgets, children
            # Show just the widget name now that the full path is remembered.
            self.path_edit.setText(self._widget_name(path))
            self.log.clear()
            if not fields:
                self._log("No Verse fields on this widget.", C_WARN)
            self._log("Loaded %d Verse field(s), %d engine widget(s), %d sub-widget(s)."
                      % (len(fields), len(widgets), len(children)), C_OK)
            self._refresh_target_combo()
            self._refresh_bulk_suggestions()
            self._refresh_category_combo()
            self._refresh_category_filter()
            self._refresh_fields()
            self._refresh_targets()
            self._refresh_manage()

        def _refresh_category_combo(self):
            """Offer the widget's existing variable categories in the Create tab.

            No blank entry — the placeholder already means "no category"; the list
            is just the real categories present on the widget.
            """
            typed = self.create_category.currentText()
            self.create_category.clear()
            for cat in sorted({f.get("category", "Default") for f in self._fields}):
                self.create_category.addItem(cat)
            # Keep the field empty unless the user had typed/picked something —
            # an editable combo shows item 0 otherwise, defeating the placeholder.
            self.create_category.setCurrentText(typed)
            if not typed:
                self.create_category.setCurrentIndex(-1)

        def _bound_map(self):
            return {(b["widget"], b["prop"]): (b["source"], b["has_conversion"])
                    for b in existing_bindings(self._wbp_path)}

        def _refresh_category_filter(self):
            """Rebuild the category filter, keeping the current choice if possible."""
            keep = self.category_filter.currentText()
            self.category_filter.blockSignals(True)
            self.category_filter.clear()
            self.category_filter.addItem("All")
            for cat in sorted({f.get("category", "Default") for f in self._fields}):
                self.category_filter.addItem(cat)
            idx = self.category_filter.findText(keep)
            self.category_filter.setCurrentIndex(idx if idx >= 0 else 0)
            self.category_filter.blockSignals(False)

        def _refresh_fields(self):
            cat = self.category_filter.currentText()
            self._visible_fields = [f for f in self._fields
                                    if cat == "All" or f.get("category", "Default") == cat]
            self.tbl_fields.setRowCount(len(self._visible_fields))
            for r, field in enumerate(self._visible_fields):
                self.tbl_fields.setItem(r, 0, QTableWidgetItem(field["name"]))
                type_item = QTableWidgetItem(field["type"])
                type_item.setForeground(QColor(_TYPE_COLOR.get(field["type"], C_TX0)))
                self.tbl_fields.setItem(r, 1, type_item)
                cat_item = QTableWidgetItem(field.get("category", "Default"))
                cat_item.setForeground(QColor(C_TX2))
                self.tbl_fields.setItem(r, 2, cat_item)
            self._fit_columns(self.tbl_fields, stretch_col=0)

        @staticmethod
        def _fit_columns(table, stretch_col):
            """Size columns to fit the viewport with no horizontal overflow.

            Non-stretch columns hug their content; `stretch_col` absorbs the rest.
            This keeps the table exactly as wide as its viewport, so a cell click
            never triggers a sideways scrollTo().
            """
            header = table.horizontalHeader()
            for c in range(table.columnCount()):
                mode = (QHeaderView.ResizeMode.Stretch if c == stretch_col
                        else QHeaderView.ResizeMode.ResizeToContents)
                header.setSectionResizeMode(c, mode)

        def _selected_field(self):
            fields = self._selected_fields()
            return fields[0] if fields else None

        def _selected_fields(self):
            # Index the FILTERED list — table rows map to _visible_fields.
            rows = self.tbl_fields.selectionModel().selectedRows()
            return [self._visible_fields[r.row()]
                    for r in sorted(rows, key=lambda x: x.row())]

        def _refresh_targets(self):
            """Rebuild the target table for the (first) selected field + mode.

            self._target_rows[i] carries what _row_to_pair needs, plus display state:
              native: {kind:'native', widget, native, prop, dest, bindable, src, has_conv}
              child:  {kind:'child',  widget, native, class_path, child_field, dest,
                       bindable, src, has_conv}
            """
            field = self._selected_field()
            self.tbl_targets.setRowCount(0)
            self._target_rows = []
            if not field or not self._wbp_path:
                return

            bound = self._bound_map()
            rows = []
            # Engine widget properties…
            for widget in self._widgets:
                for prop in sorted(widget_bindable_properties(widget["native"])):
                    direct = is_direct_bindable(field["type"], widget["native"], prop)
                    # Show a conversion-only prop LOCKED, but only for a field
                    # type that could actually target it (texture/material→Brush).
                    needs_conv = field["type"] in _CONVERSION_PROPS.get(prop, ())
                    if not direct and not needs_conv:
                        continue
                    src, has_conv = bound.get((widget["name"], prop), (None, False))
                    rows.append({"kind": "native", "widget": widget["name"],
                                 "native": widget["native"], "prop": prop,
                                 "dest": prop, "bindable": direct,
                                 "src": src, "has_conv": has_conv})
            # …and sub-widget Verse fields (exact type match only), together.
            for child in self._child_widgets:
                for cf in child["fields"]:
                    if cf["type"] != field["type"]:
                        continue
                    src, has_conv = bound.get((child["name"], cf["name"]), (None, False))
                    rows.append({"kind": "child", "widget": child["name"],
                                 "native": child["child_wbp_path"].rsplit("/", 1)[-1],
                                 "class_path": child["class_path"],
                                 "child_field": cf["name"], "dest": cf["name"],
                                 "bindable": True, "src": src, "has_conv": has_conv})

            self._target_rows = rows
            self.tbl_targets.setRowCount(len(rows))
            for r, row in enumerate(rows):
                self.tbl_targets.setItem(r, 0, QTableWidgetItem(row["widget"]))
                self.tbl_targets.setItem(r, 1, QTableWidgetItem(row["native"]))
                prop_item = QTableWidgetItem(row["dest"])
                if not row["bindable"]:
                    prop_item.setForeground(QColor(C_TX2))
                self.tbl_targets.setItem(r, 2, prop_item)

                if not row["bindable"]:
                    item = QTableWidgetItem("needs conversion — do in editor UI")
                    item.setForeground(QColor(C_TX2))
                elif row["has_conv"]:
                    item = QTableWidgetItem("%s (conversion — locked)" % (row["src"] or "?"))
                    item.setForeground(QColor(C_WARN))
                elif row["src"] == field["name"]:
                    item = QTableWidgetItem("← this field")
                    item.setForeground(QColor(C_OK))
                elif row["src"]:
                    item = QTableWidgetItem(row["src"])
                    item.setForeground(QColor(C_WARN))
                else:
                    item = QTableWidgetItem("—")
                    item.setForeground(QColor(C_TX2))
                self.tbl_targets.setItem(r, 3, item)
            self._fit_columns(self.tbl_targets, stretch_col=3)

            if not rows:
                self._log("No bindable target for a `%s` field on this widget "
                          "(engine properties and sub-widget fields both checked)."
                          % field["type"], C_TX2)

        def _selected_target_rows(self):
            rows = self.tbl_targets.selectionModel().selectedRows()
            return [self._target_rows[r.row()]
                    for r in sorted(rows, key=lambda x: x.row())]

        @staticmethod
        def _row_to_pair(field_name, row):
            """A target-row dict + a field name -> an apply_bindings pair."""
            if row["kind"] == "child":
                return {"mode": "child", "field": field_name, "widget": row["widget"],
                        "class_path": row["class_path"], "child_field": row["child_field"]}
            return (field_name, row["widget"], row["native"], row["prop"])

        # ── actions ─────────────────────────────────────────────────────────
        def _bind_selected(self):
            """Bind ONE selected field to every selected target (fan-out)."""
            field = self._selected_field()
            targets = [t for t in self._selected_target_rows() if t["bindable"]]
            if not field or not targets:
                self._log("Select a field and at least one (unlocked) target.", C_WARN)
                return
            pairs = [self._row_to_pair(field["name"], t) for t in targets]
            self._run_apply(pairs)

        def _bind_selected_pairs(self):
            """Zip selected fields with selected targets 1:1, in row order."""
            fields = self._selected_fields()
            targets = [t for t in self._selected_target_rows() if t["bindable"]]
            if len(fields) != len(targets) or not fields:
                self._log("Select an EQUAL number of fields and unlocked targets "
                          "(%d field(s), %d target(s))." % (len(fields), len(targets)), C_WARN)
                return
            pairs = [self._row_to_pair(f["name"], t) for f, t in zip(fields, targets)]
            for f, t in zip(fields, targets):
                self._log("  pair  %s  →  %s.%s" % (f["name"], t["widget"], t["dest"]))
            self._run_apply(pairs)

        def _unbind_selected(self):
            targets = self._selected_target_rows()
            if not targets:
                self._log("Select target(s) to unbind.", C_WARN)
                return
            dests = [(t["widget"], t["dest"]) for t in targets]
            try:
                dropped = remove_bindings(self._wbp_path, dests)
            except Exception as exc:
                self._log("Unbind failed: %s" % exc, C_ERR)
                return
            self._log("Removed %d binding(s)." % dropped, C_OK)
            self._refresh_fields()
            self._refresh_targets()

        @staticmethod
        def _pair_label(pair):
            """(field, widget, dest) label for either a native tuple or child dict."""
            if isinstance(pair, dict):
                return pair["field"], pair["widget"], pair["child_field"]
            field, widget, _native, prop = pair
            return field, widget, prop

        def _preview_bulk(self):
            if not self._wbp_path:
                self._log("Load a Widget Blueprint first.", C_WARN)
                return
            target = self.target_combo.currentData()
            if target is None:
                self._log("Pick a target from the dropdown.", C_WARN)
                return
            if target[0] == "child":
                pairs, warnings = match_child_by_suffix(
                    self._fields, self._child_widgets,
                    self.field_pattern.currentText().strip(),
                    self.widget_pattern.currentText().strip(), target[1])
            else:
                pairs, warnings = match_by_suffix(
                    self._fields, self._widgets,
                    self.field_pattern.currentText().strip(),
                    self.widget_pattern.currentText().strip(), target[2])
            self.log.clear()
            for warning in warnings:
                self._log("  ! %s" % warning, C_WARN)
            if not pairs:
                self._log("No matching pairs.", C_ERR)
                self.btn_apply_bulk.setEnabled(False)
                self._pending_pairs = []
                return
            for pair in pairs:
                field, widget, dest = self._pair_label(pair)
                self._log("  %s  →  %s.%s" % (field, widget, dest))
            self._log("%d pair(s) ready. Click Bind All to apply." % len(pairs), C_OK)
            self._pending_pairs = pairs
            self.btn_apply_bulk.setEnabled(True)

        def _apply_bulk(self):
            if not self._pending_pairs:
                return
            self._run_apply(self._pending_pairs)
            self._pending_pairs = []
            self.btn_apply_bulk.setEnabled(False)

        def _run_apply(self, pairs):
            try:
                result = apply_bindings(self._wbp_path, pairs)
            except Exception as exc:
                self._log("Bind failed: %s" % exc, C_ERR)
                return
            for pair in result["created"]:
                self._log("  created   %s → %s.%s" % pair, C_OK)
            for pair in result["replaced"]:
                self._log("  replaced  %s → %s.%s" % pair, C_OK)
            for pair, reason in result["skipped"]:
                self._log("  skipped   %s → %s.%s   (%s)" % (pair + (reason,)), C_WARN)
            total = len(result["created"]) + len(result["replaced"])
            self._log("Done: %d bound, %d skipped." % (total, len(result["skipped"])),
                      C_OK if total else C_WARN)
            self._refresh_fields()
            self._refresh_targets()

        # ── create-fields tab ────────────────────────────────────────────────
        def _create_pairs(self):
            return expand_batch_spec(
                self.create_name.text(), self.create_type.currentText(),
                self.create_start.value(), self.create_count.value())

        def _preview_create(self):
            pairs, error = self._create_pairs()
            self.create_preview.clear()
            if error:
                self.create_preview.append('<span style="color:%s">%s</span>' % (C_WARN, error))
                if hasattr(self, "btn_create"):
                    self.btn_create.setEnabled(False)
                return
            existing = set()
            if self._wbp_path:
                existing = {f["name"] for f in self._fields}
            cat = self.create_category.currentText().strip()
            lines = ['<span style="color:%s">Will create %d field(s)%s:</span>'
                     % (C_TX2, len(pairs), (" in category '%s'" % cat) if cat else "")]
            for name, kind in pairs:
                if name in existing:
                    lines.append('<span style="color:%s">  %s : %s   (exists — skipped)</span>'
                                 % (C_WARN, name, kind))
                else:
                    lines.append("  %s : %s" % (name, kind))
            self.create_preview.append("\n".join(lines))
            if hasattr(self, "btn_create"):
                self.btn_create.setEnabled(True)

        def _run_create(self):
            if not self._wbp_path:
                self.create_preview.append(
                    '<span style="color:%s">Load a Widget Blueprint first.</span>' % C_WARN)
                return
            pairs, error = self._create_pairs()
            if error:
                self.create_preview.append('<span style="color:%s">%s</span>' % (C_ERR, error))
                return
            category = self.create_category.currentText().strip() or None
            try:
                result = create_verse_fields(self._wbp_path, pairs, category=category)
            except Exception as exc:
                self.create_preview.append(
                    '<span style="color:%s">Create failed: %s</span>' % (C_ERR, exc))
                return

            self.create_preview.clear()
            for name in result["created"]:
                self.create_preview.append(
                    '<span style="color:%s">  created  %s</span>' % (C_OK, name))
            for name in result["skipped_existing"]:
                self.create_preview.append(
                    '<span style="color:%s">  skipped  %s (already exists)</span>' % (C_WARN, name))
            self.create_preview.append(
                '<span style="color:%s">Done: %d created, all verified as Verse fields.</span>'
                % (C_OK, len(result["created"])))
            unreal.log("[VerseBinder] created %d field(s)" % len(result["created"]))
            # Refresh the Bind tab so the new fields show up immediately.
            self._load()

    # ═══════════════════════════════════════════════════════════════════════
    #  ENTRY POINT
    # ═══════════════════════════════════════════════════════════════════════

    def main():
        app = QApplication.instance() or QApplication(sys.argv)

        # ALWAYS re-apply the current stylesheet. The QApplication is shared and
        # persists across re-runs, so a previously-applied (older) style would
        # otherwise stick forever — re-running would never reflect edits. Force a
        # repolish so an already-open window restyles in place.
        app.setStyleSheet(STYLE)

        def _repolish(w):
            w.style().unpolish(w)
            w.style().polish(w)
            w.update()

        # The window ref must live on the persistent QApplication, NOT a module
        # global — each run execs this file in a fresh namespace, so a module
        # global is always None and every run would stack a duplicate window.
        win = getattr(app, "_verse_binder_win", None)
        if win is not None and win.isVisible():
            _repolish(win)
            for child in win.findChildren(QWidget):
                _repolish(child)
            win.raise_()
            win.activateWindow()
            return

        win = VerseBinderWindow()
        app._verse_binder_win = win   # also keeps the window alive (GC anchor)
        win.setWindowFlags(Qt.WindowType.Window)
        win.show()
        win.raise_()
        win.activateWindow()

        # Auto-load the Content Browser selection if it's a widget (field then
        # shows just its name). Otherwise the field starts empty — no default path.
        for asset in unreal.EditorUtilityLibrary.get_selected_assets():
            if isinstance(asset, unreal.WidgetBlueprint):
                win._load(asset.get_path_name())
                break

    main()
