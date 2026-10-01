"""Backend-independent exact JSON numbers, including hostile compact inputs."""

import os
from pathlib import Path
import subprocess
import sys
import unittest


SCRIPTS = Path(__file__).resolve().parent


def _exercise(case):
    # This function is called only after the subprocess selected its decimal
    # backend and the smallest supported integer-string conversion limit.
    import decimal
    from fractions import Fraction
    import random
    from unittest.mock import patch
    import exact_decimal as exact
    import validate_artifacts as v

    def parse(raw):
        return v.strict_load_bytes(raw.encode('ascii'))

    def rejected(raw, code='invalid_json', **kwargs):
        try:
            v.strict_load_bytes(raw.encode('ascii'), **kwargs)
        except v.ArtifactError as error:
            assert error.code == code, (error.code, code)
        else:
            raise AssertionError('invalid input accepted')

    if case == 'parse':
        # Below, at and beyond the minimum and default Python digit caps.
        for width in (18, 19, 640, 641, 4300, 4301, 5000, 100000):
            exponent = '9' * width
            for sign in ('', '-'):
                for exponent_sign in ('', '+', '-'):
                    assert parse(sign + '0.000e' + exponent_sign + exponent) == 0
                value = parse(sign + '1e-' + exponent)
                assert value.is_finite()
                assert (value < 0) == bool(sign)
                assert (value > -1) if sign else (value < 1)
                rejected(sign + '1e' + exponent)
        for width in (640, 641, 4300, 4301, 5000, 100000):
            raw = '1' * width + '.5'
            positive, negative = parse(raw), parse('-' + raw)
            assert positive.is_finite() and negative.is_finite()
            assert exact.number_parts(positive) == (0, '1' * width + '5', '-1')
            assert positive > 0 and negative < 0
        annotation = ('{"yamai":"1.0-draft.1","kind":"action","session_id":"s",'
                      '"game_id":"g","request_id":"r","action_id":"a","x_test_number":'
                      + '1' * 5000 + '.5}')
        v.SchemaSet().validate(parse(annotation),
                               {'$ref': f'urn:yamai:schema:protocol:{v.PROTOCOL}:action'})
        assert parse('9.007199254740991e15') == v.MAX_INT
        rejected('9.007199254740992e15')
        rejected('1e+' + '9' * 100000)
        for raw in ('NaN', 'Infinity', '-Infinity', '1e', '1e--9'):
            rejected(raw)

    elif case == 'comparison':
        exponent = '9' * 5000
        tiny = parse('1e-' + exponent)
        tiny2 = parse('2e-' + exponent)
        assert 0 < tiny < tiny2 < decimal.Decimal('0.1')
        assert parse('-2e-' + exponent) < parse('-1e-' + exponent) < 0
        assert tiny == parse('10e-' + '1' + '0' * 5000)
        coefficient = '7' * 5000
        a = parse(coefficient + 'e-5001')
        b = parse(coefficient + '0e-5002')
        assert a == b and not a < b and not a > b
        assert decimal.Decimal('0.07') < a < decimal.Decimal('0.08')
        schemas = v.SchemaSet()
        schemas.validate(tiny, {'type': 'number', 'minimum': 0, 'maximum': tiny2,
                                'enum': [parse('10e-' + '1' + '0' * 5000)]})
        schemas.validate(tiny, {'const': parse('10e-' + '1' + '0' * 5000)})
        schemas.validate([tiny, tiny2], {'type': 'array', 'uniqueItems': True})
        for instance, schema in ((tiny, {'minimum': tiny2}),
                                 (tiny2, {'maximum': tiny}),
                                 (tiny, {'const': tiny2}),
                                 (tiny, {'enum': [0, tiny2]}),
                                 ([tiny, parse('10e-' + '1' + '0' * 5000)],
                                  {'type': 'array', 'uniqueItems': True})):
            try:
                schemas.validate(instance, schema)
            except v.ArtifactError as error:
                assert error.code == 'invalid_message'
            else:
                raise AssertionError('numeric schema mismatch accepted')
        assert v._json_equal({'value': tiny}, {'value': tiny})
        assert not v._json_equal(tiny, False)
        assert not v._json_equal(tiny, 0)

    elif case == 'coefficient':
        # Expected residues are a streaming base-10 oracle, independent of
        # the implementation's balanced assembly and Decimal constructors.
        for width in (1, 18, 19, 640, 641, 4301, 5000, 100000):
            digits = ('1234567890' * ((width + 9) // 10))[:width]
            value = exact.coefficient_to_int(digits)
            assert value >= 0
            for modulus in (7, 97, 65537):
                expected = 0
                for digit in digits:
                    expected = (expected * 10 + ord(digit) - 48) % modulus
                assert value % modulus == expected
        assert exact.coefficient_to_int('0' * 5000) == 0

    elif case == 'multiple':
        exponent = '9' * 5000
        cases = [('0.5', '1e-' + exponent, True),
                 ('0.5', '3e-' + exponent, False),
                 ('1e-' + exponent, '0.5', False),
                 ('0', '1e-' + exponent, True),
                 ('1' * 5000 + '.5', '0.5', True),
                 ('1' * 5000 + '.5', '0.2', False)]
        large = '1' + '0' * 4998 + '1'
        triple = '3' + '0' * 4998 + '3'
        neighbor = '3' + '0' * 4998 + '4'
        for e in ('-1', '-' + exponent):
            cases.extend([(triple + 'e' + e, large + 'e' + e, True),
                          (neighbor + 'e' + e, large + 'e' + e, False)])
        schemas = v.SchemaSet()
        for numerator, divisor, expected in cases:
            assert v._is_multiple_of(parse(numerator), parse(divisor)) == expected
            schema = {'type': 'number', 'multipleOf': parse(divisor)}
            if expected:
                schemas.validate(parse(numerator), schema)
            else:
                try:
                    schemas.validate(parse(numerator), schema)
                except v.ArtifactError as error:
                    assert error.code == 'invalid_message'
                else:
                    raise AssertionError('nonmultiple accepted')
            assert v._is_multiple_of(parse('-' + numerator), parse(divisor)) == expected
        # Build long coefficients independently via small base-10 blocks,
        # never relaxing the interpreter's integer-string safety cap.
        def digits(number):
            blocks = []
            while number >= 10**18:
                number, block = divmod(number, 10**18)
                blocks.append(f'{block:018d}')
            return str(number) + ''.join(reversed(blocks))
        for prime in (2, 5):
            for power in (1, 2, 3, 7, 8, 9, 31, 32, 33, 10000):
                coefficient = digits(prime**power)
                assert v._is_multiple_of(1, parse(coefficient + 'e-' + str(power)))
                assert not v._is_multiple_of(1, parse(coefficient + 'e-' + str(power - 1)))
                assert v._is_multiple_of(3, parse(digits(3 * prime**power) + 'e-' + str(power)))
                assert not v._is_multiple_of(1, parse(digits(3 * prime**power) + 'e-' + str(power)))
        for a, b, residual in ((0, 0, 1), (1, 1, 1), (7, 8, 3), (32, 31, 7),
                               (10000, 17000, 1), (17000, 10000, 11)):
            assert exact.denominator_factors(residual * 2**a * 5**b) == (residual, a, b)
        for invalid in (0, -1, True, decimal.Decimal('NaN'), decimal.Decimal('Infinity')):
            try:
                v._is_multiple_of(1, invalid)
            except v.ArtifactError as error:
                assert error.code == 'schema_error'
            else:
                raise AssertionError('invalid multipleOf accepted')

    elif case == 'oracle':
        rng = random.Random(917347)
        # The independent oracle forms a rational directly from integers;
        # it does not parse decimal tokens or use Decimal arithmetic.
        with decimal.localcontext() as context:
            context.prec = 2
            for _ in range(3000):
                a, b = rng.randrange(-10**12, 10**12), rng.randrange(1, 10**12)
                ae, be = rng.randrange(-100, 0), rng.randrange(-100, 0)
                af, bf = Fraction(a, 10**-ae), Fraction(b, 10**-be)
                left, right = parse(str(a) + 'e' + str(ae)), parse(str(b) + 'e' + str(be))
                assert exact.compare_numbers(left, right) == ((af > bf) - (af < bf))
                assert v._is_multiple_of(left, right) == ((af / bf).denominator == 1)

    elif case == 'bounded':
        # The child has a CPU/address-space guard in addition to the parent
        # timeout. Exponent magnitude must not allocate an expanded power.
        limit = 1048576
        raw = '1e-' + '9' * (limit - 3)
        value = v.strict_load_bytes(raw.encode('ascii'), max_bytes=limit)
        assert isinstance(value, exact.ExactDecimal) and 0 < value < 1
        assert v._is_multiple_of(value, value)
        assert v._is_multiple_of(parse('0.5'), value)
        coefficient = '1' * (limit - 2) + '.5'
        value = v.strict_load_bytes(coefficient.encode('ascii'), max_bytes=limit)
        assert value > 0 and v._is_multiple_of(value, parse('0.5'))
        assert v._is_multiple_of(value, value)
        # A huge odd denominator was a CPU-exhaustion path in modular pow.
        # Also exercise large even and 5-divisible non-power denominators.
        exponent = '-999999999999999999999999999'
        width = limit - len(exponent) - 1
        for ending in ('1', '2', '5'):
            divisor = parse('1' + '2' * (width - 2) + ending + 'e' + exponent)
            assert not v._is_multiple_of(parse('0.5'), divisor)
        rejected(raw + '0', code='resource_limit', max_bytes=limit)
        # Framing must reject over-limit bytes before numeric parsing.
        with patch.object(v, '_parse_real', side_effect=AssertionError('parsed oversized input')):
            rejected(raw + '0', code='resource_limit', max_bytes=limit)
        assert sys.get_int_max_str_digits() == 640
    else:
        raise AssertionError(case)


class ExactDecimalBackends(unittest.TestCase):
    def run_case(self, case):
        for backend in ('python', 'c'):
            with self.subTest(backend=backend):
                program = '''
import sys
if sys.argv[1] == 'python':
    sys.modules['_decimal'] = None
else:
    try:
        import _decimal
    except ImportError:
        print('native Decimal backend unavailable')
        sys.exit(77)
sys.set_int_max_str_digits(640)
try:
    import resource
except ImportError:
    resource = None
if resource is not None:
    for name, limit in (('RLIMIT_CPU', 20), ('RLIMIT_AS', 512 * 1024 * 1024)):
        if hasattr(resource, name):
            kind = getattr(resource, name)
            hard = resource.getrlimit(kind)[1]
            if hard != resource.RLIM_INFINITY:
                limit = min(limit, hard)
            try:
                resource.setrlimit(kind, (limit, limit))
            except (OSError, ValueError):
                pass  # The parent wall-clock timeout still bounds the case.
import decimal
assert decimal.Decimal.__module__ == 'decimal'
if sys.argv[1] == 'python':
    assert decimal.Decimal.__new__.__module__ == 'decimal'
else:
    assert type(decimal.Decimal.__new__).__name__ == 'builtin_function_or_method'
import test_exact_decimal
assert sys.get_int_max_str_digits() == 640
test_exact_decimal._exercise(sys.argv[2])
assert sys.get_int_max_str_digits() == 640
print(sys.argv[1], sys.argv[2], 'passed')
'''
                result = subprocess.run([sys.executable, '-c', program, backend, case],
                                        cwd=SCRIPTS, capture_output=True, text=True, timeout=30,
                                        env={**os.environ, 'PYTHONDONTWRITEBYTECODE': '1'})
                if backend == 'c' and result.returncode == 77:
                    self.skipTest('optional native Decimal backend unavailable')
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(f'{backend} {case} passed', result.stdout)

    def test_parse_both_backends(self):
        self.run_case('parse')

    def test_comparison_both_backends(self):
        self.run_case('comparison')

    def test_coefficient_conversion_both_backends(self):
        self.run_case('coefficient')

    def test_multiple_of_both_backends(self):
        self.run_case('multiple')

    def test_fraction_oracle_both_backends(self):
        self.run_case('oracle')

    def test_bounded_hostile_inputs_both_backends(self):
        self.run_case('bounded')


if __name__ == '__main__':
    unittest.main()
