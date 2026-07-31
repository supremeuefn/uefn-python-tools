from __future__ import annotations

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
TOOL_FILE = REPOSITORY_ROOT / "tools" / "thumbnail_creator_tool.py"
TOOL_METADATA = REPOSITORY_ROOT / "tools" / "thumbnail_creator"


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
        self.assertFalse(any(TOOL_METADATA.glob("*.py")))
        self.assertNotIn("from .", source)
        self.assertNotIn("thumbnail_creator.", source)


class ThumbnailCreatorReleaseTests(unittest.TestCase):
    def test_version_metadata_matches_tool(self):
        source = TOOL_FILE.read_bytes()
        version = (TOOL_METADATA / "VERSION.txt").read_text(encoding="utf-8").strip()

        self.assertEqual(tool.__version__, version)
        self.assertEqual(tool._embedded_version(source.decode("utf-8")), version)

    def test_version_parser_is_strict_and_numeric(self):
        self.assertGreater(tool._version_tuple("1.10.0"), tool._version_tuple("1.9.9"))
        self.assertEqual(tool._version_tuple("v2.3.4"), (2, 3, 4))
        with self.assertRaises(ValueError):
            tool._version_tuple("1.2")

    def test_release_notes_include_only_newer_versions(self):
        changelog = """# Changelog

## v1.2.0
- Newest

## v1.1.0
- Newer

## v1.0.0
- Current
"""
        notes = tool.incoming_release_notes(changelog, current="1.0.0")

        self.assertIn("v1.2.0", notes)
        self.assertIn("v1.1.0", notes)
        self.assertNotIn("v1.0.0", notes)

    def test_update_settings_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(tool, "default_saved_directory", return_value=directory):
                self.assertEqual(tool.load_update_settings(), tool._DEFAULT_UPDATE_SETTINGS)
                saved = {
                    "check_updates_on_launch": False,
                    "auto_install_updates": True,
                }
                self.assertTrue(tool.save_update_settings(saved))
                self.assertEqual(tool.load_update_settings(), saved)

    def test_release_download_urls_are_tag_scoped(self):
        url = tool._tool_url_for_version("1.2.3")

        self.assertIn("/thumbnail-creator/v1.2.3/", url)
        self.assertNotIn("/main/", url)

    def test_validated_update_is_atomically_applied(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "thumbnail_creator_tool.py"
            target.write_text("VALUE = 1\n", encoding="utf-8")
            payload = b"VALUE = 2\n"
            update = {
                "version": "1.0.1",
                "payload": payload,
                "code": compile(payload.decode("utf-8"), str(target), "exec"),
            }
            with mock.patch.object(tool, "_self_path", return_value=str(target)):
                with mock.patch.object(tool, "close_window"):
                    self.assertTrue(tool.apply_validated_update(update))

            self.assertEqual(target.read_bytes(), payload)
            self.assertFalse(Path(str(target) + ".bak").exists())

    def test_failed_update_restores_previous_script(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "thumbnail_creator_tool.py"
            previous = b"VALUE = 1\n"
            target.write_bytes(previous)
            payload = b"raise RuntimeError('broken release')\n"
            update = {
                "version": "1.0.1",
                "payload": payload,
                "code": compile(payload.decode("utf-8"), str(target), "exec"),
            }
            with mock.patch.object(tool, "_self_path", return_value=str(target)):
                with mock.patch.object(tool, "close_window"):
                    with self.assertRaisesRegex(RuntimeError, "previous version was restored"):
                        tool.apply_validated_update(update)

            self.assertEqual(target.read_bytes(), previous)


if __name__ == "__main__":
    unittest.main()
