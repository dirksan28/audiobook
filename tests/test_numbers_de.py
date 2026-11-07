import json
import unittest
from types import SimpleNamespace

import tts_audiobook as tts


class GermanNumberTests(unittest.TestCase):
    def test_cardinals(self):
        expected = {
            0: "null",
            1: "eins",
            21: "einundzwanzig",
            100: "einhundert",
            101: "einhunderteins",
            1000: "eintausend",
            3100: "dreitausendeinhundert",
            150000: "einhundertfünfzigtausend",
            1000000: "eine Million",
            1427097: "eine Million vierhundertsiebenundzwanzigtausendsiebenundneunzig",
        }
        for number, words in expected.items():
            with self.subTest(number=number):
                self.assertEqual(tts._cardinal_de(number), words)

    def test_years(self):
        expected = {
            1811: "achtzehnhundertelf",
            1887: "achtzehnhundertsiebenundachtzig",
            1908: "neunzehnhundertacht",
            1930: "neunzehnhundertdreißig",
            2000: "zweitausend",
        }
        for year, words in expected.items():
            with self.subTest(year=year):
                self.assertEqual(tts._year_de(year), words)

    def test_ordinals(self):
        self.assertEqual(tts._ordinal_de(1, "n"), "ersten")
        self.assertEqual(tts._ordinal_de(3, "e"), "dritte")
        self.assertEqual(tts._ordinal_de(19, "s"), "neunzehntes")
        self.assertEqual(tts._ordinal_de(27, "n"), "siebenundzwanzigsten")
        self.assertEqual(tts._ordinal_de(101, "e"), "einhunderterste")

    def test_designations(self):
        self.assertEqual(tts._spell_numbers_de("C96"), "C sechsundneunzig")
        self.assertEqual(tts._spell_numbers_de("C93"), "C dreiundneunzig")
        self.assertEqual(tts._spell_numbers_de("M712"), "M siebenhundertzwölf")
        self.assertEqual(tts._spell_numbers_de("Pistole 08"), "Pistole null acht")
        self.assertEqual(tts._spell_numbers_de("08/15"), "null acht fünfzehn")

    def test_dates_and_centuries(self):
        self.assertEqual(
            tts._spell_numbers_de("am 27. Juni 1838"),
            "am siebenundzwanzigsten Juni achtzehnhundertachtunddreißig",
        )
        self.assertEqual(
            tts._spell_numbers_de("im 19. Jahrhundert"),
            "im neunzehnten Jahrhundert",
        )
        self.assertEqual(
            tts._spell_numbers_de("Ende des 19. Jahrhunderts"),
            "Ende des neunzehnten Jahrhunderts",
        )
        self.assertEqual(
            tts._spell_numbers_de("in den 1930er Jahren"),
            "in den neunzehnhundertdreißiger Jahren",
        )
        self.assertEqual(
            tts._spell_numbers_de("Konstruktion vom 4.10.1926"),
            "Konstruktion vom vierten Oktober neunzehnhundertsechsundzwanzig",
        )
        self.assertEqual(
            tts._spell_numbers_de("Konstruktion vom 4.10.26"),
            "Konstruktion vom vierten Oktober sechsundzwanzig",
        )
        self.assertEqual(
            tts._spell_numbers_de("der 1. Weltkrieg"),
            "der erste Weltkrieg",
        )

    def test_regnal_numbers(self):
        self.assertEqual(
            tts._spell_numbers_de("König Friedrich I. von Württemberg"),
            "König Friedrich der Erste von Württemberg",
        )
        self.assertEqual(
            tts._spell_numbers_de("Kaiser Wilhelm II. erhielt"),
            "Kaiser Wilhelm der Zweite erhielt",
        )
        self.assertEqual(tts._spell_numbers_de("Kapitel I."), "Kapitel I.")

    def test_quantities_and_decimals(self):
        self.assertEqual(tts._spell_numbers_de("1.000 Pistolen"), "eintausend Pistolen")
        self.assertEqual(tts._spell_numbers_de("3.100 Stück"), "dreitausendeinhundert Stück")
        self.assertEqual(tts._spell_numbers_de("150.000 Stück"), "einhundertfünfzigtausend Stück")
        self.assertEqual(
            tts._spell_numbers_de("7,63 mal 25 Millimeter"),
            "sieben Komma sechs drei mal fünfundzwanzig Millimeter",
        )
        self.assertEqual(tts._spell_numbers_de("1,16 Kilogramm"), "eins Komma eins sechs Kilogramm")
        self.assertEqual(
            tts._spell_numbers_de(".357 Magnum"),
            "Punkt dreihundertsiebenundfünfzig Magnum",
        )

    def test_patent_numbers(self):
        self.assertEqual(
            tts._spell_numbers_de("Das Patent trägt die Nummer 1.427.097."),
            "Das Patent trägt die Nummer eins vier zwei sieben null neun sieben.",
        )
        self.assertEqual(
            tts._spell_numbers_de("Patentschrift Nummer 11943"),
            "Patentschrift Nummer eins eins neun vier drei",
        )
        self.assertEqual(
            tts._spell_numbers_de("Nummer 90430"),
            "Nummer neun null vier drei null",
        )

    def test_auto_spells_german_for_cosyvoice(self):
        args = SimpleNamespace(speed=1.0, normalize_text=False, normalize_loudness=False, lufs_target=-23.0, spell_numbers="auto")
        body, _ = tts._build_http_request(args, "C96 und 1887", tts.ENGINE_CONFIGS["cosyvoice"])
        decoded = body.decode("utf-8")
        self.assertIn("C sechsundneunzig", decoded)
        self.assertIn("achtzehnhundertsiebenundachtzig", decoded)

    def test_auto_leaves_kokoro_unchanged(self):
        args = SimpleNamespace(speed=1.0, normalize_text=False, normalize_loudness=False, lufs_target=-23.0, spell_numbers="auto")
        body, _ = tts._build_http_request(args, "C96 und 1887", tts.ENGINE_CONFIGS["kokoro"])
        self.assertEqual(json.loads(body)["text"], "C96 und 1887")

    def test_de_applies_to_kokoro(self):
        args = SimpleNamespace(speed=1.0, normalize_text=False, normalize_loudness=False, lufs_target=-23.0, spell_numbers="de")
        body, _ = tts._build_http_request(args, "C96 und 1887", tts.ENGINE_CONFIGS["kokoro"])
        self.assertEqual(json.loads(body)["text"], "C sechsundneunzig und achtzehnhundertsiebenundachtzig")

    def test_off_leaves_cosyvoice_unchanged(self):
        args = SimpleNamespace(speed=1.0, normalize_text=False, normalize_loudness=False, lufs_target=-23.0, spell_numbers="off")
        body, _ = tts._build_http_request(args, "C96 und 1887", tts.ENGINE_CONFIGS["cosyvoice"])
        self.assertIn("C96 und 1887", body.decode("utf-8"))

    def test_no_english_number_words(self):
        text = "Im Jahr 1887 kaufte Mustafa eine C96 für 1.000 Pistolen."
        result = tts._spell_numbers_de(text)
        self.assertEqual(
            result,
            "Im Jahr achtzehnhundertsiebenundachtzig kaufte Mustafa eine C sechsundneunzig für eintausend Pistolen.",
        )
        for word in ("thousand", "hundred", "ninety", "one", "eight"):
            self.assertNotIn(word, result.lower())


if __name__ == "__main__":
    unittest.main()
