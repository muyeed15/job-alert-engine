#!/usr/bin/env python3
"""Entry point: ``python main.py --test-fetch`` or ``python -m job_notifier --test-fetch``."""

from job_notifier.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
