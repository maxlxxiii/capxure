# capxure

Python library for capturing GitHub repository metadata and README files locally.

## Install

```
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Usage

`capxure` is a pure library — there is no CLI or console script. Your consumer code is responsible for obtaining a GitHub personal-access token (e.g., via `python-dotenv`, your shell environment, or a secrets manager) and passing it to `GitHubClient`.

```python
import asyncio
import os

from capxure import GitHubClient, Storage, process_repo, Severity


async def main() -> None:
    storage = Storage()
    storage.ensure_directories()

    def log(message: str, severity: Severity) -> None:
        print(f"[{severity}] {message}")

    async with GitHubClient(os.environ["GITHUB_TOKEN"]) as gh:
        await process_repo(
            "https://github.com/owner/repo",
            github=gh,
            storage=storage,
            on_status=log,
        )


asyncio.run(main())
```

Captured metadata goes to `data/metadata.json`; README files go to `data/readmes/{owner}--{repo}.md`. Pass a custom `Path` to `Storage(data_dir=...)` to change the location.
