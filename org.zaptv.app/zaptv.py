"""ZapTV — a Nostr-native zap display.

The user connects a wallet (LNbits, Nostr Wallet Connect, or on-chain xpub);
ZapTV shows its most recent payments on the left, with the Nostr profile
picture, display name, and a receive QR on the right.

Transaction monitoring uses the same mechanism as Lightning Piggy — the ported
wallet.py / lnbits_wallet.py / nwc_wallet.py / onchain_wallet.py modules.
npub.cash itself can't list recent zaps without authentication, hence the
wallet-backend approach.

Implemented: layout, welcome flow, three-level settings menu, prefs + slot
caching, live payments feed, lightning-strike effect and 30 s blink on new
transactions, Nostr kind-0 profile picture + auto-populated name, emoji-capable
fonts for user content, and auto-scroll (on arrival, and after 2 min idle).

Still stubbed: the camera-selfie profile source (see `_refresh_profile`) — the
option is selectable but nothing captures the image yet.
"""

import json
import time

import lvgl as lv

from mpos import (Activity, Intent, DisplayMetrics, SharedPreferences,
                  SettingsActivity, TaskManager, DownloadManager)

# Focus-highlight helper for custom buttons, added in MPOS 0.13+. Guarded so
# the app still runs on 0.10.x firmware (buttons stay focusable via the
# focusgroup either way; only the visual highlight is version-dependent).
try:
    from mpos import add_focus_highlight
except ImportError:
    add_focus_highlight = None

# Emoji-capable fonts for labels showing user content (zap comments, the
# profile name). FontManager.getFont(emoji=True) chains an image-font
# fallback that draws the OS's bundled emoji PNGs for codepoints Montserrat
# lacks. Guarded: on firmware without the emoji machinery the labels fall
# back to the plain builtin font (emojis show as boxes, as before).
try:
    from mpos import FontManager
except ImportError:
    FontManager = None


def _sanitize_display_text(s):
    """Replace typographic punctuation with ASCII equivalents.

    The firmware's Montserrat fonts are compiled with the range
    0x20-0xFF (+ bullet, +Bitcoin sign), so the general-punctuation block
    renders as tofu boxes — and zap comments are full of it, because phone
    keyboards autocorrect '--' to an em-dash and straight quotes to curly
    ones. Emojis are untouched: they're drawn by FontManager's image-font
    fallback, which handles them fine."""
    if not s:
        return s
    for frm, to in (("—", "-"), ("–", "-"),     # em/en dash
                    ("‘", "'"), ("’", "'"),     # curly single
                    ("“", '"'), ("”", '"'),     # curly double
                    ("…", "...")):                   # ellipsis
        if frm in s:
            s = s.replace(frm, to)
    return s


def _content_font(size, fallback):
    if FontManager:
        try:
            return FontManager.getFont(size=size, emoji=True)
        except Exception as e:
            print("zaptv: emoji font unavailable, using fallback:", e)
    return fallback


def _register_focusable(widget):
    """Make a widget reachable by keypad / D-pad navigation.

    add_focus_highlight() (MPOS 0.13+) both draws the focus ring AND adds the
    widget to the default focus group, so calling group.add_obj() as well
    entered it twice and put it in the tab order twice. Only fall back to the
    manual add when the helper isn't available (0.10.x firmware)."""
    if add_focus_highlight:
        add_focus_highlight(widget)
        return
    focusgroup = lv.group_get_default()
    if focusgroup:
        focusgroup.add_obj(widget)

# Import the wallet modules at the top so they resolve before MPOS restores
# sys.path on a wallet-type switch (see Lightning Piggy's displaywallet.py).
from lnbits_wallet import LNBitsWallet
from nwc_wallet import NWCWallet
from onchain_wallet import OnchainWallet
from payment import Payment
import wallet_cache

# Nostr profile-picture fetch deps.
from nostr.key import PublicKey
from nostr.relay_manager import RelayManager
from nostr.filter import Filter, Filters
from nostr.message_type import ClientMessageType

from lightning import Lightning
from fullscreen_qr import FullscreenQR

# Left transaction-area width — matches Lightning Piggy so the two apps feel
# consistent. The remaining width holds the profile picture and receive QR.
ZAP_LIST_PCT = 67
RIGHT_COL_PCT = 28

# Default + upper bound for the "Number of Transactions" Customise setting.
# The user picks any value in [1, MAX_ZAPS_LIMIT] via a slider; the wallet
# classes' PAYMENTS_TO_SHOW is bumped to match in _build_wallet so the
# per-fetch cap moves in lockstep with what the UI wants to show.
DEFAULT_MAX_ZAPS = 8
MAX_ZAPS_LIMIT = 21

# Blink a newly-arrived transaction for this long, toggling its color
# every BLINK_TICK_MS so it visibly stands out from older entries.
BLINK_DURATION_MS = 30000
BLINK_TICK_MS = 500

# Branded splash shown once per app launch. Two pre-rendered variants live in
# res/ (light = black wordmark, dark = white wordmark plus a white border round
# the TV mark so its black edge doesn't vanish into the background). They ship
# pre-scaled to 256px wide — scaling the 1661px source on-device would waste
# several MB of RAM decoding it.
SPLASH_DURATION_MS = 2000

# Snap the zap list back to the top after this long without any touch
# contact, so the device naturally re-presents the most recent zaps to
# anyone walking up after someone scrolled down to inspect older entries.
AUTO_SCROLL_IDLE_MS = 120000  # 2 min, matches Lightning Piggy

# npub.cash issues a Lightning address of the form <npub>@npub.cash, so the
# npub doubles as the default receive address shown in the QR.
NPUB_CASH_DOMAIN = "npub.cash"

# Relay used for the one-shot kind-0 metadata fetch that powers the profile
# picture. Hardcoded for now — could become a Customise setting later.
NOSTR_PROFILE_RELAY = "wss://relay.damus.io"

# wsrv.nl is a widely-used free image proxy. We route the picture URL
# through it so anything (JPEG / PNG truecolor / WebP) comes back as a
# small JPEG that tjpgd can render — lodepng can't render truecolor PNGs
# on MPOS 0.10.0 (MPOS_APP_DEV.md §6), so JPEG via the proxy is the most
# reliable on-device format. 128 gives headroom over the ~89-px profile box.
IMG_PROXY_URL = "https://wsrv.nl/?url={url}&w=128&h=128&output=jpg"


def _add_back_button(screen, on_back):
    """Floating back button at the bottom-right of a settings screen — the
    same pattern Lightning Piggy uses. Tapping it finishes the activity,
    returning to the previous screen. FLOATING keeps it out of the screen's
    flex-column flow so the align() position holds."""
    btn = lv.obj(screen)
    btn.set_size(50, 50)
    btn.align(lv.ALIGN.BOTTOM_RIGHT, 0, 0)
    btn.add_flag(lv.obj.FLAG.CLICKABLE)
    btn.add_flag(lv.obj.FLAG.FLOATING)
    btn.set_style_bg_opa(lv.OPA.TRANSP, lv.PART.MAIN)
    btn.set_style_border_width(0, lv.PART.MAIN)
    btn.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
    btn.add_event_cb(lambda e: on_back(), lv.EVENT.CLICKED, None)
    icon = lv.label(btn)
    icon.set_text(lv.SYMBOL.LEFT)
    icon.set_style_text_font(lv.font_montserrat_24, lv.PART.MAIN)
    icon.center()
    _register_focusable(btn)


class _SettingsScreen(SettingsActivity):
    """A SettingsActivity with a floating back button — base class for every
    ZapTV settings screen. onResume runs again on each return to the screen,
    and the base class's screen.clean() drops the old back button first, so
    re-adding it here never stacks duplicates."""

    def onResume(self, screen):
        super().onResume(screen)
        _add_back_button(screen, self.finish)


class AboutActivity(Activity):
    """About screen: full logo, version info, and the project web address.

    A plain Activity rather than a SettingsActivity — nothing here is
    editable, so the declarative settings-row UI would be the wrong shape.
    """

    WEB_ADDRESS = "www.ZapTV.org"
    CREDIT = "A fully open-source app by Richard Nakamoto"

    def onCreate(self):
        extras = self.getIntent().extras or {}
        self.prefs = extras.get("prefs")
        dark = True
        if self.prefs:
            dark = (self.prefs.get_string("theme") or "dark") != "light"
        bg = lv.color_black() if dark else lv.color_white()
        fg = lv.color_white() if dark else lv.color_black()

        screen = lv.obj()
        screen.set_style_bg_color(bg, lv.PART.MAIN)
        screen.set_style_bg_opa(lv.OPA.COVER, lv.PART.MAIN)
        screen.set_style_border_width(0, lv.PART.MAIN)
        screen.set_style_pad_all(DisplayMetrics.pct_of_width(3), lv.PART.MAIN)
        screen.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        screen.set_flex_align(lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.CENTER,
                              lv.FLEX_ALIGN.CENTER)
        screen.set_scroll_dir(lv.DIR.VER)
        screen.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)

        # Full ZapTV logo (wordmark + TV mark) — reuse the splash artwork,
        # picking the variant that suits the current theme.
        logo = lv.image(screen)
        logo.set_src("M:apps/" + self._fullname() + "/res/splash_"
                     + ("dark" if dark else "light") + ".png")

        for text in ("ZapTV " + self._app_version(),
                     "MicroPythonOS " + self._os_version(),
                     "Hardware: " + self._hardware(),
                     self.WEB_ADDRESS):
            lbl = lv.label(screen)
            lbl.set_text(text)
            lbl.set_style_text_font(lv.font_montserrat_14, lv.PART.MAIN)
            lbl.set_style_text_color(fg, lv.PART.MAIN)
            lbl.set_style_margin_top(4, lv.PART.MAIN)

        # Credit line. Too long for one 320 px row, so unlike the lines above
        # it wraps and is set a size smaller to keep the version block the
        # focus of the screen.
        credit = lv.label(screen)
        credit.set_text(self.CREDIT)
        credit.set_width(lv.pct(100))
        credit.set_long_mode(lv.label.LONG_MODE.WRAP)
        credit.set_style_text_align(lv.TEXT_ALIGN.CENTER, lv.PART.MAIN)
        credit.set_style_text_font(lv.font_montserrat_12, lv.PART.MAIN)
        credit.set_style_text_color(fg, lv.PART.MAIN)
        credit.set_style_text_opa(lv.OPA._70, lv.PART.MAIN)
        credit.set_style_margin_top(12, lv.PART.MAIN)

        self.setContentView(screen)

    def onResume(self, screen):
        super().onResume(screen)
        _add_back_button(screen, self.finish)

    def _fullname(self):
        # appFullName is set by the navigator; fall back for safety since a
        # wrong path here would only cost us the logo, not the screen.
        return getattr(self, "appFullName", None) or "org.zaptv.app"

    def _app_version(self):
        # Read from our own MANIFEST so the displayed version can never drift
        # from the packaged one.
        for path in ("apps/%s/MANIFEST.JSON" % self._fullname(),
                     "apps/%s/META-INF/MANIFEST.JSON" % self._fullname()):
            try:
                with open(path) as f:
                    return json.load(f).get("version", "?")
            except Exception:
                continue
        return "?"

    def _os_version(self):
        try:
            from mpos import BuildInfo
            return BuildInfo.version.release
        except Exception:
            return "?"

    def _hardware(self):
        try:
            from mpos import DeviceInfo      # MPOS 0.13+
            hw = DeviceInfo.hardware_id
            # Desktop builds (and any board that doesn't register itself)
            # leave the sentinel in place — don't show it to the user.
            return "unknown" if not hw or hw == "missing-hardware-info" else hw
        except Exception:
            return "unknown"


class MainSettingsActivity(_SettingsScreen):
    """Top-level settings menu (Zaps! / Profile / Customise). The settings
    list comes from the launching Intent's extras, via the inherited
    SettingsActivity.onCreate."""


class ZapsSettingsActivity(_SettingsScreen):
    """Zaps! sub-menu — all wallet (transaction-monitoring) settings.

    A SettingsActivity subclass: it builds its own `self.settings` in
    onCreate; the base class renders the rows in onResume.
    """

    def onCreate(self):
        extras = self.getIntent().extras or {}
        self.prefs = extras.get("prefs")
        # Lightning Address placeholder: most informative value first —
        # the connected wallet's static receive code if known, then the
        # <npub>@npub.cash derived default, then a placeholder hint.
        wallet_addr = self.prefs.get_string("wallet_static_receive_code")
        npub = self.prefs.get_string("npub")
        if wallet_addr:
            derived = wallet_addr
        elif npub:
            derived = npub + "@" + NPUB_CASH_DOMAIN
        else:
            derived = "<npub>@npub.cash"
        self.settings = [
            {"title": "Wallet Type", "key": "wallet_type", "ui": "radiobuttons",
             "ui_options": [("LNbits", "lnbits"),
                            ("Nostr Wallet Connect", "nwc"),
                            ("On-chain (xpub)", "onchain")],
             "default_value": "lnbits"},
            {"title": "LNbits URL", "key": "lnbits_url",
             "placeholder": "https://demo.lnpiggy.com", "should_show": self._show_lnbits},
            {"title": "LNbits Read Key", "key": "lnbits_readkey",
             "placeholder": "fd92e3f8168ba314dc22e54182784045",
             "should_show": self._show_lnbits},
            {"title": "Nostr Wallet Connect", "key": "nwc_url",
             "placeholder": "nostr+walletconnect://...", "should_show": self._show_nwc},
            {"title": "xpub / ypub / zpub", "key": "onchain_xpub",
             "placeholder": "zpub6rF...", "should_show": self._show_onchain},
            {"title": "Blockbook URL", "key": "onchain_blockbook_url",
             "placeholder": OnchainWallet.DEFAULT_BLOCKBOOK_URL,
             "default_value": OnchainWallet.DEFAULT_BLOCKBOOK_URL,
             "should_show": self._show_onchain},
            {"title": "Lightning Address", "key": "lightning_address",
             "placeholder": derived,
             "should_show": self._show_lightning_address},
        ]
        screen = lv.obj()
        screen.set_style_pad_all(DisplayMetrics.pct_of_width(2), lv.PART.MAIN)
        screen.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        screen.set_style_border_width(0, lv.PART.MAIN)
        self.setContentView(screen)

    def _show_lnbits(self, setting):
        # Empty wallet_type (first run) is treated as the "lnbits" default so
        # the LNbits fields show before the user picks a type.
        return (self.prefs.get_string("wallet_type") or "lnbits") == "lnbits"

    def _show_nwc(self, setting):
        return self.prefs.get_string("wallet_type") == "nwc"

    def _show_onchain(self, setting):
        return self.prefs.get_string("wallet_type") == "onchain"

    def _show_lightning_address(self, setting):
        # Hide for on-chain wallets — the QR shows a rotating Bitcoin
        # address from the wallet itself, not a Lightning address.
        return self.prefs.get_string("wallet_type") != "onchain"


class ProfileSettingsActivity(_SettingsScreen):
    """Profile sub-menu — npub, display name, and profile-picture source."""

    def onCreate(self):
        extras = self.getIntent().extras or {}
        self.prefs = extras.get("prefs")
        self.settings = [
            {"title": "Npub", "key": "npub", "placeholder": "npub1..."},
            {"title": "Name", "key": "name", "placeholder": "Your display name"},
            {"title": "Profile picture", "key": "profile_source", "ui": "radiobuttons",
             "ui_options": [("Nostr npub", "nostr"), ("Camera selfie", "selfie")],
             "default_value": "nostr"},
        ]
        screen = lv.obj()
        screen.set_style_pad_all(DisplayMetrics.pct_of_width(2), lv.PART.MAIN)
        screen.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        screen.set_style_border_width(0, lv.PART.MAIN)
        self.setContentView(screen)


class CustomiseSettingsActivity(_SettingsScreen):
    """Customise sub-menu — display denomination and Light/Dark theme."""

    def onCreate(self):
        extras = self.getIntent().extras or {}
        self.prefs = extras.get("prefs")
        self.settings = [
            {"title": "Denomination", "key": "denomination", "ui": "radiobuttons",
             "ui_options": [("sats", "sats"), ("₿ symbol", "₿ symbol"),
                            ("bits", "bits"), ("micro-BTC", "ubtc"),
                            ("milli-BTC", "mbtc"), ("BTC", "btc")],
             "default_value": "sats"},
            {"title": "Number of Transactions", "key": "max_zaps",
             "ui": "slider", "min": 1, "max": MAX_ZAPS_LIMIT,
             "default_value": str(DEFAULT_MAX_ZAPS)},
            {"title": "Sort", "key": "sort_order", "ui": "radiobuttons",
             "ui_options": [("Most recent first", "recent"),
                            ("Largest first", "largest")],
             "default_value": "recent"},
            {"title": "Theme", "key": "theme", "ui": "radiobuttons",
             "ui_options": [("Dark", "dark"), ("Light", "light")],
             "default_value": "dark"},
        ]
        screen = lv.obj()
        screen.set_style_pad_all(DisplayMetrics.pct_of_width(2), lv.PART.MAIN)
        screen.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        screen.set_style_border_width(0, lv.PART.MAIN)
        self.setContentView(screen)


class ZapTV(Activity):

    wallet = None
    lightning = None
    _profile_fetch_in_flight = False
    # Blink state for newly-arrived transactions. _seen_payments and
    # _blink_until are reset in _build_wallet so the cached load doesn't
    # spuriously blink, and a wallet swap starts with a clean slate.
    _seen_payments = None
    _blink_until = None
    _blink_phase = 0
    _blink_timer = None
    # Touch-contact tracking for the LP-style "scroll-to-top-after-idle"
    # behaviour. None = no touch seen yet.
    _last_screen_contact_ms = None
    _idle_timer = None
    # Splash is shown once per launch, not on every return from settings.
    _splash_shown = False
    _splash_timer = None
    # Previous wallet_type seen by onResume — used to detect Wallet Type
    # switches so we can clear the stale wallet_static_receive_code pref
    # left over from the previous wallet. Initialised in onCreate.
    _last_wallet_type = None

    def onCreate(self):
        # MPOS 0.10.0 wires up image decoders but never activates them;
        # without these, indexed PNGs (the camera selfie) and JPEGs (the
        # proxied Nostr profile picture) fail to render.
        try:
            lv.lodepng_init()
        except Exception as e:
            print("zaptv: lv.lodepng_init() failed:", e)
        try:
            lv.tjpgd_init()
        except Exception as e:
            print("zaptv: lv.tjpgd_init() failed:", e)

        self.prefs = SharedPreferences(self.appFullName)
        self._blink_until = {}
        # Seed the wallet_type tracker so first onResume doesn't spuriously
        # clear a cached wallet_static_receive_code on app launch.
        self._last_wallet_type = self.prefs.get_string("wallet_type")

        self.main_screen = lv.obj()
        self.main_screen.set_style_pad_all(0, lv.PART.MAIN)
        self.main_screen.set_scroll_dir(lv.DIR.NONE)
        self.main_screen.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)

        self._build_zap_list()
        self._build_profile_box()
        self._build_receive_qr()
        self._build_settings_button()
        self._build_welcome()
        # Built last so it's the topmost child and covers the whole UI.
        self._build_splash()

        self.setContentView(self.main_screen)

        # Lightning effect renders on lv.layer_top() (global), so the
        # screen must already exist when it's constructed. No PNG assets
        # needed — the bolt is drawn with lv.line, the flash is an lv.obj.
        self.lightning = Lightning(self.main_screen)

        # Screen-contact tracking. LVGL 9 doesn't bubble events to
        # ancestors by default, so a single listener on main_screen would
        # miss touches on children — register PRESSED on each interactive
        # widget. The idle timer ticks every 5 s and snaps the zap list
        # back to the top once AUTO_SCROLL_IDLE_MS has passed.
        for w in (self.main_screen, self.zap_container, self.profile_box,
                  self.receive_qr, self.settings_button, self.welcome):
            try:
                w.add_event_cb(self._on_screen_contact, lv.EVENT.PRESSED, None)
            except Exception as e:
                print("zaptv: install contact tracker failed:", e)
        self._idle_timer = lv.timer_create(self._idle_check, 5000, None)

    # --- UI construction -------------------------------------------------

    def _build_zap_list(self):
        # Stored as self.* so _on_payments (scroll-to-top on new) and
        # _idle_check (scroll-to-top after idle) can call scroll_to_y on it.
        self.zap_container = lv.obj(self.main_screen)
        self.zap_container.set_size(DisplayMetrics.pct_of_width(ZAP_LIST_PCT),
                                    DisplayMetrics.height())
        self.zap_container.align(lv.ALIGN.TOP_LEFT, 0, 0)
        self.zap_container.set_style_border_width(0, lv.PART.MAIN)
        self.zap_container.set_style_bg_opa(lv.OPA.TRANSP, lv.PART.MAIN)
        self.zap_container.set_scroll_dir(lv.DIR.VER)
        self.zap_container.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)

        self.zap_label = lv.label(self.zap_container)
        self.zap_label.set_width(lv.pct(100))
        self.zap_label.set_long_mode(lv.label.LONG_MODE.WRAP)
        self.zap_label.set_style_text_font(
            _content_font(16, lv.font_montserrat_16), lv.PART.MAIN)
        # Enable inline "#RRGGBB text#" color tags so _render_zaps can
        # flash blinking-new transactions in a bright color without
        # rebuilding the widget tree on each tick. Guarded for LVGL
        # bindings that may not expose set_recolor — blink would degrade
        # silently to no visible effect, the rest of the list still works.
        try:
            self.zap_label.set_recolor(True)
        except AttributeError:
            pass
        self.zap_label.set_text("")

    def _build_profile_box(self):
        size = DisplayMetrics.pct_of_width(RIGHT_COL_PCT)
        self.profile_box = lv.obj(self.main_screen)
        self.profile_box.set_size(size, size)
        self.profile_box.align(lv.ALIGN.TOP_RIGHT, -6, 6)
        self.profile_box.set_style_radius(8, lv.PART.MAIN)
        self.profile_box.set_style_pad_all(0, lv.PART.MAIN)
        self.profile_box.set_style_bg_opa(lv.OPA.TRANSP, lv.PART.MAIN)
        self.profile_box.set_style_border_width(0, lv.PART.MAIN)
        self.profile_box.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        # Contents are filled in by _refresh_profile() — it depends on prefs
        # (profile source) which can change while the app is backgrounded.

        # Display-name label between the profile picture and the receive QR.
        # Auto-populated from the npub's kind-0 metadata (see
        # _fetch_nostr_profile_image) or set manually in Settings → Profile
        # → Name. DOTS truncation keeps it to one line; centered under the
        # profile box. Empty text collapses to ~0 height so the QR sits in
        # roughly the same place when no name is set.
        self.name_label = lv.label(self.main_screen)
        self.name_label.set_width(DisplayMetrics.pct_of_width(RIGHT_COL_PCT))
        self.name_label.align_to(self.profile_box, lv.ALIGN.OUT_BOTTOM_MID, 0, 2)
        self.name_label.set_style_text_font(
            _content_font(12, lv.font_montserrat_12), lv.PART.MAIN)
        self.name_label.set_style_text_align(lv.TEXT_ALIGN.CENTER, lv.PART.MAIN)
        self.name_label.set_long_mode(lv.label.LONG_MODE.DOTS)
        self.name_label.set_text("")

    def _build_receive_qr(self):
        size = DisplayMetrics.pct_of_width(RIGHT_COL_PCT)
        self.receive_qr = lv.qrcode(self.main_screen)
        self.receive_qr.set_size(size)
        # Dark/light colors are set per-theme in _apply_theme — see there
        # for the rationale (dark mode flips so the QR blends with the
        # screen instead of being a glaring white block).
        self.receive_qr.set_style_border_width(0, lv.PART.MAIN)
        self.receive_qr.align_to(self.name_label, lv.ALIGN.OUT_BOTTOM_MID, 0, 2)
        # Tap-to-enlarge — launches FullscreenQR with the current address,
        # same UX as Lightning Piggy.
        self.receive_qr.add_flag(lv.obj.FLAG.CLICKABLE)
        self.receive_qr.add_event_cb(self._qr_clicked, lv.EVENT.CLICKED, None)
        # Focusable too, so keypad-only devices can reach tap-to-enlarge:
        # LVGL sends CLICKED to the focused widget when ENTER is pressed.
        _register_focusable(self.receive_qr)

    def _build_settings_button(self):
        # Stored as self.* so onCreate's contact-tracker loop can also
        # register a PRESSED listener on it.
        self.settings_button = lv.obj(self.main_screen)
        self.settings_button.set_size(40, 40)
        self.settings_button.align(lv.ALIGN.BOTTOM_RIGHT, 0, 0)
        self.settings_button.add_flag(lv.obj.FLAG.CLICKABLE)
        self.settings_button.set_style_bg_opa(lv.OPA.TRANSP, lv.PART.MAIN)
        self.settings_button.set_style_border_width(0, lv.PART.MAIN)
        self.settings_button.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        self.settings_button.add_event_cb(self._open_settings, lv.EVENT.CLICKED, None)
        self.settings_icon = lv.label(self.settings_button)
        self.settings_icon.set_text(lv.SYMBOL.SETTINGS)
        self.settings_icon.set_style_text_font(lv.font_montserrat_18, lv.PART.MAIN)
        self.settings_icon.center()
        _register_focusable(self.settings_button)

    def _build_splash(self):
        """Full-screen branded overlay, shown once per launch. The logo image
        src is chosen at show time (not here) so it follows the Customise
        theme without decoding both variants."""
        self.splash = lv.obj(self.main_screen)
        self.splash.set_size(lv.pct(100), lv.pct(100))
        self.splash.set_style_border_width(0, lv.PART.MAIN)
        self.splash.set_style_pad_all(0, lv.PART.MAIN)
        self.splash.set_style_bg_opa(lv.OPA.COVER, lv.PART.MAIN)
        self.splash.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        self.splash.add_flag(lv.obj.FLAG.HIDDEN)
        self.splash_logo = lv.image(self.splash)
        self.splash_logo.center()

    def _show_splash(self):
        if self._splash_shown:
            return
        self._splash_shown = True
        bg, _ = self._theme_colors()
        dark = (self.prefs.get_string("theme") or "dark") != "light"
        self.splash.set_style_bg_color(bg, lv.PART.MAIN)
        name = "splash_dark.png" if dark else "splash_light.png"
        try:
            self.splash_logo.set_src(
                "M:apps/" + self.appFullName + "/res/" + name)
        except Exception as e:
            print("zaptv: splash logo failed:", e)
        self.splash.remove_flag(lv.obj.FLAG.HIDDEN)
        self.splash.move_foreground()
        # Repeating timer + explicit delete on first fire. NOT a one-shot
        # (.set_repeat_count(1)): one-shot timers were observed never firing
        # in the desktop test harness, leaving the splash up forever. The
        # repeating pattern is the same shape as the blink timer, which is
        # proven on both desktop and device.
        self._splash_timer = lv.timer_create(
            self._hide_splash, SPLASH_DURATION_MS, None)

    def _hide_splash(self, timer=None):
        self.splash.add_flag(lv.obj.FLAG.HIDDEN)
        if self._splash_timer is not None:
            self._splash_timer.delete()
            self._splash_timer = None

    def _build_welcome(self):
        self.welcome = lv.obj(self.main_screen)
        self.welcome.set_size(lv.pct(100), lv.pct(100))
        self.welcome.set_style_border_width(0, lv.PART.MAIN)
        self.welcome.set_style_pad_all(DisplayMetrics.pct_of_width(5), lv.PART.MAIN)
        self.welcome.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        self.welcome.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER,
                                    lv.FLEX_ALIGN.CENTER)
        self.welcome.set_scrollbar_mode(lv.SCROLLBAR_MODE.OFF)
        self.welcome.add_flag(lv.obj.FLAG.HIDDEN)

        # TV-mark logo above the title. The asset carries its own cream
        # sticker outline, so it reads on both light and dark themes.
        logo = lv.image(self.welcome)
        logo.set_src("M:apps/" + self.appFullName + "/res/logo_tv.png")

        self.welcome_title = lv.label(self.welcome)
        self.welcome_title.set_text("ZapTV")
        self.welcome_title.set_style_text_font(lv.font_montserrat_24, lv.PART.MAIN)
        self.welcome_title.set_style_margin_top(
            DisplayMetrics.pct_of_height(2), lv.PART.MAIN)

        self.welcome_subtitle = lv.label(self.welcome)
        self.welcome_subtitle.set_text("Connect an LNbits or NWC wallet\n"
                                       "to start showing your zaps.")
        self.welcome_subtitle.set_style_text_font(lv.font_montserrat_12, lv.PART.MAIN)
        self.welcome_subtitle.set_long_mode(lv.label.LONG_MODE.WRAP)
        self.welcome_subtitle.set_width(lv.pct(90))
        self.welcome_subtitle.set_style_text_align(lv.TEXT_ALIGN.CENTER, lv.PART.MAIN)
        self.welcome_subtitle.set_style_margin_top(DisplayMetrics.pct_of_height(3), lv.PART.MAIN)

        setup_btn = lv.button(self.welcome)
        setup_btn.set_style_margin_top(DisplayMetrics.pct_of_height(4), lv.PART.MAIN)
        setup_btn.add_event_cb(self._open_settings, lv.EVENT.CLICKED, None)
        setup_label = lv.label(setup_btn)
        setup_label.set_text(lv.SYMBOL.SETTINGS + " Setup")
        setup_label.center()
        _register_focusable(setup_btn)

    # --- lifecycle -------------------------------------------------------

    def onResume(self, screen):
        super().onResume(screen)
        self._apply_theme()
        # Guarded internally so it only runs on the first resume of a launch,
        # not when returning from a settings sub-screen.
        self._show_splash()
        # Detect Wallet Type changes since the last onResume and clear any
        # cached wallet_static_receive_code from the previous wallet — its
        # LNURL/lud16/bitcoin-address is meaningless for the new wallet,
        # and leaving it in the pref makes the QR show stale data until
        # the new wallet's first fetch fires _on_static_receive_code.
        current_type = self._wallet_type()
        if current_type != self._last_wallet_type:
            ed = self.prefs.edit()
            ed.put_string("wallet_static_receive_code", None)
            ed.commit()
            self._last_wallet_type = current_type
        configured = self._wallet_configured()
        if configured:
            self.welcome.add_flag(lv.obj.FLAG.HIDDEN)
        else:
            self.welcome.remove_flag(lv.obj.FLAG.HIDDEN)

        self._refresh_receive_qr()
        self._refresh_profile()

        if configured and not self.wallet:
            self.wallet = self._build_wallet()
            if self.wallet:
                self.wallet.start(
                    self._on_balance, self._on_payments,
                    static_receive_code_updated_cb=self._on_static_receive_code,
                    error_cb=self._on_error)
        self._render_zaps()

    def onPause(self, screen):
        super().onPause(screen)
        # Stop the feed and release sockets — leaking them across activity
        # switches can exhaust the ESP32 TCP pool (MPOS_APP_DEV.md §10). The
        # wallet is rebuilt fresh on the next onResume.
        if self.wallet:
            self.wallet.stop()
            self.wallet = None

    def _theme_colors(self):
        # (bg, fg) for the current Customise theme. Used by _apply_theme
        # and by code that creates a label after _apply_theme has run (the
        # profile placeholder in _refresh_profile).
        if (self.prefs.get_string("theme") or "dark") == "light":
            return lv.color_white(), lv.color_black()
        return lv.color_black(), lv.color_white()

    def _apply_theme(self):
        # ZapTV-only Light/Dark theme, chosen in Customise. Sub-settings
        # screens keep the MicroPythonOS framework theme. text_color is
        # *nominally* inheritable in LVGL, but the framework theme sets it
        # directly on every label, which beats inheritance — so we have to
        # explicitly stamp the fg color on each label we own.
        bg, fg = self._theme_colors()
        for obj in (self.main_screen, self.welcome):
            obj.set_style_bg_color(bg, lv.PART.MAIN)
            obj.set_style_bg_opa(lv.OPA.COVER, lv.PART.MAIN)
            obj.set_style_text_color(fg, lv.PART.MAIN)
        for label in (self.zap_label, self.welcome_title,
                      self.welcome_subtitle, self.settings_icon,
                      self.name_label):
            label.set_style_text_color(fg, lv.PART.MAIN)
        # QR colors flip with the theme so the receive QR blends with the
        # screen instead of being a glaring white block in dark mode. Most
        # scanners handle inverted polarity fine. _refresh_receive_qr runs
        # next in onResume and its update() call redraws the modules.
        self.receive_qr.set_dark_color(fg)
        self.receive_qr.set_light_color(bg)

    # --- wallet feed -----------------------------------------------------

    def _wallet_type(self):
        return self.prefs.get_string("wallet_type")

    def _max_zaps(self):
        # Customise → Number of Transactions, with safety clamp + default.
        try:
            n = int(self.prefs.get_string("max_zaps") or str(DEFAULT_MAX_ZAPS))
        except (TypeError, ValueError):
            n = DEFAULT_MAX_ZAPS
        return max(1, min(MAX_ZAPS_LIMIT, n))

    def _wallet_configured(self):
        wt = self._wallet_type()
        if wt == "lnbits":
            return bool(self.prefs.get_string("lnbits_url")
                        and self.prefs.get_string("lnbits_readkey"))
        if wt == "nwc":
            return bool(self.prefs.get_string("nwc_url"))
        if wt == "onchain":
            return bool(self.prefs.get_string("onchain_xpub"))
        return False

    def _build_wallet(self):
        wt = self._wallet_type()
        w = None
        try:
            if wt == "lnbits":
                w = LNBitsWallet(self.prefs.get_string("lnbits_url"),
                                 self.prefs.get_string("lnbits_readkey"))
            elif wt == "nwc":
                w = NWCWallet(self.prefs.get_string("nwc_url"))
            elif wt == "onchain":
                w = OnchainWallet(
                    self.prefs.get_string("onchain_xpub"),
                    self.prefs.get_string("onchain_blockbook_url"))
        except Exception as e:
            print("zaptv: could not build wallet:", e)
            return None
        if w is None:
            return None
        # Stamp cache slot identity. Without these, the wallet writes cache
        # rows whose fingerprints no later load_slot can ever match — so
        # caching has been effectively dead. compute_slot_key gives a
        # forward-compatible name ("lnbits_1", "nwc_1") that leaves room
        # for multi-slot later.
        w.slot_key = wallet_cache.compute_slot_key(wt, slot=1)
        w.creds_fingerprint, w.qr_fingerprint = (
            wallet_cache.compute_fingerprints(wt, self.prefs, slot=1))
        # Prime from disk so the previous session's zaps show instantly
        # while the live fetch is in flight. Each wallet type has its own
        # slot, so switching LNbits → NWC → LNbits returns to the LNbits
        # history immediately.
        cached = wallet_cache.load_slot(
            w.slot_key, w.creds_fingerprint, w.qr_fingerprint)
        if cached.get("payments"):
            w.payment_list = cached["payments"]
        if cached.get("balance") is not None:
            w.last_known_balance = cached["balance"]
        if cached.get("static_receive_code"):
            # Stamp on the wallet so handle_new_static_receive_code doesn't
            # re-notify the unchanged value, and mirror to the pref so
            # _effective_lightning_address (and the Zaps! settings screen)
            # can see it without reaching into self.wallet.
            w.static_receive_code = cached["static_receive_code"]
            ed = self.prefs.edit()
            ed.put_string("wallet_static_receive_code",
                          cached["static_receive_code"])
            ed.commit()
        # Push the per-fetch payments cap into the wallet classes so the
        # next LNbits/NWC fetch returns enough rows for the Customise
        # "Number of Transactions" setting. Class attr — affects future
        # fetches of any instance (we only have one wallet at a time).
        n = self._max_zaps()
        LNBitsWallet.PAYMENTS_TO_SHOW = n
        NWCWallet.PAYMENTS_TO_SHOW = n
        OnchainWallet.PAYMENTS_TO_SHOW = n
        # Reset blink-detection state — the next _on_payments call treats
        # whatever's in payment_list (likely just cached entries) as
        # already-seen, so opening the app doesn't spuriously blink old
        # zaps. New arrivals after that get the highlight.
        self._seen_payments = None
        self._blink_until = {}
        self._blink_phase = 0
        if self._blink_timer is not None:
            self._blink_timer.delete()
            self._blink_timer = None
        return w

    def _on_balance(self, sats_added):
        # ZapTV doesn't show a balance label, but a positive sats_added
        # means a zap just landed — fire the full-screen lightning effect.
        # The first balance after connect fires with sats_added=0 (see
        # Wallet.handle_new_balance), so initial connect is silent.
        if sats_added > 0 and self.lightning:
            self.lightning.start()

    def _on_payments(self):
        # Detect newly-arrived payments and start the blink on them.
        # Called by wallet.handle_new_payment (single push from websocket
        # / NWC notification) and by wallet.handle_new_payments (full list
        # refresh from polling) — both paths route through this callback.
        if not self.wallet:
            return
        current = set(self._payment_id(p) for p in self.wallet.payment_list)
        if self._seen_payments is None:
            # First callback after _build_wallet — adopt whatever's there
            # as baseline, don't blink the initial fill.
            self._seen_payments = current
        else:
            new_ids = current - self._seen_payments
            if new_ids:
                expiry = time.ticks_add(time.ticks_ms(), BLINK_DURATION_MS)
                for pid in new_ids:
                    self._blink_until[pid] = expiry
                self._ensure_blink_timer()
                # Snap to top so the incoming zap is visible regardless of
                # where the user had scrolled.
                try:
                    self.zap_container.scroll_to_y(0, True)
                except Exception as e:
                    print("zaptv: scroll-on-new failed:", e)
            self._seen_payments = current
        self._render_zaps()

    def _on_error(self, e):
        # Only surface the error when there's nothing better to show, so a
        # transient blip doesn't wipe a populated zap list.
        if not self.wallet or len(self.wallet.payment_list) == 0:
            self.zap_label.set_text(str(e))

    def _on_static_receive_code(self):
        # LNbits' fetch_static_receive_code or NWC's async_wallet_manager_task
        # (the lud16 from the NWC URL) just produced a new receive code.
        # Mirror it to the pref so _effective_lightning_address picks it
        # up without reaching into self.wallet, then refresh the QR.
        if not self.wallet:
            return
        code = self.wallet.static_receive_code
        if not code:
            return
        if self.prefs.get_string("wallet_static_receive_code") != code:
            ed = self.prefs.edit()
            ed.put_string("wallet_static_receive_code", code)
            ed.commit()
        self._refresh_receive_qr()

    def _render_zaps(self):
        # Denomination affects display: the bitcoin-symbol prefix when
        # "symbol" is picked, otherwise a "sats" suffix. Other units leave
        # zap amounts in sats, matching Lightning Piggy's transaction list.
        Payment.use_symbol = self.prefs.get_string("denomination") == "₿ symbol"
        if not self.wallet or len(self.wallet.payment_list) == 0:
            self.zap_label.set_text(lv.SYMBOL.REFRESH + " Waiting for zaps...")
            return
        # payment_list is a UniqueSortedList — iterates newest-first by
        # default. The Customise "Sort" setting can flip it to largest-
        # amount-first, which requires a re-sort into a regular list.
        limit = self._max_zaps()
        if (self.prefs.get_string("sort_order") or "recent") == "largest":
            payments = sorted(self.wallet.payment_list,
                              key=lambda p: p.amount_sats, reverse=True)
        else:
            payments = self.wallet.payment_list
        # Phase 0 of the blink wraps the line in a #FFE600 recolor tag;
        # phase 1 leaves it plain so the alternation reads as a yellow
        # flash on the freshly-arrived row.
        bright = self._blink_phase == 0
        lines = []
        for payment in payments:
            line = lv.SYMBOL.CHARGE + " " + _sanitize_display_text(str(payment))
            if bright and self._payment_id(payment) in self._blink_until:
                line = "#FFE600 " + line + "#"
            lines.append(line)
            if len(lines) >= limit:
                break
        self.zap_label.set_text("\n".join(lines))

    def _payment_id(self, payment):
        # Hashable identity tuple for blink tracking. id() doesn't work —
        # the wallet rebuilds Payment objects on each list refresh, so
        # id() changes even when the underlying record is the same.
        return (payment.epoch_time, payment.amount_sats, payment.comment)

    def _ensure_blink_timer(self):
        if self._blink_timer is None and self._blink_until:
            self._blink_timer = lv.timer_create(
                self._blink_tick, BLINK_TICK_MS, None)

    def _blink_tick(self, timer):
        # Toggle the blink phase, cull expired entries, re-render. Self-
        # stops the timer when nothing's blinking anymore so we're not
        # running a needless timer in the background.
        now = time.ticks_ms()
        self._blink_until = {pid: exp for pid, exp in self._blink_until.items()
                             if time.ticks_diff(exp, now) > 0}
        if not self._blink_until:
            self._blink_phase = 0
            if self._blink_timer is not None:
                self._blink_timer.delete()
                self._blink_timer = None
        else:
            self._blink_phase ^= 1
        self._render_zaps()

    def _on_screen_contact(self, event):
        # Stamp the contact timestamp on any touch. _idle_check uses it
        # to decide when to snap the zap list back to the top.
        self._last_screen_contact_ms = time.ticks_ms()

    def _idle_check(self, timer):
        # If no touch has been recorded for AUTO_SCROLL_IDLE_MS, scroll
        # the zap list back to the top so the device naturally re-presents
        # the most recent zaps. Reset the timestamp afterwards so the snap
        # fires once per idle stretch, not every tick.
        if self._last_screen_contact_ms is None:
            return
        now = time.ticks_ms()
        if time.ticks_diff(now, self._last_screen_contact_ms) >= AUTO_SCROLL_IDLE_MS:
            try:
                self.zap_container.scroll_to_y(0, True)
            except Exception as e:
                print("zaptv: idle scroll failed:", e)
            self._last_screen_contact_ms = None

    # --- receive QR ------------------------------------------------------

    def _effective_lightning_address(self):
        """Resolve the Lightning address shown in the receive QR. Priority:
        1. User-set Lightning Address pref (manual override).
        2. The connected wallet's own static receive code (LNbits LNURL
           or NWC `lud16`) — cached in a pref so it survives offline
           launches and so the Settings screen can show it too.
        3. <npub>@npub.cash derived from the configured npub.
        Returns None when none of those is available."""
        addr = self.prefs.get_string("lightning_address")
        if addr:
            return addr
        addr = self.prefs.get_string("wallet_static_receive_code")
        if addr:
            return addr
        npub = self.prefs.get_string("npub")
        if npub:
            return npub + "@" + NPUB_CASH_DOMAIN
        return None

    def _qr_clicked(self, event):
        # Launch the FullscreenQR activity with the current receive code
        # and ZapTV's theme so its colors match. No-op if there's nothing
        # to display (no wallet code, no npub).
        data = self._effective_lightning_address()
        if not data:
            return
        dark = (self.prefs.get_string("theme") or "dark") != "light"
        intent = Intent(activity_class=FullscreenQR)
        intent.putExtra("receive_qr_data", data)
        intent.putExtra("dark_mode", dark)
        self.startActivity(intent)

    def _refresh_receive_qr(self):
        addr = self._effective_lightning_address()
        if addr:
            self.receive_qr.update(addr, len(addr))
            self.receive_qr.remove_flag(lv.obj.FLAG.HIDDEN)
        else:
            self.receive_qr.add_flag(lv.obj.FLAG.HIDDEN)

    # --- profile picture -------------------------------------------------

    def _refresh_profile(self):
        self.profile_box.clean()
        self.name_label.set_text(
            _sanitize_display_text(self.prefs.get_string("name") or ""))
        source = self.prefs.get_string("profile_source") or "nostr"
        selfie_path = self._data_path("selfie.png")

        if source == "selfie" and self._file_exists(selfie_path):
            img = lv.image(self.profile_box)
            img.set_src("M:" + selfie_path)
            img.center()
            return

        if source == "nostr":
            npub = self.prefs.get_string("npub")
            if npub:
                jpg_path = self._profile_jpg_path()
                # Cache hit only when we have the image AND we've attempted
                # a metadata fetch for this npub (the latter tells us
                # whether to expect a name). Without the second check,
                # images cached before the auto-name code shipped would
                # prevent name from ever being backfilled.
                if (self.prefs.get_string("profile_cached_npub") == npub
                        and self._file_exists(jpg_path)
                        and self.prefs.get_string("profile_metadata_npub") == npub):
                    img = lv.image(self.profile_box)
                    img.set_src("M:" + jpg_path)
                    img.center()
                    return
                # Cache stale or missing — kick off a background fetch and
                # fall through to the placeholder while it runs.
                self._start_nostr_profile_fetch(npub)

        # Placeholder — shown when no source matches, no npub, or a Nostr
        # fetch is in flight. The on-device camera-selfie capture path is
        # still a TODO (would save an indexed-palette PNG to selfie_path
        # per MPOS_APP_DEV.md §6 so the selfie branch above can render it).
        placeholder = lv.label(self.profile_box)
        placeholder.set_text(lv.SYMBOL.IMAGE)
        placeholder.set_style_text_font(lv.font_montserrat_24, lv.PART.MAIN)
        # Same theme-beats-inheritance issue as in _apply_theme.
        placeholder.set_style_text_color(self._theme_colors()[1], lv.PART.MAIN)
        placeholder.center()

    def _file_exists(self, path):
        try:
            import os
            os.stat(path)
            return True
        except OSError:
            return False

    def _data_path(self, filename):
        """App-data file path, colocated with the prefs dir. Derived from
        prefs.appdir rather than hardcoding "data/<app>/" because MPOS
        0.13.0+ stores prefs in prefs/<app>/ and its legacy migration
        renames the whole data/<app>/ directory there — a hardcoded path
        would point at files the framework just moved. prefs.appdir is
        correct on both old (data/...) and new (prefs/...) firmware, and
        our files ride along with the migration."""
        appdir = getattr(self.prefs, "appdir", "data/" + self.appFullName)
        return appdir + "/" + filename

    def _profile_jpg_path(self):
        return self._data_path("profile.jpg")

    def _start_nostr_profile_fetch(self, npub):
        if self._profile_fetch_in_flight:
            return
        self._profile_fetch_in_flight = True
        TaskManager.create_task(self._fetch_nostr_profile_image(npub))

    async def _fetch_nostr_profile_image(self, npub):
        """Fetch the npub's kind-0 metadata, pull the `picture` URL, route
        it through an image proxy that re-encodes to JPEG (which tjpgd can
        render on-device), save the result, then refresh the box.

        All errors are caught + logged; the placeholder stays on screen so
        a failed fetch doesn't break the UI. The in-flight flag stops
        concurrent fetches piling up on rapid onResumes."""
        import ssl
        try:
            hex_pubkey = PublicKey.from_npub(npub).hex()
            print("zaptv: profile fetch for", hex_pubkey[:10] + "...")

            rm = RelayManager()
            rm.add_relay(NOSTR_PROFILE_RELAY)
            await rm.open_connections({"cert_reqs": ssl.CERT_NONE})
            for _ in range(50):  # up to 5 s for the relay to connect
                await TaskManager.sleep(0.1)
                if rm.connected_or_errored_relays() > 0:
                    break

            sub_id = "zaptv_profile_" + hex_pubkey[:8]
            filters = Filters([Filter(authors=[hex_pubkey], kinds=[0])])
            rm.add_subscription(sub_id, filters)
            req = [ClientMessageType.REQUEST, sub_id]
            req.extend(filters.to_json_array())
            rm.publish_message(json.dumps(req))

            picture_url = None
            display_name = None
            for _ in range(100):  # up to 10 s for the kind-0 event
                await TaskManager.sleep(0.1)
                if rm.message_pool.has_events():
                    ev = rm.message_pool.get_event()
                    try:
                        meta = json.loads(ev.event.content)
                        picture_url = meta.get("picture")
                        display_name = (meta.get("display_name")
                                        or meta.get("name"))
                        break  # one event is enough — we have the metadata
                    except Exception as e:
                        print("zaptv: parse kind-0 failed:", e)

            try:
                await rm.close_connections()
            except Exception as e:
                print("zaptv: close relay failed:", e)

            # Mark that we've completed a metadata fetch for this npub so
            # future opens skip the relay round-trip even when the npub's
            # kind-0 has no `picture` (or no `name`). Auto-populate the
            # Name pref from the metadata, but only when it's empty — a
            # manual entry the user set wins; clearing the field in
            # Settings re-enables auto-fill on the next fetch.
            ed = self.prefs.edit()
            ed.put_string("profile_metadata_npub", npub)
            if display_name and not self.prefs.get_string("name"):
                ed.put_string("name", display_name)
                print("zaptv: auto-populated name from npub:", display_name)
            ed.commit()

            if not picture_url:
                print("zaptv: no picture in kind-0 metadata")
                return

            proxy_url = IMG_PROXY_URL.format(url=picture_url)
            data = await DownloadManager.download_url(proxy_url)
            if not data:
                print("zaptv: profile download returned empty")
                return

            # Let the framework create its own prefs/data dir structure —
            # keeps us in sync with wherever prefs.appdir points.
            self.prefs.make_folder_structure()
            with open(self._profile_jpg_path(), "wb") as f:
                f.write(data)
            ed = self.prefs.edit()
            ed.put_string("profile_cached_npub", npub)
            ed.commit()
            print("zaptv: profile image cached, re-rendering")
            self._refresh_profile()
        except Exception as e:
            print("zaptv: profile fetch failed:", e)
            import sys
            sys.print_exception(e)
        finally:
            self._profile_fetch_in_flight = False

    # --- settings --------------------------------------------------------

    def _open_settings(self, event):
        # Two-level menu: a "Zaps!" sub-screen for wallet settings and a
        # "Profile" sub-screen, each its own SettingsActivity subclass.
        # No "Scan npub" row needed: the framework's InputActivity already
        # adds a "Scan data from QR code" button to every text setting when
        # the device has a camera, so Npub / NWC URL / xpub are scannable.
        intent = Intent(activity_class=MainSettingsActivity)
        intent.putExtra("prefs", self.prefs)
        intent.putExtra("settings", [
            {"title": "Zaps!", "placeholder": "Wallet settings",
             "ui": "activity", "activity_class": ZapsSettingsActivity},
            {"title": "Profile", "placeholder": "Npub, name, picture",
             "ui": "activity", "activity_class": ProfileSettingsActivity},
            {"title": "Customise", "placeholder": "Denomination, theme",
             "ui": "activity", "activity_class": CustomiseSettingsActivity},
            {"title": "About", "placeholder": "Version and web address",
             "ui": "activity", "activity_class": AboutActivity,
             "dont_persist": True},
        ])
        self.startActivity(intent)
