# Ghép dữ liệu 1.3.1 — 16/10/2026

## Quy tắc

Giữ các liên kết đang hợp lệ. Với mục chưa nối, thử lần lượt:

1. English chính xác trong cùng namespace/bảng; câu ngắn cần ID.
2. Cùng ID và bảng nguồn. Chấp nhận English thay đổi khi câu Trung khớp;
   nếu không có câu Trung thì cần English đạt ít nhất 70%.
3. Câu Trung chính xác, có một bản Việt không mơ hồ trong phạm vi nguồn.
4. Bản Việt duy nhất giữa các ứng viên cùng ID/câu Trung thuộc họ DB liên quan.
5. Cùng ID/bảng và quan hệ DB `lang_multi_text` / `1sthalf` / `2ndhalf`,
   English ít nhất 70%, không có câu Trung mâu thuẫn.
6. English 90%, 80%, 70% trong cùng identity hoặc Flow ShowTalk đã được xác định.

Tỷ lệ là so khớp ký tự sau chuẩn hóa Unicode, hoa/thường, dấu câu và markup,
không phải xác suất đúng hay điểm ngữ nghĩa. Các ngưỡng được kiểm thử riêng.
Không quét toàn bộ database ở mức 70% để gán một câu ngắn bất kỳ. "Bản Việt
duy nhất" chỉ giải quyết các ứng viên đã có liên hệ, không phải bằng chứng
để lấy một bản dịch không liên quan. Các bản Việt khác nhau cạnh tranh hoặc
điểm gần nhau trong 5 điểm phần trăm vẫn bị chặn.

Các bước phục hồi không tự bỏ phủ định, thay số hay thay placeholder. Không
đoán namespace, bảng hay index redirect không biết. Không thay model OCR hoặc
ngưỡng nhận diện OCR trực tiếp. Provenance lưu stage, điểm và hash nguồn/đích,
ID nguồn gốc, namespace/bảng để kiểm tra lại liên kết.

Tên riêng được nhận từ Speaker/RoleInfo/OccupationConfig/CharacterDisplayStyle
và placeholder tên người chơi, không suy mọi câu viết hoa là tên. Các câu chỉ
gọi những tên đã nhận diện, cùng cảnh không có English, không vào mẫu số của
Tiến trình; số loại trừ vẫn được lưu trong coverage.json. Tên chưa có trong
metadata không tự bị loại bằng suy đoán. Tên đã đánh dấu không làm mốc runtime.

## Kết quả kiểm thử

| Hạng mục | Kết quả trên máy kiểm thử |
| --- | --- |
| Source unit/integration | 853 PASS, 0 skip |
| Từ cache 1.3.0 + nguồn bản dịch v3.0.17 | 276.472 → 277.419 mục Việt, thêm 947 |
| 53 mục có bản Việt ứng viên trong bảng rà soát | Phục hồi 53/53 |
| Các liên kết cũ có nguồn không đổi | Cổng hồi quy mất 0 |
| Simulacrum of the Heart (3.7) | 1.370/1.370 sau lọc tên |
| Song of the Heart Sword (3.6) | 1.395/1.398 sau lọc 13 tên và 2 cảnh không chữ |
| The Nethermancer's Requiem (3.6) | 96/96 |
| Gói công khai | Không userdata, database game, ảnh riêng hay Microsoft OneOCR assets |
| EXE cài sạch | Mở UI 1.3.1, báo thiếu English đúng trạng thái |
| EXE native Windows 11 | OCR/capture cửa sổ, overlay/clipboard, phím tắt và các cảnh QA đạt |
| Updater đã phát hành 1.3.0 → ZIP 1.3.1 | Helper thật, giữ 7 file, cập nhật alias và giữ rollback |

Lượt native trong sandbox không được đăng ký hotkey/chụp cửa sổ. Chạy lại
ngoài sandbox bằng cùng EXE đã đạt; không coi lỗi quyền của harness là sửa
thành công trong app. Updater dùng NoRestart trong QA, không gọi đó là đã
kiểm chứng tự mở GUI trên mọi máy. Windows 10 thật, GPU/driver khác, máy RAM
thấp và chơi ngâm thật: NOT_RUN. Xem ma trận QA-WINDOWS.md.

Ba mục chưa nối của nhóm 3.6 chính còn thiếu ứng viên nguồn/bản Việt hợp lệ;
không bịa bản dịch để đạt 100%. Thống kê đếm identity nguồn, không phải số
câu độc nhất hay số cảnh đã chơi thử trong game.

## Nâng dữ liệu cũ

Revision ghép tăng 5 → 6. Sau nâng app, **Dữ liệu → Kiểm tra cập nhật → Cập
nhật bản dịch** sẽ dựng lại liên kết ngay cả khi upstream vẫn là v3.0.17.
Không cần làm mới English nếu game chưa cập nhật và nguồn đã chuẩn bị còn
nguyên. Mọi công việc ghép diễn ra trong generation staging rồi kiểm tra mất
liên kết trước khi đổi active.json; không sửa file game hay ghi đè generation cũ.

README, giấy phép và các thông báo có sẵn được giữ nguyên.
