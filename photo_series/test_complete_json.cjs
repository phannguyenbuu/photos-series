const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('D:/Dropbox/_Documents/GenImgs/chatgpt_unified_bridge/content.js', 'utf8');
const start = source.indexOf('function isCompletePhotoSeriesResponse');
const end = source.indexOf('// --------------------------------------------------------------------------', source.indexOf('async function waitForAssistantResponse', start));
const code = source.slice(start, end);
const partial = 'JSON{"context":"'+ 'x'.repeat(180);
const complete = 'JSON'+JSON.stringify({ context: 'scene', images: [{ image_id: 'one' }] });
async function run(requireCompleteJson) {
  let phase = 0, clock = 0;
  const current = () => ({ text: phase < 3 ? partial : complete, char_count: phase < 3 ? partial.length : complete.length, code_block_count: 0 });
  const turn = { querySelector: () => null };
  const context = vm.createContext({ console: { log() {} }, Date: { now: () => clock },
    updateWidgetText() {}, assertAssignmentActive() {}, getAssistantTurns: () => [turn],
    parseAssistantTurn: current, isChatGptGenerating: () => false, isAssistantTurnFinished: () => false,
    extractLatestAssistantResponse: current, setTimeout: callback => { phase++; clock += 12000; callback(); } });
  vm.runInContext(code, context);
  return context.waitForAssistantResponse({ requireCompleteJson }, 240000);
}
(async () => {
  assert.equal((await run(true)).text, complete, 'Photo-series must not return partial JSON during a pause');
  assert.equal((await run(false)).text, partial, 'Unrelated text workflows retain their current completion behavior');
  console.log('Complete-JSON guard passed: stream pauses cannot truncate photo-series responses.');
})().catch(error => { console.error(error); process.exit(1); });
