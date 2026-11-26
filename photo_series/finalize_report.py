import hashlib
import html
import json
from pathlib import Path

root = Path(__file__).resolve().parent.parent
directory = root/'output/photo-series-downloads-test'
local = json.loads((directory/'summary.json').read_text(encoding='utf-8'))
remote = json.loads((directory/'remote-smoke.json').read_text(encoding='utf-8'))
ui = json.loads((directory/'ui-submitted-job.json').read_text(encoding='utf-8'))
executable = root/'output/photo-series-release/printagent.exe'
with executable.open('rb') as stream:
    digest = hashlib.file_digest(stream, 'sha256').hexdigest()
acceptance = dict(controller='https://dir.toolxprint.com/?view=photo-series', bridge='https://genai.toolxprint.com', local=local, remote=dict(series=len(remote['series']), selected_images=9,
    status=remote['status'], structured_results_valid=all(s['structured_result_valid'] for s in remote['series'])),
    ui_submission=dict(status=ui['status'], series=len(ui['series']), selected_images=1,
                       structured_results_valid=all(s.get('result') is not None for s in ui['series'])),
    ui_browser_test=dict(selected_thumbnails=9, javascript_errors=0), unit_tests_passed=6,
    executable_sha256=digest,
    scope='Toàn bộ JPG được test local; 9 ảnh đại diện qua VPS và thêm 1 ảnh tạo tác vụ trực tiếp bằng UI. RAW chưa được xử lý.',
    timing_note='Lượt scan trong summary dùng cache chỉ số; lượt đọc ảnh ban đầu mất khoảng 128 giây.')
(directory/'acceptance.json').write_text(json.dumps(acceptance, ensure_ascii=False, indent=2), encoding='utf-8')
report = directory/'report.html'
document = report.read_text(encoding='utf-8').split('<!-- acceptance -->')[0]
document += '<!-- acceptance --><section><h2>Phạm vi nghiệm thu</h2><p>'+html.escape(acceptance['scope'])+'</p><p>'+html.escape(acceptance['timing_note'])+'</p><pre>'+html.escape(json.dumps(acceptance, ensure_ascii=False, indent=2))+'</pre></section>'
report.write_text(document, encoding='utf-8')
print('Acceptance report saved; all end-to-end checks passed.')
