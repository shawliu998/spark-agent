"""Run all opt-in real-provider autonomous task scenarios."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from real_task_scenarios import SCENARIOS


def main() -> None:
    if not os.environ.get("SPARK_LIVE_MODEL", "").strip() or not os.environ.get(
        "SPARK_LIVE_API_KEY", ""
    ).strip():
        print("SKIP: set SPARK_LIVE_MODEL and SPARK_LIVE_API_KEY to run the live regression batch")
        return
    smoke = Path(__file__).with_name("live_provider_smoke.py")
    for name in SCENARIOS:
        environment = dict(os.environ)
        environment["SPARK_LIVE_SCENARIO"] = name
        subprocess.run([sys.executable, "-B", str(smoke)], check=True, env=environment)


if __name__ == "__main__":
    main()
