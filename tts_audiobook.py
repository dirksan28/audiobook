#!/usr/bin/env python3
from __future__ import annotations

import argparse
import atexit
import io
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence
from urllib import error, parse, request

DEFAULT_PORT = 8000
DEFAULT_MP3_BITRATE = "192k"
DEFAULT_MP3_SAMPLE_RATE = 44100
DEFAULT_KOKORO_EPOCH = 5
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
BITRATE_PATTERN = re.compile(r"^\d+[kKmM]?$")
_CONTAINER_CLEANUP_NAME: Optional[str] = None
_CONTAINER_CLEANUP_DONE = False


class SetupError(RuntimeError):
    pass


class TtsError(RuntimeError):
    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass
class GpuStatus:
    usable: bool
    reason: str
    hardware: bool
    remediation: str = ""


@dataclass
class ServerContext:
    url: str
    container_name: Optional[str] = None
    uses_gpu: Optional[bool] = None


@dataclass(frozen=True)
class EngineConfig:
    name: str
    kind: str
    api: str
    image: Optional[str] = None
    container_name: Optional[str] = None
    model_volume: Optional[str] = None
    volume_target: Optional[str] = None
    internal_port: int = 8000
    gpu_capable: bool = False


ENGINE_CONFIGS = {
    "cosyvoice": EngineConfig(
        name="cosyvoice",
        kind="http",
        api="cosyvoice",
        image="thorstenvoice/cosyvoice-tts:cosyvoice3",
        container_name="cosyvoice-tts",
        model_volume="cosyvoice_models",
        volume_target="/app/CosyVoice/pretrained_models",
        internal_port=8000,
        gpu_capable=True,
    ),
    "kokoro": EngineConfig(
        name="kokoro",
        kind="http",
        api="kokoro",
        image="thorstenvoice/kokoro-tts:latest",
        container_name="thorsten-kokoro-tts",
        model_volume="kokoro_cache",
        volume_target="/data/hf-cache",
        internal_port=8000,
        gpu_capable=False,
    ),
    "piper": EngineConfig(
        name="piper",
        kind="local",
        api="piper",
    ),
}


def _positive_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _non_negative_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return parsed


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def _non_negative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must not be negative")
    return parsed


def _speed(value: str) -> float:
    parsed = _positive_float(value)
    if not 0.5 <= parsed <= 2.0:
        raise argparse.ArgumentTypeError("must be between 0.5 and 2.0")
    return parsed


def _epoch(value: str) -> int:
    parsed = _positive_int(value)
    if not 1 <= parsed <= 10:
        raise argparse.ArgumentTypeError("must be between 1 and 10")
    return parsed


def _port(value: str) -> int:
    parsed = _positive_int(value)
    if parsed > 65535:
        raise argparse.ArgumentTypeError("must be between 1 and 65535")
    return parsed


def _bitrate(value: str) -> str:
    if not BITRATE_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError("must look like 192k or 192000")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate German audiobook MP3s with Thorsten-Voice CosyVoice3, Kokoro, or Piper.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    input_group = parser.add_argument_group("input and output")
    input_group.add_argument(
        "--text-dir",
        "--text-file",
        dest="text_paths",
        metavar="PATH",
        type=Path,
        nargs="+",
        action="extend",
        required=True,
        default=argparse.SUPPRESS,
        help="one or more text directories or text files",
    )
    input_group.add_argument("--glob", default="*.txt", help="glob pattern used to find text files in directory inputs")
    input_group.add_argument("--recursive", action="store_true", help="search subdirectories of directory inputs")
    input_group.add_argument(
        "--force",
        "--overwrite",
        dest="force",
        action="store_true",
        help="regenerate MP3s even when they already exist",
    )
    input_group.add_argument("--dry-run", action="store_true", help="show what would be done without contacting Docker or the server")
    input_group.add_argument(
        "--cooldown",
        metavar="SECONDS",
        type=_non_negative_float,
        default=0.0,
        help="pause after each generated MP3 to let the system cool down",
    )

    engine_group = parser.add_argument_group("engine")
    engine_group.add_argument("--engine", choices=("cosyvoice", "kokoro", "piper"), default="cosyvoice", help="speech engine")
    engine_group.add_argument("--kokoro-epoch", type=_epoch, default=DEFAULT_KOKORO_EPOCH, help="Kokoro training checkpoint epoch, from 1 to 10")

    server_group = parser.add_argument_group("HTTP engine server")
    server_group.add_argument("--server-url", help="existing TTS server URL, for example http://127.0.0.1:8000")
    server_group.add_argument("--port", type=_port, help="local Docker and server port (default: 8000 or the port from --server-url)")
    server_group.add_argument("--timeout", type=_positive_float, default=7200.0, help="HTTP timeout in seconds per synthesis request")
    server_group.add_argument("--retries", type=_non_negative_int, default=2, help="number of retries after failed synthesis requests")
    server_group.add_argument("--start-timeout", type=_positive_float, default=1800.0, help="seconds to wait for a new container to become healthy")

    tts_group = parser.add_argument_group("generator options")
    tts_group.add_argument("--speed", type=_speed, default=1.0, help="speaking speed, from 0.5 to 2.0; Piper maps this to length scale")
    tts_group.add_argument(
        "--spell-numbers",
        choices=("auto", "de", "off"),
        default="auto",
        help="spell out numbers before synthesis; auto uses German for CosyVoice, de for all engines, off disables",
    )
    tts_group.add_argument("--normalize-text", action=argparse.BooleanOptionalAction, default=False, help="request CosyVoice text normalization (server support required)")
    tts_group.add_argument("--normalize-loudness", action=argparse.BooleanOptionalAction, default=False, help="request CosyVoice EBU R128 loudness normalization (server support required)")
    tts_group.add_argument("--lufs-target", type=float, default=-23.0, help="LUFS target for CosyVoice loudness normalization (server support required)")

    mp3_group = parser.add_argument_group("MP3 output")
    mp3_group.add_argument("--mp3-bitrate", type=_bitrate, default=DEFAULT_MP3_BITRATE, help="MP3 audio bitrate")
    mp3_group.add_argument("--mp3-sample-rate", type=_positive_int, default=DEFAULT_MP3_SAMPLE_RATE, help="MP3 sample rate in Hz")
    mp3_group.add_argument("--artist", default="Thorsten-Voice", help="artist ID3 tag")
    mp3_group.add_argument("--album", default="", help="album ID3 tag; omitted when empty")
    mp3_group.add_argument("--no-tags", action="store_true", help="do not write ID3 tags")

    docker_group = parser.add_argument_group("Docker and GPU")
    docker_group.add_argument("--docker", choices=("auto", "never"), default="auto", help="start or reuse a local Docker container when needed")
    docker_group.add_argument("--gpu", choices=("auto", "on", "off"), default="auto", help="NVIDIA GPU usage for CosyVoice; Kokoro and Piper run on CPU")
    docker_group.add_argument("--docker-network", help="Docker network mode for a managed container, for example host")
    docker_group.add_argument("--image", help="Docker image (default depends on --engine)")
    docker_group.add_argument("--container-name", help="managed Docker container name (default depends on --engine)")
    docker_group.add_argument("--model-volume", help="Docker volume for pretrained models (default depends on --engine)")
    docker_group.add_argument("--recreate", action="store_true", help="remove and recreate an existing managed container")
    docker_group.add_argument(
        "--stop-container",
        action="store_true",
        default=True,
        help="accepted for compatibility; managed containers are always stopped on exit",
    )

    piper_group = parser.add_argument_group("Piper engine")
    piper_group.add_argument("--piper-binary", help="Piper executable (default: piper on PATH or python -m piper)")
    piper_group.add_argument("--piper-model", type=Path, help="path to the Piper .onnx model; required for --engine piper")
    piper_group.add_argument("--piper-speaker", type=_non_negative_int, help="Piper speaker id for multi-speaker models")
    piper_group.add_argument("--piper-length-scale", type=_positive_float, help="Piper length scale; overrides --speed when set")

    return parser


def _format_duration(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.1f}s"
    total = int(round(seconds))
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {sec}s"
    return f"{minutes}m {sec}s"


def _format_timestamp(seconds: float) -> str:
    total = int(round(seconds))
    minutes, sec = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes}:{sec:02d}"


def _format_size(size: int) -> str:
    if size >= 1024 * 1024:
        return f"{size / (1024 * 1024):.1f} MiB"
    if size >= 1024:
        return f"{size / 1024:.0f} KiB"
    return f"{size} B"


_GERMAN_SMALL = (
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
_GERMAN_TENS = ("", "", "zwanzig", "dreißig", "vierzig", "fünfzig", "sechzig", "siebzig", "achtzig", "neunzig")
_GERMAN_ORDINALS = {
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
_GERMAN_MONTHS = "Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember"
_ROMAN_VALUES = {
    "I": 1,
    "II": 2,
    "III": 3,
    "IV": 4,
    "V": 5,
    "VI": 6,
    "VII": 7,
    "VIII": 8,
    "IX": 9,
    "X": 10,
}
_GERMAN_MONTH_NAMES = (
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


def _cardinal_below_100(number: int) -> str:
    if number < 20:
        return _GERMAN_SMALL[number]
    tens, unit = divmod(number, 10)
    if unit == 0:
        return _GERMAN_TENS[tens]
    prefix = "ein" if unit == 1 else _GERMAN_SMALL[unit]
    return f"{prefix}und{_GERMAN_TENS[tens]}"


def _cardinal_below_1000(number: int) -> str:
    if number < 100:
        return _cardinal_below_100(number)
    hundreds, rest = divmod(number, 100)
    prefix = "ein" if hundreds == 1 else _GERMAN_SMALL[hundreds]
    word = f"{prefix}hundert"
    if rest:
        word += _cardinal_below_100(rest)
    return word


def _cardinal_below_million(number: int) -> str:
    if number < 1000:
        return _cardinal_below_1000(number)
    thousands, rest = divmod(number, 1000)
    prefix = "ein" if thousands == 1 else _cardinal_below_1000(thousands)
    word = f"{prefix}tausend"
    if rest:
        word += _cardinal_below_1000(rest)
    return word


def _cardinal_de(number: int) -> str:
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
    if number in _GERMAN_ORDINALS:
        word = _GERMAN_ORDINALS[number]
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
    return " ".join(_GERMAN_SMALL[int(char)] for char in value if char.isdigit())


def _number_token_de(value: str) -> str:
    if len(value) > 1 and value.startswith("0"):
        return _digits_de(value)
    return _cardinal_de(int(value))


def _century_de(match: re.Match) -> str:
    before = match.string[max(0, match.start() - 24):match.start()].lower()
    if re.search(r"\b(?:im|des|vom|seit dem|anfang des|mitte des|ende des)\s*$", before):
        ending = "n"
    elif re.search(r"\bdas\s*$", before):
        ending = "e"
    else:
        ending = "s"
    return f"{_ordinal_de(int(match.group(1)), ending)} {match.group(2)}"


def _war_de(match: re.Match) -> str:
    before = match.string[max(0, match.start() - 20):match.start()].lower()
    ending = "r"
    if re.search(r"\b(?:im|am|dem|des|vom|seit dem|nach dem|vor dem)\s*$", before):
        ending = "n"
    elif re.search(r"\b(?:der|die|das)\s*$", before):
        ending = "e"
    return f"{_ordinal_de(int(match.group(1)), ending)} {match.group(2)}"


def _date_de(match: re.Match) -> str:
    day = int(match.group(1))
    month = int(match.group(2))
    year_text = match.group(3)
    if not 1 <= day <= 31 or not 1 <= month <= 12:
        return match.group(0)
    year_words = _year_de(int(year_text)) if len(year_text) == 4 else _number_token_de(year_text)
    return f"{_ordinal_de(day, 'n')} {_GERMAN_MONTH_NAMES[month]} {year_words}"


def _regnal_de(match: re.Match) -> str:
    ordinal = _ordinal_de(_ROMAN_VALUES[match.group(3)], "e").capitalize()
    return f"{match.group(1)} {match.group(2)} der {ordinal}"


def _decade_de(value: str) -> str:
    number = int(value)
    if len(value) == 4 and 1100 <= number <= 1999:
        return f"{_year_de(number)}er"
    return f"{_cardinal_de(number)}er"


def _spell_numbers_de(text: str) -> str:
    text = re.sub(
        r"\b(Nummer|Patentschrift)\s+([0-9](?:[0-9.]*[0-9])?)",
        lambda match: f"{match.group(1)} {_digits_de(match.group(2))}",
        text,
    )
    text = re.sub(
        r"\b(König|Kaiser|Papst|Fürst|Herzog|Graf|Kurfürst|Zar|Sultan|Prinz|Prinzessin)\s+"
        r"([A-ZÄÖÜ][a-zäöüß]+)\s+(I|II|III|IV|V|VI|VII|VIII|IX|X)\.(?=\s|,|$)",
        _regnal_de,
        text,
    )
    text = re.sub(
        r"\b([A-Za-zÄÖÜäöüß]{1,12})(\d+)\b",
        lambda match: f"{match.group(1)} {_number_token_de(match.group(2))}",
        text,
    )
    text = re.sub(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})\b", _date_de, text)
    text = re.sub(
        rf"\b(\d{{1,2}})\.\s+({_GERMAN_MONTHS})\b",
        lambda match: f"{_ordinal_de(int(match.group(1)), 'n')} {match.group(2)}",
        text,
    )
    text = re.sub(r"\b(\d{1,3})\.\s+(Jahrhundert(?:s)?)\b", _century_de, text)
    text = re.sub(r"\b(\d{1,2})\.\s+(Weltkrieg(?:s)?)\b", _war_de, text)
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
    text = re.sub(r"(?<!\w)\.(\d+)\b", lambda match: f"Punkt {_number_token_de(match.group(1))}", text)
    text = re.sub(r"\b(1\d{3})\b", lambda match: _year_de(int(match.group(1))), text)
    text = re.sub(r"\b0(\d+)\b", lambda match: f"null {_digits_de(match.group(1))}", text)
    text = re.sub(r"\b\d+\b", lambda match: _cardinal_de(int(match.group(0))), text)
    return text


def _first_line(value: str) -> str:
    for line in value.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _indent(value: str, prefix: str = "  ") -> str:
    return "\n".join(prefix + line for line in value.splitlines())


def _natural_key(value: str) -> tuple:
    return tuple(
        (1, int(part)) if part.isdigit() else (0, part.casefold())
        for part in re.split(r"(\d+)", value)
    )


def _collect_text_files(root: Path, pattern: str, recursive: bool) -> list:
    iterator = root.rglob(pattern) if recursive else root.glob(pattern)
    files = [path for path in iterator if path.is_file()]
    files.sort(key=lambda path: _natural_key(path.relative_to(root).as_posix()))
    return files


def _resolve_input_files(input_paths: list, pattern: str, recursive: bool) -> list:
    missing = [path for path in input_paths if not path.exists()]
    if missing:
        missing_text = ", ".join(str(path) for path in missing)
        raise SetupError(f"input path(s) do not exist: {missing_text}")

    directories = [path for path in input_paths if path.is_dir()]
    if not directories and recursive:
        raise SetupError("--recursive can only be used with a text directory")
    if not directories and pattern != "*.txt":
        raise SetupError("--glob can only be used with a text directory")

    files = []
    for path in input_paths:
        if path.is_dir():
            directory_files = [
                candidate
                for candidate in _collect_text_files(path, pattern, recursive)
                if candidate.suffix.lower() == ".txt"
            ]
            if not directory_files:
                raise SetupError(f"no .txt files matching {pattern!r} found in {path}")
            files.extend(directory_files)
        elif path.is_file():
            if path.suffix.lower() != ".txt":
                raise SetupError(f"input file must have a .txt suffix: {path}")
            files.append(path)
        else:
            raise SetupError(f"input path is neither a file nor a directory: {path}")

    unique_files = []
    seen = set()
    for path in files:
        key = path.resolve()
        if key not in seen:
            seen.add(key)
            unique_files.append(path)
    if not unique_files:
        raise SetupError("no .txt files found")
    return unique_files


def _valid_existing(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _is_local_host(host: Optional[str]) -> bool:
    return bool(host) and host.lower() in LOCAL_HOSTS


def _is_local_url(url: str) -> bool:
    try:
        return _is_local_host(parse.urlparse(url).hostname)
    except ValueError:
        return False


def _resolve_endpoints(args: argparse.Namespace) -> None:
    if args.engine == "piper" and args.server_url is None:
        if args.port is None:
            args.port = DEFAULT_PORT
        return
    if args.server_url:
        args.server_url = args.server_url.strip().rstrip("/")
        parsed = parse.urlparse(args.server_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise SetupError("--server-url must be a full http:// or https:// URL")
        try:
            url_port = parsed.port
        except ValueError as exc:
            raise SetupError(f"invalid port in --server-url: {exc}") from exc
        url_port = url_port or (443 if parsed.scheme == "https" else 80)
        if args.port is None:
            args.port = url_port
        elif _is_local_host(parsed.hostname) and url_port != args.port:
            raise SetupError("the port in --server-url does not match --port")
    elif args.port is None:
        args.port = DEFAULT_PORT

    if not args.server_url:
        args.server_url = f"http://127.0.0.1:{args.port}"


def _resolve_engine_config(args: argparse.Namespace) -> EngineConfig:
    base = ENGINE_CONFIGS[args.engine]
    return EngineConfig(
        name=base.name,
        kind=base.kind,
        api=base.api,
        image=args.image or base.image,
        container_name=args.container_name or base.container_name,
        model_volume=args.model_volume or base.model_volume,
        volume_target=base.volume_target,
        internal_port=base.internal_port,
        gpu_capable=base.gpu_capable,
    )


def _validate_engine_args(args: argparse.Namespace, config: EngineConfig) -> None:
    if args.engine != "cosyvoice":
        if args.normalize_text:
            raise SetupError("--normalize-text is only supported by the CosyVoice engine")
        if args.normalize_loudness:
            raise SetupError("--normalize-loudness is only supported by the CosyVoice engine")
    if args.engine != "kokoro" and args.kokoro_epoch != DEFAULT_KOKORO_EPOCH:
        raise SetupError("--kokoro-epoch is only supported by the Kokoro engine")
    if args.engine == "kokoro" and args.gpu == "on":
        raise SetupError("Kokoro runs on the CPU; --gpu on is not supported")
    if args.engine == "piper":
        if args.piper_model is None:
            raise SetupError("--piper-model is required for the Piper engine")
        if args.gpu == "on":
            raise SetupError("Piper runs locally on the CPU; --gpu on is not supported")
        if args.server_url:
            raise SetupError("--server-url is not used by the Piper engine")
    else:
        if args.piper_model is not None or args.piper_binary or args.piper_speaker is not None or args.piper_length_scale is not None:
            raise SetupError("--piper-model, --piper-binary, --piper-speaker, and --piper-length-scale require --engine piper")
    if config.kind != "http" and (args.image or args.container_name or args.model_volume or args.docker_network):
        raise SetupError("--docker-network, --image, --container-name, and --model-volume are only used by Docker engines")
    if args.docker == "never" and args.docker_network:
        raise SetupError("--docker-network is only used when --docker auto manages a container")
    if args.docker_network == "host" and args.port != DEFAULT_PORT:
        raise SetupError(f"--docker-network host requires --port {DEFAULT_PORT}")


def _read_text(path: Path) -> str:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise RuntimeError(f"cannot decode {path.name} as UTF-8: {exc}") from exc
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _spell_numbers_enabled(args: argparse.Namespace, config: EngineConfig) -> bool:
    return args.spell_numbers == "de" or (args.spell_numbers == "auto" and config.name == "cosyvoice")


def _multipart(fields: dict) -> tuple:
    boundary = f"----tts-audiobook-{uuid.uuid4().hex}"
    chunks = []
    for name, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("utf-8"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _extract_error(body: bytes, fallback: str) -> str:
    text = body.decode("utf-8", "replace").strip()
    if not text:
        return fallback
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = None
    if isinstance(payload, dict):
        for key in ("error", "detail", "message"):
            value = payload.get(key)
            if value:
                return str(value)
    return text[:500]


def _post_tts(url: str, body: bytes, content_type: str, timeout: float) -> bytes:
    req = request.Request(
        url,
        data=body,
        headers={"Content-Type": content_type, "Accept": "audio/wav, application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as response:
            data = response.read()
    except error.HTTPError as exc:
        detail = _extract_error(exc.read(), f"HTTP {exc.code} {exc.reason}")
        raise TtsError(detail, retryable=exc.code >= 500) from exc
    except (error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise TtsError(f"request failed: {reason}", retryable=True) from exc

    if not _is_wav(data):
        detail = _extract_error(data, "server did not return a WAV file")
        raise TtsError(detail, retryable=True)
    return data


def _is_wav(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def _wav_duration(data: bytes) -> Optional[float]:
    try:
        with wave.open(io.BytesIO(data), "rb") as handle:
            framerate = handle.getframerate()
            if framerate <= 0:
                return None
            return handle.getnframes() / framerate
    except (EOFError, OSError, wave.Error):
        return None


def _build_http_request(args: argparse.Namespace, text: str, config: EngineConfig) -> tuple:
    if _spell_numbers_enabled(args, config):
        text = _spell_numbers_de(text)
    if config.api == "kokoro":
        payload = json.dumps({"text": text, "speed": args.speed}, ensure_ascii=False).encode("utf-8")
        return payload, "application/json"
    text = re.sub(r"\n\s*\n+", " ", text)
    fields = {
        "text": text,
        "speed": f"{args.speed:g}",
        "normalize_text": str(args.normalize_text).lower(),
        "normalize_loudness": str(args.normalize_loudness).lower(),
        "lufs_target": f"{args.lufs_target:g}",
    }
    return _multipart(fields)


def _synthesize_http(args: argparse.Namespace, text: str, config: EngineConfig) -> bytes:
    body, content_type = _build_http_request(args, text, config)
    url = f"{args.server_url}/tts"
    attempts = args.retries + 1
    for attempt in range(1, attempts + 1):
        try:
            return _post_tts(url, body, content_type, args.timeout)
        except TtsError as exc:
            if not exc.retryable or attempt == attempts:
                raise
            delay = min(30.0, 2.0 ** attempt)
            print(f"  request failed: {exc}", file=sys.stderr)
            print(f"  retrying in {delay:.0f}s ({attempt}/{args.retries})", file=sys.stderr)
            time.sleep(delay)
    raise RuntimeError("synthesis failed")


def _resolve_piper_command(args: argparse.Namespace) -> list:
    if args.piper_binary:
        candidate = Path(args.piper_binary).expanduser()
        if candidate.is_file():
            if not os.access(candidate, os.X_OK):
                raise SetupError(f"Piper binary is not executable: {candidate}")
            return [str(candidate)]
        found = shutil.which(args.piper_binary)
        if found:
            return [found]
        raise SetupError(f"Piper binary not found: {args.piper_binary}")
    found = shutil.which("piper")
    if found:
        return [found]
    try:
        probe = subprocess.run(
            [sys.executable, "-c", "import piper"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        probe = None
    if probe is not None and probe.returncode == 0:
        return [sys.executable, "-m", "piper"]
    raise SetupError(
        "Piper was not found. Install it in a virtual environment with "
        "'python3 -m venv .venv-piper && .venv-piper/bin/pip install piper-tts' "
        "or pass --piper-binary."
    )


def _prepare_piper(args: argparse.Namespace) -> None:
    model = args.piper_model.expanduser().resolve()
    if not model.is_file():
        raise SetupError(f"Piper model not found: {model}")
    config_path = Path(f"{model}.json")
    if not config_path.is_file():
        raise SetupError(f"Piper model config not found: {config_path}")
    args.piper_model = model
    args.piper_command = _resolve_piper_command(args)


def _synthesize_piper(args: argparse.Namespace, text: str) -> bytes:
    if args.spell_numbers == "de":
        text = _spell_numbers_de(text)
    length_scale = args.piper_length_scale
    if length_scale is None:
        length_scale = 1.0 / args.speed
    with tempfile.TemporaryDirectory(prefix="tts-piper-") as tmp_dir:
        wav_path = Path(tmp_dir) / "piper.wav"
        command = [
            *args.piper_command,
            "--model",
            str(args.piper_model),
            "--output_file",
            str(wav_path),
            "--length_scale",
            f"{length_scale:g}",
        ]
        if args.piper_speaker is not None:
            command.extend(["--speaker", str(args.piper_speaker)])
        result = subprocess.run(command, input=f"{text}\n", capture_output=True, text=True)
        if result.returncode != 0:
            detail = ((result.stderr or "") + (result.stdout or "")).strip()
            raise RuntimeError(f"piper failed: {detail}")
        if not wav_path.is_file() or wav_path.stat().st_size == 0:
            raise RuntimeError("piper produced no output")
        data = wav_path.read_bytes()
    if not _is_wav(data):
        raise RuntimeError("piper produced an invalid WAV file")
    return data


def _synthesize(args: argparse.Namespace, text: str, config: EngineConfig) -> bytes:
    if config.kind == "local":
        return _synthesize_piper(args, text)
    return _synthesize_http(args, text, config)


def _title_from_stem(stem: str) -> str:
    match = re.match(r"^\d+[_\-\s]*(.+)$", stem)
    title = match.group(1) if match else stem
    title = re.sub(r"[_]+", " ", title).strip()
    return title or stem


def _track_number(stem: str) -> str:
    match = re.match(r"^(\d+)", stem)
    if not match:
        return ""
    number = int(match.group(1))
    return str(number) if number > 0 else ""


def _convert_wav_to_mp3(wav_path: Path, target: Path, args: argparse.Namespace, ffmpeg: str, source: Path, total: int) -> None:
    tmp_path = target.with_name(f".{target.name}.tmp.mp3")
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(wav_path),
        "-vn",
        "-map_metadata",
        "-1",
        "-codec:a",
        "libmp3lame",
        "-b:a",
        args.mp3_bitrate,
        "-ar",
        str(args.mp3_sample_rate),
        "-ac",
        "1",
    ]
    if not args.no_tags:
        command.extend(["-metadata", f"title={_title_from_stem(source.stem)}"])
        command.extend(["-metadata", f"artist={args.artist}"])
        if args.album:
            command.extend(["-metadata", f"album={args.album}"])
        track = _track_number(source.stem)
        if track:
            command.extend(["-metadata", f"track={track}/{total}"])
        command.extend(["-id3v2_version", "3"])
    command.append(str(tmp_path))

    try:
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(f"ffmpeg failed: {detail}")
        if not tmp_path.is_file() or tmp_path.stat().st_size == 0:
            raise RuntimeError("ffmpeg produced no output")
        os.replace(tmp_path, target)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


def _process_file(source: Path, target: Path, args: argparse.Namespace, ffmpeg: str, total: int, config: EngineConfig) -> bool:
    text = _read_text(source)
    if not text:
        print("  empty file; skipping")
        return False

    print(f"  synthesizing {len(text)} characters", flush=True)
    started = time.monotonic()
    audio = _synthesize(args, text, config)
    duration = _wav_duration(audio)
    with tempfile.TemporaryDirectory(prefix="tts-audiobook-") as tmp_dir:
        wav_path = Path(tmp_dir) / "audio.wav"
        wav_path.write_bytes(audio)
        _convert_wav_to_mp3(wav_path, target, args, ffmpeg, source, total)

    elapsed = time.monotonic() - started
    size = target.stat().st_size
    duration_text = _format_timestamp(duration) if duration is not None else "unknown"
    print(
        f"  created {target.name}: duration={duration_text}, "
        f"size={_format_size(size)}, render={_format_duration(elapsed)}"
    )
    return True


def _check_health(url: str, timeout: float) -> Optional[dict]:
    health_url = f"{url.rstrip('/')}/health"
    req = request.Request(health_url, headers={"Accept": "application/json"})
    try:
        with request.urlopen(req, timeout=timeout) as response:
            if response.status != 200:
                return None
            payload = response.read()
    except (error.URLError, error.HTTPError, TimeoutError, socket.timeout, OSError, ValueError):
        return None
    try:
        data = json.loads(payload.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _ensure_docker_daemon() -> None:
    if shutil.which("docker") is None:
        raise SetupError("Docker is not installed. Install Docker Engine or Docker Desktop and try again.")
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SetupError(f"could not run Docker: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise SetupError(
            "Docker daemon is not reachable. Start Docker and make sure your user may access it. "
            f"Details: {detail}"
        )


def _docker_runtimes() -> dict:
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{json .Runtimes}}"],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _nvidia_cdi_available() -> bool:
    for directory in (Path("/etc/cdi"), Path("/var/run/cdi")):
        try:
            entries = list(directory.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.is_file() and entry.name.startswith("nvidia") and entry.suffix in {".yaml", ".yml", ".json"}:
                return True
    return False


def _detect_gpu() -> GpuStatus:
    if not sys.platform.startswith("linux"):
        return GpuStatus(
            False,
            "NVIDIA acceleration is only supported on Linux",
            False,
            "On macOS the container runs on the CPU.",
        )

    hardware = Path("/dev/nvidia0").exists()
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi is None:
        return GpuStatus(
            False,
            "nvidia-smi was not found",
            hardware,
            "Install the NVIDIA driver and the NVIDIA Container Toolkit.",
        )

    try:
        result = subprocess.run([nvidia_smi, "-L"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return GpuStatus(False, f"nvidia-smi failed: {exc}", hardware, "Check the NVIDIA driver installation.")

    if result.returncode != 0:
        detail = _first_line(result.stderr or result.stdout) or "unknown error"
        return GpuStatus(
            False,
            f"NVIDIA GPU is not usable: {detail}",
            hardware or bool(result.stdout.strip()),
            "Reboot the machine or reload the NVIDIA kernel modules. If the problem persists, reinstall the driver.",
        )

    if "GPU" not in result.stdout:
        return GpuStatus(False, "nvidia-smi found no GPU", hardware, "Check the NVIDIA driver installation.")

    if "nvidia" not in _docker_runtimes() and not _nvidia_cdi_available():
        return GpuStatus(
            False,
            "Docker NVIDIA runtime or CDI configuration is not available",
            True,
            "Run:\n  sudo nvidia-ctk runtime configure --runtime=docker\n"
            "  sudo nvidia-ctk cdi generate --output=/etc/cdi/nvidia.yaml\n"
            "  sudo systemctl restart docker",
        )

    return GpuStatus(True, "", True)


def _gpu_error(status: GpuStatus) -> str:
    message = f"GPU acceleration requested, but it is not available: {status.reason}."
    if status.remediation:
        message += f"\n{status.remediation}"
    return message


def _warn_gpu(status: GpuStatus, args: argparse.Namespace) -> None:
    if args.gpu == "off":
        print("GPU acceleration disabled; using CPU.")
        return
    if status.hardware:
        print(f"WARNING: {status.reason}. Falling back to CPU.")
        if status.remediation:
            print(_indent(status.remediation))
    else:
        print(f"No NVIDIA GPU acceleration available ({status.reason}); using CPU.")


def _docker_cmd(arguments: list, capture: bool = True, check: bool = True, timeout: Optional[float] = None) -> subprocess.CompletedProcess:
    command = ["docker", *arguments]
    try:
        result = subprocess.run(command, capture_output=capture, text=True, timeout=timeout)
    except FileNotFoundError as exc:
        raise SetupError("Docker executable not found") from exc
    except subprocess.TimeoutExpired as exc:
        raise SetupError(f"docker command timed out: {' '.join(arguments[:3])}") from exc
    if check and result.returncode != 0:
        detail = ((result.stderr or "") + (result.stdout or "")).strip()
        suffix = f": {detail}" if detail else ""
        raise SetupError(f"docker {' '.join(arguments[:3])} failed{suffix}")
    return result


def _inspect_container(name: str) -> Optional[dict]:
    try:
        result = _docker_cmd(["inspect", name], check=False, timeout=20)
    except SetupError:
        return None
    if result.returncode != 0:
        return None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if isinstance(payload, list) and payload:
        return payload[0]
    return None


def _container_running(info: dict) -> bool:
    return bool((info.get("State") or {}).get("Running"))


def _container_uses_gpu(info: dict) -> bool:
    host_config = info.get("HostConfig") or {}
    return bool(host_config.get("DeviceRequests")) or host_config.get("Runtime") == "nvidia"


def _container_network_mode(info: dict) -> str:
    return str((info.get("HostConfig") or {}).get("NetworkMode") or "default")


def _container_uses_host_network(info: dict) -> bool:
    return _container_network_mode(info) == "host"


def _container_has_port(info: dict, host_port: int, internal_port: int) -> bool:
    bindings = (info.get("HostConfig") or {}).get("PortBindings") or {}
    entries = bindings.get(f"{internal_port}/tcp") or []
    return any(str(entry.get("HostPort")) == str(host_port) for entry in entries)


def _container_env_value(info: dict, name: str) -> Optional[str]:
    env = (info.get("Config") or {}).get("Env") or []
    prefix = f"{name}="
    for entry in env:
        if str(entry).startswith(prefix):
            return str(entry)[len(prefix):]
    return None


def _container_run_command(args: argparse.Namespace, config: EngineConfig, use_gpu: bool) -> list:
    command = [
        "run",
        "-d",
        "--name",
        config.container_name,
        "--restart",
        "unless-stopped",
    ]
    if args.docker_network:
        command.extend(["--network", args.docker_network])
    if args.docker_network != "host":
        command.extend(["-p", f"{args.port}:{config.internal_port}"])
    command.extend(["-v", f"{config.model_volume}:{config.volume_target}"])
    if config.name == "cosyvoice":
        command.extend(["-e", f"PORT={args.port}"])
    elif config.name == "kokoro":
        command.extend(["-e", f"KOKORO_EPOCH={args.kokoro_epoch}"])
    if use_gpu:
        command.extend(["--gpus", "all"])
    command.append(config.image)
    return command


def _health_label(health: dict) -> str:
    if health.get("model"):
        return str(health["model"])
    if health.get("repo_id"):
        label = str(health["repo_id"])
        if health.get("epoch"):
            label += f", epoch {health['epoch']}"
        if health.get("device"):
            label += f", {health['device']}"
        return label
    return "unknown"


def _show_logs(name: str) -> None:
    try:
        result = _docker_cmd(["logs", "--tail", "80", name], check=False, timeout=20)
    except SetupError as exc:
        print(f"Could not read container logs: {exc}", file=sys.stderr)
        return
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if output:
        print(f"Recent logs from {name}:\n{output}", file=sys.stderr)


def _pull_image(image: str) -> None:
    result = _docker_cmd(["image", "inspect", image], check=False, timeout=30)
    if result.returncode != 0:
        print(f"Pulling Docker image {image}; this may take a while", flush=True)
        _docker_cmd(["pull", image], capture=False)


def _wait_for_health(args: argparse.Namespace, config: EngineConfig) -> None:
    print(f"Waiting for TTS server at {args.server_url}/health ...", flush=True)
    started = time.monotonic()
    deadline = started + args.start_timeout
    last_note = started
    download_note = "about 9 GB of models" if config.name == "cosyvoice" else "the model"
    while True:
        health = _check_health(args.server_url, timeout=3.0)
        if health is not None:
            print(f"TTS server is ready ({_health_label(health)}).")
            return

        info = _inspect_container(config.container_name)
        if info is not None:
            state = info.get("State") or {}
            restart_count = int(state.get("RestartCount") or 0)
            if not _container_running(info):
                _show_logs(config.container_name)
                raise SetupError(f"container {config.container_name} stopped before the server became healthy")
            if restart_count >= 2:
                _show_logs(config.container_name)
                raise SetupError(
                    f"container {config.container_name} is restarting repeatedly "
                    f"({restart_count} restarts); see logs above"
                )

        now = time.monotonic()
        if now >= deadline:
            _show_logs(config.container_name)
            raise SetupError(f"TTS server did not become healthy within {args.start_timeout:.0f}s")
        if now - last_note >= 15:
            print(
                f"  still waiting after {now - started:.0f}s; the first start downloads {download_note}",
                flush=True,
            )
            last_note = now
        time.sleep(3)


def _start_or_reuse_container(args: argparse.Namespace, config: EngineConfig) -> ServerContext:
    _ensure_docker_daemon()
    use_gpu = False
    if config.gpu_capable:
        if args.gpu == "off":
            gpu = GpuStatus(False, "disabled by --gpu off", False, "")
        else:
            gpu = _detect_gpu()
        if args.gpu == "on" and not gpu.usable:
            raise SetupError(_gpu_error(gpu))
        use_gpu = args.gpu != "off" and gpu.usable
        if use_gpu:
            print("NVIDIA GPU detected; the container will use --gpus all.")
        else:
            _warn_gpu(gpu, args)
    elif config.name == "kokoro":
        print("Kokoro runs on the CPU.")

    info = _inspect_container(config.container_name)
    if info is not None and args.recreate:
        print(f"Recreating container {config.container_name} ...")
        _docker_cmd(["rm", "-f", config.container_name])
        info = None

    if info is not None:
        if config.gpu_capable and use_gpu and not _container_uses_gpu(info):
            message = (
                f"container {config.container_name} exists but has no GPU access; "
                "re-run with --recreate to rebuild it"
            )
            if args.gpu == "on":
                raise SetupError(message)
            print(f"WARNING: {message}. Continuing with the CPU-only container.")
            use_gpu = False
        if config.name == "kokoro":
            container_epoch = _container_env_value(info, "KOKORO_EPOCH") or str(DEFAULT_KOKORO_EPOCH)
            if container_epoch != str(args.kokoro_epoch):
                raise SetupError(
                    f"container {config.container_name} uses KOKORO_EPOCH={container_epoch}, "
                    f"but --kokoro-epoch {args.kokoro_epoch} was requested; re-run with --recreate"
                )
        existing_network = _container_network_mode(info)
        host_network = _container_uses_host_network(info)
        if args.docker_network and existing_network != args.docker_network:
            raise SetupError(
                f"container {config.container_name} uses network {existing_network}, "
                f"but --docker-network {args.docker_network} was requested; re-run with --recreate"
            )
        if host_network:
            if args.port != config.internal_port:
                raise SetupError(
                    f"container {config.container_name} uses host networking and listens on "
                    f"port {config.internal_port}; use --port {config.internal_port}"
                )
        elif not _container_has_port(info, args.port, config.internal_port):
            raise SetupError(
                f"container {config.container_name} does not publish port {args.port}; "
                "re-run with --recreate"
            )
        if _container_running(info):
            print(f"Reusing running container {config.container_name}; not restarting it.")
            _register_container_cleanup(config.container_name)
        else:
            _register_container_cleanup(config.container_name)
            print(f"Starting container {config.container_name} ...")
            _docker_cmd(["start", config.container_name])
    else:
        _pull_image(config.image)
        _register_container_cleanup(config.container_name)
        command = _container_run_command(args, config, use_gpu)
        print(f"Starting {config.name} container on port {args.port} ...")
        _docker_cmd(command)

    _wait_for_health(args, config)
    return ServerContext(args.server_url, config.container_name, use_gpu if config.gpu_capable else None)


def _existing_context(args: argparse.Namespace, config: EngineConfig) -> ServerContext:
    container_name = None
    uses_gpu = None
    if _is_local_url(args.server_url) and shutil.which("docker"):
        info = _inspect_container(config.container_name)
        if info is not None:
            container_name = config.container_name
            if args.docker_network and _container_network_mode(info) != args.docker_network:
                raise SetupError(
                    f"container {config.container_name} uses network {_container_network_mode(info)}, "
                    f"but --docker-network {args.docker_network} was requested; re-run with --recreate"
                )
            if config.name == "kokoro":
                container_epoch = _container_env_value(info, "KOKORO_EPOCH") or str(DEFAULT_KOKORO_EPOCH)
                if container_epoch != str(args.kokoro_epoch):
                    raise SetupError(
                        f"container {config.container_name} uses KOKORO_EPOCH={container_epoch}, "
                        f"but --kokoro-epoch {args.kokoro_epoch} was requested; re-run with --recreate"
                    )
            uses_gpu = _container_uses_gpu(info) if config.gpu_capable else None
            if config.gpu_capable and args.gpu == "on" and not uses_gpu:
                raise SetupError(
                    f"the server is reachable, but container {config.container_name} has no GPU access; "
                    "re-run with --recreate"
                )
            if uses_gpu:
                print("  Container has GPU access.")
            if args.docker != "never":
                _register_container_cleanup(config.container_name)
    if config.gpu_capable and args.gpu == "on" and container_name is None:
        print("  WARNING: cannot verify GPU usage for an externally managed server.", file=sys.stderr)
    return ServerContext(args.server_url, container_name, uses_gpu)


def _ensure_server(args: argparse.Namespace, config: EngineConfig) -> ServerContext:
    health = _check_health(args.server_url, timeout=4.0)
    if health is not None:
        if args.recreate and args.docker != "never" and _is_local_url(args.server_url) and shutil.which("docker"):
            info = _inspect_container(config.container_name)
            if info is not None and _container_running(info):
                print(f"Recreating running container {config.container_name} ...")
                return _start_or_reuse_container(args, config)
            print("WARNING: --recreate ignored because no running managed container was found.", file=sys.stderr)
        print(f"Using existing TTS server at {args.server_url} ({_health_label(health)})")
        context = _existing_context(args, config)
        if config.name == "kokoro" and context.container_name is None and health.get("epoch") is not None:
            actual_epoch = str(health["epoch"])
            if actual_epoch != str(args.kokoro_epoch):
                print(
                    f"WARNING: the external Kokoro server uses epoch {actual_epoch}; "
                    "--kokoro-epoch only controls a newly created container.",
                    file=sys.stderr,
                )
        return context

    if args.docker == "never":
        raise SetupError(
            f"TTS server at {args.server_url} is not reachable and --docker never is set"
        )

    if not _is_local_url(args.server_url):
        raise SetupError(
            f"TTS server at {args.server_url} is not reachable; "
            "automatic Docker startup only works with a localhost URL"
        )

    return _start_or_reuse_container(args, config)


def _register_container_cleanup(name: str) -> None:
    global _CONTAINER_CLEANUP_NAME, _CONTAINER_CLEANUP_DONE
    _CONTAINER_CLEANUP_NAME = name
    _CONTAINER_CLEANUP_DONE = False


def _stop_container(name: str) -> None:
    try:
        info = _inspect_container(name)
        if info is None or not _container_running(info):
            return
        print(f"Stopping container {name} ...")
        _docker_cmd(["stop", name], timeout=60)
    except SetupError as exc:
        print(f"WARNING: could not stop container: {exc}", file=sys.stderr)


def _cleanup_container() -> None:
    global _CONTAINER_CLEANUP_DONE
    name = _CONTAINER_CLEANUP_NAME
    if not name or _CONTAINER_CLEANUP_DONE:
        return
    _CONTAINER_CLEANUP_DONE = True
    _stop_container(name)


def _handle_termination(signum: int, frame: object) -> None:
    raise SystemExit(128 + signum)


atexit.register(_cleanup_container)


def _print_synthesis_summary(args: argparse.Namespace, config: EngineConfig) -> None:
    number_mode = "German" if _spell_numbers_enabled(args, config) else "unchanged"
    print(f"Numbers: {number_mode}")
    if config.api == "cosyvoice":
        print(
            "Synthesis: "
            f"speed={args.speed:g}, "
            f"normalize_text={args.normalize_text}, "
            f"normalize_loudness={args.normalize_loudness}, "
            f"lufs_target={args.lufs_target:g}"
        )
    elif config.api == "kokoro":
        print(f"Synthesis: speed={args.speed:g}, epoch={args.kokoro_epoch}")
    else:
        length_scale = args.piper_length_scale
        if length_scale is None:
            length_scale = 1.0 / args.speed
        print(
            f"Piper: model={args.piper_model}, length_scale={length_scale:g}, "
            f"speaker={args.piper_speaker if args.piper_speaker is not None else 'default'}"
        )


def _print_dry_run(files: list, args: argparse.Namespace, config: EngineConfig) -> None:
    print(f"Dry run: {len(files)} file(s) from {len(args.text_paths)} input(s)")
    print(f"Engine: {config.name}")
    if config.kind == "http":
        print(f"Server: {args.server_url}")
    _print_synthesis_summary(args, config)
    print(f"MP3: {args.mp3_bitrate}, {args.mp3_sample_rate} Hz")
    for index, source in enumerate(files, 1):
        target = source.with_suffix(".mp3")
        if _valid_existing(target) and not args.force:
            action = "skip (exists)"
        elif _valid_existing(target):
            action = "overwrite"
        else:
            action = "create"
        print(f"{index}/{len(files)}: {action}: {source} -> {target}")


def _run(args: argparse.Namespace) -> int:
    config = _resolve_engine_config(args)
    _validate_engine_args(args, config)

    input_paths = [path.expanduser().resolve() for path in args.text_paths]
    files = _resolve_input_files(input_paths, args.glob, args.recursive)
    args.text_paths = input_paths

    if args.dry_run:
        _print_dry_run(files, args, config)
        return 0

    pending = []
    skipped = 0
    for index, source in enumerate(files, 1):
        target = source.with_suffix(".mp3")
        if _valid_existing(target) and not args.force:
            print(f"{index}/{len(files)}: skip {target.name} (exists)")
            skipped += 1
        else:
            pending.append((index, source))

    if not pending:
        print(f"All {len(files)} file(s) already have an MP3. Use --force/--overwrite to regenerate.")
        return 0

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SetupError("ffmpeg was not found. Install it with apt on Linux or brew on macOS.")

    if config.kind == "http":
        context = _ensure_server(args, config)
    else:
        _prepare_piper(args)
        context = ServerContext("")

    if len(input_paths) == 1:
        print(f"Input: {input_paths[0]}")
    else:
        print(f"Inputs: {len(input_paths)} paths")
        for input_path in input_paths:
            print(f"  {input_path}")
    print(f"Engine: {config.name}")
    if config.kind == "http":
        print(f"Server: {context.url}")
    _print_synthesis_summary(args, config)

    generated = 0
    failed = 0
    started = time.monotonic()
    try:
        for index, source in pending:
            target = source.with_suffix(".mp3")
            print(f"{index}/{len(files)}: {source.name}")
            try:
                if _process_file(source, target, args, ffmpeg, len(files), config):
                    generated += 1
                    if args.cooldown > 0:
                        unit = "second" if args.cooldown == 1 else "seconds"
                        print(f"Cooling down for {args.cooldown:g} {unit}", flush=True)
                        time.sleep(args.cooldown)
                else:
                    skipped += 1
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"  error: {exc}", file=sys.stderr)
                failed += 1
    finally:
        _cleanup_container()

    elapsed = time.monotonic() - started
    print(
        f"\nFinished in {_format_duration(elapsed)}: "
        f"{generated} generated, {skipped} skipped, {failed} failed."
    )
    return 1 if failed else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    for signal_name in ("SIGTERM", "SIGHUP"):
        signum = getattr(signal, signal_name, None)
        if signum is None:
            continue
        try:
            signal.signal(signum, _handle_termination)
        except (OSError, ValueError):
            pass
    try:
        _resolve_endpoints(args)
        return _run(args)
    except SetupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
