#!/usr/bin/env python3
"""Client-side German number spelling for CosyVoice.

The Thorsten-Voice CosyVoice image uses WeText 0.0.4 and inflect for text
normalization. For German text, numbers are currently converted with English
number words. This standalone example spells numbers in German before sending
the text to the TTS server.

The module has no third-party dependencies. It is intentionally independent of
the audiobook generator so it can be tested and reused on its own.

Usage:
    from workaround_example import spell_numbers_de
    text = spell_numbers_de(text)

Run the tests:
    python3 workaround-example.py
"""

from __future__ import annotations

import re
import unittest

# German words for numbers from 0 to 19.
_SMALL = (
    "null",
    "eins",
    "zwei",
    "drei",
    "vier",
    "fünf",
    "sechs",
    "sieben",
    "acht",
    "neun",
    "zehn",
    "elf",
    "zwölf",
    "dreizehn",
    "vierzehn",
    "fünfzehn",
    "sechzehn",
    "siebzehn",
    "achtzehn",
    "neunzehn",
)

# German tens. The first two entries are placeholders.
_TENS = ("", "", "zwanzig", "dreißig", "vierzig", "fünfzig", "sechzig", "siebzig", "achtzig", "neunzig")

# Ordinal words that do not follow the regular "-ste" pattern.
_ORDINALS = {
    0: "nullte",
    1: "erste",
    2: "zweite",
    3: "dritte",
    4: "vierte",
    5: "fünfte",
    6: "sechste",
    7: "siebte",
    8: "achte",
    9: "neunte",
    10: "zehnte",
    11: "elfte",
    12: "zwölfte",
    13: "dreizehnte",
    14: "vierzehnte",
    15: "fünfzehnte",
    16: "sechzehnte",
    17: "siebzehnte",
    18: "achtzehnte",
    19: "neunzehnte",
}

_MONTH_NAMES = (
    "",
    "Januar",
    "Februar",
    "März",
    "April",
    "Mai",
    "Juni",
    "Juli",
    "August",
    "September",
    "Oktober",
    "November",
    "Dezember",
)
_MONTHS = "|".join(_MONTH_NAMES[1:])


def _cardinal_below_100(number: int) -> str:
    """Return the German cardinal word for 0 <= number < 100."""
    if number < 20:
        return _SMALL[number]
    tens, unit = divmod(number, 10)
    if unit == 0:
        return _TENS[tens]
    prefix = "ein" if unit == 1 else _SMALL[unit]
    return f"{prefix}und{_TENS[tens]}"


def _cardinal_below_1000(number: int) -> str:
    """Return the German cardinal word for 0 <= number < 1000."""
    if number < 100:
        return _cardinal_below_100(number)
    hundreds, rest = divmod(number, 100)
    prefix = "ein" if hundreds == 1 else _SMALL[hundreds]
    word = f"{prefix}hundert"
    if rest:
        word += _cardinal_below_100(rest)
    return word


def _cardinal_below_million(number: int) -> str:
    """Return the German cardinal word for 0 <= number < 1,000,000."""
    if number < 1000:
        return _cardinal_below_1000(number)
    thousands, rest = divmod(number, 1000)
    prefix = "ein" if thousands == 1 else _cardinal_below_1000(thousands)
    word = f"{prefix}tausend"
    if rest:
        word += _cardinal_below_1000(rest)
    return word


def _cardinal_de(number: int) -> str:
    """Return a German cardinal word for an integer."""
    if number < 0:
        return f"minus {_cardinal_de(-number)}"
    if number < 1000:
        return _cardinal_below_1000(number)
    if number < 1_000_000:
        return _cardinal_below_million(number)
    if number < 1_000_000_000:
        millions, rest = divmod(number, 1_000_000)
        prefix = "eine Million" if millions == 1 else f"{_cardinal_de(millions)} Millionen"
        return f"{prefix} {_cardinal_de(rest)}" if rest else prefix
    if number < 1_000_000_000_000:
        billions, rest = divmod(number, 1_000_000_000)
        prefix = "eine Milliarde" if billions == 1 else f"{_cardinal_de(billions)} Milliarden"
        return f"{prefix} {_cardinal_de(rest)}" if rest else prefix
    return _digits_de(str(number))


def _year_de(year: int) -> str:
    """Return the usual German reading for a year.

    Years from 1100 to 1999 are normally read as hundreds, for example
    1887 -> achtzehnhundertsiebenundachtzig. Other years use cardinal words.
    """
    if 1100 <= year <= 1999:
        hundreds, rest = divmod(year, 100)
        word = f"{_cardinal_below_100(hundreds)}hundert"
        if rest:
            word += _cardinal_below_100(rest)
        return word
    if year == 1000:
        return "tausend"
    return _cardinal_de(year)


def _ordinal_de(number: int, ending: str = "") -> str:
    """Return a German ordinal word with an optional adjective ending."""
    if number in _ORDINALS:
        word = _ORDINALS[number]
    elif number == 100:
        word = "hundertste"
    elif number == 1000:
        word = "tausendste"
    elif number == 1_000_000:
        word = "millionste"
    else:
        word = _cardinal_de(number)
        if word.endswith("eins"):
            word = f"{word[:-4]}erste"
        else:
            word += "ste"
    if ending == "e":
        return word
    return word + ending


def _digits_de(value: str) -> str:
    """Read a digit string one digit at a time, for example 08 -> null acht."""
    return " ".join(_SMALL[int(char)] for char in value if char.isdigit())


def _number_token_de(value: str) -> str:
    """Read a numeric token as a number, or digit by digit if it has a leading zero."""
    if len(value) > 1 and value.startswith("0"):
        return _digits_de(value)
    return _cardinal_de(int(value))


def _date_de(match: re.Match) -> str:
    """Convert a numeric date such as 27.06.1838 to German words."""
    day = int(match.group(1))
    month = int(match.group(2))
    year_text = match.group(3)
    if not 1 <= day <= 31 or not 1 <= month <= 12:
        return match.group(0)
    year_words = _year_de(int(year_text)) if len(year_text) == 4 else _number_token_de(year_text)
    return f"{_ordinal_de(day, 'n')} {_MONTH_NAMES[month]} {year_words}"


def _century_de(match: re.Match) -> str:
    """Convert 19. Jahrhundert / im 19. Jahrhundert with the correct ending."""
    before = match.string[max(0, match.start() - 24):match.start()].lower()
    if re.search(r"\b(?:im|des|vom|seit dem|anfang des|mitte des|ende des)\s*$", before):
        ending = "n"
    elif re.search(r"\bdas\s*$", before):
        ending = "e"
    else:
        ending = "s"
    return f"{_ordinal_de(int(match.group(1)), ending)} {match.group(2)}"


def _decade_de(value: str) -> str:
    """Convert 1930er to neunzehnhundertdreißiger."""
    number = int(value)
    if len(value) == 4 and 1100 <= number <= 1999:
        return f"{_year_de(number)}er"
    return f"{_cardinal_de(number)}er"


def spell_numbers_de(text: str) -> str:
    """Replace Arabic numerals in German text with spoken German words.

    The order of the substitutions matters:

    1. Number labels such as "Nummer 1.427.097" are read digit by digit.
    2. Letter-number designations such as "A96" are split into letter and number.
    3. Dates, decades, centuries, thousands, decimals and slash forms are handled.
    4. Years and leading-zero tokens are handled before the remaining integers.
    """
    text = re.sub(
        r"\bNummer\s+([0-9](?:[0-9.]*[0-9])?)",
        lambda match: f"Nummer {_digits_de(match.group(1))}",
        text,
    )
    text = re.sub(
        r"\b([A-Za-zÄÖÜäöüß]{1,12})(\d+)\b",
        lambda match: f"{match.group(1)} {_number_token_de(match.group(2))}",
        text,
    )
    text = re.sub(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})\b", _date_de, text)
    text = re.sub(
        rf"\b(\d{{1,2}})\.\s+({_MONTHS})\b",
        lambda match: f"{_ordinal_de(int(match.group(1)), 'n')} {match.group(2)}",
        text,
    )
    text = re.sub(r"\b(\d{1,3})\.\s+(Jahrhundert(?:s)?)\b", _century_de, text)
    text = re.sub(r"\b(\d{2,4})er\b", lambda match: _decade_de(match.group(1)), text)
    text = re.sub(
        r"\b\d{1,3}(?:\.\d{3})+\b",
        lambda match: _cardinal_de(int(match.group(0).replace(".", ""))),
        text,
    )
    text = re.sub(
        r"\b(\d+),(\d+)\b",
        lambda match: f"{_number_token_de(match.group(1))} Komma {_digits_de(match.group(2))}",
        text,
    )
    text = re.sub(
        r"\b(\d+)/(\d+)\b",
        lambda match: f"{_number_token_de(match.group(1))} {_number_token_de(match.group(2))}",
        text,
    )
    text = re.sub(r"\b(1\d{3})\b", lambda match: _year_de(int(match.group(1))), text)
    text = re.sub(r"\b0(\d+)\b", lambda match: f"null {_digits_de(match.group(1))}", text)
    text = re.sub(r"\b\d+\b", lambda match: _cardinal_de(int(match.group(0))), text)
    return text


class SpellNumbersDeTests(unittest.TestCase):
    """Small regression suite; run this file directly with Python."""

    def test_designation(self):
        self.assertEqual(spell_numbers_de("A96"), "A sechsundneunzig")

    def test_year(self):
        self.assertEqual(spell_numbers_de("1887"), "achtzehnhundertsiebenundachtzig")

    def test_decimal(self):
        self.assertEqual(spell_numbers_de("12,75"), "zwölf Komma sieben fünf")

    def test_slash_and_leading_zero(self):
        self.assertEqual(spell_numbers_de("04/12"), "null vier zwölf")
        self.assertEqual(spell_numbers_de("Version 08"), "Version null acht")

    def test_number_after_label(self):
        self.assertEqual(
            spell_numbers_de("Nummer 1.427.097"),
            "Nummer eins vier zwei sieben null neun sieben",
        )

    def test_date_and_year(self):
        self.assertEqual(
            spell_numbers_de("27. Juni 1838"),
            "siebenundzwanzigsten Juni achtzehnhundertachtunddreißig",
        )
        self.assertEqual(
            spell_numbers_de("27.06.1838"),
            "siebenundzwanzigsten Juni achtzehnhundertachtunddreißig",
        )

    def test_decade_and_century(self):
        self.assertEqual(spell_numbers_de("1930er"), "neunzehnhundertdreißiger")
        self.assertEqual(
            spell_numbers_de("im 19. Jahrhundert"),
            "im neunzehnten Jahrhundert",
        )

    def test_thousands_and_plain_integer(self):
        self.assertEqual(spell_numbers_de("1.000"), "eintausend")
        self.assertEqual(spell_numbers_de("42"), "zweiundvierzig")


if __name__ == "__main__":
    unittest.main(verbosity=2)
