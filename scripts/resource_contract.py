"""Finite session-wide abuse budgets, independent of queue backpressure.

Endpoints supply their published service limits. Counters survive reconnect,
replay, snapshot, and queue draining. This accounting contract does not replace
message validation, request resolution, or transport queue reservations.
"""
from dataclasses import dataclass
from session_contract import SessionError

MAX_SAFE_INTEGER = 2**53 - 1


@dataclass(frozen=True)
class SessionLimits:
    control_messages: int = 100_000
    control_bytes: int = 64 * 1024 * 1024
    ledger_bytes: int = 64 * 1024 * 1024
    replay_bytes: int = 256 * 1024 * 1024

    def __post_init__(self):
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if type(value) is not int or not 0 < value <= MAX_SAFE_INTEGER:
                raise ValueError(f"{name} must be a positive safe integer")


class SessionBudget:
    """Charge before allocating attempt history, assigning seq, or delivering.

    A control receipt includes duplicates and recoverable-invalid input after
    association with this session. Charge ledger bytes only for new immutable
    entries, and replay bytes for every scheduled retransmission. A transaction
    is charged atomically using one call. Fatal notification is a single bounded
    best-effort exception; it must never bypass the transport backlog limit.
    """
    def __init__(self, limits: SessionLimits | None = None):
        self.limits = limits or SessionLimits()
        self.used = {name: 0 for name in self.limits.__dataclass_fields__}
        self.closed = False

    def charge(self, *, control_messages=0, control_bytes=0, ledger_bytes=0, replay_bytes=0):
        increments = dict(control_messages=control_messages, control_bytes=control_bytes,
                          ledger_bytes=ledger_bytes, replay_bytes=replay_bytes)
        if self.closed:
            raise SessionError("resource_limit", "session budget is closed")
        if any(type(value) is not int or not 0 <= value <= MAX_SAFE_INTEGER
               for value in increments.values()):
            raise ValueError("budget increments must be nonnegative safe integers")
        proposed = {key: self.used[key] + value for key, value in increments.items()}
        if any(value > getattr(self.limits, key) for key, value in proposed.items()):
            self.closed = True
            raise SessionError("resource_limit", "session-wide resource budget exceeded")
        self.used = proposed
