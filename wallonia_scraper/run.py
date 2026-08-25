import sys

from .pipeline import run

if __name__ == "__main__":
    run(resume="--resume" in sys.argv)
