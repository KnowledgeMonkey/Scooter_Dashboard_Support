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
    mocks = f'''(def clock 0.0)
(defun systime () clock)
(defun secs-since (stamp) (- clock stamp))
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
(def previous-speed (setting-def 'min-speed-kmh))
(def default-ok (and (= legal-ramp-seconds 1.0) (= min-speed previous-speed)
                     (= secret-start-speed previous-speed)))
(write-setting 'min-speed-kmh 6.0)
(load-settings)
(def independent-ok (and (= min-speed 6.0) (= secret-start-speed previous-speed)))
(save-group 'start '(0.0))
(load-settings)
(set 'unlock true)
(def secret-at-zero (start-speed-reached 0.0))
(set 'unlock false)
(def boundary-ok (and (not (start-speed-reached 5.9)) (start-speed-reached 6.0)))
(save-group 'start '(3.0))
(set 'received nil)
(send-settings)
(def expected (cons 'model (map first setting-groups)))
(def actual (map (lambda (line) (read (first (str-split line " ")))) (reverse received)))
(def loaded-ok (eq actual expected))
(set 'received nil)
(send-defaults)
(def defaults-ok (= (length received) (length setting-groups)))
(save-group 'ramp '(2.0))
(save-group 'sound '(false))
(load-settings)
(def persisted-ok (and loaded-ok defaults-ok default-ok (= legal-ramp-seconds 2.0)
                       (not profile-change-beep)))

(def uart-buf (array-create 64))
(def adc1 -1.0)
(def adc2 -1.0)
(def button 0)
(def rest-decoded 0.0)
(defun get-adc-decoded (ch) (if (= ch 0) rest-decoded 0.0))
(defun app-adc-override (ch value)
    (cond ((= ch 0) (set 'adc1 value)) ((= ch 1) (set 'adc2 value))
          ((= ch 3) (set 'button value))))
(defun conf-get (key) (assoc '((adc-ctrl-type . 8) (adc-v1-min . 0.3)
    (adc-v1-max . 3.1) (adc-v1-start . 1.0) (adc-v1-end . 3.0)
    (adc-v2-start . 1.0)) key))
(defun configure-speed (a b c d e) t)
(defun frame (thr brk dt) {
    (bufset-u8 uart-buf thr-idx thr)
    (bufset-u8 uart-buf brk-idx brk)
    (set 'clock (+ clock dt))
    (adc-input 0)
})
(defun near (a b) (< (abs (- a b)) 0.001))
(set 'off false)
(set 'lock false)
(set 'unlock false)
(set 'light false)
(set 'cur-speed-kmh 15.0)
(set 'cruise-allow true)
(set 'cruise-enabled true)
; Legal: settle at rest, then take two seconds from idle to full request.
(frame 60 60 0.1)
(frame 60 60 0.3)
(frame 231 60 0.1)
(def rising-ok (and ramp-ready (near adc1 1.1)))
(looprange i 0 19 (frame 231 60 0.1))
(def full-ok (near adc1 (/ 231 77.2)))
(frame 60 60 0.1)
(def release-ok (near adc1 (/ 60 77.2)))
(frame 231 155 0.1)
(def brake-ok (and (near adc2 (/ 155 77.2)) (near adc1 1.0)))
(set 'cur-speed-kmh 0.0)
(frame 220 60 0.1)
(def kick-ok (near adc1 1.0))
(set 'cur-speed-kmh 15.0)
(frame 255 60 0.1)
(def range-ok (near adc1 3.3))
(frame 60 60 0.1)
(frame 60 60 0.3)
(frame 220 60 0.1)
; Mode switching while the lever stays held.
(toggle-secret)
(frame 220 60 0.1)
(def secret-ok (and (not (start-speed-reached 2.9)) (start-speed-reached 3.0) (near adc1 (/ 220 77.2)) (= feedback 0)))
(toggle-secret)
(frame 220 60 0.05)
(def back-ok (and (not (start-speed-reached 3.0)) (> adc1 1.0) (<= adc1 1.101)))
(frame 220 60 2.0)
(def gap-ok (near adc1 1.1))
(set 'legal-ramp-seconds 0.0)
(frame 220 60 0.1)
(def bypass-ok (near adc1 (/ 220 77.2)))
(check (and independent-ok secret-at-zero boundary-ok persisted-ok rising-ok full-ok release-ok brake-ok kick-ok range-ok
            secret-ok back-ok gap-ok bypass-ok))
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
            print(f'LispBM startup, settings, legal ramp, braking and profile switches: {name} OK')


if __name__ == '__main__':
    main()
