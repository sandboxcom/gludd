import sys, tomllib
from general_ludd import __version__
def main():
    with open("pyproject.toml", "rb") as f:
        t = tomllib.load(f)["project"]["version"]
    if __version__ != t:
        print(f"MISMATCH: __init__.py={__version__} pyproject.toml={t}")
        return 1
    print(f"OK: {__version__}")
    return 0
if __name__ == "__main__":
    sys.exit(main())
