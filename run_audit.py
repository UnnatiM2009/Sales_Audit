#!/usr/bin/env python3
"""Sales Process Audit — entry point.

    python run_audit.py --help
"""
import sys

from sales_audit.cli import main

if __name__ == "__main__":
    sys.exit(main())
