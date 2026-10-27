"""Run a deterministic, disjoint share of unittest discovery (one-based shards)."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import unittest


def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item


def main() -> int:
    # Match `python -m unittest`: helpers such as experiments/ are importable
    # from the working directory even though this entry point lives in ci/.
    sys.path.insert(0, str(Path.cwd()))
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start-directory', type=Path, default=Path('tests/application_companion'))
    parser.add_argument('--top-level-directory', type=Path)
    parser.add_argument('--shard', type=int, required=True)
    parser.add_argument('--shards', type=int, required=True)
    args = parser.parse_args()
    if not 1 <= args.shard <= args.shards:
        parser.error('--shard must be between 1 and --shards')

    loader = unittest.TestLoader()
    suite = loader.discover(str(args.start_directory), top_level_dir=(
        str(args.top_level_directory) if args.top_level_directory else None))
    if loader.errors:
        print('\n'.join(loader.errors), file=sys.stderr)
        return 1
    # Adjacent tests stay ordered by module/class so class fixtures remain shared.
    tests = sorted(flatten(suite), key=lambda test: test.id())
    selected = tests[args.shard - 1::args.shards]
    if not selected:
        parser.error('shard contains no tests')
    print(f'Shard {args.shard}/{args.shards}: {len(selected)}/{len(tests)} tests', flush=True)
    return 0 if unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(selected)).wasSuccessful() else 1


if __name__ == '__main__':
    sys.exit(main())
