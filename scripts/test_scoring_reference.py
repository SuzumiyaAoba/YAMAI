"""Scoring invariants beyond the individual official goldens."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from scoring_reference import TILES, calculate_fixture, score_hand, waits


class ScoringInvariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / 'test-vectors/yrc-0005/1.0-draft.4/scoring.json'
        data = json.loads(path.read_text())
        cls.rules = data['rules']
        cls.fixtures = {f['id']: f for f in data['fixtures']}
        cls.negatives = {f['id']: f for f in data['negative_fixtures']}

    def test_waits_need_no_yaku_but_cannot_use_a_fifth_tile(self):
        hand = self.negatives['N01_bonus_is_not_yaku']['input']['hand']
        self.assertEqual({TILES[i] for i in waits(hand, self.rules)}, {'E'})
        blocked = {
            'concealed_tiles': ['1m','2m','3m','4p','5p','6p','7s','8s','9s','E'],
            'melds': [{'kind':'pon','open':True,'source':0,'tiles':['E']*3}],
        }
        self.assertEqual(waits(blocked, self.rules), set())

    def test_ron_completed_triplet_preserves_menzen_but_not_suuankou(self):
        f = deepcopy(self.fixtures['yaku_menzen_tsumo'])
        f['input']['hand'] = {'concealed_tiles':['1m']*3+['2m']*3+['3p']*3+['4s']*2+['5s']*2,'melds':[]}
        f['input'].update(winning_tile='4s',win_method='ron',target=0)
        f['state']['events'] = []
        ron = score_hand(f['input'], f['state'], self.rules)
        self.assertEqual((ron['fu'], ron['han'], ron['hand_points']), (50,4,8000))
        self.assertEqual({y['id'] for y in ron['yakus']}, {'sanankou','toitoi'})
        f['input'].update(win_method='tsumo',target=1)
        tsumo = score_hand(f['input'], f['state'], self.rules)
        self.assertEqual(tsumo['yakus'], [{'id':'suuankou','value':1,'unit':'yakuman'}])
        self.assertEqual((tsumo['fu'], tsumo['hand_points']), (0,32000))

    def test_concealed_order_does_not_select_a_different_decomposition(self):
        original = self.fixtures['decomposition_max_points']
        tiles = original['input']['hand']['concealed_tiles']
        for order in (tiles[::-1], tiles[5:]+tiles[:5], tiles[::2]+tiles[1::2]):
            f = deepcopy(original)
            f['input']['hand']['concealed_tiles'] = order
            self.assertEqual(calculate_fixture(f, self.rules), original['expected'])

    def test_true_yakuman_suppresses_actual_dora(self):
        f = deepcopy(self.fixtures['yakuman_suuankou'])
        f['input']['dora_markers'] = ['9m']  # Three 1m would otherwise give three han.
        result = score_hand(f['input'], f['state'], self.rules)
        self.assertEqual((result['fu'], result['han'], result['bonuses']), (0,0,[]))
        self.assertEqual(result['hand_points'], 64000)

    def test_furiten_does_not_block_tsumo(self):
        f = deepcopy(self.fixtures['yaku_menzen_tsumo'])
        f['state']['furiten'] = True
        self.assertEqual(calculate_fixture(f, self.rules), f['expected'])

    def test_disabled_pao_retains_normal_payment_and_honba(self):
        f = deepcopy(self.fixtures['settlement_pao_partial_ron'])
        f['rule_overrides']['pao'] = {**self.rules['pao'], 'yakus':[]}
        result = calculate_fixture(f, self.rules)['wins'][0]
        self.assertEqual(result['pao'], [])
        self.assertEqual(result['hand_points'], 96000)
        self.assertEqual(result['payments'], [{'from':0,'to':1,'points':96300}])


if __name__ == '__main__':
    unittest.main()
