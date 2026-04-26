"""POC: concurrent YouTube -> MP3 pipeline.

Validates the core hypothesis:
  yt-dlp downloads audio in parallel, then converts each to MP3 with ffmpeg.

This is an isolated POC. The production app will live elsewhere and follow
hexagonal architecture; this file is intentionally a single flat script.

Usage:
    python poc.py URL [URL ...]
    python poc.py --concurrency 3 URL [URL ...]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass
from pathlib import Path

OUTPUT_DIR = Path(__file__).parent / "downloads"
DEFAULT_CONCURRENCY = 3
MP3_BITRATE = "128k"


@dataclass
class Result:
    url: str
    ok: bool
    path: Path | None
    error: str | None
    elapsed_s: float


async def fetch_one(url: str, sem: asyncio.Semaphore, idx: int, total: int) -> Result:
    async with sem:
        started = time.monotonic()
        prefix = f"[{idx}/{total}]"
        print(f"{prefix} START {url}", flush=True)

        out_template = str(OUTPUT_DIR / "%(id)s.%(ext)s")
        cmd = [
            "yt-dlp",
            "-x",
            "--audio-format", "mp3",
            "--audio-quality", MP3_BITRATE,
            "-o", out_template,
            "--no-playlist",
            "--print", "after_move:filepath",
            "--no-progress",
            url,
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        elapsed = time.monotonic() - started

        if proc.returncode != 0:
            tail = stderr.decode(errors="replace").strip().splitlines()[-1:] or ["unknown error"]
            print(f"{prefix} FAIL {url} ({elapsed:.1f}s): {tail[0]}", flush=True)
            return Result(url, False, None, tail[0], elapsed)

        path_str = stdout.decode().strip().splitlines()[-1] if stdout else ""
        path = Path(path_str) if path_str else None
        size_mb = (path.stat().st_size / 1_048_576) if path and path.exists() else 0.0
        print(f"{prefix} DONE  {path.name if path else '?'} ({size_mb:.1f} MB, {elapsed:.1f}s)", flush=True)
        return Result(url, True, path, None, elapsed)


async def run(urls: list[str], concurrency: int) -> list[Result]:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(concurrency)
    tasks = [fetch_one(u, sem, i + 1, len(urls)) for i, u in enumerate(urls)]
    return await asyncio.gather(*tasks)


def main() -> int:
    parser = argparse.ArgumentParser(description="POC: concurrent YouTube -> MP3")
    parser.add_argument("urls", nargs="+", help="YouTube URLs")
    parser.add_argument("-c", "--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    args = parser.parse_args()

    overall = time.monotonic()
    results = asyncio.run(run(args.urls, args.concurrency))
    total = time.monotonic() - overall

    ok = sum(1 for r in results if r.ok)
    print(f"\n=== {ok}/{len(results)} succeeded in {total:.1f}s "
          f"(concurrency={args.concurrency}) ===")
    for r in results:
        if not r.ok:
            print(f"  FAIL {r.url}: {r.error}")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
