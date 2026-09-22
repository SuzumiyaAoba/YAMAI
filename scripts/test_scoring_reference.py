"""Scoring invariants beyond the individual official goldens."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from scoring_reference import TILES, ScoringError, calculate_fixture, score_hand, waits, validate_score_bounds, validate_win_declarations
from game_contract import GameError, check_hora_yaku_context


class ScoringInvariants(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / 'test-vectors/riichi-4p/1.0-draft.1/scoring.json'
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

    def test_computed_hands_pass_public_yaku_compatibility(self):
        for name, fixture in self.fixtures.items():
            if fixture['input']['type'] != 'hora':
                continue
            with self.subTest(fixture=name):
                rules = {**self.rules, **fixture.get('rule_overrides', {})}
                for win in calculate_fixture(fixture, self.rules).get('wins', []):
                    validate_win_declarations(win, rules)
                    data = fixture['input']
                    state = fixture['state']
                    if win['actor'] != data['actor']:
                        peer = next(w for w in data['other_winners'] if w['actor'] == win['actor'])
                        data, state = peer, peer['state']
                    kyoku, cause = self.public_context(data, state)
                    check_hora_yaku_context(win, kyoku, cause, rules)

    @staticmethod
    def public_context(data, state):
        actor = data['actor']
        melds = [[] for _ in range(4)]
        melds[actor] = [dict(type=m['kind']) for m in data['hand']['melds']]
        reach = [dict(state='none', double=False, ippatsu=False) for _ in range(4)]
        reach[actor] = dict(state='accepted' if state['reach_accepted'] else 'none',
                            double=state['double_riichi'], ippatsu=state['ippatsu'])
        first = [False] * 4
        first[actor] = state['first_turn']
        kyoku = dict(melds=melds, reach_status=reach, first_turn_eligible=first,
                     oya=state['oya'], rinshan=state['rinshan'], haitei=state['last_tile'])
        cause = dict(type=state['pending_kan']['kind'] + '_declared' if state['pending_kan']
                     else 'tsumo' if data['win_method'] == 'tsumo' else 'dahai')
        return kyoku, cause

    def test_public_kan_yaku_requires_exact_count_and_cannot_be_omitted(self):
        for name, role in (('yaku_sankantsu', 'sankantsu'), ('yakuman_suukantsu', 'suukantsu')):
            fixture = self.fixtures[name]
            win = calculate_fixture(fixture, self.rules)['wins'][0]
            kyoku, cause = self.public_context(fixture['input'], fixture['state'])
            check_hora_yaku_context(win, kyoku, cause, self.rules)
            for variant in ('absent_kan', 'omitted_role'):
                with self.subTest(role=role, variant=variant):
                    bad_win, bad_kyoku = deepcopy(win), deepcopy(kyoku)
                    if variant == 'absent_kan':
                        bad_kyoku['melds'][win['actor']][0]['type'] = 'pon'
                    else:
                        bad_win['yakus'] = [y for y in bad_win['yakus'] if y['id'] != role]
                    with self.assertRaises(GameError):
                        check_hora_yaku_context(bad_win, bad_kyoku, cause, self.rules)

    def test_public_fixed_melds_constrain_hidden_hand_roles(self):
        cases = (
            ('yaku_pinfu', ['ankan'], None),
            ('yaku_toitoi_sanankou', ['pon', 'pon'], None),
            ('yaku_toitoi_sanankou', ['chi'], None),
            ('yakuman_daisangen', ['chi', 'chi'], None),
            ('yakuman_shousuushii', ['chi', 'chi'], None),
            ('yakuman_daisuushii', ['chi'], None),
            ('yakuman_tsuuiisou', ['chi'], 'tsuuiisou'),
            ('yaku_honroutou', ['chi'], 'honroutou'),
        )
        for name, meld_types, only_role in cases:
            with self.subTest(fixture=name):
                fixture = self.fixtures[name]
                win = calculate_fixture(fixture, self.rules)['wins'][0]
                if only_role:
                    win['yakus'] = [y for y in win['yakus'] if y['id'] == only_role]
                kyoku, cause = self.public_context(fixture['input'], fixture['state'])
                kyoku['melds'][win['actor']] = [dict(type=t) for t in meld_types]
                with self.assertRaises((GameError, ScoringError)):
                    check_hora_yaku_context(win, kyoku, cause, self.rules)

    def test_big_four_winds_multiplier_follows_rule_in_both_directions(self):
        win = deepcopy(self.fixtures['yakuman_daisuushii']['expected']['wins'][0])
        for enabled in (False, True):
            rules = {**self.rules, 'double_yakuman': ['daisuushii'] if enabled else []}
            for value in (1, 2):
                with self.subTest(enabled=enabled, value=value):
                    win['yakus'][0]['value'] = value
                    if value == (2 if enabled else 1):
                        validate_win_declarations(win, rules)
                    else:
                        with self.assertRaises(ScoringError):
                            validate_win_declarations(win, rules)

    def test_four_concealed_triplets_ron_requires_tanki_multiplier(self):
        win = deepcopy(self.fixtures['yakuman_suukantsu']['expected']['wins'][0])
        for enabled in (False, True):
            rules = {**self.rules, 'double_yakuman': ['suuankou_tanki'] if enabled else []}
            for tsumo in (False, True):
                win['target'] = win['actor'] if tsumo else 0
                for value in (1, 2):
                    with self.subTest(enabled=enabled, tsumo=tsumo, value=value):
                        win['yakus'][0]['value'] = value
                        valid = value == 1 if not enabled else tsumo or value == 2
                        if valid:
                            validate_win_declarations(win, rules)
                        else:
                            with self.assertRaises(ScoringError):
                                validate_win_declarations(win, rules)

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

    def test_rule_bound_rejects_the_first_hundred_above_admissible_start(self):
        # Default rules reserve 19,230,400,000,000 points for the event budget.
        rules = {**self.rules, 'starting_points':8987968854740900}
        validate_score_bounds(rules)
        rules['starting_points'] += 100
        with self.assertRaises(ScoringError) as error:
            validate_score_bounds(rules)
        self.assertEqual(error.exception.code, 'invalid_message')

    def test_individually_safe_amounts_can_exceed_the_combined_budget(self):
        for changes in ({'honba_ron_value':200000}, {'riichi_stick_value':400000}):
            validate_score_bounds({**self.rules, **changes})
        f = deepcopy(self.fixtures['yaku_tanyao'])
        f['rule_overrides'].update(honba_ron_value=200000, riichi_stick_value=400000)
        with self.assertRaises(ScoringError) as error:
            calculate_fixture(f, self.rules)
        self.assertEqual(error.exception.code, 'invalid_message')

    def test_reviewed_overflow_is_rejected_before_scoring(self):
        f = deepcopy(self.fixtures['yaku_tanyao'])
        f['rule_overrides']['starting_points'] = 9007199254740900
        for state in (f['state'], f['state']['pre_state']):
            state['scores'] = [9007199254740900]*4
        with self.assertRaises(ScoringError) as error:
            calculate_fixture(f, self.rules)
        self.assertEqual(error.exception.code, 'invalid_message')

    def test_projected_kan_declaration_requires_normalized_tile(self):
        f = deepcopy(self.fixtures['yaku_rinshan'])
        next(e for e in f['state']['events'] if e['type'] == 'ankan_declared').pop('pai')
        with self.assertRaises(ScoringError) as error:
            calculate_fixture(f, self.rules)
        self.assertEqual(error.exception.code, 'invalid_message')
        f = deepcopy(self.fixtures['yaku_rinshan'])
        next(e for e in f['state']['events'] if e['type'] == 'ankan_declared')['pai'] = '5mr'
        with self.assertRaises(ScoringError) as error:
            calculate_fixture(f, self.rules)
        self.assertEqual(error.exception.code, 'invalid_context')

    def test_chankan_inventory_includes_the_opponents_entire_quad(self):
        for name in ('yaku_chankan', 'yakuman_kokushi_ankan_robbery'):
            with self.subTest(fixture=name):
                original = self.fixtures[name]
                self.assertEqual(calculate_fixture(original, self.rules), original['expected'])
                f = deepcopy(original)
                f['input']['dora_markers'][0] = f['state']['pending_kan']['pai']
                with self.assertRaises(ScoringError) as error:
                    calculate_fixture(f, self.rules)
                self.assertEqual(error.exception.code, 'invalid_hand')

        f = deepcopy(self.fixtures['yakuman_kokushi_ankan_robbery'])
        for state in (f['state'], f['state']['pre_state']):
            state.update(reach_accepted=True, kyotaku=1, scores=[25000, 24000, 25000, 25000])
        f['input']['ura_dora_markers'] = ['2p']
        self.assertEqual(calculate_fixture(f, self.rules)['result_type'], 'hora')
        f['input']['ura_dora_markers'] = ['E']
        with self.assertRaises(ScoringError) as error:
            calculate_fixture(f, self.rules)
        self.assertEqual(error.exception.code, 'invalid_hand')

    def test_chankan_declaration_requires_live_wall_and_kan_capacity(self):
        for wall, counts in ((0, [0, 0, 0, 0]), (40, [4, 0, 0, 0])):
            with self.subTest(wall=wall, counts=counts):
                f = deepcopy(self.fixtures['yaku_chankan'])
                for state in (f['state'], f['state']['pre_state']):
                    state.update(wall_remaining=wall, kan_counts=counts)
                f['input']['dora_markers'] = ['3m', '6m', '7m', '8m', '9p'][:1 + sum(counts)]
                with self.assertRaises(ScoringError) as error:
                    calculate_fixture(f, self.rules)
                self.assertEqual(error.exception.code, 'invalid_context')

    def test_multiple_ron_shares_last_tile_and_pending_kan(self):
        f = deepcopy(self.fixtures['settlement_multiple_ron'])
        contexts = [f['state'], f['input']['other_winners'][0]['state']]
        for state in contexts:
            state.update(wall_remaining=0, last_tile=True)
            state['pre_state'].update(wall_remaining=0, last_tile=True)
        self.assertEqual(calculate_fixture(f, self.rules)['result_type'], 'hora')
        contexts[1]['last_tile'] = contexts[1]['pre_state']['last_tile'] = False
        with self.assertRaises(ScoringError) as error:
            calculate_fixture(f, self.rules)
        self.assertEqual(error.exception.code, 'invalid_context')

        f = deepcopy(self.fixtures['yaku_chankan'])
        other = deepcopy(f['input'])
        other.pop('type')
        other.update(actor=2, state=deepcopy(f['state']))
        other['hand']['melds'][0]['tiles'] = ['P'] * 3
        other['hand']['concealed_tiles'][other['hand']['concealed_tiles'].index('5m')] = '5mr'
        f['input']['other_winners'] = [other]
        self.assertEqual(calculate_fixture(f, self.rules)['result_type'], 'hora')
        other['state'].update(pending_kan=None, events=[])
        with self.assertRaises(ScoringError) as error:
            calculate_fixture(f, self.rules)
        self.assertEqual(error.exception.code, 'invalid_context')


if __name__ == '__main__':
    unittest.main()
