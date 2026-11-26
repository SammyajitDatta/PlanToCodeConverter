# Arduino plan converter for ESP32

Extract the ZIP and open PowerShell in the folder containing plan_to_arduino.py.
On Python 3.10 install tomli once. Python 3.11+ includes the TOML parser.

    py -3 -m pip install tomli
    py -3 plan_to_arduino.py examples/motion_hold.txt
    py -3 plan_to_arduino.py examples/motion_hold.json
Every successful conversion replaces executablescript/executablescript.ino beside the
converter. The bundled sketch starts with examples/motion_hold.txt. All ten plan pairs
are in examples/. The converter uses device.toml by default.

Optional developer check: run the following after changing the converter or catalog
to verify example equivalence, expression handling, and validation of invalid plans.

    py -3 -m unittest discover -s tests -v

Open the generated sketch in Arduino IDE with the matching ESP32 board selected.
For DHT examples install Adafruit's DHT sensor library and its Adafruit Unified Sensor dependency.
The dimmer example can run with just a potentiometer and an LED. Change the GPIOs
in device.toml to match your board before uploading.

## Example catalog

| Component | Default GPIO | Functions |
|---|---|---|
| DHT11 data | 26 | temperature() in Celsius, humidity() in percent |
| Potentiometer wiper | 34 | readKnob() raw ADC value, using the default 12-bit ADC range |
| Push button to ground | 32 | buttonPressed(), active low with internal pull-up |
| LED | 25 | turnLEDOn(), turnLEDOff(), setBrightness(int32), range 0–255 |
| PIR output | 27 | detectMotion() |
| Active-high buzzer | 33 | turnBuzzerOn(), turnBuzzerOff() |
| Serial monitor | USB serial at 115200 | reportValue(float) |

The catalog also provides scaleKnob(int32) for converting ADC readings into brightness
and validReading(float) for detecting invalid DHT readings. The compiler collects
pins, modules and function definitions from all calls, including calls nested in expressions.

## Plan format for the LLM

Use one top-level loop with an ordered body. Statements are call, wait, let, set or if.
Pseudocode uses indentation. JSON uses arrays for body, then, else and args.
Use the exact case of the function names in device.toml.

| Pseudocode | Corresponding JSON |
|---|---|
| temperature() | `{"function":"temperature","args":[]}` |
| LET t = temperature() | `{"type":"let","name":"t","value":{"function":"temperature","args":[]}}` |
| t > 30 | `{"op":">","args":[{"var":"t"},30]}` |
| CALL reportValue(t) | `{"type":"call","function":"reportValue","args":[{"var":"t"}]}` |
| CALL setBrightness(scaleKnob(readKnob())) | `{"type":"call","function":"setBrightness","args":[{"function":"scaleKnob","args":[{"function":"readKnob","args":[]}]}]}` |

Conditions support >, >=, <, <=, ==, !=, AND, OR and NOT. JSON uses lower-case
and/or/not operator names. Use parentheses to group pseudocode conditions. Write
a range as t > 20 AND t < 30. Logical operators short-circuit. Arithmetic operators
+, -, * and / yield floating-point values, including integer division expressions. In SET assignments to int32 state,
integer addition and subtraction preserve integer type and saturate at the int32 limits.
Check a dynamic divisor before dividing. Parameters support bool, int32 and float.
An int32 can be passed to a float parameter. Use a typed helper for explicit scaling
or conversion when an actuator expects an integer.

LET captures a reading once per execution of its block. Its value is available to
later statements in that block and its nested branches. Use separate LET statements
when sensor read order matters. LET variables start fresh each loop iteration.
WAIT takes a nonnegative integer literal in milliseconds and blocks the loop.
There is one repeating loop. Additional loop statements,
strings and arbitrary C++ expressions are outside this version's plan format.

## Persistent state and timers

Declare STATE before LOOP with a name, type and literal initial value. JSON plans
place declarations in an optional top-level state array. For example,
`STATE start: timestamp = 0` becomes `{"name":"start","type":"timestamp","value":0}`
inside that array. Supported state types are bool, int32, float, timestamp and duration.
State survives loop iterations and initializes again when the sketch restarts.

Inside any block, `SET start = NOW_MS()` becomes
`{"type":"set","name":"start","value":{"function":"NOW_MS","args":[]}}`.
SET updates a declared state variable. LET remains local and read-only.
NOW_MS() samples the 32-bit millisecond clock. ELAPSED_MS(start) returns a duration
using unsigned subtraction. Compare it with an integer threshold or another duration.
Check the timer within one full clock cycle (about 49.7 days).
Both built-ins use the same function/args JSON shape as device calls.

## New plans to try

| File stem in examples/ | Expected behavior |
|---|---|
| motion_hold | Motion activates the LED and buzzer. One continuous second of LOW readings switches them off. Returning motion cancels the countdown. |
| consecutive_heat | Three consecutive valid readings above 30 °C activate the buzzer. A lower or failed reading clears the counter and buzzer. |
| heat_alarm | 30 °C keeps the buzzer off. Above 30 °C turns it on. A failed read silences it. |
| knob_dimmer | Knob readings 0, 2048 and 4095 yield brightness 0, 127 and 255. |
| climate_monitor | At 30 °C and 70% humidity, the LED is off unless the button is held. At 71% humidity it lights. Reports Fahrenheit for valid readings. |
| adjustable_alarm | The knob sweeps the threshold from 20 to 40 °C. Above the threshold the buzzer sounds unless the button is held. |

Each example has matching .txt and .json files.
