# Photos Series — Lọc ảnh trong thư mục qua PrintAgent & Module Federation

Module quét đệ quy thư mục ảnh (JPEG, RAW, ARW) trên máy người dùng qua **PrintAgent cục bộ** (`http://127.0.0.1:9173`), tự động nhóm ảnh cùng lần chụp (EXIF + ORB/SIFT RANSAC), tách nhóm đổi người, chấm điểm kỹ thuật, lấy ngữ cảnh từ 1 ảnh đại diện mỗi nhóm và xuất các ảnh đẹp nhất sang thư mục `selected` (ghi ngữ cảnh vào EXIF JPEG hoặc sidecar `.xmp` cho RAW, giữ nguyên ảnh gốc).

---

## 1. Tích hợp Web (Module Federation · Iframe · REST API)

Tài liệu tích hợp đầy đủ nằm tại [`docs/index.html`](./docs/index.html) (bản online: https://dir.toolxprint.com/photo-series/index.html) và được đặt trực tiếp ngay trong nút **`📖 Tích hợp web`** của component [`PhotoSeriesSection.tsx`](./photo_series/PhotoSeriesSection.tsx).

| Hình thức | Remote Entry / URL | Scope / Module |
| :--- | :--- | :--- |
| **Vite Module Federation (ESM — khuyên dùng)** | `https://dir.toolxprint.com/remoteEntry.js` | `printagentx` → `./PhotoSeries` *(alias `./PhotoSeriesSection`)* |
| **Webpack 5 / CRA Module Federation** | `https://toolxprint.com/modules/photo-series/remoteEntry.js` | `remote_photo_series` → `./PhotoSeries` *(alias `./PhotoSeriesSection`)* |
| **Iframe nhúng gọn (HTML/PHP/Vue/Angular)** | `https://dir.toolxprint.com/?view=photo-series&embed=1` | Hỗ trợ `&hide_docs=1`, `&n=3`, `&docs=1`, `&agent_url=...` |
| **PrintAgent REST API cục bộ** | `http://127.0.0.1:9173/api/photo-series/*` | `GET /health`, `POST /pick-folder`, `POST /jobs`, `GET /jobs/:id`, `POST /jobs/:id/export` |

### Ví dụ nhanh (Vite Host)

```js
// vite.config.js
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
  build: { target: 'esnext' },
});
```

```tsx
import React, { Suspense } from 'react';

const PhotoSeries = React.lazy(() => import('printagentx/PhotoSeries'));

export default function PhotoFilterPage() {
  return (
    <Suspense fallback={<div>Đang nạp Ảnh theo series…</div>}>
      <PhotoSeries
        agentBaseUrl="http://127.0.0.1:9173"
        defaultN={3}
        showDocsButton={false}
        syncUrlQuery={false}
        onJobComplete={(job) => console.log('Hoàn tất:', job.series.length)}
        onExportComplete={(state) => console.log('Đã xuất vào:', state.folder)}
      />
    </Suspense>
  );
}
```

> **Lưu ý CORS PrintAgent:** Khi nhúng qua Module Federation trên tên miền ngoài danh sách mặc định, thêm origin website vào `%APPDATA%\GoxPrintAgent\settings.json` (`web.allowed_origins`).

---

## 2. Cấu trúc thư mục

- `photo_series/pipeline.py` — Quét thư mục, gộp cặp JPEG+RAW, chấm điểm chất lượng, chọn top `N`, chuẩn bị ảnh đại diện và điều phối job.
- `photo_series/grouping.py` — Nhóm cảnh theo nhịp chụp EXIF + histogram màu + ORB/SIFT RANSAC.
- `photo_series/image_source.py` — Đọc JPEG/PNG/TIFF và ảnh RAW (`rawpy` + `exifread`), chuẩn hóa hướng xoay EXIF.
- `photo_series/people_split.py` — Tách nhóm trùng phông nền nhưng khác người.
- `photo_series/export.py` — Chép 1–3 ảnh đẹp nhất mỗi nhóm vào thư mục con `selected` + ghi EXIF / `.xmp`.
- `photo_series/local_routes.py` — Flask routes loopback cho PrintAgent (`127.0.0.1:9173`).
- `photo_series/bridge_routes.py`, `photo_series/grid_batch.py` — Adapter phía máy chủ đóng gói lưới ảnh (`4×4` ngữ cảnh, `5×5` tách người).
- `photo_series/PhotoSeriesSection.tsx`, `PhotoSeriesFederationDocs.tsx`, `PhotoSeriesSection.css` — Giao diện React + tài liệu tích hợp web đặt sẵn trên UI.
- `docs/index.html` — Trang tài liệu tĩnh tích hợp web đầy đủ.
- `federation/` — Mẫu cấu hình Vite MF, Webpack 5 MF và file khai báo TypeScript `.d.ts`.

---

## 3. Chạy CLI & Kiểm thử cục bộ

```bash
pip install -r requirements.txt

# Chạy bộ unit test đầy đủ
python -m unittest photo_series.test_pipeline -v

# Chạy CLI quét & xử lý thư mục ảnh
python -m photo_series.main scan "D:\Photos"
python -m photo_series.main status <JOB_ID>
```

Xem thêm chi tiết kỹ thuật vận hành trong [`photo_series/README.md`](./photo_series/README.md).
