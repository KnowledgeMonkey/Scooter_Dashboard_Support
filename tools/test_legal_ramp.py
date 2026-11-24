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
    mocks += """(def raw-brake-supported true)
(defun conf-get (key)
    (if (and (eq key 'adc-v2-start) (not raw-brake-supported))
        (exit-error 'unknown-parameter)
        (assoc '((adc-ctrl-type . 8) (adc-v1-min . 0.3) (adc-v1-max . 3.1)
                 (adc-v1-start . 1.0) (adc-v1-end . 3.0) (adc-v2-start . 1.0)) key)))
"""
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
(defun get-adc-decoded (ch) (if (= ch 0) rest-decoded (if (> adc2 1.0) 1.0 0.0)))
(defun app-adc-override (ch value)
    (cond ((= ch 0) (set 'adc1 value)) ((= ch 1) (set 'adc2 value))
          ((= ch 3) (set 'button value))))
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
; Read the same inputs the button loop sees. A moving wheel and a legal
; throttle ramp must not suppress physical taps, and decoded ADC stays at rest.
(defun get-speed () (/ cur-speed-kmh 3.6))
(set 'cur-speed-kmh 20.0)
(set 'button-safety-speed 0.0)
(set 'legal-ramp-seconds 2.0)
(def taps-default-off (= profile-taps 0))
(save-group 'taps '(1290i32)) ; throttle, brake, button, throttle
(load-settings)
(def taps-saved (and (= profile-taps 1290) brake-start-known))
; Early 7.00 still provides the decoded ADC2 value. Exercise this version
; for the rest of the moving tap tests, including frames with a rising throttle.
(set 'raw-brake-supported false)
(load-settings)
(def early-700-ok (not brake-start-known))
(defun sample (keys dt) {
    (frame (if (!= 0 (bitwise-and keys 2)) 165 60)
           (if (!= 0 (bitwise-and keys 1)) 165 60) dt)
    (setq lev-brk (get-adc-decoded 1))
    (read-profile-taps (!= 0 (bitwise-and keys 4)))
})
(defun tap (key) { (sample key 0.12) (sample 0 0.12) })
(set 'unlock false)
(set 'secret-enabled true)
(sample 0 0.12)
(tap 2) (tap 1) (set 'presses 1) (tap 4)
(sample 2 0.12)
(def before-release (and (not unlock) (= presses 0) (not (gestures-allowed))))
(sample 0 0.12)
(def moving-ok (and unlock before-release))
(sample 0 0.12)
(def fires-once unlock)
(tap 2) (tap 4) (tap 1) (tap 4) (tap 2)
(def wrong-order-ok unlock)
(sample 0 1.2)
(tap 2) (sample 3 0.10) (sample 1 0.12) (sample 0 0.10) (tap 1) (tap 4) (tap 2)
(def overlap-ok unlock)
(sample 0 1.2)
(tap 2) (sample 0 1.2) (tap 1) (tap 4) (tap 2)
(def gap-cancels unlock)
(sample 0 1.2)
(sample 2 0.12) (sample 2 0.6) (sample 0 0.10) (tap 1) (tap 4) (tap 2)
(def long-cancels unlock)
(sample 0 1.2)
(tap 2) (set 'clock (+ clock 0.75)) (read-profile-taps false)
(sample 0 0.12) (tap 1) (tap 4) (tap 2)
(def stale-cancels unlock)
(sample 0 1.2)
(tap 2) (tap 1) (tap 4) (tap 2)
(def return-ok (not unlock))
(set 'secret-enabled false)
(tap 2) (tap 1) (tap 4) (tap 2)
(def disabled-ok (not unlock))
(set 'secret-enabled true)
(set 'secret-requires-lock true)
(tap 2) (tap 1) (tap 4) (tap 2)
(def lock-pref-ok (not unlock))
(set 'secret-requires-lock false)
(save-group 'taps '(9)) ; two separate brake taps
(load-settings)
(set 'software-adc false) (set 'software-adc2 false)
(read-profile-taps false)
(set 'lev-brk 1.0) (read-profile-taps false) (read-profile-taps false)
(set 'lev-brk 0.0) (read-profile-taps false)
(def repeat-once (not unlock))
(set 'lev-brk 1.0) (read-profile-taps false)
(set 'lev-brk 0.0) (read-profile-taps false)
(def repeat-ok (and repeat-once unlock))
; With the original riding shortcut configured, a simultaneous half-second
; hold still returns to legal through the same gated legacy handlers.
(set 'button-safety-speed 20.0)
(set 'secret-off-presses 0) (set 'secret-off-combo 0)
(set 'lev-thr 1.0) (set 'lev-brk 1.0)
(read-profile-taps false) (if (gestures-allowed) (handle-lever-gestures))
(set 'clock (+ clock 0.6))
(read-profile-taps false) (if (gestures-allowed) (handle-lever-gestures))
(def hold-ok (and (not unlock) (not lever-armed)))
(set 'lev-thr 0.0) (set 'lev-brk 0.0)
(read-profile-taps false) (handle-lever-gestures)
; A separate power-button tap still reaches the ordinary headlight shortcut.
(set 'light false) (set 'presses 1)
(read-profile-taps true) (read-profile-taps false)
(handle-button)
(def button-ok (and light lever-armed))
; Use native 0.1 ms tick units for the ordinary button dispatch window.
(defun systime () (to-i (* clock 10000.0)))
(defun secs-since (stamp) (- clock (/ stamp 10000.0)))
(save-group 'taps '(20)) ; button, throttle
(load-settings)
(set 'software-adc false) (set 'software-adc2 false)
(set 'button-safety-speed 20.0)
(set 'light false)
(defun controls (keys dt) {
    (set 'clock (+ clock dt))
    (var down (!= 0 (bitwise-and keys 4)))
    (if (and down (not last-button-state)) {
        (setq presses (+ presses 1)) (setq press-time (systime)) })
    (setq last-button-state down)
    (setq lev-thr (if (!= 0 (bitwise-and keys 2)) 1.0 0.0))
    (setq lev-brk (if (!= 0 (bitwise-and keys 1)) 1.0 0.0))
    (if (> profile-taps 0) (read-profile-taps down))
    (button-apply down)
})
(set 'presses 0)
(controls 0 0.06) (controls 4 0.06) (controls 0 0.10) (controls 0 0.25)
(def solo-button-ok (and light (not unlock) (= presses 0)))
(controls 4 0.06) (controls 0 0.08) (controls 2 0.08) (controls 0 0.22)
(def button-first-ok (and light unlock (= presses 0)))
(save-group 'taps '(36)) ; two separate button taps
(load-settings)
(set 'software-adc false) (set 'software-adc2 false)
(controls 0 0.06) (controls 4 0.06) (controls 0 0.08) (controls 4 0.08) (controls 0 0.08)
(controls 0 0.40)
(def two-buttons-ok (and light (not unlock) (= presses 0)))
(save-group 'taps '(26)) ; second digit is a simultaneous press
(load-settings)
(def bad-code-ok (= profile-taps 0))
(save-group 'taps '(0)) (load-settings)
(def taps-off-again (= profile-taps 0))
(check (and independent-ok secret-at-zero boundary-ok persisted-ok rising-ok full-ok release-ok brake-ok kick-ok range-ok
            secret-ok back-ok gap-ok bypass-ok taps-default-off taps-saved early-700-ok moving-ok fires-once
            wrong-order-ok overlap-ok gap-cancels long-cancels stale-cancels return-ok disabled-ok
            lock-pref-ok repeat-ok hold-ok button-ok solo-button-ok button-first-ok two-buttons-ok bad-code-ok taps-off-again))
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
                                    text=True, errors="replace", capture_output=True, timeout=20)
            output = result.stdout + result.stderr
            # Upstream's test runner exit code is not a conventional pass/fail code.
            if '\nSUCCESS\n' not in output or 'Error:' in output:
                raise SystemExit(f'LispBM startup failed ({name}):\n{output}')
            print(f'LispBM startup, settings, legal ramp, braking, profile switches and ordered taps: {name} OK')


if __name__ == '__main__':
    main()
