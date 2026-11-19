#!/usr/bin/env python3
"""Exercise settings startup and replies in real LispBM, with mocked hardware storage."""
import argparse
import re
import subprocess
import tempfile
from pathlib import Path


def program(source, saved):
    source = source[:source.rindex('(def img-ok true)')]
    source = source.replace('@const-start', '').replace('@const-end', '')
    source = re.sub(r'^\(import .*$', '', source, flags=re.M)
    source = source.replace('(read-eval-program code-server)', '')
    mocks = f'''(defun systime () 0)
(def storage '{saved})
(defun eeprom-read-f (addr) (assoc storage addr))
(defun eeprom-read-i (addr) (assoc storage addr))
(defun eeprom-store-f (addr val) {{ (set 'storage (setassoc storage addr val)) t }})
(defun eeprom-store-i (addr val) {{ (set 'storage (setassoc storage addr val)) t }})
(def received nil)
(defun send-data (value) (set 'received (cons value received)))
'''
    assertions = '''
(load-settings)
(send-settings)
(def expected (cons 'model (map first setting-groups)))
(def actual (map (lambda (line) (read (first (str-split line " ")))) (reverse received)))
(def loaded-ok (eq actual expected))
(set 'received nil)
(send-defaults)
(def defaults-ok (= (length received) (length setting-groups)))
(save-group 'normal '(2.0 false))
(save-group 'start '(3.0))
(save-group 'sound '(false))
(load-settings)
(check (and loaded-ok defaults-ok (= normal-ramp-time 2.0)
            (not normal-cruise-allow) (= secret-min-speed 3.0)
            (not profile-change-beep)))
'''
    return mocks + source + assertions


def main():
    args = argparse.ArgumentParser()
    args.add_argument('runner', type=Path)
    opts = args.parse_args()
    runner = opts.runner.resolve()
    root = Path(__file__).resolve().parents[1]
    source = (root / 'scooter_support.lisp').read_text()
    # Repository-local temporary files also work in restricted build environments.
    with tempfile.TemporaryDirectory(prefix='startup-', dir=root) as directory:
        for name, saved in [('empty', '()'), ('existing', '((0 . 420i32) (37 . 0) (7 . 5.0) (99 . 0))')]:
            path = Path(directory) / f'{name}.lisp'
            path.write_text(program(source, saved))
            result = subprocess.run([str(runner), '-h', '100000', '-t', '10', str(path)],
                                    text=True, capture_output=True, timeout=20)
            output = result.stdout + result.stderr
            # Upstream's test runner exit code is not a conventional pass/fail code.
            if '\nSUCCESS\n' not in output or 'Error:' in output:
                raise SystemExit(f'LispBM startup failed ({name}):\n{output}')
            print(f'LispBM startup, all settings replies, defaults and save/reload: {name} OK')


if __name__ == '__main__':
    main()
