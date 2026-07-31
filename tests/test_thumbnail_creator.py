from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TOOL_FILE = REPOSITORY_ROOT / "tools" / "thumbnail_creator_tool.py"


def _load_tool_module():
    fake_unreal = types.ModuleType("unreal")
    fake_unreal.log_warning = lambda *_args, **_kwargs: None
    sys.modules.setdefault("unreal", fake_unreal)

    module_name = "thumbnail_creator_tool_under_test"
    spec = importlib.util.spec_from_file_location(module_name, TOOL_FILE)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load %s" % TOOL_FILE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


tool = _load_tool_module()
AdjustState = tool.AdjustState
CameraState = tool.CameraState
CaptureSource = tool.CaptureSource
ExportOptions = tool.ExportOptions
SourceKind = tool.SourceKind
ThumbnailCreatorState = tool.ThumbnailCreatorState
ThumbnailCreatorUIState = tool.ThumbnailCreatorUIState
render_pattern = tool.render_pattern


class ThumbnailCreatorStateTests(unittest.TestCase):
    def test_active_state_round_trip(self):
        state = ThumbnailCreatorState(
            source=CaptureSource(
                SourceKind.STATIC_MESH,
                ["/Game/Props/SM_Crate.SM_Crate"],
                "SM_Crate",
            ),
            camera=CameraState(yaw=15.0, pitch=-10.0),
            adjust=AdjustState(contrast=1.25, outline_width=3),
            export=ExportOptions(
                output_size=1024,
                naming_pattern="{name}_{preset}_{size}",
                preset_name="Cinematic",
            ),
            preview_background="Dark",
            live_preview=True,
            ui=ThumbnailCreatorUIState(
                diagnostics_visible=True,
                overlay_thirds=True,
                safe_frame=90,
            ),
        )

        restored = ThumbnailCreatorState.from_dict(state.to_dict())

        self.assertEqual(restored.source, state.source)
        self.assertEqual(restored.camera, state.camera)
        self.assertEqual(restored.adjust, state.adjust)
        self.assertEqual(restored.export, state.export)
        self.assertEqual(restored.preview_background, "Dark")
        self.assertTrue(restored.live_preview)
        self.assertEqual(restored.ui, state.ui)

    def test_legacy_session_keys_are_ignored(self):
        legacy = {
            "active_preset": "Old Camera Preset",
            "last_png": "C:/old/thumbnail.png",
            "export": {"preset_name": "Legacy", "output_size": 256},
            "ui": {
                "inspector_tab": "Look",
                "utility_tab": "Library",
                "utility_visible": True,
                "library_view": "Table",
                "favorite_presets": ["objects:Old Camera Preset"],
                "overlay_center": True,
                "safe_frame": 80,
            },
        }

        restored = ThumbnailCreatorState.from_dict(legacy)
        serialized = restored.to_dict()

        self.assertFalse(hasattr(restored, "active_preset"))
        self.assertFalse(hasattr(restored, "last_png"))
        self.assertEqual(restored.export.preset_name, "Legacy")
        self.assertTrue(restored.ui.overlay_center)
        self.assertEqual(restored.ui.safe_frame, 80)
        self.assertNotIn("active_preset", serialized)
        self.assertNotIn("last_png", serialized)
        for removed_key in (
            "inspector_tab",
            "utility_tab",
            "utility_visible",
            "library_view",
            "favorite_presets",
        ):
            self.assertNotIn(removed_key, serialized["ui"])


class ThumbnailNamingTests(unittest.TestCase):
    def test_preset_token_remains_supported(self):
        rendered = render_pattern(
            "{name}_{preset}_{size}",
            source_path="/Game/Props/SM Crate.SM Crate",
            index=1,
            preset="My Lighting",
            size=512,
        )

        self.assertEqual(rendered, "SM_Crate_My_Lighting_512")


class ThumbnailCreatorLayoutTests(unittest.TestCase):
    def test_tool_is_self_contained(self):
        source = TOOL_FILE.read_text(encoding="utf-8")

        self.assertTrue(TOOL_FILE.is_file())
        self.assertFalse((TOOL_FILE.parent / "thumbnail_creator").exists())
        self.assertNotIn("from .", source)
        self.assertNotIn("thumbnail_creator.", source)


if __name__ == "__main__":
    unittest.main()
