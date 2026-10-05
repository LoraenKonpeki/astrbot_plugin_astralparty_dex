from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def asset(*parts):
    return ROOT.joinpath(*parts)
