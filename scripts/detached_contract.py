"""Finite host lifecycle model for fatal-closed, default-only seats.

This composes the existing request evaluator; it does not implement a mahjong
host, authentication, transport delivery, or byte quotas. Requests and ordinary
start_kyoku boundaries are assumed already validated by the game validator.
``enqueue_ready`` is the nonfatal session's admission/reservation result; while
disconnected it represents readiness of the redelivery ledger, not a working
transport. Ledger entries below are semantic observations, not serialized
YAMAI messages. Pre-group-start ingress buffering remains outside this model.
"""
from __future__ import annotations

from copy import deepcopy
from request_contract import evaluate


class DetachedTable:
    def __init__(self, *, grace_ms: int, bank_ms: int, bank_scope: str = "game",
                 invalid_action_policy: str = "reject", ron_policy: str = "multiple"):
        if bank_scope not in {"game", "kyoku"}:
            raise ValueError("unknown bank scope")
        self.grace_ms, self.initial_bank = grace_ms, bank_ms
        self.bank_scope = bank_scope
        self.policy, self.ron_policy = invalid_action_policy, ron_policy
        self.banks = [bank_ms] * 4
        self.closed = set()
        self.connected = set(range(4))
        self.token_valid = [True] * 4
        self.ledger = {seat: [] for seat in range(4)}
        self.effects = []
        self.now_us = 0
        self.game_ended = False
        self.decision = None
        self.last_result = None
        self.used_requests = set()
        self.next_group = 1
        self.terminal_requests = {}
        self.seen_late = set()

    def _time(self, at_us):
        if type(at_us) is not int or at_us < self.now_us:
            raise ValueError("clock must be nondecreasing integer microseconds")
        self.now_us = at_us

    def _emit(self, seat, message):
        # Fatal-closed output is not queued, counted, or added to its ledger.
        if seat not in self.closed:
            self.ledger[seat].append(deepcopy(message))

    def _refresh(self, operation):
        d = self.decision
        d["trace"]["steps"].append(operation)
        result = evaluate(d["trace"])[-1]
        if result["phase"] == "CLOSED":
            d["trace"]["steps"].append({"op": "resolve", "at_us": operation["at_us"]})
            result = evaluate(d["trace"])[-1]
        after_effects = []
        for request in d["requests"]:
            rid, seat = request["request_id"], request["seat"]
            state = result["requests"][rid]
            if state["phase"] != "OPEN":
                self.banks[seat] = state["time_bank_ms"]
            messages = result["messages"][rid]
            terminal_seen = False
            for index, message in enumerate(messages):
                late = terminal_seen and message["kind"] == "ack" and message["status"] == "stale"
                if message["kind"] == "ack" and message["status"] != "rejected":
                    terminal_seen = True
                if index >= d["published"].get(rid, 0):
                    payload = {"request_id": rid, **message}
                    if late:
                        after_effects.append((seat, payload))
                    else:
                        self._emit(seat, payload)
            d["published"][rid] = len(messages)
        for effect in result["effects"][d["effect_count"]:]:
            self.effects.append(deepcopy(effect))
            for seat in range(4):
                self._emit(seat, {"kind": "effect", **effect})
        d["effect_count"] = len(result["effects"])
        for seat, message in after_effects:
            self._emit(seat, message)
        d["result"] = result
        if result["phase"] == "TERMINAL":
            for request in d["requests"]:
                rid = request["request_id"]
                self.terminal_requests[rid] = {"seat": request["seat"],
                                               **deepcopy(result["requests"][rid])}
                terminal_seen = False
                for message in result["messages"][rid]:
                    if message["kind"] == "ack" and message["status"] != "rejected":
                        if not terminal_seen:
                            terminal_seen = True
                        elif message["status"] == "stale":
                            self.seen_late.add((rid, message["action_id"]))
            self.last_result = deepcopy(result)
            self.decision = None

    def _maybe_start(self):
        d = self.decision
        if d is None or d["start_us"] is not None or d["ready"] != d["seats"]:
            return
        d["start_us"] = self.now_us
        self._refresh({"op": "advance", "at_us": 0})

    def issue(self, requests, *, target, at_us):
        """Prepare one turn or all three reaction seats; clocks start at readiness."""
        self.advance(at_us)
        if self.game_ended or self.decision is not None:
            raise ValueError("cannot issue another decision")
        requests = deepcopy(requests)
        seats = {r["seat"] for r in requests}
        ids = {r["request_id"] for r in requests}
        if len(requests) not in {1, 3} or len(seats) != len(requests) or not seats <= set(range(4)):
            raise ValueError("one turn or three distinct reaction seats required")
        if len(requests) == 3 and seats != set(range(4)) - {target}:
            raise ValueError("reaction must retain all three other seats")
        if len(ids) != len(requests) or ids & self.used_requests:
            raise ValueError("request IDs must be fresh")
        for r in requests:
            r["time_bank_ms"] = self.banks[r["seat"]]
        if len(requests) == 3:
            members = [{"request_id": r["request_id"], "seat": r["seat"]}
                       for r in sorted(requests, key=lambda r: r["seat"])]
            deadline = max(self.grace_ms + r["timeout_ms"] + r["time_bank_ms"] for r in requests)
            if deadline > 1200000:
                raise ValueError("generated group deadline exceeds the profile limit")
            if self.next_group > 9007199254740991:
                raise ValueError("group ID allocator exhausted")
            for r in requests:
                r.update(decision_group_id="dg:" + str(self.next_group),
                         decision_group_members=deepcopy(members),
                         decision_group_deadline_ms=deadline,
                         decision_group_close="all_selected_or_deadline")
            self.next_group += 1
        self.used_requests |= ids
        self.decision = {"requests": requests, "seats": seats, "ready": seats & self.closed,
                         "start_us": None, "published": {}, "effect_count": 0,
                         "trace": {"grace_ms": self.grace_ms, "invalid_action_policy": self.policy,
                                   "ron_policy": self.ron_policy, "target": target,
                                   "requests": requests, "steps": []}}
        self._maybe_start()

    def prepare(self, seat, *, enqueue_ready, at_us):
        self.advance(at_us)
        d = self.decision
        if d is None or seat not in d["seats"] or d["start_us"] is not None:
            raise ValueError("no preparation due for this seat")
        if seat not in self.closed and not enqueue_ready:
            return False
        if seat not in d["ready"]:
            request = next(r for r in d["requests"] if r["seat"] == seat)
            self._emit(seat, {"kind": "request", **request})
        d["ready"].add(seat)
        self._maybe_start()
        return True

    def advance(self, at_us):
        self._time(at_us)
        if self.decision is not None and self.decision["start_us"] is not None:
            self._refresh({"op": "advance", "at_us": at_us - self.decision["start_us"]})

    def submit(self, seat, request_id, action_id, *, at_us):
        self.advance(at_us)
        if seat in self.closed or seat not in self.connected or self.game_ended:
            return "ignored"
        terminal = self.terminal_requests.get(request_id)
        if terminal is not None and terminal["seat"] == seat:
            if terminal["source"] == "user" and terminal["status"] != "stale":
                if action_id == terminal["action_id"]:
                    return "ignored"
                self._emit(seat, {"kind": "error", "code": "request_conflict",
                                  "request_id": request_id, "action_id": action_id,
                                  "original_status": terminal["status"]})
                return "request_conflict"
            attempt = (request_id, action_id)
            if attempt in self.seen_late:
                return "ignored"
            self.seen_late.add(attempt)
            self._emit(seat, {"kind": "ack", "request_id": request_id, "action_id": action_id,
                              "status": "stale", "elapsed_ms": terminal["elapsed_ms"],
                              "time_bank_ms": terminal["time_bank_ms"]})
            return "stale"
        d = self.decision
        if d is None or not any(r["request_id"] == request_id and r["seat"] == seat for r in d["requests"]):
            self._emit(seat, {"kind": "error", "code": "invalid_action",
                              "request_id": request_id, "action_id": action_id})
            return "invalid_action"
        if d["start_us"] is None:
            return "unavailable"
        self._refresh({"op": "submit", "at_us": at_us - d["start_us"],
                       "request_id": request_id, "action_id": action_id})
        return "processed"

    def fatal_close(self, seat, *, at_us):
        # Closure suppresses new output immediately; due internal timers still run.
        self._time(at_us)
        self.closed.add(seat)
        self.connected.discard(seat)
        self.token_valid[seat] = False
        if self.decision is not None and seat in self.decision["seats"]:
            self.decision["ready"].add(seat)
        self.advance(at_us)
        self._maybe_start()

    def disconnect(self, seat, *, at_us):
        self.advance(at_us)
        self.connected.discard(seat)

    def resume(self, seat, *, at_us):
        self.advance(at_us)
        if not self.token_valid[seat] or seat in self.closed:
            return False
        self.connected.add(seat)
        return True

    def can_replace(self, seat):
        """Whether the reservation has ended for participation in a NEW game.

        This never permits replacement or rejoining the same finished game.
        """
        return self.game_ended

    def start_kyoku(self, *, at_us):
        self.advance(at_us)
        if self.game_ended or self.decision is not None:
            raise ValueError("round reset requires a decision boundary")
        if self.bank_scope == "kyoku":
            self.banks = [self.initial_bank] * 4

    def end_game(self, *, at_us):
        self.advance(at_us)
        if self.decision is not None:
            raise ValueError("game cannot end with an unresolved decision")
        self.game_ended = True
