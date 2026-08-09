"""ZapTV emoji-font rendering test.

Verifies that ZapTV's _content_font() helper produces a font that actually
renders emoji glyphs (via FontManager's image-font fallback), including in
combination with LVGL recolor tags (used by ZapTV's new-transaction blink).

Detection approach: emojis are the only *colorful* content this test puts on
screen — plain text and missing-glyph tofu render grayscale (white on black).
So "any strongly saturated pixel present" == "an emoji image was drawn".
"""
import sys
import unittest

import lvgl as lv

from mpos import capture_screenshot, wait_for_render

sys.path.insert(0, "apps/org.zaptv.app")
from zaptv import _content_font

EMOJI_TEXT = "zap! \U0001F525\U000026A1"  # "zap! 🔥⚡"


def _saturated_pixel_count(buf):
    """Count pixels where max(R,G,B) - min(R,G,B) is large (colorful)."""
    count = 0
    for i in range(0, len(buf), 3):
        b, g, r = buf[i], buf[i + 1], buf[i + 2]
        if max(r, g, b) - min(r, g, b) > 80:
            count += 1
    return count


class TestZapTVEmojiFont(unittest.TestCase):

    def setUp(self):
        self.orig_screen = lv.screen_active()

    def tearDown(self):
        lv.screen_load(self.orig_screen)
        wait_for_render(5)

    def _render_and_count(self, font, text, recolor=False):
        screen = lv.obj()
        screen.set_size(320, 240)
        screen.set_style_bg_color(lv.color_black(), 0)
        label = lv.label(screen)
        label.set_width(300)
        label.set_style_text_color(lv.color_white(), 0)
        label.set_style_text_font(font, lv.PART.MAIN)
        if recolor:
            label.set_recolor(True)
        label.set_text(text)
        label.center()
        lv.screen_load(screen)
        wait_for_render(20)
        buf = capture_screenshot(width=320, height=240,
                                 color_format=lv.COLOR_FORMAT.RGB888)
        return _saturated_pixel_count(buf)

    def test_montserrat_renders_no_color(self):
        # Baseline: plain Montserrat has no emoji glyphs — nothing colorful.
        count = self._render_and_count(lv.font_montserrat_16, EMOJI_TEXT)
        self.assertTrue(count < 20,
                        "expected no colorful pixels with Montserrat, got %d" % count)

    def test_content_font_renders_emoji(self):
        font = _content_font(16, lv.font_montserrat_16)
        count = self._render_and_count(font, EMOJI_TEXT)
        self.assertTrue(count > 50,
                        "expected colorful emoji pixels, got %d" % count)

    def test_content_font_renders_emoji_with_recolor(self):
        # ZapTV's blink wraps lines in "#FFE600 ...#" recolor tags — emoji
        # images must still draw (recolor applies to glyph text, not the
        # imgfont bitmaps).
        font = _content_font(16, lv.font_montserrat_16)
        count = self._render_and_count(
            font, "#FFE600 " + EMOJI_TEXT + "#", recolor=True)
        self.assertTrue(count > 50,
                        "expected emoji pixels under recolor, got %d" % count)


if __name__ == "__main__":
    unittest.main()
