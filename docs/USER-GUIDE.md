# Ossmoc Echo — hướng dẫn nhanh

Giải nén toàn bộ thư mục, mở **OssmocEcho.exe**. Không cần Python.
Giữ `_internal` cạnh EXE; cấu hình và dữ liệu được lưu trong `userdata`.

## Lần đầu sử dụng

Nếu app đã có đủ dữ liệu English và bản Việt, bỏ qua bước 1–3.

1. Vào **Dữ liệu → Chuẩn bị / làm mới dữ liệu English**.
2. Chọn **thư mục Wuthering Waves Game**, bên trong có thư mục `Client`.
   Không chọn `launcher.exe`, file `.exe`, `.pak`, `.db` hoặc ZIP bản dịch.
   Ví dụ bố cục: `Wuthering Waves/Wuthering Waves Game/Client` — chọn
   `Wuthering Waves Game`, không chọn `Client`. Nếu không nhớ chỗ cài,
   dùng chức năng mở thư mục game của launcher rồi tìm thư mục này.
3. Nhập **phiên bản game đang cài**, chẳng hạn `3.7`, không phải phiên bản
   Ossmoc Echo `1.0.2`. Đợi đọc dữ liệu hoàn tất, rồi bấm **Cập nhật bản dịch**.
4. Mở Wuthering Waves bằng ngôn ngữ **English**. Trong **Tổng quát**,
   bấm **Chọn game**, chọn Wuthering Waves, **Kết nối**.
5. Bấm **Bắt đầu dịch**. Phím bật/tạm dừng mặc định **Alt+W**.

## Dữ liệu English dùng để làm gì?

Đây là các câu tiếng Anh gốc lấy từ game, giúp đối chiếu chữ OCR, tìm
đúng câu và ghép bản Việt. Không phải model OCR hoặc một bản dịch.
Nút **Chuẩn bị / làm mới dữ liệu English** chỉ cần dùng lần đầu khi
chưa có dữ liệu hoặc sau khi game cập nhật. Không cần bấm mỗi lần mở app.
App chỉ đọc file game và lưu kết quả trong `userdata`, không sửa game.

Nếu **Cập nhật bản dịch** chưa bấm được và hiện “Chưa có dữ liệu English”,
hãy hoàn thành bước 1–3. Nếu gặp lỗi đọc dữ liệu, xem nhật ký ở
**Dữ liệu → Chẩn đoán và lịch sử**; dữ liệu cũ không bị xóa.
Mở lại hướng dẫn ngay trong app bằng **Dữ liệu → Hướng dẫn sử dụng**.

Bản công khai không kèm dữ liệu game hay FModelCLI. Đọc lại game
có thể cần tải công cụ trích xuất riêng từ nguồn được app chỉ định;
kiểm tra nguồn và giấy phép của công cụ trước khi sử dụng.
Khi chưa có dữ liệu, app hiện hướng dẫn trong giao diện, không tự dịch.

## Các trang

- **Tổng quát:** chọn game, bắt đầu/tạm dừng, đổi phím tắt.
- **Tiến trình:** chọn cốt truyện bạn đang ở hiện tại hoặc để tự nhận diện.
  Đây chỉ là gợi ý tìm câu; app luôn đối chiếu chữ OCR, không đoán câu tiếp.
- **Hiển thị:** chỉnh sửa hiển thị bản dịch — font, cỡ chữ, màu, viền và nền.
- **Dữ liệu:** phiên bản app và bản dịch, kiểm tra/cập nhật, chuẩn bị
  dữ liệu English. **Kiểm tra cập nhật** kiểm tra cả hai; chọn **Cập nhật
  Ossmoc Echo** hoặc **Cập nhật bản dịch** theo loại cần nâng cấp.
  Các tiện ích chẩn đoán nằm trong **Chẩn đoán và lịch sử**.

Không cần khoanh vùng. Hội thoại và đáp án dịch tại chỗ; tài liệu đọc
dài có khung cuộn riêng. Thoại lúc di chuyển còn thử nghiệm.
Không khớp bản Việt chắc chắn thì app giữ English, không tự dịch bằng API.

Nút **X** âm thầm thu về khay. Bấm đúp biểu tượng khay để mở lại;
chuột phải biểu tượng → **Thoát Ossmoc Echo** để thoát hẳn.
Alt+Tab không tắt xử lý; lớp dịch ẩn khi bạn dùng ứng dụng khác.
Game thu nhỏ có thể ngừng trả ảnh: app chờ ảnh, không tự tắt dịch.

Nếu không thấy bản dịch: kiểm tra đã chọn đúng cửa sổ, dữ liệu đã nạp,
English trong game và nút đang ở trạng thái chạy. Bản chưa có dữ liệu
Việt, chữ đang fade hoặc OCR không rõ có thể bị bỏ qua.
Không cam kết overlay trên mọi exclusive fullscreen; thử borderless.

App chỉ chụp cửa sổ và đọc dữ liệu, không sửa game/PAK, đọc bộ nhớ,
inject DLL hoặc tự bấm game. OCR offline; kiểm tra/cập nhật GitHub có
sử dụng mạng. Đừng chia sẻ `userdata` nếu có cấu hình/dữ liệu riêng.
