#!/usr/bin/env python3
"""Health-data protection gate for the CI pipeline.

The Sedentary Tracker stores activity data linked to user accounts, which is
health data under GDPR Art. 9. This check fails the build if a committed file
looks like it contains real personal data instead of demo or sensor data.

Rules:
  1. E-mail addresses are only allowed on reserved test domains
     (example.com / example.org / example.net, RFC 2606).
  2. No German health insurance numbers (KVNR) or IBANs anywhere.
  3. Sensor logs (*.log) may only contain the fields ts, pir and acc.
     Anything else (a user id, a name, a location) would make the replay
     data personal.
  4. No .env files or database dumps may be committed.

Usage: python3 scripts/check_health_data.py   (scans all files tracked by git)
"""

import json
import re
import subprocess
import sys
from pathlib import Path

ALLOWED_EMAIL_DOMAINS = {"example.com", "example.org", "example.net"}
ALLOWED_SENSOR_FIELDS = {"ts", "pir", "acc"}
SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".pdf", ".lock", ".ico"}
FORBIDDEN_FILES = re.compile(r"(^|/)\.env$|\.(dump|sqlite|db)$|(^|/)backup.*\.sql$")

EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
KVNR = re.compile(r"\b[A-Z][0-9]{9}\b")  # German health insurance number
SERIAL_TIMESTAMP = re.compile(r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?\] ?")
IBAN = re.compile(r"\b[A-Z]{2}[0-9]{2}(?:\s?[A-Z0-9]{4}){3,7}\b")


def tracked_files():
    out = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=True)
    return [Path(p) for p in out.stdout.splitlines()]


def check_text(path, lines, findings):
    for no, line in enumerate(lines, 1):
        for match in EMAIL.finditer(line):
            domain = match.group(1).lower()
            if domain not in ALLOWED_EMAIL_DOMAINS:
                findings.append(f"{path}:{no}: e-mail outside test domains ({match.group(0)})")
        if KVNR.search(line):
            findings.append(f"{path}:{no}: possible health insurance number (KVNR)")
        if IBAN.search(line):
            findings.append(f"{path}:{no}: possible IBAN")


def check_sensor_log(path, lines, findings):
    records = malformed = 0
    for no, line in enumerate(lines, 1):
        # The serial monitor sometimes injected its own timestamp into a line,
        # e.g. "p[2026-01-23 16:13:53.660] ir". Remove it before checking.
        line = SERIAL_TIMESTAMP.sub("", line).strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1  # serial noise from the Arduino, not personal data
            continue
        if not isinstance(record, dict):
            continue
        records += 1
        # An empty field name comes from two readings merged into one line.
        extra = {k for k in record if k.strip()} - ALLOWED_SENSOR_FIELDS
        if extra:
            findings.append(f"{path}:{no}: sensor record has extra fields {sorted(extra)}")
    print(f"  {path}: {records} sensor records checked, {malformed} malformed lines ignored")


def main():
    findings = []
    files = tracked_files()
    for path in files:
        if FORBIDDEN_FILES.search(str(path)):
            findings.append(f"{path}: this kind of file must not be committed")
            continue
        if path.suffix.lower() in SKIP_SUFFIXES or not path.is_file():
            continue
        lines = path.read_text(errors="ignore").splitlines()
        check_text(path, lines, findings)
        if path.suffix == ".log":
            check_sensor_log(path, lines, findings)

    print(f"Scanned {len(files)} tracked files.")
    if findings:
        print("\nPossible personal or health data found:")
        print("\n".join(f"  - {f}" for f in findings))
        sys.exit(1)
    print("Health-data check passed: no personal data found.")


if __name__ == "__main__":
    main()
