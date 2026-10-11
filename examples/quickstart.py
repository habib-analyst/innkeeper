"""innkeeper quickstart: init a guest house, run a full guest journey in ~30s.

Usage: python examples/quickstart.py
"""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.demo import run_demo, SCRIPT
from innkeeper.cli import main

tmp = Path(tempfile.mkdtemp(prefix="innkeeper_qs_"))
main(["init", "quickstart-house", "--directory", str(tmp),
      "--owner-phone", "+929990000000",
      "--easypaisa", "0345-1111111",
      "--review-link", "https://g.page/quickstart/review"])
print("\n" + "=" * 60)
print("SIMULATED WHATSAPP CONVERSATION")
print("=" * 60 + "\n")
run_demo(str(tmp / "quickstart-house.db"))
shutil.rmtree(tmp, ignore_errors=True)
print("\nDone. Every action above is journaled and reversible — try the eval: python eval/run_eval.py")
