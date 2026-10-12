"""User-facing setup guidance, shared by startup, data page and errors."""

ENGLISH_DATA_NOTE = (
    'Tác dụng: lấy câu tiếng Anh gốc từ game để tìm đúng câu và ghép bản Việt.\n'
    'Khi dùng: lần đầu chưa có dữ liệu English hoặc sau khi game cập nhật. '
    'Đã có dữ liệu thì không cần làm lại mỗi lần mở app.\n'
    'Cách dùng: chọn thư mục Wuthering Waves Game (bên trong có Client), '
    'không chọn file .exe hay .pak. Nhập phiên bản game đang cài, ví dụ 3.7 '
    '(không phải phiên bản Ossmoc Echo), đợi hoàn tất rồi bấm Cập nhật bản dịch. '
    'App chỉ đọc, không sửa file game.'
)

MISSING_ENGLISH_DATA = (
    'Chưa có dữ liệu English. Vào Dữ liệu → Chuẩn bị / làm mới dữ liệu English, '
    'chọn thư mục Wuthering Waves Game và phiên bản game đang cài. '
    'Đợi hoàn tất rồi bấm Cập nhật bản dịch.'
)

USAGE_GUIDE = (
    'Chuẩn bị lần đầu\n'
    '1. Nếu chưa có dữ liệu: vào Dữ liệu → Chuẩn bị / làm mới dữ liệu English.\n'
    '2. Chọn thư mục Wuthering Waves Game có thư mục Client; không chọn .exe hoặc .pak.\n'
    '3. Nhập phiên bản game đang cài (ví dụ 3.7), không phải phiên bản Ossmoc Echo. '
    'Đợi đọc xong, rồi bấm Cập nhật bản dịch.\n'
    'Đã có đủ dữ liệu rồi thì bỏ qua các bước chuẩn bị. Chỉ làm mới English sau khi game cập nhật.\n\n'
    'Sử dụng\n'
    '4. Mở game bằng ngôn ngữ English. Trong Tổng quát, Chọn game → Kết nối.\n'
    '5. Bấm Bắt đầu dịch (Alt+W mặc định); chỉnh chữ ở Hiển thị.\n\n'
    'Dữ liệu English là câu gốc để đối chiếu chữ OCR, không phải một model OCR hay bản dịch. '
    'App chỉ đọc dữ liệu game, không sửa game.\n'
    'Kiểm tra cập nhật kiểm tra cả app lẫn bản dịch; chỉ tải khi bạn chọn cập nhật.\n'
    'Nút X thu app về khay; thoát hẳn từ menu biểu tượng khay.'
)
