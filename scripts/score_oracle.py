#!/usr/bin/env python3
"""Score riichi-4p inputs independently of fixture IDs and expected results.

The normalized input contains the hand before the winning tile, actual melds,
timing predicates and settlement counters. Every possible decomposition AND
winning-tile assignment is scored. Fixture metadata is used only for reporting.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "test-vectors/yrc-0005/1.0-draft.4/scoring.json"
SEATS = (0, 1, 2, 3)
WINDS = ("E", "S", "W", "N")
DRAGONS = {"P": "yakuhai_haku", "F": "yakuhai_hatsu", "C": "yakuhai_chun"}
HONORS = set(WINDS) | set(DRAGONS)
TILES = tuple(f"{n}{s}" for s in "mps" for n in range(1, 10)) + tuple("ESWNPFC")
ORPHANS = {t for t in TILES if t in HONORS or t[0] in "19"}
GREEN_TILES = {"2s", "3s", "4s", "6s", "8s", "F"}


def norm(tile: str) -> str:
    return tile[:-1] if tile.endswith("r") else tile


def is_honor(tile: str) -> bool:
    return norm(tile) in HONORS


def is_terminal(tile: str) -> bool:
    """Terminal OR honour (the yaochuu set)."""
    return norm(tile) in ORPHANS


def is_simple(tile: str) -> bool:
    return not is_terminal(tile)


def ceil100(points: int) -> int:
    return ((points + 99) // 100) * 100


def ceil10(points: int) -> int:
    return ((points + 9) // 10) * 10


@dataclass(frozen=True)
class Group:
    kind: str
    tiles: tuple[str, ...]
    open: bool
    winning: bool = False


@dataclass(frozen=True)
class Decomposition:
    pair: str
    groups: tuple[Group, ...]
    pair_winning: bool


def meld_group(meld: Mapping[str, Any]) -> Group:
    kind = meld["kind"]
    tiles = tuple(sorted(norm(t) for t in meld["tiles"]))
    if kind in {"chi", "shuntsu"}:
        if len(tiles) != 3 or any(is_honor(t) for t in tiles):
            raise ValueError("invalid sequence meld")
        if len({t[1] for t in tiles}) != 1 or [int(t[0]) for t in tiles] != list(range(int(tiles[0][0]), int(tiles[0][0]) + 3)):
            raise ValueError("invalid sequence meld")
        group_kind = "sequence"
    elif kind in {"pon", "koutsu", "kantsu", "ankan", "daiminkan", "kakan"}:
        group_kind = "triplet" if kind in {"pon", "koutsu"} else "quad"
        if len(set(tiles)) != 1 or len(tiles) != (3 if group_kind == "triplet" else 4):
            raise ValueError("invalid triplet/quad meld")
    else:
        raise ValueError("unknown meld kind")
    opened = meld["open"]
    if not isinstance(opened, bool):
        raise ValueError("meld open must be boolean")
    if kind in {"chi", "pon", "daiminkan", "kakan"} and not opened or kind == "ankan" and opened:
        raise ValueError("meld open flag contradicts its kind")
    return Group(group_kind, tiles, opened)


def raw_tiles(hand: Mapping[str, Any], winning_tile: str | None = None) -> list[str]:
    result = list(hand["concealed_tiles"])
    result.extend(t for m in hand["melds"] for t in m["tiles"])
    if winning_tile is not None:
        result.append(winning_tile)
    return result


def all_tiles(hand: Mapping[str, Any], winning_tile: str) -> list[str]:
    return [norm(t) for t in raw_tiles(hand, winning_tile)]


def validate_hand(hand: Mapping[str, Any], rules: Mapping[str, Any], winning_tile: str | None = None) -> None:
    groups = [meld_group(m) for m in hand["melds"]]
    if len(groups) > 4:
        raise ValueError("too many melds")
    tiles = raw_tiles(hand, winning_tile)
    if len(tiles) != 13 + sum(g.kind == "quad" for g in groups) + (winning_tile is not None):
        raise ValueError("hand has the wrong physical tile count")
    if any(norm(t) not in TILES or (t.endswith("r") and t not in {"5mr", "5pr", "5sr"}) for t in tiles):
        raise ValueError("unknown tile")
    if any(n > 4 for n in Counter(norm(t) for t in tiles).values()):
        raise ValueError("more than four physical copies of a tile")
    counts = Counter(tiles)
    for suit in "mps":
        reds = rules["red_fives"][suit]
        if counts[f"5{suit}r"] > reds or counts[f"5{suit}"] > 4 - reds:
            raise ValueError("five-tile inventory violates red_fives")


def standard_decompositions(hand: Mapping[str, Any], winning_tile: str) -> list[Decomposition]:
    win = norm(winning_tile)
    fixed = tuple(meld_group(m) for m in hand["melds"])
    results: list[Decomposition] = []

    def search(left: Counter[str], pair: str | None, groups: tuple[Group, ...]) -> None:
        if not left:
            if pair is None or len(groups) + len(fixed) != 4:
                return
            # Each location of the new tile is a distinct wait interpretation.
            if pair == win:
                results.append(Decomposition(pair, fixed + groups, True))
            for i, group in enumerate(groups):
                if win in group.tiles:
                    marked = tuple(replace(g, winning=(j == i)) for j, g in enumerate(groups))
                    results.append(Decomposition(pair, fixed + marked, False))
            return
        tile = min(left)

        def take(tiles: Sequence[str], next_pair: str | None, next_groups: tuple[Group, ...]) -> None:
            needed = Counter(tiles)
            if all(left[t] >= n for t, n in needed.items()):
                search(left - needed, next_pair, next_groups)

        if pair is None:
            take((tile, tile), tile, groups)
        if len(groups) + len(fixed) < 4:
            take((tile,) * 3, pair, groups + (Group("triplet", (tile,) * 3, False),))
            if not is_honor(tile) and int(tile[0]) <= 7:
                seq = tuple(f"{int(tile[0]) + i}{tile[1]}" for i in range(3))
                take(seq, pair, groups + (Group("sequence", seq, False),))

    search(Counter(norm(t) for t in hand["concealed_tiles"]) + Counter([win]), None, ())
    return list(dict.fromkeys(results))


def is_chiitoitsu(tiles: Sequence[str], melds: Sequence[Mapping[str, Any]]) -> bool:
    counts = Counter(norm(t) for t in tiles)
    return not melds and len(tiles) == 14 and len(counts) == 7 and all(n == 2 for n in counts.values())


def is_kokushi(tiles: Sequence[str]) -> bool:
    counts = Counter(norm(t) for t in tiles)
    return len(tiles) == 14 and set(counts) == ORPHANS and sorted(counts.values()) == [1] * 12 + [2]


def is_tenpai(hand: Mapping[str, Any]) -> bool:
    # Availability elsewhere in the wall does not affect tenpai. A fifth copy
    # already inside this hand cannot form a legal physical winning hand.
    counts = Counter(norm(t) for t in raw_tiles(hand))
    for tile in TILES:
        if counts[tile] >= 4:
            continue
        tiles = all_tiles(hand, tile)
        if standard_decompositions(hand, tile) or is_chiitoitsu(tiles, hand["melds"]) or is_kokushi(tiles):
            return True
    return False


def pair_fu(pair: str, actor: int, state: Mapping[str, Any]) -> int:
    return 2 * (int(pair in DRAGONS) + int(pair == WINDS[(actor - state["oya"]) % 4]) + int(pair == state["bakaze"]))


def winning_wait_fu(decomp: Decomposition, winning_tile: str, win_method: str = "ron") -> int:
    if decomp.pair_winning:
        return 2
    for group in decomp.groups:
        if group.winning and group.kind == "sequence":
            rank, start = int(norm(winning_tile)[0]), int(group.tiles[0][0])
            return 2 if rank == start + 1 or (start, rank) in {(1, 3), (7, 7)} else 0
    return 0


def concealed_group(group: Group, method: str) -> bool:
    return not group.open and not (group.winning and method == "ron")


def is_pinfu(decomp: Decomposition, actor: int, state: Mapping[str, Any], winning_tile: str) -> bool:
    return (all(g.kind == "sequence" and not g.open for g in decomp.groups)
            and pair_fu(decomp.pair, actor, state) == 0
            and winning_wait_fu(decomp, winning_tile) == 0)


def fu_for(decomp: Decomposition, state: Mapping[str, Any], winning_tile: str, method: str) -> int:
    closed = not any(g.open for g in decomp.groups)
    if method == "tsumo" and is_pinfu(decomp, state["actor"], state, winning_tile):
        return 20
    fu = 20 + (2 if method == "tsumo" else 10 if closed else 0)
    fu += pair_fu(decomp.pair, state["actor"], state) + winning_wait_fu(decomp, winning_tile)
    for group in decomp.groups:
        if group.kind in {"triplet", "quad"}:
            amount = 2 if group.kind == "triplet" else 8
            fu += amount * (2 if concealed_group(group, method) else 1) * (2 if is_terminal(group.tiles[0]) else 1)
    if method == "ron" and not closed and fu == 20:
        return 30
    return ceil10(fu)


def timing_flags(state: Mapping[str, Any], actor: int) -> dict[str, bool]:
    """Use normalized predicates; complete event history verifies/derives them."""
    flags = {k: bool(state.get(k, False)) for k in ("reach_accepted", "double_riichi", "ippatsu", "first_turn")}
    events = state.get("events", [])
    if events and events[0]["type"] == "start_kyoku":
        called = False
        discarded = False
        flags = dict(reach_accepted=False, double_riichi=False, ippatsu=False, first_turn=True)
        for event in events:
            kind = event["type"]
            if kind in {"chi", "pon", "daiminkan", "ankan", "kakan"}:
                called = True
                flags["ippatsu"] = False
            if kind == "reach" and event.get("actor") == actor:
                flags["double_riichi"] = not discarded and not called
            if kind == "reach_accepted" and event.get("actor") == actor:
                flags["reach_accepted"] = True
                flags["ippatsu"] = True
            if kind == "dahai" and event.get("actor") == actor:
                discarded = True
                flags["ippatsu"] = False
            if kind == "end_kyoku":
                flags["ippatsu"] = False
        flags["first_turn"] = not discarded and not called
        for key, value in flags.items():
            if key in state and state[key] != value:
                raise ValueError(f"{key} contradicts complete event history")
    if (flags["double_riichi"] or flags["ippatsu"]) and not flags["reach_accepted"]:
        raise ValueError("riichi timing predicate without accepted riichi")
    return flags


def dora_next(marker: str) -> str:
    marker = norm(marker)
    if marker not in HONORS:
        return f"{int(marker[0]) % 9 + 1}{marker[1]}"
    if marker in WINDS:
        return WINDS[(WINDS.index(marker) + 1) % 4]
    return {"P": "F", "F": "C", "C": "P"}[marker]


def dora_count(tiles: Sequence[str], marker: str) -> int:
    return sum(norm(t) == dora_next(marker) for t in tiles)


def yaku_for(hand: Mapping[str, Any], state: Mapping[str, Any], winning_tile: str,
             method: str, decomp: Decomposition | None, rules: Mapping[str, Any],
             markers: Sequence[str], ura_markers: Sequence[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    tiles = all_tiles(hand, winning_tile)
    groups = decomp.groups if decomp else ()
    closed = not any(m["open"] for m in hand["melds"])
    actor = state["actor"]
    flags = timing_flags(state, actor)
    if flags["reach_accepted"] and not closed:
        raise ValueError("open hand cannot have accepted riichi")
    doubles = set(rules["double_yakuman"])
    yakuman: list[dict[str, Any]] = []

    def limit(name: str, condition: bool, double: str | None = None) -> None:
        if condition:
            yakuman.append({"id": name, "value": 2 if double in doubles else 1, "unit": "yakuman"})

    counts = Counter(tiles)
    before = Counter(norm(t) for t in hand["concealed_tiles"])
    triplets = {g.tiles[0] for g in groups if g.kind in {"triplet", "quad"}}
    limit("kokushi_musou", is_kokushi(tiles), "kokushi_13_wait" if set(before) == ORPHANS and all(n == 1 for n in before.values()) else None)
    limit("suuankou", bool(decomp) and sum(g.kind in {"triplet", "quad"} and concealed_group(g, method) for g in groups) == 4,
          "suuankou_tanki" if decomp and decomp.pair_winning else None)
    limit("daisangen", set(DRAGONS) <= triplets)
    limit("shousuushii", bool(decomp) and len(set(WINDS) & triplets) == 3 and decomp.pair in WINDS)
    limit("daisuushii", set(WINDS) <= triplets, "daisuushii")
    limit("tsuuiisou", all(is_honor(t) for t in tiles))
    limit("chinroutou", all(is_terminal(t) and not is_honor(t) for t in tiles))
    limit("ryuuiisou", all(t in GREEN_TILES for t in tiles))
    limit("suukantsu", sum(g.kind == "quad" for g in groups) == 4)
    suits = {t[1] for t in tiles if not is_honor(t)}
    if closed and not hand["melds"] and len(suits) == 1 and not any(is_honor(t) for t in tiles):
        suit = next(iter(suits))
        base = Counter({f"{n}{suit}": 3 if n in {1, 9} else 1 for n in range(1, 10)})
        limit("chuuren_poutou", all(counts[t] >= n for t, n in base.items()), "junsei_chuuren" if before == base else None)
    limit("tenhou", method == "tsumo" and closed and flags["first_turn"] and actor == state["oya"])
    limit("chiihou", method == "tsumo" and closed and flags["first_turn"] and actor != state["oya"])

    yakus: list[dict[str, Any]] = []

    def add(name: str, value: int, condition: bool = True) -> None:
        if condition:
            yakus.append({"id": name, "value": value, "unit": "han"})

    if yakuman:
        yakus = yakuman
    else:
        if flags["reach_accepted"]:
            add("double_riichi" if flags["double_riichi"] else "riichi", 2 if flags["double_riichi"] else 1)
        add("ippatsu", 1, flags["ippatsu"])
        add("menzen_tsumo", 1, closed and method == "tsumo")
        add("tanyao", 1, all(is_simple(t) for t in tiles) and (closed or rules["kuitan"]))
        add("honroutou", 2, all(is_terminal(t) for t in tiles))
        if decomp is None:
            add("chiitoitsu", 2, is_chiitoitsu(tiles, hand["melds"]))
        else:
            seqs = Counter((g.tiles[0][1], int(g.tiles[0][0])) for g in groups if g.kind == "sequence")
            pairs = sum(n // 2 for n in seqs.values())
            add("pinfu", 1, is_pinfu(decomp, actor, state, winning_tile))
            if closed and pairs:
                add("ryanpeikou" if pairs == 2 else "iipeikou", 3 if pairs == 2 else 1)
            for tile, name in DRAGONS.items():
                add(name, 1, tile in triplets)
            add("seat_wind", 1, WINDS[(actor - state["oya"]) % 4] in triplets)
            add("round_wind", 1, state["bakaze"] in triplets)
            add("sanshoku_doujun", 2 if closed else 1, any(all((s, n) in seqs for s in "mps") for n in range(1, 8)))
            add("ikkitsuukan", 2 if closed else 1, any(all((s, n) in seqs for n in (1, 4, 7)) for s in "mps"))
            if seqs and is_terminal(decomp.pair) and all(any(is_terminal(t) for t in g.tiles) for g in groups):
                add("chanta" if any(is_honor(t) for t in tiles) else "junchan",
                    (2 if closed else 1) if any(is_honor(t) for t in tiles) else (3 if closed else 2))
            add("toitoi", 2, all(g.kind in {"triplet", "quad"} for g in groups))
            add("sanankou", 2, sum(g.kind in {"triplet", "quad"} and concealed_group(g, method) for g in groups) == 3)
            add("sanshoku_doukou", 2, any(all(f"{n}{s}" in triplets for s in "mps") for n in range(1, 10)))
            add("sankantsu", 2, sum(g.kind == "quad" for g in groups) == 3)
            add("shousangen", 2, len(set(DRAGONS) & triplets) == 2 and decomp.pair in DRAGONS)
        if len(suits) == 1:
            has_honors = any(is_honor(t) for t in tiles)
            add("honitsu" if has_honors else "chinitsu", (3 if closed else 2) if has_honors else (6 if closed else 5))
        events = state.get("events", [])
        last = events[-1] if events else {}
        add("rinshan_kaihou", 1, method == "tsumo" and state["rinshan"])
        pending = state.get("pending_kan")
        add("chankan", 1, method == "ron" and bool(pending))
        last_live = state.get("pre_state", {}).get("wall_remaining") == 1 and state["wall_remaining"] == 0 and not state["rinshan"]
        add("haitei", 1, method == "tsumo" and last_live)
        add("houtei", 1, method == "ron" and last_live and last.get("type") == "dahai" and not pending)

    bonuses = []
    if not yakuman:
        if ura_markers and not flags["reach_accepted"]:
            raise ValueError("ura dora without accepted riichi")
        physical = raw_tiles(hand, winning_tile)
        for name, count in (("dora", sum(dora_count(physical, m) for m in markers)),
                            ("uradora", sum(dora_count(physical, m) for m in ura_markers)),
                            ("akadora", sum(t.endswith("r") for t in physical))):
            if count:
                bonuses.append({"id": name, "han": count})
    pao_by_yaku = {}
    for event in state.get("events", []):
        if event.get("type") == "pao" and event.get("actor") == actor:
            name, liable = event["yaku_id"], event["liable_seat"]
            if name in pao_by_yaku or liable == actor:
                raise ValueError("invalid pao history")
            pao_by_yaku[name] = liable
    scored = {y["id"] for y in yakus}
    pao = [{"yaku_id": name, "liable_seat": seat} for name, seat in sorted(pao_by_yaku.items())
           if name in scored and name in rules["pao"]["yakus"]]
    return sorted(yakus, key=lambda y: y["id"]), bonuses, pao


def basic_points(fu: int, han: int, rules: Mapping[str, Any], overrides: Mapping[str, Any] | None = None) -> int:
    rules = dict(rules) | dict(overrides or {})
    if han >= 13:
        return 8000 if rules["kazoe_yakuman"] == "yakuman" else 6000
    if han >= 11:
        return 6000
    if han >= 8:
        return 4000
    if han >= 6:
        return 3000
    if han >= 5 or han == 4 and fu >= 40 or han == 3 and fu >= 70:
        return 2000
    if rules["kiriage_mangan"] and (han == 4 and fu >= 30 or han == 3 and fu >= 60):
        return 2000
    return fu * 2 ** (han + 2)


def normal_payments(actor: int, target: int, method: str, basic: int, oya: int) -> dict[int, int]:
    if method == "ron":
        return {target: ceil100(basic * (6 if actor == oya else 4))}
    return {seat: ceil100(basic * (2 if actor == oya or seat == oya else 1)) for seat in SEATS if seat != actor}


def compute_win(input_data: Mapping[str, Any], state: Mapping[str, Any], rules: Mapping[str, Any]) -> dict[str, Any]:
    hand, tile = input_data["hand"], input_data["winning_tile"]
    actor, target, method = input_data["actor"], input_data["target"], input_data["win_method"]
    if actor not in SEATS or target not in SEATS or method not in {"ron", "tsumo"} or (actor == target) != (method == "tsumo"):
        raise ValueError("invalid winner/target/method")
    validate_hand(hand, rules, tile)
    state = dict(state, actor=actor)
    candidates: list[Decomposition | None] = list(standard_decompositions(hand, tile))
    if is_chiitoitsu(all_tiles(hand, tile), hand["melds"]) or is_kokushi(all_tiles(hand, tile)):
        candidates.append(None)
    markers, ura_markers = input_data.get("dora_markers", []), input_data.get("ura_dora_markers", [])
    if len(markers) > 5 or len(ura_markers) > 5 or any(norm(t) not in TILES for t in list(markers) + list(ura_markers)):
        raise ValueError("invalid dora markers")
    best = None
    for decomp in candidates:
        yakus, bonuses, pao = yaku_for(hand, state, tile, method, decomp, rules, markers, ura_markers)
        if not yakus:
            continue
        yakuman = sum(y["value"] for y in yakus if y["unit"] == "yakuman")
        han = 0 if yakuman else sum(y["value"] for y in yakus) + sum(b["han"] for b in bonuses)
        fu = 0 if yakuman else 25 if decomp is None else fu_for(decomp, state, tile, method)
        basic = 8000 * yakuman if yakuman else basic_points(fu, han, rules)
        payments = normal_payments(actor, target, method, basic, state["oya"])
        points = sum(payments.values())
        key = (points, han, fu, tuple(y["id"] for y in yakus))
        if best is None or key > best[0]:
            best = (key, yakus, bonuses, pao, fu, han, basic, points, payments)
    if best is None:
        raise ValueError("no legal winning decomposition with a yaku")
    _, yakus, bonuses, pao, fu, han, basic, points, payments = best
    # Responsibility applies to each liable yakuman component, not unrelated
    # stacked yakuman. Payer transfers are aggregated after each component.
    if pao:
        values = {y["id"]: y["value"] for y in yakus}
        for liability in pao:
            liable_basic = 8000 * values[liability["yaku_id"]]
            normal = normal_payments(actor, target, method, liable_basic, state["oya"])
            liable_points = sum(normal.values())
            liable = liability["liable_seat"]
            adjusted = normal
            if method == "ron":
                if rules["pao"]["ron"] == "liable_all" or liable == target:
                    adjusted = {liable: liable_points}
                else:
                    half = ceil100(liable_points // 2)
                    adjusted = {liable: half, target: liable_points - half}
            elif rules["pao"]["tsumo"] == "liable_all":
                adjusted = {liable: liable_points}
            for seat, amount in normal.items():
                payments[seat] -= amount
            for seat, amount in adjusted.items():
                payments[seat] = payments.get(seat, 0) + amount
    entries = [{"from": seat, "to": actor, "points": amount} for seat, amount in sorted(payments.items()) if amount]
    return dict(actor=actor, target=target, winning_tile=tile, yakus=yakus, bonuses=bonuses,
                pao=pao, ura_dora_markers=list(ura_markers), fu=fu, han=han,
                basic_points=basic, hand_points=points, payments=entries,
                kyotaku_points=0, deltas=settlement_deltas(entries))


def settlement_deltas(payments: Sequence[Mapping[str, int]]) -> list[int]:
    deltas = [0, 0, 0, 0]
    for payment in payments:
        deltas[payment["from"]] -= payment["points"]
        deltas[payment["to"]] += payment["points"]
    return deltas


def allocate_honba(win: dict[str, Any], amount: int) -> None:
    if not amount:
        return
    # Proportional allocation in 100-point units; distribute residual units by
    # descending fractional remainder, then ascending payer seat.
    entries = win["payments"]
    total = win["hand_points"]
    units = amount // 100
    shares = [(units * p["points"]) // total for p in entries]
    order = sorted(range(len(entries)), key=lambda i: (-(units * entries[i]["points"] % total), entries[i]["from"]))
    for i in order[:units - sum(shares)]:
        shares[i] += 1
    for payment, share in zip(entries, shares):
        payment["points"] += share * 100


def compute_fixture(fixture: Mapping[str, Any], root_rules: Mapping[str, Any]) -> dict[str, Any]:
    rules = dict(root_rules) | dict(fixture.get("rule_overrides", {}))
    data, state = fixture["input"], fixture["state"]
    kind = data["result_type"]
    if kind == "ryukyoku":
        hands = data["hands"]
        if len(hands) != 4:
            raise ValueError("draw requires four hands")
        for hand in hands:
            validate_hand(hand, rules)
        tenpai = [is_tenpai(hand) for hand in hands]
        count, total = sum(tenpai), rules["noten_payment"]["total_points"]
        deltas = [0] * 4 if count in {0, 4} else [total // count if t else -total // (4 - count) for t in tenpai]
        return dict(result_type="ryukyoku", reason="fanpai", tenpai=tenpai, deltas=deltas)
    if kind == "penalty":
        offender, penalty = data["offender"], rules["chombo"]["penalty_points"]
        if offender not in SEATS:
            raise ValueError("invalid offender")
        others = [s for s in SEATS if s != offender]
        share = penalty // 300 * 100
        payments = [{"from": offender, "to": s, "points": share + (penalty - 3 * share if s == others[0] else 0)} for s in others]
        return dict(result_type="penalty", offender=offender, payments=payments, deltas=settlement_deltas(payments))
    if kind != "hora":
        raise ValueError("unknown result_type")
    inputs = [data] + list(data.get("other_winners", []))
    if len(inputs) > 1:
        if len({w["actor"] for w in inputs}) != len(inputs) or len({w["target"] for w in inputs}) != 1 or any(w["win_method"] != "ron" for w in inputs):
            raise ValueError("invalid multiple ron")
        if rules["ron_policy"] == "head_bump" or len(inputs) == 3 and rules["ron_policy"] == "double_only":
            raise ValueError("winners violate ron_policy")
    wins = []
    for winner in inputs:
        winner_state = winner.get("state", state)
        if any(winner_state.get(k, 0) != state.get(k, 0) for k in ("honba", "kyotaku")):
            raise ValueError("winner settlement counters differ")
        wins.append(compute_win(winner, winner_state, rules))
    wins.sort(key=lambda w: w["actor"])
    first = min(range(len(wins)), key=lambda i: (wins[i]["actor"] - wins[i]["target"]) % 4)
    honba, sticks = state.get("honba", 0), state.get("kyotaku", 0) * rules["riichi_stick_value"]
    kyotaku = [0] * len(wins)
    if rules["multiple_ron_settlement"]["kyotaku"] == "equal_split":
        kyotaku = [sticks // (100 * len(wins)) * 100] * len(wins)
    kyotaku[first] += sticks - sum(kyotaku)
    for i, win in enumerate(wins):
        tsumo = win["actor"] == win["target"]
        if tsumo:
            extra = honba * rules["honba_tsumo_value_per_payer"]
            if win["pao"] and rules["pao"]["tsumo"] == "liable_all":
                allocate_honba(win, extra * 3)
            else:
                for p in win["payments"]:
                    p["points"] += extra
        elif len(wins) == 1 or rules["multiple_ron_settlement"]["honba"] == "each_winner" or i == first:
            allocate_honba(win, honba * rules["honba_ron_value"])
        win["kyotaku_points"] = kyotaku[i]
        win["deltas"] = settlement_deltas(win["payments"])
        win["deltas"][win["actor"]] += kyotaku[i]
    return dict(result_type="hora", wins=wins, deltas=[sum(w["deltas"][s] for w in wins) for s in SEATS])


def summary_vectors(vectors: Sequence[Mapping[str, Any]], rules: Mapping[str, Any]) -> list[str]:
    errors = []
    for vector in vectors:
        if "basic_points" not in vector:
            # Descriptive coverage entries are checked by the fixture suite.
            continue
        applied = dict(rules) | dict(vector.get("rule_overrides", {}))
        basic = 8000 * vector["yakuman_value"] if "yakuman_value" in vector else basic_points(vector["fu"], vector["han"], applied)
        if basic != vector["basic_points"]:
            errors.append(f"{vector['id']}: incorrect basic_points")
        if "hand_points" in vector:
            actor = 0 if vector["dealer"] else 1
            method = "tsumo" if vector["tsumo"] else "ron"
            points = sum(normal_payments(actor, actor if method == "tsumo" else (actor + 1) % 4, method, basic, 0).values())
            if points != vector["hand_points"]:
                errors.append(f"{vector['id']}: incorrect hand_points")
    return errors


def run(path: Path, print_json: bool = False) -> int:
    data = json.loads(path.read_text())
    errors, computed = [], {}
    for fixture in data["fixtures"]:
        identifier = fixture["id"]
        try:
            actual = compute_fixture(fixture, data["rules"])
            computed[identifier] = actual
            if actual != fixture["expected"]:
                errors.append(f"{identifier}: expected={json.dumps(fixture['expected'], sort_keys=True)} actual={json.dumps(actual, sort_keys=True)}")
        except (ValueError, KeyError, TypeError) as exc:
            errors.append(f"{identifier}: {exc}")
    errors.extend(summary_vectors(data["vectors"], data["rules"]))
    if print_json:
        print(json.dumps(computed, ensure_ascii=False, indent=2))
    for error in errors:
        print(error, file=sys.stderr)
    print(f"score oracle: {len(computed)} fixtures and {len(data['vectors'])} summary vectors; {len(errors)} failures")
    return int(bool(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--print", dest="print_json", action="store_true")
    args = parser.parse_args()
    return run(args.path, args.print_json)


if __name__ == "__main__":
    raise SystemExit(main())
