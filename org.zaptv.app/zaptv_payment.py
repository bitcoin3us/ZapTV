# SPDX-License-Identifier: GPL-3.0-or-later AND MIT
# Copyright (C) 2026 ZapTV.org
#
# Derived from Lightning Piggy's payment.py in MicroPythonOS,
# Copyright (c) 2025 MicroPythonOS (Thomas Farstrike), MIT License. That
# copyright notice and permission notice are preserved for the original
# portions. ZapTV's modifications, and the file as a whole, are distributed
# under the GNU General Public License, either version 3 or (at your option)
# any later version, WITHOUT ANY WARRANTY; see LICENSE for details.

# Payment class — one entry in the on-screen transaction list.
#
# Amounts are always stored as signed integer satoshis (negative = outgoing /
# fee-only self-transfer); the unit shown to the user is always sats too,
# regardless of whether the balance label is set to bits / micro-BTC / etc.
# The only display variation is whether to use the "₿" prefix vs the "sats"
# suffix (`use_symbol` toggle), and the thousands separator follows the
# MicroPythonOS NumberFormat preference (US "1,234", EU "1.234", etc.) so
# transaction amounts match the balance label's separator style.

from mpos import NumberFormat


def _format_sats(amount):
    """Render a signed satoshi integer with the user's MPOS-configured
    thousands separator."""
    return NumberFormat.format_number(int(amount))


class Payment:
    use_symbol = False  # When True, use ₿ prefix instead of "sats" suffix

    def __init__(self, epoch_time, amount_sats, comment):
        self.epoch_time = epoch_time
        self.amount_sats = amount_sats
        self.comment = comment

    def __str__(self):
        amount_str = _format_sats(self.amount_sats)
        if Payment.use_symbol:
            # The sign goes before the symbol: "-₿42", not "₿-42".
            if self.amount_sats < 0:
                amount_str = "-₿" + _format_sats(-self.amount_sats)
            else:
                amount_str = "₿" + amount_str
            if not self.comment:
                verb = "spent"
                if self.amount_sats > 0:
                    verb = "received!"
                return f"{amount_str} {verb}"
            return f"{amount_str}: {self.comment}"
        else:
            sattext = "sats"
            if self.amount_sats == 1:
                sattext = "sat"
            if not self.comment:
                verb = "spent"
                if self.amount_sats > 0:
                    verb = "received!"
                return f"{amount_str} {sattext} {verb}"
            return f"{amount_str} {sattext}: {self.comment}"

    def __eq__(self, other):
        if not isinstance(other, Payment):
            return False
        return self.epoch_time == other.epoch_time and self.amount_sats == other.amount_sats and self.comment == other.comment

    def __lt__(self, other):
        if not isinstance(other, Payment):
            return NotImplemented
        return (self.epoch_time, self.amount_sats, self.comment) < (other.epoch_time, other.amount_sats, other.comment)

    def __le__(self, other):
        if not isinstance(other, Payment):
            return NotImplemented
        return (self.epoch_time, self.amount_sats, self.comment) <= (other.epoch_time, other.amount_sats, other.comment)

    def __gt__(self, other):
        if not isinstance(other, Payment):
            return NotImplemented
        return (self.epoch_time, self.amount_sats, self.comment) > (other.epoch_time, other.amount_sats, other.comment)

    def __ge__(self, other):
        if not isinstance(other, Payment):
            return NotImplemented
        return (self.epoch_time, self.amount_sats, self.comment) >= (other.epoch_time, other.amount_sats, other.comment)
