"""Exercise real multipart uploads, queue execution and controller responses."""
import copy
import json
import time
from .pipeline import Pipeline, configuration


def main():
    pipeline = Pipeline("output/photo-series-downloads-test")
    source = pipeline.get("downloads-n3")
    groups = sorted(source["series"], key=lambda s: -len(s["images"]))
    job = pipeline.get("downloads-remote-smoke") or dict(job_id="downloads-remote-smoke", folder=source["folder"], processed=source["processed"],
               total=source["total"], series=copy.deepcopy(groups[:3]), status="selected")
    pipeline.save(job)
    started = time.perf_counter()
    pipeline.run(job, configuration({}))
    result = dict(status=job["status"], seconds=round(time.perf_counter()-started, 2),
                  series=[dict(series_id=s["series_id"], status=s["status"], remote_job_id=s.get("remote_job_id"),
                               structured_result_valid=s.get("result") is not None, result=s.get("result"),
                               result_text=s.get("result_text")) for s in job["series"]])
    (pipeline.directory/"remote-smoke.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k:v for k,v in result.items() if k != "series"}), flush=True)
    print([(s["status"], s["structured_result_valid"]) for s in result["series"]], flush=True)


if __name__ == "__main__":
    main()
