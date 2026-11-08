#!/usr/bin/env python3
"""Administrative CLI entry point: ``python manage.py <command>``."""

from job_notifier.manage import main

if __name__ == "__main__":
    raise SystemExit(main())
