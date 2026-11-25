"""Write the API tests' synthetic gold fixture for the image smoke: smoke_api_seed.py <dir>."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

import api_gold  # noqa: E402

if __name__ == "__main__":
    print(api_gold.build(Path(sys.argv[1])))
