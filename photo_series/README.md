# Chọn ảnh theo series

PrintAgent xử lý local bằng Pillow, OpenCV và NumPy, không chạy AI. Giao diện điều khiển nằm trong tab **Ảnh theo series** của File Explorer tại **dir.toolxprint.com**. GenAI chỉ cung cấp bridge phân tích.

Luồng: chọn thư mục bằng hộp thoại Windows → quét đệ quy → tự suy ra nhịp chụp từ EXIF và kết hợp độ giống bố cục/màu để nhóm → xếp hạng kỹ thuật → chọn tối đa N ảnh đẹp tại local → chỉ lấy ảnh hạng #1 làm đại diện → crop trung tâm 2/3 và thumbnail màu của đúng ảnh đó → bridge lấy ngữ cảnh chung → trả kết quả về UI. N không làm tăng số ảnh gửi server: luôn 1 ảnh nguồn mỗi series, được truyền thành crop + thumbnail.

Crop được truyền dưới dạng hình chữ nhật bao quanh elip. Preview vẽ đường elip. Tọa độ tính theo ảnh gốc sau chuẩn hóa hướng EXIF, kể cả khi đọc từ preview RAW. JPEG, RAW và ARW được hỗ trợ. RAW dùng preview màu do máy ảnh nhúng trong file; khi không có preview thì dùng giải mã CPU. Cặp JPEG + RAW cùng tên trong cùng thư mục tính là một lần chụp; ưu tiên JPEG làm nguồn đánh giá màu, giữ danh sách cả hai file trong kết quả chọn. Không sửa file gốc.

## Sử dụng

Mở `https://dir.toolxprint.com/?view=photo-series`, chọn máy cục bộ và mở PrintAgent mới. Đèn xanh xác nhận kết nối. Bấm **Chọn thư mục**, đặt **Số ảnh đẹp nhất trong series**, rồi bấm **Nhóm & phân tích** ở hàng trên. Các nhóm hiển thị bằng cụm tối đa 3 lá bài xòe, 12 nhóm mỗi trang; bấm cụm để xem toàn bộ N ảnh được chọn. Tiến độ chỉ hiện khi đang xử lý. Nhãn kết quả mẫu luôn ghi rõ số nhóm thực sự đã có ngữ cảnh và không coi đó là toàn bộ thư mục. Nếu chọn agent từ xa, UI chặn thao tác thay vì chạy nhầm trên máy local.

Khóa API có thể cấu hình bằng biến môi trường `PHOTO_SERIES_API_KEY` trên bridge và cấu hình agent tương ứng. UI không có ô khóa API hay Tab ID; cấu hình vận hành nằm ở agent. API local chỉ nhận từ loopback, giới hạn origin và yêu cầu header `X-Photo-Series: 1` khi chọn thư mục/tạo/tiếp tục tác vụ.

SQLite local lưu hash, chỉ số chất lượng và tiến độ. SQLite trên bridge lưu yêu cầu/kết quả để khôi phục sau restart. Retry upload dùng khóa job/series để tránh tạo tác vụ trùng; kết quả thiếu JSON hoặc thiếu ID không được coi là hoàn tất. Upload/phân tích chạy tối đa 4 series đồng thời theo cấu hình.

Các giá trị mặc định: N=3, nhóm tự động, độ giống tối thiểu 0.80, crop tối đa 1280 px, thumbnail 480 px. Ngưỡng thời gian được suy ra từ nhịp chụp trong dữ liệu, người dùng không phải đặt một số giây cố định. Bộ xếp hạng đánh giá độ nét tương đối, vùng cháy/tối và độ lệch màu; không nhận diện người hay biểu cảm tại local. Thiếu EXIF sẽ dùng giờ tệp và đánh dấu cảnh báo. Màu khác biệt chỉ là nghi vấn, không tự sửa màu.

## CLI

Từ thư mục Toolx:

```powershell
python -m photo_series.main scan "C:\Photos"
python -m photo_series.main preview JOB_ID
python -m photo_series.main upload JOB_ID
python -m photo_series.main status JOB_ID
python -m photo_series.main retry JOB_ID
```

`scan` chỉ nhóm/chọn, `preview` tạo ảnh kiểm tra, `process`/`upload`/`retry` chạy các bước còn thiếu đến lấy kết quả. Đổi N hoặc thông số nhóm cần tạo lượt scan mới.

## Tích hợp và kiểm tra

- Nguồn PrintAgent: `Goxprint/GoxAgent/agent/photo_series`; đăng ký route tại `agent/webs/web.py`.
- Nguồn UI: `Goxprint/auto-gox-react/src/components/FileExplorer/components/PhotoSeriesSection.tsx`; hiển thị trong tab Ảnh theo series của DirToolx.
- Nguồn bridge: `GenImgs/vps_backend/photo_series/bridge_routes.py`.
- `GET /api/photo-series/health`, `POST /api/photo-series/pick-folder`, `POST /api/photo-series/jobs`, `GET /api/photo-series/jobs/{id}`, `POST /api/photo-series/jobs/{id}/resume`: local.
- `POST /api/vision/analyze`: multipart metadata + crop/thumbnail.
- `GET /api/vision/series/{id}`, `POST /api/vision/series/{id}/retry`: bridge; response dùng ngữ cảnh và ID ảnh, không công khai cấu hình xử lý nền.
- `python -m unittest photo_series.test_pipeline -v`: kiểm thử nhóm/chọn, bảo toàn ảnh gốc, cache, API, kết quả thiếu và khôi phục kết quả.
- `python -m photo_series.acceptance_v2`: kiểm tra toàn bộ JPEG/ARW trong `~/Downloads/1`, kiểm tra decoder RAW trực tiếp, lấy ngữ cảnh 3 series mẫu (đúng 1 ảnh đại diện mỗi series), cập nhật dữ liệu mẫu của UI. Báo cáo tại `output/photo-series-v2-test/acceptance.json`.
- `node photo_series/ui_v2.cjs`: kiểm tra đèn kết nối, bố cục, lá bài xòe, phân trang 12+2 bằng fixture chỉ trong trình duyệt, chọn thư mục qua kết quả chooser giả lập và tạo một tác vụ RAW thật qua bản exe. Runtime Tk của hộp thoại được kiểm tra riêng bằng `python -m photo_series.test_frozen_picker`.

Không thay toàn bộ server production bằng bản local: server live có các tích hợp riêng. Khi triển khai chỉ thêm module và dòng đăng ký route; giữ các asset giao diện cũ để tránh lỗi các tab đang mở.
