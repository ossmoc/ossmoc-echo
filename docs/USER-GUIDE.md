# Ossmoc Echo — hướng dẫn nhanh

Giải nén toàn bộ thư mục, mở **OssmocEcho.exe**. Không cần Python.
Giữ `_internal` cạnh EXE; cấu hình và dữ liệu được lưu trong `userdata`.

## Lần đầu sử dụng

1. Mở Wuthering Waves, chọn ngôn ngữ English.
2. Trong **Dữ liệu**, chọn thư mục game để đọc dữ liệu English, rồi cập
   nhật bản Việt WAHU hoặc nhập dữ liệu offline hợp lệ.
3. Trong **Tổng quát**, bấm **Chọn game**, chọn Wuthering Waves, **Kết nối**.
4. Bấm **Bắt đầu dịch**. Phím bật/tạm dừng mặc định **Alt+W**.

Bản công khai không kèm dữ liệu game hay FModelCLI. Đọc lại game
có thể cần tải công cụ trích xuất riêng từ nguồn được app chỉ định;
kiểm tra nguồn và giấy phép của công cụ trước khi sử dụng.
Khi chưa có dữ liệu, app hiện hướng dẫn trong giao diện, không tự dịch.

## Các trang

- **Tổng quát:** chọn game, bắt đầu/tạm dừng, đổi phím tắt.
- **Tiến trình:** chọn cốt truyện bạn đang ở hiện tại hoặc để tự nhận diện.
  Đây chỉ là gợi ý tìm câu; app luôn đối chiếu chữ OCR, không đoán câu tiếp.
- **Hiển thị:** chỉnh sửa hiển thị bản dịch — font, cỡ chữ, màu, viền và nền.
- **Dữ liệu:** phiên bản đang dùng, kiểm tra/cập nhật, nhập offline,
  đọc lại game; các tiện ích chẩn đoán nằm trong Dữ liệu nâng cao.

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
