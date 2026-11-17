"""Fail closed before publishing a small allowlisted S07 evidence directory."""
import argparse
import re
from pathlib import Path


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence',type=Path);parser.add_argument('secrets',type=Path)
    args=parser.parse_args()
    assert args.evidence.is_dir() and args.secrets.is_dir(), 'Missing evidence or credential directory'
    secrets=[p.read_bytes().strip() for p in args.secrets.iterdir() if p.is_file()]
    assert secrets and all(len(value)>=32 for value in secrets), 'Missing temporary credentials'
    paths=list(args.evidence.rglob('*'));files=0
    for path in paths:
        assert not path.is_symlink(), 'Symlinks are forbidden in evidence'
        if not path.is_file():continue
        assert path.suffix in ('.json','.jsonl','.png','.log','.txt','.exit'), 'Unexpected artifact type'
        raw=path.read_bytes();assert len(raw)<=8*1024**2, 'Unexpected artifact size'
        assert not any(value in raw for value in secrets), 'Credential found: artifact upload blocked'
        assert not re.search(rb'-----BEGIN .*PRIVATE KEY-----|Test-only-[0-9a-f]{32}|"cookies"\s*:',raw), 'Unsafe artifact content'
        files+=1
    assert files, 'No evidence was produced; do not report a skipped check as PASS'
    print(f'PASS: {files} bounded, allowlisted evidence files; no known test credentials')


if __name__=='__main__':main()
