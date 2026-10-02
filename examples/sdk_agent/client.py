"""Submit a document or reconnect to a saved run."""

import argparse
import asyncio
import json
from uuid import UUID, uuid4

from durable_agent import Client


async def main(args: argparse.Namespace) -> None:
    async with Client(args.url) as client:
        if args.run_id:
            run = await client.runs.get(args.run_id)
        else:
            submission_key = args.submission_key or uuid4()
            print(f"Submission key: {submission_key}", flush=True)
            run = await client.runs.start(
                agent="document_writer",
                input={"title": "Release notes", "body": "The SDK example is running."},
                submission_key=submission_key,
            )
        print(f"Run ID: {run.id}", flush=True)
        print(json.dumps(await run.result(timeout=60), indent=2))
        listing = await run.files.list()
        document = await run.files.read("document.md", version_id=listing.workspace_version_id)
        print(document.decode("utf-8"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://localhost:8000")
    identity = parser.add_mutually_exclusive_group()
    identity.add_argument("--run-id", type=UUID)
    identity.add_argument("--submission-key", type=UUID)
    asyncio.run(main(parser.parse_args()))
