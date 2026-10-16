# Kết quả kiểm thử 1.2.0 — 16/10/2026

## Phạm vi đã xác nhận

Máy chạy thực: Windows 11 x64 build 26300, Python 3.12.13, Qt 6.11.2,
windows-capture 2.0.1. Dùng dependencies khóa; không đổi model OCR.

| Hạng mục | Kết quả | Phạm vi |
| --- | --- | --- |
| Toàn bộ source tests | PASS: 813, skip 0 | gồm native PowerShell rollback khi file bị khóa |
| UI/viewport/overlay | PASS: 110 ca × 4 lượt | Qt offscreen scale 1/1.25/1.5/2; không phải 4 màn hình thật |
| Dữ liệu release thật | PASS | so với code EXE 1.1.3: 260.165 → 262.116 câu Việt; mất/đổi bản Việt cũ: 0 |
| ID báo lỗi | PASS | Main_Mengzhou_3_6_90_1 và Main_Mengzhou_3_6_94_1 qua source → DB → cache → resolve |
| EXE từ ZIP cuối | PASS_NATIVE | Windows OCR, chữ Việt, frame 16:9/letterbox/câu dài, không mở hộp trên |
| WGC / lớp dịch / clipboard | PASS_NATIVE | che game bằng cửa sổ khác, thu nhỏ/khôi phục/đóng cửa sổ QA, ảnh composite vào clipboard |
| Cài sạch | PASS_NATIVE | không dữ liệu: app mở, chỉ dẫn chuẩn bị English không bị check nền ghi đè |
| Updater đã phát hành 1.1.3 → 1.2.0 | PASS_NATIVE (NoRestart) | ZIP thật, helper thật, thư mục có dấu; 7 file config/data/docs giữ nguyên; alias cập nhật; giữ rollback |
| Windows 10 / các build Windows 11 khác | NOT_RUN | không có máy/VM tương ứng trong lượt này |
| iGPU đời cũ / hybrid GPU / external-only / mixed DPI thật | NOT_RUN | cần ma trận máy thật trong QA-WINDOWS.md |
| Tự khởi động GUI từ helper sau update | NOT_RUN native | script dùng Normal đã có regression test; lượt helper dùng NoRestart |
| Ngâm 30–60 phút trong game thật | NOT_RUN | replay và native capture dùng cảnh QA, không thay thế chơi dài |

Không suy rộng 813 tests thành “mọi máy Windows 10/11 đều đã thử”. Ma trận
và bước tái hiện đầy đủ: [QA-WINDOWS.md](QA-WINDOWS.md). CI chạy Windows
Server 2022/2025 cũng không thay cho Windows desktop hay driver GPU thật.

## Điều kiện cập nhật dữ liệu

Sau khi cập nhật app, vào **Dữ liệu → Kiểm tra cập nhật → Cập nhật bản dịch**
thêm một lần. Quy tắc dựng nguồn tăng từ revision 3 lên 4; cache cũ không tự
có các liên kết mới chỉ vì thay EXE. Không cần quét lại PAK chỉ vì bản sửa này.

Chấp nhận cùng bản Việt ở hai bảng chỉ khi bằng chứng ID/bảng/phiên bản,
nguồn English/Chinese và bản Việt hợp lệ; không dùng giống vài từ để bỏ qua
chốt dữ liệu. Sai Chinese, đổi nghĩa, thêm phủ định/số lượng vẫn bị từ chối.

## Tính toàn vẹn

- Windows ZIP SHA-256:
  `87c0b2980caa4340d0c8429ea60bf39adde1929594e12c5caa3214150bd402c1`
- EXE SHA-256:
  `c8fa8cd95d458da67d5d897d00ea26403a1b055277afba1cfb027e51903d5fc4`
- Gói công khai không chứa userdata, config riêng, database game/bản dịch,
  log, ảnh game hoặc assets OneOCR của Microsoft. README/giấy phép cũ giữ nguyên.
- Kết quả dữ liệu là của bộ mẫu đã kiểm tra, không cam kết mọi câu thiếu dịch
  đều đã khôi phục. Các đầu vào dữ liệu QA và cài đặt người dùng không thay đổi.
