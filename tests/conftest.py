"""Lets the tests in this folder import the project's modules (nba, gui, picks, …), which live one folder up."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
