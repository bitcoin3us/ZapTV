"""Full-screen lightning-strike effect — a rapid white-flash flicker over
the whole display with a jagged yellow bolt drawn across it. Triggered
when a zap arrives. Lightweight: two LVGL objects on lv.layer_top() and
a sequence of one-shot timers, no PNG assets."""

import random
import lvgl as lv

from mpos import DisplayMetrics

# Bright yellow — high contrast against the white flash AND the dark/light
# theme screen so the bolt reads in every phase of the sequence.
_BOLT_COLOR = lv.color_hex(0xFFE600)


class Lightning:
    """Manages the lightning-strike flash + bolt animation."""

    def __init__(self, screen):
        self.screen = screen
        self.width = DisplayMetrics.width()
        self.height = DisplayMetrics.height()
        self.is_running = False
        self._step = 0

        # Full-screen white overlay for the flash. On lv.layer_top() so it
        # paints over every screen (settings sub-screens too). CLICKABLE is
        # removed so touches pass through to the screen underneath during
        # the brief animation.
        self.flash = lv.obj(lv.layer_top())
        self.flash.set_size(self.width, self.height)
        self.flash.set_pos(0, 0)
        self.flash.set_style_bg_color(lv.color_white(), lv.PART.MAIN)
        self.flash.set_style_bg_opa(lv.OPA.TRANSP, lv.PART.MAIN)
        self.flash.set_style_border_width(0, lv.PART.MAIN)
        self.flash.set_style_pad_all(0, lv.PART.MAIN)
        self.flash.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        self.flash.remove_flag(lv.obj.FLAG.CLICKABLE)

        # Jagged bolt drawn with lv.line. Created AFTER the flash so it
        # sits on top in z-order (children of a parent draw in creation
        # order). Points are refreshed every .start() so each strike has
        # a slightly different shape.
        self.bolt = lv.line(lv.layer_top())
        self.bolt.set_style_line_color(_BOLT_COLOR, lv.PART.MAIN)
        self.bolt.set_style_line_width(4, lv.PART.MAIN)
        self.bolt.set_style_line_rounded(True, lv.PART.MAIN)
        self.bolt.add_flag(lv.obj.FLAG.HIDDEN)
        self.bolt.remove_flag(lv.obj.FLAG.CLICKABLE)

        # Pre-baked phase sequence — (flash_opa, bolt_visible, duration_ms).
        # Total ~6 s, modeled on a thunderstorm lightning strike: rapid
        # triple-flicker initial burst (kept short — real lightning is
        # instantaneous), long stepped afterglow with the bolt still
        # ghostly visible early on, an aftershock flicker, then a final
        # slow fade to nothing.
        self._sequence = [
            # Initial triple-flicker burst — rapid stutter (~340 ms).
            (lv.OPA.COVER,  True,  100),  # first strike, full bright
            (lv.OPA.TRANSP, False,  40),
            (lv.OPA.COVER,  True,   70),  # second flicker
            (lv.OPA.TRANSP, False,  40),
            (lv.OPA.COVER,  True,   90),  # peak strike
            # Long afterglow — slow stepped fade with bolt ghosting (~2700 ms).
            (lv.OPA._80,    True,  250),
            (lv.OPA._70,    True,  280),
            (lv.OPA._60,    True,  300),
            (lv.OPA._50,    False, 320),
            (lv.OPA._40,    False, 340),
            (lv.OPA._30,    False, 360),
            (lv.OPA._20,    False, 380),
            (lv.OPA._10,    False, 470),
            # Aftershock flicker — second strike feel (~400 ms).
            (lv.OPA._60,    True,  100),
            (lv.OPA._20,    False, 120),
            (lv.OPA._40,    True,   80),
            (lv.OPA._10,    False, 100),
            # Final slow fade to nothing with a tiny final flicker (~2600 ms).
            (lv.OPA._20,    False, 300),
            (lv.OPA._10,    False, 400),
            (lv.OPA._10,    False, 500),
            (lv.OPA.TRANSP, False, 400),
            (lv.OPA._10,    False, 200),  # tiny final flicker
            (lv.OPA.TRANSP, False, 800),
        ]

    def _make_bolt_points(self):
        """Top-down zigzag from a random column near the centre, jittering
        left/right at each step, clamped inside the screen margins."""
        pts = []
        x = random.randint(int(self.width * 0.3), int(self.width * 0.7))
        y = 0
        pts.append({'x': x, 'y': y})
        steps = 8
        step_h = max(1, self.height // steps)
        for _ in range(steps):
            y += step_h
            x += random.randint(-35, 35)
            x = max(20, min(self.width - 20, x))
            pts.append({'x': x, 'y': y})
        return pts

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        self._step = 0
        # The bolt's points get regenerated at every visible phase in
        # _enter_phase, so no initial set_points is needed here.
        self._enter_phase(self._step)

    def _enter_phase(self, step):
        # Past the end → reset overlay state and finish.
        if step >= len(self._sequence):
            self.flash.set_style_bg_opa(lv.OPA.TRANSP, lv.PART.MAIN)
            self.bolt.add_flag(lv.obj.FLAG.HIDDEN)
            self.is_running = False
            return
        opa, bolt_visible, dur = self._sequence[step]
        self.flash.set_style_bg_opa(opa, lv.PART.MAIN)
        if bolt_visible:
            # Regenerate so consecutive flicker frames show visually
            # varying lightning instead of a static line.
            pts = self._make_bolt_points()
            self.bolt.set_points(pts, len(pts))
            self.bolt.remove_flag(lv.obj.FLAG.HIDDEN)
        else:
            self.bolt.add_flag(lv.obj.FLAG.HIDDEN)
        self._schedule(dur)

    def _schedule(self, ms):
        t = lv.timer_create(self._tick, ms, None)
        t.set_repeat_count(1)  # one-shot; auto-deletes after firing

    def _tick(self, timer):
        self._step += 1
        self._enter_phase(self._step)
