"""The ported skin's tokens, stylesheet, and icon set stay complete.

Runs off-screen and constructs no window.
"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from philon_desktop.gui.qt import QApplication  # noqa: E402
from philon_desktop.gui import theme  # noqa: E402
from philon_desktop.gui.phosphor import pixmap  # noqa: E402
from philon_desktop.gui.phosphor_data import ICON_PATHS  # noqa: E402


class ThemeTokensTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        theme.apply_application_font(self.app)

    def test_light_and_dark_share_the_same_token_keys(self):
        self.assertEqual(set(theme.LIGHT), set(theme.DARK))

    def test_accent_values_match_the_source_stylesheet(self):
        self.assertEqual(theme.LIGHT["accent"], "#74d2a2")
        self.assertEqual(theme.LIGHT["accent_text"], "#0f7a55")
        self.assertEqual(theme.LIGHT["window_bg"], "#f5f5f7")
        self.assertEqual(theme.DARK["window_bg"], "#1c1c1e")

    def test_unskinned_values_are_present_in_both_appearances(self):
        # These are the base-layer colors the light skin never overrode; the
        # port carries them verbatim instead of "fixing" them.
        for key in ("error_banner_bg", "notice_banner_bg", "history_good", "history_warning", "export_link_text", "review_queue_bg", "history_hover_bg"):
            self.assertIn(key, theme.UNSKINNED)

    def test_qcolor_parses_hex_and_rgba(self):
        color = theme.qcolor("rgba(15,122,85,.5)")
        self.assertEqual((color.red(), color.green(), color.blue()), (15, 122, 85))
        self.assertEqual(color.alpha(), 128)
        self.assertEqual(theme.qcolor("#74d2a2").name(), "#74d2a2")

    def test_stylesheet_builds_and_names_the_major_surfaces(self):
        theme.reset_tokens()
        sheet = theme.build_qss()
        for selector in ("#AppHeader", "#ConversionGrid", "#TaskProgress", "#QueuePanel", "#SecondaryWorkspace", "#SettingsSection", "QPushButton#PrimaryButton"):
            self.assertIn(selector, sheet)


    def test_the_interface_face_exists_on_this_machine(self):
        """Qt's platform default is a family name, not a promise it is installed.

        The offscreen plugin reports "Sans Serif", which no Mac has, and
        resolving a missing family walks the whole font database once.
        """
        from philon_desktop.gui.qt import QFontDatabase

        theme.apply_application_font(self.app)
        resolved = self.app.font()
        self.assertIn(resolved.family(), QFontDatabase.families())
        self.assertTrue(set(resolved.families()) & set(QFontDatabase.families()))

class IconSetTest(unittest.TestCase):
    def setUp(self) -> None:
        self.app = QApplication.instance() or QApplication([])
        theme.apply_application_font(self.app)

    def test_every_icon_used_by_the_interface_is_present(self):
        needed = {
            "Archive", "ArrowClockwise", "BookOpenText", "CaretLeft", "CaretRight",
            "CheckCircle", "CircleNotch", "ClipboardText", "CloudSlash", "DownloadSimple",
            "FileArrowUp", "FilePdf", "FolderOpen", "Gauge", "GearSix", "ImageSquare",
            "Info", "ListChecks", "MagicWand", "Play", "ShieldCheck", "WarningCircle", "X",
        }
        self.assertTrue(needed.issubset(set(ICON_PATHS)))
        for name in needed:
            self.assertEqual(set(ICON_PATHS[name]), {"thin", "light", "regular", "bold", "fill", "duotone"})

    def test_icons_render_to_non_empty_pixmaps(self):
        for name in ("ShieldCheck", "FilePdf", "MagicWand"):
            for weight in ("regular", "fill", "bold", "duotone", "thin"):
                rendered = pixmap(name, 16, "#0f7a55", weight)
                self.assertFalse(rendered.isNull())
                self.assertGreater(rendered.width(), 0)


if __name__ == "__main__":
    unittest.main()
