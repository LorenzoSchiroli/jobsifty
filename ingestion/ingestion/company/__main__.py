import logging
import os
import sys
import time
from dotenv import load_dotenv
from groq import Groq

from ingestion.company.company import enrich

load_dotenv(".env.public")
load_dotenv(".env", override=True)

def main():
    debug = "--debug" in sys.argv
    args = [a for a in sys.argv[1:] if a != "--debug"]

    logging.basicConfig(level=logging.DEBUG if debug else logging.WARNING)
    for _noisy in ("httpx", "httpcore"):
        logging.getLogger(_noisy).setLevel(logging.ERROR)

    if not args:
        print("Usage: python -m company \"<company name>\" [location] [--debug]")
        sys.exit(1)
    name = args[0]
    location = args[1] if len(args) > 1 else ""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("GROQ_API_KEY is not set: deterministic enrichment only (no LLM).", file=sys.stderr)
    client = Groq(api_key=api_key) if api_key else None
    t0 = time.perf_counter()
    profile, timings = enrich(name, location, client)
    elapsed = time.perf_counter() - t0

    for field in vars(profile):
        print(f"{field}: {getattr(profile, field)}")

    print()
    print("timing")
    for label, secs in timings:
        print(f"  {label:<30} {secs:.2f}s")
    print(f"  {'total':<30} {elapsed:.2f}s")


if __name__ == "__main__":
    main()
