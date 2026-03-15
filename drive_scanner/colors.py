"""ANSI escape code utilities for colored terminal output."""

import os
import sys

# ANSI escape codes
_RED = "\033[31m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_MAGENTA = "\033[35m"
_BOLD = "\033[1m"
_RESET = "\033[0m"

_enabled = True


def disable():
    global _enabled
    _enabled = False


def enable():
    global _enabled
    _enabled = True


def _wrap(color: str, text: str) -> str:
    if not _enabled or not sys.stdout.isatty():
        return text
    return f"{color}{text}{_RESET}"


def red(text: str) -> str:
    return _wrap(_RED, text)


def green(text: str) -> str:
    return _wrap(_GREEN, text)


def yellow(text: str) -> str:
    return _wrap(_YELLOW, text)


def cyan(text: str) -> str:
    return _wrap(_CYAN, text)


def magenta(text: str) -> str:
    return _wrap(_MAGENTA, text)


def bold(text: str) -> str:
    return _wrap(_BOLD, text)


def info(msg: str) -> None:
    print(cyan(f"[INFO] {msg}"))


def success(msg: str) -> None:
    print(green(f"[OK] {msg}"))


def warn(msg: str) -> None:
    print(yellow(f"[WARN] {msg}"))


def error(msg: str) -> None:
    print(red(f"[ERROR] {msg}"), file=sys.stderr)


def header(msg: str) -> None:
    print(bold(magenta(f"\n{'='*60}")))
    print(bold(magenta(f"  {msg}")))
    print(bold(magenta(f"{'='*60}")))
