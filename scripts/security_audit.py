"""Automated credential and secret audit script.

Scans the repository to ensure no AWS credentials, private keys, certificates,
or .env secrets are committed or tracked by Git.
"""

from pathlib import Path
import re
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Patterns for sensitive data
SECRET_PATTERNS = [
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS Access Key ID (AKIA...)"),
    (re.compile(r"BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY"), "Private Key Block"),
    (re.compile(r"(?:aws_secret_access_key|secret_key)\s*=\s*['\"][A-Za-z0-9/+=]{30,}['\"]"), "AWS Secret Access Key"),
    (re.compile(r"password\s*=\s*['\"][^'\"]{8,}['\"]", re.IGNORECASE), "Hardcoded Password"),
]

EXCLUDED_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache"}
EXCLUDED_EXTS = {".pyc", ".png", ".jpg", ".jpeg", ".ico"}


def audit_repository() -> int:
    """Scan all project files for sensitive secrets."""
    print("Running credential and security scan on repository...")
    violations = []
    scanned_files = 0

    for path in PROJECT_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        if path.suffix in EXCLUDED_EXTS:
            continue

        scanned_files += 1
        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception as exc:
            print(f"Warning: Could not read {path}: {exc}")
            continue

        for idx, line in enumerate(content.splitlines(), start=1):
            # Skip mock credential fixture lines in test conftest
            if "testing-mock-" in line:
                continue

            for pattern, desc in SECRET_PATTERNS:
                if pattern.search(line):
                    violations.append((path.relative_to(PROJECT_ROOT), idx, desc))

    print(f"Scanned {scanned_files} files.")

    if violations:
        print("\n[FAIL] SECURITY VULNERABILITIES DETECTED:")
        for file_path, line_no, desc in violations:
            print(f"  - [{desc}] at {file_path}:{line_no}")
        return 1
    else:
        print("[PASS] SECURITY AUDIT PASSED: Zero AWS secrets, access keys, or private keys found.")
        return 0


if __name__ == "__main__":
    sys.exit(audit_repository())
