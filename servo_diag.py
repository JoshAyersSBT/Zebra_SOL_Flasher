# servo_diag.py (standalone)
from machine import Pin, PWM, I2C
import time

# GPIO candidates to try for direct servo signal
# (avoids flash pins 6-11; avoids input-only 34-39 for output)
CAND = [4,5,12,13,14,15,16,17,18,19,21,22,23,25,26,27,32,33]

FREQ = 50
MIN_US = 500
MAX_US = 2500

def set_angle(pwm, a):
    a = max(0, min(180, int(a)))
    pulse = MIN_US + (MAX_US - MIN_US) * a // 180
    period = 20000  # us at 50 Hz
    duty_u16 = (pulse * 65535) // period
    if hasattr(pwm, "duty_u16"):
        pwm.duty_u16(int(duty_u16))
    else:
        pwm.duty(int(duty_u16 * 1023 // 65535))

def try_servo_gpio(gpio):
    print("Trying servo pulses on GPIO", gpio)
    try:
        pwm = PWM(Pin(gpio, Pin.OUT), freq=FREQ)
        set_angle(pwm, 60);  time.sleep(0.3)
        set_angle(pwm, 120); time.sleep(0.3)
        set_angle(pwm, 90);  time.sleep(0.3)
        pwm.deinit()
        return True
    except Exception as e:
        print("  PWM failed on GPIO", gpio, "->", e)
        try:
            pwm.deinit()
        except Exception:
            pass
        return False

def i2c_scan():
    # Try common ESP32 I2C pin pairs
    pairs = [(21,22), (22,21), (18,19), (19,18), (16,17), (17,16), (25,26), (26,25)]
    found_any = False
    for sda, scl in pairs:
        try:
            i2c = I2C(0, scl=Pin(scl), sda=Pin(sda), freq=400000)
            addrs = i2c.scan()
            if addrs:
                found_any = True
                print("I2C devices on SDA=%d SCL=%d:" % (sda, scl), [hex(a) for a in addrs])
        except Exception:
            pass
    if not found_any:
        print("No I2C devices found on common pin pairs (this does not prove no I2C).")

print("\n=== Servo diagnostics ===")
print("1) Scanning I2C (looking for PWM expanders like PCA9685: often 0x40..0x7F)")
i2c_scan()

print("\n2) Sweeping GPIOs with 50Hz servo pulses")
print("Plug ONE servo in and watch for movement.")
while True:
    for g in CAND:
        try_servo_gpio(g)
        time.sleep(0.25)