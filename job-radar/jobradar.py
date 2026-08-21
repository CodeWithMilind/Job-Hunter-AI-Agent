#!/usr/bin/env python3
"""
Backward-compatible wrapper — delegates to the jobradar package.

All original CLI flags still work unchanged.
"""
from jobradar.cli import main

if __name__ == "__main__":
    main()
