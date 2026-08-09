"""Fullscreen receive-code QR — same UX as Lightning Piggy.

The launching activity passes `receive_qr_data` (the Lightning address /
LNURL / BIP-21 URI) plus a `dark_mode` boolean via Intent extras.

Dismiss by tapping anywhere, or — on devices with no touchscreen — by
selecting the focusable close button with the D-pad and pressing ENTER.
MPOS does not map lv.KEY.ESC to back_screen(), and back_screen() is only
reached via the touch swipe gesture, so without that button a keypad-only
device would have no way out of this screen."""

import lvgl as lv

from mpos import Activity, DisplayMetrics

try:
    from mpos import add_focus_highlight      # MPOS 0.13+
except ImportError:
    add_focus_highlight = None


def _register_focusable(widget):
    """Add to the default focus group (add_focus_highlight already does that,
    so don't also call group.add_obj or the widget lands in the tab order
    twice)."""
    if add_focus_highlight:
        add_focus_highlight(widget)
        return
    focusgroup = lv.group_get_default()
    if focusgroup:
        focusgroup.add_obj(widget)


class FullscreenQR(Activity):

    def onCreate(self):
        extras = self.getIntent().extras
        data = extras.get("receive_qr_data") if extras else None
        dark = bool(extras.get("dark_mode")) if extras else False

        screen = lv.obj()
        screen.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        screen.set_scroll_dir(lv.DIR.NONE)
        # Explicit bg + flipped QR colors per theme — otherwise LVGL's
        # default dark-theme charcoal shows around the QR instead of
        # matching ZapTV's main display.
        if dark:
            bg = lv.color_black()
            qr_dark, qr_light = lv.color_white(), lv.color_black()
            border = lv.color_black()
            fg = lv.color_white()
        else:
            bg = lv.color_white()
            qr_dark, qr_light = lv.color_black(), lv.color_white()
            border = lv.color_white()
            fg = lv.color_black()
        screen.set_style_bg_color(bg, lv.PART.MAIN)
        # Tap anywhere on the screen dismisses; finish() pops the activity
        # off the navigator stack, returning the user to ZapTV.
        screen.add_event_cb(lambda e: self.finish(), lv.EVENT.CLICKED, None)

        qr = lv.qrcode(screen)
        qr.set_size(round(DisplayMetrics.min_dimension() * 0.9))
        qr.set_dark_color(qr_dark)
        qr.set_light_color(qr_light)
        qr.center()
        # Quiet-zone border so the QR remains scannable against any bg.
        qr.set_style_border_color(border, lv.PART.MAIN)
        qr.set_style_border_width(round(DisplayMetrics.min_dimension() * 0.1),
                                  lv.PART.MAIN)
        if data:
            qr.update(data, len(data))

        self._add_close_button(screen, fg)
        self.setContentView(screen)

    def _add_close_button(self, screen, fg):
        """Focusable close affordance for keypad-only devices. Transparent so
        it stays visually out of the way on touch devices, where tapping
        anywhere already works."""
        btn = lv.obj(screen)
        btn.set_size(50, 50)
        btn.align(lv.ALIGN.BOTTOM_RIGHT, 0, 0)
        btn.add_flag(lv.obj.FLAG.CLICKABLE)
        btn.add_flag(lv.obj.FLAG.FLOATING)
        btn.set_style_bg_opa(lv.OPA.TRANSP, lv.PART.MAIN)
        btn.set_style_border_width(0, lv.PART.MAIN)
        btn.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        btn.add_event_cb(lambda e: self.finish(), lv.EVENT.CLICKED, None)
        icon = lv.label(btn)
        icon.set_text(lv.SYMBOL.LEFT)
        icon.set_style_text_font(lv.font_montserrat_24, lv.PART.MAIN)
        icon.set_style_text_color(fg, lv.PART.MAIN)
        icon.center()
        _register_focusable(btn)
