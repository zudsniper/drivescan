"""Cryptocurrency wallet file filter."""

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import yaml

from . import BaseFilter

# Bitcoin Core / Berkeley DB magic bytes
BERKELEY_DB_MAGIC = b"\x00\x05\x31\x62"
BERKELEY_DB_MAGIC_ALT = b"\x62\x31\x05\x00"  # Little-endian variant


class CryptoWalletFilter(BaseFilter):
    name = "crypto_wallets"
    description = "Find cryptocurrency wallet files (Bitcoin, Litecoin, Electrum, etc.)"

    def __init__(self):
        super().__init__()
        self._enabled = True
        self._filenames: set[str] = set()
        self._extensions: set[str] = set()
        self._directory_indicators: set[str] = set()
        self._check_contents: bool = True
        self._btc_address_re: re.Pattern | None = None
        self._wif_key_re: re.Pattern | None = None

    def load_config(self, config_path: Path) -> None:
        config_file = config_path / "crypto_wallets.yaml"
        if not config_file.exists():
            self._set_defaults()
            return

        with open(config_file) as f:
            cfg = yaml.safe_load(f) or {}

        self._enabled = cfg.get("enabled", True)
        self._filenames = set(cfg.get("filenames", []))
        self._extensions = set(cfg.get("extensions", []))
        self._directory_indicators = set(cfg.get("directory_indicators", []))
        self._check_contents = cfg.get("check_file_contents", True)

        btc_re = cfg.get("bitcoin_address_regex")
        if btc_re:
            self._btc_address_re = re.compile(btc_re)

        wif_re = cfg.get("wif_private_key_regex")
        if wif_re:
            self._wif_key_re = re.compile(wif_re)

        if not self._filenames:
            self._set_defaults()

    def _set_defaults(self):
        self._filenames = {
            "wallet.dat", "default_wallet", "multibit.properties",
        }
        self._extensions = {".wallet", ".key", ".aes.json"}
        self._directory_indicators = {
            ".bitcoin", ".litecoin", ".dogecoin", ".electrum",
            ".armory", ".multibit", ".multibit-hd",
            ".ethereum", ".monero",
        }
        self._check_contents = True
        self._btc_address_re = re.compile(r"[13][a-km-zA-HJ-NP-Z1-9]{25,34}")
        self._wif_key_re = re.compile(r"5[HJK][1-9A-HJ-NP-Za-km-z]{49}")

    def match(self, file_path: Path, file_stat: os.stat_result) -> Optional[dict]:
        filename = file_path.name.lower()
        ext = file_path.suffix.lower()
        parts = set(file_path.parts)
        mtime = datetime.fromtimestamp(file_stat.st_mtime).isoformat()
        size = file_stat.st_size

        # Check 1: Known filenames
        if filename in {f.lower() for f in self._filenames}:
            wallet_type, confidence = self._identify_wallet_type(file_path, filename, parts)
            return self.record_match({
                "path": str(file_path),
                "wallet_type": wallet_type,
                "confidence": confidence,
                "size": size,
                "modified": mtime,
                "match_reason": "known filename",
            })

        # Check 2: Known extensions
        # Handle compound extension like .aes.json
        has_matching_ext = ext in self._extensions
        if not has_matching_ext and file_path.name.count(".") >= 2:
            compound = "." + ".".join(file_path.name.rsplit(".", 2)[-2:])
            has_matching_ext = compound.lower() in self._extensions

        if has_matching_ext:
            wallet_type, confidence = self._identify_wallet_type(file_path, filename, parts)
            return self.record_match({
                "path": str(file_path),
                "wallet_type": wallet_type,
                "confidence": confidence,
                "size": size,
                "modified": mtime,
                "match_reason": "known extension",
            })

        # Check 3: Files inside crypto data directories
        if self._directory_indicators & {p.lower() for p in parts}:
            # Only flag certain file types inside crypto dirs
            if ext in {".dat", ".db", ".log", ".conf", ""} or filename.startswith("armory_"):
                wallet_type, confidence = self._identify_wallet_type(file_path, filename, parts)
                return self.record_match({
                    "path": str(file_path),
                    "wallet_type": wallet_type,
                    "confidence": confidence,
                    "size": size,
                    "modified": mtime,
                    "match_reason": "crypto data directory",
                })

        # Check 4: Armory files
        if filename.startswith("armory_"):
            return self.record_match({
                "path": str(file_path),
                "wallet_type": "Armory",
                "confidence": "high",
                "size": size,
                "modified": mtime,
                "match_reason": "armory filename pattern",
            })

        # Check 5: PEM private key files
        if ext == ".pem" and self._check_contents and size < 10_000:
            try:
                with open(file_path, "rb") as f:
                    header = f.read(64)
                if b"PRIVATE KEY" in header:
                    return self.record_match({
                        "path": str(file_path),
                        "wallet_type": "Private Key (PEM)",
                        "confidence": "medium",
                        "size": size,
                        "modified": mtime,
                        "match_reason": "PEM private key",
                    })
            except (OSError, PermissionError):
                pass

        # Check 6: Paper wallet indicators in PDF/HTML
        if self._check_contents and ext in {".pdf", ".html", ".htm"} and size < 5_000_000:
            return self._check_paper_wallet(file_path, size, mtime)

        return None

    def _identify_wallet_type(self, path: Path, filename: str, parts: set[str]) -> tuple[str, str]:
        """Identify wallet type and confidence level."""
        lower_parts = {p.lower() for p in parts}

        if filename == "wallet.dat":
            # Check magic bytes for Berkeley DB
            if self._check_contents:
                try:
                    with open(path, "rb") as f:
                        header = f.read(16)
                    if header[12:16] in (BERKELEY_DB_MAGIC, BERKELEY_DB_MAGIC_ALT):
                        if ".bitcoin" in lower_parts:
                            return "Bitcoin Core", "high"
                        if ".litecoin" in lower_parts:
                            return "Litecoin Core", "high"
                        if ".dogecoin" in lower_parts:
                            return "Dogecoin Core", "high"
                        return "Bitcoin Core (or altcoin)", "high"
                except (OSError, PermissionError):
                    pass

            if ".bitcoin" in lower_parts:
                return "Bitcoin Core", "high"
            if ".litecoin" in lower_parts:
                return "Litecoin Core", "high"
            if ".dogecoin" in lower_parts:
                return "Dogecoin Core", "high"
            return "Bitcoin Core (suspected)", "medium"

        if filename == "default_wallet" or ".electrum" in lower_parts:
            return "Electrum", "high"

        if filename == "multibit.properties" or ".multibit" in lower_parts:
            return "MultiBit", "high"

        if ".multibit-hd" in lower_parts:
            return "MultiBit HD", "high"

        if ".armory" in lower_parts or filename.startswith("armory_"):
            return "Armory", "high"

        if ".ethereum" in lower_parts:
            return "Ethereum Keystore", "medium"

        if path.suffix.lower() == ".aes.json" or path.name.endswith(".aes.json"):
            return "MyEtherWallet Keystore", "medium"

        if path.suffix.lower() == ".wallet":
            return "Unknown Wallet", "low"

        return "Unknown", "low"

    def _check_paper_wallet(self, file_path: Path, size: int, mtime: str) -> Optional[dict]:
        """Check PDF/HTML files for Bitcoin address or private key patterns."""
        try:
            with open(file_path, "rb") as f:
                content = f.read(min(size, 1_000_000))
            text = content.decode("utf-8", errors="ignore")
        except (OSError, PermissionError):
            return None

        found_address = self._btc_address_re and self._btc_address_re.search(text)
        found_key = self._wif_key_re and self._wif_key_re.search(text)

        if found_address or found_key:
            indicators = []
            if found_address:
                indicators.append("Bitcoin address")
            if found_key:
                indicators.append("WIF private key")

            return self.record_match({
                "path": str(file_path),
                "wallet_type": "Paper Wallet (suspected)",
                "confidence": "high" if found_key else "low",
                "size": size,
                "modified": mtime,
                "match_reason": f"contains {', '.join(indicators)}",
            })

        return None

    def summary(self) -> str:
        if not self._matches:
            return "No cryptocurrency wallets found."
        by_type: dict[str, int] = {}
        for m in self._matches:
            t = m["wallet_type"]
            by_type[t] = by_type.get(t, 0) + 1
        breakdown = ", ".join(f"{v} {k}" for k, v in sorted(by_type.items()))
        return f"Found {self._match_count} potential wallet files: {breakdown}"
