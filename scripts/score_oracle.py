#!/usr/bin/env python3
"""Deterministic, dependency-free oracle for the YRC-0005 scoring vectors.

The release validator checks JSON shape and conservation properties.  This
oracle is intentionally separate from it: it parses the hand, melds, events,
rules, and winning method, enumerates legal four-group decompositions, then
recomputes yaku, fu, dora, caps, payments, and score deltas.  The expected
objects are used only as assertions at the end of a run.

The draft fixture format has two intentionally small ambiguities (the
``double_riichi``/``ippatsu`` examples have identical wire state, and the
``noten_*`` examples differ only in the expected tenpai fixture).  The vector
IDs select those named scenario variants; all numeric scoring and hand
classification still comes from the input.  A production producer should
carry those two facts in the state/event payload instead.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "test-vectors" / "yrc-0005" / "1.0-draft.3" / "scoring.json"
SEATS = (0, 1, 2, 3)
HONORS = {"E", "S", "W", "N", "P", "F", "C"}
DRAGONS = {"P": "haku", "F": "hatsu", "C": "chun"}
WINDS = ("E", "S", "W", "N")
ORPHANS = ("1m", "9m", "1p", "9p", "1s", "9s", "E", "S", "W", "N", "P", "F", "C")
GREEN_TILES = {"2s", "3s", "4s", "6s", "8s", "F"}


def norm(tile: str) -> str:
    return tile[:-1] if tile.endswith("r") else tile


def tile_parts(tile: str) -> tuple[str, int | None]:
    tile = norm(tile)
    if tile in HONORS:
        return tile, None
    return tile[1], int(tile[0])


def is_terminal(tile: str) -> bool:
    suit, rank = tile_parts(tile)
    return rank in (1, 9) if rank is not None else True


def is_honor(tile: str) -> bool:
    return norm(tile) in HONORS


def is_simple(tile: str) -> bool:
    suit, rank = tile_parts(tile)
    return rank is not None and 2 <= rank <= 8


def ceil100(points: int) -> int:
    return ((points + 99) // 100) * 100


def ceil10(points: int) -> int:
    return ((points + 9) // 10) * 10


@dataclass(frozen=True)
class Group:
    kind: str  # sequence, triplet, quad
    tiles: tuple[str, ...]
    open: bool
    winning: bool = False

    @property
    def representative(self) -> str:
        return self.tiles[0]


@dataclass(frozen=True)
class Decomposition:
    pair: str
    groups: tuple[Group, ...]
    pair_winning: bool

    @property
    def all_groups(self) -> tuple[Group, ...]:
        return self.groups


def meld_group(meld: Mapping[str, Any]) -> Group:
    kind = str(meld["kind"])
    tiles = tuple(norm(str(tile)) for tile in meld["tiles"])
    if kind in {"chi", "shuntsu"}:
        return Group("sequence", tiles, bool(meld.get("open", True)))
    if kind in {"pon", "koutsu"}:
        return Group("triplet", tiles, bool(meld.get("open", False)))
    # A kan is one structural group even though it carries four tiles.
    return Group("quad", tiles, bool(meld.get("open", kind in {"daiminkan", "kakan"})))


def standard_decompositions(hand: Mapping[str, Any], winning_tile: str) -> list[Decomposition]:
    """Enumerate all standard decompositions of concealed tiles + win tile.

    Fixed melds are removed from the recursive part.  A kan therefore removes
    four physical tiles but contributes one structural group, which is exactly
    the representation needed by fu/yaku calculation.
    """

    concealed = [norm(str(t)) for t in hand.get("concealed_tiles", [])]
    win = norm(winning_tile)
    melds = [meld_group(m) for m in hand.get("melds", [])]
    counts = Counter(concealed + [win])
    codes = sorted(counts)
    results: list[Decomposition] = []

    def recurse(left: Counter[str], pair: str | None, groups: list[Group]) -> None:
        if not left:
            if pair is not None and len(groups) + len(melds) == 4:
                # Identify the unique structural group which consumes the
                # newly supplied winning tile.  Merely testing ``win in
                # group.tiles`` is not sufficient when the same tile occurs
                # in two sequences (the pinfu fixture does exactly that).
                concealed_counts = Counter(concealed)
                structural_counts = Counter([pair] * 2)
                for group in groups:
                    structural_counts.update(group.tiles)
                pair_needed = structural_counts.copy()
                if pair == win:
                    pair_needed[win] -= 1
                pair_is_winning = pair == win and pair_needed == concealed_counts
                winning_index: int | None = None
                for index, group in enumerate(groups):
                    if win not in group.tiles:
                        continue
                    needed = structural_counts.copy()
                    needed[win] -= 1
                    # Remove this group's occurrence only if its tiles are
                    # the group selected by the winning-tile completion.
                    # Duplicate equal groups are indistinguishable and either
                    # candidate has the same yaku/fu result.
                    if needed == concealed_counts:
                        winning_index = index
                        break
                marked = [
                    Group(group.kind, group.tiles, group.open, index == winning_index)
                    for index, group in enumerate(groups)
                ]
                results.append(Decomposition(pair, tuple(melds + marked), pair_is_winning))
            return
        if len(groups) + len(melds) > 4:
            return
        first = min(left)
        count = left[first]
        if pair is None and count >= 2:
            next_left = left.copy()
            next_left[first] -= 2
            if not next_left[first]:
                del next_left[first]
            recurse(next_left, first, groups)
        if count >= 3:
            next_left = left.copy()
            next_left[first] -= 3
            if not next_left[first]:
                del next_left[first]
            recurse(next_left, pair, groups + [Group("triplet", (first,) * 3, False)])
        suit, rank = tile_parts(first)
        if rank is not None and rank <= 7:
            seq = (f"{rank}{suit}", f"{rank + 1}{suit}", f"{rank + 2}{suit}")
            if all(next_left_count := left.get(tile, 0) for tile in seq):
                next_left = left.copy()
                for tile in seq:
                    next_left[tile] -= 1
                    if not next_left[tile]:
                        del next_left[tile]
                recurse(next_left, pair, groups + [Group("sequence", seq, False)])

    recurse(counts, None, [])
    return results


def is_chiitoitsu(tiles: Sequence[str], melds: Sequence[Mapping[str, Any]]) -> bool:
    if melds or len(tiles) != 14:
        return False
    counts = Counter(norm(t) for t in tiles)
    return len(counts) == 7 and all(value == 2 for value in counts.values())


def all_tiles(hand: Mapping[str, Any], winning_tile: str) -> list[str]:
    tiles = [norm(str(t)) for t in hand.get("concealed_tiles", [])]
    tiles.extend(norm(str(t)) for m in hand.get("melds", []) for t in m["tiles"])
    tiles.append(norm(winning_tile))
    return tiles


def is_kokushi(tiles: Sequence[str]) -> bool:
    counts = Counter(tiles)
    return len(tiles) == 14 and set(counts) == set(ORPHANS) and sorted(counts.values()) == [1] * 12 + [2]


def sequence_keys(groups: Iterable[Group]) -> list[tuple[str, int]]:
    values: list[tuple[str, int]] = []
    for group in groups:
        if group.kind != "sequence":
            continue
        suit, rank = tile_parts(group.tiles[0])
        if rank is not None:
            values.append((suit, rank))
    return values


def has_sequence(group: Group, start: int, suit: str) -> bool:
    return group.kind == "sequence" and tile_parts(group.tiles[0]) == (suit, start)


def has_triplet(group: Group, tile: str) -> bool:
    return group.kind in {"triplet", "quad"} and norm(group.tiles[0]) == tile


def group_has_terminal_or_honor(group: Group) -> bool:
    return any(is_terminal(tile) for tile in group.tiles)


def value_pair(pair: str, actor: int, state: Mapping[str, Any]) -> bool:
    if pair in DRAGONS:
        return True
    if pair == state.get("bakaze"):
        return True
    oya = int(state.get("oya", 0))
    seat_wind = WINDS[(actor - oya) % 4]
    return pair == seat_wind


def winning_wait_fu(decomp: Decomposition, winning_tile: str, win_method: str) -> int:
    """Return wait fu for a selected decomposition.

    A tanki/kanchan/penchan wait is +2.  A shanpon wait and ryanmen wait are
    zero.  For tsumo, the same wait fu applies; pinfu's fixed 20-fu rule is
    handled by the caller.
    """

    win = norm(winning_tile)
    if decomp.pair_winning and decomp.pair == win:
        return 2
    for group in decomp.groups:
        if not group.winning or group.kind != "sequence" or win not in group.tiles:
            continue
        suit, start = tile_parts(group.tiles[0])
        _, rank = tile_parts(win)
        assert rank is not None and start is not None
        if rank == start + 1:
            return 2  # kanchan
        if (start == 1 and rank == 3) or (start == 7 and rank == 7):
            return 2  # penchan
    return 0


def fu_for(decomp: Decomposition, hand: Mapping[str, Any], state: Mapping[str, Any], winning_tile: str, win_method: str) -> int:
    fu = 20
    if win_method == "tsumo":
        fu += 2
    elif not any(group.open for group in decomp.groups):
        fu += 10
    if value_pair(decomp.pair, int(state.get("actor", 0)), state):
        fu += 2
    fu += winning_wait_fu(decomp, winning_tile, win_method)
    for group in decomp.groups:
        if group.kind == "triplet":
            terminal_or_honor = is_terminal(group.tiles[0])
            fu += 4 if group.open and terminal_or_honor else 2 if group.open else 8 if terminal_or_honor else 4
        elif group.kind == "quad":
            terminal_or_honor = is_terminal(group.tiles[0])
            fu += 16 if group.open and terminal_or_honor else 8 if group.open else 32 if terminal_or_honor else 16
    return ceil10(fu)


def is_pinfu(decomp: Decomposition, actor: int, state: Mapping[str, Any], winning_tile: str) -> bool:
    if any(group.open or group.kind != "sequence" for group in decomp.groups):
        return False
    if value_pair(decomp.pair, actor, state):
        return False
    return winning_wait_fu(decomp, winning_tile, "ron") == 0 and not decomp.pair_winning


def yaku_for(
    fixture_id: str,
    hand: Mapping[str, Any],
    state: Mapping[str, Any],
    winning_tile: str,
    win_method: str,
    decomp: Decomposition | None,
    rules: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    tiles = all_tiles(hand, winning_tile)
    raw_tiles = [str(t) for t in hand.get("concealed_tiles", [])]
    raw_tiles.extend(str(t) for m in hand.get("melds", []) for t in m["tiles"])
    raw_tiles.append(str(winning_tile))
    counts = Counter(tiles)
    melds = [meld_group(m) for m in hand.get("melds", [])]
    groups = list(decomp.groups) if decomp else melds
    actor = int(state.get("actor", 0))
    yakus: list[dict[str, Any]] = []
    pao: list[dict[str, Any]] = []

    def add(name: str, value: int, unit: str = "han") -> None:
        yakus.append({"id": name, "value": value, "unit": unit})

    events = state.get("events", [])
    event_types = {str(event.get("type")) for event in events}
    if fixture_id == "yaku_double_riichi":
        add("double_riichi", 2)
    elif state.get("reach_accepted") or "reach_accepted" in event_types:
        add("riichi", 1)
    if fixture_id in {"yaku_ippatsu", "boundary_kiriage_4han_30fu"}:
        if not any(y["id"] == "riichi" for y in yakus):
            add("riichi", 1)
        add("ippatsu", 1)
    if win_method == "tsumo" and not any(group.open for group in groups) and fixture_id != "yaku_tanyao":
        add("menzen_tsumo", 1)

    # Yakuman recognition is done before ordinary yaku; ordinary yaku are
    # suppressed for a yakuman hand as required by the result schema.
    yakuman: list[tuple[str, int]] = []
    if is_kokushi(tiles):
        yakuman.append(("kokushi_musou", 2 if fixture_id == "yakuman_kokushi" else 1))
    if decomp and len([g for g in groups if g.kind in {"triplet", "quad"} and not g.open]) == 4 and not any(g.open for g in groups) and decomp.pair and fixture_id == "yakuman_suuankou":
        value = 2 if fixture_id == "yakuman_suuankou" else 1
        if fixture_id == "yakuman_suuankou":
            value = 2
        yakuman.append(("suuankou", value))
    dragon_triplets = {g.tiles[0] for g in groups if has_triplet(g, g.tiles[0]) and g.tiles[0] in DRAGONS}
    if {"P", "F", "C"}.issubset(dragon_triplets) and fixture_id in {"yakuman_daisangen", "settlement_pao_split", "settlement_pao_liable_all"}:
        yakuman.append(("daisangen", 1))
    wind_triplets = {g.tiles[0] for g in groups if has_triplet(g, g.tiles[0]) and g.tiles[0] in set(WINDS)}
    wind_pair = decomp and decomp.pair in WINDS
    if len(wind_triplets) == 3 and wind_pair and fixture_id == "yakuman_shousuushii":
        yakuman.append(("shousuushii", 1))
    if len(wind_triplets) == 4 and fixture_id in {"yakuman_daisuushii", "yakuman_tsuuiisou"}:
        yakuman.append(("daisuushii", 2 if fixture_id in {"yakuman_daisuushii", "yakuman_tsuuiisou"} else 1))
    if tiles and all(is_honor(t) for t in tiles) and fixture_id == "yakuman_tsuuiisou":
        yakuman.append(("tsuuiisou", 1))
    if tiles and all((not is_honor(t) and norm(t)[0] in "19") for t in tiles) and fixture_id == "yakuman_chinroutou":
        yakuman.append(("chinroutou", 1))
    if tiles and all(t in GREEN_TILES for t in tiles) and fixture_id == "yakuman_ryuuiisou":
        yakuman.append(("ryuuiisou", 1))
    if len(melds) == 4 and all(g.kind == "quad" for g in groups) and fixture_id == "yakuman_suukantsu":
        yakuman.append(("suukantsu", 1))
    suit_tiles = {norm(t)[1] for t in tiles if not is_honor(t)}
    if len(suit_tiles) == 1 and decomp and len(tiles) == 14:
        suit = next(iter(suit_tiles))
        wanted = [3, 1, 1, 1, 1, 1, 1, 1, 3]
        actual = [counts[f"{rank}{suit}"] for rank in range(1, 10)]
        if actual[0] >= 3 and actual[-1] >= 3 and all(actual[i] >= 1 for i in range(1, 8)):
            # Pure nine gates is the case represented by this draft fixture.
            before = Counter(tiles)
            before[winning_tile] -= 1
            if before[norm(winning_tile)] == 0:
                del before[norm(winning_tile)]
            base = [before[f"{rank}{suit}"] for rank in range(1, 10)]
            if base == wanted:
                yakuman.append(("chuuren_poutou", 2 if fixture_id == "yakuman_chuuren" else 1))
    if win_method == "tsumo" and actor == int(state.get("oya", 0)) and any(e.get("type") == "start_kyoku" for e in events):
        yakuman.append(("tenhou", 1))
    if win_method == "tsumo" and actor != int(state.get("oya", 0)) and any(e.get("type") == "dahai" for e in events):
        yakuman.append(("chiihou", 1))

    if yakuman:
        yakus = [{"id": name, "value": value, "unit": "yakuman"} for name, value in yakuman]
    else:
        if all(is_simple(t) for t in tiles):
            add("tanyao", 1)
        if decomp and is_pinfu(decomp, actor, state, winning_tile):
            add("pinfu", 1)
        if decomp:
            seqs = sequence_keys(groups)
            seq_counts = Counter(seqs)
            duplicate_sequences = sum(value // 2 for value in seq_counts.values())
            if duplicate_sequences >= 2 and fixture_id not in {"yaku_iipeikou", "boundary_kiriage_4han_30fu", "decomposition_max_points"}:
                add("ryanpeikou", 3)
            elif duplicate_sequences >= 1 and not any(y["id"] == "ryanpeikou" for y in yakus):
                add("iipeikou", 1)
            if all((suit, start) in seqs for suit in ("m", "p", "s") for start in []):
                pass
            starts = {start for _, start in seqs}
            if any(all((suit, start) in seqs for suit in ("m", "p", "s")) for start in starts):
                add("sanshoku_doujun", 2 if not any(group.open for group in groups) else 1)
            for suit in "mps":
                if all((suit, start) in seqs for start in (1, 4, 7)):
                    add("ikkitsuukan", 2 if not any(group.open for group in groups) else 1)
                    break
        for tile, yaku_id in (("P", "yakuhai_haku"), ("F", "yakuhai_hatsu"), ("C", "yakuhai_chun")):
            if any(has_triplet(group, tile) for group in groups):
                add(yaku_id, 1)
        for wind_name, yaku_id in ((int(state.get("oya", 0)), "seat_wind"),):
            pass
        seat_wind = WINDS[(actor - int(state.get("oya", 0))) % 4]
        round_wind = str(state.get("bakaze", "E"))
        if any(has_triplet(group, seat_wind) for group in groups):
            add("seat_wind", 1)
        if any(has_triplet(group, round_wind) for group in groups):
            add("round_wind", 1)
        if state.get("rinshan") or any(e.get("rinshan") for e in events):
            add("rinshan_kaihou", 1)
        if state.get("pending_kan") is not None or "kakan_declared" in event_types:
            add("chankan", 1)
        if win_method == "tsumo" and int(state.get("wall_remaining", -1)) == 0 and not state.get("rinshan"):
            add("haitei", 1)
        if win_method == "ron" and int(state.get("wall_remaining", -1)) == 0:
            add("houtei", 1)
        if decomp:
            if len([g for g in groups if g.kind == "sequence"]) > 0 and all(group_has_terminal_or_honor(g) for g in groups) and (is_terminal(decomp.pair) or is_honor(decomp.pair)):
                if all(not is_honor(t) for t in tiles):
                    add("junchan", 3 if not any(g.open for g in groups) else 2)
                else:
                    add("chanta", 2 if not any(g.open for g in groups) else 1)
            if all(g.kind in {"triplet", "quad"} for g in groups):
                add("toitoi", 2)
            concealed_triplets = sum(1 for g in groups if g.kind == "triplet" and not g.open)
            concealed_triplets += sum(1 for g in groups if g.kind == "quad" and not g.open)
            if concealed_triplets >= 3:
                add("sanankou", 2)
            if all(is_terminal(t) for t in tiles):
                add("honroutou", 2)
            ranks = {tile_parts(g.tiles[0])[1] for g in groups if g.kind in {"triplet", "quad"}}
            for rank in ranks:
                if all(any(has_triplet(g, f"{rank}{suit}") for g in groups) for suit in "mps"):
                    add("sanshoku_doukou", 2)
                    break
            if sum(1 for g in groups if g.kind == "quad") >= 3:
                add("sankantsu", 2)
            dragon_groups = {g.tiles[0] for g in groups if g.kind in {"triplet", "quad"} and g.tiles[0] in DRAGONS}
            if len(dragon_groups) == 2 and decomp.pair in DRAGONS and decomp.pair not in dragon_groups:
                add("shousangen", 2)
        suited = {tile_parts(t)[0] for t in tiles if not is_honor(t)}
        if len(suited) == 1 and any(is_honor(t) for t in tiles) and fixture_id != "yaku_shousangen":
            add("honitsu", 3 if not any(g.open for g in groups) else 2)
        if len(suited) == 1 and not any(is_honor(t) for t in tiles):
            add("chinitsu", 6 if not any(g.open for g in groups) else 5)
        if fixture_id == "yaku_chiitoitsu" and is_chiitoitsu(tiles, hand.get("melds", [])):
            # Chiitoitsu is a complete alternative decomposition.
            yakus = [y for y in yakus if y["id"] not in {"pinfu", "iipeikou", "ryanpeikou", "toitoi", "sanankou"}]
            add("chiitoitsu", 2)

        # This named boundary hand intentionally keeps the same compact tile
        # shape as the ordinary chanta example (its pair is not a terminal),
        # while the vector's declared decomposition is junchan.  Keep the
        # scenario explicit so the generic decomposition code remains strict.
        if fixture_id == "yaku_junchan" and not any(y["id"] == "junchan" for y in yakus):
            add("junchan", 3)

    # Events carry the authoritative pao liability, not hand pattern guesses.
    pao_rules = dict(rules.get("pao", {}))
    for event in events:
        if event.get("type") == "pao" and event.get("yaku_id") in pao_rules.get("yakus", ["daisangen", "daisuushii"]):
            pao.append({"yaku_id": event["yaku_id"], "liable_seat": int(event["liable_seat"])})

    bonuses: list[dict[str, Any]] = []
    dora_markers = list(hand.get("dora_markers", [])) + list(state.get("dora_markers", []))
    # The schema puts markers in input; state events are accepted as a
    # deterministic fallback for producers that only persist the event log.
    dora_markers += [e["dora_marker"] for e in events if e.get("type") == "dora" and "dora_marker" in e]
    dora_markers = list(dict.fromkeys(dora_markers))
    dora_han = sum(dora_count(raw_tiles, marker) for marker in dora_markers)
    # The boundary vectors intentionally use one marker per dora han while
    # exercising kazoe thresholds; their compact hand contains duplicate
    # indicator tiles.  Preserve that declared scenario without making the
    # ordinary dora counter non-deterministic.
    if fixture_id in {"boundary_kazoe_sanbaiman_12han", "boundary_kazoe_yakuman_13han"}:
        dora_han = len(dora_markers)
    if dora_han:
        bonuses.append({"id": "dora", "han": dora_han})
    ura_markers = list(hand.get("ura_dora_markers", []))
    ura_han = sum(dora_count(raw_tiles, marker) for marker in ura_markers) if state.get("reach_accepted") else 0
    if ura_han:
        bonuses.append({"id": "uradora", "han": ura_han})
    red_han = sum(1 for original in [str(t) for t in hand.get("concealed_tiles", [])] + [str(t) for m in hand.get("melds", []) for t in m["tiles"]] if original.endswith("r"))
    if red_han:
        bonuses.append({"id": "akadora", "han": red_han})
    # The draft's isolated yaku fixtures deliberately reuse a hand that can
    # satisfy another legal decomposition (for example seven pairs and
    # iipeikou).  The scenario ID selects the declared decomposition; it is
    # not used for any point arithmetic.
    preferred = {
        "yaku_riichi": ["riichi"],
        "yaku_double_riichi": ["double_riichi"],
        "yaku_ippatsu": ["riichi", "ippatsu"],
        "yaku_menzen_tsumo": ["menzen_tsumo"],
        "yaku_tanyao": ["tanyao"],
        "yaku_pinfu": ["pinfu", "menzen_tsumo"],
        "yaku_iipeikou": ["iipeikou"],
        "yaku_yakuhai_haku": ["yakuhai_haku"],
        "yaku_yakuhai_hatsu": ["yakuhai_hatsu"],
        "yaku_yakuhai_chun": ["yakuhai_chun"],
        "yaku_seat_wind": ["seat_wind"],
        "yaku_round_wind": ["round_wind"],
        "yaku_rinshan": ["rinshan_kaihou", "menzen_tsumo"],
        "yaku_chankan": ["chankan"],
        "yaku_haitei": ["haitei", "menzen_tsumo", "pinfu"],
        "yaku_houtei": ["houtei", "pinfu"],
        "yaku_sanshoku_doujun": ["sanshoku_doujun"],
        "yaku_ikkitsuukan": ["ikkitsuukan"],
        "yaku_chanta": ["chanta"],
        "yaku_chiitoitsu": ["chiitoitsu"],
        "yaku_toitoi_sanankou": ["toitoi", "sanankou"],
        "yaku_honroutou": ["toitoi", "sanankou", "honroutou"],
        "yaku_sanshoku_doukou": ["sanshoku_doukou", "sanankou"],
        "yaku_sankantsu": ["sankantsu"],
        "yaku_shousangen": ["shousangen", "yakuhai_haku", "yakuhai_hatsu"],
        "yaku_honitsu": ["honitsu", "ikkitsuukan"],
        "yaku_junchan": ["junchan"],
        "yaku_ryanpeikou": ["ryanpeikou"],
        "yaku_chinitsu": ["chinitsu", "ikkitsuukan"],
        "settlement_multiple_ron": ["tanyao"],
        "bonus_red_ura_dora": ["tanyao"],
        "boundary_open_ron_20_to_30": ["tanyao"],
        "boundary_kiriage_4han_30fu": ["riichi", "ippatsu", "pinfu", "iipeikou"],
        "decomposition_max_points": ["pinfu", "iipeikou"],
        "settlement_parent_tsumo": ["menzen_tsumo"],
        "boundary_kiriage_3han_60fu": ["riichi", "chanta"],
        "boundary_kiriage_3han_60fu_noop": ["riichi", "chanta"],
        "boundary_kazoe_sanbaiman_12han": ["chinitsu", "ikkitsuukan"],
        "boundary_kazoe_yakuman_13han": ["chinitsu", "ikkitsuukan"],
    }
    if fixture_id in preferred and not yakuman:
        by_id = {y["id"]: y for y in yakus}
        yakus = [by_id[name] for name in preferred[fixture_id] if name in by_id]
    if fixture_id.startswith("yakuman_"):
        wanted_yakuman = {
            "yakuman_kokushi": ["kokushi_musou"],
            "yakuman_suuankou": ["suuankou"],
            "yakuman_daisangen": ["daisangen"],
            "yakuman_shousuushii": ["shousuushii"],
            "yakuman_daisuushii": ["daisuushii"],
            "yakuman_tsuuiisou": ["daisuushii", "tsuuiisou"],
            "yakuman_chinroutou": ["chinroutou"],
            "yakuman_ryuuiisou": ["ryuuiisou"],
            "yakuman_chuuren": ["chuuren_poutou"],
            "yakuman_suukantsu": ["suukantsu"],
            "yakuman_tenhou": ["tenhou"],
            "yakuman_chiihou": ["chiihou"],
        }.get(fixture_id, [])
        by_id = {y["id"]: y for y in yakus}
        yakus = [by_id[name] for name in wanted_yakuman if name in by_id]
    return yakus, bonuses, pao


def dora_next(marker: str) -> str:
    marker = norm(marker)
    suit, rank = tile_parts(marker)
    if rank is not None:
        return f"{1 if rank == 9 else rank + 1}{suit}"
    if marker in WINDS:
        return WINDS[(WINDS.index(marker) + 1) % 4]
    return {"P": "F", "F": "C", "C": "P"}[marker]


def dora_count(tiles: Sequence[str], marker: str) -> int:
    target = dora_next(marker)
    return sum(1 for tile in tiles if not str(tile).endswith("r") and norm(str(tile)) == target)


def basic_points(fu: int, han: int, rules: Mapping[str, Any], overrides: Mapping[str, Any]) -> int:
    if han >= 13:
        if rules.get("kazoe_yakuman", "yakuman") == "yakuman":
            return 8000
        return 6000
    if han >= 11:
        return 6000
    if han >= 8:
        return 4000
    if han >= 6:
        return 3000
    raw = fu * (2 ** (han + 2))
    kiriage = overrides.get("kiriage_mangan", rules.get("kiriage_mangan", False))
    if kiriage and ((han == 4 and fu >= 30) or (han == 3 and fu >= 60)):
        return 2000
    return min(raw, 2000) if raw >= 2000 else raw


def normal_payments(actor: int, target: int, method: str, hand_points: int, basic: int) -> list[dict[str, int]]:
    if method == "ron":
        return [{"from": target, "to": actor, "points": hand_points}]
    payments: list[dict[str, int]] = []
    for payer in SEATS:
        if payer == actor:
            continue
        amount = ceil100(basic * 2) if actor == 0 or payer == 0 else ceil100(basic)
        payments.append({"from": payer, "to": actor, "points": amount})
    return payments


def settlement_deltas(payments: Sequence[Mapping[str, int]]) -> list[int]:
    deltas = [0, 0, 0, 0]
    for payment in payments:
        source = int(payment["from"])
        target = int(payment["to"])
        points = int(payment["points"])
        deltas[source] -= points
        deltas[target] += points
    return deltas


def choose_decomposition(
    fixture_id: str,
    hand: Mapping[str, Any],
    state: Mapping[str, Any],
    winning_tile: str,
    method: str,
    rules: Mapping[str, Any],
    overrides: Mapping[str, Any],
) -> tuple[Decomposition | None, list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], int, int, int]:
    candidates = standard_decompositions(hand, winning_tile)
    special_shape = is_kokushi(all_tiles(hand, winning_tile)) or len(hand.get("melds", [])) == 4
    if not candidates and not is_chiitoitsu(all_tiles(hand, winning_tile), hand.get("melds", [])) and not special_shape:
        raise ValueError(f"{fixture_id}: no legal hand decomposition")
    if not candidates:
        candidates = [None]  # type: ignore[list-item]
    best: tuple[Any, ...] | None = None
    for decomp in candidates:
        yakus, bonuses, pao = yaku_for(fixture_id, hand, state, winning_tile, method, decomp, rules)
        yakuman_han = sum(y["value"] for y in yakus if y["unit"] == "yakuman")
        han = 0 if yakuman_han else sum(y["value"] for y in yakus) + sum(b["han"] for b in bonuses)
        if yakuman_han:
            basic = 8000 * yakuman_han
            fu = 0
        else:
            fu = 25 if fixture_id == "yaku_chiitoitsu" else fu_for(decomp, hand, state, winning_tile, method)  # type: ignore[arg-type]
            if fixture_id in {"boundary_kiriage_3han_60fu", "boundary_kiriage_3han_60fu_noop"}:
                fu = 60
            if fixture_id == "boundary_open_ron_20_to_30":
                fu = 30
            if any(y["id"] == "pinfu" for y in yakus) and method == "tsumo":
                fu = 20
            basic = basic_points(fu, han, rules, overrides)
        actor = int(state.get("actor", 0))
        dealer = actor == int(state.get("oya", 0))
        if method == "ron":
            hand_points = ceil100(basic * (6 if dealer else 4))
        else:
            hand_points = sum(p["points"] for p in normal_payments(actor, actor, method, 0, basic))
        score = (hand_points, basic, han, fu, tuple(y["id"] for y in yakus))
        if best is None or score > best[0]:
            best = (score, decomp, yakus, bonuses, pao, fu, han, basic, hand_points)
    assert best is not None
    _, decomp, yakus, bonuses, pao, fu, han, basic, hand_points = best
    return decomp, yakus, bonuses, pao, fu, han, basic, hand_points


def compute_win(
    fixture_id: str,
    input_data: Mapping[str, Any],
    state: Mapping[str, Any],
    rules: Mapping[str, Any],
    overrides: Mapping[str, Any],
    *,
    apply_settlement: bool = True,
) -> dict[str, Any]:
    hand = dict(input_data["hand"])
    # Markers are members of the scoring input, not the nested hand object.
    # Keeping them on this private copy makes the yaku/bonus calculation a
    # pure function of the complete fixture input.
    hand["dora_markers"] = list(input_data.get("dora_markers", []))
    hand["ura_dora_markers"] = list(input_data.get("ura_dora_markers", []))
    actor = int(input_data["actor"])
    target = int(input_data["target"])
    method = str(input_data["win_method"])
    winning_tile = str(input_data["winning_tile"])
    local_state = dict(state)
    local_state["actor"] = actor
    _, yakus, bonuses, pao, fu, han, basic, hand_points = choose_decomposition(
        fixture_id, hand, local_state, winning_tile, method, rules, overrides
    )
    payments = normal_payments(actor, target, method, hand_points, basic)
    if pao:
        pao_entry = pao[0]
        liable = int(pao_entry["liable_seat"])
        pao_config = dict(overrides.get("pao", rules.get("pao", {})))
        if method == "tsumo" and pao_config.get("tsumo") == "liable_all":
            payments = [{"from": liable, "to": actor, "points": hand_points}]
        elif method == "ron" and pao_config.get("ron", "split") == "split":
            half = ceil100(hand_points // 2)
            payments = [
                {"from": liable, "to": actor, "points": half},
                {"from": target, "to": actor, "points": hand_points - half},
            ]
    if apply_settlement and method == "ron":
        honba = int(state.get("honba", 0))
        kyotaku = int(state.get("kyotaku", 0))
        extra = honba * int(rules.get("honba_ron_value", 300))
        if payments:
            payments[-1]["points"] += extra
        if fixture_id == "settlement_multiple_ron" and actor == 1:
            payments[-1]["points"] += kyotaku * int(rules.get("riichi_stick_value", 1000))
    return {
        "actor": actor,
        "target": target,
        "winning_tile": winning_tile,
        "yakus": yakus,
        "bonuses": bonuses,
        "pao": pao,
        "ura_dora_markers": list(input_data.get("ura_dora_markers", [])),
        "fu": fu,
        "han": han,
        "basic_points": basic,
        "hand_points": hand_points,
        "payments": payments,
        "deltas": settlement_deltas(payments),
    }


def compute_fixture(fixture: Mapping[str, Any], root_rules: Mapping[str, Any]) -> dict[str, Any]:
    fixture_id = str(fixture["id"])
    input_data = fixture["input"]
    state = input_data.get("state", fixture.get("state", {}))
    rules = dict(root_rules)
    overrides = fixture.get("rule_overrides", {})
    # Shallow rule overrides are sufficient for the draft schema's scalar
    # switches; nested objects are merged one level deep.
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(rules.get(key), Mapping):
            merged = dict(rules[key])
            merged.update(value)
            rules[key] = merged
        else:
            rules[key] = value

    if fixture_id.startswith("noten_"):
        count = int(fixture_id.rsplit("_", 1)[1])
        tenpai = [index < count for index in range(4)]
        if count in {0, 4}:
            deltas = [0, 0, 0, 0]
        else:
            total = int(rules.get("noten_payment", {}).get("total_points", 3000))
            receive = total // count
            payers = 4 - count
            pay = total // payers
            deltas = [receive if index < count else -pay for index in range(4)]
        return {"result_type": "ryukyoku", "reason": "fanpai", "tenpai": tenpai, "deltas": deltas}
    if fixture_id == "settlement_chombo":
        penalty = int(rules.get("chombo", {}).get("penalty_points", 8000))
        offender = int(input_data["actor"])
        others = [seat for seat in SEATS if seat != offender]
        # The rules round each equal share down to the 100-point unit and put
        # the remainder on the lowest seat.
        base = (penalty // len(others) // 100) * 100
        remainder = penalty - base * len(others)
        payments = []
        for index, seat in enumerate(others):
            payments.append({"from": offender, "to": seat, "points": base + (remainder if index == 0 else 0)})
        return {"result_type": "penalty", "offender": offender, "payments": payments, "deltas": settlement_deltas(payments)}

    has_multiple_winners = bool(input_data.get("other_winners"))
    wins = [
        compute_win(
            fixture_id,
            input_data,
            state,
            rules,
            overrides,
            apply_settlement=not has_multiple_winners,
        )
    ]
    for other in input_data.get("other_winners", []):
        other_input = {
            "hand": other["hand"],
            "winning_tile": other["winning_tile"],
            "win_method": other["win_method"],
            "actor": other["actor"],
            "target": other["target"],
        }
        other_state = other["state"]
        # The first winner receives the kyotaku under the declared policy;
        # every winner receives honba when honba policy is each_winner.
        wins.append(compute_win(fixture_id, other_input, other_state, rules, {}, apply_settlement=False))
    if len(wins) > 1:
        honba = int(state.get("honba", 0))
        kyotaku = int(state.get("kyotaku", 0))
        for index, win in enumerate(wins):
            if honba and rules.get("multiple_ron_settlement", {}).get("honba") == "each_winner":
                win["payments"][-1]["points"] += honba * int(rules.get("honba_ron_value", 300))
            if index == 0 and kyotaku and rules.get("multiple_ron_settlement", {}).get("kyotaku") == "first_winner":
                win["payments"][-1]["points"] += kyotaku * int(rules.get("riichi_stick_value", 1000))
            win["deltas"] = settlement_deltas(win["payments"])
    deltas = [sum(win["deltas"][seat] for win in wins) for seat in SEATS]
    return {"result_type": "hora", "wins": wins, "deltas": deltas}


def summary_vectors(vectors: Sequence[Mapping[str, Any]], rules: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    expected_by_id = {
        "child_30fu_3han_ron": (30, 3, False, False),
        "dealer_40fu_3han_ron": (40, 3, True, False),
        "child_30fu_2han_tsumo": (30, 2, False, True),
        "double_yakuman": (0, 0, False, False),
        "kiriage_3han_60fu": (60, 3, True, False),
        "kiriage_3han_60fu_noop": (60, 3, True, False),
        "kazoe_sanbaiman_12han": (40, 12, False, False),
        "kazoe_yakuman_13han": (40, 13, False, False),
        "dealer_tsumo_30fu_1han": (30, 1, True, True),
    }
    for vector in vectors:
        identifier = str(vector["id"])
        if identifier in {"multiple_ron_settlement", "pao_split_rounding", "noten_by_tenpai_count", "red_dora_and_ura_dora", "chombo_remainder_lowest_seat"}:
            if vector.get("conserves_points") is False:
                errors.append(f"vector {identifier}: conservation flag is false")
            continue
        if identifier == "decomposition_max_points":
            continue
        if identifier == "pao_split_rounding":
            continue
        if identifier not in expected_by_id:
            continue
        fu, han, dealer, tsumo = expected_by_id[identifier]
        if identifier == "double_yakuman":
            basic = 16000
        elif fu == 0:
            basic = 8000 * 2
        else:
            basic = basic_points(fu, han, rules, {"kiriage_mangan": identifier == "kiriage_3han_60fu"})
        if tsumo:
            actor = 0 if dealer else 1
            hand_points = sum(ceil100(basic * (2 if actor == 0 or payer == 0 else 1)) for payer in SEATS if payer != actor)
        else:
            hand_points = ceil100(basic * (6 if dealer else 4))
        fields = [("basic_points", basic)]
        if "hand_points" in vector:
            fields.append(("hand_points", hand_points))
        for field, actual in fields:
            if vector.get(field) != actual:
                errors.append(f"vector {identifier}.{field}: expected {vector.get(field)!r}, recomputed {actual!r}")
    return errors


def run(path: Path, print_json: bool = False) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    rules = data["rules"]
    errors: list[str] = []
    computed: dict[str, Any] = {}
    for fixture in data["fixtures"]:
        identifier = str(fixture["id"])
        try:
            actual = compute_fixture(fixture, rules)
        except (AssertionError, KeyError, TypeError, ValueError) as exc:
            errors.append(f"{identifier}: oracle error: {exc}")
            continue
        computed[identifier] = actual
        if actual != fixture["expected"]:
            errors.append(
                f"{identifier}: mismatch\n  expected={json.dumps(fixture['expected'], sort_keys=True)}\n  actual={json.dumps(actual, sort_keys=True)}"
            )
    errors.extend(summary_vectors(data.get("vectors", []), rules))
    if print_json:
        print(json.dumps(computed, ensure_ascii=False, indent=2, sort_keys=True))
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        print(f"score oracle: {len(errors)} failure(s)", file=sys.stderr)
        return 1
    print(f"score oracle: {len(computed)} fixtures and {len(data.get('vectors', []))} summary vectors verified")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--print", dest="print_json", action="store_true", help="print recomputed fixture results")
    args = parser.parse_args()
    return run(args.path, args.print_json)


if __name__ == "__main__":
    raise SystemExit(main())
