"""Run with: python -m photo_series.main scan D:\\Photos --config config.json"""
import argparse
import json
import sys
import uuid
from pathlib import Path
from .pipeline import Pipeline, configuration


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["scan", "process", "upload", "retry", "status", "preview"])
    parser.add_argument("target", help="Folder for scan, job ID for other commands")
    parser.add_argument("--config", default=str(Path(__file__).with_name("config.json")))
    parser.add_argument("--output", default="output/photo-series")
    args = parser.parse_args()
    cfg = configuration(json.loads(Path(args.config).read_text(encoding="utf-8")))
    pipeline = Pipeline(args.output)
    if args.command == "scan":
        job = dict(job_id=uuid.uuid4().hex, folder=str(Path(args.target).resolve()), series=[])
        pipeline.scan(job, cfg)
    else:
        job = pipeline.get(args.target)
        if not job:
            parser.error("Không tìm thấy tác vụ")
        if args.command == "preview":
            for group in job["series"]:
                pipeline.prepare(job, group, cfg)
            pipeline.save(job)
        elif args.command != "status":
            pipeline.run(job, cfg)
    print(json.dumps(job, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
