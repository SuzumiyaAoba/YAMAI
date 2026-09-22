"""Executable negotiation, recovery and resource contracts for YAMAI specification.

The schema/JSON callbacks are supplied by the artifact validator. These
contracts do not implement a mahjong game or an authorization service.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable
from game_contract import EventState, GameError, canonical_action
from scoring_reference import ScoringError


class SessionError(ValueError):
    def __init__(self, code: str, message: str, severity: str = "fatal"):
        super().__init__(message)
        self.code = code
        self.severity = severity


def require(condition: bool, code: str, message: str, severity: str = "fatal") -> None:
    if not condition:
        raise SessionError(code, message, severity)


def check_clock(request: dict, clock: dict, grace: int, *, user: bool = False, timeout: bool = False) -> None:
    deadline = grace + request["timeout_ms"] + request["time_bank_ms"]
    elapsed = clock["elapsed_ms"]
    require(0 <= elapsed <= deadline and (not user or elapsed < deadline),
            "invalid_message", "selection clock exceeds its original deadline")
    if "remaining_ms" in request and request.get("selection") is None:
        require(elapsed >= deadline - request["remaining_ms"], "invalid_message",
                "selection clock precedes an observed open snapshot")
    require(not timeout or elapsed == deadline, "invalid_message", "default before deadline without default policy")
    consumed = min(max(0, elapsed - grace - request["timeout_ms"]), request["time_bank_ms"])
    require(clock["time_bank_ms"] == request["time_bank_ms"] - consumed,
            "invalid_message", "selection time-bank arithmetic differs")


def check_snapshot_clock(request: dict, grace: int) -> None:
    """Both remaining durations describe the same atomic snapshot instant."""
    deadline = grace + request["timeout_ms"] + request["time_bank_ms"]
    remaining = request["remaining_ms"]
    require(remaining <= deadline, "invalid_message", "snapshot remaining time exceeds original budget")
    if "decision_group_id" not in request:
        return
    group_deadline = request["decision_group_deadline_ms"]
    group_remaining = request["decision_group_remaining_ms"]
    require(group_deadline >= deadline and remaining <= group_remaining <= group_deadline,
            "invalid_message", "snapshot group clock differs from original deadlines")
    selection = request["selection"]
    if selection is None:
        require(group_remaining - remaining == group_deadline - deadline,
                "invalid_message", "open request and group clocks describe different snapshot instants")
    elif group_remaining > 0:
        require(selection["elapsed_ms"] <= group_deadline - group_remaining,
                "invalid_message", "selection occurs after the snapshot's group clock")


def negotiate(hello: dict, join: dict, welcome: dict, context: dict,
              validate: Callable[[str, dict], None], protocol: str,
              revision: str, profile_hash: str, *, check_client_support: bool = True) -> dict:
    validate("hello", hello)
    validate("join-proposal", join)
    require(join["version"] == protocol and join["version"] in hello["versions"], "unsupported_version", "no selected common version")
    profiles = {p["name"]: p for p in hello["profiles"]}
    require(join["profile"] == "riichi-4p" and join["profile"] in profiles, "unsupported_profile", "profile is not advertised")
    profile = profiles[join["profile"]]
    require(join["profile_revision"] == revision and join["profile_revision"] in profile["revisions"], "profile_mismatch", "profile revision differs")
    require(join["version"] in profile["protocol_versions"][revision], "profile_mismatch", "profile revision does not support the selected protocol")
    require(join["profile_hash"] == profile_hash == profile["hashes"][revision], "profile_mismatch", "profile hash differs")
    require(join["mode"] in context.get("supported_modes", ["play", "spectate", "replay"]), "unsupported_view", "host does not implement the requested mode")
    supported_views = context.get("supported_views", {}).get(join["mode"])
    require(supported_views is None or join["view"] in supported_views, "unsupported_view", "host does not implement the requested view")
    host = set(hello["capabilities"]["required"] + hello["capabilities"]["optional"])
    player = set(join["capabilities"]["required"] + join["capabilities"]["optional"])
    required = set(hello["capabilities"]["required"] + join["capabilities"]["required"])
    enabled = host & player
    require(required <= enabled, "unsupported_capability", "required capability is absent at the peer")
    if join["mode"] != "play":
        require("resume" not in required, "unsupported_capability", "resume is unavailable in this mode")
        enabled.discard("resume")
    if join["mode"] == "spectate" and context.get("game_started", False):
        require("snapshot" in enabled, "unsupported_capability", "mid-game spectate needs snapshot")
    limits = {"max_message_bytes": 1048576, "max_json_depth": 64, "max_unresolved_requests": 4}
    require(all(hello["receive_limits"][key] == value and join["receive_limits"][key] == value for key, value in limits.items()), "unsupported_limit", "profile receive limits cannot be met")
    if join["mode"] in {"spectate", "replay"}:
        require(context.get("target_available", True), "resume_unavailable", "target is unavailable")

    resumed = "resume" in join
    if resumed:
        require("resume" in enabled and join["mode"] == "play", "resume_unavailable", "resume is not enabled")
        require(context.get("secure_transport", False), "resume_unavailable", "resume requires authenticated confidential transport")
        previous = context.get("resume_state")
        require(isinstance(previous, dict), "resume_unavailable", "session is not retained")
        require(not previous.get("fatal", False), "resume_unavailable", "fatal session cannot resume")
        require(join["resume"]["token"] == previous["token"] and not previous.get("consumed", False), "resume_unavailable", "token is invalid or consumed")
        require(context["now_ms"] < previous["expires_at_ms"], "resume_unavailable", "token expired")
        require(join["resume"]["last_seq"] <= previous["highest_seq"], "resume_unavailable", "client claims an unissued sequence")
        old = previous["welcome"]
        require(all(join[key] == old[key] for key in ("mode", "view", "profile", "profile_revision", "profile_hash")), "resume_unavailable", "resume identity differs")
        require(sorted(enabled) == old["capabilities"], "resume_unavailable", "resume capabilities differ")

    validate("welcome", welcome)
    require(welcome["yamai"] == join["version"], "invalid_message", "welcome version differs")
    for key in ("mode", "view", "profile", "profile_revision", "profile_hash"):
        require(welcome[key] == join[key], "invalid_message", "welcome selection differs")
    require(welcome["capabilities"] == sorted(enabled), "invalid_message", "welcome enabled set differs")
    require(welcome["resumed"] is resumed, "invalid_message", "welcome resume flag differs")
    if join["mode"] in {"spectate", "replay"}:
        target_id = join["target"]["id"] if join["target"]["type"] == "game" else context.get("target_game_id")
        require(target_id is not None and welcome["game_id"] == target_id, "invalid_message", "welcome target game differs")
    if resumed:
        previous = context["resume_state"]
        old = previous["welcome"]
        require(all(welcome[key] == old[key] for key in ("session_id", "game_id", "seat", "players", "rules")), "invalid_message", "resume changed retained session facts")
        require(welcome["replay_from_seq"] == join["resume"]["last_seq"] + 1 and welcome["replay_through_seq"] == previous["highest_seq"], "invalid_message", "resume replay bounds differ")
        require(welcome["resume"]["token"] != previous["token"], "invalid_message", "resume token was not rotated")
        if "scores" in previous:
            require(welcome["scores"] == previous["scores"], "invalid_message", "resumed welcome scores differ from captured state")
    else:
        require(welcome["session_id"] not in context.get("used_session_ids", []), "invalid_message", "new session id was reused")
        if join["mode"] in {"play", "replay"}:
            require(welcome["scores"] == [welcome["rules"]["starting_points"]] * 4,
                    "invalid_message", "new play/replay scores differ from the initial game state")
        if join["mode"] == "play":
            available = context.get("available_seats")
            if available is not None:
                require(bool(available) and all(type(s) is int and 0 <= s < 4 for s in available), "resource_limit", "no assignable seat")
                requested = join.get("seat", min(available))
                require(requested in available, "resource_limit", "requested seat is occupied or unavailable")
                require(welcome["seat"] == requested, "invalid_message", "welcome changed the selected seat")
            elif "seat" in join:
                require(welcome["seat"] == join["seat"], "invalid_message", "welcome changed the requested seat")
            require(welcome["game_id"] not in context.get("finished_game_ids", []), "invalid_message", "new game id was reused")
    if check_client_support:
        for path, accepted in context.get("supported_rules", {}).items():
            value: Any = welcome["rules"]
            for key in path.split("."):
                value = value[key]
            require(value in accepted, "unsupported_rules", "client does not support the offered rule value")
        for yaku in welcome["rules"]["local_yaku"]:
            capability = context.get("local_yaku_capabilities", {}).get(yaku)
            supported = context.get("local_yaku_supported", [])
            require(yaku in supported and capability in enabled, "unsupported_rules", "local yaku is not bound to an understood enabled capability")
    return {"session_id": welcome["session_id"], "game_id": welcome["game_id"],
            "mode": welcome["mode"], "view": welcome["view"], "seat": welcome["seat"],
            "capabilities": sorted(enabled), "resumed": resumed}


def check_token_trace(trace: dict, validate: Callable[[str, dict], None],
                      protocol: str, revision: str, profile_hash: str) -> list[dict]:
    retained = deepcopy(trace["initial"])
    used = set()
    observations = []
    previous_time = 0
    for step in trace["steps"]:
        now = step["now_ms"]
        require(type(now) is int and now >= previous_time, "invalid_message", "resume clock moved backwards")
        previous_time = now
        context = {**trace.get("context", {}), "resume_state": retained, "now_ms": now,
                   "secure_transport": step.get("secure_transport", True)}
        try:
            negotiate(step["hello"], step["join"], step["welcome"], context, validate, protocol, revision, profile_hash, check_client_support=False)
            old = retained["token"]
            new = step["welcome"]["resume"]["token"]
            require(new not in used and new != old, "invalid_message", "token identity was reused")
            used.add(old)
            retained.update(token=new, expires_at_ms=now + step["welcome"]["resume"]["expires_in_ms"],
                            welcome=deepcopy(step["welcome"]), active_connection=step["connection_id"])
            outcome = "accepted"
        except SessionError as error:
            outcome = error.code
        observations.append({"outcome": outcome, "token": retained["token"],
                             "expires_at_ms": retained["expires_at_ms"],
                             "active_connection": retained["active_connection"]})
    return observations


class Receiver:
    """Per-session wire prefix and recovery state, with immutable raw payloads."""
    def __init__(self, welcome: dict, decode: Callable[[bytes], dict], validate: Callable[[str, dict], None], *, last_seq: int = 0, initial_snapshot: bool = False):
        self.welcome = welcome
        self.decode = decode
        self.validate = validate
        self.applied = last_seq
        self.known: dict[int, bytes] = {}
        self.floor = 0
        self.gap_received = 0
        self.recovery = "resume" if welcome["resumed"] else "initial" if initial_snapshot else None
        self.resume_snapshot_allowed = welcome["resumed"]
        self.through = welcome.get("replay_through_seq", last_seq)
        self.started = bool(last_seq)
        self.ended = False
        self.closed = False
        self.request_ids: set[str] = set()
        self.active_requests: set[str] = set()
        self.awaiting_request = False
        self.requests: dict[str, dict] = {}
        # Diagnostic ACKs constrain later clocks without charging the bank.
        self.request_clock_floor: dict[str, int] = {}
        self.terminal_acks: dict[str, dict] = {}
        self.late_attempts: set[tuple[str, str]] = set()
        self.expected_effects: list[dict] = []
        self.unadopted_reaction: dict | None = None
        self.time_bank_ms = welcome["rules"]["time_control"]["bank_ms"]
        self.original_seq = 0
        self.last_event_seq: int | None = None
        self.event_seq_floor = 0  # Preserved even by snapshots outside a round.
        self.applications: list[int] = []
        self.game = EventState(welcome["rules"], welcome["seat"] if welcome["mode"] == "play" else None)
        if self.recovery == "resume" and self.through == last_seq:
            self.recovery = None

    def begin_resume(self, welcome: dict) -> None:
        require(not self.closed, "resume_unavailable", "fatal receiver session cannot resume")
        self.validate("welcome", welcome)
        require(welcome["resumed"] and all(welcome[key] == self.welcome[key] for key in
                ("session_id", "game_id", "mode", "view", "seat", "profile", "profile_revision", "profile_hash", "players", "rules", "capabilities")),
                "invalid_message", "resume changed session identity")
        require(welcome["replay_from_seq"] == self.applied + 1 and welcome["replay_through_seq"] >= self.applied,
                "invalid_message", "resume does not start at the applied prefix")
        self.welcome = welcome
        self.through = welcome["replay_through_seq"]
        self.recovery = "resume" if self.applied < self.through else None
        self.resume_snapshot_allowed = True
        self.gap_received = 0

    def receive(self, raw: bytes) -> str:
        if self.closed:
            return "closed"
        saved = {key: deepcopy(value) for key, value in vars(self).items() if key not in {"welcome", "decode", "validate"}}
        try:
            return self._receive(raw)
        except Exception as error:
            vars(self).update(saved)
            if getattr(error, "severity", "fatal") == "fatal":
                self.closed = True
            raise

    def _check_snapshot_prefix(self, state: dict, *, allow_unreceived_request: bool = False) -> None:
        pending = state.get("pending_requests", [])
        require(not (self.expected_effects or self.unadopted_reaction),
                "invalid_message", "snapshot lacks the acknowledged decision's result event")
        require((allow_unreceived_request and self.awaiting_request)
                or (not self.awaiting_request and {r["request_id"] for r in pending} == self.active_requests),
                "invalid_message", "snapshot changes the request set without a new cause event")
        expected_game = deepcopy(self.game)
        expected_bank = self.time_bank_ms
        for request in pending:
            selection = request["selection"]
            if selection is not None:
                # Selection may advance before a terminal ACK is emitted;
                # its clock and immutable candidate are checked below.
                expected_bank = selection["time_bank_ms"]
                expected_game.acknowledge(request, {**selection, "status": "defaulted"})
        require(state["mode"] != "play" or state["time_bank_ms"] == expected_bank,
                "invalid_message", "contiguous snapshot changes the shared time bank")
        require(state["mode"] != "replay" or state["original_seq"] == self.original_seq,
                "invalid_message", "contiguous snapshot advances the recording cursor")
        if state["kyoku"] is not None:
            require(state["kyoku"]["turn"]["last_event_seq"] == self.last_event_seq,
                    "invalid_message", "contiguous snapshot changes the cause sequence")
        expected_game.check_snapshot_prefix(state)

    def _expect_effects(self, request: dict, ack: dict) -> None:
        """Bind terminal decisions to their subsequent public effects."""
        if ack["status"] == "stale":
            require(self.welcome["rules"]["invalid_action_policy"] == "chombo",
                    "invalid_message", "request cancellation is not enabled")
            self.expected_effects = [{"type": "end_kyoku", "result_type": "penalty"}]
            return
        chosen = next(c["action"] for c in request["legal_actions"] if c["action_id"] == ack["action_id"])
        if "decision_group_id" in request and (ack["status"] in {"passed", "superseded"}
                                               or ack["status"] == "defaulted" and chosen["type"] == "none"):
            self.unadopted_reaction = {"status": ack["status"], "type": chosen["type"]}
        if ack["status"] not in {"accepted", "defaulted"}:
            return
        action = deepcopy(chosen)
        kind = action["type"]
        if kind == "none" or kind.startswith("x-"):
            return  # Private effects are checked by the negotiated owner.
        effects = []
        current = self.game.round
        if current["pending_dora"] is not None:
            effects.append({"type": "dora"})
        cause = self.game.last_cause
        if kind in {"chi", "pon", "daiminkan"} and current["reach_status"][cause["actor"]]["state"] == "declared":
            effects.append({"type": "reach_accepted", "actor": cause["actor"]})
        if kind == "reach":
            effects.extend([{"type": "reach", "actor": action["actor"]}, action["dahai"]])
        elif kind in {"chi", "pon"}:
            discard = action.pop("dahai")
            effects.extend([action, discard])
        elif kind in {"ankan", "kakan"}:
            effects.append({**action, "type": kind + "_declared"})
        elif kind == "hora":
            effects.append({"type": "end_kyoku", "winner": action["actor"]})
        elif kind == "ryukyoku":
            effects.append({"type": "end_kyoku", "reason": "kyushukyuhai"})
        else:
            effects.append(action)
        self.expected_effects = effects

    def _check_effect(self, event: dict) -> None:
        if self.unadopted_reaction is not None:
            reaction = self.unadopted_reaction
            resolution = event["type"] in {"chi", "pon", "daiminkan", "ankan", "kakan", "tsumo", "end_kyoku"}
            priority = {"chi": 1, "pon": 2, "daiminkan": 2, "hora": 3}
            if resolution and reaction["status"] == "superseded" and reaction["type"] in priority:
                result = event.get("result", {})
                if event["type"] in {"chi", "pon", "daiminkan"}:
                    target = event["target"]
                    require((priority[event["type"]], -((event["actor"] - target) % 4))
                            > (priority[reaction["type"]], -((self.welcome["seat"] - target) % 4)),
                            "invalid_message", "superseding call has lower priority")
                else:
                    require(result.get("type") == "hora" or result.get("reason") == "sanchaho",
                            "invalid_message", "superseded choice has no higher-priority result")
                    if reaction["type"] == "hora" and result.get("type") == "hora":
                        target = self.game.last_cause["actor"]
                        require(self.welcome["rules"]["ron_policy"] == "head_bump"
                                and all((win["actor"] - target) % 4 < (self.welcome["seat"] - target) % 4
                                        for win in result["wins"]),
                                "invalid_message", "superseded hora did not lose to head bump")
            if event["type"] in {"chi", "pon", "daiminkan"}:
                require(event["actor"] != self.welcome["seat"], "invalid_message",
                        "unadopted reaction generated its own call")
            elif event["type"] == "end_kyoku":
                result = event["result"]
                require(result["type"] != "penalty", "invalid_message",
                        "penalty requires a cancellation ACK")
                if result["type"] == "hora":
                    require(all(win["actor"] != self.welcome["seat"] for win in result["wins"]),
                            "invalid_message", "unadopted reaction generated its own win")
                elif result["type"] == "ryukyoku" and result["reason"] == "sanchaho":
                    require(self.unadopted_reaction == {"status":"superseded", "type":"hora"},
                            "invalid_message", "three-ron draw requires this seat's explicit hora")
            if resolution:
                self.unadopted_reaction = None
        if not self.expected_effects:
            return
        # A pao assignment belongs between the adopted pon and its discard.
        # EventState checks the exact derived assignment before applying it.
        if event["type"] == "pao" and self.game.pao_due:
            return
        expected = self.expected_effects[0]
        require(event["type"] == expected["type"], "invalid_message", "event differs from acknowledged action")
        if "winner" in expected:
            require(event["result"]["type"] == "hora" and expected["winner"] in {w["actor"] for w in event["result"]["wins"]},
                    "invalid_message", "acknowledged winner is missing from settlement")
        elif "reason" in expected:
            require(event["result"]["type"] == "ryukyoku" and event["result"]["reason"] == expected["reason"],
                    "invalid_message", "settlement differs from acknowledged draw")
        elif "result_type" in expected:
            require(event["result"]["type"] == expected["result_type"],
                    "invalid_message", "cancelled decision did not end with a penalty")
        elif expected["type"] in {"dora", "reach_accepted"}:
            require(all(event.get(k) == value for k, value in expected.items()), "invalid_message", "derived event differs from acknowledged action")
        else:
            require(canonical_action(event) == canonical_action(expected), "invalid_message", "event differs from acknowledged candidate arguments")
        self.expected_effects.pop(0)

    def _receive(self, raw: bytes) -> str:
        message = self.decode(raw)
        require(isinstance(message, dict), "invalid_message", "message is not an object")
        require(isinstance(message.get("kind"), str)
                and message["kind"] in {"event", "request", "ack", "error", "snapshot"},
                "invalid_message", "unexpected host application kind")
        for key in ("yamai", "session_id", "game_id"):
            require(message.get(key) == self.welcome[key], "invalid_message", "application identity differs")
        seq = message.get("seq")
        require(type(seq) is int and seq > 0, "invalid_message", "host sequence is invalid")
        if seq <= self.applied:
            if seq in self.known:
                require(raw == self.known[seq], "sequence_conflict", "same sequence has different payload bytes")
            else:
                require(seq <= self.floor, "sequence_conflict", "unknown duplicate outside snapshot coverage")
            return "duplicate"
        self.validate("host-application", message)
        kind = message["kind"]
        mode = self.welcome["mode"]
        require(mode == "play" or kind not in {"request", "ack"}, "invalid_message",
                "observer received a request or ACK")
        if kind == "snapshot":
            require("snapshot" in self.welcome["capabilities"], "invalid_message", "snapshot capability is not enabled")
            contiguous = seq == self.applied + 1
            require(not contiguous or not (self.expected_effects or self.unadopted_reaction),
                    "invalid_message", "snapshot interrupts acknowledged action effects")
            # A retained snapshot is an ordinary ledger entry. Its old prefix
            # need not cover the current recovery frontier, and applying it
            # must not end replay early. Only a jump needs recovery authority.
            require((contiguous and self.started) or self.recovery is not None or self.resume_snapshot_allowed, "invalid_message", "snapshot lacks bootstrap or recovery authorization")
            require(self.recovery != "initial" or seq == 1, "invalid_message",
                    "initial observer snapshot must use the first session sequence")
            required_floor = self.applied if contiguous else max(self.applied, self.gap_received, self.through if self.resume_snapshot_allowed else 0)
            require(message["replaces_through_seq"] >= required_floor and seq == message["replaces_through_seq"] + 1, "invalid_message", "snapshot replacement range is insufficient")
            state = message["state"]
            require(all(state[key] == self.welcome[key] for key in ("mode", "view", "seat", "players")), "invalid_message", "snapshot changed session identity")
            if not self.started and mode == "spectate" and not self.welcome["resumed"] and seq == 1:
                require(state["scores"] == self.welcome["scores"], "invalid_message",
                        "initial observer snapshot differs from welcome scores")
            require(not self.ended or state["game_phase"] == "ended", "invalid_message", "snapshot reopened an ended game")
            turn = state["kyoku"]["turn"] if state["kyoku"] is not None else None
            if turn is not None:
                cause_seq = turn["last_event_seq"]
                require(cause_seq is not None or state["mode"] == "spectate" and self.event_seq_floor == 0,
                        "invalid_message", "snapshot erased a known session event sequence")
                if cause_seq is not None:
                    require(cause_seq >= self.event_seq_floor, "invalid_message", "snapshot rewound its cause event")
                    if cause_seq in self.known:
                        cause = self.decode(self.known[cause_seq])
                        require(cause["kind"] == "event" and canonical_action(cause["event"]) == canonical_action(turn["last_event"]),
                                "invalid_message", "snapshot cause differs from the retained ledger event")
                    if cause_seq == self.last_event_seq and self.game.round is not None:
                        require(canonical_action(turn["last_event"]) == canonical_action(self.game.last_cause),
                                "invalid_message", "snapshot changed an already observed cause event")
            same_cause = (turn is not None and self.game.round is not None
                          and turn["last_event_seq"] == self.last_event_seq)
            if self.started and (contiguous or self.ended or same_cause):
                try:
                    # A gap with the same last event can contain diagnostics,
                    # selection and an as-yet unreceived request, but cannot
                    # alter facts that require a newer game event.
                    self._check_snapshot_prefix(state, allow_unreceived_request=not contiguous and same_cause)
                except (GameError, ScoringError) as error:
                    raise SessionError("invalid_message", str(error)) from error
            if self.started and state["mode"] == "play" and self.welcome["rules"]["time_control"]["bank_scope"] == "game":
                require(state["time_bank_ms"] <= self.time_bank_ms, "invalid_message",
                        "snapshot replenishes a game-scoped time bank")
            self.floor = message["replaces_through_seq"]
            self.started = True
            self.ended = state["game_phase"] == "ended"
            try:
                self.game.restore(state)
            except (GameError, ScoringError) as error:
                raise SessionError("invalid_message", str(error)) from error
            if state["mode"] == "play":
                require(state["time_bank_ms"] <= self.welcome["rules"]["time_control"]["bank_ms"], "invalid_message", "snapshot invents time bank")
                self.time_bank_ms = state["time_bank_ms"]
            if state["mode"] == "replay":
                require(state["original_seq"] >= self.original_seq, "invalid_message", "snapshot rewound the recording cursor")
                self.original_seq = state["original_seq"]
            kyoku = state.get("kyoku")
            turn = kyoku["turn"] if isinstance(kyoku, dict) else None
            self.last_event_seq = turn["last_event_seq"] if turn is not None else None
            if turn is not None:
                bootstrap = state["mode"] == "spectate" and message["seq"] == 1 and message["replaces_through_seq"] == 0
                last_event_seq = turn["last_event_seq"]
                require((not bootstrap or last_event_seq is None)
                        and (last_event_seq is None and state["mode"] == "spectate"
                             or type(last_event_seq) is int and 0 < last_event_seq <= message["replaces_through_seq"]),
                        "invalid_message", "snapshot cause event lies outside replacement range")
                if last_event_seq is not None:
                    self.event_seq_floor = last_event_seq
                self.validate("visible-event", {"event": turn["last_event"], "mode": state["mode"],
                                                "view": self.welcome["view"], "seat": self.welcome["seat"]})
                self_state = kyoku.get("self_state")
                if self_state is not None:
                    require(self_state["time_bank_ms"] == state["time_bank_ms"],
                            "invalid_message", "snapshot time bank differs")
                view = self.welcome["view"]
                visible_seat = state["seat"] if state["mode"] == "play" else view.get("seat") if isinstance(view, dict) else None
                for actor, hand in enumerate(kyoku["hands"]):
                    require(("tiles" in hand) == (view == "full" or actor == visible_seat),
                            "invalid_message", "snapshot hand visibility differs")
            if state["mode"] == "play":
                needs_request = turn is not None and ((turn["phase"] == "awaiting_action" and turn["actor"] == state["seat"])
                                                      or (turn["phase"] == "awaiting_responses" and turn["actor"] != state["seat"]))
                require(len(state.get("pending_requests", [])) == int(needs_request),
                        "invalid_message", "snapshot pending requests do not match this seat's decision state")
            for request in state.get("pending_requests", []):
                rid = request["request_id"]
                require(rid not in self.terminal_acks, "invalid_message", "snapshot reopened a terminal request")
                require(request["seat"] == state["seat"], "invalid_message", "snapshot carries another seat's request")
                require(turn is not None and request["caused_by_seq"] == turn["last_event_seq"] < message["replaces_through_seq"],
                        "invalid_message", "snapshot request owner/cause differs")
                self.validate("pending-request", request)
                self.validate("decision-cause", {"request": request, "cause": turn["last_event"]})
                grace = self.welcome["rules"]["time_control"]["grace_ms"]
                deadline = grace + request["timeout_ms"] + request["time_bank_ms"]
                check_snapshot_clock(request, grace)
                selection = request["selection"]
                if selection is not None:
                    require(selection["action_id"] in {c["action_id"] for c in request["legal_actions"]}, "invalid_message", "snapshot selection is not legal")
                    require(selection["source"] != "default" or selection["action_id"] == request["default_action_id"], "invalid_message", "snapshot default selection differs")
                    require(selection["time_bank_ms"] <= request["time_bank_ms"], "invalid_message", "snapshot selection invents bank time")
                    require(selection["time_bank_ms"] == state["time_bank_ms"], "invalid_message", "selected time bank differs from the shared balance")
                    check_clock(request, selection, grace, user=selection["source"] == "user",
                                timeout=selection["source"] == "default" and self.welcome["rules"]["invalid_action_policy"] != "default")
                else:
                    require(request["time_bank_ms"] == state["time_bank_ms"], "invalid_message", "open request changed the shared balance")
                observed_elapsed = selection["elapsed_ms"] if selection is not None else deadline - request["remaining_ms"]
                require(observed_elapsed >= self.request_clock_floor.get(rid, 0), "invalid_message",
                        "snapshot clock precedes an observed request clock")
                self.request_clock_floor[rid] = observed_elapsed
                if request["request_id"] in self.requests:
                    old = self.requests[request["request_id"]]
                    immutable = ("seat", "caused_by_seq", "timeout_ms", "time_bank_ms", "legal_actions", "default_action_id", "decision_group_id", "decision_group_members", "decision_group_deadline_ms", "decision_group_close")
                    require(all(request.get(key) == old.get(key) for key in immutable), "invalid_message", "snapshot changed an issued request")
                    if "remaining_ms" in old:
                        require(request["remaining_ms"] <= old["remaining_ms"], "invalid_message",
                                "snapshot increased the remaining request time")
                        if "decision_group_remaining_ms" in old:
                            require(request["decision_group_remaining_ms"] <= old["decision_group_remaining_ms"],
                                    "invalid_message", "snapshot increased the remaining group time")
                        if selection is not None:
                            check_clock(old, selection, grace, user=selection["source"] == "user")
                    if old.get("selection") is not None:
                        require(selection is not None and all(selection[k] == old["selection"][k] for k in ("action_id", "source", "elapsed_ms", "time_bank_ms")),
                                "invalid_message", "snapshot changed a frozen selection")
                self.requests[request["request_id"]] = deepcopy(request)
            self.active_requests = {r["request_id"] for r in state.get("pending_requests", [])}
            self.awaiting_request = False
            self.request_ids |= self.active_requests
            self.expected_effects = []  # A jump can cover the entire result transaction.
            self.unadopted_reaction = None
            if self.recovery == "initial":
                self.recovery = None
        else:
            if seq != self.applied + 1:
                self.gap_received = max(self.gap_received, seq)
                self.recovery = "gap"
                return "sequence_gap"
            require(self.started or kind == "event" and message["event"]["type"] == "start_game"
                    or kind == "error" and message["severity"] == "fatal",
                    "invalid_message", "nonfatal message precedes session initialization")
            require(not (self.expected_effects or self.unadopted_reaction) or kind == "event" or (kind == "error" and message["severity"] == "fatal"),
                    "invalid_message", "message interrupts acknowledged action effects")
            if kind == "event":
                event = message["event"]
                self.validate("visible-event", {"event":event,"mode":mode,"view":self.welcome["view"],"seat":self.welcome["seat"]})
                require(not self.ended, "invalid_message", "game event after end_game")
                if mode == "replay":
                    require(type(message.get("original_seq")) is int and message["original_seq"] > self.original_seq, "invalid_message", "original_seq is not increasing")
                    self.original_seq = message["original_seq"]
                else:
                    require("original_seq" not in message, "invalid_message", "original_seq outside replay")
                if not self.started:
                    require(event["type"] == "start_game" and seq == 1, "invalid_message", "first game event is not start_game")
                    require(all(event[key] == self.welcome[key] for key in ("players", "rules")), "invalid_message", "start_game differs from welcome")
                    expected_scores = [self.welcome["rules"]["starting_points"]] * 4 if self.welcome["resumed"] else self.welcome["scores"]
                    require(event["scores"] == expected_scores, "invalid_message", "start_game initial scores differ")
                    self.started = True
                else:
                    require(event["type"] != "start_game", "invalid_message", "start_game repeated")
                require(not self.active_requests and not self.awaiting_request, "invalid_message", "state event precedes this seat's required decision and terminal ACK")
                self._check_effect(event)
                try:
                    self.game.apply(event)
                except (GameError, ScoringError) as error:
                    raise SessionError("invalid_message", str(error)) from error
                self.last_event_seq = seq
                self.event_seq_floor = seq
                bank_scope = self.welcome["rules"]["time_control"]["bank_scope"]
                if event["type"] == "start_game" or (event["type"] == "start_kyoku" and bank_scope == "kyoku"):
                    self.time_bank_ms = self.welcome["rules"]["time_control"]["bank_ms"]
                if self.game.round is not None and "self_state" in self.game.round:
                    self.game.round["self_state"]["time_bank_ms"] = self.time_bank_ms
                if mode == "play" and event["type"] in {"tsumo", "dahai", "ankan_declared", "kakan_declared"}:
                    own = event["actor"] == self.welcome["seat"]
                    self.awaiting_request = own if event["type"] == "tsumo" else not own
                if event["type"] in {"end_kyoku", "end_game"}:
                    require(not self.active_requests, "invalid_message", "round ended with unresolved requests")
                if event["type"] == "end_game":
                    self.ended = True
            elif kind == "request":
                require(self.started and not self.ended, "invalid_message", "request outside an active game")
                require(self.awaiting_request, "invalid_message", "no new decision is due for this seat")
                require(message["seat"] == self.welcome["seat"], "invalid_message", "request belongs to another seat")
                require(message["request_id"] not in self.request_ids and not self.active_requests, "invalid_message", "request id or seat is already in use")
                require(message["time_bank_ms"] == self.time_bank_ms, "invalid_message", "request changed the seat's remaining time bank")
                require(message["caused_by_seq"] < seq, "invalid_message", "request cause is not a previous event")
                require(message["caused_by_seq"] == self.last_event_seq, "invalid_message",
                        "request does not refer to the current decision event's sequence")
                cause = self.decode(self.known[message["caused_by_seq"]]) if message["caused_by_seq"] in self.known else None
                require(cause is not None and cause["kind"] == "event", "invalid_message", "request does not refer to an applied event")
                self.validate("decision-cause", {"request":message,"cause":cause["event"]})
                require(cause["event"] == self.game.last_cause, "invalid_message", "request does not refer to the current decision event")
                self.request_ids.add(message["request_id"])
                self.active_requests.add(message["request_id"])
                self.requests[message["request_id"]] = deepcopy(message)
                self.awaiting_request = False
            elif kind == "ack":
                rid, status = message["request_id"], message["status"]
                if rid not in self.active_requests:
                    require(status == "stale" and (rid in self.request_ids or self.floor > 0), "invalid_message", "ACK refers to an unissued or terminal request")
                    attempt = (rid, message["action_id"])
                    require(attempt not in self.late_attempts, "invalid_message",
                            "late attempt was assigned a second ACK sequence")
                    if rid in self.terminal_acks:
                        require(self.terminal_acks[rid]["status"] in {"defaulted", "stale"},
                                "invalid_message", "late stale ACK follows an explicit terminal selection")
                        require(all(message[key] == self.terminal_acks[rid][key] for key in ("elapsed_ms", "time_bank_ms")), "invalid_message", "late ACK changed the original clock")
                    self.late_attempts.add(attempt)
                else:
                    request = self.requests[rid]
                    grace = self.welcome["rules"]["time_control"]["grace_ms"]
                    require(message["elapsed_ms"] >= self.request_clock_floor.get(rid, 0), "invalid_message",
                            "ACK clock precedes an observed request clock")
                    check_clock(request, message, grace, user=status in {"accepted", "passed", "superseded", "rejected"},
                                timeout=status == "defaulted" and self.welcome["rules"]["invalid_action_policy"] != "default")
                    self.request_clock_floor[rid] = message["elapsed_ms"]
                    remaining = message["time_bank_ms"]
                    selection = request.get("selection")
                    if selection is not None:
                        require(status != "rejected" and all(message[k] == selection[k] for k in ("action_id","elapsed_ms","time_bank_ms")), "invalid_message", "ACK changed snapshot's frozen selection")
                        require(status == "stale" or (status == "defaulted") == (selection["source"] == "default"),
                                "invalid_message", "ACK changed snapshot's selection source")
                    candidates = {c["action_id"]: c["action"] for c in request["legal_actions"]}
                    aid = message["action_id"]
                    if status == "rejected":
                        require(self.welcome["rules"]["invalid_action_policy"] != "default", "invalid_message", "default policy cannot send a rejected ACK")
                        require(aid not in candidates, "invalid_message", "legal candidate was rejected as malformed")
                    else:
                        require(aid in candidates, "invalid_message", "ACK selected an unissued candidate")
                        require(status != "defaulted" or aid == request["default_action_id"], "invalid_message", "default ACK differs from the declared default")
                        require(status != "passed" or candidates[aid]["type"] == "none", "invalid_message", "passed ACK is not a pass")
                        require(status not in {"accepted", "superseded"} or candidates[aid]["type"] != "none", "invalid_message", "none has an invalid terminal status")
                        require(status != "superseded" or "decision_group_id" in request,
                                "invalid_message", "single decision cannot be superseded")
                    if status != "rejected":
                        self._expect_effects(request, message)
                        try:
                            self.game.acknowledge(request, message)
                        except (GameError, ScoringError) as error:
                            raise SessionError("invalid_message", str(error)) from error
                        self.active_requests.discard(rid)
                        self.time_bank_ms = remaining
                        self.terminal_acks[rid] = deepcopy(message)
                        if self.game.round is not None and "self_state" in self.game.round:
                            self.game.round["self_state"]["time_bank_ms"] = remaining
            elif kind == "error" and message["severity"] == "fatal":
                self.closed = True
        recovery_through = max(self.gap_received, self.through if self.resume_snapshot_allowed else 0)
        if self.recovery in {"resume", "gap"} and seq >= recovery_through:
            self.recovery = None
            self.gap_received = 0
        if seq > self.through:
            self.resume_snapshot_allowed = False
        self.applied = seq
        self.known[seq] = raw
        self.applications.append(seq)
        return "applied"


def replay_plan(history: list[bytes], expected: int, received: int, *, snapshot: bool = False) -> dict:
    require(1 <= expected < received <= len(history), "invalid_message", "gap request lies outside emitted history")
    if any(payload is None for payload in history[expected-1:]):
        require(snapshot, "resume_unavailable", "history is not retained")
        return {"strategy": "snapshot", "replaces_through_seq": len(history), "seq": len(history)+1}
    return {"strategy": "replay", "from": expected, "through": len(history), "payloads": history[expected-1:].copy()}


def classify_player_input(message: dict, context: dict, validate: Callable[[str, dict], None]) -> dict:
    if isinstance(message, dict) and message.get("kind") == "action":
        for old in context.get("retired_sessions", []):
            if all(message.get(key) == old[key] for key in ("yamai", "session_id", "game_id")):
                try:
                    validate("player-application", message)
                    return {"code": "ignored", "severity": None}
                except SessionError:
                    break
    identity = context["identity"]
    if not isinstance(message, dict) or message.get("kind") != "action" or not all(message.get(key) == value for key, value in identity.items()):
        return {"code": "invalid_message", "severity": "fatal"}
    known = message.get("request_id") in context["known_request_ids"] if isinstance(message.get("request_id"), str) else False
    try:
        validate("player-application", message)
    except SessionError:
        return {"code": "invalid_message", "severity": "recoverable" if known else "fatal"}
    if context.get("game_ended", False):
        return {"code": "ignored", "severity": None}
    return {"code": "valid" if known else "invalid_action", "severity": None if known else "recoverable"}


def resource_trace(trace: dict) -> list[dict]:
    """Resource timers use a monotonic, integer-ms test clock."""
    pending_frame = None
    frame_bytes = 0
    waiting_join = trace.get("join_wait_started_ms")
    waiting_hello = trace.get("hello_wait_started_ms")
    waiting_welcome = trace.get("welcome_wait_started_ms")
    backlog_bytes = trace.get("backlog_bytes", 0)
    backlog_messages = trace.get("backlog_messages", 0)
    require(type(backlog_bytes) is int and type(backlog_messages) is int and backlog_bytes >= 0 and backlog_messages >= 0, "invalid_message", "invalid initial backlog")
    require(backlog_bytes <= 8388608 and backlog_messages <= 1024, "resource_limit", "initial backlog exceeds limits")
    stalled_since = 0 if backlog_bytes == 8388608 or backlog_messages == 1024 else None
    unresolved = 0
    reserved_bytes = 0
    reserved_messages = 0
    event_count = trace.get("event_count", 0)
    require(type(event_count) is int and event_count >= 0, "invalid_message", "invalid event count")
    require(event_count <= 100000, "resource_limit", "game event count exceeded")
    now = 0
    result = []
    for step in trace["steps"]:
        require(type(step["at_ms"]) is int and step["at_ms"] >= now, "invalid_message", "test clock moved backwards")
        now = step["at_ms"]
        for field in ("bytes", "messages", "reserved_bytes", "reserved_messages"):
            if field in step:
                require(type(step[field]) is int and step[field] >= 0, "invalid_message", "resource amounts must be nonnegative integers")
        expired = any(start is not None and now >= start + 60000 for start in (pending_frame, waiting_join, waiting_hello, waiting_welcome, stalled_since))
        require(not expired, "resource_limit", "resource or handshake deadline reached")
        op = step["op"]
        if op == "chunk":
            if pending_frame is None:
                pending_frame = now
            frame_bytes += step["bytes"]
            require(frame_bytes <= trace.get("max_message_bytes", 1048576), "resource_limit", "frame payload limit exceeded")
        elif op == "frame_complete":
            require(pending_frame is not None, "invalid_message", "no frame to complete")
            pending_frame, frame_bytes = None, 0
        elif op == "hello":
            waiting_hello, waiting_join = None, now
        elif op == "join":
            waiting_join = None
            waiting_welcome = now
        elif op == "welcome":
            waiting_welcome = None
        elif op == "enqueue":
            uses_reservation = step.get("uses_reservation", False)
            if uses_reservation:
                require(reserved_messages > 0 and 0 <= step["bytes"] <= reserved_bytes, "invalid_message", "message exceeds its reservation")
            unavailable = backlog_bytes + step["bytes"] + (0 if uses_reservation else reserved_bytes) > 8388608 or backlog_messages + 1 + (0 if uses_reservation else reserved_messages) > 1024
            if unavailable:
                stalled_since = now if stalled_since is None else stalled_since
                result.append({"at_ms":now,"outcome":"backpressure","unresolved":unresolved})
                continue
            backlog_bytes += step["bytes"]
            backlog_messages += 1
            if uses_reservation:
                reserved_bytes -= step["bytes"]
                reserved_messages -= 1
                if reserved_messages == 0:
                    reserved_bytes = 0
            if backlog_bytes == 8388608 or backlog_messages == 1024:
                stalled_since = now if stalled_since is None else stalled_since
        elif op == "drain":
            require(step["bytes"] <= backlog_bytes and step["messages"] <= backlog_messages, "invalid_message", "drained more than queued")
            backlog_bytes -= step["bytes"]
            backlog_messages -= step["messages"]
            if step["bytes"] or step["messages"]:
                stalled_since = now if backlog_bytes == 8388608 or backlog_messages == 1024 else None
        elif op == "open_request":
            require(unresolved == 0 and reserved_messages == 0, "resource_limit", "duplicate unresolved request or undelivered result at one seat")
            require(backlog_bytes + step["reserved_bytes"] <= 8388608 and backlog_messages + step["reserved_messages"] <= 1024, "resource_limit", "request output cannot be reserved")
            unresolved = 1
            reserved_bytes, reserved_messages = step["reserved_bytes"], step["reserved_messages"]
        elif op == "terminalize":
            require(unresolved == 1, "invalid_message", "no open request")
            unresolved = 0
        elif op == "event":
            require(event_count < 100000, "resource_limit", "game event count exceeded")
            event_count += 1
        elif op in {"replay_event", "snapshot"}:
            pass
        elif op != "tick":
            raise SessionError("invalid_message", "unknown resource test operation")
        result.append({"at_ms":now,"outcome":"ok","unresolved":unresolved})
    return result
