"""ZapTV must not share module names with other apps.

All MicroPythonOS apps share one sys.modules, so importing a module name that
another app already imported silently returns that app's module. Lightning
Piggy ships wallet.py, nwc_wallet.py, payment.py and friends and often
auto-starts at boot; while ZapTV used the same names it ran Lightning Piggy's
wallet code and wrote to Lightning Piggy's transaction cache, and whichever
app started second in a boot got the other's modules.

Run from a MicroPythonOS checkout with the app linked in:

    ln -s /path/to/org.zaptv.app internal_filesystem/apps/
    ./scripts/test_runner.py /path/to/tests/test_zaptv_module_names.py
"""
import os
import sys
import unittest

APP = "apps/org.zaptv.app"

# The names ZapTV's helpers had before they were prefixed; Lightning Piggy
# still uses most of them.
GENERIC = ("fullscreen_qr", "lightning", "lnbits_wallet", "nwc_wallet",
           "onchain_wallet", "payment", "profile_fetch", "unique_sorted_list",
           "wallet", "wallet_cache")


class _Impostor:
    """Stands in for another app's module of the same name. Any attribute
    ZapTV imports from it is a class that says where it came from."""

    def __init__(self, name):
        self.__name__ = name

    def __getattr__(self, attr):
        cls = type(attr, (), {})
        cls.impostor = self.__name__
        return cls


def _app_modules():
    return [f[:-3] for f in os.listdir(APP) if f.endswith(".py")]


class _FreshImport:
    """Save every module name involved, let the test rearrange sys.modules,
    import zaptv fresh, and put everything back afterwards."""

    def __init__(self):
        names = set(GENERIC) | set(_app_modules())
        self.saved = {n: sys.modules[n] for n in names if n in sys.modules}
        self.names = names
        for n in names:
            sys.modules.pop(n, None)
        if APP not in sys.path:
            sys.path.insert(0, APP)

    def restore(self):
        for n in self.names:
            sys.modules.pop(n, None)
        sys.modules.update(self.saved)


class TestModuleNames(unittest.TestCase):

    def test_every_helper_module_has_the_zaptv_prefix(self):
        helpers = [m for m in _app_modules() if m != "zaptv"]
        self.assertTrue(helpers, "no helper modules found in " + APP)
        clashes = [m for m in helpers if not m.startswith("zaptv_")]
        self.assertEqual(clashes, [], "helper modules without the zaptv_ prefix")

    def test_zaptv_uses_its_own_modules_when_another_app_loaded_first(self):
        fresh = _FreshImport()
        try:
            for name in GENERIC:
                sys.modules[name] = _Impostor(name)
            import zaptv
            for attr, module in (("NWCWallet", "zaptv_nwc_wallet"),
                                 ("LNBitsWallet", "zaptv_lnbits_wallet"),
                                 ("OnchainWallet", "zaptv_onchain_wallet"),
                                 ("Payment", "zaptv_payment"),
                                 ("Lightning", "zaptv_lightning"),
                                 ("FullscreenQR", "zaptv_fullscreen_qr")):
                cls = getattr(zaptv, attr)
                self.assertFalse(hasattr(cls, "impostor"),
                                 attr + " came from another app's module")
                self.assertTrue(cls is getattr(sys.modules[module], attr), attr)
            self.assertTrue(zaptv.wallet_cache is sys.modules["zaptv_wallet_cache"])
            self.assertTrue(zaptv.fetch_profile
                            is sys.modules["zaptv_profile_fetch"].fetch_profile)
            # ...and the wallets' own base class and helpers are ZapTV's too.
            nwc = sys.modules["zaptv_nwc_wallet"]
            self.assertTrue(nwc.Wallet is sys.modules["zaptv_wallet"].Wallet)
            self.assertTrue(sys.modules["zaptv_wallet"].wallet_cache
                            is sys.modules["zaptv_wallet_cache"])
        finally:
            fresh.restore()

    def test_zaptv_leaves_generic_names_free_for_other_apps(self):
        fresh = _FreshImport()
        try:
            import zaptv
            taken = [n for n in GENERIC if n in sys.modules]
            self.assertEqual(taken, [], "ZapTV claimed generic module names")
        finally:
            fresh.restore()


if __name__ == "__main__":
    unittest.main()
