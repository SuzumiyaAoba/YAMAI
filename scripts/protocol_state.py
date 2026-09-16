"""Small executable lifecycle rules; scoring and transport are separate checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass
class Projection:
    game: str = "NOT_STARTED"
    turn: str = "idle"
    actor: int | None = None
    compound: tuple[str, int | None, str | None] | None = None
    pending_kan: tuple[str, int | None] | None = None
    ending: bool = False

    def request(self, seat: int) -> None:
        if self.game != "IN_KYOKU" or self.compound:
            raise ValueError("request interrupts a compound action or is outside a kyoku")
        if self.turn == "awaiting_action" and seat != self.actor:
            raise ValueError("self-turn request belongs to another seat")

    def apply(self, event: Mapping[str, Any], transaction: str | None = None) -> None:
        kind, actor = event["type"], event.get("actor", self.actor)
        if self.compound:
            expected, owner, tx = self.compound
            if kind != expected or actor != owner or transaction != tx:
                raise ValueError("compound action must commit its discard in the same transaction")
            self.compound = None
        if kind == "start_game":
            if self.game != "NOT_STARTED":
                raise ValueError("duplicate start_game")
            self.game = "READY"
        elif kind == "start_kyoku":
            if self.game != "READY" or self.ending:
                raise ValueError("start_kyoku requires start_game or a continuing end_kyoku")
            self.game, self.turn, self.actor = "IN_KYOKU", "awaiting_draw", event.get("oya", 0)
        elif kind == "end_game":
            if self.game != "READY" or not self.ending:
                raise ValueError("end_game requires a terminal end_kyoku")
            self.game, self.turn = "ENDED", "idle"
        else:
            if self.game != "IN_KYOKU":
                raise ValueError("event outside kyoku")
            if kind == "tsumo":
                if self.turn not in {"awaiting_draw", "awaiting_responses"} or self.pending_kan:
                    raise ValueError("draw before resolving the previous action")
                self.turn, self.actor = "awaiting_action", actor
            elif kind == "dahai":
                if self.turn != "awaiting_action" or actor != self.actor:
                    raise ValueError("discard outside actor's turn")
                self.turn = "awaiting_responses"
            elif kind == "reach":
                if self.turn != "awaiting_action" or actor != self.actor:
                    raise ValueError("reach must precede its discard")
                self.compound = ("dahai", actor, transaction)
            elif kind in {"chi", "pon"}:
                if self.turn != "awaiting_responses":
                    raise ValueError("call outside reaction phase")
                self.turn, self.actor = "awaiting_action", actor
                self.compound = ("dahai", actor, transaction)
            elif kind in {"ankan_declared", "kakan_declared"}:
                if self.turn != "awaiting_action" or actor != self.actor or self.pending_kan:
                    raise ValueError("invalid kan declaration")
                self.pending_kan = (kind.removesuffix("_declared"), actor)
                self.turn = "awaiting_responses"
            elif kind in {"ankan", "kakan"}:
                if self.pending_kan != (kind, actor):
                    raise ValueError("kan commit requires a matching declaration")
                self.pending_kan, self.turn = None, "awaiting_draw"
            elif kind == "daiminkan":
                if self.turn != "awaiting_responses" or self.pending_kan:
                    raise ValueError("invalid daiminkan")
                self.actor, self.turn = actor, "awaiting_draw"
            elif kind == "end_kyoku":
                if self.pending_kan and event.get("result", {}).get("type") != "hora":
                    raise ValueError("uncommitted kan can end only by robbery")
                self.game, self.turn, self.pending_kan = "READY", "idle", None
                self.ending = event.get("next", {}).get("type") == "end_game"

    @classmethod
    def from_snapshot(cls, state: Mapping[str, Any]) -> "Projection":
        kyoku = state.get("kyoku") or {}
        turn = kyoku.get("turn", {})
        pending = kyoku.get("pending_kan")
        return cls(game=state["game_phase"], turn=turn.get("phase", "idle"),
                   actor=turn.get("actor"),
                   pending_kan=(pending["type"].removesuffix("_declared"), pending["actor"]) if pending else None)


def next_round(rules: Mapping[str, Any], kyoku: Mapping[str, Any], scores: list[int],
               *, dealer_continues: bool, dealer_won: bool) -> tuple[str, int]:
    """Return next.type and extension_round after settlement (protocol §7.2)."""
    extra, oya = kyoku["extension_round"], kyoku["oya"]
    if rules["bankruptcy"] == "end_game" and min(scores) < rules["bankruptcy_threshold"]:
        return "end_game", extra
    final = extra == 0 and kyoku["kyoku"] == 4 and kyoku["bakaze"] == ("E" if rules["game_length"] == "tonpu" else "S")
    continued = "renchan" if dealer_continues else "rotate"
    extension = rules["extension"]
    target = extension["target_points"]
    if final and dealer_continues:
        first = min(range(4), key=lambda seat: (-scores[seat], seat))
        if dealer_won and rules["agariyame"] and first == oya and scores[oya] >= target:
            return "end_game", 0
        return "renchan", 0
    if final or extra > 0:
        if max(scores) >= target or extension["mode"] == "none" or extra >= extension["max_extra_rounds"]:
            return "end_game", extra
        return continued, extra + 1
    return continued, 0
