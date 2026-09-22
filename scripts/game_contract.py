"""Pure riichi-4p decision and round-boundary reference contracts.

Inputs are complete decision positions or already settled round results.
This is not a transport server, wall shuffler, or a second scoring engine.
No expected observation, fixture ID, or supplied legal-action list is read.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from itertools import combinations
import json
from typing import Any

from scoring_reference import (
    Meld, ORPHANS, TILES, ScoringError, hand_parts, inventory,
    score_hand, shapes, tile_index, waits, pao_assignments,
    basic_points, normal_payments, settle_win, validate_win_declarations,
    YAKU_MIN_SEQUENCES, YAKU_MIN_TRIPLETS,
)


class GameError(ValueError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise GameError(message)


def rankings(scores: list[int]) -> list[int]:
    order = sorted(range(4), key=lambda seat: (-scores[seat], seat))
    return [order.index(seat) + 1 for seat in range(4)]


def round_coordinates_reachable(coords: dict, rules: dict) -> bool:
    """Necessary coordinate bounds without reconstructing unseen round results."""
    extra = coords["extension_round"]
    scheduled_winds = 1 if rules["game_length"] == "tonpu" else 2
    if (coords["bakaze"] not in {"E", "S", "W", "N"}
            or not 1 <= coords["kyoku"] <= 4 or coords["oya"] != coords["kyoku"] - 1
            or not 0 <= extra <= rules["extension"]["max_extra_rounds"]):
        return False
    coordinate = "ESWN".index(coords["bakaze"]) * 4 + coords["kyoku"] - 1
    if extra == 0:
        return coordinate < scheduled_winds * 4
    # Each extra deal advances by either zero (renchan/penalty) or one
    # coordinate; winds wrap after 16 rotations, without resetting extra.
    return (rules["extension"]["mode"] == "sudden_death"
            and (coordinate - scheduled_winds * 4) % 16 <= extra - 1)


def check_snapshot_rinshan(kyoku: dict, rules: dict | None = None) -> None:
    """Check facts visible in a snapshot, preserving original pon ordering."""
    turn = kyoku["turn"]
    melds = kyoku["melds"][turn["actor"]]
    # Kakan updates its original pon in place. Any kakan, or a newly
    # appended ankan/daiminkan, may therefore be the most recent kan.
    possible_kans = {m["type"] for i, m in enumerate(melds)
                     if m["type"] == "kakan" or i == len(melds) - 1 and m["type"] in {"ankan", "daiminkan"}}
    rinshan_decision = turn["last_event"]["type"] == "tsumo" and turn["phase"] in {"awaiting_action", "resolving"}
    pending = kyoku["pending_dora"]
    require(pending is None or (pending["timing"] == "after_rinshan_discard" and rinshan_decision),
            "deferred dora marker survives outside the rinshan decision")
    require(pending is None or (kyoku["rinshan"] and pending["kan_type"] in possible_kans),
            "deferred dora marker lacks its committed kan")
    require(not kyoku["haitei"] or kyoku["wall_remaining"] == 0,
            "last-tile flag without an exhausted live wall")
    require(not (kyoku["rinshan"] and kyoku["haitei"]), "rinshan and last-live-tile flags are mutually exclusive")
    require(not kyoku["rinshan"] or (possible_kans and (rinshan_decision or kyoku["pending_kan"] is not None)),
            "rinshan draw pending without a committed kan or its decision window")
    if rules is not None and kyoku["rinshan"] and rinshan_decision:
        if pending is not None:
            valid_timing = rules["kan_dora_timing"][pending["kan_type"]] == "after_rinshan_discard"
        else:
            valid_timing = any(rules["kan_dora_timing"][kind] == "before_rinshan" for kind in possible_kans)
        require(valid_timing, "deferred dora marker missing for the committed kan or timing differs")


def next_kyoku(current: dict, result: dict, scores: list[int], kyotaku: int, rules: dict) -> dict:
    """YAMAI specification §7.2, after settlement and before any next deal."""
    extra = current["extension_round"]
    extension = rules["extension"]
    oya = current["oya"]
    require(0 <= extra <= extension["max_extra_rounds"], "invalid extension counter")
    require(current["bakaze"] in "ESWN" and 1 <= current["kyoku"] <= 4, "invalid round coordinate")
    require(oya == current["kyoku"] - 1, "dealer differs from absolute initial seat order")
    last_wind = "E" if rules["game_length"] == "tonpu" else "S"
    if extra == 0:
        require("ESWN".index(current["bakaze"]) <= "ESWN".index(last_wind), "ordinary round beyond scheduled length")
    end = {"type": "end_game", "kyotaku": kyotaku}
    if rules["bankruptcy"] == "end_game" and min(scores) < rules["bankruptcy_threshold"]:
        return end
    if extra > 0 and (max(scores) >= extension["target_points"] or extra >= extension["max_extra_rounds"]):
        return end
    kind = result["type"]
    require(kind in {"hora", "ryukyoku", "penalty"}, "unknown settled result")
    dealer_win = kind == "hora" and any(w["actor"] == oya for w in result["wins"])
    if kind == "penalty":
        continues = True
    elif kind == "hora":
        continues = dealer_win and rules["dealer_continuation"]["win"]
    elif result["reason"] == "fanpai":
        continues = result["tenpai"][oya] and rules["dealer_continuation"]["tenpai_draw"]
    else:
        continues = rules["abortive_draw_continuation"]
    last = extra == 0 and current["bakaze"] == last_wind and current["kyoku"] == 4
    if last and kind != "penalty":
        if continues:
            if dealer_win and rules["agariyame"] and rankings(scores)[oya] == 1 and scores[oya] >= extension["target_points"]:
                return end
        elif max(scores) >= extension["target_points"] or extension["mode"] == "none" or extension["max_extra_rounds"] == 0:
            return end
    new_extra = extra + 1 if extra > 0 or (last and not continues) else 0
    honba = current["honba"] if kind == "penalty" else current["honba"] + 1 if kind == "ryukyoku" or continues else 0
    nxt = {**current, "type": "renchan" if continues else "rotate", "honba": honba, "kyotaku": kyotaku, "extension_round": new_extra}
    if not continues:
        nxt["oya"] = (oya + 1) % 4
        nxt["kyoku"] = current["kyoku"] % 4 + 1
        if current["kyoku"] == 4:
            nxt["bakaze"] = "ESWN"[("ESWN".index(current["bakaze"]) + 1) % 4]
    return {key: nxt[key] for key in ("type", "bakaze", "kyoku", "oya", "honba", "kyotaku", "extension_round")}


def kuikae(kind: str, pai: str, consumed: list[str]) -> set[int]:
    forbidden = {tile_index(pai)}
    if kind == "chi":
        pair = sorted(tile_index(t) for t in consumed)
        for tile in range(27):
            tiles = sorted([*pair, tile])
            if tiles[0] // 9 == tiles[2] // 9 and tiles == list(range(tiles[0], tiles[0] + 3)):
                forbidden.add(tile)
    return forbidden


def canonical_action(action: dict) -> str:
    """Ignore annotation fields and consumed order; bind optional none actor."""
    def project(value: Any) -> Any:
        if isinstance(value, dict):
            out = {k: project(v) for k, v in value.items() if not k.startswith("x_")}
            if "consumed" in out:
                out["consumed"] = sorted(out["consumed"])
            if out.get("type") == "none":
                out.pop("actor", None)
            return out
        if isinstance(value, list):
            return [project(x) for x in value]
        return value
    return json.dumps(project(action), sort_keys=True, separators=(",", ":"))


def scoring_melds(row: list[dict]) -> list[dict]:
    return [{"kind": m["type"], "open": m["type"] != "ankan",
             "tiles": [*m["consumed"], *([m["pai"]] if m["type"] != "ankan" else [])],
             **({"source": m["target"]} if m["type"] != "ankan" else {})} for m in row]


def public_pao(melds: list[list[dict]], rules: dict) -> list[dict]:
    result = []
    for actor, row in enumerate(melds):
        hand = {"melds": scoring_melds(row)}
        result.extend({"actor":actor,"yaku_id":yaku,"liable_seat":seat} for yaku,seat in pao_assignments(hand,rules).items())
    return sorted(result,key=lambda p:(p["actor"],p["yaku_id"]))


def check_snapshot_public_history(kyoku: dict) -> None:
    """Match called discard occurrences, and preserve the riichi contract."""
    called = Counter((target, tile["called_by"], tile["pai"])
                     for target, river in enumerate(kyoku["rivers"])
                     for tile in river if tile["called_by"] is not None)
    claimed = Counter((meld["target"], actor, meld["pai"])
                      for actor, row in enumerate(kyoku["melds"])
                      for meld in row if meld["type"] != "ankan")
    require(called == claimed, "called discard occurrences do not match committed melds")
    for seat, reach in enumerate(kyoku["reach_status"]):
        marks = sum(tile["reach"] for tile in kyoku["rivers"][seat])
        require(marks == int(reach["state"] != "none"), "riichi must have exactly one declaration discard")
        require(reach["state"] == "none" or all(m["type"] == "ankan" for m in kyoku["melds"][seat]),
                "riichi snapshot contains an open meld")


def known_round_tiles(kyoku: dict) -> list[str]:
    """One entry per observed physical tile, including a pending declaration."""
    known = [t for hand in kyoku["hands"] for t in hand.get("tiles", [])]
    known.extend(t["pai"] for river in kyoku["rivers"] for t in river if t["called_by"] is None)
    for row in kyoku["melds"]:
        for meld in row:
            known.extend(meld["consumed"])
            if meld["type"] != "ankan":
                known.append(meld["pai"])
    known.extend(kyoku["dora_markers"])
    pending = kyoku["pending_kan"]
    if pending is not None and "tiles" not in kyoku["hands"][pending["actor"]]:
        # Ankan's four tiles, or kakan's added tile, still belong to the
        # concealed hand. Kakan's original pon is already counted above.
        known.extend(pending["consumed"] if pending["type"] == "ankan_declared" else [pending["pai"]])
    return known


def check_hora_payments(wins: list[dict], kyoku: dict, rules: dict) -> None:
    """Recompute public amounts without assuming access to hidden hands."""
    nearest = min(wins, key=lambda w: (w["actor"] - w["target"]) % 4)["actor"]
    total_credit = kyoku["kyotaku"] * rules["riichi_stick_value"]
    share = total_credit // (len(wins) * 100) * 100 if rules["multiple_ron_settlement"]["kyotaku"] == "equal_split" else 0
    credits = {win["actor"]: share for win in wins}
    credits[nearest] += total_credit - sum(credits.values())
    for win in wins:
        score = {k: win[k] for k in ("fu", "han", "yakus", "bonuses", "hand_points")}
        score["basic_points"] = basic_points(win["fu"], win["han"],
            sum(y["value"] for y in win["yakus"] if y["unit"] == "yakuman"), rules)
        require(win["hand_points"] == sum(normal_payments(score["basic_points"], win["actor"], win["target"], kyoku["oya"]).values()),
                "hand points differ from public fu/han/yakuman")
        data = {"actor": win["actor"], "target": win["target"], "winning_tile": win["pai"],
                "ura_dora_markers": win["ura_dora_markers"],
                "hand": {"melds": scoring_melds(kyoku["melds"][win["actor"]])}}
        honba = kyoku["honba"] if len(wins) == 1 or rules["multiple_ron_settlement"]["honba"] == "each_winner" or win["actor"] == nearest else 0
        result = settle_win(data, kyoku, score, rules, honba, credits[win["actor"]])
        require(win["pao"] == result["pao"] and win["deltas"] == result["deltas"],
                "win deltas differ from public payments, honba or deposits")


def check_hora_yaku_context(win: dict, kyoku: dict, cause: dict, rules: dict) -> None:
    """Reject role claims contradicted by public calls, reach and win source."""
    actor = win["actor"]
    melds = kyoku["melds"][actor]
    closed = all(m["type"] == "ankan" for m in melds)
    validate_win_declarations(win, rules, closed=closed)
    ids = {y["id"] for y in win["yakus"]}
    yakuman = any(y["unit"] == "yakuman" for y in win["yakus"])
    quads = sum(m["type"] in {"ankan", "daiminkan", "kakan"} for m in melds)
    require(("suukantsu" in ids) == (quads == 4),
            "four-quads yakuman differs from the committed melds")
    if not yakuman:
        require(("sankantsu" in ids) == (quads == 3),
                "three-quads yaku differs from the committed melds")
        require("sanankou" not in ids or sum(m["type"] != "ankan" for m in melds) <= 1,
                "three concealed triplets conflict with public open melds")
    sequences = sum(m["type"] == "chi" for m in melds)
    triplets = len(melds) - sequences
    require(all(YAKU_MIN_SEQUENCES.get(name, 0) <= 4 - triplets
                and YAKU_MIN_TRIPLETS.get(name, 0) <= 4 - sequences for name in ids),
            "yaku shape conflicts with the committed melds")
    require(not ids & {"honroutou", "tsuuiisou", "chinroutou"} or sequences == 0,
            "all terminals or honors cannot contain a public sequence")
    tsumo = win["actor"] == win["target"]
    reach = kyoku["reach_status"][actor]
    accepted = reach["state"] == "accepted"
    first_draw = tsumo and kyoku["first_turn_eligible"][actor]
    require(("tenhou" in ids) == (first_draw and actor == kyoku["oya"])
            and ("chiihou" in ids) == (first_draw and actor != kyoku["oya"]),
            "first-draw yakuman differs from public history")
    require(not ids & {"kokushi_musou", "suuankou", "chuuren_poutou", "tenhou", "chiihou"} or closed,
            "closed-only yakuman on an open hand")
    require(not ids & {"kokushi_musou", "chuuren_poutou", "chiitoitsu"} or not melds,
            "special hand contains a fixed meld")
    if not yakuman:
        expected = {
            "riichi": accepted and not reach["double"],
            "double_riichi": accepted and reach["double"],
            "ippatsu": accepted and reach["ippatsu"],
            "menzen_tsumo": closed and tsumo,
            "rinshan_kaihou": tsumo and kyoku["rinshan"],
            "chankan": cause["type"] in {"ankan_declared", "kakan_declared"},
            "haitei": tsumo and kyoku["haitei"],
            "houtei": not tsumo and kyoku["haitei"],
        }
        require(all((name in ids) == present for name, present in expected.items()),
                "situational yaku differ from public history")
    require(accepted or all(b["id"] != "uradora" for b in win["bonuses"]),
            "ura bonus without accepted reach")


def riichi_ankan(hand: dict, drawn: str, rules: dict) -> bool:
    """The draw is in hand. Every interpretation must preserve the triplet."""
    base = tile_index(drawn)
    tiles = hand["concealed_tiles"]
    if sum(tile_index(t) == base for t in tiles) != 4:
        return False
    before = deepcopy(hand)
    before["concealed_tiles"].remove(drawn)
    before_waits = waits(before, rules)
    consumed = [t for t in tiles if tile_index(t) == base]
    after = {"concealed_tiles": [t for t in tiles if tile_index(t) != base],
             "melds": [*deepcopy(hand["melds"]), {"kind": "ankan", "tiles": consumed, "open": False}]}
    if not before_waits or waits(after, rules) != before_waits:
        return False
    counts, fixed, _ = hand_parts(before, rules)
    for tile in before_waits:
        complete = list(counts)
        complete[tile] += 1
        forms = shapes(tuple(complete), fixed)
        if any(form != "standard" or Meld("triplet", base, False) not in groups for form, _, groups in forms):
            return False
    return True


def furiten(position: dict, rules: dict, hand: dict | None = None) -> dict:
    hand = position["hand"] if hand is None else hand
    waiting = waits(hand, rules)
    own_discards = {tile_index(t) for t in position["river"]}
    flags = {"discard": bool(waiting & own_discards),
             "temporary": position["temporary_furiten"], "riichi": position["riichi_furiten"]}
    return {"waits": [TILES[t] for t in sorted(waiting)], **flags, "ron_forbidden": any(flags.values())}


def furiten_step(position: dict, operation: dict, rules: dict) -> dict:
    p = deepcopy(position)
    kind = operation["type"]
    if kind == "draw":
        p["temporary_furiten"] = False
    elif kind == "reaction":
        # This is a shape check, even when hora was absent for lack of yaku.
        if tile_index(operation["pai"]) in waits(p["hand"], rules) and operation["selected"] != "hora":
            p["riichi_furiten" if p["reach_accepted"] else "temporary_furiten"] = True
    elif kind not in {"call", "discard"}:
        raise GameError("unknown furiten transition")
    return {"temporary_furiten": p["temporary_furiten"], "riichi_furiten": p["riichi_furiten"]}


def legal_actions(position: dict, rules: dict) -> list[dict]:
    """Generate every core choice from one complete player decision position.

    dora_markers includes the reserved deferred marker for this rinshan turn;
    it is host input, not a claim that a private marker has been published.
    The shape/yaku calculation never uses a caller's assertion of legality.
    """
    p, seat = position, position["seat"]
    require(not rules["local_yaku"], "core candidate reference requires no local-yaku handler")
    hand = deepcopy(p["hand"])
    turn = p["cause"]["type"] == "tsumo"
    cause = p["cause"]
    tiles, melds = hand["concealed_tiles"], hand["melds"]
    require(type(seat) is int and 0 <= seat < 4, "invalid decision seat")
    require(cause["type"] in {"tsumo", "dahai", "ankan_declared", "kakan_declared"}, "invalid decision cause")
    require((cause["actor"] == seat) == turn, "decision belongs to cause actor")
    require(len(tiles) + 3 * len(melds) == (14 if turn else 13), "wrong decision hand size")
    inventory([*tiles, *(t for m in melds for t in m["tiles"])], rules)
    before = deepcopy(hand)
    if turn:
        require(cause["pai"] in tiles, "drawn tile absent from hand")
        before["concealed_tiles"].remove(cause["pai"])
    hand_parts(before, rules, seat)
    own_quads = sum(m["kind"] in {"ankan", "daiminkan", "kakan"} for m in melds)
    require(p["kan_counts"][seat] == own_quads and sum(p["kan_counts"]) <= 4, "kan count differs from committed melds")
    require(not p["reach_accepted"] or all(m["kind"] == "ankan" for m in melds), "open hand accepted riichi")
    actions: list[dict] = []
    def add(kind: str, **fields: Any) -> None:
        actions.append({"type": kind, "actor": seat, **fields})
    def discards(concealed: list[str], drawn: str | None = None, forbidden: set[int] = frozenset()) -> list[dict]:
        remaining = list(concealed)
        result = []
        if drawn is not None:
            remaining.remove(drawn)
            result.append({"type": "dahai", "actor": seat, "pai": drawn, "tsumogiri": True})
        for tile in sorted(set(remaining)):
            if tile_index(tile) not in forbidden:
                result.append({"type": "dahai", "actor": seat, "pai": tile, "tsumogiri": False})
        return result
    win_tile = cause["consumed"][0] if cause["type"] == "ankan_declared" else cause["pai"]
    blocked = furiten(p, rules, before)["ron_forbidden"]
    if turn or not blocked:
        pending = None if cause["type"] in {"tsumo", "dahai"} else {
            "kind": cause["type"].removesuffix("_declared"), "actor": cause["actor"], "pai": win_tile}
        state = {k: p[k] for k in ("bakaze", "oya", "kyotaku", "wall_remaining", "kan_counts",
                                   "reach_accepted", "double_riichi", "ippatsu", "first_turn", "rinshan", "last_tile")}
        state.update(pending_kan=pending, furiten=blocked, events=[])
        data = {"actor": seat, "target": cause["actor"], "winning_tile": win_tile,
                "win_method": "tsumo" if turn else "ron", "hand": before,
                "dora_markers": p["dora_markers"], "ura_dora_markers": p["ura_dora_markers"]}
        try:
            score_hand(data, state, rules)
        except ScoringError as error:
            if error.code not in {"no_yaku", "invalid_hand", "invalid_context"}:
                raise
        else:
            add("hora")
    kan_ok = p["wall_remaining"] > 0 and sum(p["kan_counts"]) < 4
    fourth_abort = sum(p["kan_counts"]) == 4 and max(p["kan_counts"]) < 4 and "suukan_sanra" in rules["abortive_draws"]
    if not turn:
        add("none")
        if cause["type"] == "dahai" and not p["reach_accepted"] and p["wall_remaining"] > 0 and not fourth_abort:
            # A called discard is one physical tile, not a fresh copy per choice.
            inventory([*tiles, *(t for m in melds for t in m["tiles"]), win_tile], rules)
            base = tile_index(win_tile)
            for kind, count in (("chi", 2), ("pon", 2), ("daiminkan", 3)):
                if kind == "chi" and cause["actor"] != (seat + 3) % 4:
                    continue
                if kind == "daiminkan" and not kan_ok:
                    continue
                for consumed in sorted(set(tuple(sorted(c)) for c in combinations(tiles, count))):
                    indices = sorted([base, *(tile_index(t) for t in consumed)])
                    valid = (indices[0] < 27 and indices[0] // 9 == indices[-1] // 9 and indices == list(range(indices[0], indices[0] + 3))) if kind == "chi" else len(set(indices)) == 1
                    if not valid:
                        continue
                    fields = {"target": cause["actor"], "pai": win_tile, "consumed": list(consumed)}
                    if kind == "daiminkan":
                        add(kind, **fields)
                    else:
                        rest = list(tiles)
                        for tile in consumed:
                            rest.remove(tile)
                        for discard in discards(rest, forbidden=kuikae(kind, win_tile, list(consumed))):
                            add(kind, **fields, dahai=discard)
    else:
        drawn = cause["pai"]
        if p["reach_accepted"]:
            add("dahai", pai=drawn, tsumogiri=True)
        else:
            candidates = discards(tiles, drawn)
            actions.extend(candidates)
            if not fourth_abort:
                if all(m["kind"] == "ankan" for m in melds) and p["scores"][seat] >= rules["riichi_stick_value"] and p["wall_remaining"] >= 4:
                    for discard in candidates:
                        rest = deepcopy(hand)
                        rest["concealed_tiles"].remove(discard["pai"])
                        if waits(rest, rules):
                            add("reach", dahai=discard)
                if p["first_turn"] and "kyushukyuhai" in rules["abortive_draws"] and len({tile_index(t) for t in tiles} & ORPHANS) >= 9:
                    add("ryukyoku")
        if kan_ok:
            counts = Counter(tile_index(t) for t in tiles)
            for base, count in counts.items():
                if count == 4 and (not p["reach_accepted"] or (base == tile_index(drawn) and riichi_ankan(hand, drawn, rules))):
                    add("ankan", consumed=sorted(t for t in tiles if tile_index(t) == base))
            if not p["reach_accepted"]:
                for meld in melds:
                    if meld["kind"] == "pon":
                        for tile in sorted(set(tiles)):
                            if tile_index(tile) == tile_index(meld["tiles"][0]):
                                add("kakan", pai=tile, consumed=sorted(meld["tiles"]))
    unique = {canonical_action(action): action for action in actions}
    require(len(unique) <= 512, "complete candidate set exceeds protocol limit")
    return [unique[key] for key in sorted(unique)]


def abortive_reason(state: dict, rules: dict) -> str | None:
    """After discard reactions and any reach acceptance; wins/calls already fixed."""
    if state["ron_selected"]:
        return "sanchaho" if len(state["ron_selected"]) == 3 and rules["ron_policy"] == "double_only" else None
    enabled = rules["abortive_draws"]
    if all(state["reach_accepted"]) and "suucha_riichi" in enabled:
        return "suucha_riichi"
    if not state["call_selected"] and not state["calls_occurred"] and "suufon_renda" in enabled:
        rivers = state["rivers"]
        if all(len(r) == 1 for r in rivers) and len({r[0] for r in rivers}) == 1 and rivers[0][0] in {"E", "S", "W", "N"}:
            return "suufon_renda"
    if state["fourth_kan_discard"] and sum(state["kan_counts"]) == 4 and max(state["kan_counts"]) < 4 and "suukan_sanra" in enabled:
        return "suukan_sanra"
    if not state["call_selected"] and state["wall_remaining"] == 0:
        return "fanpai"
    return None


def kan_sequence(kind: str, timing: str, choice: str, *, robbed: bool = False, pao: bool = False) -> list[str]:
    """State-event projection after terminal ACKs for a selected kan."""
    require(kind in {"ankan", "kakan", "daiminkan"}, "unknown kan")
    require(timing in {"before_rinshan", "after_rinshan_discard"}, "unknown dora timing")
    require(choice in {"dahai", "reach", "ankan", "kakan", "hora", "penalty"}, "invalid rinshan choice")
    require(not pao or kind == "daiminkan", "only open new set can trigger pao")
    if robbed:
        require(kind != "daiminkan", "daiminkan has no second ron group")
        return ["end_kyoku"]
    events = [kind] + (["pao"] if pao else [])
    if timing == "before_rinshan":
        events.append("dora")
    events.append("tsumo")
    if timing == "after_rinshan_discard" and choice != "penalty":
        events.append("dora")
    events.extend({"dahai": ["dahai"], "reach": ["reach", "dahai"],
                   "ankan": ["ankan_declared"], "kakan": ["kakan_declared"],
                   "hora": ["end_kyoku"], "penalty": ["end_kyoku"]}[choice])
    return events


class EventState:
    """Observable core state for an ordered event prefix, including snapshots.

    Hidden hands remain counts. Acceptance cannot certify an unseen yaku or
    the host's wall permutation; complete decision/scoring fixtures test those
    separately. The caller supplies atomic rollback and request/ACK ordering.
    """
    def __init__(self, rules: dict, self_seat: int | None = None):
        self.rules = rules
        self.self_seat = self_seat
        self.game_phase = "not_started"
        self.scores = [rules["starting_points"]] * 4
        self.kyotaku = 0
        self.next = {"type": "rotate", "bakaze": "E", "kyoku": 1, "oya": 0, "honba": 0, "kyotaku": 0, "extension_round": 0}
        self.round: dict | None = None
        self.last_cause: dict | None = None
        self.required_event: str | None = None
        self.pao_due: list[dict] = []

    def check_snapshot_prefix(self, snapshot: dict) -> None:
        """A checkpoint of a fully observed prefix cannot replace its facts."""
        require(self.required_event is None and not self.pao_due
                and not (self.game_phase == "between_kyoku" and self.next.get("type") == "end_game"),
                "snapshot interrupts a committed event transaction")
        require(snapshot["game_phase"] == self.game_phase
                and snapshot["scores"] == self.scores and snapshot["kyotaku"] == self.kyotaku,
                "contiguous snapshot changes committed phase, scores or deposits")
        expected_next = ({key: value for key, value in self.next.items() if key != "type"}
                         if self.game_phase == "between_kyoku" else None)
        require(canonical_action(snapshot["next_kyoku"]) == canonical_action(expected_next),
                "contiguous snapshot changes the next round")
        if self.round is None:
            return
        previous, current = deepcopy(self.round), deepcopy(snapshot["kyoku"])
        old_phase, new_phase = previous["turn"]["phase"], current["turn"]["phase"]
        require(new_phase == old_phase or (new_phase == "resolving"
                and old_phase in {"awaiting_action", "awaiting_responses"}),
                "contiguous snapshot rewinds the decision phase")
        require(canonical_action(current["turn"]["last_event"]) == canonical_action(self.last_cause),
                "contiguous snapshot changes the last committed event")
        # Ordering a visible hand or a consumed multiset is not a game event.
        # Selection clocks are checked against the receiver's shared bank.
        for kyoku in (previous, current):
            kyoku["turn"] = {"actor": kyoku["turn"]["actor"]}
            for hand in kyoku["hands"]:
                if "tiles" in hand:
                    hand["tiles"].sort()
            if "self_state" in kyoku:
                kyoku["self_state"].pop("time_bank_ms", None)
        require(canonical_action(current) == canonical_action(previous),
                "contiguous snapshot changes observed round state")

    def restore(self, snapshot: dict) -> None:
        kyoku = snapshot["kyoku"]
        require((kyoku is not None) == (snapshot["game_phase"] == "in_kyoku"),
                "snapshot kyoku presence differs from phase")
        nxt = snapshot["next_kyoku"]
        require((nxt is not None) == (snapshot["game_phase"] == "between_kyoku")
                and (nxt is None or (nxt["kyotaku"] == snapshot["kyotaku"] and round_coordinates_reachable(nxt, self.rules))),
                "snapshot next kyotaku differs")
        if kyoku is not None:
            require(round_coordinates_reachable(kyoku, self.rules), "snapshot round coordinates are unreachable")
            require(sorted(kyoku["pao"],key=lambda p:(p["actor"],p["yaku_id"])) == public_pao(kyoku["melds"],self.rules), "snapshot pao differs from public meld history")
            # A snapshot is fixed only at a transaction boundary, so its last
            # committed event is a decision cause or the round start — never
            # a transaction-interior event (call, acceptance, marker, pao).
            reactions = {"dahai", "ankan_declared", "kakan_declared"}
            phase, cause_type = kyoku["turn"]["phase"], kyoku["turn"]["last_event"]["type"]
            require(phase == "awaiting_draw" and cause_type == "start_kyoku"
                    or phase == "awaiting_action" and cause_type == "tsumo"
                    or phase == "awaiting_responses" and cause_type in reactions
                    or phase == "resolving" and cause_type in reactions | {"tsumo"},
                    "snapshot phase does not follow its last committed event")
            declared = cause_type in {"ankan_declared", "kakan_declared"}
            require(kyoku["pending_kan"] == (kyoku["turn"]["last_event"] if declared else None),
                    "snapshot pending kan differs from its cause")
            self_state = kyoku.get("self_state")
            require(self_state is None or not self_state["kuikae_forbidden"],
                    "snapshot pauses a compound discard")
            reach_declared = [a for a, s in enumerate(kyoku["reach_status"]) if s["state"] == "declared"]
            require(not reach_declared or (reach_declared == [kyoku["turn"]["actor"]] and cause_type == "dahai"
                                           and phase in {"awaiting_responses", "resolving"}),
                    "unaccepted reach declaration survives outside its discard window")
            for a, s in enumerate(kyoku["reach_status"]):
                river = kyoku["rivers"][a]
                if s["state"] == "none":
                    require(not s["double"] and not s["ippatsu"] and not any(t["reach"] for t in river),
                            "reach flags or river mark precede any declaration")
                elif s["state"] == "declared":
                    require(river and river[-1]["reach"] and not s["ippatsu"],
                            "declaration discard is unmarked or carries ippatsu")
                    require(s["double"] == (len(river) == 1 and not any(kyoku["melds"])),
                            "double flag differs from declaration-time eligibility")
                else:
                    require(any(t["reach"] for t in river), "accepted reach has no marked discard")
                    require(not s["double"] or river[0]["reach"],
                            "double flag differs from declaration-time eligibility")
                    require(s["double"] or not (river[0]["reach"] and not any(kyoku["melds"])),
                            "double flag differs from declaration-time eligibility")
                    require(not s["ippatsu"] or river[-1]["reach"], "ippatsu survives a post-reach discard")
                    require(s["ippatsu"] or not (river[-1]["reach"] and not any(kyoku["melds"])),
                            "ippatsu flag differs from the open reach window")
            cause = kyoku["turn"]["last_event"]
            # The turn actor and projected state follow the committed cause:
            # a round start fixes the dealer and a fresh board, while a
            # decision cause belongs to the acting seat and its physical
            # effects are already visible in rivers, hands and the pending
            # declaration.
            if cause_type == "start_kyoku":
                require(kyoku["turn"]["actor"] == cause["oya"]
                        and all(kyoku[k] == cause[k] for k in ("bakaze", "kyoku", "oya", "honba", "kyotaku", "extension_round"))
                        and all(("tiles" in hand) == ("tiles" in dealt)
                                and (Counter(hand["tiles"]) == Counter(dealt["tiles"]) if "tiles" in hand
                                     else hand["count"] == dealt["count"])
                                for hand, dealt in zip(kyoku["hands"], cause["hands"]))
                        and kyoku["dora_markers"] == [cause["dora_marker"]]
                        and snapshot["scores"] == cause["scores"],
                        "snapshot round start differs from its start event")
                require(not any(kyoku["rivers"]) and not any(kyoku["melds"]) and not kyoku["pao"]
                        and kyoku["kan_counts"] == [0] * 4 and not kyoku["rinshan"] and not kyoku["haitei"]
                        and all(s["state"] == "none" for s in kyoku["reach_status"])
                        and kyoku["wall_remaining"] == 70
                        and (kyoku.get("self_state") is None
                             or not kyoku["self_state"]["temporary_furiten"] and not kyoku["self_state"]["riichi_furiten"]),
                        "snapshot round start is not a fresh deal")
            else:
                actor = kyoku["turn"]["actor"]
                require(actor == cause["actor"], "snapshot turn actor differs from its cause")
                if cause_type == "dahai":
                    tail = kyoku["rivers"][actor][-1] if kyoku["rivers"][actor] else None
                    require(tail is not None and tail["pai"] == cause["pai"]
                            and tail["tsumogiri"] == cause["tsumogiri"] and tail["called_by"] is None
                            and tail["reach"] == (kyoku["reach_status"][actor]["state"] == "declared"),
                            "snapshot river tail differs from its cause discard")
                elif cause_type == "tsumo":
                    hand = kyoku["hands"][actor]
                    require(cause["pai"] is None or "tiles" not in hand or cause["pai"] in hand["tiles"],
                            "snapshot draw is absent from the visible hand")
                else:
                    require(len({tile_index(t) for t in cause["consumed"]}) == 1,
                            "kan declaration mixes tile kinds")
                    require(kyoku["wall_remaining"] > 0 and sum(kyoku["kan_counts"]) < 4,
                            "kan declaration lacks commit capacity")
                    if cause_type == "kakan_declared":
                        require(kyoku["reach_status"][actor]["state"] == "none"
                                and tile_index(cause["pai"]) == tile_index(cause["consumed"][0])
                                and any(m["type"] == "pon" and Counter([m["pai"], *m["consumed"]]) == Counter(cause["consumed"])
                                        for m in kyoku["melds"][actor]),
                                "kakan declaration has no matching pon")
                    hand = kyoku["hands"][actor]
                    if "tiles" in hand:
                        needed = Counter(cause["consumed"] if cause_type == "ankan_declared" else [cause["pai"]])
                        require(not needed - Counter(hand["tiles"]), "kan declaration uses absent visible tiles")
            # Self furiten flags follow public state: riichi furiten exists
            # only under an accepted declaration, and a seat's own draw
            # clears its temporary furiten (§10.4, §13.3).
            if self_state is not None:
                seat = snapshot.get("seat")
                require(isinstance(seat, int) and 0 <= seat <= 3, "self state without a play seat")
                require(not self_state["riichi_furiten"]
                        or kyoku["reach_status"][seat]["state"] == "accepted",
                        "riichi furiten without an accepted declaration")
                require(not self_state["temporary_furiten"]
                        or cause_type != "tsumo" or cause["actor"] != seat,
                        "temporary furiten survives its own draw")
            # Committed melds must be physically well-formed, and every
            # called river tile links the caller to the meld that took it:
            # the discard stays in the discarder's river with `called_by`
            # set, and the caller's meld names the same tile and seat.
            for seat, row in enumerate(kyoku["melds"]):
                for meld in row:
                    require(meld["actor"] == seat, "snapshot meld seat differs")
                    require(len(meld["consumed"]) == {"chi":2,"pon":2,"daiminkan":3,"ankan":4,"kakan":3}[meld["type"]],
                            "snapshot meld consumed count differs")
                    target = meld.get("target")
                    if meld["type"] == "ankan":
                        require(target is None and len({tile_index(t) for t in meld["consumed"]}) == 1,
                                "invalid ankan meld")
                        continue
                    require(isinstance(target, int) and 0 <= target <= 3 and target != seat,
                            "snapshot meld target seat differs")
                    indices = sorted(tile_index(t) for t in [meld.get("pai"), *meld["consumed"]])
                    if meld["type"] == "chi":
                        require(target == (seat + 3) % 4 and indices[0] < 27
                                and indices[0] // 9 == indices[-1] // 9
                                and indices == list(range(indices[0], indices[0] + 3)),
                                "invalid chi meld geometry")
                    else:
                        require(len(set(indices)) == 1, "invalid meld geometry")
                    require(any(t["pai"] == meld.get("pai") and t["called_by"] == seat
                                for t in kyoku["rivers"][target]),
                            "called meld lacks its river discard")
            for seat, river in enumerate(kyoku["rivers"]):
                for tile in river:
                    caller = tile["called_by"]
                    require(caller is None
                            or (isinstance(caller, int) and 0 <= caller <= 3 and caller != seat
                                and any(m["type"] != "ankan" and m["target"] == seat and m.get("pai") == tile["pai"]
                                        for m in kyoku["melds"][caller])),
                            "called river tile has no matching meld")
            check_snapshot_public_history(kyoku)
            check_snapshot_rinshan(kyoku, self.rules)
            require(sum(s["state"] == "accepted" for s in kyoku["reach_status"]) <= kyoku["kyotaku"],
                    "accepted riichi deposits exceed the round's deposit count")
            for a in range(4):
                require(kyoku["first_turn_eligible"][a] == (not kyoku["rivers"][a] and not any(kyoku["melds"])),
                        "first-turn eligibility differs from public discard/call history")
            require(kyoku["kyotaku"] == snapshot["kyotaku"], "snapshot kyotaku differs")
            holds_draw = (phase == "awaiting_action"
                          or phase == "resolving" and cause_type == "tsumo"
                          or kyoku["pending_kan"] is not None and phase in {"awaiting_responses", "resolving"})
            for a, hand in enumerate(kyoku["hands"]):
                concealed = len(hand["tiles"]) if "tiles" in hand else hand["count"]
                require(concealed == 13 - 3 * len(kyoku["melds"][a]) + int(a == kyoku["turn"]["actor"] and holds_draw),
                        "snapshot concealed tile count differs")
            require(sum(kyoku["kan_counts"]) <= 4
                    and all(kyoku["kan_counts"][a] == sum(m["type"] in {"ankan", "daiminkan", "kakan"} for m in kyoku["melds"][a])
                            for a in range(4)),
                    "kan count differs from committed melds")
            concealed = sum(len(h["tiles"]) if "tiles" in h else h["count"] for h in kyoku["hands"])
            meld_tiles = sum(len(m["consumed"]) + int(m["type"] != "ankan") for row in kyoku["melds"] for m in row)
            river_tiles = sum(t["called_by"] is None for river in kyoku["rivers"] for t in river)
            dead = 14 + int(kyoku["rinshan"] and phase == "awaiting_draw")
            require(concealed + meld_tiles + river_tiles + kyoku["wall_remaining"] + dead == 136,
                    "snapshot loses or creates physical tiles")
            require(len(kyoku["dora_markers"]) == 1 + sum(kyoku["kan_counts"]) - int(kyoku["pending_dora"] is not None),
                    "dora count differs from kan state")
            inventory(known_round_tiles(kyoku), self.rules)
        require(snapshot["game_phase"] != "ended"
                or snapshot.get("final_rankings") == rankings(snapshot["scores"]),
                "snapshot rankings differ")
        require(sum(snapshot["scores"]) + snapshot["kyotaku"] * self.rules["riichi_stick_value"]
                == 4 * self.rules["starting_points"],
                "snapshot does not conserve scores and deposits")
        self.self_seat = snapshot["seat"] if snapshot["mode"] == "play" else None
        self.game_phase = snapshot["game_phase"]
        self.scores = snapshot["scores"].copy()
        self.kyotaku = snapshot["kyotaku"]
        self.next = deepcopy(snapshot["next_kyoku"])
        self.round = deepcopy(kyoku)
        self.required_event, self.pao_due = None, []
        if self.round is not None:
            self.last_cause = deepcopy(self.round["turn"]["last_event"])
            self.round["turn"] = {"actor": self.round["turn"]["actor"], "phase": self.round["turn"]["phase"]}
        else:
            self.last_cause = None

    def acknowledge(self, request: dict, ack: dict) -> None:
        if self.round is None or self.self_seat is None or ack["status"] in {"rejected", "stale"}:
            return
        chosen = next(c["action"] for c in request["legal_actions"] if c["action_id"] == ack["action_id"])
        cause = self.last_cause
        if chosen["type"] == "hora" or cause["type"] == "tsumo":
            return
        hand = self._scoring_hand(self.self_seat)
        tile = cause["consumed"][0] if cause["type"] == "ankan_declared" else cause["pai"]
        if cause["type"] == "ankan_declared":
            if self.rules["ankan_chankan"] == "never":
                return
            counts, fixed, _ = hand_parts(hand,self.rules)
            full = list(counts)
            full[tile_index(tile)] += 1
            if not any(form == "kokushi" for form,_,_ in shapes(tuple(full),fixed)):
                return
        if tile_index(tile) in waits(hand, self.rules):
            flag = "riichi_furiten" if self.round["reach_status"][self.self_seat]["state"] == "accepted" else "temporary_furiten"
            self.round["self_state"][flag] = True

    def _hand(self, actor: int, remove: list[str] = (), add: list[str | None] = ()) -> None:
        hand = self.round["hands"][actor]
        if "tiles" in hand:
            for tile in remove:
                require(tile in hand["tiles"], "event consumes a tile absent from the visible hand")
                hand["tiles"].remove(tile)
            require(all(t is not None for t in add), "visible draw is missing")
            hand["tiles"].extend(add)
        else:
            hand["count"] += len(add) - len(remove)
            require(hand["count"] >= 0, "negative concealed hand count")

    def _scoring_hand(self, actor: int) -> dict:
        melds = []
        for event in self.round["melds"][actor]:
            m = {"kind":event["type"],"tiles":[*event["consumed"]],"open":event["type"] != "ankan"}
            if m["open"]:
                m["tiles"].append(event["pai"])
                m["source"] = event["target"]
            melds.append(m)
        return {"concealed_tiles":self.round["hands"][actor]["tiles"].copy(),"melds":melds}

    def _meld(self, event: dict) -> None:
        r, actor, kind = self.round, event["actor"], event["type"]
        if kind == "kakan":
            candidates = [m for m in r["melds"][actor] if m["type"] == "pon" and Counter([m["pai"], *m["consumed"]]) == Counter(event["consumed"])]
            require(len(candidates) == 1, "kakan does not identify an existing pon")
            old = candidates[0]
            require(tile_index(event["pai"]) == tile_index(old["pai"]), "added tile differs from pon kind")
            self._hand(actor, remove=[event["pai"]])
            old.update(deepcopy(event), target=old["target"], pai=old["pai"],
                       consumed=[*old["consumed"], event["pai"]])
        else:
            indices = sorted(tile_index(t) for t in event["consumed"])
            if kind != "ankan":
                indices.append(tile_index(event["pai"]))
                indices.sort()
            if kind == "chi":
                require(len(indices) == 3 and indices[0] < 27 and indices[0] // 9 == indices[-1] // 9 and indices == list(range(indices[0], indices[0] + 3)), "invalid chi geometry")
            else:
                require(len(set(indices)) == 1 and len(indices) == (3 if kind == "pon" else 4), "invalid triplet/quad geometry")
            self._hand(actor, remove=event["consumed"])
            if kind in {"pon", "daiminkan"}:
                seen = {tile_index(m["consumed"][0]) for m in r["melds"][actor] if m["type"] != "chi"}
                for name, needed in (("daisangen", {31, 32, 33}), ("daisuushii", {27, 28, 29, 30})):
                    if name in self.rules["pao"]["yakus"] and indices[0] in needed and len(seen & needed) == len(needed) - 1:
                        self.pao_due.append({"type": "pao", "actor": actor, "yaku_id": name, "liable_seat": event["target"]})
            r["melds"][actor].append(deepcopy(event))
        r["first_turn_eligible"] = [False] * 4
        for reach in r["reach_status"]:
            reach["ippatsu"] = False

    def _commit_kan(self, event: dict) -> None:
        r = self.round
        require(r["wall_remaining"] > 0 and sum(r["kan_counts"]) < 4, "kan lacks live-wall replacement/rinshan capacity")
        self._meld(event)
        r["kan_counts"][event["actor"]] += 1
        r["wall_remaining"] -= 1
        r["rinshan"], r["haitei"], r["pending_kan"] = True, False, None
        r["turn"].update(actor=event["actor"], phase="awaiting_draw")
        timing = self.rules["kan_dora_timing"][event["type"]]
        r["pending_dora"] = {"kan_type": event["type"], "timing": timing}

    def _inventory(self) -> None:
        inventory(known_round_tiles(self.round), self.rules)

    def _automatic_draw_reason(self) -> str | None:
        r = self.round
        public = {"ron_selected":[],"reach_accepted":[s["state"] == "accepted" for s in r["reach_status"]],
                  "call_selected":False,"calls_occurred":any(r["melds"]),"rivers":[[t["pai"] for t in river] for river in r["rivers"]],
                  "fourth_kan_discard":r["turn"]["phase"] in {"awaiting_responses","resolving"} and r["pending_kan"] is None,
                  "kan_counts":r["kan_counts"],"wall_remaining":r["wall_remaining"]}
        return abortive_reason(public, self.rules)

    def apply(self, event: dict) -> None:
        kind = event["type"]
        if kind.startswith("x-"):
            return  # The negotiated owner validates extension state separately.
        if kind == "start_game":
            require(self.game_phase == "not_started", "duplicate start_game")
            require(event["rules"] == self.rules, "start_game changed negotiated rules")
            require(event["scores"] == [self.rules["starting_points"]] * 4, "wrong initial scores")
            self.game_phase = "between_kyoku"
            self.scores = event["scores"].copy()
            return
        require(self.game_phase not in {"not_started", "ended"}, "event outside a started game")
        if kind == "start_kyoku":
            # A between-round snapshot carries coordinates, without the prior
            # result's renchan/rotate tag. Either representation starts a round.
            require(self.game_phase == "between_kyoku" and self.next is not None and self.next.get("type") != "end_game", "unexpected next round")
            require(all(event[k] == self.next[k] for k in ("bakaze", "kyoku", "oya", "honba", "kyotaku", "extension_round")), "start_kyoku differs from settled next coordinate")
            require(event["scores"] == self.scores, "start_kyoku changed scores")
            require(all(len(h["tiles"]) == 13 if "tiles" in h else h["count"] == 13 for h in event["hands"]), "initial hands must have thirteen tiles")
            self.round = {k: deepcopy(event[k]) for k in ("bakaze", "kyoku", "oya", "honba", "kyotaku", "extension_round", "hands")}
            self.round.update(rivers=[[] for _ in range(4)], melds=[[] for _ in range(4)], wall_remaining=70,
                              dora_markers=[event["dora_marker"]], turn={"actor":event["oya"], "phase":"awaiting_draw"},
                              kan_counts=[0]*4, first_turn_eligible=[True]*4, rinshan=False, haitei=False,
                              reach_status=[{"state":"none","double":False,"ippatsu":False} for _ in range(4)],
                              pending_kan=None, pending_dora=None, pao=[])
            if self.self_seat is not None:
                self.round["self_state"] = {"temporary_furiten":False,"riichi_furiten":False,"kuikae_forbidden":[],"time_bank_ms":self.rules["time_control"]["bank_ms"]}
            self.game_phase, self.required_event, self.pao_due = "in_kyoku", None, []
            self.last_cause = deepcopy(event)
            self._inventory()
            return
        if kind == "end_game":
            require(self.game_phase == "between_kyoku" and self.next is not None and self.next.get("type") == "end_game", "end_game without final round decision")
            require(event["scores"] == self.scores and event["kyotaku"] == self.kyotaku and event["rankings"] == rankings(self.scores), "final scores/deposits/rankings differ")
            self.game_phase = "ended"
            return
        require(self.game_phase == "in_kyoku", "round event between rounds")
        r, actor = self.round, event.get("actor")
        phase, turn_actor = r["turn"]["phase"], r["turn"]["actor"]
        if phase == "resolving":
            # The linearized decision is already fixed; its expansion is
            # applied under the same preconditions as the decision window
            # it replaced (turn action after a draw, reactions otherwise).
            phase = "awaiting_action" if self.last_cause["type"] == "tsumo" else "awaiting_responses"
        require(not self.pao_due or event == self.pao_due[0], "required pao event missing or incorrect")
        require(self.required_event is None or kind == self.required_event or (self.pao_due and kind == "pao"), "compound event sequence interrupted")
        if self.required_event == kind:
            self.required_event = None
        pending_dora = r["pending_dora"]
        if pending_dora is not None and kind not in {"dora", "pao"}:
            before = pending_dora["timing"] == "before_rinshan"
            cancelling = kind == "end_kyoku" and event["result"]["type"] == "penalty"
            require((not before and phase == "awaiting_draw" and kind == "tsumo") or (not before and cancelling), "kan marker publication is out of order")
        if kind == "tsumo":
            if phase == "awaiting_responses":
                require(r["pending_kan"] is None and self.last_cause["type"] == "dahai", "draw before kan resolution")
                require(r["reach_status"][turn_actor]["state"] != "declared", "draw before reach acceptance")
                require(self._automatic_draw_reason() is None, "next draw after automatic round end")
                turn_actor = (turn_actor + 1) % 4
            else:
                require(phase == "awaiting_draw", "draw outside draw phase")
            require(actor == turn_actor and (r["wall_remaining"] > 0 or r["rinshan"]), "wrong draw actor or empty live wall")
            if not r["rinshan"]:
                r["wall_remaining"] -= 1
            r["haitei"] = r["wall_remaining"] == 0 and not r["rinshan"]
            self._hand(actor, add=[event["pai"]])
            if actor == self.self_seat:
                r["self_state"]["temporary_furiten"] = False
            r["turn"].update(actor=actor, phase="awaiting_action")
            self.last_cause = deepcopy(event)
        elif kind == "dahai":
            require(phase == "awaiting_action" and actor == turn_actor, "discard outside own action phase")
            if event["tsumogiri"]:
                require(self.last_cause["type"] == "tsumo" and self.last_cause["actor"] == actor and self.last_cause["pai"] in {None, event["pai"]}, "tsumogiri differs from latest draw")
            elif self.last_cause["type"] == "tsumo" and self.last_cause["pai"] == event["pai"] and "tiles" in r["hands"][actor]:
                require(r["hands"][actor]["tiles"].count(event["pai"]) >= 2,
                        "hand discard cannot use the sole drawn tile")
            if r["reach_status"][actor]["state"] == "accepted":
                require(event["tsumogiri"], "changed discard after accepted riichi")
                r["reach_status"][actor]["ippatsu"] = False
            self._hand(actor, remove=[event["pai"]])
            if self.last_cause["type"] in {"chi", "pon"}:
                require(not event["tsumogiri"] and tile_index(event["pai"]) not in kuikae(self.last_cause["type"], self.last_cause["pai"], self.last_cause["consumed"]), "compound discard violates kuikae")
            if actor == self.self_seat:
                r["self_state"]["kuikae_forbidden"] = []
            if r["reach_status"][actor]["state"] == "declared" and "tiles" in r["hands"][actor]:
                require(bool(waits(self._scoring_hand(actor), self.rules)), "reach discard is not tenpai")
            r["rivers"][actor].append({"pai":event["pai"],"tsumogiri":event["tsumogiri"],"reach":r["reach_status"][actor]["state"] == "declared","called_by":None})
            r["first_turn_eligible"][actor] = False
            r["rinshan"] = False
            r["turn"]["phase"] = "awaiting_responses"
            self.last_cause = deepcopy(event)
        elif kind == "reach":
            require(phase == "awaiting_action" and actor == turn_actor and r["reach_status"][actor]["state"] == "none", "invalid reach declaration")
            require(all(m["type"] == "ankan" for m in r["melds"][actor]) and self.scores[actor] >= self.rules["riichi_stick_value"] and r["wall_remaining"] >= 4, "reach prerequisites differ")
            r["reach_status"][actor].update(state="declared", double=r["first_turn_eligible"][actor])
            self.required_event = "dahai"
        elif kind == "reach_accepted":
            require(phase == "awaiting_responses" and actor == turn_actor and self.last_cause["type"] == "dahai" and r["reach_status"][actor]["state"] == "declared", "reach acceptance lacks declaration/discard")
            deltas = [-self.rules["riichi_stick_value"] if seat == actor else 0 for seat in range(4)]
            require(event["deltas"] == deltas and event["scores"] == [s+d for s,d in zip(self.scores,deltas)] and event["kyotaku"] == self.kyotaku + 1, "reach deposit differs")
            self.scores, self.kyotaku = event["scores"].copy(), event["kyotaku"]
            r["kyotaku"] = self.kyotaku
            r["reach_status"][actor].update(state="accepted", ippatsu=True)
        elif kind in {"ankan_declared", "kakan_declared"}:
            require(phase == "awaiting_action" and actor == turn_actor and r["pending_kan"] is None, "kan declaration outside own turn")
            require(r["wall_remaining"] > 0 and sum(r["kan_counts"]) < 4, "kan declaration without replacement capacity")
            require(len({tile_index(t) for t in event["consumed"]}) == 1, "kan declaration mixes tile kinds")
            if kind == "kakan_declared":
                require(r["reach_status"][actor]["state"] == "none", "kakan after riichi")
                require(any(m["type"] == "pon" and Counter([m["pai"],*m["consumed"]]) == Counter(event["consumed"]) for m in r["melds"][actor]), "kakan declaration has no matching pon")
                require(tile_index(event["pai"]) == tile_index(event["consumed"][0]), "added tile differs from declared pon")
            if "tiles" in r["hands"][actor]:
                held = Counter(r["hands"][actor]["tiles"])
                consumed = Counter(event["consumed"] if kind == "ankan_declared" else [event["pai"]])
                require(not consumed - held, "kan declaration uses absent visible tiles")
                if r["reach_status"][actor]["state"] == "accepted":
                    require(self.last_cause["type"] == "tsumo" and riichi_ankan(self._scoring_hand(actor), self.last_cause["pai"], self.rules), "riichi kan changes shape or does not use draw")
            r["pending_kan"] = deepcopy(event)
            r["turn"]["phase"] = "awaiting_responses"
            self.last_cause = deepcopy(event)
        elif kind in {"ankan", "kakan"}:
            pending = r["pending_kan"]
            require(phase == "awaiting_responses" and pending is not None and canonical_action(event) == canonical_action({**pending,"type":kind}) and pending["type"] == kind + "_declared", "kan commit differs from pending declaration")
            self._commit_kan(event)
        elif kind in {"chi", "pon", "daiminkan"}:
            cause = self.last_cause
            require(phase == "awaiting_responses" and r["pending_kan"] is None and cause["type"] == "dahai", "call without resolved discard")
            require(r["reach_status"][cause["actor"]]["state"] != "declared", "call before reach acceptance")
            require(r["reach_status"][actor]["state"] == "none" and r["wall_remaining"] > 0, "call after riichi or last tile")
            require(event["target"] == cause["actor"] != actor and event["pai"] == cause["pai"], "call target/tile differs")
            require(kind != "chi" or event["target"] == (actor + 3) % 4, "chi from wrong seat")
            require(not (sum(r["kan_counts"]) == 4 and max(r["kan_counts"]) < 4 and "suukan_sanra" in self.rules["abortive_draws"]), "call on fourth-kan final discard")
            r["rivers"][event["target"]][-1]["called_by"] = actor
            if kind == "daiminkan":
                self._commit_kan(event)
            else:
                self._meld(event)
                r["turn"].update(actor=actor, phase="awaiting_action")
                if actor == self.self_seat:
                    r["self_state"]["kuikae_forbidden"] = [TILES[t] for t in sorted(kuikae(kind,event["pai"],event["consumed"]))]
                self.required_event = "dahai"
                self.last_cause = deepcopy(event)
        elif kind == "pao":
            require(bool(self.pao_due), "unsolicited pao assignment")
            self.pao_due.pop(0)
            r["pao"].append({k:event[k] for k in ("actor", "yaku_id", "liable_seat")})
        elif kind == "dora":
            require(pending_dora is not None, "dora without committed kan")
            require(phase == ("awaiting_draw" if pending_dora["timing"] == "before_rinshan" else "awaiting_action"), "dora at wrong rinshan boundary")
            r["dora_markers"].append(event["dora_marker"])
            r["pending_dora"] = None
        elif kind == "end_kyoku":
            result = event["result"]
            require(event["scores"] == [s+d for s,d in zip(self.scores,event["deltas"])], "settlement scores differ from previous scores")
            deposits = 0 if result["type"] == "hora" else self.kyotaku
            require(sum(event["deltas"]) + (deposits-self.kyotaku)*self.rules["riichi_stick_value"] == 0, "settlement does not conserve deposits")
            if result["type"] == "hora":
                cause = self.last_cause
                require(cause["type"] in {"tsumo", "dahai", "ankan_declared", "kakan_declared"}, "hora has no winning source")
                require(phase == ("awaiting_action" if cause["type"] == "tsumo" else "awaiting_responses"), "win outside its decision window")
                tile = cause["consumed"][0] if cause["type"] == "ankan_declared" else cause["pai"]
                if cause["type"] == "dahai":
                    river = r["rivers"][cause["actor"]]
                    require(not (river and river[-1]["reach"] and r["reach_status"][cause["actor"]]["state"] == "accepted"), "ron after the reach discard was accepted")
                if cause["type"] == "ankan_declared":
                    require(self.rules["ankan_chankan"] == "kokushi_only"
                            and all(any(yaku["id"] == "kokushi_musou" for yaku in win["yakus"]) for win in result["wins"]),
                            "ankan rob is not an allowed kokushi")
                actors = [win["actor"] for win in result["wins"]]
                require(actors == sorted(actors) and len(set(actors)) == len(actors), "wins are not sorted by unique actor")
                require(event["deltas"] == [sum(win["deltas"][seat] for win in result["wins"]) for seat in range(4)],
                        "hora deltas do not match win deltas")
                for win in result["wins"]:
                    require(win["target"] == cause["actor"] and (tile is None or win["pai"] == tile), "win differs from its source event")
                    require((win["actor"] == win["target"]) == (cause["type"] == "tsumo"), "win method differs from its source event")
                    check_hora_yaku_context(win, r, cause, self.rules)
                    require([yaku["id"] for yaku in win["yakus"]] == sorted(yaku["id"] for yaku in win["yakus"]), "win yaku ids are not in ASCII order")
                    require([bonus["id"] for bonus in win["bonuses"]] == sorted(bonus["id"] for bonus in win["bonuses"]), "win bonus ids are not in ASCII order")
                    yakuman = sum(yaku["value"] for yaku in win["yakus"] if yaku["unit"] == "yakuman")
                    if yakuman:
                        require(win["han"] == 0 and win["fu"] == 0 and win["bonuses"] == []
                                and all(yaku["unit"] == "yakuman" for yaku in win["yakus"]), "yakuman win must not carry han/fu/bonuses")
                    else:
                        require(win["han"] == sum(yaku["value"] for yaku in win["yakus"] if yaku["unit"] == "han") + sum(bonus["han"] for bonus in win["bonuses"]),
                                "win han does not match yaku/bonus sum")
                    if r["reach_status"][win["actor"]]["state"] == "accepted":
                        require(len(win["ura_dora_markers"]) == len(r["dora_markers"]), "ura markers differ from published dora markers")
                    else:
                        require(win["ura_dora_markers"] == [], "ura markers without an accepted reach")
                require(cause["type"] != "tsumo" or len(result["wins"]) == 1, "multiple tsumo winners")
                require(self.rules["ron_policy"] != "head_bump" or len(result["wins"]) == 1, "multiple winners under head bump")
                require(self.rules["ron_policy"] != "double_only" or len(result["wins"]) <= 2, "three accepted winners under double-only")
                for win in result["wins"]:
                    yakus = {y["id"] for y in win["yakus"]}
                    expected_pao = [{"yaku_id":p["yaku_id"],"liable_seat":p["liable_seat"]} for p in r["pao"] if p["actor"] == win["actor"] and p["yaku_id"] in yakus]
                    require(win["pao"] == sorted(expected_pao,key=lambda p:p["yaku_id"]), "winner pao differs from public assignment history")
                ura = [win["ura_dora_markers"] for win in result["wins"] if win["ura_dora_markers"]]
                require(not ura or all(markers == ura[0] for markers in ura),
                        "winners do not share ura indicator positions")
                known = known_round_tiles(r)
                if cause["type"] == "tsumo" and "tiles" not in r["hands"][cause["actor"]]:
                    known.append(result["wins"][0]["pai"])
                inventory([*known, *(ura[0] if ura else [])], self.rules)
                check_hora_payments(result["wins"], r, self.rules)
            elif result["type"] == "penalty":
                require(self.rules["invalid_action_policy"] == "chombo", "penalty is disabled")
                offender = result["offender"]
                amount = self.rules["chombo"]["penalty_points"]
                others = [seat for seat in range(4) if seat != offender]
                base, remainder = amount // 300 * 100, amount % 300
                expected = {seat: base + (remainder if seat == others[0] else 0) for seat in others}
                expected = {seat: points for seat, points in expected.items() if points}
                shares = {}
                for payment in result["penalty"]["payments"]:
                    require(payment["from"] == offender and payment["to"] != offender and payment["to"] not in shares, "penalty payment endpoints are invalid")
                    shares[payment["to"]] = payment["points"]
                require(shares == expected, "penalty payments differ from the chombo distribution")
                deltas = [0] * 4
                for seat, points in expected.items():
                    deltas[offender] -= points
                    deltas[seat] += points
                require(event["deltas"] == deltas, "penalty deltas differ from the chombo payments")
            else:
                reason = result["reason"]
                if reason == "fanpai":
                    require(r["wall_remaining"] == 0 and phase == "awaiting_responses" and r["pending_kan"] is None, "exhaustive draw before last discard")
                    tenpai = result["tenpai"]
                    total = self.rules["noten_payment"]["total_points"]
                    count = sum(tenpai)
                    expected = [0] * 4 if count in (0, 4) else [total // count if ready else -total // (4 - count) for ready in tenpai]
                    require(event["deltas"] == expected, "noten settlement differs from the tenpai count")
                    for seat in range(4):
                        if "tiles" in r["hands"][seat]:
                            require(bool(waits(self._scoring_hand(seat), self.rules)) == tenpai[seat], "tenpai declaration differs from a visible hand")
                else:
                    require(reason in self.rules["abortive_draws"], "abortive draw disabled")
                    require(result["tenpai"] is None and event["deltas"] == [0] * 4,
                            "abortive draw must preserve scores without tenpai settlement")
                    if reason == "sanchaho":
                        cause = self.last_cause
                        require(self.rules["ron_policy"] == "double_only" and phase == "awaiting_responses"
                                and cause["type"] in {"dahai", "ankan_declared", "kakan_declared"},
                                "sanchaho without a ron group")
                        if cause["type"] == "dahai":
                            river = r["rivers"][cause["actor"]]
                            require(not (river and river[-1]["reach"] and r["reach_status"][cause["actor"]]["state"] == "accepted"),
                                    "sanchaho after the reach discard was accepted")
                        elif cause["type"] == "ankan_declared":
                            require(self.rules["ankan_chankan"] == "kokushi_only"
                                    and tile_index(cause["consumed"][0]) in ORPHANS,
                                    "sanchaho on an ankan that cannot be robbed")
                    elif reason == "kyushukyuhai":
                        require(phase == "awaiting_action" and r["first_turn_eligible"][turn_actor], "nine-orphans draw after first turn interruption")
                        if "tiles" in r["hands"][turn_actor]:
                            require(len({tile_index(t) for t in r["hands"][turn_actor]["tiles"]} & ORPHANS) >= 9, "fewer than nine different orphans")
                if reason not in {"sanchaho", "kyushukyuhai"}:
                    require(self._automatic_draw_reason() == reason, "automatic draw condition or priority differs")
            current = {key:r[key] for key in ("bakaze", "kyoku", "oya", "honba", "extension_round")}
            expected_next = next_kyoku(current, result, event["scores"], deposits, self.rules)
            require(event["next"] == expected_next, "round progression differs from rule priority")
            self.scores, self.kyotaku, self.next = event["scores"].copy(), deposits, expected_next
            self.round, self.game_phase, self.required_event, self.pao_due = None, "between_kyoku", None, []
        else:
            raise GameError("unknown core event")
        if self.round is not None:
            self._inventory()
