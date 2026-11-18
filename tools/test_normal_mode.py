import itertools
import operator
import unittest
from test_start_speed import SOURCE, parser

FORMS = parser['parse'](SOURCE.replace('{', '(progn ').replace('}', ')'))
FUNCTIONS = {f[1]: f for f in FORMS if isinstance(f, list) and f and f[0] == 'defun'}


class NormalModeTests(unittest.TestCase):
    def setUp(self):
        self.state = {'unlock': False, 'software-adc': True, 'software-adc2': True,
                      'normal-ramp-time': 2.0, 'cruising': False, 'off': False, 'lock': False,
                      'calib-stage': 'idle', 'adc-touch': .02, 'throttle-ramp-level': 0.,
                      'throttle-ramp-time': 0., 'min-speed': 1., 'secret-min-speed': 0.,
                      'cruise-allow': True, 'normal-cruise-allow': True}
        self.conf = {'adc-v1-start': 1., 'adc-v1-end': 3., 'adc-v2-start': 1., 'adc-v2-end': 3.}
        self.now = .1
        self.speed = 2.
        self.pin_brake = 0.

    def call(self, name, *args):
        f = FUNCTIONS[name]
        local = dict(zip(f[2], args))
        result = None
        for expr in f[3:]: result = self.ev(expr, local)
        return result

    def ev(self, expr, local):
        if isinstance(expr, str):
            if expr.startswith("'"): return expr[1:]
            try: return float(expr)
            except ValueError: return local[expr] if expr in local else self.state[expr]
        op, *args = expr
        ev = lambda x: self.ev(x, local)
        if op == 'if': return ev(args[1]) if ev(args[0]) else (ev(args[2]) if len(args) > 2 else None)
        if op == 'and': return all(ev(a) for a in args)
        if op == 'or': return any(ev(a) for a in args)
        if op == 'not': return not ev(args[0])
        if op == 'progn':
            result = None
            for a in args: result = ev(a)
            return result
        if op == 'var': local[args[0]] = ev(args[1]); return local[args[0]]
        if op == 'set': self.state[ev(args[0])] = ev(args[1]); return self.state[ev(args[0])]
        if op in ['min', 'max']: return (min if op == 'min' else max)(map(ev, args))
        binary = {'+': operator.add, '-': operator.sub, '*': operator.mul, '/': operator.truediv,
                  '>': operator.gt, '<': operator.lt, '<=': operator.le, 'eq': operator.eq}
        if op in binary: return binary[op](ev(args[0]), ev(args[1]))
        if op == 'abs': return abs(ev(args[0]))
        if op == 'conf-get': return self.conf[ev(args[0])]
        if op == 'get-speed': return self.speed
        if op == 'get-adc-decoded': return self.pin_brake
        if op == 'systime': return self.now
        if op == 'secs-since': return self.now - ev(args[0])
        return self.call(op, *(ev(a) for a in args))

    def test_two_second_rise_and_immediate_release(self):
        for n in range(1, 21):
            self.now = n / 10
            self.assertAlmostEqual(self.call('normal-throttle', 3., 1.), 1. + n / 10)
        self.now += .1
        self.assertEqual(self.call('normal-throttle', 1., 1.), 1.)
        self.assertEqual(self.state['throttle-ramp-level'], 0.)

    def test_brake_resets_ramp_for_dashboard_and_pin_brakes(self):
        for dashboard in (True, False):
            self.state.update({'software-adc2': dashboard, 'throttle-ramp-level': .8})
            self.pin_brake = .5
            self.assertEqual(self.call('normal-throttle', 3., 2.), 1.)
            self.assertEqual(self.state['throttle-ramp-level'], 0.)

    def test_secret_disabled_and_cruising_bypass_extra_ramp(self):
        for key, value in [('unlock', True), ('normal-ramp-time', 0.), ('cruising', True), ('software-adc', False)]:
            old = self.state[key]; self.state[key] = value
            self.assertEqual(self.call('normal-throttle', 3., 1.), 3.)
            self.state[key] = old

    def test_no_preloading_while_stopped_locked_or_calibrating(self):
        for key, value in [('off', True), ('lock', True), ('calib-stage', 'prep')]:
            old = self.state[key]; self.state[key] = value
            self.state['throttle-ramp-level'] = .8
            self.assertEqual(self.call('normal-throttle', 3., 1.), 1.)
            self.state[key] = old
        self.speed = 0.
        self.assertEqual(self.call('normal-throttle', 3., 1.), 1.)

    def test_link_gap_restarts_ramp_and_invalid_mapping_does_not_divide(self):
        self.state['throttle-ramp-level'] = .8; self.now = 2.
        self.assertAlmostEqual(self.call('normal-throttle', 3., 1.), 1.1)
        self.conf['adc-v1-end'] = 1.
        self.assertEqual(self.call('normal-throttle', 3., 1.), 1.)

    def test_cruise_policy_all_combinations(self):
        for master, secret, normal in itertools.product([False, True], repeat=3):
            self.state.update({'cruise-allow': master, 'unlock': secret, 'normal-cruise-allow': normal})
            self.assertEqual(self.call('cruise-available'), master and (secret or normal))
        for name in ['ctrl-cruise', 'handle-cruise', 'g2-extras', 'apply-mode']:
            self.assertIn('cruise-available', str(FUNCTIONS[name]))


if __name__ == '__main__':
    unittest.main()
