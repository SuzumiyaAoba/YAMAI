"""YAMAI specification section 7.6 scoring reference, independent of expected fixtures.

All uncommitted concealed tiles are decomposed; only actual calls and ankan
are fixed melds. The normalized context carries history-dependent facts.
This is a scoring oracle, not a transport or complete game implementation.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from typing import Any


TILES = tuple(f"{n}{s}" for s in "mps" for n in range(1, 10)) + tuple("ESWNPFC")
ORPHANS = frozenset((0, 8, 9, 17, 18, 26, 27, 28, 29, 30, 31, 32, 33))
TERMINALS = frozenset((0, 8, 9, 17, 18, 26))
GREENS = frozenset((19, 20, 21, 23, 25, 32))
DRAGONS = frozenset((31, 32, 33))
WINDS = frozenset((27, 28, 29, 30))
MAX_INTEGER = 2**53 - 1
MAX_GAME_EVENTS = 100_000
# All twelve registered yakuman plus all four double conditions, even though
# they cannot all coexist: 16 * 8000 basic points * 6 for a dealer ron.
MAX_HAND_POINTS = 768_000

# Public role values from §7.6.3; checked against the release registry.
NORMAL_YAKU_HAN = {
    "riichi": (1, None), "double_riichi": (2, None), "ippatsu": (1, None),
    "menzen_tsumo": (1, None), "tanyao": (1, 1), "pinfu": (1, None),
    "iipeikou": (1, None), "yakuhai_haku": (1, 1), "yakuhai_hatsu": (1, 1),
    "yakuhai_chun": (1, 1), "seat_wind": (1, 1), "round_wind": (1, 1),
    "rinshan_kaihou": (1, 1), "chankan": (1, 1), "haitei": (1, 1), "houtei": (1, 1),
    "sanshoku_doujun": (2, 1), "ikkitsuukan": (2, 1), "chanta": (2, 1),
    "chiitoitsu": (2, None), "toitoi": (2, 2), "sanankou": (2, 2),
    "honroutou": (2, 2), "sanshoku_doukou": (2, 2), "sankantsu": (2, 2),
    "shousangen": (2, 2), "honitsu": (3, 2), "junchan": (3, 2),
    "ryanpeikou": (3, None), "chinitsu": (6, 5),
}
DOUBLE_YAKUMAN_CONDITIONS = {
    "kokushi_musou": "kokushi_13_wait", "suuankou": "suuankou_tanki",
    "chuuren_poutou": "junsei_chuuren", "daisuushii": "daisuushii",
}
# Minimum logical meld requirements of the public standard-yaku claims.
# Maxima are used because a single meld may establish several different yaku.
YAKU_MIN_SEQUENCES = {
    "pinfu": 4, "iipeikou": 2, "ryanpeikou": 4,
    "sanshoku_doujun": 3, "ikkitsuukan": 3, "chanta": 1, "junchan": 1,
}
YAKU_MIN_TRIPLETS = {
    "toitoi": 4, "sanankou": 3, "sanshoku_doukou": 3, "sankantsu": 3,
    "shousangen": 2, "yakuhai_haku": 1, "yakuhai_hatsu": 1,
    "yakuhai_chun": 1, "seat_wind": 1, "round_wind": 1,
    "daisangen": 3, "shousuushii": 3, "daisuushii": 4,
    "suuankou": 4, "suukantsu": 4, "chinroutou": 4,
}
YAKUMAN_IDS = frozenset((
    "kokushi_musou", "suuankou", "daisangen", "shousuushii", "daisuushii",
    "tsuuiisou", "chinroutou", "ryuuiisou", "chuuren_poutou", "suukantsu",
    "tenhou", "chiihou",
))
YAKU_ALLOWED_TILES = {
    "tanyao": frozenset(range(34)) - ORPHANS,
    "honroutou": ORPHANS, "kokushi_musou": ORPHANS,
    "junchan": frozenset(range(27)), "chinitsu": frozenset(range(27)),
    "chuuren_poutou": frozenset(range(27)),
    "tsuuiisou": WINDS | DRAGONS, "chinroutou": TERMINALS, "ryuuiisou": GREENS,
}


def allowed_yaku_tiles(ids: set[str]) -> set[int]:
    allowed = set(range(34))
    for name in ids:
        allowed.intersection_update(YAKU_ALLOWED_TILES.get(name, range(34)))
    return allowed


class ScoringError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise ScoringError(code, message)


def validate_win_declarations(win: dict, rules: dict | None = None, *, closed: bool | None = None) -> None:
    """Check public declarations, without guessing a hidden hand's shape.

    Schemas check individual role objects; uniqueness is by ID even when
    annotations or the two allowed han values make those objects different.
    Rules/closed status add constraints only when that context is available.
    """
    for field, key in (("yakus", "id"), ("bonuses", "id"), ("pao", "yaku_id")):
        ids = [item[key] for item in win[field]]
        require(ids == sorted(set(ids)), "invalid_message", f"{field} IDs must be sorted and unique")
    ids = {item["id"] for item in win["yakus"]}
    for left, right in (("riichi", "double_riichi"), ("iipeikou", "ryanpeikou"),
                        ("honitsu", "chinitsu"), ("chanta", "junchan"),
                        ("shousuushii", "daisuushii"), ("tenhou", "chiihou"),
                        ("ippatsu", "rinshan_kaihou")):
        require(not {left, right} <= ids, "invalid_message", "mutually exclusive yaku declarations")
    for special in ("kokushi_musou", "chuuren_poutou"):
        require(special not in ids or not ids & (YAKUMAN_IDS - {special, "tenhou", "chiihou"}),
                "invalid_message", "special yakuman cannot combine with another hand shape")
    require(not (ids & {"tenhou", "chiihou"} and "suukantsu" in ids),
            "invalid_message", "first-draw yakuman cannot follow four kans")
    require(not ("daisangen" in ids and ids & {"shousuushii", "daisuushii"}),
            "invalid_message", "dragon and wind yakuman require more than four melds")
    allowed = allowed_yaku_tiles(ids)
    require(len(allowed) * 4 >= 14, "invalid_message", "yaku tile restrictions cannot form a hand")
    require("daisangen" not in ids or DRAGONS <= allowed, "invalid_message",
            "big three dragons conflicts with the allowed tiles")
    require(not ids & {"shousuushii", "daisuushii"} or WINDS <= allowed, "invalid_message",
            "four winds conflicts with the allowed tiles")
    if not any(yaku["unit"] == "yakuman" for yaku in win["yakus"]):
        sequences = max((YAKU_MIN_SEQUENCES.get(name, 0) for name in ids), default=0)
        triplets = max((YAKU_MIN_TRIPLETS.get(name, 0) for name in ids), default=0)
        dragon_triplets = max(len(ids & {"yakuhai_haku", "yakuhai_hatsu", "yakuhai_chun"}),
                              2 if "shousangen" in ids else 0)
        wind_triplets = int(bool(ids & {"seat_wind", "round_wind"}))
        triplets = max(triplets, dragon_triplets + wind_triplets
                       + (3 if "sanshoku_doukou" in ids else 0))
        require(sequences + triplets <= 4, "invalid_message", "yaku claims require more than four melds")
        require("chiitoitsu" not in ids or sequences + triplets == 0, "invalid_message",
                "seven pairs cannot combine with meld-based yaku")
        require("honroutou" not in ids or sequences == 0, "invalid_message",
                "all terminals and honors cannot contain a sequence")
        honors = {"yakuhai_haku", "yakuhai_hatsu", "yakuhai_chun", "seat_wind",
                  "round_wind", "shousangen", "chanta", "honitsu"}
        require(not (ids & {"tanyao", "junchan", "chinitsu"} and ids & honors),
                "invalid_message", "yaku claims both require and exclude honors")
        require("tanyao" not in ids or not ids & {"junchan", "honroutou", "ikkitsuukan"},
                "invalid_message", "all simples cannot contain required terminals")
        require(not (ids & {"honitsu", "chinitsu"} and ids & {"sanshoku_doujun", "sanshoku_doukou"}),
                "invalid_message", "one-suit and three-suit yaku cannot combine")
        require("ryanpeikou" not in ids or not ids & {"sanshoku_doujun", "ikkitsuukan"},
                "invalid_message", "two sequence pairs cannot contain three distinct required sequences")
        require("ikkitsuukan" not in ids or not ids & {"sanshoku_doujun", "chanta", "junchan"},
                "invalid_message", "full straight conflicts with the other required sequences")
        tsumo = win["actor"] == win["target"]
        require((win["fu"] == 25) == ("chiitoitsu" in ids), "invalid_message",
                "25 fu is reserved for seven pairs")
        require(win["fu"] != 20 or (tsumo and "pinfu" in ids), "invalid_message",
                "20 fu is reserved for pinfu tsumo")
        require("pinfu" not in ids or win["fu"] == (20 if tsumo else 30), "invalid_message",
                "pinfu fu differs from win method")
    for yaku in win["yakus"]:
        name, value = yaku["id"], yaku["value"]
        if name in NORMAL_YAKU_HAN:
            choices = NORMAL_YAKU_HAN[name]
            require(yaku["unit"] == "han" and value in (choices if closed is None else (choices[0 if closed else 1],)),
                    "invalid_message", "yaku value differs from the public open/closed state")
        if rules is not None:
            require(not name.startswith("x_") or name in rules["local_yaku"],
                    "invalid_message", "result uses an unconfigured local yaku")
            if name in DOUBLE_YAKUMAN_CONDITIONS and value == 2:
                require(DOUBLE_YAKUMAN_CONDITIONS[name] in rules["double_yakuman"],
                        "invalid_message", "double yakuman condition is disabled")
            if name == "daisuushii":
                require(value == (2 if "daisuushii" in rules["double_yakuman"] else 1),
                        "invalid_message", "big four winds multiplier differs from the enabled rule")
            if name == "suuankou" and win["actor"] != win["target"]:
                require(value == (2 if "suuankou_tanki" in rules["double_yakuman"] else 1),
                        "invalid_message", "four concealed triplets by ron require the tanki multiplier")
            require(name != "tanyao" or closed is not False or rules["kuitan"],
                    "invalid_message", "open tanyao is disabled")


def score_magnitude_bound(rules: dict[str, Any]) -> int:
    """Conservative absolute score bound over the complete core event budget.

    Honba and deposits cannot exceed the event count. A settlement has at
    most three winners (or three tsumo payers); each seat's absolute delta
    is bounded by the total transfer plus all deposits. Python integers keep
    this admission check exact even when an offered rule fails the bound.
    """
    events = MAX_GAME_EVENTS
    stick = rules["riichi_stick_value"]
    honba = max(rules["honba_ron_value"], rules["honba_tsumo_value_per_payer"])
    delta = max(stick, rules["noten_payment"]["total_points"],
                rules["chombo"]["penalty_points"],
                3 * MAX_HAND_POINTS + events * (3 * honba + stick))
    return rules["starting_points"] + events * delta


def validate_score_bounds(rules: dict[str, Any]) -> None:
    require(score_magnitude_bound(rules) <= MAX_INTEGER, "invalid_message",
            "rules cannot guarantee scores within the wire integer range")


def tile_index(tile: str) -> int:
    require(tile in TILES or tile in ("5mr", "5pr", "5sr"), "invalid_hand", "unknown tile")
    return TILES.index(tile[:-1] if tile.endswith("r") else tile)


def inventory(tiles: list[str], rules: dict[str, Any]) -> None:
    kinds = Counter(tile_index(tile) for tile in tiles)
    physical = Counter(tiles)
    require(all(n <= 4 for n in kinds.values()), "invalid_hand", "more than four copies of a tile")
    for suit in "mps":
        red = rules["red_fives"][suit]
        require(physical[f"5{suit}r"] <= red and physical[f"5{suit}"] <= 4 - red,
                "invalid_hand", "red/ordinary five inventory exceeded")


@dataclass(frozen=True)
class Meld:
    kind: str  # sequence, triplet, quad
    base: int
    open: bool

    def tiles(self) -> tuple[int, ...]:
        return tuple(range(self.base, self.base + 3)) if self.kind == "sequence" else (self.base,) * (4 if self.kind == "quad" else 3)


def hand_parts(hand: dict[str, Any], rules: dict[str, Any], actor: int | None = None) -> tuple[tuple[int, ...], tuple[Meld, ...], list[str]]:
    concealed = hand["concealed_tiles"]
    raw_melds = hand["melds"]
    require(len(raw_melds) <= 4 and len(concealed) + 3 * len(raw_melds) == 13,
            "invalid_hand", "hand must have thirteen logical tiles before winning")
    fixed = []
    physical = list(concealed)
    for raw in raw_melds:
        kind = raw["kind"]
        require(kind in {"chi", "pon", "daiminkan", "ankan", "kakan"}, "invalid_hand", "only committed melds may be fixed")
        opened = kind != "ankan"
        require(raw["open"] is opened, "invalid_hand", "meld open flag differs")
        if opened:
            require(type(raw.get("source")) is int and 0 <= raw["source"] < 4 and raw["source"] != actor,
                    "invalid_hand", "open meld source is required and differs from actor")
            if kind == "chi" and actor is not None:
                require(raw["source"] == (actor + 3) % 4, "invalid_hand", "chi is not from the previous seat")
        else:
            require("source" not in raw, "invalid_hand", "ankan has no discarder")
        indices = sorted(tile_index(tile) for tile in raw["tiles"])
        if kind == "chi":
            require(len(indices) == 3 and indices[0] < 27 and indices[0] % 9 <= 6 and indices == list(range(indices[0], indices[0] + 3)),
                    "invalid_hand", "chi is not a same-suit sequence")
            m = Meld("sequence", indices[0], True)
        else:
            quad = kind in {"ankan", "daiminkan", "kakan"}
            require(len(indices) == (4 if quad else 3) and len(set(indices)) == 1, "invalid_hand", "meld is not a triplet or quad")
            m = Meld("quad" if quad else "triplet", indices[0], opened)
        fixed.append(m)
        physical.extend(raw["tiles"])
    inventory(physical, rules)
    counts = Counter(tile_index(tile) for tile in concealed)
    return tuple(counts.get(i, 0) for i in range(34)), tuple(fixed), physical


@lru_cache(maxsize=65536)
def _groups(counts: tuple[int, ...], remaining: int) -> tuple[tuple[Meld, ...], ...]:
    if sum(counts) != 3 * remaining:
        return ()
    if remaining == 0:
        return ((),)
    base = next(i for i,n in enumerate(counts) if n)
    options = []
    if counts[base] >= 3:
        options.append(Meld("triplet", base, False))
    if base < 27 and base % 9 <= 6 and counts[base + 1] and counts[base + 2]:
        options.append(Meld("sequence", base, False))
    result = []
    for meld in options:
        rest = list(counts)
        for tile in meld.tiles():
            rest[tile] -= 1
        for tail in _groups(tuple(rest), remaining - 1):
            result.append((meld, *tail))
    return tuple(result)


def shapes(counts: tuple[int, ...], fixed: tuple[Meld, ...]) -> list[tuple[str, int, tuple[Meld, ...]]]:
    result = []
    if not fixed and sum(counts) == 14:
        if sum(n == 2 for n in counts) == 7:
            result.append(("chiitoitsu", -1, ()))
        if all(counts[i] for i in ORPHANS) and sum(counts[i] for i in ORPHANS) == 14:
            result.append(("kokushi", -1, ()))
    for pair,n in enumerate(counts):
        if n >= 2:
            rest = list(counts)
            rest[pair] -= 2
            for groups in _groups(tuple(rest), 4 - len(fixed)):
                result.append(("standard", pair, groups))
    return result


def waits(hand: dict[str, Any], rules: dict[str, Any]) -> set[int]:
    counts, fixed, physical = hand_parts(hand, rules)
    all_counts = Counter(tile_index(tile) for tile in physical)
    result = set()
    for tile in range(34):
        if all_counts[tile] == 4:
            continue
        complete = list(counts)
        complete[tile] += 1
        if shapes(tuple(complete), fixed):
            result.add(tile)
    return result


def basic_points(fu: int, han: int, yakuman: int, rules: dict[str, Any]) -> int:
    if yakuman:
        return 8000 * yakuman
    require(han > 0, "no_yaku", "no yaku before bonuses")
    if han >= 13:
        return 8000 if rules["kazoe_yakuman"] == "yakuman" else 6000
    if han >= 11:
        return 6000
    if han >= 8:
        return 4000
    if han >= 6:
        return 3000
    if han >= 5 or (han == 4 and fu >= 40) or (han == 3 and fu >= 70):
        return 2000
    if rules["kiriage_mangan"] and ((han == 4 and fu >= 30) or (han == 3 and fu >= 60)):
        return 2000
    return fu * 2 ** (han + 2)


def ceil100(value: int) -> int:
    return (value + 99) // 100 * 100


def normal_payments(basic: int, actor: int, target: int, oya: int) -> dict[int, int]:
    if actor != target:
        return {target: ceil100(basic * (6 if actor == oya else 4))}
    return {seat: ceil100(basic * (2 if actor == oya or seat == oya else 1)) for seat in range(4) if seat != actor}


def validate_state_projection(state: dict[str, Any], actor: int, rules: dict[str, Any]) -> None:
    """Replay the scoring-relevant event projection from explicit prior facts."""
    observed = deepcopy(state["pre_state"])
    declared_double = False
    declared = False
    rinshan_actor = None
    for event in state["events"]:
        require(isinstance(event, dict) and "type" in event and "actor" in event, "invalid_message", "projection event lacks type or actor")
        kind = event["type"]
        seat = event["actor"]
        if kind == "reach":
            if seat == actor:
                require(not observed["reach_accepted"] and not declared, "invalid_context", "duplicate riichi declaration")
                declared = True
                declared_double = observed["first_turn"]
        elif kind == "reach_accepted":
            require(observed["scores"][seat] >= rules["riichi_stick_value"], "invalid_context", "riichi deposit exceeds the player's score")
            observed["scores"][seat] -= rules["riichi_stick_value"]
            observed["kyotaku"] += 1
            if seat == actor:
                require(declared and not observed["reach_accepted"], "invalid_context", "riichi acceptance without declaration")
                observed.update(reach_accepted=True,double_riichi=declared_double,ippatsu=True)
                declared = False
        elif kind == "dahai":
            if seat == actor:
                observed["first_turn"] = False
                observed["ippatsu"] = False
            observed["rinshan"] = False
        elif kind in {"ankan_declared", "kakan_declared"}:
            require(observed["pending_kan"] is None, "invalid_context", "another kan is already pending")
            require("pai" in event, "invalid_message", "projected kan declaration lacks its tile")
            if kind == "ankan_declared":
                require(not event["pai"].endswith("r"), "invalid_context", "projected ankan tile is not red-normalized")
            observed["pending_kan"] = {"kind":"ankan" if kind=="ankan_declared" else "kakan","actor":seat,"pai":event["pai"]}
        elif kind in {"chi", "pon", "daiminkan", "ankan", "kakan"}:
            observed.update(first_turn=False,ippatsu=False,last_tile=False)
            if kind in {"daiminkan", "ankan", "kakan"}:
                if kind != "daiminkan":
                    require(observed["pending_kan"] is not None and observed["pending_kan"]["kind"]==kind and observed["pending_kan"]["actor"]==seat,
                            "invalid_context", "kan commit without matching declaration")
                require(observed["wall_remaining"]>0 and sum(observed["kan_counts"])<4,"invalid_context","kan has no replacement tile")
                observed["kan_counts"][seat] += 1
                observed["wall_remaining"] -= 1
                observed["pending_kan"] = None
                rinshan_actor = seat
        elif kind == "tsumo":
            rinshan = rinshan_actor == seat
            if rinshan_actor is not None:
                require(rinshan, "invalid_context", "rinshan draw belongs to another seat")
            if not rinshan:
                require(observed["wall_remaining"]>0, "invalid_context", "draw from an empty live wall")
                observed["wall_remaining"] -= 1
            observed["rinshan"] = rinshan and seat == actor
            observed["last_tile"] = not rinshan and observed["wall_remaining"] == 0
            rinshan_actor = None
        else:
            raise ScoringError("invalid_context", "unsupported scoring projection event")
    # Furiten additionally depends on choices and full discard history, which
    # this event projection does not carry. It is an explicit final-state fact.
    expected = {key:value for key,value in state.items() if key not in {"events", "pre_state", "furiten"}}
    require(observed == expected, "invalid_context", "state does not follow its scoring event projection")


def _context(data: dict[str, Any], state: dict[str, Any], fixed: tuple[Meld, ...], physical: list[str], rules: dict[str, Any]) -> None:
    actor, target = data["actor"], data["target"]
    require(data["win_method"] in {"ron", "tsumo"} and ((data["win_method"] == "tsumo") == (actor == target)), "invalid_context", "win method and target differ")
    require(not state["furiten"] or data["win_method"] == "tsumo", "invalid_context", "ron while furiten")
    closed = not any(m.open for m in fixed)
    require(not state["reach_accepted"] or closed, "invalid_context", "open hand declared riichi")
    require(not state["reach_accepted"] or state["kyotaku"]>=1, "invalid_context", "accepted riichi deposit is missing")
    require(not (state["double_riichi"] or state["ippatsu"]) or state["reach_accepted"], "invalid_context", "riichi qualification without accepted riichi")
    require(not state["first_turn"] or (not fixed and not state["reach_accepted"] and not state["rinshan"]), "invalid_context", "first turn qualification contradicts calls/riichi")
    require(not state["first_turn"] or not any(state["kan_counts"]),
            "invalid_context", "first turn qualification survives a committed kan")
    if state["first_turn"] and data["win_method"] == "tsumo":
        require(state["wall_remaining"] == 69 - (actor - state["oya"]) % 4, "invalid_context", "first draw wall count differs")
    require(not state["rinshan"] or (actor == target and any(m.kind == "quad" for m in fixed)), "invalid_context", "rinshan without own kan/tsumo")
    require(not (state["rinshan"] and state["ippatsu"]),
            "invalid_context", "ippatsu survives the kan preceding a rinshan win")
    require(not state["last_tile"] or (state["wall_remaining"] == 0 and not state["rinshan"] and state["pending_kan"] is None), "invalid_context", "last live tile qualification differs")
    pending = state["pending_kan"]
    if pending:
        require(data["win_method"] == "ron" and pending["actor"] == target and pending["pai"] == data["winning_tile"], "invalid_context", "chankan target/tile differs")
        require(state["wall_remaining"] > 0 and sum(state["kan_counts"]) < 4,
                "invalid_context", "robbed declaration has no live-wall or kan capacity")
        require(tile_index(data["winning_tile"]) not in {tile_index(t) for t in physical}, "invalid_hand", "opponent's kan and own tile exceed four copies")
    if state["events"]:
        source = state["events"][-1]
        expected_kind = "tsumo" if data["win_method"] == "tsumo" else pending["kind"] + "_declared" if pending else "dahai"
        require(source["type"] == expected_kind and source["actor"] == target and source.get("pai") == data["winning_tile"],
                "invalid_context", "winning tile differs from the final projected source event")
    dora, ura = data["dora_markers"], data["ura_dora_markers"]
    if pending:
        # All four copies belong to the declarer's hand/pon. The virtual
        # winning tile is one of those copies, not a fifth physical tile.
        require(all(tile_index(t) != tile_index(data["winning_tile"]) for t in [*dora, *ura]),
                "invalid_hand", "opponent's kan and indicator exceed four copies")
    kan_counts = state["kan_counts"]
    require(kan_counts[actor] == sum(m.kind == "quad" for m in fixed) and sum(kan_counts) <= 4,
            "invalid_context", "kan counts differ from committed melds")
    require(len(dora) == 1 + sum(kan_counts), "invalid_context", "scoring needs the revealed marker for each committed kan")
    require((len(ura) == len(dora)) if state["reach_accepted"] else not ura, "invalid_context", "ura markers differ from riichi state")
    inventory([*physical, data["winning_tile"], *dora, *ura], rules)


def score_hand(data: dict[str, Any], state: dict[str, Any], rules: dict[str, Any]) -> dict[str, Any]:
    pre, fixed, physical = hand_parts(data["hand"], rules, data["actor"])
    _context(data, state, fixed, physical, rules)
    win_tile = tile_index(data["winning_tile"])
    complete = list(pre)
    complete[win_tile] += 1
    all_tiles = [tile_index(t) for t in [*physical, data["winning_tile"]]]
    all_counts = Counter(all_tiles)
    present = set(all_tiles)
    closed = not any(m.open for m in fixed)
    tsumo = data["win_method"] == "tsumo"
    seat_wind = 27 + (data["actor"] - state["oya"]) % 4
    round_wind = tile_index(state["bakaze"])
    candidates = []
    forms = shapes(tuple(complete), fixed)
    require(bool(forms), "invalid_hand", "no complete hand shape")
    if state["pending_kan"] and state["pending_kan"]["kind"] == "ankan":
        require(rules["ankan_chankan"] == "kokushi_only" and any(form == "kokushi" for form,pair,groups in forms),
                "invalid_context", "ankan may only be robbed by permitted kokushi")
    for form, pair, concealed_groups in forms:
        allocations = [-1] if form != "standard" else ([i for i,m in enumerate(concealed_groups) if win_tile in m.tiles()] + ([-1] if pair == win_tile else []))
        for allocation in allocations:
            groups = (*concealed_groups, *fixed)
            sequences = Counter(m.base for m in groups if m.kind == "sequence")
            triplets = {m.base for m in groups if m.kind != "sequence"}
            concealed_count = sum(not m.open and m.kind != "sequence" and not (not tsumo and i == allocation) for i,m in enumerate(groups))
            quads = sum(m.kind == "quad" for m in groups)
            pair_fu = (2 * int(pair in DRAGONS) + 2 * int(pair == seat_wind) + 2 * int(pair == round_wind)) if form == "standard" else 0
            wait_fu = 0
            if form == "standard":
                if allocation == -1:
                    wait_fu = 2
                elif groups[allocation].kind == "sequence":
                    base = groups[allocation].base
                    wait_fu = 2 * int(win_tile == base + 1 or (base % 9 == 0 and win_tile == base + 2) or (base % 9 == 6 and win_tile == base))
            yakuman: dict[str, int] = {}
            def ym(name: str, condition: bool, double_condition: str | None = None) -> None:
                if condition:
                    yakuman[name] = 2 if double_condition in rules["double_yakuman"] else 1
            ym("kokushi_musou", form == "kokushi", "kokushi_13_wait" if all(pre[i] == 1 for i in ORPHANS) else None)
            ym("suuankou", form == "standard" and concealed_count == 4, "suuankou_tanki" if allocation == -1 else None)
            ym("daisangen", DRAGONS.issubset(triplets))
            ym("shousuushii", len(triplets & WINDS) == 3 and pair in WINDS)
            ym("daisuushii", WINDS.issubset(triplets), "daisuushii")
            ym("tsuuiisou", all(t >= 27 for t in all_tiles))
            ym("chinroutou", present.issubset(TERMINALS))
            ym("ryuuiisou", present.issubset(GREENS))
            suits = {t // 9 for t in all_tiles if t < 27}
            nine_gates = False
            pure_gates = False
            if not fixed and len(suits) == 1 and max(all_tiles) < 27:
                suit_base = min(all_tiles) // 9 * 9
                pattern = (3, 1, 1, 1, 1, 1, 1, 1, 3)
                nine_gates = all(complete[suit_base+i] >= n for i,n in enumerate(pattern))
                pure_gates = tuple(pre[suit_base:suit_base+9]) == pattern
            ym("chuuren_poutou", nine_gates, "junsei_chuuren" if pure_gates else None)
            ym("suukantsu", quads == 4)
            ym("tenhou", tsumo and state["first_turn"] and data["actor"] == state["oya"])
            ym("chiihou", tsumo and state["first_turn"] and data["actor"] != state["oya"])
            pending = state["pending_kan"]
            if pending and pending["kind"] == "ankan" and not (rules["ankan_chankan"] == "kokushi_only" and form == "kokushi"):
                continue
            normal: dict[str, int] = {}
            def y(name: str, condition: bool, closed_han: int = 1, open_han: int | None = 1) -> None:
                if condition and (closed or open_han is not None):
                    normal[name] = closed_han if closed else open_han
            pinfu = form == "standard" and closed and sum(sequences.values()) == 4 and pair_fu == 0 and wait_fu == 0
            y("double_riichi" if state["double_riichi"] else "riichi", state["reach_accepted"], 2 if state["double_riichi"] else 1, None)
            y("ippatsu", state["ippatsu"], 1, None)
            y("menzen_tsumo", tsumo, 1, None)
            y("tanyao", not (present & ORPHANS), 1, 1 if rules["kuitan"] else None)
            y("pinfu", pinfu, 1, None)
            paired_sequences = sum(n // 2 for n in sequences.values())
            y("ryanpeikou", paired_sequences == 2, 3, None)
            y("iipeikou", paired_sequences == 1, 1, None)
            for name,tile in (("yakuhai_haku",31),("yakuhai_hatsu",32),("yakuhai_chun",33),("seat_wind",seat_wind),("round_wind",round_wind)):
                y(name, tile in triplets)
            y("rinshan_kaihou", state["rinshan"])
            y("chankan", pending is not None)
            y("haitei" if tsumo else "houtei", state["last_tile"])
            y("sanshoku_doujun", any(all(s*9+n in sequences for s in range(3)) for n in range(7)), 2, 1)
            y("ikkitsuukan", any(all(s*9+n in sequences for n in (0,3,6)) for s in range(3)), 2, 1)
            outside = form == "standard" and pair in ORPHANS and all(any(t in ORPHANS for t in m.tiles()) for m in groups) and bool(sequences)
            y("chanta", outside and any(t >= 27 for t in all_tiles), 2, 1)
            y("junchan", outside and max(all_tiles) < 27, 3, 2)
            y("chiitoitsu", form == "chiitoitsu", 2, None)
            y("toitoi", form == "standard" and not sequences, 2, 2)
            y("sanankou", concealed_count >= 3, 2, 2)
            y("honroutou", present.issubset(ORPHANS), 2, 2)
            y("sanshoku_doukou", any(all(s*9+n in triplets for s in range(3)) for n in range(9)), 2, 2)
            y("sankantsu", quads == 3, 2, 2)
            y("shousangen", len(triplets & DRAGONS) == 2 and pair in DRAGONS, 2, 2)
            y("honitsu", len(suits) == 1 and max(all_tiles) >= 27, 3, 2)
            y("chinitsu", len(suits) == 1 and max(all_tiles) < 27, 6, 5)
            if not yakuman and not normal:
                continue
            bonus: dict[str, int] = {}
            def next_tile(marker: str) -> int:
                t = tile_index(marker)
                return (t // 9)*9 + (t + 1) % 9 if t < 27 else 27+(t-27+1)%4 if t < 31 else 31+(t-31+1)%3
            for name,markers in (("dora",data["dora_markers"]),("uradora",data["ura_dora_markers"])):
                n = sum(all_counts[next_tile(marker)] for marker in markers)
                if n:
                    bonus[name] = n
            reds = sum(t.endswith("r") for t in [*physical,data["winning_tile"]])
            if reds:
                bonus["akadora"] = reds
            if yakuman:
                fu, han, bonus = 0, 0, {}
                yakus = [{"id": name, "value": n, "unit": "yakuman"} for name,n in sorted(yakuman.items())]
            else:
                if form == "chiitoitsu":
                    fu = 25
                else:
                    fu = 20 + (2 if tsumo and not pinfu else 0) + (10 if not tsumo and closed else 0) + pair_fu + wait_fu
                    for i,m in enumerate(groups):
                        if m.kind == "sequence":
                            continue
                        concealed = not m.open and not (not tsumo and i == allocation)
                        fu += (8 if m.kind == "quad" else 2) * (2 if concealed else 1) * (2 if m.base in ORPHANS else 1)
                    if not tsumo and not closed and fu == 20:
                        fu = 30
                    fu = (fu + 9) // 10 * 10
                han = sum(normal.values()) + sum(bonus.values())
                yakus = [{"id": name, "value": n, "unit": "han"} for name,n in sorted(normal.items())]
            basic = basic_points(fu, han, sum(yakuman.values()), rules)
            total = sum(normal_payments(basic,data["actor"],data["target"],state["oya"]).values())
            candidates.append({"fu":fu,"han":han,"yakus":yakus,"bonuses":[{"id":name,"han":n} for name,n in sorted(bonus.items())],"basic_points":basic,"hand_points":total})
    require(bool(candidates), "no_yaku", "no legal scoring interpretation")
    return min(candidates,key=lambda c:(-c["hand_points"],-sum(y["value"] for y in c["yakus"] if y["unit"]=="yakuman"),-c["han"],-c["fu"],tuple(y["id"] for y in c["yakus"])))


def pao_assignments(hand: dict[str, Any], rules: dict[str, Any]) -> dict[str,int]:
    seen: set[int] = set()
    pao = {}
    for meld in hand["melds"]:
        if meld["kind"] == "chi":
            continue
        tile = tile_index(meld["tiles"][0])
        for name,needed in (("daisangen",DRAGONS),("daisuushii",WINDS)):
            if name in rules["pao"]["yakus"] and tile in needed and len(seen & needed) == len(needed)-1 and meld["open"]:
                pao[name] = meld["source"]
        seen.add(tile)
    return pao


def settle_win(data: dict[str, Any], state: dict[str, Any], score: dict[str, Any], rules: dict[str, Any], honba: int, credit: int) -> dict[str, Any]:
    actor,target,oya = data["actor"],data["target"],state["oya"]
    assignments = pao_assignments(data["hand"],rules)
    role_values = {y["id"]:y["value"] for y in score["yakus"] if y["unit"] == "yakuman"}
    assignments = {name:liable for name,liable in assignments.items() if name in role_values}
    payers = normal_payments(score["basic_points"],actor,target,oya)
    transferred = False
    for name,liable in assignments.items():
        component = normal_payments(8000*role_values[name],actor,target,oya)
        if actor == target and rules["pao"]["tsumo"] == "normal":
            continue
        transferred = True
        for seat,points in component.items():
            payers[seat] -= points
        total = sum(component.values())
        if actor == target or rules["pao"]["ron"] == "liable_all" or liable == target:
            payers[liable] = payers.get(liable,0)+total
        else:
            liability = ceil100((total+1)//2)
            payers[liable] = payers.get(liable,0)+liability
            payers[target] = payers.get(target,0)+total-liability
    if transferred:
        total_honba = honba * (3*rules["honba_tsumo_value_per_payer"] if actor==target else rules["honba_ron_value"])
        total_base = sum(payers.values())
        units = total_honba//100
        shares = {seat:units*points//total_base for seat,points in payers.items() if points}
        order = sorted(shares,key=lambda seat:(-(units*payers[seat]%total_base),seat not in assignments.values(),seat))
        for seat in order[:units-sum(shares.values())]:
            shares[seat] += 1
        for seat,n in shares.items():
            payers[seat] += n*100
    elif actor==target:
        for seat in payers:
            payers[seat] += honba*rules["honba_tsumo_value_per_payer"]
    else:
        payers[target] += honba*rules["honba_ron_value"]
    deltas = [0]*4
    payments = []
    for seat,points in sorted(payers.items()):
        if points:
            payments.append({"from":seat,"to":actor,"points":points})
            deltas[seat] -= points
            deltas[actor] += points
    deltas[actor] += credit
    return {"actor":actor,"target":target,"winning_tile":data["winning_tile"],**score,
            "pao":[{"yaku_id":name,"liable_seat":seat} for name,seat in sorted(assignments.items())],
            "ura_dora_markers":data["ura_dora_markers"],"payments":payments,"kyotaku_points":credit,"deltas":deltas}


def calculate_fixture(fixture: dict[str, Any], base_rules: dict[str, Any]) -> dict[str, Any]:
    rules = {**base_rules, **fixture["rule_overrides"]}
    validate_score_bounds(rules)
    require(not rules["local_yaku"], "invalid_context", "the core scoring reference has no negotiated local-yaku handler")
    data,state = fixture["input"],fixture["state"]
    require(sum(state["scores"])+state["kyotaku"]*rules["riichi_stick_value"]==4*rules["starting_points"],
            "invalid_context", "input scores and deposits differ from the initial game total")
    result_type = data["type"]
    next_kyotaku = state["kyotaku"]
    if result_type == "hora":
        entries = [(data,state),*((other,other["state"]) for other in data.get("other_winners",[]))]
        for winner,context in entries:
            validate_state_projection(context,winner["actor"],rules)
        require(len({d["actor"] for d,s in entries})==len(entries),"invalid_context","duplicate winner")
        scored = [(d,s,score_hand(d,s,rules)) for d,s in entries]
        if len(scored)>1:
            require(all(d["win_method"]=="ron" and d["target"]==data["target"] and d["winning_tile"]==data["winning_tile"] for d,s,c in scored),"invalid_context","winners do not share one discard")
            shared=("oya","bakaze","kyoku","honba","kyotaku","wall_remaining","kan_counts","scores","pending_kan","last_tile")
            require(all(all(s[key]==state[key] for key in shared) and d["dora_markers"]==data["dora_markers"] for d,s,c in scored),"invalid_context","winner contexts differ")
            require(state["kyotaku"] >= sum(s["reach_accepted"] for d,s,c in scored), "invalid_context", "multiple riichi deposits are missing")
            exposed_ura = [d["ura_dora_markers"] for d,s,c in scored if s["reach_accepted"]]
            require(not exposed_ura or all(markers == exposed_ura[0] for markers in exposed_ura), "invalid_context", "winners do not share ura indicator positions")
            physical = [data["winning_tile"], *data["dora_markers"], *(exposed_ura[0] if exposed_ura else [])]
            for d,s,c in scored:
                physical.extend(d["hand"]["concealed_tiles"])
                physical.extend(tile for meld in d["hand"]["melds"] for tile in meld["tiles"])
            inventory(physical,rules)
        if len(scored)==3 and rules["ron_policy"]=="double_only":
            result={"result_type":"ryukyoku","reason":"sanchaho","tenpai":None,"deltas":[0]*4}
        else:
            nearest=min(scored,key=lambda item:(item[0]["actor"]-item[0]["target"]+4)%4)[0]["actor"]
            if len(scored)>1 and rules["ron_policy"]=="head_bump":
                scored=[entry for entry in scored if entry[0]["actor"]==nearest]
            total_credit=state["kyotaku"]*rules["riichi_stick_value"]
            credits={d["actor"]:0 for d,s,c in scored}
            if rules["multiple_ron_settlement"]["kyotaku"]=="equal_split":
                credits={seat:total_credit//(len(scored)*100)*100 for seat in credits}
            credits[nearest] += total_credit-sum(credits.values())
            wins=[]
            for d,s,c in sorted(scored,key=lambda entry:entry[0]["actor"]):
                honba=state["honba"] if len(scored)==1 or rules["multiple_ron_settlement"]["honba"]=="each_winner" or d["actor"]==nearest else 0
                wins.append(settle_win(d,s,c,rules,honba,credits[d["actor"]]))
            result={"result_type":"hora","wins":wins,"deltas":[sum(w["deltas"][i] for w in wins) for i in range(4)]}
            next_kyotaku=0
    elif result_type=="ryukyoku":
        validate_state_projection(state,0,rules)
        require(data["reason"]=="fanpai","invalid_context","explicit draw fixture must be exhaustive")
        for seat,hand in enumerate(data["hands"]):
            hand_parts(hand,rules,seat)
        kan_counts = [sum(m["kind"] in {"ankan", "daiminkan", "kakan"} for m in hand["melds"])
                      for hand in data["hands"]]
        require(state["kan_counts"] == kan_counts and sum(kan_counts) <= 4,
                "invalid_context", "draw kan counts differ from the four hands' committed melds")
        physical = [tile for hand in data["hands"] for tile in hand["concealed_tiles"]]
        physical.extend(tile for hand in data["hands"] for meld in hand["melds"] for tile in meld["tiles"])
        inventory(physical,rules)
        require(state["wall_remaining"]==0 and state["pending_kan"] is None, "invalid_context", "exhaustive draw before the wall is exhausted")
        tenpai=[bool(waits(hand,rules)) for hand in data["hands"]]
        count=sum(tenpai)
        amount=rules["noten_payment"]["total_points"]
        deltas=[0]*4 if count in (0,4) else [amount//count if ready else -amount//(4-count) for ready in tenpai]
        result={"result_type":"ryukyoku","reason":"fanpai","tenpai":tenpai,"deltas":deltas}
    elif result_type=="penalty":
        validate_state_projection(state,data["offender"],rules)
        require(rules["invalid_action_policy"]=="chombo", "invalid_context", "penalty without chombo policy")
        offender=data["offender"]
        amount=rules["chombo"]["penalty_points"]
        seats=[seat for seat in range(4) if seat!=offender]
        payments=[{"from":offender,"to":seat,"points":amount//300*100+(amount%300 if i==0 else 0)} for i,seat in enumerate(seats)]
        payments=[p for p in payments if p["points"]]
        deltas=[0]*4
        for payment in payments:
            deltas[offender]-=payment["points"]
            deltas[payment["to"]]+=payment["points"]
        result={"result_type":"penalty","reason":"illegal_action","offender":offender,"payments":payments,"deltas":deltas}
    else:
        raise ScoringError("invalid_context","unknown scoring result type")
    scores=[state["scores"][i]+result["deltas"][i] for i in range(4)]
    require(sum(result["deltas"])+(next_kyotaku-state["kyotaku"])*rules["riichi_stick_value"]==0,"invalid_context","settlement violates conservation")
    return {**result,"scores":scores,"kyotaku":next_kyotaku}
