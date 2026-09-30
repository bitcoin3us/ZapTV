# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 ZapTV.org
#
# This file is part of ZapTV. ZapTV is free software: you can redistribute
# it and/or modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version. It is distributed WITHOUT
# ANY WARRANTY; see the GNU General Public License (LICENSE) for details.

"""Fetch an npub's Nostr profile (kind-0 metadata), falling back across relays.

Profiles are not on every relay, and any one relay can be down or silent,
so ZapTV asks a short list in turn and stops at the first that returns the
profile. One at a time, not all at once: on an ESP32-S3 each TLS websocket
takes internal RAM, and with the wallet's own connection open, a fourth
concurrent handshake fails with ENOMEM (measured on a Waveshare
ESP32-S3-Touch-LCD-3.5). Freed sessions also take a few seconds to come
back, so there is a short pause between relays.
"""

import gc
import json
import time

from nostr.filter import Filter, Filters

# purplepag.es specialises in profiles and relay lists, so it goes first.
PROFILE_RELAYS = (
    "wss://purplepag.es",
    "wss://relay.primal.net",
    "wss://relay.damus.io",
    "wss://relay.nostr.net",
)
# Per relay: an ESP32 TLS handshake alone can take a few seconds.
RELAY_TIMEOUT_MS = 6000
BETWEEN_RELAYS_S = 0.5
POLL_S = 0.1


def newer_profile(best, event, hex_pubkey):
    """Return (created_at, metadata) for the newer of `best` and `event`.

    `event` only counts if it is a kind-0 by `hex_pubkey` with a valid
    signature and a JSON object as content, so a relay can't substitute a
    forged profile or someone else's."""
    try:
        if event.kind != 0 or event.public_key != hex_pubkey:
            return best
        if best is not None and event.created_at <= best[0]:
            return best
        if not event.verify():
            return best
        meta = json.loads(event.content)
    except Exception:
        return best
    if not isinstance(meta, dict):
        return best
    return (event.created_at, meta)


async def fetch_profile(hex_pubkey, relays=PROFILE_RELAYS, ssl_options=None,
                        timeout_ms=RELAY_TIMEOUT_MS, new_manager=None,
                        sleep=None, now_ms=None):
    """Return the kind-0 metadata dict for `hex_pubkey` from the first relay
    that has it, or None.

    `new_manager`, `sleep` and `now_ms` default to nostr's RelayManager,
    TaskManager.sleep and time.ticks_ms; tests pass fakes."""
    if new_manager is None:
        from nostr.relay_manager import RelayManager
        new_manager = RelayManager
    if sleep is None:
        from mpos import TaskManager
        sleep = TaskManager.sleep
    if now_ms is None:
        now_ms = time.ticks_ms

    for i, url in enumerate(relays):
        if i:
            gc.collect()
            await sleep(BETWEEN_RELAYS_S)
        meta = await _ask_relay(new_manager(), url, hex_pubkey, ssl_options,
                                timeout_ms, sleep, now_ms)
        if meta is not None:
            return meta
        print("zaptv: no profile from", url)
    return None


async def _ask_relay(manager, url, hex_pubkey, ssl_options, timeout_ms,
                     sleep, now_ms):
    manager.add_relay(url)
    relay = manager.relays[url]
    # Relays drop EVENTs for subscriptions they don't know, so register it
    # before asking.
    sub_id = "zaptv_profile_" + hex_pubkey[:8]
    filters = Filters([Filter(authors=[hex_pubkey], kinds=[0], limit=1)])
    manager.add_subscription(sub_id, filters)
    req = json.dumps(["REQ", sub_id] + filters.to_json_array())
    pool = manager.message_pool
    asked = False
    best = None
    start = now_ms()

    await manager.open_connections(ssl_options)
    try:
        while True:
            if relay.connected and not asked:
                relay.publish(req)
                asked = True
            # Drain events before the end-of-stored-events notice that
            # follows them.
            while pool.has_events():
                best = newer_profile(best, pool.get_event().event, hex_pubkey)
            done = False
            while pool.has_eose_notices():
                pool.get_eose_notice()
                done = True
            while pool.has_closed_messages():
                pool.get_closed_message()
                done = True
            if not relay.connected and relay.error_counter > 0:
                done = True   # never connected, or dropped
            if done or time.ticks_diff(now_ms(), start) >= timeout_ms:
                break
            await sleep(POLL_S)
    finally:
        try:
            await manager.close_connections()
        except Exception as e:
            print("zaptv: closing profile relay failed:", e)

    return best[1] if best is not None else None
