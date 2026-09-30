"""Sent payments: shown as negative amounts, or hidden by a Zaps! setting.

NIP-47 wallets report every amount as an unsigned number plus a separate
"type" (incoming / outgoing), so ZapTV has to sign sends itself or they look
like received zaps. LNbits and the on-chain wallet already produce signed
amounts. Settings > Zaps! > Sent payments picks "show" (default) or "hide".

Run from a MicroPythonOS checkout with the app linked in:

    ln -s /path/to/org.zaptv.app internal_filesystem/apps/
    ./scripts/test_runner.py /path/to/tests/test_zaptv_sent_payments.py
"""
import os
import sys
import unittest

import lvgl as lv
import mpos
from mpos import Intent, NumberFormat, SharedPreferences
from mpos.ui.view import screen_stack
from mpos.ui.testing import wait_for_render

sys.path.insert(0, "apps/org.zaptv.app")
import asyncio
from nostr.key import PrivateKey
from zaptv_lnbits_wallet import LNBitsWallet
from zaptv_nwc_wallet import NWCWallet
from zaptv_payment import Payment
from zaptv_unique_sorted_list import UniqueSortedList

NWC_URL = ("nostr+walletconnect://" + "ab" * 32
           + "?relay=wss://relay.example.com&secret=" + "cd" * 32)


def _nwc_wallet():
    w = NWCWallet(NWC_URL)
    w.slot_key = None          # no writes to the real wallet cache
    return w


def _tx(kind, sats, t=1000, description="hi"):
    return {"type": kind, "amount": sats * 1000, "created_at": t,
            "description": description}


class TestNwcDirection(unittest.TestCase):

    def test_list_transactions_sends_become_negative(self):
        w = _nwc_wallet()
        self.assertEqual(w._payment_from_nwc(_tx("outgoing", 42)).amount_sats, -42)
        self.assertEqual(w._payment_from_nwc(_tx("incoming", 42)).amount_sats, 42)
        untyped = {"amount": 21000, "created_at": 5, "description": "x"}
        self.assertEqual(w._payment_from_nwc(untyped).amount_sats, 21)

    def test_send_notification_adds_a_negative_row_and_leaves_the_balance(self):
        w = _nwc_wallet()
        w.last_known_balance = 1000
        seen = []
        w.balance_updated_cb = seen.append
        w._handle_notification(_tx("outgoing", 42))
        self.assertEqual([p.amount_sats for p in w.payment_list], [-42])
        # A budget-reporting wallet (Primal) would otherwise read the next
        # unchanged poll as a rise and fire the lightning strike.
        self.assertEqual(w.last_known_balance, 1000)
        self.assertEqual(seen, [])

    def test_receive_notification_raises_the_balance(self):
        w = _nwc_wallet()
        w.last_known_balance = 1000
        seen = []
        w.balance_updated_cb = seen.append
        w._handle_notification(_tx("incoming", 42))
        self.assertEqual(w.last_known_balance, 1042)
        self.assertEqual(seen, [42])            # sats_added > 0: the strike
        self.assertEqual([p.amount_sats for p in w.payment_list], [42])

    def test_receive_notification_before_any_balance_still_strikes(self):
        w = _nwc_wallet()
        self.assertTrue(w.last_known_balance is None)
        seen = []
        w.balance_updated_cb = seen.append
        w._handle_notification(_tx("incoming", 42))
        self.assertEqual([p.amount_sats for p in w.payment_list], [42])
        self.assertEqual(seen, [42])
        # The zap amount is not the wallet balance: leave that to get_balance.
        self.assertTrue(w.last_known_balance is None)

    def test_only_settled_payment_notifications_count(self):
        w = _nwc_wallet()
        w.last_known_balance = 1000
        seen = []
        w.balance_updated_cb = seen.append
        w._handle_notification(_tx("incoming", 42), "hold_invoice_accepted")
        self.assertEqual(len(w.payment_list), 0)
        self.assertEqual(seen, [])
        w._handle_notification(_tx("incoming", 42), "payment_received")
        w._handle_notification(_tx("outgoing", 7, t=2000), "payment_sent")
        self.assertEqual(sorted(p.amount_sats for p in w.payment_list), [-7, 42])

    def test_hidden_sends_are_not_stored_from_notifications(self):
        w = _nwc_wallet()
        try:
            NWCWallet.INCOMING_ONLY = True
            for t in range(5):
                w._handle_notification(_tx("outgoing", 5, t=t))
            self.assertEqual(len(w.payment_list), 0)
        finally:
            NWCWallet.INCOMING_ONLY = False

    def test_unknown_notification_type_is_ignored(self):
        w = _nwc_wallet()
        w._handle_notification(_tx("sideways", 42))
        self.assertEqual(len(w.payment_list), 0)

    def test_hidden_sends_ask_the_wallet_for_receives_only(self):
        w = _nwc_wallet()
        try:
            NWCWallet.INCOMING_ONLY = False
            self.assertFalse("type" in w._list_transactions_params())
            NWCWallet.INCOMING_ONLY = True
            params = w._list_transactions_params()
            self.assertEqual(params["type"], "incoming")
            self.assertEqual(params["limit"], NWCWallet.PAYMENTS_TO_SHOW)
        finally:
            NWCWallet.INCOMING_ONLY = False


class _Event:
    def __init__(self, kind, tags):
        self.kind, self.tags = kind, tags


class TestOnlyOurReplies(unittest.TestCase):
    """Another client on the same NWC connection (e.g. Lightning Piggy set up
    with the same string) gets its replies addressed to the same key."""

    def test_replies_to_other_requests_are_foreign(self):
        w = _nwc_wallet()
        ours, theirs = "aa" * 32, "bb" * 32
        w._request_ids = [ours]
        self.assertFalse(w._is_foreign_reply(_Event(23195, [["p", "cc" * 32], ["e", ours]])))
        self.assertTrue(w._is_foreign_reply(_Event(23195, [["p", "cc" * 32], ["e", theirs]])))
        # Notifications answer no request, and an untagged reply can't be judged.
        self.assertFalse(w._is_foreign_reply(_Event(23196, [["p", "cc" * 32]])))
        self.assertFalse(w._is_foreign_reply(_Event(23195, [["p", "cc" * 32]])))

    def test_published_requests_are_remembered(self):
        wallet_key = PrivateKey()
        url = ("nostr+walletconnect://" + wallet_key.public_key.hex()
               + "?relay=wss://relay.example.com&secret=" + PrivateKey().hex())
        w = NWCWallet(url)
        w.slot_key = None
        w.private_key = PrivateKey(bytes.fromhex(w.secret))
        published = []

        class _Relays:
            def publish_event(self, event):
                published.append(event.id)
        w.relay_manager = _Relays()
        asyncio.run(w.fetch_payments())
        asyncio.run(w.fetch_balance())
        self.assertEqual(len(published), 2)
        self.assertEqual(w._request_ids, published)
        class _Request:
            id = "x"
        for _ in range(40):
            w._remember_request(_Request())
        self.assertEqual(len(w._request_ids), 16)    # bounded


class TestLnbitsFilter(unittest.TestCase):

    def test_hidden_sends_ask_lnbits_for_receives_only(self):
        w = LNBitsWallet("https://lnbits.example.com/", "readkey")
        try:
            LNBitsWallet.INCOMING_ONLY = False
            self.assertFalse("amount" in w._payments_url())
            LNBitsWallet.INCOMING_ONLY = True
            url = w._payments_url()
            self.assertTrue(url.startswith("https://lnbits.example.com/api/v1/payments?limit="), url)
            self.assertTrue(url.endswith("&amount%5Bgt%5D=0"), url)
        finally:
            LNBitsWallet.INCOMING_ONLY = False


class TestPaymentFormat(unittest.TestCase):

    def tearDown(self):
        Payment.use_symbol = False

    def test_negative_amounts(self):
        Payment.use_symbol = False
        self.assertEqual(str(Payment(1, -42, "hi")), "-42 sats: hi")
        self.assertEqual(str(Payment(1, -42, "")), "-42 sats spent")
        Payment.use_symbol = True
        self.assertEqual(str(Payment(1, -42, "hi")), "-₿42: hi")
        self.assertEqual(str(Payment(1, -42, "")), "-₿42 spent")
        big = NumberFormat.format_number(1234)
        self.assertEqual(str(Payment(1, -1234, "hi")), "-₿" + big + ": hi")

    def test_positive_amounts_unchanged(self):
        Payment.use_symbol = True
        self.assertEqual(str(Payment(1, 42, "hi")), "₿42: hi")
        self.assertEqual(str(Payment(1, 42, "")), "₿42 received!")
        Payment.use_symbol = False
        self.assertEqual(str(Payment(1, 42, "hi")), "42 sats: hi")


# --- the setting, in the running app -------------------------------------------

def _top():
    return screen_stack[-1][0]


def _zaptv():
    if type(_top()).__name__ != "ZapTV":
        mpos.AppManager.start_app("org.zaptv.app")
        wait_for_render(60)
    return _top()


class _FakeWallet:
    def __init__(self, *payments):
        self.payment_list = UniqueSortedList()
        for p in payments:
            self.payment_list.add(p)


RECEIVED = Payment(300, 21, "in")
SENT = Payment(200, -42, "out")
RECEIVED_EARLIER = Payment(100, 100, "in too")


class TestSentPaymentsSetting(unittest.TestCase):

    def setUp(self):
        self.app = _zaptv()
        self.real_prefs = self.app.prefs
        self.real_wallet = self.app.wallet
        self.prefs = SharedPreferences("org.zaptv.app", filename="test_zaptv_sent.json")
        self.app.prefs = self.prefs

    def tearDown(self):
        while type(_top()).__name__ != "ZapTV":
            _top().finish()
            wait_for_render(10)
        app = self.app
        if app._blink_timer is not None:
            app._blink_timer.delete()
            app._blink_timer = None
        app._blink_until = {}
        app._seen_payments = None
        app.prefs = self.real_prefs
        app.wallet = self.real_wallet
        NWCWallet.INCOMING_ONLY = LNBitsWallet.INCOMING_ONLY = False
        try:
            os.remove(self.prefs.filepath)
        except OSError:
            pass
        app._render_zaps()
        wait_for_render(5)

    def _set(self, value):
        self.prefs.edit().put_string("sent_payments", value).commit()

    def _shown(self):
        self.app._render_zaps()
        return self.app.zap_label.get_text()

    def test_sends_show_as_negative_by_default(self):
        self.app.wallet = _FakeWallet(RECEIVED, SENT, RECEIVED_EARLIER)
        text = self._shown()
        self.assertTrue("-42 sats: out" in text, text)
        self.assertTrue("21 sats: in" in text and "100 sats: in too" in text, text)

    def test_hide_removes_sends_only(self):
        self.app.wallet = _FakeWallet(RECEIVED, SENT, RECEIVED_EARLIER)
        self._set("hide")
        text = self._shown()
        self.assertFalse("-42" in text, text)
        self.assertTrue("21 sats: in" in text and "100 sats: in too" in text, text)

    def test_hide_with_only_sends_says_waiting(self):
        self.app.wallet = _FakeWallet(SENT)
        self._set("hide")
        self.assertTrue("Waiting for zaps" in self._shown())

    def test_a_hidden_send_does_not_blink(self):
        self._set("hide")
        self.app.wallet = _FakeWallet(RECEIVED)
        self.app._on_payments()                       # baseline
        self.app.wallet.payment_list.add(Payment(400, -7, "new send"))
        self.app._on_payments()
        self.assertEqual(self.app._blink_until, {})

    def test_a_shown_send_blinks_like_any_new_row(self):
        self.app.wallet = _FakeWallet(RECEIVED)
        self.app._on_payments()
        new = Payment(400, -7, "new send")
        self.app.wallet.payment_list.add(new)
        self.app._on_payments()
        self.assertTrue(self.app._payment_id(new) in self.app._blink_until)

    def test_building_the_wallet_passes_the_setting_to_the_wallets(self):
        self.prefs.edit().put_string("wallet_type", "nwc").put_string("nwc_url", NWC_URL).commit()
        for value, expected in (("hide", True), ("show", False)):
            self._set(value)
            w = self.app._build_wallet()
            self.assertTrue(w is not None)
            self.assertEqual(NWCWallet.INCOMING_ONLY, expected)
            self.assertEqual(LNBitsWallet.INCOMING_ONLY, expected)
            w.slot_key = None

    def test_errors_show_when_every_row_is_hidden(self):
        self.app.wallet = _FakeWallet(SENT)
        self._set("hide")
        self.app._on_error("Could not connect to any Nostr Wallet Connect relays.")
        self.assertTrue("Could not connect" in self.app.zap_label.get_text())

    def test_zaps_settings_page_has_the_row(self):
        import zaptv
        intent = Intent(activity_class=zaptv.ZapsSettingsActivity)
        intent.putExtra("prefs", self.prefs)
        self.app.startActivity(intent)
        wait_for_render(30)
        rows = [s for s in _top().settings if s.get("key") == "sent_payments"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["title"], "Sent payments")
        self.assertEqual(row["ui"], "radiobuttons")
        self.assertEqual([v for _, v in row["ui_options"]], ["show", "hide"])
        self.assertEqual(row["default_value"], "show")


if __name__ == "__main__":
    unittest.main()
