"""Executable negotiation, recovery and resource contracts for YRC 0003.

The schema/JSON callbacks are supplied by the artifact validator. These
contracts do not implement a mahjong game or an authorization service.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable
from game_contract import EventState, GameError
from scoring_reference import ScoringError


class SessionError(ValueError):
    def __init__(self, code: str, message: str, severity: str = "fatal"):
        super().__init__(message)
        self.code = code
        self.severity = severity


def require(condition: bool, code: str, message: str, severity: str = "fatal") -> None:
    if not condition:
        raise SessionError(code, message, severity)


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
    limits = {"max_message_bytes": 1048576, "max_json_depth": 64, "max_unresolved_requests": 4}
    require(all(hello["receive_limits"][key] == value and join["receive_limits"][key] == value for key, value in limits.items()), "unsupported_limit", "profile receive limits cannot be met")
    if join["mode"] == "spectate" and context.get("game_started", False):
        require("snapshot" in enabled, "unsupported_capability", "mid-game spectate needs snapshot")
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
            require(welcome["scores"] == [welcome["rules"]["starting_points"]] * 4, "invalid_message", "new game scores differ from rules")
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
        self.terminal_acks: dict[str, dict] = {}
        self.time_bank_ms = welcome["rules"]["time_control"]["bank_ms"]
        self.original_seq = 0
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

    def _receive(self, raw: bytes) -> str:
        message = self.decode(raw)
        require(isinstance(message, dict), "invalid_message", "message is not an object")
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
        if kind == "snapshot":
            require("snapshot" in self.welcome["capabilities"], "invalid_message", "snapshot capability is not enabled")
            contiguous = seq == self.applied + 1
            # A retained snapshot is an ordinary ledger entry. Its old prefix
            # need not cover the current recovery frontier, and applying it
            # must not end replay early. Only a jump needs recovery authority.
            require((contiguous and self.started) or self.recovery is not None or self.resume_snapshot_allowed, "invalid_message", "snapshot lacks bootstrap or recovery authorization")
            required_floor = self.applied if contiguous else max(self.applied, self.gap_received, self.through if self.resume_snapshot_allowed else 0)
            require(message["replaces_through_seq"] >= required_floor and seq == message["replaces_through_seq"] + 1, "invalid_message", "snapshot replacement range is insufficient")
            state = message["state"]
            require(all(state[key] == self.welcome[key] for key in ("mode", "view", "seat", "players")), "invalid_message", "snapshot changed session identity")
            require(not self.ended or state["game_phase"] == "ended", "invalid_message", "snapshot reopened an ended game")
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
            for request in state.get("pending_requests", []):
                if request["request_id"] in self.requests:
                    old = self.requests[request["request_id"]]
                    immutable = ("seat", "caused_by_seq", "timeout_ms", "time_bank_ms", "legal_actions", "default_action_id", "decision_group_id", "decision_group_members", "decision_group_deadline_ms", "decision_group_close")
                    require(all(request.get(key) == old.get(key) for key in immutable), "invalid_message", "snapshot changed an issued request")
                self.requests[request["request_id"]] = deepcopy(request)
            self.active_requests = {r["request_id"] for r in state.get("pending_requests", [])}
            self.awaiting_request = False
            self.request_ids |= self.active_requests
            if self.recovery == "initial":
                self.recovery = None
        else:
            if seq != self.applied + 1:
                self.gap_received = max(self.gap_received, seq)
                self.recovery = "gap"
                return "sequence_gap"
            mode = self.welcome["mode"]
            require(mode == "play" or kind not in {"request", "ack"}, "invalid_message", "observer received a request or ACK")
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
                try:
                    self.game.apply(event)
                except (GameError, ScoringError) as error:
                    raise SessionError("invalid_message", str(error)) from error
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
                    if rid in self.terminal_acks:
                        require(all(message[key] == self.terminal_acks[rid][key] for key in ("elapsed_ms", "time_bank_ms")), "invalid_message", "late ACK changed the original clock")
                else:
                    request = self.requests[rid]
                    grace = self.welcome["rules"]["time_control"]["grace_ms"]
                    elapsed, bank = message["elapsed_ms"], request["time_bank_ms"]
                    require(elapsed <= grace + request["timeout_ms"] + bank, "invalid_message", "ACK exceeds original deadline")
                    remaining = bank - min(max(0, elapsed - grace - request["timeout_ms"]), bank)
                    require(message["time_bank_ms"] == remaining, "invalid_message", "ACK time-bank arithmetic differs")
                    selection = request.get("selection")
                    if selection is not None:
                        require(status != "rejected" and all(message[k] == selection[k] for k in ("action_id","elapsed_ms","time_bank_ms")), "invalid_message", "ACK changed snapshot's frozen selection")
                    candidates = {c["action_id"]: c["action"] for c in request["legal_actions"]}
                    aid = message["action_id"]
                    if status == "rejected":
                        require(aid not in candidates, "invalid_message", "legal candidate was rejected as malformed")
                    elif status != "stale":
                        require(aid in candidates, "invalid_message", "ACK selected an unissued candidate")
                        require(status != "defaulted" or aid == request["default_action_id"], "invalid_message", "default ACK differs from the declared default")
                        require(status != "passed" or candidates[aid]["type"] == "none", "invalid_message", "passed ACK is not a pass")
                        require(status not in {"accepted", "superseded"} or candidates[aid]["type"] != "none", "invalid_message", "none has an invalid terminal status")
                    if status != "rejected":
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
