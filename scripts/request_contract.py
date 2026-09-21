"""Executable selection/ACK contract for YAMAI specification sections 8 and 9.

The caller validates request shapes. This finite trace evaluator deliberately
does not decide mahjong legality, score hands, serialize wire frames, or model
transport delivery. Inputs use integer microseconds to exercise the strict
deadline before millisecond rounding. Observations are per request, so tests
do not impose an order on messages addressed to different sessions.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def evaluate(trace: dict[str, Any]) -> list[dict[str, Any]]:
    grace = trace["grace_ms"]
    policy = trace["invalid_action_policy"]
    ron_policy = trace["ron_policy"]
    requests = {r["request_id"]: r for r in trace["requests"]}
    states = {rid: {"phase": "OPEN"} for rid in requests}
    messages: dict[str, list[dict[str, Any]]] = {rid: [] for rid in requests}
    late: dict[str, list[str]] = {rid: [] for rid in requests}
    seen_late: set[tuple[str, str]] = set()
    effects: list[dict[str, Any]] = []
    observations = []
    now = 0
    finished = False

    def timing(rid: str, at_us: int) -> dict[str, int]:
        r = requests[rid]
        deadline_ms = grace + r["timeout_ms"] + r["time_bank_ms"]
        elapsed = min(at_us // 1000, deadline_ms)
        consumed = min(max(0, elapsed - grace - r["timeout_ms"]), r["time_bank_ms"])
        return {"elapsed_ms": elapsed, "time_bank_ms": r["time_bank_ms"] - consumed}

    def select(rid: str, aid: str, source: str, at_us: int) -> None:
        states[rid] = {"phase": "SELECTED", "action_id": aid, "source": source, **timing(rid, at_us)}

    def deadlines() -> None:
        for rid, r in requests.items():
            deadline_us = (grace + r["timeout_ms"] + r["time_bank_ms"]) * 1000
            if states[rid]["phase"] == "OPEN" and now >= deadline_us:
                select(rid, r["default_action_id"], "default", deadline_us)

    def ack(rid: str, aid: str, status: str, clock: dict[str, Any]) -> None:
        messages[rid].append({
            "kind": "ack", "action_id": aid, "status": status,
            "elapsed_ms": clock["elapsed_ms"], "time_bank_ms": clock["time_bank_ms"],
        })

    def flush_late(rid: str) -> None:
        for aid in late[rid]:
            ack(rid, aid, "stale", states[rid])
        late[rid].clear()

    def terminal(rid: str, status: str) -> None:
        states[rid].update(phase="TERMINAL", status=status)
        ack(rid, states[rid]["action_id"], status, states[rid])

    def action_type(rid: str) -> str:
        aid = states[rid]["action_id"]
        return next(c["action"]["type"] for c in requests[rid]["legal_actions"] if c["action_id"] == aid)

    deadlines()
    for step in trace["steps"]:
        at_us = step["at_us"]
        if type(at_us) is not int or at_us < now:
            raise ValueError("trace time must be nondecreasing integer microseconds")
        now = at_us
        deadlines()
        op = step["op"]
        if op == "submit":
            rid, aid = step["request_id"], step["action_id"]
            if rid not in requests:
                messages.setdefault(rid, []).append({"kind": "error", "code": "invalid_action", "action_id": aid})
            else:
                s = states[rid]
                if s["phase"] != "OPEN" and (s["source"] != "user" or s.get("status") == "stale"):
                    if (rid, aid) not in seen_late:
                        seen_late.add((rid, aid))
                        late[rid].append(aid)
                        if s["phase"] == "TERMINAL":
                            flush_late(rid)
                elif s["phase"] != "OPEN":
                    if s["action_id"] != aid:
                        error = {"kind": "error", "code": "request_conflict", "action_id": aid}
                        if "status" in s:
                            error["original_status"] = s["status"]
                        messages[rid].append(error)
                elif aid in {c["action_id"] for c in requests[rid]["legal_actions"]}:
                    select(rid, aid, "user", now)
                elif policy == "default":
                    select(rid, requests[rid]["default_action_id"], "default", now)
                else:
                    ack(rid, aid, "rejected", timing(rid, now))
                    if policy == "reject":
                        messages[rid].append({"kind": "error", "code": "invalid_action", "action_id": aid})
                    else:
                        for other in requests:
                            if states[other]["phase"] == "OPEN":
                                select(other, requests[other]["default_action_id"], "cancelled", now)
                            if states[other]["phase"] != "TERMINAL":
                                terminal(other, "stale")
                        effects.append({"type": "penalty", "offender": requests[rid]["seat"]})
                        finished = True
                        for other in requests:
                            flush_late(other)
        elif op == "resolve":
            if finished or any(s["phase"] != "SELECTED" for s in states.values()):
                raise ValueError("resolution requires all selections and occurs once")
            types = {rid: action_type(rid) for rid in requests}
            hours = [rid for rid in requests if types[rid] == "hora"]
            distance = lambda rid: (requests[rid]["seat"] - trace["target"] + 4) % 4
            if hours:
                if ron_policy == "double_only" and len(hours) == 3:
                    chosen, effect = [], "sanchaho"
                else:
                    chosen = [min(hours, key=distance)] if ron_policy == "head_bump" else hours
                    effect = "hora"
            else:
                calls = [rid for rid in requests if types[rid] in {"pon", "daiminkan"}]
                chis = [rid for rid in requests if types[rid] == "chi"]
                if calls or chis:
                    chosen = [min(calls or chis, key=distance)]
                else:
                    chosen = [rid for rid in requests if types[rid] != "none"]
                effect = types[chosen[0]] if chosen else "continue"
            for rid in requests:
                if states[rid]["source"] == "default":
                    status = "defaulted"
                elif types[rid] == "none":
                    status = "passed"
                else:
                    status = "accepted" if rid in chosen else "superseded"
                terminal(rid, status)
            effects.append({"type": effect, "actors": sorted(requests[rid]["seat"] for rid in chosen)})
            finished = True
            for rid in requests:
                flush_late(rid)
        elif op != "advance":
            raise ValueError("unsupported trace operation")
        phase = "TERMINAL" if finished else "CLOSED" if all(s["phase"] != "OPEN" for s in states.values()) else "OPEN"
        observations.append(deepcopy({"phase": phase, "requests": states, "messages": messages, "effects": effects}))
    return observations
