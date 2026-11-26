import { useState } from 'react';

export interface PhotoSeriesFederationDocsProps {
  docsUrl: string;
  agentBaseUrl?: string;
  onClose?: () => void;
}

const VITE_CONFIG_SNIPPET = `// vite.config.js (Host App dùng Vite + React 18/19)
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import federation from '@originjs/vite-plugin-federation';

export default defineConfig({
  plugins: [
    react(),
    federation({
      name: 'my_host_app',
      remotes: {
        printagentx: 'https://dir.toolxprint.com/remoteEntry.js',
      },
      shared: ['react', 'react-dom'],
    }),
  ],
  build: {
    target: 'esnext',
  },
});`;

const VITE_REACT_SNIPPET = `import React, { Suspense } from 'react';

// Nạp động component Ảnh theo series từ dir.toolxprint.com
const PhotoSeries = React.lazy(() => import('printagentx/PhotoSeries'));

export default function PhotoFilterPage() {
  return (
    <Suspense fallback={<div style={{ padding: 32 }}>Đang nạp module Ảnh theo series…</div>}>
      <PhotoSeries
        agentBaseUrl="http://127.0.0.1:9173"
        defaultN={3}
        showDocsButton={false}
        syncUrlQuery={false}
        onJobComplete={(job) => {
          console.log('Đã nhóm xong:', job.series.length, 'nhóm');
        }}
        onExportComplete={(state) => {
          console.log('Đã chép ảnh đẹp vào:', state.folder);
        }}
      />
    </Suspense>
  );
}`;

const TS_DECL_SNIPPET = `// src/types/printagentx.d.ts
declare module 'printagentx/PhotoSeries' {
  import type { ComponentType } from 'react';
  export interface PhotoSeriesProps {
    defaultFolder?: string;
    agentBaseUrl?: string;
    defaultN?: number;
    showDocsButton?: boolean;
    initialShowDocs?: boolean;
    docsUrl?: string;
    storageKey?: string | null;
    syncUrlQuery?: boolean;
    embedded?: boolean;
    className?: string;
    onJobComplete?: (job: any) => void;
    onExportComplete?: (state: any) => void;
  }
  export const PhotoSeriesSection: ComponentType<PhotoSeriesProps>;
  export default PhotoSeriesSection;
}`;

const WEBPACK_CONFIG_SNIPPET = `// webpack.config.js (Host App dùng Webpack 5 / CRA)
const { ModuleFederationPlugin } = require('webpack').container;

module.exports = {
  plugins: [
    new ModuleFederationPlugin({
      name: 'my_webpack_host',
      remotes: {
        remote_photo_series:
          'remote_photo_series@https://toolxprint.com/modules/photo-series/remoteEntry.js',
      },
      shared: {
        react: { singleton: true },
        'react-dom': { singleton: true },
      },
    }),
  ],
};

// Sau đó trong React component:
// const PhotoSeries = React.lazy(() => import('remote_photo_series/PhotoSeries'));`;

const WEBPACK_RUNTIME_SNIPPET = `// Nạp runtime Webpack 5 MF không cần sửa webpack.config.js
import React, { lazy, Suspense } from 'react';
import * as ReactDOM from 'react-dom';

function loadPhotoSeriesRemote() {
  const URL = 'https://toolxprint.com/modules/photo-series/remoteEntry.js';
  return new Promise((resolve, reject) => {
    const existing = document.querySelector(\`script[src^="\${URL}"]\`);
    if (existing && window.remote_photo_series) return resolve();
    const s = document.createElement('script');
    s.src = \`\${URL}?_t=\${Date.now()}\`;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error('Không thể tải remoteEntry.js'));
    document.head.appendChild(s);
  }).then(async () => {
    const container = window.remote_photo_series;
    await container.init({
      react: { [React.version]: { get: () => Promise.resolve(() => React), loaded: 1, eager: true } },
      'react-dom': { [React.version]: { get: () => Promise.resolve(() => ReactDOM), loaded: 1, eager: true } },
    });
    const factory = await container.get('./PhotoSeries');
    const mod = factory();
    return { default: mod.default || mod.PhotoSeriesSection };
  });
}

const RemotePhotoSeries = lazy(loadPhotoSeriesRemote);

export function EmbeddedPhotoFilter() {
  return (
    <Suspense fallback={<div>Đang nạp Ảnh theo series…</div>}>
      <RemotePhotoSeries showDocsButton={false} syncUrlQuery={false} />
    </Suspense>
  );
}`;

const IFRAME_SNIPPET = `<!-- Nhúng trực tiếp vào HTML, PHP, Laravel, WordPress, Vue, Angular -->
<iframe
  src="https://dir.toolxprint.com/?view=photo-series&embed=1"
  title="Ảnh theo series"
  style="width: 100%; height: 820px; border: 1px solid #34435c; border-radius: 16px;"
  allow="clipboard-read; clipboard-write; local-network-access"
></iframe>`;

const AGENT_CORS_SNIPPET = `// %APPDATA%\\GoxPrintAgent\\settings.json (trên máy cài PrintAgent)
{
  "web": {
    "allowed_origins": [
      "https://your-website.com",
      "http://localhost:5173"
    ]
  }
}`;

function CodeBlock({
  id,
  title,
  hint,
  code,
  copiedId,
  onCopy,
}: {
  id: string;
  title: string;
  hint?: string;
  code: string;
  copiedId: string;
  onCopy: (id: string, code: string) => void;
}) {
  const copied = copiedId === id;
  return (
    <div className="ps-doc-card">
      <div className="ps-doc-card-head">
        <div>
          <h4>{title}</h4>
          {hint && <p className="ps-doc-hint">{hint}</p>}
        </div>
        <button
          type="button"
          className={`ps-doc-copy ${copied ? 'is-copied' : ''}`}
          onClick={() => onCopy(id, code)}
        >
          {copied ? '✓ Đã sao chép' : 'Sao chép'}
        </button>
      </div>
      <pre className="ps-doc-pre"><code>{code}</code></pre>
    </div>
  );
}

export function PhotoSeriesFederationDocs({
  docsUrl,
  agentBaseUrl = 'http://127.0.0.1:9173',
}: PhotoSeriesFederationDocsProps) {
  const [tab, setTab] = useState<'federation' | 'api'>('federation');
  const [copiedId, setCopiedId] = useState('');

  async function handleCopy(id: string, text: string) {
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const ta = document.createElement('textarea');
      ta.value = text;
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      ta.remove();
    }
    setCopiedId(id);
    window.setTimeout(() => setCopiedId((prev) => (prev === id ? '' : prev)), 2200);
  }

  return (
    <div className="ps-docs">
      <div className="ps-docs-bar">
        <div className="ps-docs-tabs" role="tablist" aria-label="Chọn loại tài liệu tích hợp">
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'federation'}
            className={`ps-docs-tab ${tab === 'federation' ? 'is-active' : ''}`}
            onClick={() => setTab('federation')}
          >
            ⚡ Module Federation &amp; Nhúng Web
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={tab === 'api'}
            className={`ps-docs-tab ${tab === 'api' ? 'is-active' : ''}`}
            onClick={() => setTab('api')}
          >
            📄 Tài liệu REST API đầy đủ
          </button>
        </div>
        <a
          className="ps-secondary ps-docs-open"
          href={docsUrl}
          target="_blank"
          rel="noopener noreferrer"
        >
          Mở tài liệu đầy đủ ↗
        </a>
      </div>

      {tab === 'api' ? (
        <iframe
          className="ps-docs-frame"
          src={docsUrl}
          title="Tài liệu tích hợp Ảnh theo series"
        />
      ) : (
        <div className="ps-fed-docs">
          <div className="ps-doc-hero">
            <span className="ps-doc-badge">Micro-Frontend · Module Federation</span>
            <h3>Tích hợp Ảnh theo series vào website khác</h3>
            <p>
              Nhúng trọn bộ giao diện quét thư mục, nhóm ảnh theo lần chụp, chọn ảnh đẹp nhất và xuất thư mục{' '}
              <code>selected</code> (EXIF / <code>.xmp</code>) trực tiếp vào website của bạn qua{' '}
              <strong>Module Federation</strong> (không cần build lại mã nguồn khi nâng cấp) hoặc{' '}
              <strong>Iframe</strong>. Toàn bộ xử lý ảnh gốc chạy cục bộ trên PrintAgent (
              <code>{agentBaseUrl}</code>).
            </p>
          </div>

          <div className="ps-doc-specs">
            <div className="ps-doc-spec">
              <div className="ps-doc-spec-tag is-vite">Khuyên dùng · Vite MF (ESM)</div>
              <dl>
                <dt>Remote Entry URL</dt>
                <dd><code>https://dir.toolxprint.com/remoteEntry.js</code></dd>
                <dt>Scope / Module</dt>
                <dd><code>printagentx</code> → <code>./PhotoSeries</code> <small>(alias: <code>./PhotoSeriesSection</code>)</small></dd>
              </dl>
              <button
                type="button"
                className={`ps-doc-copy ${copiedId === 'url-vite' ? 'is-copied' : ''}`}
                onClick={() => void handleCopy('url-vite', 'https://dir.toolxprint.com/remoteEntry.js')}
              >
                {copiedId === 'url-vite' ? '✓ Đã sao chép URL' : 'Sao chép URL'}
              </button>
            </div>

            <div className="ps-doc-spec">
              <div className="ps-doc-spec-tag is-webpack">Webpack 5 / CRA MF</div>
              <dl>
                <dt>Remote Entry URL</dt>
                <dd><code>https://toolxprint.com/modules/photo-series/remoteEntry.js</code></dd>
                <dt>Scope / Module</dt>
                <dd><code>remote_photo_series</code> → <code>./PhotoSeries</code> <small>(alias: <code>./PhotoSeriesSection</code>)</small></dd>
              </dl>
              <button
                type="button"
                className={`ps-doc-copy ${copiedId === 'url-webpack' ? 'is-copied' : ''}`}
                onClick={() => void handleCopy('url-webpack', 'https://toolxprint.com/modules/photo-series/remoteEntry.js')}
              >
                {copiedId === 'url-webpack' ? '✓ Đã sao chép URL' : 'Sao chép URL'}
              </button>
            </div>

            <div className="ps-doc-spec">
              <div className="ps-doc-spec-tag is-iframe">Iframe (Mọi nền tảng)</div>
              <dl>
                <dt>Embed URL (gọn, không thanh tab)</dt>
                <dd><code>https://dir.toolxprint.com/?view=photo-series&amp;embed=1</code></dd>
                <dt>Tùy chọn URL query</dt>
                <dd><code>&amp;agent_url=…</code> · <code>&amp;hide_docs=1</code> · <code>&amp;n=3</code></dd>
              </dl>
              <button
                type="button"
                className={`ps-doc-copy ${copiedId === 'url-iframe' ? 'is-copied' : ''}`}
                onClick={() => void handleCopy('url-iframe', 'https://dir.toolxprint.com/?view=photo-series&embed=1')}
              >
                {copiedId === 'url-iframe' ? '✓ Đã sao chép URL' : 'Sao chép URL'}
              </button>
            </div>
          </div>

          <div className="ps-doc-notice">
            <strong>Yêu cầu bắt buộc trên máy người dùng:</strong> Cài{' '}
            <a href="https://download.goxprint.com/printagentinstall.exe" target="_blank" rel="noopener noreferrer">
              PrintAgent Windows
            </a>{' '}
            (chạy nền tại <code>http://127.0.0.1:9173</code>). Với website nằm trên tên miền mới (khác{' '}
            <code>dir.toolxprint.com</code> / <code>mzpic.com</code> / <code>toolxprint.com</code>), cần khai báo
            origin của website vào <code>%APPDATA%\GoxPrintAgent\settings.json</code> (mục 4 bên dưới). Nếu nhúng qua
            Iframe của <code>dir.toolxprint.com</code> thì không cần thêm origin vì trang bên trong vẫn thuộc{' '}
            <code>dir.toolxprint.com</code>.
          </div>

          <CodeBlock
            id="vite-config"
            title="1A. Cấu hình Vite Host (vite.config.js)"
            hint="Cài plugin trước: npm install -D @originjs/vite-plugin-federation — dùng chung remoteEntry.js với ./FileExplorer."
            code={VITE_CONFIG_SNIPPET}
            copiedId={copiedId}
            onCopy={(id, code) => void handleCopy(id, code)}
          />

          <CodeBlock
            id="vite-react"
            title="1B. Nhúng component trong React (Vite Host)"
            hint="CSS của module tự động tách riêng (scoped dưới .photo-series-panel), không ảnh hưởng CSS của trang host."
            code={VITE_REACT_SNIPPET}
            copiedId={copiedId}
            onCopy={(id, code) => void handleCopy(id, code)}
          />

          <CodeBlock
            id="ts-decl"
            title="1C. Khai báo kiểu TypeScript (tùy chọn)"
            hint="Tạo tệp .d.ts nếu dự án host dùng TypeScript."
            code={TS_DECL_SNIPPET}
            copiedId={copiedId}
            onCopy={(id, code) => void handleCopy(id, code)}
          />

          <CodeBlock
            id="webpack-config"
            title="2A. Tích hợp với Webpack 5 / CRA (ModuleFederationPlugin)"
            hint="Dành cho các ứng dụng dùng Webpack 5 hoặc hệ sinh thái Toolx."
            code={WEBPACK_CONFIG_SNIPPET}
            copiedId={copiedId}
            onCopy={(id, code) => void handleCopy(id, code)}
          />

          <CodeBlock
            id="webpack-runtime"
            title="2B. Nạp runtime Webpack 5 MF (không cần sửa webpack.config.js)"
            hint="Tải script remoteEntry.js động lúc chạy và chia sẻ trực tiếp React của ứng dụng host."
            code={WEBPACK_RUNTIME_SNIPPET}
            copiedId={copiedId}
            onCopy={(id, code) => void handleCopy(id, code)}
          />

          <CodeBlock
            id="iframe"
            title="3. Nhúng nhanh bằng Iframe (HTML / PHP / WordPress / Vue / Angular)"
            hint="Tham số &embed=1 ẩn thanh điều hướng DirToolx, chỉ hiển thị gọn khung Ảnh theo series. Thêm &hide_docs=1 để ẩn nút Tích hợp web."
            code={IFRAME_SNIPPET}
            copiedId={copiedId}
            onCopy={(id, code) => void handleCopy(id, code)}
          />

          <div className="ps-doc-card">
            <div className="ps-doc-card-head">
              <div>
                <h4>4. Danh sách Props &amp; Callbacks của &lt;PhotoSeries /&gt;</h4>
                <p className="ps-doc-hint">Áp dụng giống nhau cho cả bản Vite MF và Webpack 5 MF.</p>
              </div>
            </div>
            <div className="ps-doc-table-wrap">
              <table className="ps-doc-table">
                <thead>
                  <tr>
                    <th>Prop</th>
                    <th>Kiểu / Mặc định</th>
                    <th>Ý nghĩa</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td><code>agentBaseUrl</code></td>
                    <td><code>string</code> · <code>"http://127.0.0.1:9173"</code></td>
                    <td>Địa chỉ PrintAgent cục bộ trên máy người dùng.</td>
                  </tr>
                  <tr>
                    <td><code>defaultFolder</code></td>
                    <td><code>string</code> · <code>""</code></td>
                    <td>Điền sẵn đường dẫn thư mục ảnh cần xử lý (tùy chọn).</td>
                  </tr>
                  <tr>
                    <td><code>defaultN</code></td>
                    <td><code>number</code> · <code>3</code></td>
                    <td>Số ảnh đẹp nhất mặc định mỗi nhóm (1–20).</td>
                  </tr>
                  <tr>
                    <td><code>showDocsButton</code></td>
                    <td><code>boolean</code> · <code>true</code></td>
                    <td>Ẩn/hiện nút <code>📖 Tích hợp web</code> ở góc phải tiêu đề.</td>
                  </tr>
                  <tr>
                    <td><code>initialShowDocs</code></td>
                    <td><code>boolean</code> · <code>false</code></td>
                    <td>Mở sẵn màn hình tài liệu tích hợp web khi vừa hiển thị.</td>
                  </tr>
                  <tr>
                    <td><code>syncUrlQuery</code></td>
                    <td><code>boolean</code> · <code>true</code></td>
                    <td>Đặt <code>false</code> khi nhúng vào app có router riêng để không ghi <code>?photo_job=</code> lên URL.</td>
                  </tr>
                  <tr>
                    <td><code>storageKey</code></td>
                    <td><code>string | null</code> · <code>"photo-series-v2-job"</code></td>
                    <td>Khóa lưu <code>job_id</code> gần nhất trong <code>localStorage</code>; truyền <code>null</code> để tắt.</td>
                  </tr>
                  <tr>
                    <td><code>embedded</code></td>
                    <td><code>boolean</code> · <code>false</code></td>
                    <td>Loại bỏ margin ngoài để khít khung chứa của trang host / iframe.</td>
                  </tr>
                  <tr>
                    <td><code>onJobComplete</code></td>
                    <td><code>(job) =&gt; void</code></td>
                    <td>Gọi khi lượt nhóm &amp; phân tích hoàn tất (<code>status === "completed"</code>).</td>
                  </tr>
                  <tr>
                    <td><code>onExportComplete</code></td>
                    <td><code>(state) =&gt; void</code></td>
                    <td>Gọi khi thao tác <b>Lọc tất cả ảnh</b> chép xong sang thư mục <code>selected</code>.</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>

          <CodeBlock
            id="agent-cors"
            title="5. Cho phép origin website kết nối PrintAgent local"
            hint="Bắt buộc khi nhúng qua Module Federation trên tên miền ngoài danh sách mặc định (origin phải khớp chính xác scheme + domain + port)."
            code={AGENT_CORS_SNIPPET}
            copiedId={copiedId}
            onCopy={(id, code) => void handleCopy(id, code)}
          />
        </div>
      )}
    </div>
  );
}

export default PhotoSeriesFederationDocs;
