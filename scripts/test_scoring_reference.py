"""Scoring invariants beyond the individual official goldens."""
from copy import deepcopy
from itertools import combinations
import json
from pathlib import Path
import unittest

from scoring_reference import TILES, ScoringError, calculate_fixture, score_hand, waits, validate_score_bounds, validate_win_declarations
from game_contract import GameError, check_hora_yaku_context, check_hora_bonuses


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

    def test_red_bonus_declaration_respects_configured_stock(self):
        for configured in (0, 1, 3, 12):
            rules = deepcopy(self.rules)
            rules['red_fives'] = dict(zip('mps', {0: (0, 0, 0), 1: (1, 0, 0),
                                                  3: (1, 1, 1), 12: (4, 4, 4)}[configured]))
            for claimed in (0, configured, configured + 1):
                win = deepcopy(self.fixtures['yaku_menzen_tsumo']['expected']['wins'][0])
                win['bonuses'] = [dict(id='akadora', han=claimed)] if claimed else []
                with self.subTest(configured=configured, claimed=claimed):
                    if claimed > configured:
                        with self.assertRaisesRegex(ScoringError, 'configured physical red-five stock'):
                            validate_win_declarations(win, rules)
                    else:
                        validate_win_declarations(win, rules)

    def test_red_bonus_counts_disclosed_winning_tile_once_and_ignores_yakuman(self):
        for tsumo in (False, True):
            for visible in (False, True):
                win = dict(actor=1, target=1 if tsumo else 0, pai='5mr',
                           yakus=[dict(id='tanyao', unit='han', value=1)],
                           bonuses=[dict(id='akadora', han=1)], ura_dora_markers=[])
                concealed = ['2m', '3m', '4m', '3m', '4m', '4p', '5p', '6p',
                             '6p', '7p', '8p', '5m', '5m']
                hands = [{'count': 13} for _ in range(4)]
                hands[1] = ({'tiles': concealed + (['5mr'] if tsumo else [])} if visible
                            else {'count': 14 if tsumo else 13})
                rivers = [[] for _ in range(4)]
                if not tsumo:
                    rivers[0] = [dict(pai='5mr', called_by=None)]
                kyoku = dict(hands=hands, melds=[[] for _ in range(4)], rivers=rivers,
                             pending_kan=None, dora_markers=['5pr'])
                # The two other red fives are indicators.
                win['ura_dora_markers'] = ['5sr']
                with self.subTest(tsumo=tsumo, visible=visible):
                    # The disclosed 5pr indicator also reveals two 6p.
                    if visible:
                        win['bonuses'].append(dict(id='dora', han=2))
                    check_hora_bonuses([win], kyoku, self.rules)
                    for claimed in (0, 2):
                        bad = deepcopy(win)
                        bad['bonuses'] = [dict(id='akadora', han=claimed)] if claimed else []
                        with self.assertRaisesRegex(GameError, 'red bonus differs'):
                            check_hora_bonuses([bad], kyoku, self.rules)
                    # The same physical red tiles never add bonus to a
                    # true yakuman; this helper must not demand akadora.
                    win.update(yakus=[dict(id='suuankou', unit='yakuman', value=1)], bonuses=[])
                    check_hora_bonuses([win], kyoku, self.rules)

    def test_indicator_bonus_bounds_multiply_indicators_and_share_ron_inventory(self):
        def context(tile, markers):
            return dict(hands=[{'count': 13} for _ in range(4)], melds=[[] for _ in range(4)],
                        rivers=[[dict(pai=tile, called_by=None)], [], [], []],
                        pending_kan=None, dora_markers=markers)

        def winner(actor, tile, name, claimed, ura=()):
            return dict(actor=actor, target=0, pai=tile,
                        yakus=[dict(id='tanyao', unit='han', value=1)],
                        bonuses=[dict(id=name, han=claimed)], ura_dora_markers=list(ura))

        for name in ('dora', 'uradora'):
            for shared in (False, True):
                tile = '2m' if shared else '6s'
                markers = ['1m', '1m']
                kyoku = context(tile, markers if name == 'dora' else ['9p'])
                ura = markers if name == 'uradora' else []
                # Two copies of the indicator multiply every 2m by two.
                # Pool capacity is 8; a shared 2m ron adds two more virtual
                # bonus hits, while still consuming just one physical tile.
                legal = (4, 6) if shared else (4, 4)
                for claims in (legal, (6, 6)):
                    wins = [winner(actor, tile, name, claimed, ura)
                            for actor, claimed in zip((1, 2), claims)]
                    with self.subTest(name=name, shared=shared, claims=claims):
                        for win in wins:
                            check_hora_bonuses([win], kyoku, self.rules)
                        if claims == legal:
                            check_hora_bonuses(wins, kyoku, self.rules)
                        else:
                            with self.assertRaisesRegex(GameError, name + ' bonus.*across multiple winners'):
                                check_hora_bonuses(wins, kyoku, self.rules)
        # A non-riichi winner gets no ura bonus, but ura exposed by the other
        # winner still consumes red stock in both winners' visible inventory.
        kyoku = context('6s', ['9p'])
        first = winner(1, '6s', 'uradora', 1, ['5mr'])
        second = winner(2, '6s', 'akadora', 2)
        check_hora_bonuses([first, second], kyoku, self.rules)
        second['bonuses'][0]['han'] = 3
        with self.assertRaisesRegex(GameError, 'red bonus differs'):
            check_hora_bonuses([first, second], kyoku, self.rules)

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
                    win['pai'] = win['winning_tile']
                    kyoku, cause = self.public_context(data, state)
                    check_hora_yaku_context(win, kyoku, cause, rules)
                    # The same check must remain valid when this view reveals
                    # the complete concealed part as well as the public melds.
                    kyoku['hands'] = [{'count': 13} for _ in range(4)]
                    kyoku['hands'][win['actor']] = {'tiles': data['hand']['concealed_tiles']}
                    check_hora_yaku_context(win, kyoku, cause, rules)

    @staticmethod
    def public_context(data, state):
        actor = data['actor']
        melds = [[] for _ in range(4)]
        for m in data['hand']['melds']:
            event = dict(type=m['kind'], actor=actor)
            if m['kind'] == 'ankan':
                event['consumed'] = m['tiles'].copy()
            else:
                event.update(pai=m['tiles'][0], consumed=m['tiles'][1:], target=m['source'])
            melds[actor].append(event)
        reach = [dict(state='none', double=False, ippatsu=False) for _ in range(4)]
        reach[actor] = dict(state='accepted' if state['reach_accepted'] else 'none',
                            double=state['double_riichi'], ippatsu=state['ippatsu'])
        first = [False] * 4
        first[actor] = state['first_turn']
        kyoku = dict(melds=melds, reach_status=reach, first_turn_eligible=first,
                     oya=state['oya'], bakaze=state['bakaze'], rinshan=state['rinshan'], haitei=state['last_tile'])
        cause = dict(type=state['pending_kan']['kind'] + '_declared' if state['pending_kan']
                     else 'tsumo' if data['win_method'] == 'tsumo' else 'dahai')
        return kyoku, cause

    def test_impossible_yakuman_combinations_are_rejected(self):
        pairs = (
            ('kokushi_musou', 'daisangen'), ('chuuren_poutou', 'suuankou'),
            ('daisangen', 'daisuushii'), ('daisangen', 'shousuushii'),
            ('daisangen', 'ryuuiisou'), ('daisangen', 'chinroutou'),
            ('daisuushii', 'chinroutou'), ('shousuushii', 'ryuuiisou'),
            ('tsuuiisou', 'chinroutou'), ('tsuuiisou', 'ryuuiisou'),
            ('tenhou', 'suukantsu'), ('chiihou', 'suukantsu'),
        )
        for roles in pairs:
            with self.subTest(roles=roles):
                win = deepcopy(self.fixtures['yakuman_daisangen']['expected']['wins'][0])
                win['yakus'] = [dict(id=name, value=1, unit='yakuman') for name in sorted(roles)]
                with self.assertRaises(ScoringError):
                    validate_win_declarations(win)

    def test_valid_yakuman_combinations_remain_possible(self):
        for roles in (('kokushi_musou', 'chiihou'), ('chuuren_poutou', 'tenhou'),
                      ('daisangen', 'suuankou', 'tsuuiisou'),
                      ('daisuushii', 'suukantsu', 'tsuuiisou'), ('suuankou', 'ryuuiisou')):
            with self.subTest(roles=roles):
                win = deepcopy(self.fixtures['yakuman_daisangen']['expected']['wins'][0])
                win['yakus'] = [dict(id=name, value=1, unit='yakuman') for name in sorted(roles)]
                validate_win_declarations(win)

    def test_shousangen_requires_exactly_two_dragon_yakuhai_ids(self):
        fixture = self.fixtures['yaku_shousangen']
        kyoku, cause = self.public_context(fixture['input'], fixture['state'])
        # No meld or concealed tile discloses which dragons are present.
        # The declared shousangen alone requires both dragon yakuhai IDs.
        kyoku['melds'] = [[] for _ in range(4)]
        kyoku['hands'] = [{'count': 13} for _ in range(4)]
        dragons = ('yakuhai_haku', 'yakuhai_hatsu', 'yakuhai_chun')
        for count in range(4):
            for roles in combinations(dragons, count):
                win = deepcopy(fixture['expected']['wins'][0])
                win['yakus'] = [dict(id=name, value=2 if name == 'shousangen' else 1, unit='han')
                                for name in sorted(('shousangen', *roles))]
                win.update(pai='3m', han=2 + count)
                checks = (
                    ('declarations_only', lambda: validate_win_declarations(win)),
                    ('with_rules', lambda: validate_win_declarations(win, self.rules)),
                    ('hidden_hand', lambda: check_hora_yaku_context(win, kyoku, cause, self.rules)),
                )
                for context, check in checks:
                    with self.subTest(roles=roles, context=context):
                        if count == 2:
                            check()
                        else:
                            with self.assertRaisesRegex(ScoringError, 'exactly two dragon yakuhai') as error:
                                check()
                            self.assertEqual(error.exception.code, 'invalid_message')

    def test_each_small_dragon_pair_remains_valid_open_or_closed(self):
        dragon_roles = {'P': 'yakuhai_haku', 'F': 'yakuhai_hatsu', 'C': 'yakuhai_chun'}
        for pair in dragon_roles:
            for closed in (False, True):
                with self.subTest(pair=pair, closed=closed):
                    fixture = deepcopy(self.fixtures['yaku_shousangen'])
                    data = fixture['input']
                    triplets = [tile for tile in dragon_roles if tile != pair]
                    data['hand'] = dict(concealed_tiles=['1m', '2m', '3m', '4p', '5p', '6p', pair],
                                        melds=[])
                    data['winning_tile'] = pair
                    for tile in triplets:
                        if closed:
                            data['hand']['concealed_tiles'].extend([tile] * 3)
                        else:
                            data['hand']['melds'].append(dict(kind='pon', open=True, source=data['target'],
                                                             tiles=[tile] * 3))
                    win = calculate_fixture(fixture, self.rules)['wins'][0]
                    self.assertEqual({y['id'] for y in win['yakus']},
                                     {'shousangen', *(dragon_roles[tile] for tile in triplets)})
                    validate_win_declarations(win, self.rules, closed=closed)
                    win['pai'] = pair
                    kyoku, cause = self.public_context(data, fixture['state'])
                    check_hora_yaku_context(win, kyoku, cause, self.rules)

    def test_three_dragon_yakuhai_require_daisangen_instead(self):
        fixture = self.fixtures['yaku_shousangen']
        kyoku, cause = self.public_context(fixture['input'], fixture['state'])
        kyoku['melds'] = [[] for _ in range(4)]
        kyoku['hands'] = [{'count': 13} for _ in range(4)]
        dragons = ('yakuhai_haku', 'yakuhai_hatsu', 'yakuhai_chun')
        for count in (1, 2, 3):
            for roles in combinations(dragons, count):
                win = deepcopy(fixture['expected']['wins'][0])
                win['yakus'] = [dict(id=name, value=1, unit='han') for name in sorted(roles)]
                win.update(pai='3m', han=count)
                checks = (
                    ('declarations_only', lambda: validate_win_declarations(win)),
                    ('with_rules', lambda: validate_win_declarations(win, self.rules)),
                    ('hidden_hand', lambda: check_hora_yaku_context(win, kyoku, cause, self.rules)),
                )
                for context, check in checks:
                    with self.subTest(roles=roles, context=context):
                        if count < 3:
                            check()
                        else:
                            with self.assertRaisesRegex(ScoringError, 'require the big three dragons yakuman') as error:
                                check()
                            self.assertEqual(error.exception.code, 'invalid_message')

    def test_impossible_normal_yaku_combinations_are_rejected(self):
        cases = [
            {'menzen_tsumo': 1, 'toitoi': 2},
            {'menzen_tsumo': 1, 'sanankou': 2, 'toitoi': 2},
            {'chinitsu': 6, 'honroutou': 2},
            {'chinitsu': 6, 'honroutou': 2, 'toitoi': 2},
            {'chinitsu': 6, 'junchan': 3, 'sanankou': 2},
            {'chinitsu': 6, 'junchan': 3, 'sankantsu': 2},
        ]
        for sequences in ('sanshoku_doujun', 'ikkitsuukan'):
            for honor in ('yakuhai_haku', 'yakuhai_hatsu', 'yakuhai_chun', 'seat_wind', 'round_wind'):
                cases.append({'iipeikou': 1, sequences: 2, honor: 1})
        for roles in cases:
            fixture = self.fixtures['yaku_menzen_tsumo']
            win = deepcopy(fixture['expected']['wins'][0])
            tsumo = 'menzen_tsumo' in roles
            win.update(pai='1m', fu=40, han=sum(roles.values()), target=win['actor'] if tsumo else 0)
            win['yakus'] = [dict(id=name, value=value, unit='han') for name, value in sorted(roles.items())]
            kyoku, _ = self.public_context(fixture['input'], fixture['state'])
            kyoku['hands'] = [{'count': 13} for _ in range(4)]
            cause = dict(type='tsumo' if tsumo else 'dahai')
            checks = (
                ('declarations_only', lambda: validate_win_declarations(win)),
                ('with_rules', lambda: validate_win_declarations(win, self.rules)),
                ('hidden_hand', lambda: check_hora_yaku_context(win, kyoku, cause, self.rules)),
            )
            for context, check in checks:
                with self.subTest(roles=roles, context=context):
                    with self.assertRaises(ScoringError) as error:
                        check()
                    self.assertEqual(error.exception.code, 'invalid_message')

    def test_triplet_ron_and_open_tsumo_do_not_require_suuankou(self):
        for closed, tsumo in ((True, False), (False, False), (False, True)):
            with self.subTest(closed=closed, tsumo=tsumo):
                fixture = deepcopy(self.fixtures['yaku_toitoi_sanankou'])
                data = fixture['input']
                if closed:
                    data['hand'] = dict(concealed_tiles=['1m'] * 3 + ['2m'] * 3 + ['3p'] * 3
                                        + ['4s'] * 2 + ['5s'] * 2, melds=[])
                    data['winning_tile'] = '4s'
                data.update(win_method='tsumo' if tsumo else 'ron', target=data['actor'] if tsumo else 0)
                win = calculate_fixture(fixture, self.rules)['wins'][0]
                self.assertEqual({y['id'] for y in win['yakus']}, {'sanankou', 'toitoi'})
                validate_win_declarations(win, self.rules, closed=closed)
                win['pai'] = data['winning_tile']
                kyoku, cause = self.public_context(data, fixture['state'])
                check_hora_yaku_context(win, kyoku, cause, self.rules)

    def test_four_sequence_combinations_remain_possible(self):
        for role, tiles in (
            ('sanshoku_doujun', ['4m', '5m', '6m'] * 2 + ['4p', '5p', '6p', '4s', '5s', '6s']),
            ('ikkitsuukan', ['1m', '2m', '3m'] * 2 + ['4m', '5m', '6m', '7m', '8m', '9m']),
        ):
            with self.subTest(role=role):
                fixture = deepcopy(self.fixtures['yaku_shousangen'])
                data = fixture['input']
                data['hand'] = dict(concealed_tiles=tiles + ['E'], melds=[])
                data['winning_tile'] = 'E'
                win = calculate_fixture(fixture, self.rules)['wins'][0]
                self.assertTrue({'iipeikou', role} <= {y['id'] for y in win['yakus']})
                validate_win_declarations(win, self.rules, closed=True)
                win['pai'] = 'E'
                kyoku, cause = self.public_context(data, fixture['state'])
                check_hora_yaku_context(win, kyoku, cause, self.rules)

    def test_half_flush_all_terminals_and_honors_remains_possible(self):
        fixture = deepcopy(self.fixtures['yaku_honroutou'])
        fixture['input']['hand']['concealed_tiles'] = ['9m'] * 3 + ['P'] * 3 + ['F'] * 3 + ['E']
        for case in (fixture, self.fixtures['yaku_chinitsu']):
            with self.subTest(fixture=case['id']):
                win = calculate_fixture(case, self.rules)['wins'][0]
                expected = {'honitsu', 'honroutou'} if case is fixture else {'chinitsu'}
                self.assertTrue(expected <= {y['id'] for y in win['yakus']})
                validate_win_declarations(win, self.rules)
                win['pai'] = case['input']['winning_tile']
                kyoku, cause = self.public_context(case['input'], case['state'])
                check_hora_yaku_context(win, kyoku, cause, self.rules)

    def test_all_terminals_and_honors_declares_its_hand_form(self):
        for form, tsumo in (('toitoi', False), ('chiitoitsu', False), ('chiitoitsu', True)):
            fixture = deepcopy(self.fixtures['yaku_honroutou'])
            data = fixture['input']
            if form == 'chiitoitsu':
                data['hand'] = dict(concealed_tiles=[tile for tile in ('1m', '9m', '1p', '9p', '1s', '9s')
                                                     for _ in range(2)] + ['E'], melds=[])
            data.update(win_method='tsumo' if tsumo else 'ron', target=data['actor'] if tsumo else 0)
            win = calculate_fixture(fixture, self.rules)['wins'][0]
            self.assertTrue({'honroutou', form} <= {y['id'] for y in win['yakus']})
            win['pai'] = 'E'
            kyoku, cause = self.public_context(data, fixture['state'])
            kyoku['hands'] = [{'count': 13} for _ in range(4)]
            checks = (
                ('declarations_only', lambda: validate_win_declarations(win)),
                ('with_rules', lambda: validate_win_declarations(win, self.rules)),
                ('hidden_hand', lambda: check_hora_yaku_context(win, kyoku, cause, self.rules)),
            )
            for _, check in checks:
                check()
            win['yakus'] = [y for y in win['yakus'] if y['id'] != form]
            win['han'] -= 2
            win['fu'] = 40  # Do not reject just because 25 fu implies seven pairs.
            for context, check in checks:
                with self.subTest(form=form, tsumo=tsumo, context=context):
                    with self.assertRaisesRegex(ScoringError, 'requires all triplets or seven pairs') as error:
                        check()
                    self.assertEqual(error.exception.code, 'invalid_message')

    def test_one_suit_pure_outside_hand_can_have_one_terminal_triplet(self):
        for triplet, pair, sequence, repeated in (
            ('1m', '9m', ['1m', '2m', '3m'], ['7m', '8m', '9m']),
            ('9m', '1m', ['7m', '8m', '9m'], ['1m', '2m', '3m']),
        ):
            with self.subTest(triplet=triplet, pair=pair):
                fixture = deepcopy(self.fixtures['yaku_chinitsu'])
                data = fixture['input']
                data['hand'] = dict(concealed_tiles=[triplet] * 3 + sequence + repeated * 2 + [pair],
                                    melds=[])
                data['winning_tile'] = pair
                win = calculate_fixture(fixture, self.rules)['wins'][0]
                self.assertEqual({y['id'] for y in win['yakus']}, {'chinitsu', 'junchan', 'iipeikou'})
                validate_win_declarations(win, self.rules, closed=True)
                win['pai'] = pair
                kyoku, cause = self.public_context(data, fixture['state'])
                check_hora_yaku_context(win, kyoku, cause, self.rules)

    def test_public_tiles_constrain_roles_without_revealing_the_hand(self):
        cases = (('yaku_tanyao', '1m'), ('yakuman_tsuuiisou', '9s'),
                 ('yakuman_ryuuiisou', 'S'), ('yakuman_chinroutou', 'E'),
                 ('yakuman_kokushi', '5mr'))
        for name, tile in cases:
            fixture = self.fixtures[name]
            with self.subTest(fixture=fixture['id'], tile=tile):
                rules = {**self.rules, **fixture.get('rule_overrides', {})}
                win = calculate_fixture(fixture, self.rules)['wins'][0]
                win['pai'] = win['winning_tile']
                kyoku, cause = self.public_context(fixture['input'], fixture['state'])
                check_hora_yaku_context(win, kyoku, cause, rules)
                win['pai'] = tile
                with self.assertRaises(GameError):
                    check_hora_yaku_context(win, kyoku, cause, rules)

    def test_public_honor_meld_cannot_lose_its_yaku(self):
        fixture = self.fixtures['yakuman_daisangen']
        win = calculate_fixture(fixture, self.rules)['wins'][0]
        win['pai'] = win['winning_tile']
        kyoku, cause = self.public_context(fixture['input'], fixture['state'])
        # Complete public sets establish daisangen irrespective of what is
        # concealed; a different yakuman cannot replace that declaration.
        actor = win['actor']
        kyoku['melds'][actor] = [dict(type='pon', actor=actor, target=(actor+1)%4,
                                    pai=tile, consumed=[tile, tile]) for tile in ('P', 'F', 'C')]
        win['yakus'] = [dict(id='tsuuiisou', value=1, unit='yakuman')]
        win['pai'] = 'E'
        with self.assertRaisesRegex(GameError, 'public melds is missing'):
            check_hora_yaku_context(win, kyoku, cause, self.rules)

    def test_four_fixed_melds_determine_the_small_dragon_or_wind_pair(self):
        for name, tiles, pair in (('yaku_shousangen', ('P', 'F', '1m', '2p'), 'C'),
                                  ('yakuman_shousuushii', ('E', 'S', 'W', '2p'), 'N')):
            with self.subTest(fixture=name):
                fixture = deepcopy(self.fixtures[name])
                data = fixture['input']
                source = (data['actor'] + 1) % 4
                data.update(winning_tile=pair, win_method='ron', target=source)
                data['hand'] = dict(concealed_tiles=[pair], melds=[
                    dict(kind='pon', open=True, source=source, tiles=[tile] * 3)
                    for tile in tiles])
                win = calculate_fixture(fixture, self.rules)['wins'][0]
                win['pai'] = pair
                kyoku, cause = self.public_context(data, fixture['state'])
                check_hora_yaku_context(win, kyoku, cause, self.rules)
                win['pai'] = '9s'
                with self.assertRaisesRegex(GameError, 'wrong disclosed pair'):
                    check_hora_yaku_context(win, kyoku, cause, self.rules)

    def test_public_meld_fu_is_a_lower_bound(self):
        fixture = self.fixtures['yaku_sankantsu']
        rules = {**self.rules, **fixture.get('rule_overrides', {})}
        win = calculate_fixture(fixture, self.rules)['wins'][0]
        win['pai'] = win['winning_tile']
        kyoku, cause = self.public_context(fixture['input'], fixture['state'])
        check_hora_yaku_context(win, kyoku, cause, rules)
        win['fu'] = 30
        with self.assertRaisesRegex(GameError, 'fu is below'):
            check_hora_yaku_context(win, kyoku, cause, rules)

    def test_public_kan_yaku_requires_exact_count_and_cannot_be_omitted(self):
        for name, role in (('yaku_sankantsu', 'sankantsu'), ('yakuman_suukantsu', 'suukantsu')):
            fixture = self.fixtures[name]
            win = calculate_fixture(fixture, self.rules)['wins'][0]
            win['pai'] = win['winning_tile']
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

    def test_rinshan_cancels_ippatsu_with_or_without_event_projection(self):
        original = self.fixtures['timing_riichi_rinshan_cancels_ippatsu']
        self.assertEqual(calculate_fixture(original, self.rules), original['expected'])
        f = deepcopy(original)
        f['state']['events'] = []
        f['state']['pre_state'] = {k: deepcopy(v) for k, v in f['state'].items()
                                  if k not in {'events', 'pre_state', 'furiten'}}
        self.assertEqual(calculate_fixture(f, self.rules), original['expected'])
        for state in (f['state'], f['state']['pre_state']):
            state['ippatsu'] = True
        with self.assertRaises(ScoringError) as error:
            calculate_fixture(f, self.rules)
        self.assertEqual(error.exception.code, 'invalid_context')

        win = deepcopy(original['expected']['wins'][0])
        win['yakus'].insert(0, dict(id='ippatsu', value=1, unit='han'))
        win['han'] += 1
        with self.assertRaisesRegex(ScoringError, 'mutually exclusive'):
            validate_win_declarations(win, self.rules)

    def test_first_draw_cannot_survive_another_seats_committed_kan(self):
        for name in ('yakuman_tenhou', 'yakuman_chiihou'):
            with self.subTest(fixture=name):
                f = deepcopy(self.fixtures[name])
                f['state']['events'] = []
                f['state']['pre_state'] = {k: deepcopy(v) for k, v in f['state'].items()
                                          if k not in {'events', 'pre_state', 'furiten'}}
                self.assertEqual(calculate_fixture(f, self.rules), f['expected'])
                for state in (f['state'], f['state']['pre_state']):
                    state['kan_counts'][(f['input']['actor'] + 1) % 4] = 1
                f['input']['dora_markers'].append('8m')
                with self.assertRaises(ScoringError) as error:
                    calculate_fixture(f, self.rules)
                self.assertEqual(error.exception.code, 'invalid_context')

    def test_draw_kan_counts_match_every_seats_hand(self):
        for name in ('noten_1', 'noten_1_with_ankan'):
            original = self.fixtures[name]
            self.assertEqual(calculate_fixture(original, self.rules), original['expected'])
            for seat in range(4):
                with self.subTest(fixture=name, seat=seat):
                    f = deepcopy(original)
                    for state in (f['state'], f['state']['pre_state']):
                        state['kan_counts'][seat] = 1 - state['kan_counts'][seat]
                    with self.assertRaises(ScoringError) as error:
                        calculate_fixture(f, self.rules)
                    self.assertEqual(error.exception.code, 'invalid_context')

    def test_nonwinning_scalar_facts_cannot_contradict_themselves(self):
        from score_oracle import validate_fixture_input
        from validate_artifacts import SchemaSet
        schemas = SchemaSet()
        invalid = (
            dict(double_riichi=True), dict(ippatsu=True),
            dict(first_turn=True, reach_accepted=True, kyotaku=1),
            dict(first_turn=True, kan_counts=[1, 0, 0, 0]),
            dict(first_turn=True, pending_kan=dict(kind='kakan', actor=1, pai='5m')),
            dict(rinshan=True),
            dict(rinshan=True, ippatsu=True, reach_accepted=True, kyotaku=1,
                 kan_counts=[1, 0, 0, 0]),
            dict(rinshan=True, last_tile=True, kan_counts=[1, 0, 0, 0]),
            dict(reach_accepted=True),
            dict(kan_counts=[2, 1, 1, 1]),
            dict(wall_remaining=70, kan_counts=[1, 0, 0, 0]),
            dict(wall_remaining=69, kan_counts=[1, 0, 0, 0]),
            dict(first_turn=True, wall_remaining=40),
            dict(last_tile=True, wall_remaining=40),
            dict(pending_kan=dict(kind='ankan', actor=1, pai='E'), wall_remaining=0),
            dict(pending_kan=dict(kind='ankan', actor=1, pai='E'), kan_counts=[4, 0, 0, 0]),
            dict(double_riichi=True, reach_accepted=True, kyotaku=1, wall_remaining=70),
            dict(double_riichi=True, ippatsu=True, reach_accepted=True, kyotaku=1),
            dict(double_riichi=True, ippatsu=True, reach_accepted=True, kyotaku=1,
                 wall_remaining=65, kan_counts=[1, 0, 0, 0]),
            dict(double_riichi=True, ippatsu=True, reach_accepted=True, kyotaku=1,
                 wall_remaining=65, pending_kan=dict(kind='kakan', actor=1, pai='5m')),
        )
        for name in ('noten_1', 'settlement_chombo'):
            for patch in invalid:
                fixture = deepcopy(self.fixtures[name])
                for state in (fixture['state'], fixture['state']['pre_state']):
                    state.update(deepcopy(patch))
                    state['scores'] = [25000 - 1000 * state['kyotaku'], 25000, 25000, 25000]
                validate_fixture_input(fixture, self.rules, schemas)
                with self.subTest(fixture=name, patch=patch):
                    with self.assertRaises(ScoringError) as error:
                        calculate_fixture(fixture, self.rules)
                    self.assertEqual(error.exception.code, 'invalid_context')

    def test_projection_cannot_launder_impossible_prior_scalar_facts(self):
        for name in ('noten_1', 'settlement_chombo'):
            fixture = deepcopy(self.fixtures[name])
            # This call clears ippatsu in the output, but it cannot make an
            # earlier ippatsu period without accepted riichi legitimate.
            fixture['state']['pre_state']['ippatsu'] = True
            fixture['state']['events'] = [dict(type='pon', actor=1)]
            with self.subTest(fixture=name):
                with self.assertRaisesRegex(ScoringError, 'without accepted riichi') as error:
                    calculate_fixture(fixture, self.rules)
                self.assertEqual(error.exception.code, 'invalid_context')
        for patch in (dict(first_turn=False), dict(first_turn=False, reach_accepted=True, kyotaku=1),
                      dict(first_turn=False, pending_kan=dict(kind='ankan', actor=3, pai='E'))):
            fixture = deepcopy(self.fixtures['settlement_chombo'])
            for state in (fixture['state'], fixture['state']['pre_state']):
                state.update(patch)
                state['scores'] = [25000 - 1000 * state['kyotaku'], 25000, 25000, 25000]
            fixture['state']['pre_state']['wall_remaining'] = 70
            fixture['state']['wall_remaining'] = 69
            # A later first draw/call cannot legalize the impossible input
            # checkpoint even when all contradictory flags disappear.
            fixture['state']['events'] = [dict(type='tsumo', actor=0, pai='1m')]
            if patch.get('pending_kan'):
                fixture['state']['events'].extend([dict(type='ankan', actor=3, pai='E'),
                                                  dict(type='tsumo', actor=3, pai='2p')])
                fixture['state'].update(wall_remaining=68, pending_kan=None, kan_counts=[0, 0, 0, 1])
            with self.subTest(initial_patch=patch):
                with self.assertRaisesRegex(ScoringError, 'initial scalar state'):
                    calculate_fixture(fixture, self.rules)

    def test_projection_riichi_observations_are_once_per_seat(self):
        from score_oracle import validate_fixture_input
        from validate_artifacts import SchemaSet
        schemas = SchemaSet()
        for evaluated in range(4):
            for seat in range(4):
                for kinds in (('reach', 'reach'), ('reach_accepted', 'reach_accepted'),
                              ('reach_accepted', 'reach'), ('reach', 'reach_accepted', 'reach'),
                              ('reach', 'reach_accepted', 'reach_accepted')):
                    fixture = deepcopy(self.fixtures['settlement_chombo'])
                    fixture['input']['offender'] = evaluated
                    accepted = kinds.count('reach_accepted')
                    fixture['state']['events'] = [dict(type=kind, actor=seat) for kind in kinds]
                    fixture['state']['kyotaku'] = accepted
                    fixture['state']['scores'][seat] -= 1000 * accepted
                    if evaluated == seat and accepted:
                        fixture['state'].update(reach_accepted=True, ippatsu=True)
                    validate_fixture_input(fixture, self.rules, schemas)
                    with self.subTest(evaluated=evaluated, seat=seat, kinds=kinds):
                        with self.assertRaises(ScoringError) as error:
                            calculate_fixture(fixture, self.rules)
                        self.assertEqual(error.exception.code, 'invalid_context')
            for first, second in ((a, b) for a in range(4) for b in range(4)
                                  if a != b and evaluated not in (a, b)):
                fixture = deepcopy(self.fixtures['settlement_chombo'])
                fixture['input']['offender'] = evaluated
                # The other seats may already have declared before this
                # projection. Their initial declaration facts are unrepresented;
                # observing each distinct acceptance once remains legitimate.
                fixture['state']['events'] = [dict(type='reach_accepted', actor=seat)
                                               for seat in (first, second)]
                fixture['state']['kyotaku'] = 2
                for seat in (first, second):
                    fixture['state']['scores'][seat] -= 1000
                self.assertEqual(calculate_fixture(fixture, self.rules)['kyotaku'], 2)

    def test_first_normal_draw_riichi_preserves_initial_dealer_ankan(self):
        from scoring_reference import validate_scalar_context
        for oya in range(4):
            for evaluated in range(4):
                for own_kans in range(3):
                    for other_kans in range(2):
                        for double in (False, True):
                            state = deepcopy(self.fixtures['settlement_chombo']['state'])
                            kans = [0] * 4
                            kans[evaluated], kans[(evaluated + 1) % 4] = own_kans, other_kans
                            state.update(oya=oya, wall_remaining=69 - sum(kans), kan_counts=kans,
                                         reach_accepted=True, double_riichi=double, kyotaku=1)
                            valid = evaluated == oya and double == (own_kans == 0)
                            with self.subTest(oya=oya, evaluated=evaluated, own=own_kans,
                                              other=other_kans, double=double):
                                if valid:
                                    validate_scalar_context(state, evaluated, self.rules)
                                else:
                                    with self.assertRaises(ScoringError) as error:
                                        validate_scalar_context(state, evaluated, self.rules)
                                    self.assertEqual(error.exception.code, 'invalid_context')
        # The first normal draw can lead to an ankan, replacement draw,
        # ordinary riichi and an opponent's call/discard without another
        # normal draw. The call creates the dealer's next reaction request.
        fixture = deepcopy(self.fixtures['settlement_chombo'])
        fixture['state']['pre_state'].update(wall_remaining=70, first_turn=True)
        fixture['state'].update(wall_remaining=68, kan_counts=[1, 0, 0, 0],
                                reach_accepted=True, double_riichi=False, kyotaku=1,
                                scores=[24000, 25000, 25000, 25000], events=[
                                    dict(type='tsumo', actor=0, pai='E'),
                                    dict(type='ankan_declared', actor=0, pai='E'),
                                    dict(type='ankan', actor=0, pai='E'),
                                    dict(type='tsumo', actor=0, pai='C'),
                                    dict(type='reach', actor=0),
                                    dict(type='dahai', actor=0, pai='C'),
                                    dict(type='reach_accepted', actor=0),
                                    dict(type='pon', actor=1, pai='C'),
                                    dict(type='dahai', actor=1, pai='S')])
        self.assertEqual(calculate_fixture(fixture, self.rules)['result_type'], 'penalty')

    def test_initial_projected_event_requires_the_dealers_first_draw(self):
        from scoring_reference import validate_state_projection
        kinds = ('tsumo', 'dahai', 'reach', 'reach_accepted', 'chi', 'pon',
                 'daiminkan', 'ankan_declared', 'ankan', 'kakan_declared', 'kakan')
        for oya in range(4):
            for evaluated in range(4):
                for kind in kinds:
                    for owner in range(4):
                        state = deepcopy(self.fixtures['settlement_chombo']['state'])
                        state.update(oya=oya, first_turn=True, wall_remaining=69,
                                     events=[dict(type=kind, actor=owner, pai='E')])
                        state['pre_state'].update(oya=oya, first_turn=True, wall_remaining=70)
                        with self.subTest(oya=oya, evaluated=evaluated, kind=kind, owner=owner):
                            if kind == 'tsumo' and owner == oya:
                                validate_state_projection(state, evaluated, self.rules)
                            else:
                                with self.assertRaisesRegex(ScoringError, 'initial projection must begin'):
                                    validate_state_projection(state, evaluated, self.rules)

    def test_first_draw_riichi_observations_preserve_unknown_other_seat_status(self):
        from scoring_reference import validate_state_projection
        for oya in range(4):
            for evaluated in range(4):
                for owner in range(4):
                    for acceptance_only in (False, True):
                        if acceptance_only and owner == evaluated:
                            continue  # The evaluated declaration must be captured.
                        state = deepcopy(self.fixtures['settlement_chombo']['state'])
                        state['pre_state'].update(oya=oya, first_turn=True, wall_remaining=69)
                        state.update(oya=oya, wall_remaining=69, first_turn=evaluated != owner,
                                     reach_accepted=evaluated == owner, double_riichi=evaluated == owner,
                                     ippatsu=evaluated == owner, kyotaku=1)
                        state['scores'][owner] -= 1000
                        state['events'] = ([] if acceptance_only else
                                           [dict(type='reach', actor=owner), dict(type='dahai', actor=owner, pai='E')])
                        state['events'].append(dict(type='reach_accepted', actor=owner))
                        with self.subTest(oya=oya, evaluated=evaluated, owner=owner,
                                          acceptance_only=acceptance_only):
                            if owner == oya:
                                validate_state_projection(state, evaluated, self.rules)
                            else:
                                with self.assertRaises(ScoringError) as error:
                                    validate_state_projection(state, evaluated, self.rules)
                                self.assertEqual(error.exception.code, 'invalid_context')

    def test_projection_cannot_repair_an_impossible_intermediate_qualification(self):
        from scoring_reference import validate_state_projection
        for double_ippatsu in (False, True):
            state = deepcopy(self.fixtures['settlement_chombo']['state'])
            state.update(wall_remaining=64 if double_ippatsu else 68,
                         reach_accepted=double_ippatsu, double_riichi=double_ippatsu,
                         kyotaku=int(double_ippatsu), scores=[24000 if double_ippatsu else 25000, 25000, 25000, 25000],
                         events=[dict(type='tsumo', actor=1, pai='2m'),
                                 dict(type='pon', actor=2, pai='2m'), dict(type='dahai', actor=2, pai='3m')])
            state['pre_state'] = {key: deepcopy(value) for key, value in state.items()
                                  if key not in {'pre_state', 'events', 'furiten'}}
            state['pre_state'].update(wall_remaining=65 if double_ippatsu else 69,
                                      first_turn=not double_ippatsu, ippatsu=double_ippatsu)
            with self.subTest(double_ippatsu=double_ippatsu):
                with self.assertRaisesRegex(ScoringError, 'first cycle|initial draw cycle'):
                    validate_state_projection(state, 0, self.rules)

    def test_projected_observed_successor_obligations_cannot_be_skipped(self):
        from scoring_reference import validate_state_projection
        kinds = ('tsumo', 'dahai', 'reach', 'reach_accepted', 'chi', 'pon',
                 'daiminkan', 'ankan_declared', 'ankan', 'kakan_declared', 'kakan')
        for evaluated in range(4):
            for owner in range(4):
                for kind in ('reach', 'chi', 'pon', 'ankan', 'kakan', 'daiminkan'):
                    state = deepcopy(self.fixtures['settlement_chombo']['state'])
                    kan = kind in {'ankan', 'kakan', 'daiminkan'}
                    tile = '5mr' if kind == 'kakan' else 'E'
                    prefix = ([dict(type=kind + '_declared', actor=owner, pai=tile)]
                              if kind in {'ankan', 'kakan'} else [])
                    prefix.append(dict(type=kind, actor=owner, pai=tile))
                    if kan:
                        state['kan_counts'][owner] = 1
                        state['wall_remaining'] = 39
                    state['events'] = prefix
                    with self.subTest(evaluated=evaluated, owner=owner, kind=kind, suffix='omitted'):
                        with self.assertRaisesRegex(ScoringError, 'omits its'):
                            validate_state_projection(state, evaluated, self.rules)
                    for following in kinds:
                        for successor in range(4):
                            if following == ('tsumo' if kan else 'dahai') and successor == owner:
                                continue
                            candidate = deepcopy(state)
                            candidate['events'].append(dict(type=following, actor=successor, pai='2m'))
                            with self.subTest(evaluated=evaluated, owner=owner, kind=kind,
                                              following=following, successor=successor):
                                with self.assertRaisesRegex(ScoringError, 'not followed by its'):
                                    validate_state_projection(candidate, evaluated, self.rules)
                    state['events'].append(dict(type='tsumo' if kan else 'dahai', actor=owner, pai='2m'))
                    state['rinshan'] = kan and owner == evaluated
                    validate_state_projection(state, evaluated, self.rules)

    def test_projected_pending_kan_keeps_its_commit_and_terminal_reaction(self):
        from scoring_reference import validate_state_projection
        for kind, tile in (('ankan', 'E'), ('kakan', '5mr')):
            for owner in range(4):
                for evaluated in range(4):
                    state = deepcopy(self.fixtures['settlement_chombo']['state'])
                    state['pending_kan'] = dict(kind=kind, actor=owner, pai=tile)
                    state['events'] = [dict(type=kind + '_declared', actor=owner, pai=tile)]
                    # A final declaration is a legitimate robbery/penalty
                    # reaction boundary; unlike a commit, it owes no draw.
                    validate_state_projection(state, evaluated, self.rules)
                    for other in ('tsumo', 'dahai', 'reach', 'reach_accepted', 'chi', 'pon',
                                  'daiminkan', 'ankan_declared', 'kakan_declared'):
                        candidate = deepcopy(state)
                        candidate['events'].append(dict(type=other, actor=(owner + 1) % 4, pai='2m'))
                        with self.subTest(kind=kind, owner=owner, evaluated=evaluated, other=other):
                            with self.assertRaisesRegex(ScoringError, 'pending kan is interrupted'):
                                validate_state_projection(candidate, evaluated, self.rules)
                    # An explicitly pending pre-state can commit in this
                    # capture; prior unrelated draw obligations are unknown.
                    state['pre_state']['pending_kan'] = deepcopy(state['pending_kan'])
                    state.update(pending_kan=None, wall_remaining=39, rinshan=owner == evaluated,
                                 events=[dict(type=kind, actor=owner, pai=tile),
                                         dict(type='tsumo', actor=owner, pai='2m')])
                    state['kan_counts'][owner] = 1
                    validate_state_projection(state, evaluated, self.rules)
                    state['events'] = []
                    state['pre_state'] = {key: deepcopy(value) for key, value in state.items()
                                          if key not in {'pre_state', 'events', 'furiten'}}
                    validate_state_projection(state, evaluated, self.rules)

    def test_direct_scoring_preserves_double_riichi_ippatsu_boundaries(self):
        for wall, valid in ((65, True), (63, False), (69, False)):
            fixture = deepcopy(self.fixtures['yaku_double_riichi'])
            fixture['state'].update(wall_remaining=wall, ippatsu=True, events=[])
            with self.subTest(wall=wall):
                if valid:
                    ids = {y['id'] for y in score_hand(fixture['input'], fixture['state'], self.rules)['yakus']}
                    self.assertTrue({'double_riichi', 'ippatsu'} <= ids)
                else:
                    with self.assertRaises(ScoringError) as error:
                        score_hand(fixture['input'], fixture['state'], self.rules)
                    self.assertEqual(error.exception.code, 'invalid_context')

    def test_uninterrupted_pending_source_uses_the_current_draw_owner(self):
        from scoring_reference import validate_scalar_context
        for oya in range(4):
            for actor in range(4):
                offset = (actor - oya) % 4
                first_wall = 69 - offset
                for declarer in range(4):
                    for wall in range(71):
                        for first in (False, True):
                            state = deepcopy(self.fixtures['settlement_chombo']['state'])
                            state.update(oya=oya, wall_remaining=wall, first_turn=first,
                                         double_riichi=not first, ippatsu=not first,
                                         reach_accepted=not first, kyotaku=int(not first),
                                         pending_kan=dict(kind='ankan', actor=declarer, pai='E'))
                            current = (oya + 69 - wall) % 4
                            valid = (first_wall <= wall < 70 if first else first_wall - 4 <= wall <= first_wall)
                            valid = valid and declarer == current
                            if not first and declarer == actor:
                                valid = valid and wall == first_wall - 4
                            with self.subTest(oya=oya, actor=actor, declarer=declarer, wall=wall, first=first):
                                if valid:
                                    validate_scalar_context(state, actor, self.rules)
                                else:
                                    with self.assertRaises(ScoringError) as error:
                                        validate_scalar_context(state, actor, self.rules)
                                    self.assertEqual(error.exception.code, 'invalid_context')

    def test_double_riichi_ippatsu_win_and_penalty_sources_follow_next_cycle(self):
        for oya in range(4):
            for actor in range(4):
                first_wall = 69 - (actor - oya) % 4
                for wall in range(first_wall - 5, first_wall + 2):
                    fixture = deepcopy(self.fixtures['yaku_double_riichi'])
                    fixture['input']['actor'] = actor
                    fixture['state'].update(oya=oya, wall_remaining=wall, ippatsu=True, events=[])
                    for target in range(4):
                        fixture['input'].update(target=target, win_method='tsumo' if actor == target else 'ron')
                        valid = (wall == first_wall - 4 if actor == target else
                                 first_wall - 3 <= wall <= first_wall - 1
                                 and target == (oya + 69 - wall) % 4)
                        with self.subTest(oya=oya, actor=actor, target=target, wall=wall):
                            if valid:
                                score_hand(fixture['input'], fixture['state'], self.rules)
                            else:
                                with self.assertRaises(ScoringError) as error:
                                    score_hand(fixture['input'], fixture['state'], self.rules)
                                self.assertEqual(error.exception.code, 'invalid_context')
                    penalty = deepcopy(self.fixtures['settlement_chombo'])
                    penalty['input']['offender'] = actor
                    for state in (penalty['state'], penalty['state']['pre_state']):
                        state.update(oya=oya, wall_remaining=wall, double_riichi=True,
                                     ippatsu=True, reach_accepted=True, kyotaku=1,
                                     scores=[24000, 25000, 25000, 25000])
                    if first_wall - 4 <= wall < first_wall:
                        self.assertEqual(calculate_fixture(penalty, self.rules)['result_type'], 'penalty')
                    else:
                        with self.assertRaises(ScoringError) as error:
                            calculate_fixture(penalty, self.rules)
                        self.assertEqual(error.exception.code, 'invalid_context')

    def test_exhaustive_draw_qualifications_do_not_restrict_penalty_timing(self):
        for offender in range(4):
            own_kan = [int(seat == offender) for seat in range(4)]
            other_kan = [int(seat == (offender + 1) % 4) for seat in range(4)]
            contexts = (
                dict(first_turn=True, wall_remaining=69),
                dict(first_turn=offender != 0, wall_remaining=69 if offender else 68,
                     pending_kan=dict(kind='ankan', actor=0 if offender else 1, pai='E')),
                dict(reach_accepted=True, double_riichi=True, ippatsu=True, kyotaku=1,
                     wall_remaining=68 - offender),
                dict(reach_accepted=True, ippatsu=True, kyotaku=1),
                dict(rinshan=True, kan_counts=own_kan),
                dict(reach_accepted=True, rinshan=True, kan_counts=own_kan, kyotaku=1),
                dict(kan_counts=other_kan),
            )
            for patch in contexts:
                fixture = deepcopy(self.fixtures['settlement_chombo'])
                fixture['input']['offender'] = offender
                for state in (fixture['state'], fixture['state']['pre_state']):
                    state.update(deepcopy(patch))
                    state['scores'][offender] -= 1000 * state['kyotaku']
                with self.subTest(offender=offender, patch=patch):
                    self.assertEqual(calculate_fixture(fixture, self.rules)['result_type'], 'penalty')
                    if patch.get('rinshan'):
                        for state in (fixture['state'], fixture['state']['pre_state']):
                            state['kan_counts'] = other_kan.copy()
                        with self.assertRaisesRegex(ScoringError, 'no own committed kan'):
                            calculate_fixture(fixture, self.rules)
        for flag in ('first_turn', 'ippatsu', 'rinshan'):
            fixture = deepcopy(self.fixtures['noten_1'])
            if flag == 'rinshan':
                hand = fixture['input']['hands'][0]
                hand['concealed_tiles'] = [t for t in hand['concealed_tiles'] if t not in {'2p', '3p', '4p'}]
                hand['melds'] = [dict(kind='ankan', open=False, tiles=['F'] * 4)]
            for state in (fixture['state'], fixture['state']['pre_state']):
                state[flag] = True
                if flag == 'ippatsu':
                    state.update(reach_accepted=True, kyotaku=1, scores=[24000, 25000, 25000, 25000])
                if flag == 'rinshan':
                    state['kan_counts'] = [1, 0, 0, 0]
            with self.subTest(flag=flag):
                with self.assertRaisesRegex(ScoringError, 'expired turn qualification|initial draw cycle'):
                    calculate_fixture(fixture, self.rules)

    def test_penalty_requires_a_decision_but_its_projection_can_start_before_play(self):
        fixture = deepcopy(self.fixtures['settlement_chombo'])
        for state in (fixture['state'], fixture['state']['pre_state']):
            state.update(first_turn=True, wall_remaining=70)
        with self.assertRaisesRegex(ScoringError, 'penalty decision precedes'):
            calculate_fixture(fixture, self.rules)
        fixture['state']['wall_remaining'] = 69
        fixture['state']['events'] = [dict(type='tsumo', actor=0, pai='1m')]
        self.assertEqual(calculate_fixture(fixture, self.rules)['result_type'], 'penalty')
        for offender in range(4):
            for declarer in range(4):
                for rinshan in (False, True):
                    candidate = deepcopy(self.fixtures['settlement_chombo'])
                    candidate['input']['offender'] = offender
                    for state in (candidate['state'], candidate['state']['pre_state']):
                        state.update(pending_kan=dict(kind='ankan', actor=declarer, pai='E'),
                                     rinshan=rinshan,
                                     kan_counts=[int(rinshan and a == offender) for a in range(4)])
                    with self.subTest(offender=offender, declarer=declarer, rinshan=rinshan):
                        if offender == declarer or rinshan:
                            with self.assertRaisesRegex(ScoringError, 'no request in the pending kan'):
                                calculate_fixture(candidate, self.rules)
                        else:
                            self.assertEqual(calculate_fixture(candidate, self.rules)['result_type'], 'penalty')

    def test_draw_scalar_riichi_belongs_only_to_evaluated_seat_zero(self):
        from score_oracle import validate_fixture_input
        from validate_artifacts import SchemaSet
        schemas = SchemaSet()
        original = self.fixtures['noten_1']
        for ready_seat in range(4):
            for accepted in (False, True):
                fixture = deepcopy(original)
                hands = fixture['input']['hands']
                hands[0], hands[ready_seat] = hands[ready_seat], hands[0]
                for state in (fixture['state'], fixture['state']['pre_state']):
                    state['reach_accepted'] = accepted
                    state['kyotaku'] = int(accepted)
                    state['scores'][0] -= self.rules['riichi_stick_value'] * accepted
                validate_fixture_input(fixture, self.rules, schemas)
                with self.subTest(ready_seat=ready_seat, accepted=accepted):
                    if accepted and ready_seat != 0:
                        with self.assertRaisesRegex(ScoringError, 'accepted riichi.*tenpai') as error:
                            calculate_fixture(fixture, self.rules)
                        self.assertEqual(error.exception.code, 'invalid_context')
                    else:
                        actual = calculate_fixture(fixture, self.rules)
                        self.assertEqual(actual['tenpai'], [seat == ready_seat for seat in range(4)])
                        self.assertEqual(actual['kyotaku'], int(accepted))
                        self.assertEqual(sum(actual['scores']) + actual['kyotaku'] * self.rules['riichi_stick_value'],
                                         4 * self.rules['starting_points'])

    def test_draw_evaluated_riichi_requires_a_closed_hand_and_deposit(self):
        from score_oracle import validate_fixture_input
        from validate_artifacts import SchemaSet
        schemas = SchemaSet()
        for kind in (None, 'chi', 'pon', 'daiminkan', 'ankan', 'kakan'):
            for accepted in (False, True):
                for kyotaku in (0, 1):
                    fixture = deepcopy(self.fixtures['noten_1'])
                    quad = kind in {'daiminkan', 'ankan', 'kakan'}
                    if kind is not None:
                        hand = fixture['input']['hands'][0]
                        hand['concealed_tiles'] = [t for t in hand['concealed_tiles'] if t not in {'2p', '3p', '4p'}]
                        meld = dict(kind=kind, open=kind != 'ankan',
                                    tiles=['2p', '3p', '4p'] if kind == 'chi' else ['F'] * (4 if quad else 3))
                        if kind != 'ankan':
                            meld['source'] = 3 if kind == 'chi' else 1
                        hand['melds'] = [meld]
                    for state in (fixture['state'], fixture['state']['pre_state']):
                        state.update(reach_accepted=accepted, kyotaku=kyotaku,
                                     kan_counts=[int(quad), 0, 0, 0],
                                     scores=[25000 - 1000 * kyotaku, 25000, 25000, 25000])
                    validate_fixture_input(fixture, self.rules, schemas)
                    valid = not accepted or (kind in {None, 'ankan'} and kyotaku > 0)
                    with self.subTest(kind=kind, accepted=accepted, kyotaku=kyotaku):
                        if valid:
                            actual = calculate_fixture(fixture, self.rules)
                            self.assertEqual(actual['tenpai'], [True, False, False, False])
                        else:
                            with self.assertRaises(ScoringError) as error:
                                calculate_fixture(fixture, self.rules)
                            self.assertEqual(error.exception.code, 'invalid_context')

    def test_draw_cannot_bypass_enabled_four_kan_abort(self):
        from score_oracle import validate_fixture_input
        from validate_artifacts import SchemaSet
        schemas = SchemaSet()
        hands = [
            {'concealed_tiles': ['1m','2m','3m','4m','5m','6m','7m','8m','9m','5s'],
             'melds': [{'kind': 'ankan', 'open': False, 'tiles': ['E'] * 4}]},
            {'concealed_tiles': ['1p','2p','3p','4p','5p','6p','7p','8p','C','2s'],
             'melds': [{'kind': 'ankan', 'open': False, 'tiles': ['F'] * 4}]},
            {'concealed_tiles': ['1s','2s','3s','4s','5s','6s','7s','8s','P','2m'],
             'melds': [{'kind': 'ankan', 'open': False, 'tiles': ['S'] * 4}]},
            {'concealed_tiles': ['1m','3m','5m','7m','9m','1p','3p','5p','7p','9p'],
             'melds': [{'kind': 'ankan', 'open': False, 'tiles': ['W'] * 4}]},
        ]
        def fixture(draw_hands, counts, enabled=True):
            f = deepcopy(self.fixtures['noten_1'])
            f['input']['hands'] = deepcopy(draw_hands)
            for state in (f['state'], f['state']['pre_state']):
                state['kan_counts'] = counts.copy()
            reasons = [r for r in self.rules['abortive_draws'] if r != 'suukan_sanra']
            f['rule_overrides']['abortive_draws'] = reasons + (['suukan_sanra'] if enabled else [])
            validate_fixture_input(f, self.rules, schemas)
            return f
        with self.assertRaises(ScoringError) as error:
            calculate_fixture(fixture(hands, [1,1,1,1]), self.rules)
        self.assertEqual(error.exception.code, 'invalid_context')
        disabled = calculate_fixture(fixture(hands, [1,1,1,1], False), self.rules)
        self.assertEqual(disabled['tenpai'], [True,False,False,False])
        self.assertEqual(disabled['deltas'], [3000,-1000,-1000,-1000])
        three = deepcopy(hands)
        three[3]['melds'] = [{'kind': 'pon', 'open': True, 'source': 0, 'tiles': ['W'] * 3}]
        result = calculate_fixture(fixture(three, [1,1,1,0]), self.rules)
        self.assertEqual(result['tenpai'], [True,False,False,False])
        self.assertEqual(result['deltas'], [3000,-1000,-1000,-1000])
        one_owner = [
            {'concealed_tiles': ['5s'], 'melds': [
                {'kind': 'ankan', 'open': False, 'tiles': [tile] * 4} for tile in ('E','F','S','W')]},
            {'concealed_tiles': ['1p','2p','3p','4p','5p','6p','7p','8p','9p','2s','3s','4s','5m'], 'melds': []},
            {'concealed_tiles': ['1s','2s','3s','4s','5s','6s','7s','8s','9s','2m','3m','4m','5p'], 'melds': []},
            {'concealed_tiles': ['1m','3m','5m','7m','9m','1p','3p','5p','7p','9p','6s','8s','9s'], 'melds': []},
        ]
        result = calculate_fixture(fixture(one_owner, [4,0,0,0]), self.rules)
        self.assertEqual(result['tenpai'], [True,True,True,False])
        self.assertEqual(result['deltas'], [1000,1000,1000,-3000])

    def test_uncommitted_kan_does_not_cancel_first_turn_or_ippatsu(self):
        for riichi in (False, True):
            with self.subTest(riichi=riichi):
                f = deepcopy(self.fixtures['yakuman_kokushi_ankan_robbery'])
                for state in (f['state'], f['state']['pre_state']):
                    state.update(first_turn=not riichi, reach_accepted=riichi, ippatsu=riichi)
                    if not riichi:
                        # The dealer's first draw may be declared as an ankan
                        # before any discard; later rounds of draws cannot
                        # retain another player's first-turn eligibility.
                        state['wall_remaining'] = 69
                    if riichi:
                        state.update(kyotaku=1, scores=[25000, 24000, 25000, 25000])
                if riichi:
                    f['input']['ura_dora_markers'] = ['2p']
                result = calculate_fixture(f, self.rules)
                self.assertEqual(result['result_type'], 'hora')
                self.assertEqual(result['wins'][0]['yakus'], f['expected']['wins'][0]['yakus'])

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

    def test_projected_kan_commit_preserves_declared_physical_tile(self):
        from scoring_reference import validate_state_projection
        for kind in ('ankan', 'kakan'):
            for declared, committed, valid in (
                ('1m', '1m', True), ('1m', '9m', False),
                ('5m', '5m', True), ('5mr', '5mr', kind == 'kakan'),
                ('5m', '5mr', False), ('5mr', '5m', False),
            ):
                f = deepcopy(self.fixtures['yaku_rinshan'])
                f['state']['events'][0].update(type=kind + '_declared', pai=declared)
                f['state']['events'][1].update(type=kind, pai=committed)
                with self.subTest(kind=kind, declared=declared, committed=committed):
                    if valid:
                        validate_state_projection(f['state'], f['input']['actor'], self.rules)
                    else:
                        with self.assertRaises(ScoringError) as error:
                            validate_state_projection(f['state'], f['input']['actor'], self.rules)
                        self.assertEqual(error.exception.code, 'invalid_context')
        f = deepcopy(self.fixtures['yaku_rinshan'])
        f['state']['events'][0]['pai'] = '9m'
        with self.assertRaises(ScoringError) as error:
            calculate_fixture(f, self.rules)
        self.assertEqual(error.exception.code, 'invalid_context')

    def test_first_turn_ron_follows_uninterrupted_dealer_order(self):
        for oya in range(4):
            for actor in range(4):
                for target in range(4):
                    if actor == target:
                        continue
                    for kind in ('dahai', 'ankan_declared', 'kakan_declared'):
                        for wall in (70, 69, 68, 67, 66, 65, 40, 0):
                            f = deepcopy(self.fixtures['yakuman_kokushi_ankan_robbery'])
                            f['input'].update(actor=actor, target=target)
                            pending = (None if kind == 'dahai' else
                                       dict(kind=kind.removesuffix('_declared'), actor=target, pai='E'))
                            f['state'].update(oya=oya, kyoku=oya + 1, first_turn=True,
                                              wall_remaining=wall, pending_kan=pending,
                                              events=[dict(type=kind, actor=target, pai='E')])
                            f['state']['pre_state'] = {key: deepcopy(value) for key, value in f['state'].items()
                                                      if key not in {'events', 'pre_state', 'furiten'}}
                            f['state']['pre_state']['pending_kan'] = None
                            valid = (kind != 'kakan_declared'
                                     and (target - oya) % 4 < (actor - oya) % 4
                                     and wall == 69 - (target - oya) % 4)
                            for projected in (False, True):
                                candidate = deepcopy(f)
                                if not projected:
                                    candidate['state']['events'] = []
                                    candidate['state']['pre_state']['pending_kan'] = deepcopy(pending)
                                with self.subTest(oya=oya, actor=actor, target=target,
                                                  kind=kind, wall=wall, projected=projected):
                                    if valid:
                                        self.assertEqual(calculate_fixture(candidate, self.rules)['result_type'], 'hora')
                                    else:
                                        with self.assertRaises(ScoringError) as error:
                                            calculate_fixture(candidate, self.rules)
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

    def test_empty_projection_preserves_final_normal_draw_qualification(self):
        def freeze(fixture, wall, last_tile):
            fixture = deepcopy(fixture)
            state = fixture['state']
            state.update(wall_remaining=wall, last_tile=last_tile, events=[])
            state['pre_state'] = {key: deepcopy(value) for key, value in state.items()
                                  if key not in {'events', 'pre_state', 'furiten'}}
            return fixture

        for name, wall, last_tile, points in (
            ('yaku_pinfu', 40, False, 1500),
            ('yaku_haitei', 0, True, 2700),
            ('yaku_rinshan', 0, False, 4000),
        ):
            with self.subTest(fixture=name):
                fixture = freeze(self.fixtures[name], wall, last_tile)
                win = calculate_fixture(fixture, self.rules)['wins'][0]
                self.assertEqual(win['hand_points'], points)
                self.assertEqual('haitei' in {y['id'] for y in win['yakus']}, last_tile)

        fixture = freeze(self.fixtures['yaku_pinfu'], 0, False)
        with self.assertRaisesRegex(ScoringError, 'final normal draw') as error:
            calculate_fixture(fixture, self.rules)
        self.assertEqual(error.exception.code, 'invalid_context')

    def test_empty_projection_preserves_initial_draw_accounting(self):
        def freeze(fixture, **changes):
            fixture = deepcopy(fixture)
            state = fixture['state']
            state.update(changes, events=[])
            state['pre_state'] = {key: deepcopy(value) for key, value in state.items()
                                  if key not in {'events', 'pre_state', 'furiten'}}
            return fixture

        first = freeze(self.fixtures['yakuman_tenhou'])
        self.assertEqual(calculate_fixture(first, self.rules)['wins'][0]['hand_points'], 48000)
        later = freeze(first, wall_remaining=65, first_turn=False)
        self.assertEqual(calculate_fixture(later, self.rules)['wins'][0]['hand_points'], 1500)
        for wall, actor in ((69, 0), (70, 0), (69, 1)):
            with self.subTest(wall=wall, actor=actor):
                bad = freeze(first, wall_remaining=wall, first_turn=False)
                bad['input'].update(actor=actor, target=actor)
                with self.assertRaises(ScoringError) as error:
                    calculate_fixture(bad, self.rules)
                self.assertEqual(error.exception.code, 'invalid_context')

        # The dealer can kan on the first draw and win on rinshan: the
        # kan consumes another live-wall tile and cancels tenhou.
        rinshan = freeze(self.fixtures['yaku_rinshan'], wall_remaining=68,
                         kan_counts=[1, 0, 0, 0])
        rinshan['input'].update(actor=0, target=0)
        win = calculate_fixture(rinshan, self.rules)['wins'][0]
        self.assertEqual({y['id'] for y in win['yakus']}, {'menzen_tsumo', 'rinshan_kaihou'})
        # There cannot already be a new normal draw at the same budget.
        ordinary = freeze(rinshan, rinshan=False)
        with self.assertRaises(ScoringError) as error:
            calculate_fixture(ordinary, self.rules)
        self.assertEqual(error.exception.code, 'invalid_context')

    def test_empty_projection_preserves_known_final_discard_qualification(self):
        for wall, kan_actor, last_tile, valid, points in (
            (40, None, False, True, 1000),
            (0, None, True, True, 2000),
            (0, None, False, False, None),
            (0, 2, True, True, 2000),
            (0, 2, False, False, None),
            (0, 0, False, True, 1000),  # The discarder may have just drawn rinshan.
            (0, 0, True, True, 2000),   # An earlier kan does not disprove houtei.
        ):
            with self.subTest(wall=wall, kan_actor=kan_actor, last_tile=last_tile):
                fixture = deepcopy(self.fixtures['yaku_houtei'])
                state = fixture['state']
                state.update(wall_remaining=wall, last_tile=last_tile, events=[])
                if kan_actor is not None:
                    state['kan_counts'][kan_actor] = 1
                    fixture['input']['dora_markers'].append('4p')
                state['pre_state'] = {key: deepcopy(value) for key, value in state.items()
                                      if key not in {'events', 'pre_state', 'furiten'}}
                if valid:
                    win = calculate_fixture(fixture, self.rules)['wins'][0]
                    self.assertEqual(win['hand_points'], points)
                    self.assertEqual('houtei' in {y['id'] for y in win['yakus']}, last_tile)
                else:
                    with self.assertRaisesRegex(ScoringError, 'final normal discard') as error:
                        calculate_fixture(fixture, self.rules)
                    self.assertEqual(error.exception.code, 'invalid_context')

    def test_houtei_projection_requires_the_final_drawers_first_discard(self):
        original = self.fixtures['yaku_houtei']
        self.assertEqual(calculate_fixture(original, self.rules), original['expected'])
        # The final discard can be taken from the hand rather than drawn.
        tedashi = deepcopy(original)
        tedashi['state']['events'][0]['pai'] = '9p'
        tedashi['state']['events'][-1]['tsumogiri'] = False
        self.assertEqual(calculate_fixture(tedashi, self.rules), original['expected'])

        for case in ('missing_draw', 'wrong_discarder', 'second_discard', 'later_discarder'):
            with self.subTest(case=case):
                fixture = deepcopy(original)
                state = fixture['state']
                if case == 'missing_draw':
                    state['pre_state'].update(wall_remaining=0, last_tile=True)
                    state['events'].pop(0)
                elif case == 'wrong_discarder':
                    fixture['input']['target'] = state['events'][-1]['actor'] = 2
                else:
                    discard = deepcopy(state['events'][-1])
                    if case == 'later_discarder':
                        fixture['input']['target'] = discard['actor'] = 2
                    state['events'].append(discard)
                with self.assertRaisesRegex(ScoringError, 'houtei does not immediately follow') as error:
                    calculate_fixture(fixture, self.rules)
                self.assertEqual(error.exception.code, 'invalid_context')

    def test_fourth_kan_abort_preserves_its_forced_rinshan_win_context(self):
        for method in ('tsumo', 'ron'):
            for enabled in (False, True):
                for flag in (False, True):
                    with self.subTest(method=method, enabled=enabled, flag=flag):
                        f = deepcopy(self.fixtures['yaku_rinshan' if method == 'tsumo' else 'yaku_houtei'])
                        f['input']['dora_markers'] = ['E', 'S', 'W', 'N', 'P']
                        state = f['state']
                        state['kan_counts'] = [2, 1, 1, 0] if method == 'tsumo' else [2, 0, 1, 1]
                        state['rinshan' if method == 'tsumo' else 'last_tile'] = flag
                        state['events'] = []
                        state['pre_state'] = {k: deepcopy(v) for k, v in state.items()
                                              if k not in {'events', 'pre_state', 'furiten'}}
                        if not enabled:
                            f['rule_overrides']['abortive_draws'] = [
                                reason for reason in self.rules['abortive_draws'] if reason != 'suukan_sanra']
                        if enabled and flag != (method == 'tsumo'):
                            with self.assertRaises(ScoringError) as error:
                                calculate_fixture(f, self.rules)
                            self.assertEqual(error.exception.code, 'invalid_context')
                        else:
                            result = calculate_fixture(f, self.rules)['wins'][0]
                            self.assertEqual(result['hand_points'],
                                             (4000 if flag else 2000) if method == 'tsumo'
                                             else (2000 if flag else 1000))

        # Four kans owned by one player do not force the abortive turn.
        f = deepcopy(self.fixtures['yakuman_suukantsu'])
        f['input'].update(win_method='tsumo', target=f['input']['actor'])
        for flag in (False, True):
            for state in (f['state'], f['state']['pre_state']):
                state['rinshan'] = flag
            self.assertEqual(calculate_fixture(f, self.rules)['wins'][0]['hand_points'], 96000)
        f = deepcopy(self.fixtures['yaku_houtei'])
        f['input']['dora_markers'] = ['E', 'S', 'W', 'N', 'P']
        f['state'].update(kan_counts=[4, 0, 0, 0], events=[])
        f['state']['pre_state'] = {k: deepcopy(v) for k, v in f['state'].items()
                                   if k not in {'events', 'pre_state', 'furiten'}}
        self.assertEqual(calculate_fixture(f, self.rules)['wins'][0]['hand_points'], 2000)

    def test_fourth_kan_abort_ron_source_must_own_a_committed_kan(self):
        f = deepcopy(self.fixtures['yaku_houtei'])
        f['input']['dora_markers'] = ['E', 'S', 'W', 'N', 'P']
        state = f['state']
        state.update(wall_remaining=40, kan_counts=[0, 0, 2, 2], last_tile=False, events=[])
        state['pre_state'] = {k: deepcopy(v) for k, v in state.items()
                              if k not in {'events', 'pre_state', 'furiten'}}
        with self.assertRaises(ScoringError) as error:
            calculate_fixture(f, self.rules)
        self.assertEqual(error.exception.code, 'invalid_context')
        for target in (2, 3):
            f['input']['target'] = target
            self.assertEqual(calculate_fixture(f, self.rules)['wins'][0]['hand_points'], 1000)
        f['input']['target'] = 0
        f['rule_overrides']['abortive_draws'] = [
            reason for reason in self.rules['abortive_draws'] if reason != 'suukan_sanra']
        self.assertEqual(calculate_fixture(f, self.rules)['wins'][0]['hand_points'], 1000)

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
