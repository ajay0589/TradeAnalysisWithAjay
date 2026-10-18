"""Replay a shared Expiry Lab ZIP/JSON offline; no broker connection or Telegram."""
import argparse
import json
from pathlib import Path
from zipfile import ZipFile

from trading_analysis.expiry.replay import replay_frames


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive",type=Path)
    args=parser.parse_args()
    if args.archive.suffix.lower()==".zip":
        with ZipFile(args.archive) as archive:
            names=[n for n in archive.namelist() if n.endswith(".json")]
            if len(names)!=1:raise ValueError("Expected one Expiry Lab JSON report")
            report=json.loads(archive.read(names[0]))
    else:report=json.loads(args.archive.read_text(encoding="utf-8"))
    result=replay_frames(report.get("frames",[]))
    print(json.dumps({k:v for k,v in result.items() if k!="trades"},indent=2))


if __name__=="__main__":main()
