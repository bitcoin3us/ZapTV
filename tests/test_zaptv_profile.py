"""ZapTV profile tests: the relay-fallback kind-0 fetch and the "Add npub" button.

The fetch tests drive zaptv_profile_fetch.fetch_profile() with fake relays and a
fake clock, so they need no network and run in milliseconds. The button
tests start ZapTV on the desktop build with a throwaway prefs file and the
profile fetch stubbed out.

Run from a MicroPythonOS checkout with the app linked in:

    ln -s /path/to/org.zaptv.app internal_filesystem/apps/
    ./scripts/test_runner.py /path/to/tests/test_zaptv_profile.py
"""
import json
import os
import sys
import unittest

import asyncio
import lvgl as lv
import mpos
from mpos import SharedPreferences
from mpos.ui.view import screen_stack
from mpos.ui.testing import wait_for_render

sys.path.insert(0, "apps/org.zaptv.app")
import zaptv_profile_fetch as profile_fetch
from nostr.event import Event
from nostr.key import PrivateKey

KEY = PrivateKey()
PK = KEY.public_key.hex()
NPUB = KEY.public_key.bech32()


def signed(meta, created_at, key=KEY, kind=0):
    e = Event(json.dumps(meta), key.public_key.hex(), created_at, kind, [])
    key.sign_event(e)
    return e


# --- fakes for the relay manager --------------------------------------------

class _Msg:
    def __init__(self, url, event=None):
        self.url = url
        self.event = event


class FakePool:
    def __init__(self):
        self.events, self.eose, self.closed = [], [], []

    def has_events(self):
        return bool(self.events)

    def get_event(self):
        return self.events.pop(0)

    def has_eose_notices(self):
        return bool(self.eose)

    def get_eose_notice(self):
        return self.eose.pop(0)

    def has_closed_messages(self):
        return bool(self.closed)

    def get_closed_message(self):
        return self.closed.pop(0)


class FakeRelay:
    """A scripted relay. Times are ms: connect_at / fail_at from when its
    connection was opened, replies / eose_after / closed_after from when it
    received the request."""

    def __init__(self, url, connect_at=0, fail_at=None, replies=(),
                 eose_after=None, closed_after=None):
        self.url = url
        self.connect_at, self.fail_at = connect_at, fail_at
        self.replies = list(replies)
        self.eose_after, self.closed_after = eose_after, closed_after
        self.connected = False
        self.error_counter = 0
        self.sent = []
        self.opened_at = None
        self.asked_at = None
        self.sub_id = None

    def publish(self, message):
        self.sent.append(message)
        self.asked_at = self.clock.t
        self.sub_id = json.loads(message)[1]

    def tick(self, now, pool, subscriptions):
        up = now - self.opened_at
        if self.fail_at is not None and up >= self.fail_at:
            if self.error_counter == 0:
                self.connected = False
                self.error_counter = 1
            return
        if self.connect_at is not None and up >= self.connect_at:
            self.connected = True
        if self.asked_at is None:
            return
        since = now - self.asked_at
        while self.replies and since >= self.replies[0][0]:
            event = self.replies.pop(0)[1]
            # Like nostr.relay.Relay: EVENTs for unregistered subscriptions
            # are dropped as invalid.
            if self.sub_id in subscriptions:
                pool.events.append(_Msg(self.url, event))
        if self.eose_after is not None and since >= self.eose_after:
            pool.eose.append(_Msg(self.url))
            self.eose_after = None
        if self.closed_after is not None and since >= self.closed_after:
            pool.closed.append(_Msg(self.url))
            self.closed_after = None


class World:
    """The fake network: a clock, the scripted relays, and a record of which
    relay connections were opened and closed, and how many at once."""

    def __init__(self, relays):
        self.t = 0
        self.scripted = {r.url: r for r in relays}
        self.managers = []
        self.open_now = 0
        self.max_open = 0
        self.opened = []
        self.closed = []

    def now(self):
        return self.t

    async def sleep(self, seconds):
        self.t += int(seconds * 1000)
        for m in self.managers:
            if m.is_open:
                m.tick()

    def new_manager(self):
        m = FakeManager(self)
        self.managers.append(m)
        return m


class FakeManager:
    def __init__(self, world):
        self.world = world
        self.relays = {}
        self.subscriptions = {}
        self.message_pool = FakePool()
        self.is_open = False

    def add_relay(self, url):
        relay = self.world.scripted[url]
        relay.clock = self.world
        self.relays[url] = relay

    def add_subscription(self, sub_id, filters):
        self.subscriptions[sub_id] = filters

    async def open_connections(self, ssl_options=None):
        self.is_open = True
        w = self.world
        w.open_now += len(self.relays)
        w.max_open = max(w.max_open, w.open_now)
        for url, relay in self.relays.items():
            relay.opened_at = w.t
            w.opened.append(url)
        self.tick()

    async def close_connections(self):
        self.is_open = False
        self.world.open_now -= len(self.relays)
        self.world.closed.extend(self.relays)

    def tick(self):
        for relay in self.relays.values():
            relay.tick(self.world.t, self.message_pool, self.subscriptions)


def run_fetch(*relays):
    world = World(relays)
    meta = asyncio.run(profile_fetch.fetch_profile(
        PK, relays=[r.url for r in relays], new_manager=world.new_manager,
        sleep=world.sleep, now_ms=world.now))
    return meta, world


def has_profile(name, created_at=100, after=200):
    return {"replies": [(after, signed({"name": name}, created_at))],
            "eose_after": after + 50}


TIMEOUT = profile_fetch.RELAY_TIMEOUT_MS


class TestFetchProfile(unittest.TestCase):

    def test_first_relay_with_the_profile_wins_and_later_ones_are_not_asked(self):
        meta, world = run_fetch(FakeRelay("wss://a", **has_profile("a")),
                                FakeRelay("wss://b", **has_profile("b")))
        self.assertEqual(meta["name"], "a")
        self.assertEqual(world.opened, ["wss://a"])
        self.assertEqual(world.closed, ["wss://a"])

    def test_relay_without_the_profile_falls_back_to_the_next(self):
        meta, world = run_fetch(FakeRelay("wss://a", eose_after=300),
                                FakeRelay("wss://b", **has_profile("b")))
        self.assertEqual(meta["name"], "b")
        self.assertEqual(world.opened, ["wss://a", "wss://b"])

    def test_failed_relay_falls_back_to_the_next(self):
        meta, world = run_fetch(FakeRelay("wss://a", connect_at=None, fail_at=300),
                                FakeRelay("wss://b", **has_profile("b")))
        self.assertEqual(meta["name"], "b")
        self.assertTrue(world.t < 2000, "took %d ms" % world.t)

    def test_refused_subscription_falls_back_to_the_next(self):
        meta, _ = run_fetch(FakeRelay("wss://a", closed_after=200),
                            FakeRelay("wss://b", **has_profile("b")))
        self.assertEqual(meta["name"], "b")

    def test_silent_relay_is_given_up_on_after_its_timeout(self):
        meta, world = run_fetch(FakeRelay("wss://silent"),
                                FakeRelay("wss://b", **has_profile("b")))
        self.assertEqual(meta["name"], "b")
        self.assertTrue(TIMEOUT <= world.t < TIMEOUT + 1500, "took %d ms" % world.t)

    def test_relay_that_connects_late_still_gets_the_request(self):
        late = FakeRelay("wss://a", connect_at=2000, **has_profile("late"))
        meta, _ = run_fetch(late)
        self.assertEqual(len(late.sent), 1)
        self.assertTrue('"kinds": [0]' in late.sent[0] or '"kinds":[0]' in late.sent[0])
        self.assertEqual(meta["name"], "late")

    def test_only_one_relay_connection_is_open_at_a_time(self):
        # ESP32 TLS sessions need internal RAM: concurrent ones hit ENOMEM.
        meta, world = run_fetch(FakeRelay("wss://a", eose_after=100),
                                FakeRelay("wss://b", fail_at=200),
                                FakeRelay("wss://c", eose_after=100))
        self.assertTrue(meta is None)
        self.assertEqual(world.max_open, 1)
        self.assertEqual(world.opened, ["wss://a", "wss://b", "wss://c"])
        self.assertEqual(world.closed, world.opened)

    def test_newest_version_from_a_relay_wins(self):
        for order in ((100, 200), (200, 100)):
            relay = FakeRelay("wss://a", eose_after=400, replies=[
                (100, signed({"name": "v%d" % order[0]}, order[0])),
                (200, signed({"name": "v%d" % order[1]}, order[1]))])
            meta, _ = run_fetch(relay)
            self.assertEqual(meta["name"], "v200")

    def test_nothing_anywhere_returns_none_after_trying_every_relay(self):
        meta, world = run_fetch(FakeRelay("wss://a"), FakeRelay("wss://b"))
        self.assertTrue(meta is None)
        self.assertEqual(world.closed, ["wss://a", "wss://b"])
        self.assertTrue(2 * TIMEOUT <= world.t < 2 * TIMEOUT + 2000, "took %d ms" % world.t)


class TestNewerProfile(unittest.TestCase):

    def test_accepts_a_newer_signed_profile(self):
        best = profile_fetch.newer_profile((100, {"name": "old"}),
                                           signed({"name": "new"}, 200), PK)
        self.assertEqual(best, (200, {"name": "new"}))

    def test_rejects_a_forged_newer_profile(self):
        real = signed({"name": "real"}, 100)
        forged = Event(json.dumps({"name": "forged", "picture": "https://x/evil.jpg"}),
                       PK, 999, 0, [], real.signature)
        best = profile_fetch.newer_profile((100, {"name": "real"}), forged, PK)
        self.assertEqual(best[1]["name"], "real")

    def test_rejects_someone_elses_profile(self):
        other = PrivateKey()
        self.assertTrue(profile_fetch.newer_profile(
            None, signed({"name": "other"}, 200, key=other), PK) is None)

    def test_rejects_other_kinds_and_non_object_content(self):
        self.assertTrue(profile_fetch.newer_profile(
            None, signed({"name": "note"}, 200, kind=1), PK) is None)
        bad = Event("not json", PK, 200, 0, [])
        KEY.sign_event(bad)
        self.assertTrue(profile_fetch.newer_profile(None, bad, PK) is None)
        listy = Event("[1, 2]", PK, 200, 0, [])
        KEY.sign_event(listy)
        self.assertTrue(profile_fetch.newer_profile(None, listy, PK) is None)


# --- the "Add npub" button ----------------------------------------------------

def _top():
    return screen_stack[-1][0]


def _zaptv():
    if type(_top()).__name__ != "ZapTV":
        mpos.AppManager.start_app("org.zaptv.app")
        wait_for_render(60)
    return _top()


def _buttons(app):
    box = app.profile_box
    return [box.get_child(i) for i in range(box.get_child_count())
            if isinstance(box.get_child(i), lv.button)]


def _label_texts(obj):
    out = []
    for i in range(obj.get_child_count()):
        c = obj.get_child(i)
        if isinstance(c, lv.label):
            out.append(c.get_text())
        out.extend(_label_texts(c))
    return out


class TestAddNpubButton(unittest.TestCase):

    def setUp(self):
        self.app = _zaptv()
        self.real_prefs = self.app.prefs
        self.prefs = SharedPreferences("org.zaptv.app", filename="test_zaptv_profile.json")
        self.app.prefs = self.prefs
        self.fetches = []
        self.app._start_nostr_profile_fetch = self.fetches.append
        self.configured = True
        self.app._wallet_configured = lambda: self.configured

    def tearDown(self):
        while type(_top()).__name__ != "ZapTV":
            _top().finish()
            wait_for_render(10)
        self.app.prefs = self.real_prefs
        del self.app._start_nostr_profile_fetch
        del self.app._wallet_configured
        try:
            os.remove(self.prefs.filepath)
        except OSError:
            pass
        self.app._refresh_profile()
        wait_for_render(5)

    def test_no_npub_shows_a_keypad_reachable_add_button(self):
        self.app._refresh_profile()
        wait_for_render(5)
        buttons = _buttons(self.app)
        self.assertEqual(len(buttons), 1)
        self.assertTrue("Add npub" in _label_texts(buttons[0]))
        self.assertTrue(buttons[0].get_group() is not None, "not in the focus group")

    def test_no_button_behind_the_welcome_screen(self):
        self.configured = False
        self.app._refresh_profile()
        self.assertEqual(_buttons(self.app), [])

    def test_no_button_once_an_npub_is_set(self):
        self.prefs.edit().put_string("npub", NPUB).commit()
        self.app._refresh_profile()
        self.assertEqual(_buttons(self.app), [])
        self.assertEqual(self.fetches, [NPUB])

    def test_button_opens_the_npub_input_and_saving_fetches_the_profile(self):
        self.app._refresh_profile()
        wait_for_render(5)
        _buttons(self.app)[0].send_event(lv.EVENT.CLICKED, None)
        wait_for_render(30)
        inp = _top()
        self.assertEqual(type(inp).__name__, "InputActivity")
        self.assertEqual(inp.setting["key"], "npub")
        inp.textarea.set_text(NPUB)
        inp.save_input()
        wait_for_render(30)
        self.assertEqual(type(_top()).__name__, "ZapTV")
        self.assertEqual(self.prefs.get_string("npub"), NPUB)
        self.assertEqual(self.fetches, [NPUB])
        self.assertEqual(_buttons(self.app), [])


if __name__ == "__main__":
    unittest.main()
