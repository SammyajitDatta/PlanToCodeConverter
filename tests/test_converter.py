"""Run with python -m unittest discover -s tests from the converter folder."""
import json
from pathlib import Path
import unittest

from plan_to_arduino import compile_plan, parse_text, tomllib

ROOT = Path(__file__).resolve().parents[1]
DEVICE = tomllib.loads((ROOT / "device.toml").read_text())


def sketch(body):
    return compile_plan(parse_text("LOOP:\n" + body), DEVICE)


class ConverterTests(unittest.TestCase):
    def test_examples(self):
        for text in sorted((ROOT / "examples").glob("*.txt")):
            with self.subTest(plan=text.stem):
                structured = json.loads(text.with_suffix(".json").read_text())
                self.assertEqual(compile_plan(parse_text(text.read_text()), DEVICE),
                                 compile_plan(structured, DEVICE))

    def test_comparisons(self):
        for operator in [">", ">=", "<", "<=", "==", "!="]:
            with self.subTest(operator=operator):
                result = sketch(f"    IF temperature() {operator} humidity():\n        WAIT 5ms")
                self.assertIn(f"temperature() {operator} humidity()", result)
                self.assertEqual(result.count("dht.begin();"), 1)

    def test_nested_calls(self):
        result = sketch("    CALL setBrightness(scaleKnob(readKnob()))")
        self.assertIn("setBrightness(scaleKnob(readKnob()));", result)
        self.assertNotIn("DHT.h", result)

    def test_scoping_and_types(self):
        cases = ["    CALL setBrightness(temperature())", "    CALL setBrightness(turnLEDOn())",
                 "    IF temperature():\n        WAIT 1ms", "    CALL unknown()",
                 "    IF buttonPressed():\n        LET t = temperature()\n    CALL reportValue(t)",
                 "    LET t = temperature()\n    LET t = 1", "    CALL reportValue(true)",
                 "    IF 1 < 2 < 3:\n        WAIT 5ms", "    CALL reportValue()"]
        for body in cases:
            with self.subTest(body=body), self.assertRaises(ValueError):
                sketch(body)

    def test_invalid_state_updates(self):
        plans = [
            "LOOP:\n    LET flag = true\n    SET flag = false",
            "STATE n: int32 = 0\nLOOP:\n    SET n = true",
            "STATE start: timestamp = NOW_MS()\nLOOP:\n    WAIT 1ms",
            "STATE n: int32 = 0\nLOOP:\n    LET elapsed = ELAPSED_MS(n)",
            "STATE n: int32 = 0\nSTATE n: bool = false\nLOOP:\n    WAIT 1ms",
            "LOOP:\n    LET t = NOW_MS(1)",
        ]
        for plan in plans:
            with self.subTest(plan=plan), self.assertRaises(ValueError):
                compile_plan(parse_text(plan), DEVICE)
