#!/usr/bin/env python3
"""
Backward-compatible wrapper — delegates to the hirevia package.

All original CLI flags still work unchanged.
"""
from hirevia.cli import main

if __name__ == "__main__":
    main()
