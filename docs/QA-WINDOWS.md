# Quy trình kiểm thử Windows — Ossmoc Echo

## Phạm vi và cách đọc kết quả

Đích phát hành hiện tại: Windows x64, Windows 10 và Windows 11. Không có
khái niệm kiểm thử hết mọi máy: dùng các ca lỗi bắt buộc và ma trận rủi ro
dưới đây, thêm ca hồi quy mỗi khi nhận báo cáo mới.

Mỗi lượt phải ghi phiên bản app, commit, SHA-256 ZIP/EXE, Windows/build,
CPU/RAM, driver GPU, số màn hình/DPI, phiên bản game, release bản dịch và
`source_build_revision`. Không đăng log/ảnh/config cá nhân lên GitHub.

Trạng thái: **PASS_NATIVE**, **PASS_SIMULATED**, **FAIL**, **NOT_RUN**,
**BLOCKED**. Mock Windows cũ không phải PASS_NATIVE trên Windows cũ.
Các runner GitHub `windows-2022`/`windows-2025` là Windows Server: chỉ là
bổ sung CI, không thay thế Windows 10/11 desktop hoặc GPU thật.

Giới hạn nền tảng từ tài liệu gốc:

- Qt 6.11: Windows 10 1809+ x64 / Windows 11.
  <https://doc.qt.io/qt-6.11/supported-platforms.html>
- WGC chọn HWND (`CreateForWindow`): Windows 10 1903+.
  <https://learn.microsoft.com/en-us/windows/win32/api/windows.graphics.capture.interop/nf-windows-graphics-capture-interop-igraphicscaptureiteminterop-createforwindow>
- `WDA_EXCLUDEFROMCAPTURE`: Windows 10 2004+; bản cũ có thể trả vùng đen,
  không được coi như đã hỗ trợ loại lớp dịch khỏi ảnh thành công.
  <https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-setwindowdisplayaffinity>
- Windows OCR có thể trả `null` nếu không có ngôn ngữ OCR phù hợp.
  <https://learn.microsoft.com/en-us/uwp/api/windows.media.ocr.ocrengine.trycreatefromlanguage>

Vì vậy ưu tiên xác nhận đầy đủ trên Windows 10 22H2 và Windows 11; các bản
Windows 10 1903/1909/2004 cần kiểm tra giới hạn riêng, không hứa hỗ trợ chỉ
vì cùng tên Windows 10. Windows 10 trước 1903 không đáp ứng đường chụp HWND.
Gói x64 không phải gói x86/ARM64; chạy giả lập ARM64 là ca riêng chưa xác nhận.

## Ma trận máy thật / VM sạch

Tạo snapshot sạch, không chép `userdata`, Python, model Microsoft hoặc dữ
liệu của máy phát triển. Cài thành phần hệ thống chỉ khi người dùng đồng ý.
Không tắt antivirus, không sửa file game, không chạy game quản trị mặc định.

| ID | Môi trường | Điều kiện phải thử |
| --- | --- | --- |
| OS01 | Win10 22H2 x64 | user thường; en-US OCR có/không; Snipping Tool cũ/không có OneOCR |
| OS02 | Win10 2004/21H2 x64 | WGC mặc định; lớp che không trở thành đầu vào OCR |
| OS03 | Win10 1903/1909 x64 | giới hạn API; lỗi phải rõ, không tự chụp desktop thay thế |
| OS04 | Win11 23H2/24H2/25H2 x64 | cài mới và nâng cấp; cập nhật Snipping Tool |
| HW01 | CPU 2–4 luồng, RAM 8 GB, iGPU | 720p/1080p; không GPU compute; HDD chậm; đo bộ nhớ/độ trễ |
| HW02 | laptop hybrid iGPU+dGPU | app/game trên GPU khác; nguồn pin/cắm sạc |
| HW03 | desktop một màn | 100/125/150/200% DPI; 720p/1080p/1440p/4K |
| HW04 | laptop + màn ngoài | Extend; external-only; laptop tắt; đổi màn chính; tọa độ âm |
| HW05 | hai màn khác DPI | đổi thứ tự màn, kéo game qua màn, tháo/cắm lại, sleep/resume |

Đổi đồng thời ít biến nhất để tìm nguyên nhân. Ít nhất chạy OS01+HW01,
OS04+HW03 và OS04+HW04/HW05. Chỉ đánh dấu máy thật đã chạy khi có biên bản.
VM không có GPU passthrough không chứng minh được capture game/driver.

## Các ca bắt buộc

Với mỗi ca ghi tiền điều kiện, thao tác, kết quả thực, PASS/FAIL và bằng chứng.
`tests/` là lớp tự động; các mục tương tác vẫn cần chạy trên EXE đã giải nén.

### Cài mới / đường dẫn / quyền (F)

- F01: giải nén ZIP công khai vào thư mục trống; không Python, không dữ liệu
  dev; mở app: hướng dẫn đủ, chọn game được, không báo đã có bản Việt giả.
- F02: đường dẫn có dấu/khoảng trắng; mở từ shortcut có working directory
  khác; hai bản giải nén ở hai ổ. Dữ liệu phải thuộc đúng bản app.
- F03: thư mục chỉ đọc/Program Files, Controlled Folder Access, ổ đầy,
  file đang khóa: báo lỗi; không âm thầm mất cấu hình hay cần quyền admin.
- F04: người dùng mở ngay trong ZIP, thiếu `_internal`, DLL bị antivirus
  cách ly, download chưa đủ: chẩn đoán/mở lại gói, không quy thành lỗi OCR.
- F05: chuyển thư mục portable sang ổ khác; giữ tên nhân vật, tiến trình,
  hiển thị và active generation; không giữ đường dẫn tuyệt đối máy cũ.
- F06: JSON cấu hình hỏng/UTF-8 BOM, active manifest hỏng/thiếu file:
  không ghi đè dữ liệu cá nhân; báo lỗi để phục hồi có kiểm soát.
- F07: mở hai lần cùng thư mục: hiện phiên cũ; hai thư mục riêng: phát hiện
  xung đột hotkey rõ, không xem lượt smoke bị trùng hotkey là đạt.

### Cập nhật app (A)

- A01: nâng cấp từ 1.0.3, 1.0.4, 1.1.0, 1.1.1, 1.1.2, 1.1.3; cấu hình
  mặc định và tùy chỉnh; từ alias Ludiglot.exe nếu có. Hash userdata và
  README/giấy phép trước/sau không đổi vì thay runtime.
- A02: app ẩn ở khay khi cập nhật; helper chạy nền nhưng EXE mới phải hiện;
  mở shortcut cũ sau cập nhật không tạo vòng hai phiên.
- A03: offline/DNS lỗi/timeout/GitHub rate limit/proxy lỗi, HTTP lỗi,
  HTML thay JSON, digest thiếu/sai, download cắt ngang: giữ EXE cũ.
- A04: cancel trước/trong/sau tải; ổ đầy khi giải nén; ZIP hỏng/traversal,
  duplicate case, symlink, gói chèn userdata/script; từ chối trước thay app.
- A05: EXE/DLL bị khóa ở giữa thay runtime; kiểm tra rollback cả EXE và
  `_internal`, không chỉ thông báo. Khởi động được bản cũ sau thất bại.
- A06: cùng phiên bản không cập nhật vòng lặp; bản cũ hơn không hạ cấp;
  SHA trên GitHub khớp ZIP đã thử; latest trỏ đúng tag đã phát hành.
- A07: cập nhật app xong nhưng còn cache revision 1/2/3: phải hiện đề nghị
  cập nhật lại bản dịch, không tuyên bố mọi dữ liệu tự được sửa bởi EXE.

### English / PAK / bản dịch (D)

- D01: cài sạch chưa có English: chặn tải ghép với hướng dẫn đúng nút/thư
  mục. Hủy chọn hoặc chọn nhầm thư mục phải chọn lại được.
- D02: launcher top-level 3.6 nhưng SD/common đã cài 3.7; UHD/SD mâu thuẫn,
  tải dở hoặc thiếu metadata: không suy đoán phiên bản từ tên ID cốt truyện.
- D03: đóng game trước quét; game đang mở/file khóa, PAK lớn, khóa AES
  thiếu, extractor thiếu/hỏng, mạng chậm: có tiến độ/timeout, giữ dữ liệu cũ.
- D04: quét English thành công → cập nhật bản dịch → restart → tra đúng
  câu. Quét lại sau update game cũng phải giữ metadata provenance/revision.
- D05: gói App-Dich cũ, VietHoa mới, seed DB rỗng, source masked, bảng
  1sthalf/2ndhalf, canonical PAK đúng/sai: không chạy EXE của gói bên ngoài.
- D06: hai bảng cùng bản Việt và khác bản Việt; cùng ID nhưng khác Chinese,
  sai bảng/phiên bản; duplicate cạnh tranh: chỉ nhận nguồn có bằng chứng.
- D07: English in/into, contraction cho phép; thêm phủ định, đổi danh từ,
  số lượng, CN rỗng/khác: không dùng fuzzy ngữ nghĩa để vượt chốt dữ liệu.
- D08: nâng cache cùng release qua revision cũ → 4; mất mạng/ổ đầy/cancel
  cuối bước/atomic replace lỗi: active cũ giữ nguyên, không cache nửa vời.
- D09: lỗi tải/check bản dịch không được che mất kết quả check app và ngược
  lại; release thiếu tài sản phải báo lỗi thay vì nút im lặng vô hiệu.
- D10: dựng lại dữ liệu thật, kiểm tra ít nhất `_90_1` và `_94_1` trong nhóm
  Main_Mengzhou_3_6; báo tổng tăng/giảm, không mất các bản Việt trước đã hợp lệ.
  File thật giữ trên máy QA, không gói cùng app công khai.

### Chụp / OCR / hiển thị (C)

- C01: chọn đúng HWND, Alt-Tab/cửa sổ khác che game vẫn đọc game; thu nhỏ,
  đóng/mở game, HWND tái sử dụng: không lặp ảnh cũ/không chụp desktop khác.
- C02: cửa sổ/borderless/fullscreen, resize, DPI/màn hình khác: chữ và ô che
  phải cùng vị trí; frame không nằm trọn màn phải báo đúng giới hạn.
- C03: cinematic 16:9, letterbox, chuyển qua lại, cảnh tối/fade, HUD chữ trắng:
  không cắt cảnh tối thành viewport giả, không dịch nhầm HUD.
- C04: thoại đang chạy chữ, một/nhiều dòng, câu ngắn có ngữ cảnh, câu dài
  bị cuộn; giữ font, xuống dòng; không đẩy đáp án/thoại lên hộp trên.
- C05: 1/2/nhiều lựa chọn, chưa highlight/đã highlight; NPC, thoại di chuyển,
  dấu ngoặc, tên nhân vật tùy chỉnh và placeholder; glossary ưu tiên hơn thoại.
- C06: không English OCR pack, chỉ en-GB, OneOCR không có/bị đổi phiên bản,
  worker crash/timeout, PPv5 thiếu model/runtime hỏng: không treo UI hay âm
  thầm dịch nhầm ngôn ngữ. Test fallback thật riêng, không chỉ mock.
- C07: ô che không giật khi OCR nhiễu cùng câu; đổi câu/đổi cảnh phải bỏ bản
  cũ; lớp dịch click-through, không giành focus, capture exclusion có control.
- C08: Alt+S có ảnh game+bản dịch và clipboard; ẩn panel, ổ đầy, clipboard
  bận, reader đóng/mở; không thay clipboard bằng ảnh lỗi hoặc desktop riêng tư.
- C09: CPU chậm/capture chậm/lookup chậm, worker hoàn thành sau pause/đổi
  game/đổi data: không backlog vô hạn hoặc ghi kết quả generation cũ.
- C10: chơi 30–60 phút, tối thiểu 100 lần đổi câu; CPU/RAM/handle không tăng
  không giới hạn. Ghi p50/p95 chụp/OCR/lookup/render thay vì hứa độ trễ cố định.

## Cách chạy lặp lại và cổng phát hành

1. Cài dependencies theo `requirements-desktop-lock.txt` bằng Python 3.12;
   không tự nâng Qt/OCR trong lúc sửa lỗi. Giữ README và giấy phép cũ.
2. Chạy `python tools/run_qa.py --output artifacts/qa-<lượt-mới>`.
   Runner cố định PYTHONPATH checkout, offscreen, temp mới; lưu JUnit/log,
   OS/runtime và kết quả. Lỗi môi trường/collection không được tính pass.
3. Chạy unit/integration toàn bộ, các ca fault injection và DPI 1/1.25/1.5/2.
4. Chạy `tools/verify_release_data.py` với gói/cache thật ở staging riêng;
   phải tra được cả hai ID báo lỗi và không mất nguồn đã hợp lệ trước đó.
5. Build EXE từ chính checkout đã thử. Đóng gói public không userdata/log/
   game data/OneOCR Microsoft assets. Thử bộ giải nén của updater thật.
6. Chạy EXE từ ZIP cuối trong thư mục sạch, working directory khác:
   `OssmocEcho.exe --config <fixture> --smoke-report <report> --qa-images <ảnh>`.
   Smoke phải ready, đúng app_version, OCR/lookup đúng, WGC/clipboard/lớp
   che đúng; timeout hoặc trùng hotkey là FAIL/BLOCKED, không đổi thành PASS.
7. Chạy cài sạch không fixture (F01), nâng cấp bản cũ (A01/A02/A05/A07).
   Chụp hash userdata trước/sau; chỉ tác động bản QA, không app người dùng.
8. Điền ma trận native; thiếu máy/VM phải ghi NOT_RUN trong release notes.
   Không gắn nhãn “đã thử mọi Win10/11”. Bugfix có thể phát hành với giới hạn
   xác nhận công khai; không tuyên bố đạt chứng nhận tương thích toàn diện.
9. Commit/tag → source ZIP đúng commit → draft release → upload ZIP+SHA →
   kiểm tra digest/size → public → kiểm tra endpoint mà updater thật sử dụng.

Mọi lỗi xác nhận mới phải thêm test tái hiện trước khi sửa. Khi chỉnh điều
kiện từ chối, cần cả ca chấp nhận và ca chống ghép nhầm; kiểm tra chênh lệch
toàn dataset, không chỉ tổng số bản Việt hoặc một câu đang mở.
