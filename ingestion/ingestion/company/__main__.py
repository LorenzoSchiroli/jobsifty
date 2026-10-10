import logging
import sys
import time
from dotenv import load_dotenv

from ingestion.company.company import enrich
from ingestion.llm import LLMPool

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
    llm = LLMPool.from_env()
    if not llm.configured():
        print("No LLM provider key set: deterministic enrichment only (no LLM).", file=sys.stderr)
    t0 = time.perf_counter()
    profile, timings = enrich(name, location, llm if llm.configured() else None)
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
