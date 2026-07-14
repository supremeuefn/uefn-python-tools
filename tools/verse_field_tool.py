"""Verse Fields + Binding Tool — create, manage, and bulk-bind Verse fields via MVVM.

Standalone single-file tool: needs the editor's `unreal` module, the stdlib, and
PySide6 (auto-installed on first run — restart UEFN once after). Run inside UEFN:
    Tools > Execute Python Script...  ->  verse_field_tool.py

Binds Verse fields to ENGINE PROPERTIES (Text, ColorAndOpacity, ...), to
SUB-WIDGET FIELDS on embedded child widgets (same type only), or to button
EVENTS — one at a time, zipped in pairs, or in bulk by `#`-numbered patterns.
Bindings needing a conversion NODE (texture/material -> Brush) cannot be
authored (the subsystem has no setters for them); they are listed LOCKED.

HARD-WON DETAILS (each cost a crash or a corrupt asset)
    * MVVMBlueprintViewBinding.SourcePath is read-only; import_text() on the
      whole struct bypasses the guard, and the whole array must be written back
      (bindings are struct copies — also why remove_binding() silently no-ops).
    * MemberParent names the class DECLARING the property: the native class for
      engine props, the wrapped generated class for child fields; UWidget props
      omit it (bSelfContext=True). Child sources zero their MemberGuid — the
      compiler re-resolves by name.
    * UEFN exposes a reduced widget set; Text arrives via UEFN_TextBlock.
      ToolTipText is hidden on purpose (type-matches message but isn't visual).
    * Verse color structs bind DIRECTLY to SlateColor/LinearColor.
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
    import functools
    import os
    import re
    import shutil
    import struct
    import time
    import uuid

    import unreal
    from PySide6.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QPushButton, QTableWidget, QTableWidgetItem, QLabel, QLineEdit,
        QHeaderView, QTextEdit, QSplitter, QComboBox, QAbstractItemView,
        QGroupBox, QTabWidget, QSpinBox, QMessageBox, QAbstractSpinBox,
        QCheckBox,
    )
    from PySide6.QtCore import Qt, QPointF, QRectF, QTimer
    from PySide6.QtGui import (QColor, QPainter, QPen, QFont, QIntValidator,
                               QDoubleValidator)

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
    /* The indicator's BOX is styled here (background/border render fine); the
       tick INSIDE it is painted in code -- see TickBox -- because this Qt build
       ignores image/CSS-drawn glyphs on the subcontrol, the same limitation that
       forced ArrowCombo. Without a box the tick floats on the panel with nothing
       around it and reads as decoration rather than a control. */
    QCheckBox {{ color: {C_TX0}; font-size: 11px; spacing: 7px; }}
    QCheckBox::indicator {{
        width: 15px; height: 15px; border-radius: 3px;
        background-color: {C_INPUT}; border: 1px solid {C_BTN};
    }}
    QCheckBox::indicator:hover {{ border-color: {C_BTNOUT}; }}
    QCheckBox::indicator:checked {{
        background-color: {C_ACC}; border: 1px solid {C_ACC};
    }}
    QCheckBox::indicator:checked:hover {{
        background-color: {C_AH}; border: 1px solid {C_AH};
    }}
    QCheckBox:disabled {{ color: {C_TX2}; }}
    QCheckBox::indicator:disabled {{
        background-color: {C_RECESS}; border: 1px solid {C_BTN};
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

    class TickBox(QCheckBox):
        """QCheckBox that PAINTS its own tick.

        Styling ::indicator (needed at all, or the native box is invisible on this
        dark theme) makes Qt stop drawing the tick with it -- leaving a filled
        accent square and no checkmark. Same build limitation as ArrowCombo's
        arrow, same fix: draw the glyph in paintEvent, where it always shows.
        """
        def paintEvent(self, event):
            super().paintEvent(event)
            if not self.isChecked():
                return
            p = QPainter(self)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            pen = QPen(QColor("#ffffff" if self.isEnabled() else C_TX2))
            pen.setWidthF(2.0)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            p.setPen(pen)
            # Centre the tick in the 15px indicator the stylesheet draws at the
            # left edge; the box is vertically centred in the widget.
            cx, cy = 7.5, self.height() / 2
            p.drawPolyline([QPointF(cx - 3.5, cy),
                            QPointF(cx - 1.0, cy + 3.0),
                            QPointF(cx + 3.5, cy - 3.0)])
            p.end()

    # Sort states for SortHeader, cycled in this order.
    SORT_NONE, SORT_ASC, SORT_DESC = 0, 1, 2

    class SortHeader(QHeaderView):
        """Header that PAINTS a 3-state sort indicator on one column.

        Clicking the column cycles neutral -> A-Z -> Z-A -> neutral and invokes
        `on_change(state)`, which must re-sort the table's BACKING LIST. Qt's own
        setSortingEnabled() only reorders the view, which would desync the
        row->field mapping that binding and deletion rely on.

        Like ArrowCombo, the glyph is painted rather than styled: this Qt build
        does not render CSS/image arrows on header subcontrols.
        """
        def __init__(self, column, on_change, parent=None):
            super().__init__(Qt.Orientation.Horizontal, parent)
            self._column = column
            self._on_change = on_change
            self.state = SORT_NONE
            self.setSectionsClickable(True)
            self.sectionClicked.connect(self._clicked)

        def _clicked(self, index):
            if index != self._column:
                return
            self.state = (self.state + 1) % 3
            self._on_change(self.state)
            self.viewport().update()

        def reset_state(self):
            """Return to neutral without firing the callback (e.g. on reload)."""
            self.state = SORT_NONE
            self.viewport().update()

        def paintSection(self, painter, rect, index):
            super().paintSection(painter, rect, index)
            if index != self._column:
                return
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            active = self.state != SORT_NONE
            color = QColor(C_ACC if active else C_TX2)
            # Classic A-over-Z sort glyph + a direction arrow, pinned to the far
            # right of the section. Z-A flips the letters to Z-over-A. Painted
            # (not styled) so it renders on this Qt build.
            arrow_x = rect.right() - 11
            letters_cx = arrow_x - 11
            cy = rect.center().y()

            # Stacked letters
            top, bot = ("Z", "A") if self.state == SORT_DESC else ("A", "Z")
            f = QFont(self.font())
            f.setPixelSize(9)
            f.setBold(True)
            painter.setFont(f)
            painter.setPen(color)
            painter.drawText(QRectF(letters_cx - 6, cy - 10, 12, 10),
                             Qt.AlignmentFlag.AlignCenter, top)
            painter.drawText(QRectF(letters_cx - 6, cy, 12, 10),
                             Qt.AlignmentFlag.AlignCenter, bot)

            # Down arrow (shaft + head) beside the letters
            pen = QPen(color)
            pen.setWidthF(1.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawLine(QPointF(arrow_x, cy - 6), QPointF(arrow_x, cy + 6))
            painter.drawPolyline([QPointF(arrow_x - 3, cy + 2),
                                  QPointF(arrow_x, cy + 6),
                                  QPointF(arrow_x + 3, cy + 2)])
            painter.restore()

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
    # Verified empirically. int -> float is deliberately absent: the view stores
    # an int->RenderOpacity binding but it FAILS at compile ("a conversion
    # function is required"), and Conv_* nodes cannot be authored from Python.
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

    # Events a Verse field may bind on a button: (label, candidate serialized
    # names). The two button shapes name the SAME event differently — UEFN
    # buttons use OnButtonBaseClicked/OnButtonCTA*, the native Custom Button
    # uses OnButtonClicked/OnButton* — so each label carries candidates and the
    # first one the class actually declares wins. Deliberately an allowlist:
    # buttons declare many more delegates (Hovered, Selected, drag/drop, ...).
    _EVENT_DELEGATES = (
        ("On Clicked",     ("OnButtonBaseClicked", "OnButtonClicked", "OnClicked")),
        ("On Highlight",   ("OnButtonCTAHighlight", "OnButtonHighlight")),
        ("On Unhighlight", ("OnButtonCTAUnhighlight", "OnButtonUnhighlight")),
    )

    _widget_delegates_cache = {}

    def _declares_delegate(cdo, name):
        """Does this class really declare `name` as a multicast delegate?

        Reflected as snake_case on the CDO (`on_button_base_clicked`). A real delegate
        reads back as a delegate object; an unbound UWidget python method reads back as
        `builtin_function_or_method_with_closure` -- that is the test.
        """
        snake = "_".join(w.lower() for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]*", name))
        value = getattr(cdo, snake, None)
        if value is None:
            return False
        return type(value).__name__ != "builtin_function_or_method_with_closure"

    def widget_event_delegates(class_path):
        """[(serialized_name, label)] an event binding can target on this widget class.

        Gate on the DELEGATE, never on class names: binding a delegate the class
        does not declare makes the engine dereference garbage — an
        EXCEPTION_ACCESS_VIOLATION that kills the editor (listing
        OnButtonBaseClicked on every Image/TextBlock did exactly that).
        """
        if class_path in _widget_delegates_cache:
            return _widget_delegates_cache[class_path]
        found = []
        try:
            cls = unreal.load_object(None, class_path)
            cdo = unreal.get_default_object(cls) if cls is not None else None
            if cdo is not None:
                for label, candidates in _EVENT_DELEGATES:
                    for name in candidates:
                        if _declares_delegate(cdo, name):
                            found.append((name, label))
                            break     # first match wins; never list one event twice
        except Exception:
            found = []
        _widget_delegates_cache[class_path] = found
        return found

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
        """[{name, type, ue5_class, category, param}] — types from the engine's
        own VerseClassFields tag, category from the Blueprint variable itself.

        `param` is the event field's parameter (int/float/logic) or None; it is
        always None for a non-event field.
        """
        asset = _el.find_asset_data(wbp_path)
        blob = str(asset.get_tag_value("VerseClassFields") or "")
        wbp = _el.load_asset(wbp_path)
        fields = []
        pattern = (r'\(Name="([^"]+)".*?Type=([A-Za-z]+),TypeUE5Class="([^"]*)"'
                   r'.*?EventParameters=(\([^)]*\)\)|)')
        for name, raw_type, ue5_class, params in re.findall(pattern, blob):
            kind = _VERSE_TYPE.get(raw_type, raw_type.lower())
            if kind == "struct":
                if ue5_class.endswith("Colors_color_alpha"):
                    kind = "color_alpha"
                elif ue5_class.endswith("Colors_color"):
                    kind = "color"
            elif kind == "asset":
                kind = "texture" if "Texture2D" in ue5_class else "material"
            # ((Type=Integer)) -> "int". The engine spells the parameter with the
            # same Verse type names it uses for a field's own Type.
            param = None
            if kind == "event" and params:
                inner = re.search(r'Type=([A-Za-z]+)', params)
                if inner:
                    param = _VERSE_TYPE.get(inner.group(1), inner.group(1).lower())
            try:
                category = str(_bel.get_blueprint_variable_category(wbp, name))
            except Exception:
                category = "Default"
            fields.append({"name": name, "type": kind, "ue5_class": ue5_class,
                           "category": category, "param": param})
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
        with open(path, "rb") as handle:
            raw_bytes = handle.read()
        if raw_bytes.startswith(b'\xff\xfe') or b'\x00' in raw_bytes[:100]:
            text = raw_bytes.decode('utf-16le', errors='ignore')
        else:
            text = raw_bytes.decode('utf-8', errors='ignore')
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

    def list_event_widgets(wbp_path, entries=None):
        """[{name, class_path, native, delegates}] for widgets an EVENT can bind.

        Keeps a widget only if its class really declares the delegates — the
        buttons — and excludes everything else by construction (offering a
        delegate on an Image crashed the editor; see widget_event_delegates).
        """
        widgets, seen = [], set()
        for class_path, name in (entries if entries is not None
                                 else _widget_tree_entries(wbp_path)):
            if name in seen:
                continue
            delegates = widget_event_delegates(class_path)
            if not delegates:
                continue
            seen.add(name)
            # `native` is the LEAF class name (UEFN_Button_Quiet_C) and must stay
            # exact: MemberParent resolves the delegate's declaring class through
            # it. `label`/`display` are cosmetic.
            native = class_path.rsplit(".", 1)[-1].rstrip("'")
            widgets.append({"name": name, "class_path": class_path,
                            "native": native,
                            "display": _widget_display_name(name, native),
                            "label": _widget_class_label(native),
                            "delegates": delegates})
        return widgets

    # Engine class name -> what the editor calls it. Display only.
    _WIDGET_CLASS_LABELS = {
        "UIFrameworkCustomButtonWidget": "Custom Button",
        "UEFN_Button_Quiet_C": "UEFN Button Quiet",
        "UEFN_Button_Regular_C": "UEFN Button Regular",
        "UEFN_Button_Loud_C": "UEFN Button Loud",
    }

    def _widget_class_label(native):
        """Friendly class name for the target table; falls back to the real one."""
        return _WIDGET_CLASS_LABELS.get(native, native)

    def _widget_display_name(name, native):
        """What the editor's Hierarchy shows: an auto-generated object name
        (class + serial, e.g. UIFrameworkCustomButtonWidget_53) displays as the
        prettified class; a renamed widget shows its own name. Display only —
        bindings key on the object name."""
        stem = re.sub(r"_\d+$", "", name)
        if stem == native or stem + "_C" == native:
            return _widget_class_label(native)
        return name

    def variable_guids(wbp_path):
        """Member VarName -> VarGuid, from the asset's own serialization.

        Anchored to NewVariables(N)= so function-LOCAL variables (which also
        serialize VarName/VarGuid) are excluded — counting them would break the
        NewVariables memory probe, which needs an exact element count.
        """
        wbp = _el.load_asset(wbp_path)
        text = _export_t3d(wbp)
        return dict(re.findall(
            r'NewVariables\(\d+\)=\(VarName="([^"]+)",VarGuid=([A-F0-9]{32})', text))

    # ── child sub-widget discovery ───────────────────────────────────────────
    # A parent field can bind a Verse field on an EMBEDDED child instance
    # (VF_Slot1Image -> Slot1.VF_SlotImage): MemberParent = the child's
    # GENERATED class, Source=Widget. Same-type only; no conversion needed.

    _child_fields_cache = {}

    def _class_path_to_asset(class_path):
        """Generated-class path -> the child WBP object path:
        "/NewTesting/WC_Slot.WC_Slot_C" -> "/NewTesting/WC_Slot.WC_Slot"."""
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

    # Whether the widget's editor tab was open, keyed by path. Set from
    # close_all_editors_for_asset's return count, consumed by _reopen_editor.
    _WAS_EDITOR_OPEN = {}

    # Set by the UI to _reclaim_focus: reopening the widget raises UEFN, so the
    # tool has to take the foreground back. A plain list so the engine-side code
    # stays importable without Qt.
    _ON_EDITOR_RAISED = []

    # Non-zero while a guarded op runs, so a mid-operation _reload does not put
    # the tab back only for the next pass to close it again (visible flicker). A
    # count, not a flag: guarded calls nest.
    _EDITOR_REOPEN_HELD = [0]

    def _keeps_editor_open(operation):
        """Restore the widget's editor tab (and the tool's focus) after a mutating
        operation, on the failure path too.

        Whether the tab closes depends on the DATA, not the function -- deleting a
        plain field leaves it alone, deleting an EVENT field removes a graph and
        closes it -- so every mutating entry point is wrapped rather than the ones
        that look like they need it.

        Nothing is probed up front: the only way to ask whether a tab is open is
        to close it. Instead the code that actually closes it (_unload,
        _close_editor) records that in _WAS_EDITOR_OPEN, and an operation that
        never closes the tab leaves nothing to restore.
        """
        @functools.wraps(operation)
        def wrapper(wbp_path, *args, **kwargs):
            _EDITOR_REOPEN_HELD[0] += 1
            try:
                return operation(wbp_path, *args, **kwargs)
            finally:
                _EDITOR_REOPEN_HELD[0] -= 1
                _reopen_editor(wbp_path)
                for callback in _ON_EDITOR_RAISED:
                    try:
                        callback()
                    except Exception:
                        pass    # focus is cosmetic; never mask the real result
        return wrapper

    @_keeps_editor_open
    def apply_bindings(wbp_path, pairs, replace=True):
        """Create bindings; pairs are native tuples or child dicts (_normalize_pair).

        A destination holds at most one binding. replace=True retargets an
        existing one REUSING its BindingId (a fresh GUID reads as one binding
        vanishing and another appearing); conversions are never clobbered.
        Returns {created, replaced, skipped}.
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

    @_keeps_editor_open
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

    # ═══════════════════════════════════════════════════════════════════════
    #  VERSE event() BINDINGS  (patched on disk)
    # ═══════════════════════════════════════════════════════════════════════
    #
    # The engine API cannot author these: import_text survives a save but the next
    # compile discards it. Patching the saved package works -- creating = cloning a
    # working event's export, retargeting its FNames, and listing the copy in the
    # view's Events array. Growing the package is safe only if every derived
    # structure grows with it (each found via a distinct engine failure): every
    # summary offset (zero = absent, never shift), the int64 heading the
    # AssetRegistry blob, per-entry thumbnail offsets, the depends table, the
    # FGenerationInfo counts, and the Events ArrayProperty tag's byte size.

    _PACKAGE_TAG = 0x9E2A83C1

    # A NameProperty's tag is fixed-size, so its FName value sits +25 from the
    # tag: [+0 name][+8 "NameProperty"][+16 ArrayIndex][+20 size][+24 flags]
    # [+25 value]. The stream is NOT int32-aligned; values are only reachable
    # from a located tag, never by strided scan.
    _NAME_VALUE_GAP = 25

    # The Events ArrayProperty tag also names its element type, so the element
    # count sits +37 from the tag, with the tag's byte-size field 5 before it.
    _ARRAY_COUNT_GAP = 37

    # A binding's "Param 0" is TEXT in a DefaultString StrProperty, nested in the
    # export's SavedPins array (absent on a parameterless event):
    #   SavedPins (Array) -> [0] MVVMBlueprintPin (Struct) -> DefaultString
    # Resizing the string must also resize BOTH enclosing sizes and the export's
    # SerialSize; miss one and the loader asserts ("Read 646B, expected 645B").
    _PIN_ARRAY_TAG = "SavedPins"
    _PIN_DEFAULT_TAG = "DefaultString"

    # Simple tag: [+0 name][+8 type][+16 idx][+20 size][+24 flags][+25 value].
    _PROPERTY_SIZE_FIELD = 20

    # An ARRAY-of-structs tag also names its inner type and the struct, so its size
    # lands at +56, NOT +20 (+20 reads as 249 -- the mistake that crashed UEFN).
    _ARRAY_SIZE_FIELD = 56

    # What an untouched "Param 0" box holds, spelled as the engine spells it.
    _EVENT_PARAM_DEFAULTS = {"int": "0", "float": "0.0", "logic": "false"}

    # The SavedPins block as a package-INDEPENDENT recipe, so a pin can be BUILT
    # rather than cloned from a donor the widget may not have (measured: the block
    # is the same for every widget and param type -- only the value and the two
    # sizes differ). Ops, not bytes, because its FNames are per-package indices:
    #     bare token -> FName ("Name" or "Name#number"), resolved per package
    #     hex token  -> literal bytes (sizes, counts, flags)
    # No name here is valid hex, so the two never collide.
    _PIN_TEMPLATE = (
        "SavedPins ArrayProperty 01000000 StructProperty 01000000 "
        "MVVMBlueprintPin "
        "010000000500000000000000000000008502000000010000009700000000000000 "
        "StructProperty 01000000 MVVMBlueprintPinId "
        "010000000500000000000000000000003900000000 PinNames ArrayProperty "
        "01000000 NameProperty 000000000c0000000001000000 Param0 None Path "
        "StructProperty 01000000 MVVMBlueprintPropertyPath "
        "010000000500000000000000000000002201000000 Paths ArrayProperty "
        "01000000 StructProperty 01000000 MVVMBlueprintFieldPath "
        "01000000050000000000000000000000040000000000000000 WidgetName "
        "NameProperty 000000000800000000 None ContextId StructProperty "
        "01000000 Guid 010000 Type "
        "0000000000100000000800000000000000000000000000000000 Source "
        "EnumProperty 02000000 EMVVMBlueprintFieldPathSource "
        "01000000050000000000000000000000 ByteProperty "
        "0000000008000000006f00000000000000 bIsComponent BoolProperty "
        "000000000000000000 bDeprecatedSource BoolProperty 000000000000000000 "
        "None DefaultString StrProperty 000000000600000000020000003000 "
        "DefaultText TextProperty 00000000090000000000000000ff00000000 "
        "DefaultObject ObjectProperty 00000000040000000000000000 bSplit "
        "BoolProperty 000000000000000000 Status EnumProperty 02000000 "
        "EMVVMBlueprintPinStatus 01000000050000000000000000000000 "
        "ByteProperty 0000000008000000007300000000000000 None")

    # The template is emitted with the int default ("0"), so the block it builds
    # is the int binding's byte for byte. _build_pin_block then rewrites the value
    # for whatever type is wanted -- proven to reproduce the float and logic
    # bindings' blocks exactly.
    _PIN_TEMPLATE_VALUE = "0"

    # FObjectExport starts ClassIndex/SuperIndex/TemplateIndex/OuterIndex (int32
    # each), then ObjectName at +16, ObjectFlags, SerialSize/SerialOffset.
    _EXPORT_NAME_FIELD = 16

    # FObjectImport is a FIXED 40-byte record: ClassPackage, ClassName, OuterIndex,
    # then ObjectName at +20. Do NOT "derive" the stride by testing whether name
    # indices look valid — a smaller stride also passes and silently reads half
    # the table.
    _IMPORT_ENTRY_SIZE = 40
    _IMPORT_NAME_FIELD = 20

    # The UE object versions the summary parse branches on.
    _UE5_ADD_SOFTOBJECTPATH_LIST = 1008
    _UE5_METADATA_SERIALIZATION_OFFSET = 1014
    _UE5_VERSE_CELLS = 1015
    _UE5_PACKAGE_SAVED_HASH = 1016
    _UE5_IMPORT_TYPE_HIERARCHIES = 1018
    _UE4_ADD_STRING_ASSET_REFERENCES_MAP = 384
    _UE4_SERIALIZE_TEXT_IN_PACKAGES = 459
    _UE4_ADDED_SEARCHABLE_NAMES = 510
    _UE4_ADDED_LOCALIZATION_ID = 516

    def _crc_table(poly, reflected):
        table = []
        for i in range(256):
            if reflected:
                c = i
                for _ in range(8):
                    c = (c >> 1) ^ (poly if c & 1 else 0)
            else:
                c = i << 24
                for _ in range(8):
                    c = (((c << 1) ^ poly) if c & 0x80000000
                         else (c << 1)) & 0xFFFFFFFF
            table.append(c)
        return table

    _CRC_DEPRECATED = _crc_table(0x04C11DB7, reflected=False)
    _CRC_REFLECTED = _crc_table(0xEDB88320, reflected=True)

    def _strihash(text):
        """UE's Strihash_DEPRECATED — the case-insensitive half of a name's hash.

        UE upper-cases with TChar::ToUpper, which is ASCII-only, so str.upper()
        would hash accented names differently.
        """
        h = 0
        for ch in text:
            code = ord(ch)
            if 0 <= code - ord("a") < 26:
                ch = chr(code - 32)
            for byte in ch.encode("utf-8"):
                h = ((h >> 8) & 0x00FFFFFF) ^ _CRC_DEPRECATED[(h ^ byte) & 0xFF]
        return h & 0xFFFFFFFF

    def _strcrc32(text):
        """UE's StrCrc32 — the case-preserving half. Feeds 4 bytes per character."""
        crc = 0xFFFFFFFF
        for ch in text:
            code = ord(ch)
            for _ in range(4):
                crc = (crc >> 8) ^ _CRC_REFLECTED[(crc ^ (code & 0xFF)) & 0xFF]
                code >>= 8
        return (~crc) & 0xFFFFFFFF

    def _name_hashes(text):
        """The two uint16 hashes UE stores after each name-map entry.

        Verified against every name in a real UEFN package: 336/336 exact.
        """
        return struct.pack("<HH",
                           _strihash(text) & 0xFFFF, _strcrc32(text) & 0xFFFF)

    def _split_fname(text):
        """Inverse of fname(): "Foo_24" -> ("Foo", 25); "Foo" -> ("Foo", 0).

        A numeric tail with a leading zero ("Foo_05") is part of the literal
        name, matching how UE parses these.
        """
        head, sep, tail = text.rpartition("_")
        if sep and tail.isdigit() and not tail.startswith("0"):
            return head, int(tail) + 1
        if sep and tail == "0":
            return head, 1
        return text, 0

    class _PkgReader:
        """Little-endian cursor over the package bytes."""

        def __init__(self, data, pos=0):
            self.data = data
            self.pos = pos

        def i32(self):
            v = struct.unpack_from("<i", self.data, self.pos)[0]
            self.pos += 4
            return v

        def u32(self):
            v = struct.unpack_from("<I", self.data, self.pos)[0]
            self.pos += 4
            return v

        def i64(self):
            v = struct.unpack_from("<q", self.data, self.pos)[0]
            self.pos += 8
            return v

        def fstring(self):
            """UE FString: int32 length; negative means UTF-16. NUL-terminated."""
            n = self.i32()
            if n == 0:
                return ""
            if n < 0:
                raw = self.data[self.pos:self.pos + (-n) * 2]
                self.pos += (-n) * 2
                return raw.decode("utf-16-le").rstrip("\x00")
            raw = self.data[self.pos:self.pos + n]
            self.pos += n
            return raw.decode("utf-8", "replace").rstrip("\x00")

    def _read_engine_version(r):
        """FEngineVersion: major/minor/patch (uint16 each), changelist, branch."""
        r.pos += 6
        r.u32()
        r.fstring()

    class _Package:
        """A parsed UEFN (UE5.8) package that can retarget AND grow its events.

        The summary parse records where every stored file offset lives, because
        an insertion anywhere invalidates all of them at once.
        """

        def __init__(self, path):
            self.path = path
            with open(path, "rb") as handle:
                self.data = bytearray(handle.read())
            self.export_entry_size = None
            self._parse_summary()
            self._parse_name_map()
            self._parse_imports()
            self._parse_exports()

        def save(self, path=None):
            """Write the package out, to `path` if given, else where it came from.

            The event patchers read a pre-unload SNAPSHOT of the asset and write
            the result to the live file, so the two paths differ.
            """
            with open(path or self.path, "wb") as handle:
                handle.write(self.data)

        def _reparse(self):
            self._parse_summary()
            self._parse_name_map()
            self._parse_imports()
            self._parse_exports()

        # -- summary --------------------------------------------------------

        def _parse_summary(self):
            r = _PkgReader(self.data)
            if r.u32() != _PACKAGE_TAG:
                raise RuntimeError("not a .uasset (bad package tag)")
            if r.i32() != -9:
                raise RuntimeError("unsupported package version (expected UE5.8)")

            r.i32()                   # LegacyUE3Version
            self.ue4_version = r.i32()
            self.ue5_version = r.i32()
            r.i32()                   # FileVersionLicenseeUE
            if self.ue5_version < _UE5_IMPORT_TYPE_HIERARCHIES:
                raise RuntimeError(
                    "package predates UE5.8 (UE5 version %d)" % self.ue5_version)

            # UE5.8: FIoHash SavedHash + SectionSixOffset (doubles as header
            # size) precede the custom version container.
            r.pos += 20               # SavedHash
            self.section_six_offset_off = r.pos
            r.i32()
            # Two statements: `r.pos += r.i32() * 20` reads the OLD r.pos.
            n_custom = r.i32()
            r.pos += n_custom * 20    # custom versions: FGuid + int32 each
            r.fstring()               # FolderName
            r.u32()                   # PackageFlags

            # Remember WHERE each stored file offset lives, not just its value —
            # an insertion must correct every one that points past it.
            self.offset_fields = {}

            def offset_field(label):
                self.offset_fields[label] = r.pos
                return r.i32()

            self.name_count_off = r.pos
            self.name_count = r.i32()
            self.name_offset = offset_field("name")

            if self.ue5_version >= _UE5_ADD_SOFTOBJECTPATH_LIST:
                r.i32()
                offset_field("soft_object_paths")
            if self.ue4_version >= _UE4_ADDED_LOCALIZATION_ID:
                r.fstring()           # LocalizationId
            if self.ue4_version >= _UE4_SERIALIZE_TEXT_IN_PACKAGES:
                r.i32()
                offset_field("gatherable_text")

            self.export_count_off = r.pos
            self.export_count = r.i32()
            self.export_offset = offset_field("export")
            self.import_count = r.i32()
            self.import_offset = offset_field("import")

            if self.ue5_version >= _UE5_VERSE_CELLS:
                r.i32()
                offset_field("cell_export")
                r.i32()
                offset_field("cell_import")
            if self.ue5_version >= _UE5_METADATA_SERIALIZATION_OFFSET:
                offset_field("metadata")

            self.depends_offset = offset_field("depends")
            if self.ue4_version >= _UE4_ADD_STRING_ASSET_REFERENCES_MAP:
                r.i32()
                offset_field("soft_package_references")
            if self.ue4_version >= _UE4_ADDED_SEARCHABLE_NAMES:
                offset_field("searchable_names")
            offset_field("thumbnail_table")
            if self.ue5_version >= _UE5_IMPORT_TYPE_HIERARCHIES:
                r.i32()
                offset_field("import_type_hierarchies")

            if self.ue5_version < _UE5_PACKAGE_SAVED_HASH:
                r.pos += 16           # PackageGuid
            r.pos += 16               # PersistentGuid

            generations = r.i32()
            if not 0 <= generations <= 16:
                raise RuntimeError("summary desync: generations=%d" % generations)
            # Each generation snapshots (ExportCount, NameCount); the engine
            # cross-checks the last one, so growing a table must update it here.
            self.generation_entries = []
            for _ in range(generations):
                self.generation_entries.append(r.pos)
                r.pos += 8

            _read_engine_version(r)   # RecordedEngineVersion
            _read_engine_version(r)   # RecordedCompatibleWithEngineVersion

            r.u32()                   # CompressionFlags
            if r.i32():
                raise RuntimeError("package-level compression is not supported")
            r.u32()                   # PackageSource
            for _ in range(r.i32()):  # AdditionalPackagesToCook
                r.fstring()

            self.asset_registry_offset = offset_field("asset_registry")

            # 64-bit pair: the engine reads (PayloadToc - BulkDataStart) bytes,
            # so shifting one without the other yields a negative length.
            self.bulk_data_offset_off = r.pos
            self.bulk_data_offset = r.i64()

            offset_field("world_tile_info")
            n_chunks = r.i32()
            r.pos += n_chunks * 4
            r.i32()                   # PreloadDependencyCount (-1 when absent)
            offset_field("preload_dependency")
            r.i32()                   # NamesReferencedFromExportDataCount
            self.payload_toc_off = r.pos
            r.i64()
            offset_field("data_resource")

            # The summary is byte-packed, so a mis-parse lands silently on
            # adjacent data. One invariant pins it: the header ends exactly
            # where the name map begins.
            if r.pos != self.name_offset:
                raise RuntimeError(
                    "summary parse desynced: header ends at 0x%X, name map "
                    "starts at 0x%X" % (r.pos, self.name_offset))

        # -- name map ---------------------------------------------------------

        def _parse_name_map(self):
            r = _PkgReader(self.data, self.name_offset)
            self.names = []
            for _ in range(self.name_count):
                self.names.append(r.fstring())
                r.pos += 4            # the two case-folding hashes
            self.name_index = {n: i for i, n in enumerate(self.names)}

        def fname(self, index, number):
            """Render an FName. A nonzero number is stored one higher than it reads."""
            if not 0 <= index < len(self.names):
                return None
            base = self.names[index]
            return base if number == 0 else "%s_%d" % (base, number - 1)

        # -- import table -------------------------------------------------------

        def _parse_imports(self):
            """ObjectName -> package index (the Nth import is -(N+1)).

            The delegate's MemberParent stores one of these, naming the class
            that DECLARES the delegate; retargeting a clone across button types
            must update it or the engine reports the delegate as <None>.
            """
            data, base = self.data, self.import_offset
            self.imports = {}
            if not base or self.import_count <= 0:
                return
            end = base + self.import_count * _IMPORT_ENTRY_SIZE
            if end > len(data):
                return
            for i in range(self.import_count):
                index, number = struct.unpack_from(
                    "<ii", data, base + i * _IMPORT_ENTRY_SIZE + _IMPORT_NAME_FIELD)
                name = self.fname(index, number)
                # First wins: an earlier import is the one MemberParent references.
                if name is not None and name not in self.imports:
                    self.imports[name] = -(i + 1)

        # -- export table -------------------------------------------------------

        def _parse_exports(self):
            data, base = self.data, self.export_offset
            if self.export_entry_size is None:
                self.export_entry_size = self._derive_export_entry_size()
            self.exports = []
            for i in range(self.export_count):
                entry = base + i * self.export_entry_size
                index, number = struct.unpack_from(
                    "<ii", data, entry + _EXPORT_NAME_FIELD)
                self.exports.append({
                    "name": self.fname(index, number),
                    "entry": entry,
                    "offset": struct.unpack_from("<q", data, entry + 36)[0],
                    "size": struct.unpack_from("<q", data, entry + 28)[0],
                })

        def _derive_export_entry_size(self):
            """Infer the FObjectExport stride rather than hard-coding it.

            Export bodies are stored consecutively, so the second entry's
            SerialOffset must equal the first's offset plus its size, and only
            the true stride satisfies that.
            """
            data, base = self.data, self.export_offset
            size0 = struct.unpack_from("<q", data, base + 28)[0]
            off0 = struct.unpack_from("<q", data, base + 36)[0]
            for stride in range(72, 152, 4):
                probe = base + stride
                if probe + 44 > len(data):
                    break
                if struct.unpack_from("<q", data, probe + 36)[0] == off0 + size0:
                    return stride
            raise RuntimeError("could not determine the export table stride")

        # -- fname lookups ------------------------------------------------------

        def find_fname(self, export, value):
            """Byte offsets inside `export` holding the FName `value`."""
            base, number = _split_fname(value)
            index = self.name_index.get(base)
            if index is None:
                return []
            needle = struct.pack("<ii", index, number)
            start, end = export["offset"], export["offset"] + export["size"]
            found, at = [], self.data.find(needle, start, end)
            while at != -1:
                found.append(at)
                at = self.data.find(needle, at + 1, end)
            return found

        def set_fname(self, offset, value):
            """Point the FName at `offset` at `value`, which must already exist.

            Names are added up front by the operations that need them; by the
            time a value is being written, byte offsets are live and growing the
            map would shift the very bytes just located.
            """
            base, number = _split_fname(value)
            if base not in self.name_index:
                raise RuntimeError(
                    "%r is not in the name map; add it before locating offsets"
                    % value)
            struct.pack_into("<ii", self.data, offset,
                             self.name_index[base], number)

        def read_fstring(self, at):
            """Decode the FString at `at` -> (text, size in bytes).

            The length field counts CHARACTERS INCLUDING the terminating NUL,
            and its sign picks the encoding: negative means UTF-16, positive
            ASCII. Empty strings store a length of 0 and no payload at all.
            """
            count = struct.unpack_from("<i", self.data, at)[0]
            if count == 0:
                return "", 4
            if count < 0:
                size = 4 - count * 2
                raw = bytes(self.data[at + 4:at + size - 2])
                return raw.decode("utf-16-le"), size
            size = 4 + count
            raw = bytes(self.data[at + 4:at + size - 1])
            return raw.decode("utf-8", "replace"), size

        @staticmethod
        def encode_fstring(text):
            """Pack `text` as an FString. ASCII where it can, UTF-16 otherwise —
            the same choice the engine makes, so a value it wrote round-trips to
            the identical bytes."""
            if not text:
                return struct.pack("<i", 0)
            try:
                raw = text.encode("ascii") + b"\x00"
                return struct.pack("<i", len(raw)) + raw
            except UnicodeEncodeError:
                raw = text.encode("utf-16-le") + b"\x00\x00"
                return struct.pack("<i", -(len(raw) // 2)) + raw

        def find_tag(self, export, name):
            """Byte offset of the property tag named `name` inside `export`.

            A tag opens with its FName, so this is find_fname plus the check
            that what follows really is a tag -- the same 8 bytes could occur
            inside a payload by chance.
            """
            for at in self.find_fname(export, name):
                type_name = self.fname(
                    *struct.unpack_from("<ii", self.data, at + 8))
                if type_name and type_name.endswith("Property"):
                    return at
            return None

        # -- structural edits (these RESIZE the package) --------------------------

        def _shift_offsets(self, at, delta):
            """Move every stored offset pointing at or past `at` by `delta`.

            >= not >: bytes inserted exactly where a table starts push it along.
            """
            data = self.data

            # Read the thumbnail table's position BEFORE the loop relocates the
            # field recording it (the buffer itself has not been mutated yet).
            table_pos = self.offset_fields.get("thumbnail_table")
            table_at = (struct.unpack_from("<i", data, table_pos)[0]
                        if table_pos is not None else 0)

            # A zero offset means "table absent", never shift it.
            for pos in self.offset_fields.values():
                value = struct.unpack_from("<i", data, pos)[0]
                if value > 0 and value >= at:
                    struct.pack_into("<i", data, pos, value + delta)

            # SectionSixOffset doubles as the total header size in UE5.8.
            value = struct.unpack_from("<i", data, self.section_six_offset_off)[0]
            if value >= at:
                struct.pack_into("<i", data, self.section_six_offset_off,
                                 value + delta)

            for pos in (self.bulk_data_offset_off, self.payload_toc_off):
                value = struct.unpack_from("<q", data, pos)[0]
                if value >= at:
                    struct.pack_into("<q", data, pos, value + delta)

            # The thumbnail table stores an absolute file offset PER ENTRY;
            # a stale one makes the engine read garbage as a compressed size
            # ("Requested read of 33554432 bytes when 141656 bytes remain").
            if table_at > 0:
                pos = table_at + 4
                for _ in range(struct.unpack_from("<i", data, table_at)[0]):
                    for _ in range(2):        # class name, object path
                        n = struct.unpack_from("<i", data, pos)[0]
                        pos += 4 + (n if n >= 0 else -n * 2)
                    value = struct.unpack_from("<i", data, pos)[0]
                    if value >= at:
                        struct.pack_into("<i", data, pos, value + delta)
                    pos += 4

            # The asset registry blob opens with an int64 pointing at its own
            # dependency section; missing it = "Package is unloadable.
            # Reason: SerializeAssetRegistryDependencyData".
            if self.asset_registry_offset > 0:
                pos = self.asset_registry_offset
                value = struct.unpack_from("<q", data, pos)[0]
                if value >= at:
                    struct.pack_into("<q", data, pos, value + delta)

            for export in self.exports:
                if export["offset"] >= at:
                    struct.pack_into("<q", data, export["entry"] + 36,
                                     export["offset"] + delta)

        def _depends_end(self, entries=None):
            """File offset just past the last depends entry (found by walking:
            one entry per export, int32 count + that many indices, no length of
            its own). `entries` overrides the walk count for the moment during
            a clone when the export count is bumped but the table isn't yet."""
            pos = self.depends_offset
            for i in range(self.export_count if entries is None else entries):
                count = struct.unpack_from("<i", self.data, pos)[0]
                pos += 4
                if not 0 <= count < 10000:
                    raise RuntimeError(
                        "depends entry %d has an implausible count (%d)"
                        % (i, count))
                pos += count * 4
            return pos

        def add_name(self, text):
            """Append `text` to the name map and return its index.

            The name map sits ahead of every other table, so growing it shifts
            the whole file. Names are stored as an FString plus two hashes.
            """
            if text in self.name_index:
                return self.name_index[text]

            encoded = text.encode("utf-8") + b"\x00"
            entry = (struct.pack("<i", len(encoded)) + encoded
                     + _name_hashes(text))

            r = _PkgReader(self.data, self.name_offset)
            for _ in range(self.name_count):
                r.fstring()
                r.pos += 4
            insert_at = r.pos

            # Correct the stored offsets BEFORE the insertion; afterwards every
            # recorded position would itself be stale.
            self._shift_offsets(insert_at, len(entry))
            struct.pack_into("<i", self.data, self.name_count_off,
                             self.name_count + 1)
            # The latest generation snapshot repeats the name count and must
            # agree with the table it describes.
            if self.generation_entries:
                pos = self.generation_entries[-1] + 4
                count = struct.unpack_from("<i", self.data, pos)[0]
                struct.pack_into("<i", self.data, pos, count + 1)
            self.data[insert_at:insert_at] = entry

            self._reparse()
            return self.name_index[text]

        def clone_export(self, source, object_name):
            """Append a copy of export dict `source`, named `object_name`.

            The clone reuses the source's preload-dependency range (valid: the
            two exports have identical dependencies).
            """
            if any(e["name"] == object_name for e in self.exports):
                raise RuntimeError("export %r already exists" % object_name)

            base, number = _split_fname(object_name)
            name_index = self.add_name(base)

            # add_name() reparsed; re-resolve the source against the new layout.
            source = next(e for e in self.exports
                          if e["name"] == source["name"])
            body = bytes(self.data[source["offset"]:
                                   source["offset"] + source["size"]])
            entry = bytearray(self.data[source["entry"]:
                                        source["entry"] + self.export_entry_size])
            struct.pack_into("<ii", entry, _EXPORT_NAME_FIELD,
                             name_index, number)

            table_end = (self.export_offset
                         + self.export_count * self.export_entry_size)
            self._shift_offsets(table_end, self.export_entry_size)
            struct.pack_into("<i", self.data, self.export_count_off,
                             self.export_count + 1)
            # The latest generation snapshot repeats the export count too.
            if self.generation_entries:
                pos = self.generation_entries[-1]
                count = struct.unpack_from("<i", self.data, pos)[0]
                struct.pack_into("<i", self.data, pos, count + 1)
            self.data[table_end:table_end] = entry

            # Reparse between inserts: a second shift over stale parsed state
            # would skip entries that already moved.
            self._reparse()

            # Every export needs a depends entry or the engine reads one short
            # and takes the next table's bytes as a length. The clone depends
            # on nothing: a bare count of 0.
            depends_at = self._depends_end(entries=self.export_count - 1)
            self._shift_offsets(depends_at, 4)
            self.data[depends_at:depends_at] = struct.pack("<i", 0)
            self._reparse()

            # Export bodies tile exactly up to BulkDataStartOffset, so the
            # clone's body goes there — a gap breaks the registry read.
            body_at = self.bulk_data_offset
            new = self.exports[-1]
            struct.pack_into("<q", self.data, new["entry"] + 28, len(body))
            struct.pack_into("<q", self.data, new["entry"] + 36, body_at)

            self._shift_offsets(body_at, len(body))
            self.data[body_at:body_at] = body
            # The shift above also moved the clone's own SerialOffset, which
            # must stay pointing at the body rather than past it.
            struct.pack_into("<q", self.data, new["entry"] + 36, body_at)

            self._reparse()
            return self.exports[-1]

    # Ancestors a delegate may be DECLARED on. The declaring class differs PER
    # DELEGATE, not per widget: a UEFN button inherits OnButtonBaseClicked from
    # CommonButtonBase but declares OnButtonCTAHighlight on ITSELF — mapping the
    # whole widget to one class makes the compiler report <None> for the other.
    _DELEGATE_ANCESTORS = ("CommonButtonBase",)

    def _delegate_declaring_classes(wbp_path):
        """(widget, delegate) -> the class that declares that delegate, asked of
        the engine (never pattern-matched from the name)."""
        owners = {}
        for w in list_event_widgets(wbp_path):
            for delegate, _label in w["delegates"]:
                owner = w["native"]           # leaf class, unless an ancestor owns it
                for ancestor in _DELEGATE_ANCESTORS:
                    base = getattr(unreal, ancestor, None)
                    base_cdo = (unreal.get_default_object(base)
                                if base is not None else None)
                    if base_cdo is not None and _declares_delegate(base_cdo, delegate):
                        owner = ancestor
                        break
                owners[(w["name"], delegate)] = owner
        return owners

    def _package_events(pkg):
        """[(export, {widget,delegate,field,graph[,parent]} -> byte offset)].

        EventPath serializes before DestinationPath: the FIRST MemberName is the
        widget's delegate, the LAST is the Verse field. `parent` is EventPath's
        MemberParent — the package index of the class DECLARING the delegate;
        a clone crossing button types must rewrite it or the compiler reports
        the delegate as <None> and the event will not generate.
        """
        events = []
        for export in pkg.exports:
            if not str(export["name"] or "").startswith("MVVMBlueprintViewEvent"):
                continue
            members = sorted(pkg.find_fname(export, "MemberName"))
            widgets = sorted(pkg.find_fname(export, "WidgetName"))
            graphs = sorted(pkg.find_fname(export, "GraphName"))
            parents = sorted(pkg.find_fname(export, "MemberParent"))
            if len(members) < 2 or not widgets or not graphs:
                continue          # an empty shell the editor made but never filled
            slots = {
                "delegate": members[0] + _NAME_VALUE_GAP,
                "field": members[-1] + _NAME_VALUE_GAP,
                "widget": widgets[0] + _NAME_VALUE_GAP,
                "graph": graphs[0] + _NAME_VALUE_GAP,
            }
            if parents:
                # Same 25-byte tag->value gap as an FName property's.
                slots["parent"] = parents[0] + _NAME_VALUE_GAP
            # A parameterised event carries the panel's "Param 0" value here.
            # Absent on a parameterless one -- there is no pin to give a value.
            pin = pkg.find_tag(export, _PIN_DEFAULT_TAG)
            if pin is not None:
                slots["param_value"] = pin
            events.append((export, slots))
        return events

    def _read_slot(pkg, offset):
        return pkg.fname(*struct.unpack_from("<ii", pkg.data, offset))

    def _read_param_value(pkg, slots):
        """The binding's "Param 0" as text, or None if it takes no parameter."""
        at = slots.get("param_value")
        if at is None:
            return None
        return pkg.read_fstring(at + _NAME_VALUE_GAP)[0]

    def _set_param_value(pkg, event_name, value):
        """Write the binding's "Param 0" (text: "7", "2.5", "true").

        Changing the string's length must move FOUR sizes together -- the array's,
        DefaultString's, the FString's own count, and the export's SerialSize --
        plus every offset past the edit. Miss one and the package still parses
        here but crashes the editor's loader on reload, so the enclosure is
        re-checked below rather than trusted.
        """
        slots = next((s for e, s in _package_events(pkg)
                      if e["name"] == event_name), None)
        if slots is None:
            raise RuntimeError("no such event: %s" % event_name)
        at = slots.get("param_value")
        if at is None:
            raise RuntimeError(
                "%s is bound to a parameterless event field, so it has no "
                "parameter to set" % event_name)

        export = next(e for e in pkg.exports if e["name"] == event_name)
        array = pkg.find_tag(export, _PIN_ARRAY_TAG)
        if array is None:
            raise RuntimeError(
                "%s has a pin value but no %s array to hold it"
                % (event_name, _PIN_ARRAY_TAG))

        value_at = at + _NAME_VALUE_GAP
        old_size = pkg.read_fstring(value_at)[1]
        new = pkg.encode_fstring(value)
        delta = len(new) - old_size

        tag_size_at = at + _PROPERTY_SIZE_FIELD
        array_size_at = array + _ARRAY_SIZE_FIELD
        tag_size = struct.unpack_from("<i", pkg.data, tag_size_at)[0]
        array_size = struct.unpack_from("<i", pkg.data, array_size_at)[0]

        # The two sizes are only meaningful if they really describe these bytes.
        # An FName scan can match inside another property's payload, so prove the
        # relationship instead of assuming the offsets landed correctly.
        if tag_size != old_size:
            raise RuntimeError(
                "%s: DefaultString claims %d bytes but its string is %d"
                % (event_name, tag_size, old_size))
        array_payload = array_size_at + 4
        if not array_payload <= at < array_payload + array_size:
            raise RuntimeError(
                "%s: the %s array (payload %d bytes) does not enclose its "
                "DefaultString -- refusing to resize"
                % (event_name, _PIN_ARRAY_TAG, array_size))

        if delta:
            struct.pack_into("<i", pkg.data, tag_size_at, tag_size + delta)
            struct.pack_into("<i", pkg.data, array_size_at, array_size + delta)
            struct.pack_into("<q", pkg.data, export["entry"] + 28,
                             export["size"] + delta)
            # Shift from just PAST the old string: the bytes being replaced do
            # not move, everything after them does.
            pkg._shift_offsets(value_at + old_size, delta)

        pkg.data[value_at:value_at + old_size] = new
        pkg._reparse()

    def _wbp_file(wbp_path):
        name = os.path.abspath(str(unreal.PackageTools.package_name_to_filename(
            wbp_path.split(".", 1)[0], extension=".uasset")))
        if not os.path.isfile(name):
            raise RuntimeError("cannot resolve the asset file: %s" % name)
        return name

    def list_event_bindings(wbp_path):
        """[{event, widget, delegate, field, value}] for every MVVM event.

        `value` is the binding's "Param 0" -- the argument this button passes to
        the Verse event -- as text, or None when the event field takes none.
        """
        pkg = _Package(_wbp_file(wbp_path))
        return [{"event": export["name"],
                 "widget": _read_slot(pkg, slots["widget"]),
                 "delegate": _read_slot(pkg, slots["delegate"]),
                 "field": _read_slot(pkg, slots["field"]),
                 "value": _read_param_value(pkg, slots)}
                for export, slots in _package_events(pkg)]

    def _save_regenerating_tags(wbp_path):
        """Save the asset AND rebuild its asset-registry tags.

        EditorAssetLibrary.save_asset does NOT regenerate the VerseClassFields
        tag, so a freshly created Verse field is written out as a plain BP
        variable (the live editor still shows it — only the FILE is missing it).
        save_packages goes through the full save path, which rebuilds the tags.
        """
        asset = _el.load_asset(wbp_path)
        if asset is None:
            return
        package = _el.get_package_for_object(asset)
        package.modify()          # dirty it, or the save is skipped as a no-op
        unreal.EditorLoadingAndSavingUtils.save_packages([package], False)
        _await_unlocked(_wbp_file(wbp_path))

    def _await_unlocked(path, tries=50):
        """Block until the engine releases the file's handle.

        save_packages returns before Windows closes the handle it wrote through,
        so the byte patcher intermittently gets PermissionError. Only opening
        for writing is a meaningful test (os.access says writable throughout).
        """
        for _ in range(tries):
            try:
                with open(path, "r+b"):
                    return True
            except PermissionError:
                time.sleep(0.05)
        return False

    def _unload(wbp_path):
        """Close and unload the asset so its file is ours to rewrite.

        A field created this session has its MetaDataArray pointing at one of OUR
        ctypes buffers, and unloading would FMemory::Free() memory the engine never
        allocated (access violation at the next GC) -- so the arrays are nulled
        first. That dirties the package, and unload_packages flushes it, so this
        DOES write emptied metadata over the file; callers snapshot first and patch
        the snapshot. Do not "fix" it by skipping the detach: the crash is real.
        """
        asset = _el.load_asset(wbp_path)
        if asset is None:
            return

        # Detach EVERY variable's array: nulling an engine-owned one is harmless
        # (the snapshot rewrites the file), missing one of ours is fatal.
        try:
            _detach_metadata_arrays(
                asset, wbp_path,
                [str(n) for n in _bel.list_member_variable_names(asset)])
        except Exception:
            pass    # no member variables -> nothing to detach

        # Close the tab -- not because it locks the file (it does not), but because
        # unload_packages leaves the package RESIDENT while a tab references it,
        # and _reload then hands back the stale object. The returned count is the
        # only way to learn a tab was open; remember it for _reopen_editor.
        closed = unreal.get_editor_subsystem(
            unreal.AssetEditorSubsystem).close_all_editors_for_asset(asset)
        _WAS_EDITOR_OPEN[wbp_path] = bool(closed)

        package = _el.get_package_for_object(asset)
        del asset
        unreal.EditorLoadingAndSavingUtils.unload_packages([package])
        unreal.SystemLibrary.collect_garbage()
        # The unload also flushes, and that write's handle outlives the call.
        _await_unlocked(_wbp_file(wbp_path))

    def _reload(wbp_path):
        """Force a re-read of the patched file, compile, and save.

        unload_packages does not always evict the package; a plain load_asset
        would hand back the stale in-memory object (metadata arrays just
        detached) and the compile+save would write that emptiness over the
        freshly patched file. reload_packages forces the disk read; the full
        save then rewrites the asset-registry tags.
        """
        package = unreal.find_package(wbp_path.split(".", 1)[0])
        if package is not None:
            unreal.EditorLoadingAndSavingUtils.reload_packages([package])

        asset = _el.load_asset(wbp_path)
        if asset is None:
            raise RuntimeError("the widget did not reload after patching")
        _bel.compile_blueprint(asset)
        _save_regenerating_tags(wbp_path)
        _reopen_editor(wbp_path)

    def _close_editor(wbp_path):
        """Close the tab and note that it WAS open, for the ops that are about to
        close it anyway (an event-field create compiles, which tears it down).
        Once the engine has destroyed the tab there is nothing left to detect."""
        asset = _el.load_asset(wbp_path)
        if asset is None:
            return
        if unreal.get_editor_subsystem(
                unreal.AssetEditorSubsystem).close_all_editors_for_asset(asset):
            _WAS_EDITOR_OPEN[wbp_path] = True

    def _reopen_editor(wbp_path):
        """Reopen the tab only if something actually closed it. Most operations
        do not (measured: of create/delete/set-category, only an EVENT create),
        and reopening an already-open tab cost ~0.44s and raised UEFN for nothing.

        Declines while an op is in flight, LEAVING the state for the outer guard --
        popping it here would lose the fact that the tab needs restoring.
        """
        if _EDITOR_REOPEN_HELD[0]:
            return
        if not _WAS_EDITOR_OPEN.pop(wbp_path, False):
            return
        asset = _el.load_asset(wbp_path)
        if asset is not None:
            unreal.get_editor_subsystem(
                unreal.AssetEditorSubsystem).open_editor_for_assets([asset])

    def _patch_on_disk(wbp_path, patch, prepare=None):
        """Snapshot the file, unload, run patch(pkg) on the snapshot, write it
        over the live file and reload. On failure -- or when patch returns falsy
        (nothing changed) -- the snapshot is restored. Returns the backup path.

        `prepare()` runs while the asset is still LOADED (it needs the engine API,
        e.g. seeding an event); running it after the unload would re-lock the file
        pkg.save is about to write. Its return value is passed to patch(pkg, prep).

        Two copies, because prepare() may MUTATE the asset: `backup` (pre-prepare)
        is the rollback target, so a failure also undoes a half-done seed;
        `working` (post-prepare) is what gets patched, so the patcher sees the
        seeded export. Patching the backup instead silently misses it.

        Both must precede the unload: _unload's metadata detach dirties the
        package and unload_packages flushes it, writing emptied metadata over the
        file (Verse fields silently demoted to plain BP variables).
        """
        source = _wbp_file(wbp_path)
        backup_dir = os.path.join(unreal.Paths.project_saved_dir(),
                                  "VerseBinderBackups")
        os.makedirs(backup_dir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(source))[0]
        tag = uuid.uuid4().hex[:8]
        backup = os.path.join(backup_dir, "%s_%s.uasset" % (stem, tag))
        working = os.path.join(backup_dir, "%s_%s_work.uasset" % (stem, tag))

        _save_regenerating_tags(wbp_path)
        shutil.copy2(source, backup)

        try:
            if prepare is None:
                # Nothing touched the asset since the backup, so it IS the working
                # copy -- copying beats re-saving (~0.2s for identical bytes).
                prepared = None
                shutil.copy2(backup, working)
            else:
                prepared = prepare()
                # Re-snapshot: prepare() may have mutated the asset.
                _save_regenerating_tags(wbp_path)
                shutil.copy2(source, working)
        except Exception:
            shutil.copy2(backup, source)
            _reload(wbp_path)
            raise

        _unload(wbp_path)
        try:
            pkg = _Package(working)
            if patch(pkg, prepared):
                pkg.save(source)
            else:
                # Nothing patched — restore the pre-prepare snapshot: the unload
                # flushed emptied metadata over the live file, and an unused seed
                # must not survive either.
                shutil.copy2(backup, source)
            _reload(wbp_path)
            return backup
        except Exception:
            shutil.copy2(backup, source)
            _reload(wbp_path)
            raise

    @_keeps_editor_open
    def retarget_event_bindings(wbp_path, changes):
        """Repoint existing events. `changes` = [(event_name, widget, field)]."""
        result = {"retargeted": [], "skipped": [], "backup": None}
        if not changes:
            return result

        def patch(pkg, _prepared):
            # Names must exist before any slot offset is located: adding one
            # grows the name map and shifts the whole file.
            for _event, widget, field in changes:
                pkg.add_name(widget)
                pkg.add_name(field)
            slots_by_event = {e["name"]: s for e, s in _package_events(pkg)}
            for event, widget, field in changes:
                slots = slots_by_event.get(event)
                if slots is None:
                    result["skipped"].append((event, "no such event"))
                    continue
                pkg.set_fname(slots["widget"], widget)
                pkg.set_fname(slots["field"], field)
                result["retargeted"].append((event, widget, field))
            return bool(result["retargeted"])

        result["backup"] = _patch_on_disk(wbp_path, patch)
        return result

    def _register_view_event(pkg, export_index):
        """Add export number `export_index` to MVVMBlueprintView's Events array.

        An event export the view does not list is invisible: the package loads,
        but the editor shows the original bindings and nothing else.
        """
        # "Events" occurs in more than one export (MVVMBlueprintView holds the
        # editable list, MVVMViewClass a compiled one), so the owner must be
        # named rather than found by scanning — growing the wrong export's
        # SerialSize corrupts an unrelated one and the engine asserts on load.
        view = next((e for e in pkg.exports
                     if str(e["name"] or "").startswith("MVVMBlueprintView_")),
                    None)
        if view is None:
            raise RuntimeError("the widget has no MVVMBlueprintView export")

        tags = pkg.find_fname(view, "Events")
        if not tags:
            raise RuntimeError("MVVMBlueprintView has no Events array")

        at = tags[0] + _ARRAY_COUNT_GAP
        body_end = view["offset"] + view["size"]
        if not view["offset"] <= at < body_end:
            raise RuntimeError("Events array is not inside the view's body")

        count = struct.unpack_from("<i", pkg.data, at)[0]
        end = at + 4 + count * 4
        if not view["offset"] < end <= body_end:
            raise RuntimeError("Events array overruns the view's body")

        # The property tag records its payload's byte size just ahead of the
        # GUID flag and element count. The engine sizes its read from it, so it
        # has to grow with the array or the loader stops one element short.
        size_at = at - 5
        size = struct.unpack_from("<i", pkg.data, size_at)[0]
        if size != 4 + count * 4:
            raise RuntimeError(
                "Events tag size %d does not match %d elements" % (size, count))

        struct.pack_into("<i", pkg.data, at, count + 1)
        struct.pack_into("<i", pkg.data, size_at, size + 4)
        struct.pack_into("<q", pkg.data, view["entry"] + 28, view["size"] + 4)
        pkg._shift_offsets(end, 4)
        pkg.data[end:end] = struct.pack("<i", export_index)
        pkg._reparse()

    def _event_addition(addition):
        """(widget, field[, delegate[, value]])
        -> (widget, field, delegate or None, value or None).

        `value` is the argument the button passes to a parameterised event --
        the MVVM panel's "Param 0" box -- as text ("7", "2.5", "true").
        """
        return (addition[0], addition[1],
                addition[2] if len(addition) > 2 else None,
                addition[3] if len(addition) > 3 else None)

    def _retarget_event_in_package(pkg, event_name, widget, field, delegate,
                                   declaring_class=None, graph=None):
        """Point an existing event export at (widget, field, delegate).

        `declaring_class` -- the class DECLARING `delegate` -- goes into EventPath's
        MemberParent, and matters when the event moves across button TYPES (a
        Custom Button's OnButtonClicked does not exist on CommonButtonBase, so a
        stale parent yields "<None>"). It must already be an import; it always is.

        Intern every name BEFORE reading any slot offset: add_name grows the name
        map and shifts every byte offset in the file.
        """
        for text in (graph, widget, field, delegate):
            if text:
                pkg.add_name(text)

        slots = next(s for e, s in _package_events(pkg) if e["name"] == event_name)
        pkg.set_fname(slots["widget"], widget)
        pkg.set_fname(slots["field"], field)
        if delegate:
            pkg.set_fname(slots["delegate"], delegate)
        if graph:
            pkg.set_fname(slots["graph"], graph)

        if declaring_class and "parent" in slots:
            index = pkg.imports.get(declaring_class)
            if index is None:
                raise RuntimeError(
                    "%s is not in the import table, so the delegate's MemberParent "
                    "cannot be retargeted" % declaring_class)
            struct.pack_into("<i", pkg.data, slots["parent"], index)

    def _clone_event_in_package(pkg, template_name, widget, field, delegate,
                                declaring_class=None):
        """Clone the event export `template_name` and retarget the copy.

        Returns the clone's export name. The event key is never serialized —
        the engine derives it on load — so the clone gets its own on the next
        compile with nothing to forge. Each clone also needs its OWN GraphName;
        keeping the template's would collide with it.
        """
        graph = "__" + str(uuid.uuid4())
        pkg.add_name(graph)

        base, _number = _split_fname(template_name)
        used = {e["name"] for e in pkg.exports}
        number = 0
        while "%s_%d" % (base, number) in used:
            number += 1
        name = "%s_%d" % (base, number)

        template = next(e for e in pkg.exports if e["name"] == template_name)
        pkg.clone_export(template, name)

        index = next(i for i, e in enumerate(pkg.exports, start=1)
                     if e["name"] == name)
        _register_view_event(pkg, index)

        _retarget_event_in_package(pkg, name, widget, field, delegate,
                                   declaring_class, graph=graph)
        return name

    # The placeholder target the seed event is created against. It never has to
    # resolve -- the patcher retargets every FName before the engine sees the
    # file -- it only has to make the engine SERIALIZE the path structs.
    _SEED_WIDGET = "__VerseBinderSeedWidget"
    _SEED_FIELD = "__VerseBinderSeedField"
    _SEED_DELEGATE = "OnButtonBaseClicked"
    _SEED_PARENT = "/Script/CoreUObject.Class'/Script/CommonUI.CommonButtonBase'"

    _SEED_EVENT_PATH = (
        '(Paths=((BindingReference=(MemberParent="%s",MemberName="%s"),'
        'BindingKind=Property)),WidgetName="%s",'
        'ContextId=00000000000000000000000000000000,Source=Widget,'
        'bIsComponent=False,bDeprecatedSource=True)'
        % (_SEED_PARENT, _SEED_DELEGATE, _SEED_WIDGET))

    _SEED_DEST_PATH = (
        '(Paths=((BindingReference=(MemberName="%s",MemberGuid=%s,'
        'bSelfContext=True))),WidgetName="",'
        'ContextId=00000000000000000000000000000000,Source=SelfContext,'
        'bIsComponent=False,bDeprecatedSource=True)' % (_SEED_FIELD, "0" * 32))

    def _seed_first_event(wbp_path):
        """Give a widget with NO events a template to clone, using the engine, so
        the user need not hand-author the first one. MVVMEditorSubsystem.add_event
        does the structural work -- appends a real export AND registers it in the
        view's Events array (a virgin view omits Events entirely, leaving the
        patcher no array to grow).

        The shell is not quite a template: EventPath and DestinationPath serialize
        (import_text bypasses their read-only guard), but GraphName has no such
        door, stays None, and an unset FName is not written at all.
        _finish_seed_event injects that one tag on disk.
        """
        wbp = _el.load_asset(wbp_path)
        subsystem = unreal.get_editor_subsystem(unreal.MVVMEditorSubsystem)
        view = subsystem.get_view(wbp) or subsystem.request_view(wbp)
        before = {e.get_name() for e in view.get_editor_property("events")}

        event = subsystem.add_event(wbp)
        if event is None:
            raise RuntimeError("MVVMEditorSubsystem.add_event returned nothing")

        # get_editor_property on a UObject hands back the LIVE struct, so
        # import_text edits the event itself -- no (refused) set_editor_property.
        event.get_editor_property("event_path").import_text(_SEED_EVENT_PATH)
        event.get_editor_property("destination_path").import_text(_SEED_DEST_PATH)

        _bel.compile_blueprint(wbp)
        _save_regenerating_tags(wbp_path)

        after = [e.get_name() for e in view.get_editor_property("events")]
        new = [n for n in after if n not in before]
        if not new:
            raise RuntimeError("the seed event did not persist")
        return new[0]

    # A GraphName NameProperty tag: [FName name][FName "NameProperty"]
    # [int32 ArrayIndex][int32 value size = 8][byte flags][FName value] = 33 bytes.
    _GRAPH_TAG_SIZE = 33

    def _finish_seed_event(pkg, event_name, graph):
        """Write the GraphName tag the engine's seed shell leaves out.

        A property at its default is never serialized, and graph_name is
        read-only, so the shell's stream simply ends where GraphName belongs. It
        terminates with an FName "None" followed by a 4-byte trailer; writing the
        tag AT that terminator extends the stream and leaves None terminating it,
        which puts GraphName at exactly the offset a real event carries it.
        """
        pkg.add_name("GraphName")
        pkg.add_name("NameProperty")
        base, number = _split_fname(graph)
        pkg.add_name(base)

        export = next(e for e in pkg.exports if e["name"] == event_name)
        end = export["offset"] + export["size"]
        ends = [n for n in sorted(pkg.find_fname(export, "None"))
                if n + 8 + 4 == end]
        if not ends:
            raise RuntimeError("the seed event's property stream has no terminator")
        at = ends[-1]

        tag = (struct.pack("<ii", pkg.name_index["GraphName"], 0)
               + struct.pack("<ii", pkg.name_index["NameProperty"], 0)
               + struct.pack("<ii", 0, 8) + bytes([0])
               + struct.pack("<ii", pkg.name_index[base], number))
        if len(tag) != _GRAPH_TAG_SIZE:
            raise RuntimeError("built a %d-byte GraphName tag" % len(tag))

        struct.pack_into("<q", pkg.data, export["entry"] + 28,
                         export["size"] + len(tag))
        pkg._shift_offsets(at, len(tag))
        pkg.data[at:at] = tag
        pkg._reparse()

    def _pick_template(pkg, param, params):
        """(event name, its delegate) to clone for a field taking `param`.

        A clone copies its template's GRAPH, which for a parameterised binding is
        TYPED -- and the engine trusts that graph over the FName the patcher
        wrote. Cloning an int binding onto a float field silently re-points the
        field back to the int one (measured). So the donor's param must MATCH, and
        a plain event is always safe: its graph carries no parameter, and _add_pin
        builds the pin the clone then lacks.
        """
        events = _package_events(pkg)

        if param:
            for export, slots in events:      # same-typed: graph already agrees
                if params.get(_read_slot(pkg, slots["field"])) == param:
                    return export["name"], _read_slot(pkg, slots["delegate"])

        for export, slots in events:          # parameterless: no typed graph
            if "param_value" not in slots:
                return export["name"], _read_slot(pkg, slots["delegate"])

        # Only differently-typed param bindings exist; _apply_param_value will
        # strip the inherited pin and rebuild it.
        if events:
            export, slots = events[0]
            return export["name"], _read_slot(pkg, slots["delegate"])
        return None, None

    def _build_pin_block(pkg, value):
        """Serialize a SavedPins block holding `value`, for THIS package: every
        FName is resolved through the package's own name map (adding any it
        lacks), since the template stores indices, not text.

        Adding names REPARSES the package, so callers must re-locate any offset
        they took before calling this.
        """
        ops = []
        for token in _PIN_TEMPLATE.split():
            if re.fullmatch(r"[0-9a-f]+", token) and not len(token) % 2:
                ops.append(("raw", bytes.fromhex(token)))
            else:
                base, _, number = token.partition("#")
                pkg.add_name(base)
                ops.append(("name", base, int(number or 0)))

        block = bytearray()
        default_at = None
        for op in ops:
            if op[0] == "raw":
                block += op[1]
                continue
            if op[1] == "DefaultString":
                default_at = len(block)      # the tag; its size sits at +20
            block += struct.pack("<ii", pkg.name_index[op[1]], op[2])
        if default_at is None:
            raise RuntimeError("the pin template has no DefaultString tag")

        # Rewrite the template's baked-in value, resizing the string, its tag and
        # the enclosing array (the export's SerialSize is the caller's job).
        old = pkg.encode_fstring(_PIN_TEMPLATE_VALUE)
        new = pkg.encode_fstring(str(value))
        delta = len(new) - len(old)
        value_at = default_at + _NAME_VALUE_GAP
        if bytes(block[value_at:value_at + len(old)]) != old:
            raise RuntimeError("the pin template's value is not where expected")

        size_at = default_at + _PROPERTY_SIZE_FIELD
        struct.pack_into("<i", block, size_at,
                         struct.unpack_from("<i", block, size_at)[0] + delta)
        struct.pack_into("<i", block, _ARRAY_SIZE_FIELD,
                         struct.unpack_from("<i", block,
                                            _ARRAY_SIZE_FIELD)[0] + delta)
        block[value_at:value_at + len(old)] = new
        return bytes(block)

    def _add_pin(pkg, event_name, value):
        """Give a pinless event export a SavedPins block holding `value`. It goes
        immediately BEFORE GraphName -- a property stream is order-sensitive, and
        that is where the engine writes it."""
        block = _build_pin_block(pkg, value)      # adds names -> shifts offsets

        # Re-locate AFTER _build_pin_block: add_name reparsed the package.
        export = next(e for e in pkg.exports if e["name"] == event_name)
        if pkg.find_tag(export, _PIN_ARRAY_TAG) is not None:
            raise RuntimeError("%s already has a pin" % event_name)
        graph = pkg.find_tag(export, "GraphName")
        if graph is None:
            raise RuntimeError("%s has no GraphName to insert the pin before"
                               % event_name)

        struct.pack_into("<q", pkg.data, export["entry"] + 28,
                         export["size"] + len(block))
        pkg._shift_offsets(graph, len(block))
        pkg.data[graph:graph] = block
        pkg._reparse()

    def _remove_pin(pkg, event_name):
        """Strip a SavedPins block from an event that must not have one: a clone of
        a parameterised binding onto a PLAIN field inherits a pin the field cannot
        accept."""
        export = next(e for e in pkg.exports if e["name"] == event_name)
        start = pkg.find_tag(export, _PIN_ARRAY_TAG)
        if start is None:
            return
        end = pkg.find_tag(export, "GraphName")
        if end is None or end <= start:
            raise RuntimeError("%s: cannot bound the pin block" % event_name)

        size = end - start
        struct.pack_into("<q", pkg.data, export["entry"] + 28,
                         export["size"] - size)
        del pkg.data[start:end]
        pkg._shift_offsets(start, -size)
        pkg._reparse()

    def _apply_param_value(pkg, event_name, field, value, params):
        """Reconcile the clone's inherited pin with its FIELD (build one, or strip
        it), then write "Param 0". Returns True if the pin had to be BUILT.

        The value is always written, even when the caller gave none: the inherited
        one belongs to a different button.

        A value in a CLONED pin survives the create's compile; one in a pin we
        SYNTHESIZED does not (the compiler regenerates it from the field). Only
        the caller can fix that, with a second pass -- hence the return value.
        """
        param = params.get(field)
        export = next(e for e in pkg.exports if e["name"] == event_name)
        has_pin = pkg.find_tag(export, _PIN_ARRAY_TAG) is not None

        if not param:
            if has_pin:
                _remove_pin(pkg, event_name)
            return False

        if value is None:
            value = _EVENT_PARAM_DEFAULTS[param]
        if has_pin:
            _set_param_value(pkg, event_name, str(value))
            return False
        _add_pin(pkg, event_name, str(value))
        return True

    @_keeps_editor_open
    def create_event_bindings(wbp_path, additions):
        """Create new events. `additions` = [(widget, field[, delegate])];
        a missing delegate keeps the template's.

        Each event is a CLONE of an existing one. A widget with none gets a
        template first, built by the engine (_seed_first_event) and completed on
        disk, so this works on a widget that has never had an event binding.
        Patched via _patch_on_disk (backup restored on failure).
        """
        # `_values` pairs each created event with the value it is owed, for the
        # second pass below; it is popped before returning, so the result shape
        # callers see is unchanged.
        result = {"created": [], "skipped": [], "backup": None, "_values": []}
        if not additions:
            result.pop("_values")
            return result

        def prepare():
            # Runs with the asset loaded, inside _patch_on_disk's rollback: both
            # calls need the engine API / the widget tree, and seeding WRITES a
            # placeholder event, so a failure after this point must undo it.
            declaring = _delegate_declaring_classes(wbp_path)

            # field -> parameter kind (int/float/logic) or None, read from the
            # engine's own VerseClassFields tag rather than the caller's belief.
            params = {f["name"]: f.get("param") for f in list_verse_fields(wbp_path)}

            # Refuse a delegate the widget does not declare, BEFORE writing
            # anything: the two button shapes spell the same event differently
            # (OnButtonCTAHighlight vs OnButtonHighlight), and a mismatch compiles
            # to an unresolvable "<None>" delegate with only a log warning.
            for addition in additions:
                widget, field, delegate, value = _event_addition(addition)
                if delegate and (widget, delegate) not in declaring:
                    known = sorted(d for w, d in declaring if w == widget)
                    raise RuntimeError(
                        "%s does not declare %s -- it has %s"
                        % (widget, delegate, ", ".join(known) or "no events"))
                # A value needs a pin to hold it, and the FIELD decides whether
                # there is one. Catch it here, before anything is written.
                if value is not None and not params.get(field):
                    raise RuntimeError(
                        "%s takes no parameter, so %s cannot pass it a value"
                        % (field, widget))

            seed = (None if list_event_bindings(wbp_path)
                    else _seed_first_event(wbp_path))
            return seed, declaring, params

        def patch(pkg, prepared):
            seed, declaring, params = prepared
            pending = list(additions)

            if seed is not None:
                # The seed is a shell until its GraphName tag exists; without it
                # _package_events skips the export and there is still no template.
                _finish_seed_event(pkg, seed, "__" + str(uuid.uuid4()))
                # It points at a placeholder, so it is not a binding yet -- it is
                # the FIRST one, unfilled. Retarget it in place rather than cloning
                # and deleting it: no export is ever removed (shrinking the export
                # table is a whole class of surgery avoided).
                widget, field, delegate, value = _event_addition(pending.pop(0))
                _retarget_event_in_package(
                    pkg, seed, widget, field, delegate or _SEED_DELEGATE,
                    declaring.get((widget, delegate or _SEED_DELEGATE)))
                built = _apply_param_value(pkg, seed, field, value, params)
                result["created"].append((seed, widget, field))
                if built:
                    result["_values"].append(
                        (seed, value if value is not None
                         else _EVENT_PARAM_DEFAULTS[params[field]]))

            for addition in pending:
                widget, field, delegate, value = _event_addition(addition)

                # The donor's parameter must match the field's: its GRAPH is typed
                # and the engine trusts that over the patched FName. A plain event
                # is always safe -- _apply_param_value builds the pin it lacks.
                template, template_delegate = _pick_template(
                    pkg, params.get(field), params)
                if template is None:
                    raise RuntimeError("could not establish an event to clone")

                # Declaring class is per (widget, delegate); with no delegate
                # given the clone keeps the template's, so resolve against it.
                name = _clone_event_in_package(
                    pkg, template, widget, field, delegate,
                    declaring_class=declaring.get(
                        (widget, delegate or template_delegate)))
                built = _apply_param_value(pkg, name, field, value, params)
                result["created"].append((name, widget, field))
                if built:
                    result["_values"].append(
                        (name, value if value is not None
                         else _EVENT_PARAM_DEFAULTS[params[field]]))
            return True

        result["backup"] = _patch_on_disk(wbp_path, patch, prepare=prepare)

        # SECOND PASS -- only for values in pins we SYNTHESIZED, whose compile
        # would otherwise revert them. A cloned pin's value already stuck, and an
        # editor cycle costs ~2s and closes the tab, so it is skipped when it can
        # be (the common case: a donor binding exists).
        valued = result.pop("_values")
        if valued:
            def set_values(pkg, _prepared):
                # A create that rolled back leaves `valued` naming exports the
                # restored file no longer has.
                live = {e["name"] for e, _s in _package_events(pkg)}
                wrote = False
                for name, value in valued:
                    if name in live:
                        _set_param_value(pkg, name, str(value))
                        wrote = True
                return wrote
            _patch_on_disk(wbp_path, set_values)

        return result

    @_keeps_editor_open
    def remove_event_bindings(wbp_path, event_names):
        """Delete MVVM events by export name. Uses the engine API, not the disk."""
        wbp = _el.load_asset(wbp_path)
        subsystem = unreal.get_editor_subsystem(unreal.MVVMEditorSubsystem)
        view = subsystem.get_view(wbp)
        if view is None:
            return 0
        doomed, removed = set(event_names), 0
        for event in list(view.get_editor_property("events")):
            if event.get_name() in doomed:
                subsystem.remove_event(wbp, event)
                removed += 1
        if removed:
            _bel.compile_blueprint(wbp)
            _el.save_asset(wbp_path)
        return removed

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

        The LAST digit run is the index, and only it becomes `#`: UMG names a
        duplicated widget family Slot1_1, Slot1_2, ... so requiring a single digit run
        would skip exactly the case bulk binding exists for. Earlier runs stay literal,
        which is what the matchers expect. A family needs at least two members. Sorted
        largest-family first.
        """
        groups = {}
        for name in names:
            runs = list(re.finditer(r"\d+", name))
            if not runs:
                continue
            last = runs[-1]
            pat = name[:last.start()] + "#" + name[last.end():]
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

    def _match_indexed(fields_by_n, targets_by_n, pair_fn, target_noun, field_noun):
        """Shared core of the bulk matchers: walk the common indices, warn on
        ambiguity and on unmatched indices (never silently drop — a mismatch is
        usually a typo in a pattern). `pair_fn(field, target, warnings)` returns
        a pair or None (after appending its own warning)."""
        pairs, warnings = [], []
        for n in sorted(set(fields_by_n) & set(targets_by_n)):
            fs, ts = fields_by_n[n], targets_by_n[n]
            if len(fs) > 1 or len(ts) > 1:
                warnings.append("index %d ambiguous: %s / %s"
                                % (n, [f["name"] for f in fs], [t["name"] for t in ts]))
                continue
            pair = pair_fn(fs[0], ts[0], warnings)
            if pair is not None:
                pairs.append(pair)
        for n in sorted(set(fields_by_n) - set(targets_by_n)):
            warnings.append("%s has no matching %s (index %d)"
                            % (fields_by_n[n][0]["name"], target_noun, n))
        for n in sorted(set(targets_by_n) - set(fields_by_n)):
            warnings.append("%s has no matching %s (index %d)"
                            % (targets_by_n[n][0]["name"], field_noun, n))
        return pairs, warnings

    def match_by_suffix(fields, widgets, field_pattern, widget_pattern, prop_name):
        """Pair fields with engine widgets by a shared index ("VF_Color#" x
        "Image#"). Returns ([(field, widget, native, prop)], [warnings])."""
        def pair(field, widget, warnings):
            if not is_direct_bindable(field["type"], widget["native"], prop_name):
                warnings.append(
                    "%s (%s) cannot bind directly to %s.%s — needs a conversion"
                    % (field["name"], field["type"], widget["name"], prop_name))
                return None
            return (field["name"], widget["name"], widget["native"], prop_name)
        return _match_indexed(_index_by_pattern(fields, field_pattern),
                              _index_by_pattern(widgets, widget_pattern),
                              pair, "widget", "field")

    def match_events_by_suffix(fields, event_widgets, field_pattern, widget_pattern,
                               delegate_label):
        """Pair EVENT fields with buttons by a shared index ("VF_Click#" x
        "Button#" x "On Clicked"). Returns ([(field, widget, delegate)], [warnings])."""
        def pair(field, widget, warnings):
            # The same event is spelled differently per button shape, so resolve
            # the label against THIS widget's own delegates.
            delegate = next((d for d, label in widget["delegates"]
                             if label == delegate_label), None)
            if delegate is None:
                warnings.append("%s has no %s event" % (widget["name"], delegate_label))
                return None
            return (field["name"], widget["name"], delegate)
        return _match_indexed(
            _index_by_pattern([f for f in fields if f["type"] == "event"],
                              field_pattern),
            _index_by_pattern(event_widgets, widget_pattern),
            pair, "widget", "event field")

    def match_child_by_suffix(fields, child_widgets, field_pattern, child_pattern,
                              child_field):
        """Pair fields with sub-widget instances by a shared index ("VF_Slot#Image"
        x "Slot#"). Same-type only. Returns ([child-pair dicts], [warnings])."""
        def pair(field, child, warnings):
            cf = next((f for f in child["fields"] if f["name"] == child_field), None)
            if cf is None:
                warnings.append("%s has no field %s" % (child["name"], child_field))
                return None
            if field["type"] != cf["type"]:
                warnings.append("%s (%s) ≠ %s.%s (%s) — types must match"
                                % (field["name"], field["type"], child["name"],
                                   child_field, cf["type"]))
                return None
            return {"mode": "child", "field": field["name"],
                    "widget": child["name"], "class_path": child["class_path"],
                    "child_field": child_field}
        return _match_indexed(_index_by_pattern(fields, field_pattern),
                              _index_by_pattern(child_widgets, child_pattern),
                              pair, "sub-widget", "field")

    # ═══════════════════════════════════════════════════════════════════════
    #  FIELD CREATION  (memory-patched Verse metadata)
    # ═══════════════════════════════════════════════════════════════════════
    #
    # UE exposes NO API for a variable's MetaDataArray. A Verse field is a
    # normal BP member variable carrying 4 metadata keys (5 for `message`) plus
    # PropertyFlags=65541; the variable is created via the public API, then the
    # metadata is patched in memory. Nothing is build-hardcoded: NewVariables is
    # located by probing for a known VarGuid, FName keys interned at runtime.

    _DESC_SIZE = 232
    _GUID_OFFSET = 12
    _PROPERTY_FLAGS_OFFSET = 176
    _METADATA_OFFSET = 200
    _ENTRY_SIZE = 32
    _VERSE_PROPERTY_FLAGS = 65541
    _VERSE_EVENT_PROPERTY_FLAGS = 65557
    _NEWVARS_SEARCH_LIMIT = 1400

    _COLOR_STRUCT = ("/Script/CoreUObject.VerseStruct"
                     "'/VerseColors/_Verse/VNI/VerseColors.Colors_color'")
    _COLOR_ALPHA_STRUCT = ("/Script/CoreUObject.VerseStruct"
                           "'/VerseColors/_Verse/VNI/VerseColors.Colors_color_alpha'")

    _VERSE_EVENT_CLASS = ("/Script/CoreUObject.Class"
                          "'/Script/VerseTypeEditorRuntime.VerseEvent'")
    _VERSE_UI_WIDGET_CLASS = ("/Script/CoreUObject.Class"
                              "'/Script/VerseUI.VerseUIUserWidget'")

    _CREATE_TYPES = ("logic", "int", "float", "string", "message", "event",
                     "color", "color_alpha", "material", "texture")

    # An event field may carry ONE parameter -- not a different kind of field (same
    # VerseEvent member, flags and keys), just a serialized EdGraphPinType in the
    # EventParameters value a plain event leaves empty. Spelled ("event", param)
    # rather than an "event_int" kind so every `kind == "event"` test keeps holding.
    _EVENT_PARAM_PINS = {
        "int": ('(PinCategory="int64",PinSubCategoryMemberReference='
                '(MemberGuid=%s),PinValueType=())' % _ZERO_GUID),
        "float": ('(PinCategory="real",PinSubCategory="double",'
                  'PinSubCategoryMemberReference=(MemberGuid=%s),'
                  'PinValueType=())' % _ZERO_GUID),
        "logic": ('(PinCategory="bool",PinSubCategoryMemberReference='
                  '(MemberGuid=%s),PinValueType=())' % _ZERO_GUID),
    }
    _EVENT_PARAMS = tuple(_EVENT_PARAM_PINS)

    def _field_spec(field):
        """(name, kind[, param]) -> (name, kind, param or None)."""
        return (field[0], field[1], field[2] if len(field) > 2 else None)

    # What the Create tab's type dropdown offers. A parameterised event is shown
    # as its own entry rather than behind a second "param" combo, so a row stays
    # (name, kind) and the pattern/bulk path needs no special case. The variants
    # sit directly after plain `event` so the whole event family reads together.
    _CREATE_LABELS = tuple(
        label
        for kind in _CREATE_TYPES
        for label in ([kind] + (["event (%s)" % p for p in _EVENT_PARAMS]
                                if kind == "event" else [])))

    def _split_create_label(label):
        """"event (int)" -> ("event", "int");  "color" -> ("color", None)."""
        match = re.match(r'^event \((\w+)\)$', label)
        return ("event", match.group(1)) if match else (label, None)

    # ctypes buffers must outlive serialization or Unreal reads dangling
    # pointers. _unload and delete_verse_fields detach descriptor arrays before
    # the engine can destroy one — nulling an engine-owned array is harmless;
    # being wrong the other way costs the whole editor.
    if not hasattr(unreal, "_verse_field_buffers"):
        unreal._verse_field_buffers = []
    _KEEP = unreal._verse_field_buffers

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
        obj = {"event": ("object", _VERSE_EVENT_CLASS),
               "back_pointer": ("object", _VERSE_UI_WIDGET_CLASS),
               "color": ("struct", _COLOR_STRUCT),
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

    def _build_metadata_block(display_name, disable_default=False,
                              visibility="<public>", event=False,
                              hidden_only=False, event_param=None):
        if hidden_only:                            # the shared BackPointer member
            entries = [
                (_fname_key16("DisplayName"), display_name),
                (_fname_key16("Hidden"), None),
            ]
        else:
            entries = [
                (_fname_key16("FieldNotify"), None),
                (_fname_key16("VerseVariable"), None),
                (_fname_key16("DisplayName"), display_name),
                (_fname_key16("VisibilityAccess"), visibility),
            ]
            if event:
                # EventParameters holds a serialized EdGraphPinType, or nothing
                # for a parameterless event. That single string is the whole
                # difference between `event` and `event (int)`.
                entries.extend([
                    (_fname_key16("Hidden"), None),
                    (_fname_key16("EventParameters"),
                     _EVENT_PARAM_PINS[event_param] if event_param else None),
                ])
            elif disable_default:                  # `message` fields only
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

    def _event_function_graph_status(wbp, public_name):
        """(graph, has_function_entry) for a public Verse event function graph."""
        graph = _bel.find_graph(wbp, public_name)
        if graph is None:
            return None, False
        try:
            editor = unreal.BlueprintGraphEditor.get_graph_editor_by_name(
                wbp, public_name)
            entries = editor.list_nodes_of_class(unreal.K2Node_FunctionEntry)
            has_entry = any(node.get_name() == "K2Node_FunctionEntry_0"
                            for node in entries)
        except Exception:
            has_entry = False
        return graph, has_entry

    def _create_verse_fields_impl(wbp_path, fields, category=None):
        """Create Verse fields. `fields` = [(name, kind)] or, for an event that
        takes a parameter, [(name, "event", param)] where param is int/float/
        logic. Every variable is patched into a real Verse field and verified
        against VerseClassFields; raises if any fails, so a half-made plain BP
        variable never survives. Returns {created, skipped_existing, verified}.

        An `event` field is THREE things (credit: Benjamin Ferellec): a member
        VerseFieldInternalVariable_<PublicName> typed VerseEvent, a public
        function graph <PublicName>, and a shared hidden BackPointer member.
        Flags 65557; metadata adds Hidden + EventParameters -- and a parameter
        is nothing more than a pin type in that EventParameters value.
        """
        fields = [_field_spec(f) for f in fields]
        for name, kind, param in fields:
            _pin_type(kind)                        # validate all types up front
            if param is not None:
                if kind != "event":
                    raise ValueError("only event fields take a parameter, not %r"
                                     % kind)
                if param not in _EVENT_PARAM_PINS:
                    raise ValueError("unknown event parameter %r -- expected %s"
                                     % (param, ", ".join(_EVENT_PARAMS)))
            if kind == "event" and name.startswith("VerseFieldInternalVariable_"):
                raise ValueError(
                    "event names must be public names, not internal names")
            if name == "BackPointer":
                raise ValueError(
                    "BackPointer is reserved by Verse UI event fields")

        wbp = _el.load_asset(wbp_path)
        existing = set(str(n) for n in _bel.list_member_variable_names(wbp))
        public_existing = {f["name"] for f in list_verse_fields(wbp_path)}

        # Preflight the whole batch before writing anything. An event's public
        # function graph is a real Blueprint symbol and must not collide with
        # an existing member or graph, even though its storage member carries
        # the internal name. specs = (public, actual member, kind, event param).
        specs, skipped = [], []
        for name, kind, param in fields:
            actual = ("VerseFieldInternalVariable_" + name
                      if kind == "event" else name)
            if name in public_existing or actual in existing:
                skipped.append(name)
                continue
            specs.append((name, actual, kind, param))
            if kind == "event":
                graph, _has_entry = _event_function_graph_status(wbp, name)
                if name in existing or graph is not None:
                    raise ValueError(
                        "event public name %r collides with an existing "
                        "member or graph" % name)

        for public, actual, kind, _param in specs:
            if not _bel.add_member_variable(wbp, actual, _pin_type(kind)):
                raise RuntimeError("could not create Blueprint member %r" % actual)
            existing.add(actual)
            if kind == "event":
                graph = _bel.add_function_graph(wbp, public)
                if graph is None:
                    raise RuntimeError(
                        "could not create public event function graph %r" % public)

        back_pointer_created = False
        if any(kind == "event" for _public, _actual, kind, _param in specs) \
                and "BackPointer" not in existing:
            if not _bel.add_member_variable(
                    wbp, "BackPointer", _pin_type("back_pointer")):
                raise RuntimeError("could not create shared BackPointer")
            back_pointer_created = True
            existing.add("BackPointer")

        created = [public for public, _actual, _kind, _param in specs]
        if not created:
            return {"created": [], "skipped_existing": skipped, "verified": {}}

        _bel.compile_blueprint(wbp)
        _el.save_asset(wbp_path)

        # T3D export can invalidate pointers — reload before touching memory.
        wbp = _el.load_asset(wbp_path)
        guids = variable_guids(wbp_path)
        wbp = _el.load_asset(wbp_path)

        by_actual = {actual: (public, kind, param)
                     for public, actual, kind, param in specs}
        uobject = ctypes.c_uint64.from_address(id(wbp) + 16).value
        probe = next(iter(guids))
        data, count = _find_newvars(uobject, _guid_bytes(guids[probe]), len(guids))

        patched = []
        for index in range(count):
            descriptor = data + index * _DESC_SIZE
            guid = bytes((ctypes.c_uint8 * 16).from_address(descriptor + _GUID_OFFSET))
            name = next((n for n, g in guids.items() if _guid_bytes(g) == guid), None)
            if name == "BackPointer" and back_pointer_created:
                block, entries = _build_metadata_block(
                    "Back Pointer", hidden_only=True)
                flags = _VERSE_PROPERTY_FLAGS
                public_name = None
            elif name in by_actual:
                public_name, kind, param = by_actual[name]
                block, entries = _build_metadata_block(
                    public_name, disable_default=(kind == "message"),
                    event=(kind == "event"), event_param=param)
                flags = (_VERSE_EVENT_PROPERTY_FLAGS
                         if kind == "event" else _VERSE_PROPERTY_FLAGS)
            else:
                continue
            ctypes.c_uint64.from_address(descriptor + _METADATA_OFFSET).value = block
            ctypes.c_uint32.from_address(descriptor + _METADATA_OFFSET + 8).value = entries
            ctypes.c_uint32.from_address(descriptor + _METADATA_OFFSET + 12).value = entries
            ctypes.c_uint64.from_address(descriptor + _PROPERTY_FLAGS_OFFSET).value = flags
            if public_name is not None:
                patched.append(public_name)

        missing = sorted(set(created) - set(patched))
        if missing:
            raise RuntimeError(
                "created but could not patch %r — these are plain BP variables, "
                "not Verse fields. Delete them before retrying." % (missing,))

        if category:
            for _public, actual, _kind, _param in specs:
                _bel.set_blueprint_variable_category(wbp, actual, category)

        # Drop each event's function graph before saving. A real Verse event does
        # not persist one (Verse regenerates it on load), and the SAVED one
        # add_function_graph makes is restricted content: the validator's Sanitize
        # pass deletes the whole field. The graph IS needed up to here (the field
        # won't register as an event without it) -- create, patch, then remove.
        for public, _actual, kind, _param in specs:
            if kind == "event":
                _bel.remove_function_graph(wbp, public)

        # This is the save that has to carry the patched metadata into the file,
        # so it must go through the full save path -- save_asset would write the
        # package while leaving the VerseClassFields tag stale, and the fields
        # would come back as plain BP variables the next time the asset is read.
        _bel.compile_blueprint(wbp)
        _save_regenerating_tags(wbp_path)

        # VerseClassFields is regenerated by the engine on save -- the honest check.
        asset = _el.find_asset_data(wbp_path)
        blob = str(asset.get_tag_value("VerseClassFields") or "")
        present = set(re.findall(r'\(Name="([^"]+)"', blob))
        verified = {}
        for public, actual, kind, _param in specs:
            if kind == "event":
                pattern = (r'\(Name="%s",InternalName="%s".*?Type=Event,'
                           % (re.escape(public), re.escape(actual)))
                verified[public] = re.search(pattern, blob) is not None
            else:
                verified[public] = public in present
        failed = [n for n, ok in verified.items() if not ok]
        if failed:
            raise RuntimeError("patched but absent from VerseClassFields: %r" % (failed,))

        # Nothing is detached here, DELIBERATELY: the descriptors keep pointing at
        # our ctypes buffers all session (_KEEP holds them alive), and detaching
        # would make any later save write emptied metadata, demoting the fields to
        # plain BP variables. The danger is only a descriptor DESTROYED while still
        # attached (FMemory::Free on memory the engine never allocated) -- the two
        # places that can, _unload and delete_verse_fields, null the arrays first.

        return {"created": created, "skipped_existing": skipped, "verified": verified}

    @_keeps_editor_open
    def create_verse_fields(wbp_path, fields, category=None):
        """Transactional wrapper: on any failure, roll back the members and
        graphs this call created, so a half-made event never survives.
        """
        # An EVENT field adds a graph and compiles, which tears the tab down -- the
        # only op here that does. Close it deliberately so _reopen_editor knows to
        # put it back; once the engine destroys the tab there is nothing to detect.
        if any(_field_spec(f)[1] == "event" for f in fields):
            _close_editor(wbp_path)
        return _create_verse_fields_guarded(wbp_path, fields, category)

    def _create_verse_fields_guarded(wbp_path, fields, category=None):
        wbp = _el.load_asset(wbp_path)
        fields = [_field_spec(f) for f in fields]
        before_members = set(str(n) for n in _bel.list_member_variable_names(wbp))
        before_graphs = {name: _bel.find_graph(wbp, name) is not None
                         for name, kind, _param in fields if kind == "event"}
        try:
            return _create_verse_fields_impl(wbp_path, fields, category=category)
        except Exception as original:
            rollback_errors = []
            try:
                wbp = _el.load_asset(wbp_path)
                actual_names, event_names = [], []
                for public, kind, _param in fields:
                    actual = ("VerseFieldInternalVariable_" + public
                              if kind == "event" else public)
                    if actual not in before_members:
                        actual_names.append(actual)
                    if kind == "event" and not before_graphs.get(public, False):
                        if _bel.find_graph(wbp, public) is not None:
                            event_names.append(public)
                if "BackPointer" not in before_members:
                    current = set(str(n)
                                  for n in _bel.list_member_variable_names(wbp))
                    if "BackPointer" in current:
                        actual_names.append("BackPointer")

                # Memory-patched metadata may point at ctypes-owned buffers;
                # null those arrays before variable destruction. A pre-compile
                # failure simply has nothing to detach yet.
                if actual_names:
                    try:
                        _detach_metadata_arrays(wbp, wbp_path, actual_names)
                    except Exception as exc:
                        rollback_errors.append("metadata detach: %s" % exc)
                for public in event_names:
                    try:
                        _bel.remove_function_graph(wbp, public)
                    except Exception as exc:
                        rollback_errors.append(
                            "remove graph %s: %s" % (public, exc))
                editor = unreal.BlueprintGraphEditor.get_graph_editor_by_name(
                    wbp, "EventGraph")
                current = set(str(n) for n in _bel.list_member_variable_names(wbp))
                for actual in actual_names:
                    if actual in current:
                        try:
                            editor.remove_member_variable(actual)
                        except Exception as exc:
                            rollback_errors.append(
                                "remove member %s: %s" % (actual, exc))
                _bel.compile_blueprint(wbp)
                _el.save_asset(wbp_path)
            except Exception as exc:
                rollback_errors.append(str(exc))
            detail = ("; rollback warnings: %s" % "; ".join(rollback_errors)
                      if rollback_errors else "; partial creation rolled back")
            raise RuntimeError("%s%s" % (original, detail)) from original

    @_keeps_editor_open
    def set_fields_category(wbp_path, names, category):
        """Move member variables to `category` (public API), compile and save.

        Event fields store under their internal member name, so the public
        name shown in the UI has to be mapped before the engine call.
        """
        wbp = _el.load_asset(wbp_path)
        kinds = {f["name"]: f["type"] for f in list_verse_fields(wbp_path)}
        for name in names:
            actual = ("VerseFieldInternalVariable_" + name
                      if kinds.get(name) == "event" else name)
            _bel.set_blueprint_variable_category(wbp, actual, category)
        _bel.compile_blueprint(wbp)
        _el.save_asset(wbp_path)

    def _detach_metadata_arrays(wbp, wbp_path, names):
        """Null the MetaDataArray TArray of each named descriptor before it can
        be destroyed. A field created THIS session points at OUR ctypes buffers;
        destroying its descriptor live means FMemory::Free() on memory UE never
        allocated → heap-corruption crash. An empty TArray's destructor is a
        no-op, and nulling engine-owned arrays is harmless, so it's uniform."""
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

    @_keeps_editor_open
    def delete_verse_fields(wbp_path, names):
        """Delete member variables — safely, even same-session.

        NEVER use remove_unused_variables: it deletes EVERY unreferenced
        variable, including every not-yet-bound Verse field. Doomed fields'
        MetaDataArrays are detached first (see _detach_metadata_arrays), and
        bindings sourced from them are dropped (they would dangle).
        Returns {deleted, failed, bindings_dropped}.

        Deleting an EVENT field closes the widget's editor tab (it removes a
        function graph, and may run the disk patcher to drop that field's event
        bindings); deleting a plain field does not. _keeps_editor_open covers
        both rather than making the tab depend on which fields were picked.
        """
        if not names:
            return {"deleted": [], "failed": [], "bindings_dropped": 0}

        # BackPointer is shared by EVERY event field (their graphs use it as
        # the Target pin): deleting it breaks all of them at the next compile,
        # and recreating the variable does NOT repair the severed connections.
        if "BackPointer" in names:
            raise ValueError(
                "BackPointer is shared infrastructure for every event field on "
                "this widget and must not be deleted")

        # Event fields: drop their on-disk event bindings first (a deleted
        # field would leave them dangling), then delete under the INTERNAL
        # member name, and remove the public function graph too.
        field_types = {f["name"]: f["type"] for f in list_verse_fields(wbp_path)}
        event_fields = {n for n in names if field_types.get(n) == "event"}
        if event_fields:
            doomed_events = [e["event"] for e in list_event_bindings(wbp_path)
                             if e["field"] in event_fields]
            if doomed_events:
                remove_event_bindings(wbp_path, doomed_events)
        actual_names = [("VerseFieldInternalVariable_" + n
                         if field_types.get(n) == "event" else n)
                        for n in names]

        wbp = _el.load_asset(wbp_path)
        # Detach BEFORE removal so the descriptor destructor frees nothing of ours.
        _detach_metadata_arrays(wbp, wbp_path, actual_names)
        for public_name in event_fields:
            if _bel.find_graph(wbp, public_name) is not None:
                _bel.remove_function_graph(wbp, public_name)
        editor = unreal.BlueprintGraphEditor.get_graph_editor_by_name(wbp, "EventGraph")
        for actual_name in actual_names:
            editor.remove_member_variable(actual_name)

        doomed = set(actual_names) | set(names)
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

    def expand_batch_spec(spec, kind, start, count, param=None):
        """Expand a batch spec into create-field specs.

        `spec` is a name pattern. `#` (or `{n}`) marks where the running index
        goes; a bare pattern with count>1 gets the index appended. count==1 with
        no placeholder creates a single field. `param` is an event field's
        parameter (int/float/logic), so a whole batch can be `event (int)`.
        Returns (specs, error_or_None); each spec is (name, kind[, param]).
        """
        spec = spec.strip()
        if not spec:
            return [], "Enter a field name or pattern."
        if kind not in _CREATE_TYPES:
            return [], "Unknown type %r." % kind
        if param is not None and param not in _EVENT_PARAM_PINS:
            return [], "Unknown event parameter %r." % param

        def make(name):
            return (name, kind, param) if param else (name, kind)

        parts = _split_pattern(spec)
        if count <= 1 and parts is None:
            return [make(spec)], None
        pre, post = parts or (spec, "")

        return [make("%s%d%s" % (pre, i, post))
                for i in range(start, start + max(count, 1))], None

    # ═══════════════════════════════════════════════════════════════════════
    #  UI
    # ═══════════════════════════════════════════════════════════════════════

    _TYPE_COLOR = {
        "logic": "#ef3535", "int": "#1fe44b", "float": "#8bc24a",
        "string": "#ff5ecf", "message": "#ff8fd8",
        "color": "#0e86ff", "color_alpha": "#0e86ff",
        "material": "#00d9d9", "texture": "#00d9d9",
        "event": "#ffb800",
    }

    # Bulk mode only offers DIRECTLY-bindable props (it authors real bindings, so
    # a locked Brush has no place here). Restricted to the widgets UEFN exposes.
    _BULK_PROPS = {
        "TextBlock": ["Text", "ColorAndOpacity", "RenderOpacity"],
        "Image": ["ColorAndOpacity", "RenderOpacity"],
    }

    def _html(color, text):
        return '<span style="color:%s">%s</span>' % (color, text)

    class VerseBinderWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Verse Fields + Binding Tool")
            # Wide enough that the Bind tab's four target columns all fit without
            # clipping -- a Custom Button's name and class are long, and a clipped
            # TARGET column hides the event dropdown entirely.
            self.setMinimumSize(940, 680)
            self.resize(1320, 820)
            self._wbp_path = None
            self._fields = []
            self._visible_fields = []   # _fields after the category filter + sort
            self._manage_rows = []      # _fields after the Manage tab's sort
            self._widgets = []
            self._child_widgets = []
            self._event_widgets = []    # only widgets that declare event delegates
            self._target_rows = []
            self._pending_pairs = []
            self._pending_target = None
            self._build_ui()
            # Every engine op that reopens the widget raises UEFN over the tool.
            # Registering here (rather than calling _reclaim_focus from each
            # handler) means failure paths -- which return early -- get the focus
            # back too. Replace, don't append: reopening the tool builds a fresh
            # window, and a stale callback would target a deleted one.
            _ON_EDITOR_RAISED[:] = [self._reclaim_focus]

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
            self.fields_sort = SortHeader(0, lambda _s: self._refresh_fields())
            self.tbl_fields.setHorizontalHeader(self.fields_sort)
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
            # Only a PARAMETERISED event field can carry a value, so this whole
            # group hides itself for anything else (see _sync_param_row) rather
            # than sitting there greyed out for the majority of fields.
            self.param_label = QLabel("Param 0:")
            self.param_value = QLineEdit()
            self.param_value.setPlaceholderText("0")
            self.param_value.setFixedWidth(70)
            self.param_value.setToolTip(
                "The value each button passes to the event.\n"
                "With Auto-number ticked, this is the FIRST value and the rest\n"
                "count up in row order — 10 buttons from 1 give 1..10.")
            # A logic parameter is a BOOLEAN, so it gets a tick rather than the
            # text box -- typed free text there could only ever be "true"/"false"
            # (anything else is a value the engine cannot parse), and a checkbox
            # cannot express one.
            self.param_bool = TickBox("true")
            self.param_bool.setToolTip(
                "The logic value each selected button passes to the event.")
            self.param_bool.toggled.connect(
                lambda on: self.param_bool.setText("true" if on else "false"))
            self.param_auto = TickBox("Auto-number")
            self.param_auto.setChecked(True)
            self.param_auto.setToolTip(
                "Give each selected button the next value in sequence.\n"
                "Untick to pass the SAME value to all of them.")
            for w in (self.param_label, self.param_value, self.param_bool,
                      self.param_auto):
                w.setVisible(False)
            trow.addWidget(self.param_label)
            trow.addWidget(self.param_value)
            trow.addWidget(self.param_bool)
            trow.addWidget(self.param_auto)

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
            # The combo is editable, so a hand-typed pattern never fires activated();
            # without this the target list would still be filtered for the OLD family.
            self.field_pattern.lineEdit().editingFinished.connect(
                self._auto_pick_bulk_target)
            self.widget_pattern = ArrowCombo()
            self.widget_pattern.setEditable(True)
            self.widget_pattern.lineEdit().setPlaceholderText("Slot#  or  Image#")
            # The target list is filtered by which widgets this pattern matches --
            # a family of TextBlocks must not be offered Image · ColorAndOpacity.
            self.widget_pattern.activated.connect(self._refresh_target_combo)
            self.widget_pattern.lineEdit().editingFinished.connect(
                self._refresh_target_combo)
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
            self.create_type.addItems(_CREATE_LABELS)
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
            self.manage_sort = SortHeader(0, lambda _s: self._refresh_manage())
            self.tbl_manage.setHorizontalHeader(self.manage_sort)
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
            self.manage_log.append(_html(color, msg) if color else msg)

        def _fill_field_rows(self, table, fields):
            """Fill a Field/Type/Category table from a field list."""
            table.setRowCount(len(fields))
            for r, field in enumerate(fields):
                table.setItem(r, 0, QTableWidgetItem(field["name"]))
                # An event's parameter rides in the type cell -- "event (int)" --
                # but the colour still keys off the kind, so every event reads as
                # one family.
                label = field["type"]
                if field.get("param"):
                    label = "%s (%s)" % (label, field["param"])
                type_item = QTableWidgetItem(label)
                type_item.setForeground(QColor(_TYPE_COLOR.get(field["type"], C_TX0)))
                table.setItem(r, 1, type_item)
                cat_item = QTableWidgetItem(field.get("category", "Default"))
                cat_item.setForeground(QColor(C_TX2))
                table.setItem(r, 2, cat_item)
            self._fit_columns(table, stretch_col=0)

        def _fill_category_combo(self, combo):
            """Offer the widget's existing categories, keeping any typed text.

            The field stays empty unless the user typed/picked something — an
            editable combo would otherwise show item 0, defeating the placeholder.
            """
            typed = combo.currentText()
            combo.clear()
            for cat in sorted({f.get("category", "Default") for f in self._fields}):
                combo.addItem(cat)
            combo.setCurrentText(typed)
            if not typed:
                combo.setCurrentIndex(-1)

        def _refresh_manage(self):
            # Rows map to _manage_rows, not _fields — the sort may reorder them.
            self._manage_rows = self._apply_sort(
                list(self._fields), self.manage_sort.state)
            self._fill_field_rows(self.tbl_manage, self._manage_rows)
            self._fill_category_combo(self.manage_category)

        def _manage_selected(self):
            # Index the SORTED list — table rows map to _manage_rows.
            rows = self.tbl_manage.selectionModel().selectedRows()
            return [self._manage_rows[r.row()]["name"]
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

        def _refresh_target_combo(self, _index=None):
            """Bulk target list, filtered to what the CHOSEN PATTERNS can bind.

            A function of BOTH patterns: the field pattern rules out whole kinds
            (an event field can never bind a property, a color never an event),
            the widget pattern rules out the rest (Image · ColorAndOpacity
            cannot match a family of TextBlocks).
            """
            self.target_combo.clear()
            types = self._pattern_field_types()
            props = types - {"event"}

            if props:
                # Only classes the matched widgets really are, and only properties a
                # field of the chosen type binds to with no conversion.
                for cls in sorted(_BULK_PROPS):
                    if cls not in self._pattern_widget_classes():
                        continue
                    for prop in _BULK_PROPS[cls]:
                        if not any(is_direct_bindable(t, cls, prop) for t in props):
                            continue
                        self.target_combo.addItem("%s · %s" % (cls, prop),
                                                  ("native", cls, prop))
                # Sub-widget fields: bind field-to-field, so the types must match.
                for name in sorted({f["name"] for c in self._pattern_children()
                                    for f in c["fields"] if f["type"] in props}):
                    self.target_combo.addItem("Sub-widget · %s" % name,
                                              ("child", name))

            # Event targets. Offer each label once, no matter how the buttons spell it.
            if "event" in types:
                for label in sorted({label for w in self._pattern_event_widgets()
                                     for _d, label in w["delegates"]}):
                    self.target_combo.addItem("Event · %s" % label, ("event", label))

        def _matched(self, items):
            """The `items` whose names match the current widget pattern.

            An empty or unmatched pattern narrows nothing -- fall back to all of them, so
            a half-typed pattern empties the target list instead of just not filtering.
            """
            pattern = self.widget_pattern.currentText().strip()
            if not pattern:
                return items
            hits = [i for group in _index_by_pattern(items, pattern).values()
                    for i in group]
            return hits or items

        def _pattern_widget_classes(self):
            return {w["native"] for w in self._matched(self._widgets)}

        def _pattern_children(self):
            return self._matched(self._child_widgets)

        def _pattern_event_widgets(self):
            return self._matched(self._event_widgets)

        def _pattern_field_types(self):
            """Verse types of the fields the current field pattern actually matches.

            An unmatched or empty pattern tells us nothing about intent, so fall back to
            every type on the widget -- an empty target list would look like a bug.
            """
            pattern = self.field_pattern.currentText().strip()
            if pattern:
                matched = _index_by_pattern(self._fields, pattern)
                types = {f["type"] for fs in matched.values() for f in fs}
                if types:
                    return types
            return {f["type"] for f in self._fields}

        def _refresh_widget_pattern_combo(self):
            """Widget families the CHOSEN FIELD FAMILY could actually bind to.

            Same rule as the target list: an event field binds a button and nothing
            else, so offering Image# next to Slot1_# just invites a dead pairing.
            """
            keep = self.widget_pattern.currentText().strip()
            self.widget_pattern.clear()
            types = self._pattern_field_types()
            names = []
            if types - {"event"}:
                # _widgets holds only the PROPERTY-bindable widgets (Image/TextBlock).
                names += [w["name"] for w in self._widgets]
                names += [c["name"] for c in self._child_widgets]
            if "event" in types:
                names += [w["name"] for w in self._event_widgets]
            for pat, _n in detect_patterns(names):
                self.widget_pattern.addItem(pat)
            if keep and self.widget_pattern.findText(keep) >= 0:
                self.widget_pattern.setCurrentText(keep)

        def _refresh_bulk_suggestions(self):
            """Fill both pattern dropdowns with families detected from real names."""
            self.field_pattern.clear()
            for pat, _n in detect_patterns([f["name"] for f in self._fields]):
                self.field_pattern.addItem(pat)
            self._refresh_widget_pattern_combo()
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
                    self._refresh_widget_pattern_combo()

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
            # The widget list depends on the chosen field family, so re-derive it before
            # scanning. The TARGET combo cannot be the source of candidates here: it is
            # itself narrowed by the current widget pattern, and this scan is looking for
            # a BETTER widget pattern -- so enumerate the unfiltered targets instead.
            self._refresh_widget_pattern_combo()
            wpats = [self.widget_pattern.itemText(i)
                     for i in range(self.widget_pattern.count())]
            if not wpats:
                wpats = [self.widget_pattern.currentText().strip()]
            best = None   # (pair_count, widget_pattern, target_data)
            for data in self._all_bulk_targets():
                for wpat in wpats:
                    if not wpat:
                        continue
                    pairs, _ = self._match_for_target(data, fpat, wpat)
                    if pairs and (best is None or len(pairs) > best[0]):
                        best = (len(pairs), wpat, data)
            if best:
                # Widget pattern first: the target list is filtered by it.
                self.widget_pattern.setCurrentText(best[1])
                self._refresh_target_combo()
                index = self.target_combo.findData(best[2])
                if index >= 0:
                    self.target_combo.setCurrentIndex(index)
                self._log("Auto-detected: %s  →  %s . %s   (%d pairs — "
                          "Preview Matches to confirm)"
                          % (fpat, best[1], self.target_combo.currentText(), best[0]),
                          C_TX2)
                return True
            self._refresh_target_combo()
            if _index is not None:   # user picked this pattern — tell them it's dry
                self._log("No widget pattern/target pairs up with '%s' — "
                          "set them manually." % fpat, C_TX2)
            return False

        def _match_for_target(self, data, fpat, wpat):
            """Dispatch a target tuple to its matcher -> (pairs, warnings)."""
            if data[0] == "child":
                return match_child_by_suffix(self._fields, self._child_widgets,
                                             fpat, wpat, data[1])
            if data[0] == "event":
                return match_events_by_suffix(self._fields, self._event_widgets,
                                              fpat, wpat, data[1])
            return match_by_suffix(self._fields, self._widgets, fpat, wpat, data[2])

        def _all_bulk_targets(self):
            """Every target this widget could offer, ignoring the current patterns."""
            out = []
            for cls in sorted(_BULK_PROPS):
                if cls in {w["native"] for w in self._widgets}:
                    out += [("native", cls, prop) for prop in _BULK_PROPS[cls]]
            out += [("child", name) for name in
                    sorted({f["name"] for c in self._child_widgets for f in c["fields"]})]
            out += [("event", label) for label in
                    sorted({l for w in self._event_widgets for _d, l in w["delegates"]})]
            return out

        def _log(self, msg, color=None):
            self.log.append(_html(color, msg) if color else msg)
            bar = self.log.verticalScrollBar()
            bar.setValue(bar.maximum())
            unreal.log("[VerseBinder] %s" % msg)
            QApplication.processEvents()

        def _reclaim_focus(self):
            """Bring the tool back to the front after an engine operation.

            Qt's raise_()/activateWindow() are not enough: Windows only lets the
            foreground-owning process hand focus away, so the fix is Win32
            AttachThreadInput -> SetForegroundWindow -> detach (ctypes.windll;
            pywin32 doesn't exist in UEFN's Python). The editor also raises
            itself LATE — Slate focus work lands after this returns — so the
            grab is re-asserted on a few later event-loop turns.

            Do NOT call this from an operation handler: _keeps_editor_open fires
            it (via _ON_EDITOR_RAISED) for every mutating call, on the failure
            path too. Calling it by hand is what left the error branches -- which
            return early -- stuck behind the editor.
            """
            self._grab_focus()
            for delay in (60, 250, 600):
                QTimer.singleShot(delay, self._grab_focus)

        def _grab_focus(self):
            """One attempt at taking the foreground. Safe to call repeatedly."""
            try:
                self.setWindowState(
                    (self.windowState() & ~Qt.WindowState.WindowMinimized)
                    | Qt.WindowState.WindowActive)
                self.show()
                self.raise_()
                self.activateWindow()

                user32 = ctypes.windll.user32
                hwnd = int(self.winId())
                fg = user32.GetForegroundWindow()
                if not hwnd or fg == hwnd:
                    return
                ours = user32.GetWindowThreadProcessId(hwnd, None)
                theirs = user32.GetWindowThreadProcessId(fg, None)
                attached = theirs and ours != theirs and user32.AttachThreadInput(
                    theirs, ours, True)
                try:
                    user32.SetForegroundWindow(hwnd)
                    user32.BringWindowToTop(hwnd)
                    user32.SetActiveWindow(hwnd)
                finally:
                    if attached:
                        user32.AttachThreadInput(theirs, ours, False)
            except Exception:
                pass          # focus is a nicety; never fail the operation over it

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
                entries = _widget_tree_entries(path)   # one T3D export for all three
                widgets = list_widgets(path, entries)
                children = list_child_widgets(path, entries)
                event_widgets = list_event_widgets(path, entries)
            except Exception as exc:
                self._log("Load failed: %s" % exc, C_ERR)
                return
            self._wbp_path, self._fields = path, fields
            self._widgets, self._child_widgets = widgets, children
            self._event_widgets = event_widgets
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
            self._fill_category_combo(self.create_category)

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

        @staticmethod
        def _name_key(name):
            """Natural sort key: VF_Slot2 before VF_Slot10, not after.

            Plain alphabetical scatters numbered field families ("Slot10" < "Slot2"),
            which is the exact layout this tool encourages.
            """
            return [int(t) if t.isdigit() else t.lower()
                    for t in re.split(r"(\d+)", name)]

        @classmethod
        def _apply_sort(cls, fields, state):
            """Order `fields` by name for the header's sort state.

            Sorts the BACKING LIST, not the view — row index must keep mapping to
            the same field, since bind/delete resolve targets by row.
            """
            if state == SORT_NONE:
                return fields
            return sorted(fields, key=lambda f: cls._name_key(f["name"]),
                          reverse=(state == SORT_DESC))

        def _refresh_fields(self):
            cat = self.category_filter.currentText()
            self._visible_fields = [f for f in self._fields
                                    if cat == "All" or f.get("category", "Default") == cat]
            self._visible_fields = self._apply_sort(
                self._visible_fields, self.fields_sort.state)
            self._fill_field_rows(self.tbl_fields, self._visible_fields)

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
            # Every path out of here goes through this, including the early
            # returns -- deselecting must put the Param 0 row away too.
            self._sync_param_row(field)
            if not field or not self._wbp_path:
                return

            if field["type"] == "event":
                self._refresh_event_targets(field)
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

        def _sync_param_row(self, field):
            """Show the Param 0 control only for a field that HAS a parameter.

            A plain event, and every non-event field, has no pin to hold a value,
            so the row hides rather than greying out -- most fields are not
            parameterised and a permanently-dead control just adds noise.
            """
            param = (field or {}).get("param")
            numeric = param in ("int", "float")

            self.param_label.setVisible(bool(param))
            # A logic parameter takes the tick; int/float take the text box. Only
            # ONE is ever up, so there is no dead control to wonder about -- and
            # numbering is meaningless for a bool, so Auto-number goes away with
            # the box rather than sitting there disabled.
            self.param_value.setVisible(numeric)
            self.param_auto.setVisible(numeric)
            self.param_bool.setVisible(param == "logic")
            if not param:
                return

            self.param_label.setText("Param 0 (%s):" % param)
            if numeric:
                # Constrain the box to the parameter's own type, so a value the
                # engine cannot parse simply cannot be typed. Rebuild it on every
                # switch: an int validator left on a float field would refuse the
                # decimal point.
                if param == "int":
                    self.param_value.setValidator(QIntValidator(self.param_value))
                else:
                    # StandardNotation: a float pin takes "2.5", not "2.5e0".
                    validator = QDoubleValidator(self.param_value)
                    validator.setNotation(
                        QDoubleValidator.Notation.StandardNotation)
                    self.param_value.setValidator(validator)

                # Seed the box with the field's own default the first time it is
                # shown, so binding without touching it matches what the MVVM
                # panel would have produced by hand. Re-seed when switching kinds
                # too: "2.5" left over from a float field is not a valid int, and
                # the validator would strand it there unfixable.
                text = self.param_value.text().strip()
                if not text or self.param_value.hasAcceptableInput() is False:
                    self.param_value.setText(_EVENT_PARAM_DEFAULTS.get(param, ""))

        def _param_values(self, field, count):
            """The value for each of `count` buttons, in row order -> [str] or None.

            None when the field takes no parameter, which leaves every caller's
            addition tuple 3 long and the binding parameterless.
            """
            param = (field or {}).get("param")
            if not param:
                return None

            # A bool has nothing to count from: every button gets the tick's
            # state, spelled the way the engine spells it.
            if param == "logic":
                return ["true" if self.param_bool.isChecked() else "false"] * count

            # The box carries an Int/Double validator, so the text is a number of
            # the right kind -- except for the partial entries a validator must
            # permit while typing ("", "-", "2." on the way to "2.5"), which the
            # field's own default stands in for.
            text = self.param_value.text().strip()
            try:
                start = int(text) if param == "int" else float(text)
            except ValueError:
                start = float(_EVENT_PARAM_DEFAULTS[param])
                if param == "int":
                    start = int(start)

            # Auto-number counts up the rows; unticked, every button gets `start`.
            step = 1 if self.param_auto.isChecked() else 0

            if param == "int":
                return [str(start + i * step) for i in range(count)]

            # A float pin wants a float LITERAL: "2", which the validator happily
            # accepts, has to go out as "2.0". Keep the typed precision otherwise,
            # so 0.5 counts 0.5, 1.5, 2.5.
            decimals = max(len(text.split(".")[1]) if "." in text else 0, 1)
            return ["%.*f" % (decimals, start + i * step) for i in range(count)]

        def _refresh_event_targets(self, field):
            """Event fields bind a widget's DELEGATE, not a property.

            Rows come from the saved package (events live on disk). Creation
            clones an existing event, and a widget with none gets one seeded, so
            every row is bindable even on a widget that has never had an event.
            """
            try:
                events = list_event_bindings(self._wbp_path)
            except Exception as exc:
                self._log("Could not read event bindings: %s" % exc, C_ERR)
                return

            # (widget, delegate) -> LIST of events: one delegate may drive
            # several Verse fields; keying one-to-one would hide all but the last.
            bound = {}
            for ev in events:
                bound.setdefault((ev["widget"], ev["delegate"]), []).append(ev)

            # ONE row per widget, the event picked in the row (a row per
            # widget x delegate is unusable: 4 buttons x 7 events = 28 rows).
            rows = []
            for w in self._event_widgets:
                mine = [d for d, _l in w["delegates"]
                        if any(e["field"] == field["name"]
                               for e in bound.get((w["name"], d), ()))]
                # Land on the event this field is already bound to, else On Clicked.
                current = mine[0] if mine else w["delegates"][0][0]
                rows.append({"kind": "event", "widget": w["name"],
                             "display": w.get("display", w["name"]),
                             "native": w["native"],
                             "label": w.get("label", w["native"]),
                             "delegates": w["delegates"],
                             "delegate": current,
                             "bound": bound,
                             # Always bindable: a widget with no event to clone
                             # gets one seeded (see _seed_first_event), so the
                             # first binding no longer has to be made in the UI.
                             "bindable": True,
                             "has_conv": False})

            self._target_rows = rows
            self.tbl_targets.setRowCount(len(rows))
            for r, row in enumerate(rows):
                # Show the editor's names; the object name and real class are what get
                # serialized, so keep both reachable on hover.
                w_item = QTableWidgetItem(row["display"])
                w_item.setToolTip(row["widget"])
                self.tbl_targets.setItem(r, 0, w_item)
                cls_item = QTableWidgetItem(row["label"])
                cls_item.setToolTip(row["native"])
                self.tbl_targets.setItem(r, 1, cls_item)

                combo = ArrowCombo()      # plain QComboBox has no arrow: see ArrowCombo
                for name, label in row["delegates"]:
                    combo.addItem(label, name)
                combo.setCurrentIndex(
                    [n for n, _l in row["delegates"]].index(row["delegate"]))
                combo.setEnabled(row["bindable"])
                # Rebind the row's delegate on pick, and refresh CURRENT beside it --
                # what a widget is already bound to depends on WHICH event is chosen.
                combo.currentIndexChanged.connect(
                    lambda _i, row=row, r=r, combo=combo, field=field:
                        (row.__setitem__("delegate", combo.currentData()),
                         self._set_event_current(r, row, field)))
                self.tbl_targets.setCellWidget(r, 2, combo)

                self._set_event_current(r, row, field)
            # ResizeToContents measures the cell's ITEM; a cell WIDGET has none,
            # so the TARGET column would collapse and clip the combo's drop-down
            # button. Size that one column to the widgets it actually holds.
            self._fit_columns(self.tbl_targets, stretch_col=3)
            header = self.tbl_targets.horizontalHeader()
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
            want = max((self.tbl_targets.cellWidget(r, 2).sizeHint().width()
                        for r in range(len(rows))), default=120)
            self.tbl_targets.setColumnWidth(2, want + 12)

            if not rows:
                self._log("No widget on this blueprint can source an event.",
                          C_TX2)

        def _set_event_current(self, r, row, field):
            """Fill the CURRENT column for one event row, for its CHOSEN delegate.

            A delegate can drive several fields, so list them all -- showing only one
            would make the others invisible and look like binding had replaced them.
            """
            evs = row["bound"].get((row["widget"], row["delegate"]), [])
            others = [e["field"] for e in evs if e["field"] != field["name"]]
            mine = next((e for e in evs if e["field"] == field["name"]), None)
            has_mine = mine is not None

            # For a parameterised event the VALUE is the whole point of the row --
            # "← this field" alone cannot tell 10 buttons on one field apart.
            label = "← this field"
            if mine and mine.get("value") is not None:
                label = "← this field  = %s" % mine["value"]

            if has_mine and others:
                item = QTableWidgetItem(label + "  + " + ", ".join(others))
                item.setForeground(QColor(C_OK))
            elif has_mine:
                item = QTableWidgetItem(label)
                item.setForeground(QColor(C_OK))
            elif others:
                item = QTableWidgetItem(", ".join(others))
                item.setForeground(QColor(C_WARN))
            else:
                item = QTableWidgetItem("—")
                item.setForeground(QColor(C_TX2))
            self.tbl_targets.setItem(r, 3, item)

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
            if field["type"] == "event":
                # One delegate may drive SEVERAL fields, so skip only an exact
                # duplicate of THIS field on THIS delegate — skipping every
                # already-bound row would lock the button to its first field.
                fresh = []
                for t in targets:
                    evs = t["bound"].get((t["widget"], t["delegate"]), [])
                    if any(e["field"] == field["name"] for e in evs):
                        continue          # this exact binding already exists
                    fresh.append(t)
                if not fresh:
                    self._log("The selected widget(s) already fire this event.",
                              C_WARN)
                    return
                # Number the buttons that are ACTUALLY being bound: counting over
                # `targets` would burn a value on every already-bound row and leave
                # gaps in the sequence.
                values = self._param_values(field, len(fresh))
                additions = []
                for i, t in enumerate(fresh):
                    addition = (t["widget"], field["name"], t["delegate"])
                    if values is not None:
                        addition += (values[i],)
                        self._log("  %s  →  %s   Param 0 = %s"
                                  % (t["widget"], field["name"], values[i]))
                    additions.append(addition)
                self._run_create_events(additions)
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
            if fields[0]["type"] == "event":
                # Target rows were built for an event field, so every pair must
                # be one.
                if any(f["type"] != "event" for f in fields):
                    self._log("Pairing mixes event and non-event fields — "
                              "select one kind at a time.", C_WARN)
                    return
                # The Param 0 box tracks the FIRST selected field, so it can only
                # speak for pairs whose fields all share that parameter. Numbering
                # across a mix of int and float fields would be nonsense.
                values = None
                if len({f.get("param") for f in fields}) == 1:
                    values = self._param_values(fields[0], len(fields))

                additions = []
                for i, (f, t) in enumerate(zip(fields, targets)):
                    # Pass the row's OWN delegate, or a clone keeps the template's.
                    addition = (t["widget"], f["name"], t["delegate"])
                    note = ""
                    if values is not None:
                        addition += (values[i],)
                        note = "   Param 0 = %s" % values[i]
                    additions.append(addition)
                    self._log("  pair  %s  →  %s.%s%s"
                              % (f["name"], t["widget"], t["delegate"], note))
                self._run_create_events(additions)
                return
            pairs = [self._row_to_pair(f["name"], t) for f, t in zip(fields, targets)]
            for f, t in zip(fields, targets):
                self._log("  pair  %s  →  %s.%s" % (f["name"], t["widget"], t["dest"]))
            self._run_apply(pairs)

        def _run_create_events(self, additions):
            """Create event bindings by cloning on disk; the widget is closed,
            patched, reloaded and recompiled — on failure the backup wins."""
            try:
                result = create_event_bindings(self._wbp_path, additions)
            except Exception as exc:
                self._log("Event create failed (asset restored from backup): %s"
                          % exc, C_ERR)
                return
            for name, widget, field in result["created"]:
                self._log("  created event   %s  →  %s   (%s)"
                          % (widget, field, name), C_OK)
            self._log("%d event binding(s) created and compiled."
                      % len(result["created"]), C_OK)
            self._refresh_targets()

        def _unbind_selected(self):
            targets = self._selected_target_rows()
            if not targets:
                self._log("Select target(s) to unbind.", C_WARN)
                return
            if targets[0].get("kind") == "event":
                # Unbind the event on each row's CHOSEN delegate, and only when it
                # is this field's -- a button bound to another field on the same
                # delegate is not ours to remove.
                field = self._selected_field()
                doomed = []
                for t in targets:
                    for ev in t["bound"].get((t["widget"], t["delegate"]), []):
                        # A delegate may drive several fields; drop only this one's.
                        if not field or ev["field"] == field["name"]:
                            doomed.append(ev["event"])
                if not doomed:
                    self._log("The selected widget(s) have no event for this "
                              "field.", C_WARN)
                    return
                try:
                    removed = remove_event_bindings(self._wbp_path, doomed)
                except Exception as exc:
                    self._log("Event unbind failed: %s" % exc, C_ERR)
                    return
                self._log("Removed %d event binding(s)." % removed, C_OK)
                self._refresh_targets()
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
            """(field, widget, dest) label for a native tuple, child dict or event tuple."""
            if isinstance(pair, dict):
                return pair["field"], pair["widget"], pair["child_field"]
            if len(pair) == 3:                 # event: (field, widget, delegate)
                return pair
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
            pairs, warnings = self._match_for_target(
                target, self.field_pattern.currentText().strip(),
                self.widget_pattern.currentText().strip())
            self.log.clear()
            for warning in warnings:
                self._log("  ! %s" % warning, C_WARN)
            if not pairs:
                self._log("No matching pairs.", C_ERR)
                self.btn_apply_bulk.setEnabled(False)
                self._pending_pairs = []
                self._pending_target = None
                return
            for pair in pairs:
                field, widget, dest = self._pair_label(pair)
                self._log("  %s  →  %s.%s" % (field, widget, dest))
            self._log("%d pair(s) ready. Click Bind All to apply." % len(pairs), C_OK)
            self._pending_pairs = pairs
            self._pending_target = target[0]   # event pairs bind by a different path
            self.btn_apply_bulk.setEnabled(True)

        def _apply_bulk(self):
            if not self._pending_pairs:
                return
            pairs = self._pending_pairs
            # Event pairs are (field, widget, delegate) and go through the disk
            # patcher; property pairs go through the engine API. The two are never
            # mixed -- one target is chosen for the whole batch.
            if self._pending_target == "event":
                self._run_create_events(
                    [(widget, field, delegate) for field, widget, delegate in pairs])
            else:
                self._run_apply(pairs)
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
            kind, param = _split_create_label(self.create_type.currentText())
            return expand_batch_spec(
                self.create_name.text(), kind,
                self.create_start.value(), self.create_count.value(), param)

        def _preview_create(self):
            pairs, error = self._create_pairs()
            self.create_preview.clear()
            if error:
                self.create_preview.append(_html(C_WARN, error))
                if hasattr(self, "btn_create"):
                    self.btn_create.setEnabled(False)
                return
            existing = {f["name"] for f in self._fields} if self._wbp_path else set()
            cat = self.create_category.currentText().strip()
            lines = [_html(C_TX2, "Will create %d field(s)%s:" % (
                len(pairs), (" in category '%s'" % cat) if cat else ""))]
            for name, kind, param in (_field_spec(p) for p in pairs):
                label = "%s (%s)" % (kind, param) if param else kind
                if name in existing:
                    lines.append(_html(C_WARN, "  %s : %s   (exists — skipped)"
                                       % (name, label)))
                else:
                    lines.append("  %s : %s" % (name, label))
            self.create_preview.append("\n".join(lines))
            if hasattr(self, "btn_create"):
                self.btn_create.setEnabled(True)

        def _run_create(self):
            if not self._wbp_path:
                self.create_preview.append(
                    _html(C_WARN, "Load a Widget Blueprint first."))
                return
            pairs, error = self._create_pairs()
            if error:
                self.create_preview.append(_html(C_ERR, error))
                return
            category = self.create_category.currentText().strip() or None
            try:
                result = create_verse_fields(self._wbp_path, pairs, category=category)
            except Exception as exc:
                self.create_preview.append(_html(C_ERR, "Create failed: %s" % exc))
                return

            self.create_preview.clear()
            for name in result["created"]:
                self.create_preview.append(_html(C_OK, "  created  %s" % name))
            for name in result["skipped_existing"]:
                self.create_preview.append(
                    _html(C_WARN, "  skipped  %s (already exists)" % name))
            self.create_preview.append(
                _html(C_OK, "Done: %d created, all verified as Verse fields."
                      % len(result["created"])))
            unreal.log("[VerseBinder] created %d field(s)" % len(result["created"]))
            # Refresh the Bind tab so the new fields show up immediately.
            self._load()

    # ═══════════════════════════════════════════════════════════════════════
    #  ENTRY POINT
    # ═══════════════════════════════════════════════════════════════════════

    def main():
        app = QApplication.instance() or QApplication(sys.argv)

        # ALWAYS re-apply the stylesheet: the QApplication persists across
        # re-runs, so an older style would stick forever otherwise.
        app.setStyleSheet(STYLE)

        def _repolish(w):
            w.style().unpolish(w)
            w.style().polish(w)
            w.update()

        # The window ref lives on the persistent QApplication (each run execs
        # this file in a fresh namespace, so a module global is always None).
        # The ref can outlive the window: after the user closes it, ANY call on
        # the wrapper (even isVisible) raises "Internal C++ object already
        # deleted" — a dead ref must read as "no window" or reopening breaks.
        win = getattr(app, "_verse_binder_win", None)
        try:
            alive = win is not None and win.isVisible()
        except RuntimeError:
            alive = False
        if alive:
            _repolish(win)
            for child in win.findChildren(QWidget):
                _repolish(child)
            win._reclaim_focus()
            return

        win = VerseBinderWindow()
        app._verse_binder_win = win   # also keeps the window alive (GC anchor)
        win.setWindowFlags(Qt.WindowType.Window)
        win.show()
        win._reclaim_focus()

        # Auto-load the Content Browser selection if it's a widget (field then
        # shows just its name). Otherwise the field starts empty — no default path.
        for asset in unreal.EditorUtilityLibrary.get_selected_assets():
            if isinstance(asset, unreal.WidgetBlueprint):
                win._load(asset.get_path_name())
                break

    main()
