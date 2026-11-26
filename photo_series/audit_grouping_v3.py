"""Regroup the entire real folder locally; compare the exact reported problem frames."""
import json
import time
from .pipeline import Pipeline, configuration
from pathlib import Path

p = Pipeline('output/photo-series-v3-test')
job = dict(job_id='downloads-regroup-v3', folder=str(Path.home()/'Downloads/1'), series=[])
started = time.perf_counter()
p.scan(job, configuration({}))
examples = ['DSC04234.JPG', 'DSC04236.JPG', 'DSC04240.JPG', 'DSC04242.JPG', 'DSC04245.JPG', 'DSC04247.JPG', 'DSC04359.JPG', 'DSC04362.JPG', 'DSC04363.JPG']
mapping = {name: next(index+1 for index, group in enumerate(job['series']) if any(image['filename'] == name for image in group['images'])) for name in examples}
assert len({mapping[name] for name in examples[:6]}) == 1, mapping
assert len({mapping[name] for name in examples[6:]}) == 1, mapping
assert mapping[examples[0]] != mapping[examples[-1]], mapping
summary = dict(files=job['total_files'], captures=job['total'], previous_groups=253, new_groups=len(job['series']),
               errors=job['errors'], seconds=round(time.perf_counter()-started, 2), reported_examples=mapping,
               cadence_seconds=job['grouping']['cadence_seconds'], inferred_gap_seconds=job['grouping']['inferred_gap_seconds'])
(p.directory/'grouping-audit.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False), flush=True)
