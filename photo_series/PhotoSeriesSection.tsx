import { useEffect, useRef, useState, type FormEvent, type CSSProperties } from 'react';
import './PhotoSeriesSection.css';
import faceMark from './face-mark.png';
import { PhotoSeriesFederationDocs } from './PhotoSeriesFederationDocs';

export type PhotoSeriesImage = {
  image_id: string;
  filename: string;
  original_width?: number;
  original_height?: number;
  score: number;
  rank: number;
  selected: boolean;
  warnings: string[];
  thumbnail_ready?: boolean;
  upload_bytes?: number;
  formats?: string[];
  analysis_representative?: boolean;
};

export type PhotoSeriesGroup = {
  series_id: string;
  status: string;
  images: PhotoSeriesImage[];
  representative_image_id?: string;
  capture_time_start?: string;
  capture_time_end?: string;
  result?: { context: string; images?: { image_id: string; faces?: number[][] }[] };
  result_text?: string;
};

export type PhotoSeriesExportState = {
  status: 'running' | 'completed' | 'failed';
  count: number;
  done: number;
  total: number;
  folder?: string;
  images?: number;
  files?: number;
  exif?: number;
  sidecars?: number;
  without_context?: number;
  errors?: unknown[];
  error?: string;
};

export type PhotoSeriesJob = {
  job_id: string;
  folder?: string;
  status: string;
  processed: number;
  total: number;
  total_files?: number;
  running: boolean;
  error?: string;
  series: PhotoSeriesGroup[];
  is_sample?: boolean;
  source_series_total?: number;
  pipeline_version?: number;
  elapsed_seconds?: number;
  cancel_requested?: boolean;
  remote_cancel_pending?: boolean;
  replacement_job_id?: string;
  people_split?: { checked?: number; split?: number; added?: number };
  errors?: { filename: string; error: string }[];
};

export interface PhotoSeriesProps {
  /** Kept for backward compatibility with FileExplorer host */
  initialFolder?: string;
  /** Optional folder path pre-filled in the control bar */
  defaultFolder?: string;
  /** Local PrintAgent base URL (default: http://127.0.0.1:9173) */
  agentBaseUrl?: string;
  /** Default number of best images per group (1..20, default: 3) */
  defaultN?: number;
  /** Show or hide the built-in Web Integration Docs button (default: true) */
  showDocsButton?: boolean;
  /** Open the Web Integration Docs view initially (default: false) */
  initialShowDocs?: boolean;
  /** Static HTML docs URL override */
  docsUrl?: string;
  /** localStorage key for last job id; pass null to disable persistence */
  storageKey?: string | null;
  /** Sync ?photo_job= query param in window.location (default: true) */
  syncUrlQuery?: boolean;
  /** Remove outer margin when embedded inside an iframe or host container */
  embedded?: boolean;
  /** Extra CSS class name for root section */
  className?: string;
  /** Called once when a job transitions to completed */
  onJobComplete?: (job: PhotoSeriesJob) => void;
  /** Called when exporting best photos to `selected` completes */
  onExportComplete?: (state: PhotoSeriesExportState) => void;
}

type Photo = PhotoSeriesImage;
type Series = PhotoSeriesGroup;
type ExportState = PhotoSeriesExportState;
type Job = PhotoSeriesJob;

const labels: Record<string, string> = {
  pending: 'Đang chuẩn bị',
  scanning: 'Đang nhóm và chọn ảnh',
  splitting: 'Đang tách nhóm theo người',
  selected: 'Đã chọn ảnh',
  preparing: 'Đang chuẩn bị ảnh',
  uploading: 'Đang gửi ảnh đại diện',
  analyzing: 'Đang lấy ngữ cảnh',
  queued: 'Đang chờ xử lý',
  assigned: 'Đã nhận tác vụ',
  processing: 'Đang lấy ngữ cảnh',
  completed: 'Đã có ngữ cảnh',
  failed: 'Cần thử lại',
};
const warnings: Record<string, string> = {
  suspected_color_outlier: 'Nghi lệch màu',
  missing_capture_time: 'Thiếu giờ chụp',
  underexposure: 'Nhiều vùng tối',
  highlight_clipping: 'Nghi cháy sáng',
};
const PAGE_SIZE = 12;
const MODAL_IMAGE_HEIGHT = 240;

function resolveDocsUrl(customUrl?: string): string {
  if (customUrl) return customUrl;
  if (typeof window !== 'undefined') {
    const host = window.location.hostname.toLowerCase();
    const port = window.location.port;
    if (
      host === 'dir.toolxprint.com' ||
      host === 'toolxprint.com' ||
      host === 'www.toolxprint.com' ||
      ((host === 'localhost' || host === '127.0.0.1') && port === '5174')
    ) {
      return '/photo-series/index.html';
    }
  }
  return 'https://dir.toolxprint.com/photo-series/index.html';
}

function clampN(value?: number): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 3;
  return Math.min(20, Math.max(1, Math.round(value)));
}

// Face boxes come back with the context request as [x0, y0, x1, y1] fractions of the representative photo.
function facesOf(group: Series, image: Photo) {
  return group.result?.images?.find(item => item.image_id === image.image_id)?.faces || [];
}
function duration(seconds: number) {
  const s = Math.max(0, Math.floor(seconds));
  return [Math.floor(s / 3600), Math.floor(s / 60) % 60, s % 60].map(value => String(value).padStart(2, '0')).join(':');
}

export function PhotoSeriesSection({
  defaultFolder = '',
  agentBaseUrl = 'http://127.0.0.1:9173',
  defaultN = 3,
  showDocsButton = true,
  initialShowDocs = false,
  docsUrl,
  storageKey = 'photo-series-v2-job',
  syncUrlQuery = true,
  embedded = false,
  className = '',
  onJobComplete,
  onExportComplete,
}: PhotoSeriesProps = {}) {
  const LOCAL = agentBaseUrl.replace(/\/$/, '');
  const resolvedDocsUrl = resolveDocsUrl(docsUrl);
  const [folder, setFolder] = useState(defaultFolder);
  const [n, setN] = useState(() => clampN(defaultN));
  const [id, setId] = useState(() => {
    if (typeof window === 'undefined') return '';
    const fromUrl = syncUrlQuery ? new URLSearchParams(window.location.search).get('photo_job') : null;
    if (fromUrl) return fromUrl;
    if (storageKey) {
      try {
        return localStorage.getItem(storageKey) || '';
      } catch {
        return '';
      }
    }
    return '';
  });
  const [job, setJob] = useState<Job>();
  const [error, setError] = useState('');
  const [sending, setSending] = useState(false);
  const [picking, setPicking] = useState(false);
  const [connected, setConnected] = useState(false);
  const [page, setPage] = useState(0);
  const [activeGroup, setActiveGroup] = useState<Series>();
  const [cancelling, setCancelling] = useState(false);
  const [observedAt, setObservedAt] = useState(performance.now());
  const [, setTick] = useState(0);
  const [refresh, setRefresh] = useState(0);
  const [showDocs, setShowDocs] = useState(initialShowDocs);
  const [filterOpen, setFilterOpen] = useState(false);
  const [filterCount, setFilterCount] = useState(1);
  const [filterState, setFilterState] = useState<ExportState>();

  const onJobCompleteRef = useRef(onJobComplete);
  onJobCompleteRef.current = onJobComplete;
  const onExportCompleteRef = useRef(onExportComplete);
  onExportCompleteRef.current = onExportComplete;
  const notifiedJobRef = useRef('');

  function persistJobId(nextId: string) {
    if (storageKey) {
      try {
        if (nextId) localStorage.setItem(storageKey, nextId);
        else localStorage.removeItem(storageKey);
      } catch {
        // ignore storage restrictions in sandboxed embeds
      }
    }
    if (syncUrlQuery && typeof window !== 'undefined') {
      const url = new URL(window.location.href);
      if (nextId) url.searchParams.set('photo_job', nextId);
      else url.searchParams.delete('photo_job');
      window.history.replaceState({}, '', url);
    }
  }

  useEffect(() => {
    if (!job?.running) return;
    const timer = setInterval(() => setTick(value => value + 1), 1000);
    return () => clearInterval(timer);
  }, [job?.running]);

  useEffect(() => {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function check() {
      try {
        const response = await fetch(`${LOCAL}/api/photo-series/health`, {
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(4000)]),
          cache: 'no-store',
        });
        const data = await response.json();
        if (!stopped) setConnected(response.ok && data.ready && data.version >= 3);
      } catch {
        if (!stopped) setConnected(false);
      }
      if (!stopped) timer = setTimeout(check, 5000);
    }
    void check();
    return () => {
      stopped = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [LOCAL]);

  useEffect(() => {
    if (!id) return;
    let stopped = false;
    let timer: ReturnType<typeof setTimeout>;
    const controller = new AbortController();
    async function poll() {
      try {
        const response = await fetch(`${LOCAL}/api/photo-series/jobs/${encodeURIComponent(id)}`, {
          signal: AbortSignal.any([controller.signal, AbortSignal.timeout(10000)]),
          cache: 'no-store',
        });
        if (!response.ok) throw new Error('Không đọc được tác vụ. Hãy kiểm tra PrintAgent.');
        const next: Job = await response.json();
        if (stopped) return;
        if (next.replacement_job_id) {
          setJob(undefined);
          setPage(0);
          setActiveGroup(undefined);
          setId(next.replacement_job_id);
          persistJobId(next.replacement_job_id);
          return;
        }
        setObservedAt(performance.now());
        setJob(next);
        setError('');
        if (next.status === 'completed' && notifiedJobRef.current !== next.job_id) {
          notifiedJobRef.current = next.job_id;
          onJobCompleteRef.current?.(next);
        }
        if (next.status === 'completed' || !next.running) return;
      } catch (cause) {
        if (stopped) return;
        setError(cause instanceof Error ? cause.message : 'Mất kết nối PrintAgent');
      }
      timer = setTimeout(poll, 2000);
    }
    void poll();
    return () => {
      stopped = true;
      controller.abort();
      clearTimeout(timer);
    };
  }, [id, sending, LOCAL, refresh]);

  useEffect(() => {
    if (!activeGroup) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setActiveGroup(undefined);
    };
    window.addEventListener('keydown', close);
    return () => window.removeEventListener('keydown', close);
  }, [activeGroup]);

  function resetView() {
    setJob(undefined);
    setId('');
    setPage(0);
    setActiveGroup(undefined);
    setError('');
    notifiedJobRef.current = '';
    persistJobId('');
  }

  async function pickFolder() {
    setPicking(true);
    setError('');
    try {
      const response = await fetch(`${LOCAL}/api/photo-series/pick-folder`, {
        method: 'POST',
        headers: { 'X-Photo-Series': '1' },
        signal: AbortSignal.timeout(180000),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Không mở được hộp chọn thư mục');
      if (data.folder) {
        setFolder(data.folder);
        // A different folder starts a fresh view; the previous job stays in PrintAgent and its link still works.
        if (job?.folder?.toLowerCase() !== String(data.folder).toLowerCase()) resetView();
      }
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Không chọn được thư mục');
    } finally {
      setPicking(false);
    }
  }

  async function submit(event?: FormEvent, resume = false) {
    event?.preventDefault();
    if (!resume && !folder) {
      setError('Hãy chọn thư mục ảnh trước.');
      return;
    }
    setSending(true);
    setError('');
    try {
      const response = await fetch(
        LOCAL + (resume ? `/api/photo-series/jobs/${encodeURIComponent(id)}/resume` : '/api/photo-series/jobs'),
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'X-Photo-Series': '1' },
          body: JSON.stringify(resume ? {} : { folder, config: { n } }),
          signal: AbortSignal.timeout(15000),
        }
      );
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Không tạo được tác vụ');
      setJob(undefined);
      setPage(0);
      setActiveGroup(undefined);
      notifiedJobRef.current = '';
      setId(data.job_id);
      persistJobId(data.job_id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Không kết nối được PrintAgent');
    } finally {
      setSending(false);
    }
  }

  async function startFilter() {
    setError('');
    setFilterState({ status: 'running', count: filterCount, done: 0, total: 0 });
    try {
      const url = `${LOCAL}/api/photo-series/jobs/${encodeURIComponent(id)}/export`;
      const response = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Photo-Series': '1' },
        body: JSON.stringify({ count: filterCount }),
        signal: AbortSignal.timeout(15000),
      });
      let state: ExportState = await response.json();
      if (!response.ok) throw new Error((state as { error?: string }).error || 'Không lọc được ảnh');
      while (state.status === 'running') {
        setFilterState(state);
        await new Promise(resolve => setTimeout(resolve, 1000));
        state = await (await fetch(url, { cache: 'no-store', signal: AbortSignal.timeout(10000) })).json();
      }
      setFilterState(state);
      if (state.status === 'completed') {
        onExportCompleteRef.current?.(state);
      }
    } catch (cause) {
      setFilterState({
        status: 'failed',
        count: filterCount,
        done: 0,
        total: 0,
        error: cause instanceof Error ? cause.message : 'Không lọc được ảnh',
      });
    }
  }

  function closeFilter() {
    if (filterState?.status !== 'running') {
      setFilterOpen(false);
      setFilterState(undefined);
    }
  }

  async function cancel() {
    setCancelling(true);
    setError('');
    try {
      const response = await fetch(`${LOCAL}/api/photo-series/jobs/${encodeURIComponent(id)}/cancel`, {
        method: 'POST',
        headers: { 'X-Photo-Series': '1' },
        signal: AbortSignal.timeout(15000),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || 'Không gửi được yêu cầu hủy');
      setJob(previous => (previous ? { ...previous, status: data.status, cancel_requested: data.cancel_requested } : previous));
      setRefresh(value => value + 1);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Chưa hủy được tiến trình');
    } finally {
      setCancelling(false);
    }
  }

  const busy = sending || cancelling || !!job?.running;
  const elapsed = duration((job?.elapsed_seconds || 0) + (job?.running ? (performance.now() - observedAt) / 1000 : 0));
  const groups = job?.series || [];
  const pages = Math.max(1, Math.ceil(groups.length / PAGE_SIZE));
  const currentPage = Math.min(page, pages - 1);
  const completed = groups.filter(group => group.status === 'completed').length;
  const selectedCount = groups.reduce((sum, group) => sum + group.images.filter(image => image.selected).length, 0);
  const sample = !!job && (job.is_sample || /^downloads.*smoke/.test(job.job_id));
  const selected = (group: Series) => group.images.filter(image => image.selected);
  const imageUrl = (image: Photo, kind = 'thumbnail') =>
    `${LOCAL}/api/photo-series/jobs/${encodeURIComponent(id)}/images/${image.image_id}/${kind}`;

  const panelClass = ['photo-series-panel', embedded ? 'is-embedded' : '', className].filter(Boolean).join(' ');

  return (
    <section className={panelClass}>
      <header className="ps-header">
        <div>
          <h2>Ảnh theo series</h2>
          <p>Chọn ảnh đẹp trong từng nhóm. Chỉ 1 ảnh đại diện mỗi nhóm được gửi lấy ngữ cảnh.</p>
        </div>
        <span
          className={`ps-connection ${connected ? 'is-connected' : ''}`}
          role="status"
          title={
            connected
              ? 'PrintAgent sẵn sàng xử lý JPEG, RAW và ARW'
              : 'Mở PrintAgent mới và cho phép trang kết nối mạng cục bộ'
          }
        >
          <i />
          {connected ? 'PrintAgent đã kết nối' : 'Chưa kết nối PrintAgent'}
        </span>
        {showDocsButton && (
          <button
            type="button"
            className={`ps-docs-toggle ${showDocs ? 'is-active' : ''}`}
            aria-pressed={showDocs}
            onClick={() => setShowDocs(value => !value)}
          >
            {showDocs ? '← Quay lại' : '📖 Tích hợp web'}
          </button>
        )}
      </header>
      {showDocs ? (
        <PhotoSeriesFederationDocs docsUrl={resolvedDocsUrl} agentBaseUrl={LOCAL} onClose={() => setShowDocs(false)} />
      ) : (
        <>
          <form id="photo-series-controls" onSubmit={event => void submit(event)} className="ps-controls">
            <div className="ps-folder">
              <span>Thư mục ảnh</span>
              <div>
                <button
                  type="button"
                  className="ps-secondary"
                  onClick={() => void pickFolder()}
                  disabled={busy || picking || !connected}
                >
                  {picking ? 'Đang chọn…' : 'Chọn thư mục'}
                </button>
                {busy ? (
                  <button
                    type="button"
                    className="ps-cancel"
                    disabled={!job || cancelling || job.cancel_requested}
                    onClick={() => void cancel()}
                  >
                    {cancelling || job?.cancel_requested ? 'Đang hủy…' : 'Hủy tiến trình'}
                  </button>
                ) : (
                  <button type="submit" disabled={picking || !connected || !folder}>
                    Nhóm &amp; phân tích
                  </button>
                )}
                <output aria-label="Thư mục ảnh đã chọn" title={folder}>
                  {folder || 'Chưa chọn thư mục'}
                </output>
              </div>
            </div>
            <label>
              Số ảnh đẹp nhất trong series
              <input
                type="number"
                required
                min={1}
                max={20}
                value={n}
                disabled={busy}
                onChange={event => setN(Number(event.target.value))}
              />
            </label>
            <span className="ps-formats">JPEG · RAW · ARW</span>
          </form>
          {error && <p role="alert">{error}</p>}
          {sample && (
            <aside className="ps-sample">
              Kết quả mẫu: {completed}/{job?.source_series_total || groups.length} nhóm đã có ngữ cảnh. Chưa phân tích toàn bộ thư mục.
            </aside>
          )}
          {busy && (
            <section className="ps-progress" aria-label="Tiến độ xử lý">
              <div className="ps-progress-header">
                <p role="status">
                  {job?.status === 'cancelling' ? 'Đang hủy tiến trình' : labels[job?.status || 'pending']} ·{' '}
                  {job?.processed || 0}/{job?.total || 0} ảnh · {completed}/{groups.length} nhóm đã có ngữ cảnh
                </p>
                <div className="ps-progress-actions">
                  <span role="timer" aria-label="Thời gian đã xử lý">
                    {elapsed}
                  </span>
                </div>
              </div>
              <progress
                max={Math.max(job?.status === 'scanning' ? job.total : groups.length, 1)}
                value={job?.status === 'scanning' ? job.processed : completed}
              />
            </section>
          )}
          {job?.status === 'cancelled' && !busy && (
            <p className="ps-cancelled-notice" role="status">
              Đã hủy tiến trình · {elapsed}. Các kết quả đã có được giữ lại.
            </p>
          )}
          {job?.error && <p role="alert">{job.error}</p>}
          {job && !job.running && job.status !== 'completed' && (
            <button
              className="ps-secondary"
              disabled={!connected || sending || job.remote_cancel_pending}
              onClick={() => void submit(undefined, true)}
            >
              Tiếp tục xử lý
            </button>
          )}
          {groups.length > 0 && (
            <>
              <div className="ps-results-heading">
                <div>
                  <h3>
                    {groups.length} nhóm ảnh{' '}
                    <span>
                      · {selectedCount} ảnh được chọn
                      {job?.people_split?.split ? ` · tách ${job.people_split.split} nhóm theo người` : ''}
                    </span>
                  </h3>
                  {job?.folder && <p title={job.folder}>{job.folder}</p>}
                </div>
                <span className="ps-context-count">
                  {completed}/{groups.length} nhóm có ngữ cảnh
                  {!job?.running && !!job?.elapsed_seconds && (
                    <small className="ps-elapsed-final">Thời gian xử lý: {elapsed}</small>
                  )}
                </span>
                <button
                  type="button"
                  className="ps-filter-button"
                  disabled={busy || !connected}
                  onClick={() => {
                    setFilterCount(1);
                    setFilterState(undefined);
                    setFilterOpen(true);
                  }}
                >
                  Lọc tất cả ảnh
                </button>
              </div>
              <div className="ps-grid" role="list" aria-label="Các nhóm ảnh">
                {groups.slice(currentPage * PAGE_SIZE, (currentPage + 1) * PAGE_SIZE).map((group, offset) => {
                  const pictures = selected(group);
                  const representative =
                    group.images.find(image => image.image_id === group.representative_image_id) ||
                    group.images.find(image => image.analysis_representative) ||
                    group.images.find(image => image.rank === 1);
                  return (
                    <article role="listitem" key={group.series_id} className="ps-series-card">
                      <div className="ps-card-heading">
                        <strong>Nhóm {currentPage * PAGE_SIZE + offset + 1}</strong>
                        <span>
                          {group.images.length} ảnh · chọn {pictures.length}
                        </span>
                      </div>
                      <div className="ps-card-body">
                        <button
                          type="button"
                          className="ps-fan"
                          onClick={() => setActiveGroup(group)}
                          aria-label={`Xem nhóm ${currentPage * PAGE_SIZE + offset + 1}`}
                        >
                          {pictures.slice(0, 3).map((image, index) => (
                            <span
                              className="ps-playing-card"
                              key={image.image_id}
                              title={image.filename}
                              style={
                                {
                                  '--card-index': index,
                                  '--card-count': Math.min(pictures.length, 3),
                                } as CSSProperties
                              }
                            >
                              {image.thumbnail_ready || image.upload_bytes ? (
                                <img loading="lazy" src={imageUrl(image)} alt={image.filename} />
                              ) : (
                                <span className="ps-image-placeholder">Đang chuẩn bị</span>
                              )}
                              <small>
                                #{image.rank} · {image.score}/100
                              </small>
                            </span>
                          ))}
                        </button>
                        <ol className="ps-file-list" aria-label={`Tên ảnh trong nhóm ${currentPage * PAGE_SIZE + offset + 1}`}>
                          {[...group.images]
                            .sort((a, b) => a.filename.localeCompare(b.filename))
                            .map(image => {
                              const isRepresentative = image.image_id === representative?.image_id;
                              return (
                                <li
                                  key={image.image_id}
                                  className={`${image.selected ? 'is-selected' : ''} ${
                                    isRepresentative ? 'is-representative' : ''
                                  }`}
                                  title={`${image.filename}${image.selected ? ` · #${image.rank}` : ' · không chọn'}${
                                    isRepresentative ? ' · ảnh đại diện lấy ngữ cảnh' : ''
                                  }`}
                                >
                                  <span>{image.filename}</span>
                                  {image.selected && <small>{isRepresentative ? '★' : `#${image.rank}`}</small>}
                                </li>
                              );
                            })}
                        </ol>
                      </div>
                      <p className="ps-card-context">{group.result?.context || labels[group.status] || 'Chưa lấy ngữ cảnh'}</p>
                      <div className="ps-card-footer">
                        <span className="ps-representative-name" title={representative?.filename}>
                          Ngữ cảnh: {representative?.filename || 'Chưa có ảnh đại diện'}
                        </span>
                        <button type="button" className="ps-text-button" onClick={() => setActiveGroup(group)}>
                          Xem nhóm →
                        </button>
                      </div>
                    </article>
                  );
                })}
              </div>
              <nav className="ps-pagination" aria-label="Phân trang nhóm ảnh">
                <button
                  type="button"
                  className="ps-secondary"
                  disabled={currentPage === 0}
                  onClick={() => setPage(currentPage - 1)}
                >
                  ← Trước
                </button>
                <span>
                  Trang {currentPage + 1}/{pages}
                </span>
                <button
                  type="button"
                  className="ps-secondary"
                  disabled={currentPage + 1 >= pages}
                  onClick={() => setPage(currentPage + 1)}
                >
                  Sau →
                </button>
              </nav>
            </>
          )}
          {!!job?.errors?.length && (
            <details className="ps-errors">
              <summary>{job.errors.length} ảnh chưa đọc được</summary>
              {job.errors.map(item => (
                <p key={item.filename}>
                  {item.filename}: {item.error}
                </p>
              ))}
            </details>
          )}
          {!job && !busy && <div className="ps-empty">Chọn thư mục và số ảnh đẹp cần giữ. Các nhóm ảnh sẽ hiển thị tại đây.</div>}
        </>
      )}
      {filterOpen && (
        <div className="ps-modal-backdrop" onClick={closeFilter}>
          <section
            className="ps-modal ps-filter-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Lọc tất cả ảnh"
            onClick={event => event.stopPropagation()}
          >
            <header>
              <h3>Lọc tất cả ảnh</h3>
            </header>
            <p>
              Chọn số ảnh tốt nhất của mỗi nhóm. Ảnh được chép vào thư mục con mới <strong>selected</strong>; ảnh gốc giữ
              nguyên. Ngữ cảnh của nhóm được ghi vào EXIF của bản chép (RAW kèm tệp .xmp).
            </p>
            <div className="ps-count-options" role="radiogroup" aria-label="Số ảnh tốt nhất mỗi nhóm">
              {[1, 2, 3].map(value => (
                <button
                  key={value}
                  type="button"
                  role="radio"
                  aria-checked={filterCount === value}
                  className={`ps-count-circle ${filterCount === value ? 'is-active' : ''}`}
                  disabled={filterState?.status === 'running'}
                  onClick={() => setFilterCount(value)}
                >
                  {value}
                </button>
              ))}
            </div>
            <p className="ps-filter-hint">
              {filterCount} ảnh × {groups.length} nhóm
              {completed < groups.length
                ? ` · ${groups.length - completed} nhóm chưa có ngữ cảnh sẽ chép không kèm ngữ cảnh`
                : ''}
            </p>
            {filterState?.status === 'running' && (
              <p role="status">
                Đang chép {filterState.done}/{filterState.total || '…'} ảnh…
              </p>
            )}
            {filterState?.status === 'completed' && (
              <p role="status" className="ps-filter-done">
                Đã chép {filterState.images} ảnh ({filterState.files} tệp) vào{' '}
                <span title={filterState.folder}>{filterState.folder}</span>. Ghi ngữ cảnh: {filterState.exif} JPEG
                {filterState.sidecars ? `, ${filterState.sidecars} RAW (.xmp)` : ''}.
                {filterState.errors?.length ? ` ${filterState.errors.length} tệp không chép được.` : ''}
              </p>
            )}
            {filterState?.status === 'failed' && <p role="alert">{filterState.error}</p>}
            <footer className="ps-filter-actions">
              <button
                type="button"
                className="ps-secondary"
                disabled={filterState?.status === 'running'}
                onClick={closeFilter}
              >
                {filterState?.status === 'completed' ? 'Đóng' : 'Hủy'}
              </button>
              {filterState?.status !== 'completed' && (
                <button
                  type="button"
                  disabled={filterState?.status === 'running' || !connected}
                  onClick={() => void startFilter()}
                >
                  {filterState?.status === 'running' ? 'Đang lọc…' : 'Lọc ảnh'}
                </button>
              )}
            </footer>
          </section>
        </div>
      )}
      {activeGroup && (
        <div className="ps-modal-backdrop" onClick={() => setActiveGroup(undefined)}>
          <section
            className="ps-modal"
            role="dialog"
            aria-modal="true"
            aria-label="Ảnh trong nhóm"
            onClick={event => event.stopPropagation()}
          >
            <header>
              <h3>
                {activeGroup.images.length} ảnh trong nhóm · chọn {selected(activeGroup).length}
              </h3>
              <button autoFocus className="ps-secondary" type="button" onClick={() => setActiveGroup(undefined)}>
                Đóng
              </button>
            </header>
            {activeGroup.capture_time_start && (
              <p className="ps-capture-time">
                Thời gian chụp (EXIF): {activeGroup.capture_time_start} – {activeGroup.capture_time_end}
              </p>
            )}
            {activeGroup.result?.context && <p>{activeGroup.result.context}</p>}
            <div className="ps-selected-images">
              {[...activeGroup.images]
                .sort((a, b) => a.rank - b.rank)
                .map(image => (
                  <figure key={image.image_id} className={image.selected ? '' : 'is-unselected'}>
                    <a href={imageUrl(image, 'preview')} target="_blank" rel="noreferrer">
                      {(() => {
                        const faces = facesOf(activeGroup, image);
                        const w = image.original_width;
                        const h = image.original_height;
                        if (!faces.length || !w || !h) return <img loading="lazy" src={imageUrl(image)} alt={image.filename} />;
                        return (
                          <span
                            className="ps-face-frame"
                            style={{ width: `min(100%, ${(MODAL_IMAGE_HEIGHT * w) / h}px)`, aspectRatio: `${w} / ${h}` }}
                          >
                            <img loading="lazy" src={imageUrl(image)} alt={image.filename} />
                            {faces.map((box, index) => {
                              // The pen loop is drawn a little larger than the face so it circles it instead of cutting through.
                              const padX = (box[2] - box[0]) * 0.3;
                              const padY = (box[3] - box[1]) * 0.25;
                              return (
                                <span
                                  key={index}
                                  className="ps-face-box"
                                  style={{
                                    backgroundImage: `url(${faceMark})`,
                                    left: `${(box[0] - padX) * 100}%`,
                                    top: `${(box[1] - padY) * 100}%`,
                                    width: `${(box[2] - box[0] + 2 * padX) * 100}%`,
                                    height: `${(box[3] - box[1] + 2 * padY) * 100}%`,
                                  }}
                                />
                              );
                            })}
                          </span>
                        );
                      })()}
                    </a>
                    <figcaption>
                      <strong>
                        #{image.rank} {image.filename}
                      </strong>
                      <p>
                        {image.score}/100 {image.analysis_representative ? '· Ảnh đại diện lấy ngữ cảnh' : ''}
                        {facesOf(activeGroup, image).length ? ` · ${facesOf(activeGroup, image).length} khuôn mặt` : ''}
                        {image.selected ? '' : '· Không chọn'}
                      </p>
                      {!!image.formats?.length && <p>{image.formats.join(' + ')}</p>}
                      {image.warnings.map(warning => (
                        <p key={warning} className="ps-warning">
                          {warnings[warning] || warning}
                        </p>
                      ))}
                    </figcaption>
                  </figure>
                ))}
            </div>
            {activeGroup.result_text && (
              <details>
                <summary>Chi tiết ngữ cảnh từ ảnh đại diện</summary>
                <pre>{activeGroup.result_text}</pre>
              </details>
            )}
          </section>
        </div>
      )}
    </section>
  );
}

export default PhotoSeriesSection;
