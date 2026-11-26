"""Patch only the photo-series text completion guard; preserve other bridge behavior."""
import json
from pathlib import Path
import shutil
import sys

directory = Path(sys.argv[1])
path = directory/'content.js'
text = path.read_text(encoding='utf-8')
helper = '''function isCompletePhotoSeriesResponse(parsed) {
  const text = parsed?.text || '';
  const first = text.indexOf('{'), last = text.lastIndexOf('}');
  if (first < 0 || last <= first) return false;
  try {
    const value = JSON.parse(text.slice(first, last + 1));
    return typeof value.context === 'string' && Array.isArray(value.images) && value.images.length > 0;
  } catch { return false; }
}

'''
if 'function isCompletePhotoSeriesResponse' not in text:
    anchor = 'async function waitForAssistantResponse(turnContext = {}, timeoutMs = 240000, taskId = null) {'
    if text.count(anchor) != 1:
        raise RuntimeError('Text completion function anchor mismatch')
    text = text.replace(anchor, helper+anchor+'\n  const requireCompleteJson = turnContext.requireCompleteJson === true;')
    anchor = '        const isFinished = isAssistantTurnFinished(activeTurn, currentParsed);'
    text = text.replace(anchor, '''        if (requireCompleteJson && !isCompletePhotoSeriesResponse(currentParsed)) {
          stableChecks = 0;
          lastTextLength = currentLength;
          await new Promise(resolve => setTimeout(resolve, 800));
          continue;
        }
'''+anchor)
    text = text.replace('if (fallbackResp && (fallbackResp.ruby_code || fallbackResp.char_count > 150)) {',
                        'if (fallbackResp && (fallbackResp.ruby_code || fallbackResp.char_count > 150) && (!requireCompleteJson || isCompletePhotoSeriesResponse(fallbackResp))) {')
    text = text.replace('if (finalFallback && finalFallback.char_count > 50 && finalFallback.text !== initialLastText) {',
                        'if (finalFallback && finalFallback.char_count > 50 && finalFallback.text !== initialLastText && (!requireCompleteJson || isCompletePhotoSeriesResponse(finalFallback))) {')
    call = 'await waitForAssistantResponse(turnContext, 240000, task.id)'
    if text.count(call) != 1:
        raise RuntimeError('Text completion caller anchor mismatch')
    text = text.replace(call, "await waitForAssistantResponse({ ...turnContext, requireCompleteJson: String(task.prompt || '').includes('Schema: {context:string,images:') }, 240000, task.id)")
    backup = directory/'content.before-photo-json.js'
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(text, encoding='utf-8')
manifest = directory/'manifest.json'
data = json.loads(manifest.read_text(encoding='utf-8'))
if tuple(map(int, data['version'].split('.'))) < (5, 6, 3):
    data['version'] = '5.6.3'
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
print('Complete-JSON guard installed:', directory)
