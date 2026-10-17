"""
Checks that the local environment is ready for the workshop.

Run it from the backend folder with the virtual environment active:

    python verify.py
"""

import importlib
import os
import sys
from importlib import metadata
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

REQUIRED_PYTHON = (3, 14)
BLOCKED_PYTHON = {(3, 14, 1)}  # networkx (used in later phases) excludes 3.14.1

REQUIRED_PACKAGES = {
    "django": "Django",
    "rest_framework": "djangorestframework",
    "drf_spectacular": "drf-spectacular",
    "google.genai": "google-genai",
    "dotenv": "python-dotenv",
    "dj_database_url": "dj-database-url",
}

OK = "[ OK ]"
FAIL = "[FAIL]"
WARN = "[WARN]"


def check_python() -> bool:
    version = sys.version_info[:3]
    label = ".".join(map(str, version))
    if version[:2] != REQUIRED_PYTHON:
        print(f"{FAIL} Python {label} (expected {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}.x)")
        return False
    if version in BLOCKED_PYTHON:
        print(f"{FAIL} Python {label} is not supported, install a newer 3.14 patch")
        return False
    print(f"{OK} Python {label}")
    return True


def check_venv() -> bool:
    if sys.prefix == sys.base_prefix:
        print(f"{WARN} Not running inside a virtual environment")
        return True
    print(f"{OK} Virtual environment: {sys.prefix}")
    return True


def check_packages() -> bool:
    all_ok = True
    for module, distribution in REQUIRED_PACKAGES.items():
        try:
            importlib.import_module(module)
            print(f"{OK} {distribution} {metadata.version(distribution)}")
        except ImportError:
            print(f"{FAIL} {distribution} is missing, run: pip install -r requirements.txt")
            all_ok = False
    return all_ok


def check_env_file() -> bool:
    if (BASE_DIR / ".env").exists():
        print(f"{OK} .env found")
    else:
        print(f"{WARN} .env not found, copy .env.example to .env")
    return True


def check_django() -> bool:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        import django
        from django.conf import settings
        from django.core.management import call_command
        from django.db import connection

        django.setup()
        call_command("check", verbosity=0)
        connection.ensure_connection()
        print(f"{OK} Django check passed, database: {connection.vendor}")
    except Exception as error:  # noqa: BLE001 - report any setup problem
        print(f"{FAIL} Django setup failed: {error}")
        return False

    provider = settings.LLM_PROVIDER
    if provider == "gemma" and not settings.GEMINI_API_KEY:
        print(f"{FAIL} LLM_PROVIDER=gemma but GEMINI_API_KEY is empty")
        return False
    print(f"{OK} LLM provider: {provider} ({settings.LLM_MODEL})")
    return True


def main() -> int:
    print("Checking workshop environment...\n")
    results = [
        check_python(),
        check_venv(),
        check_packages(),
        check_env_file(),
    ]
    if all(results):
        results.append(check_django())

    print()
    if all(results):
        print("All good, you are ready for the workshop.")
        return 0
    print("Fix the items marked [FAIL] and run this script again.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
