# Kiểm thử và cơ chế ghép dữ liệu 1.3.0 — 16/10/2026

## Phạm vi xác nhận

Windows 11 x64 build 26300, Python 3.12.13. Không đổi model OCR, README hay
giấy phép. Không coi Windows Server CI là kiểm thử máy Windows 10 thật.

| Hạng mục | Kết quả |
| --- | --- |
| Toàn bộ source tests | 835 PASS, 0 skip |
| Ghép trên cache cũ, dùng raw EN/CN cùng release | 262.116 → 274.688 mục Việt; mất/đổi bản Việt cũ: 0 |
| Dựng lại từ nguồn game đã trích xuất, giữ bảng/namespace | 276.472 mục Việt / 320.356 identity nguồn; không quét PAK lại |
| Cổng hồi quy khi nâng generation thật | 262.116 liên kết cũ có nguồn không đổi được giữ; mất: 0 |
| Zuoyequnxing có bản Việt và tham chiếu Flow | 1.718 bản ghi vào runtime index trên bộ cache kiểm chứng |
| Tiến trình 3.1–3.7 | Có nhóm và độ bao phủ từ Quest/HandBook/Flow, không suy phiên bản từ số Hồi |
| Giả lập tương lai | 3.8, 4.12 và họ ID không theo Main/Side/numeric-scene: tự phân nhóm, tra cứu và ngữ cảnh |
| Chọn chương chưa có OCR xác nhận | Không được dùng làm bằng chứng để ép câu rất ngắn |
| Nhánh lựa chọn | Không nối mặc định sang câu nằm sau nhánh; giữ nhiều ứng viên/mơ hồ thì từ chối |
| Partial update | Chỉ thêm một nhóm nhưng làm mất nhóm nguồn không đổi: chặn active.json, giữ generation cũ |
| Collision/schema/redirect | Giữ namespace/bảng; unknown mapping, vòng lặp, payload lỗi không được đoán |

Các số trên đếm identity nguồn, không phải số câu độc nhất hay số câu đã
chơi thử trong game. Số mục ghép và số mục thực sự vào chỉ mục được tách
riêng. Không ép khớp các câu đã đổi nội dung, bị mask hoặc thiếu chứng cứ.

Lượt EXE dùng ZIP thật đã kiểm tra cài sạch, OCR/capture native, phụ đề
Việt/đáp án, cảnh letterbox/câu dài, clipboard và cửa sổ QA che/thu nhỏ/
khôi phục/đóng. Gói updater 1.2.0 → 1.3.0 dùng helper thật trong thư mục QA
có dấu, giữ 7 file dữ liệu/cấu hình/thông báo và giữ rollback. Helper dùng
`NoRestart`: không gọi đó là đã kiểm chứng tự mở GUI sau update trên mọi máy.

Windows 10, driver/GPU/mixed-DPI thực, máy RAM thấp và chơi ngâm trong game
thật: **NOT_RUN**. Tiếp tục dùng ma trận [QA-WINDOWS.md](QA-WINDOWS.md).

## Không cần vá ID từng phiên bản

Importer không có danh sách bắt buộc 3.3/3.4/3.5/3.7. Nó nhận nguồn EN/CN,
TXT và canonical cùng release; đối chiếu identity + nguồn hiện tại. Metadata
Quest → Chapter → HandBook state → ShowTalk xác định thoại/đáp án và nhóm.
Mốc phiên bản lấy từ Key nhiệm vụ khi có; chương không có mapping được để
riêng, không bịa số phiên bản.

Với game/bản dịch mới vẫn theo schema đã hỗ trợ, app tự dựng lại các liên
kết và danh mục từ dữ liệu mới. Sau cập nhật game, cần làm mới English như
hướng dẫn trong app; app không thể đọc các file mới chưa được trích xuất.
Nếu nhà phát hành đổi schema/đường dẫn/mapping redirect không tương thích,
app báo lỗi hoặc coverage partial, giữ generation trước; không hứa đoán
được mọi format tương lai.

Nguồn TXT được duyệt theo English chính xác và target Tên Anh canonical.
Chinese localization khác được ghi rõ trong provenance, không gọi đó là
bilingual proof. Nguồn raw mới và sửa grammar/redirect vẫn yêu cầu bằng
chứng EN/CN theo đúng loại quy tắc; không fuzzy để bỏ phủ định/số/tên.

## Nâng cache cũ

Sau cập nhật app, chọn **Dữ liệu → Kiểm tra cập nhật → Cập nhật bản dịch**
một lần. Revision importer tăng 4 → 5; cùng release cũng được đề nghị dựng
lại. Nếu còn nguồn game đã trích xuất, app tự dựng bảng/namespace/metadata
trong staging, không bắt chạy lại FModel/PAK. Nếu thiếu nguồn thì giữ cách
tra cứu cũ và báo tình trạng thiếu; không gắn nhãn coverage đầy đủ giả.

`coverage.json` lưu diff theo nhóm trước khi commit active.json. Bằng chứng
nguồn được lưu một lần trong registry có hash; các search alias dùng tham
chiếu, tránh nhân nhiều bản provenance. Cache JSON vẫn là dữ liệu app-owned,
không phải định dạng trao đổi bản dịch cho người dùng tự sửa.

## Tái chạy QA dữ liệu riêng

`tools/verify_release_data.py` đối chiếu provider từ EXE 1.2.0 với importer
mới, có thể chạy module lấy trực tiếp từ PYZ của EXE phát hành.
`tools/verify_story_update.py` thực hiện migration + merge + gate + commit
trong root QA riêng; `tools/verify_game_sources.py` dựng nguồn giữ đầy đủ
bảng từ dữ liệu đã trích xuất. Dữ liệu game, cache QA, ảnh, đường dẫn máy
cá nhân và các gói bản dịch không được đưa vào source/release công khai.
