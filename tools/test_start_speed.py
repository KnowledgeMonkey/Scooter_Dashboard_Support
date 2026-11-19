import ast
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Reuse the repository's Lisp parser without running its CLI entry point.
module = ast.parse((ROOT / 'tools/check_lisp.py').read_text())
module.body = [n for n in module.body if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name) and n.value.func.id == 'main')]
parser = {}
exec(compile(module, 'check_lisp.py', 'exec'), parser)
SOURCE = (ROOT / 'scooter_support.lisp').read_text()
FORMS = parser['parse'](SOURCE)
FUNCTIONS = {f[1]: f for f in FORMS if isinstance(f, list) and f and f[0] == 'defun'}


class StartSpeedTests(unittest.TestCase):
    def evaluate(self, expr, state, saved):
        if isinstance(expr, str):
            if expr.startswith("'"): return expr[1:]
            try: return float(expr)
            except ValueError: return state[expr]
        op, *args = expr
        ev = lambda x: self.evaluate(x, state, saved)
        if op == 'if': return ev(args[1]) if ev(args[0]) else (ev(args[2]) if len(args) > 2 else None)
        if op == 'eq': return ev(args[0]) == ev(args[1])
        if op in ('min2', 'max2'):
            fn = FUNCTIONS[op]
            bound = dict(state, **dict(zip(fn[2], map(ev, args))))
            return self.evaluate(fn[3], bound, saved)
        if op == '<': return ev(args[0]) < ev(args[1])
        if op == '>': return ev(args[0]) > ev(args[1])
        if op == 'read-setting': return saved.get(ev(args[0]), 'unset')
        if op == 'setting-def': return saved.get(ev(args[0]), 1.0)
        if op == 'eeprom-store-f':
            self.assertEqual(ev(args[0]), 111)
            saved['secret-min-speed-kmh'] = ev(args[1])
            return None
        if op == 'set':
            state[ev(args[0])] = ev(args[1])
            return None
        raise AssertionError(op)

    def load_start_speeds(self, saved):
        state = {}
        for form in FUNCTIONS['load-settings'][3:]:
            if not isinstance(form, list): continue
            if form[0] == 'if' and 'eeprom-store-f' in str(form):
                self.evaluate(form, state, saved)
            if form[0] == 'set' and form[1] in ["'min-speed", "'secret-min-speed"]:
                self.evaluate(form, state, saved)
        return state

    def test_upgrade_preserves_shared_value_even_at_zero_or_default(self):
        for value in (0, 1, 5):
            saved = {'min-speed-kmh': value}
            state = self.load_start_speeds(saved)
            self.assertEqual(state, {'min-speed': value, 'secret-min-speed': value})
            self.assertEqual(saved['secret-min-speed-kmh'], value)
            saved['min-speed-kmh'] = 7
            self.assertEqual(self.load_start_speeds(saved)['secret-min-speed'], value)

    def test_switching_uses_separate_value_and_preserves_saved_secret(self):
        state = self.load_start_speeds({'min-speed-kmh': 5, 'secret-min-speed-kmh': 0})
        expr = FUNCTIONS['active-start-speed'][3]
        for secret, expected in [(False, 5), (True, 0), (False, 5)]:
            state['unlock'] = secret
            self.assertEqual(self.evaluate(expr, state, {}), expected)

    def test_negative_values_clamped_and_both_consumers_use_selector(self):
        state = self.load_start_speeds({'min-speed-kmh': -5, 'secret-min-speed-kmh': -1})
        self.assertEqual(state, {'min-speed': 0, 'secret-min-speed': 0})
        self.assertIn('(>= speed-kmh (active-start-speed))', SOURCE)
        self.assertIn('(< current-speed (active-start-speed))', SOURCE)
        slots = re.findall(r'^    \([^\s()]+ (\d+) [bif] ', SOURCE, re.M)
        self.assertEqual(len(slots), len(set(slots)))


if __name__ == '__main__':
    unittest.main()
