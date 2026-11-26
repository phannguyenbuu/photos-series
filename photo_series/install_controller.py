"""Install the photo controller into DirToolx, leaving GenAI as a bridge only."""
from pathlib import Path
import shutil


def install_controller():
    source = Path(__file__).parent
    root = source.parent.parent
    explorer = root/'Goxprint/auto-gox-react/src/components/FileExplorer'
    path = explorer/'index.jsx'
    text = path.read_text(encoding='utf-8')
    if "import { PhotoSeriesSection }" not in text:
        text = "import { PhotoSeriesSection } from './components/PhotoSeriesSection';\nimport { getLocalAgentBaseUrl } from '../../services/api';\n" + text
    text = text.replace("get('view') === 'presets' ? 'presets' : 'files'", "get('view') === 'photo-series' ? 'photo-series' : new URLSearchParams(window.location.search).get('view') === 'presets' ? 'presets' : 'files'")
    text = text.replace("if (v === 'presets') url.searchParams.set('view', 'presets');", "if (v === 'presets' || v === 'photo-series') url.searchParams.set('view', v);")
    text = text.replace("fe-window ${mainView === 'presets'", "fe-window ${mainView === 'photo-series' ? 'fe-window--photo-series' : ''} ${mainView === 'presets'")
    button = '''              <button type="button" role="tab" aria-selected={mainView === 'photo-series'}
                className={`fe-view-btn fe-main-tab ${mainView === 'photo-series' ? 'active' : ''}`} onClick={() => switchMainView('photo-series')}>
                <Sparkles style={{ width: '14px', height: '14px' }} /> Ảnh theo series
              </button>
'''
    if "aria-selected={mainView === 'photo-series'}" not in text:
        anchor = '            </div>\n            <PhotoshopDocuments />'
        if text.count(anchor) != 1:
            raise RuntimeError('DirToolx tab anchor mismatch')
        text = text.replace(anchor, button+anchor)
    anchor = "        {mainView === 'presets' ? ("
    replacement = '''        {mainView === 'photo-series' ? (
          selectedAgent === 'local' ? (
            <PhotoSeriesSection key={selectedAgent} initialFolder={currentPath} agentBaseUrl={getLocalAgentBaseUrl()} />
          ) : (
            <section className="photo-series-panel"><h2>Ảnh theo series</h2><p>Chọn máy cục bộ để chạy quy trình ảnh bằng PrintAgent trên máy này. Tác vụ sẽ không tự chuyển sang máy khác.</p></section>
          )
        ) : mainView === 'presets' ? ('''
    if replacement not in text:
        if text.count(anchor) != 1:
            raise RuntimeError('DirToolx content anchor mismatch')
        text = text.replace(anchor, replacement)
    backup = path.with_name('index.jsx.before-photo-series')
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(text, encoding='utf-8')
    for name in ('PhotoSeriesSection.tsx', 'PhotoSeriesFederationDocs.tsx', 'PhotoSeriesSection.css', 'face-mark.png'):
        shutil.copy2(source/name, explorer/'components'/name)
    genai = root/'GenImgs/src/App.tsx'
    text = genai.read_text(encoding='utf-8')
    text = text.replace("import { PhotoSeriesSection } from './components/PhotoSeriesSection';\n", '')
    text = text.replace('          <PhotoSeriesSection />\n', '')
    genai.write_text(text, encoding='utf-8')
    print('Controller installed in DirToolx File Explorer; removed from GenAI Retouch.')


if __name__ == '__main__':
    install_controller()
