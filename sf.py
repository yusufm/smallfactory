#!/usr/bin/env python3
"""
smallfactory CLI entrypoint (sf.py)

Minimal, portable, zero-infrastructure PLM management.

This file delegates to the smallfactory CLI layer.
"""
from smallfactory.cli.sf_cli import main

if __name__ == "__main__":
    main()
