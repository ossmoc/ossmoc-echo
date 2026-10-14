# Ossmoc Echo

Ứng dụng Windows hiển thị bản dịch Việt cho Wuthering Waves bằng
OCR English và đối chiếu dữ liệu có sẵn. Duy trì bởi **Ossmoc**.

## Sử dụng

1. Vào trang GitHub Releases, tải bản mới nhất dành cho Windows rồi giải nén.
2. Mở Wuthering Waves và chuyển ngôn ngữ game sang English.
3. Mở OssmocEcho.exe, vào mục Dữ liệu → chọn Làm mới dữ liệu English.
4. Chọn thư mục Wuthering Waves Game có chứa thư mục Client. Chỉ cần thực hiện lại bước này sau khi game cập nhật hoặc lần đầu sử dụng app.
5. Quay về Tổng quát → chọn Đổi cửa sổ game → chọn cửa sổ Wuthering Waves.
6. Nhấn Bắt đầu dịch, sau đó quay lại game.

Bản công khai không kèm dữ liệu game, ảnh chụp, cấu hình
người dùng hoặc công cụ FModelCLI. Có thể nhập dữ liệu hợp lệ từ nguồn
chính thức. Cần đọc lại dữ liệu game sau khi game cập nhật. Xem
[hướng dẫn](docs/USER-GUIDE.md) và [build từ source](docs/BUILD.md).

## Tính năng

- Hội thoại, đáp án, chữ chuyển cảnh, giải thích thuật ngữ và tài liệu.
- Đọc riêng cửa sổ game; Alt+Tab không tắt luồng xử lý.
- Tiến trình chọn theo mốc/khu vực, tự nhớ nhiệm vụ phụ đã nhận đúng.
- Kiểu chữ, viền, nền và độ đậm tùy chỉnh; khung ổn định theo từng câu.
- Windows OCR và model English nhẹ dự phòng chạy offline trên CPU.
- Kiểm tra bản Việt mới khi mở; chỉ tải khi người dùng chọn cập nhật.

Không dùng API dịch, không tự điều khiển game, không sửa PAK, không đọc
bộ nhớ hay inject DLL. Khi chưa khớp bản Việt chắc chắn thì giữ English.
Không cam kết hỗ trợ mọi exclusive fullscreen, hiệu ứng chữ hoặc đoạn
thoại quá nhanh. Nút X thu về khay; thoát hẳn từ menu biểu tượng khay.

## Nguồn và giấy phép

Ossmoc Echo được phát triển từ [Ludiglot](https://github.com/Herselfta/Ludiglot).
Giữ thông báo bản quyền MIT của **Ludiglot Contributors** tại
[LICENSES/Ludiglot-MIT.txt](LICENSES/Ludiglot-MIT.txt).
Ứng dụng tích hợp và các đóng góp của Ossmoc được phát hành theo
**GPL-3.0-only**: xem [LICENSE](LICENSE), [NOTICE](NOTICE.md) và
[giấy phép thành phần](THIRD_PARTY.md).

GPL cho phép sử dụng, sửa đổi và phân phối theo các điều kiện của nó.
Repo công khai không phải biện pháp ngăn sao chép; lịch sử Git, bản quyền,
nguồn gốc và ghi công được giữ rõ ràng. Không áp thêm điều khoản chống
fork/clone trái với GPL. Tên dự án không có nghĩa là một fork được Ossmoc
hỗ trợ hoặc là bản phát hành chính thức.
