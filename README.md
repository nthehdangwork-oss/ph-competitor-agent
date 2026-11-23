# PH Ride-Hailing Competitor Agent (`ph-competitor-agent`)

Hệ thống AI Agent tự động theo dõi đối thủ ngành gọi xe tại thị trường Philippines (Grab PH, Move It, Angkas, JoyRide, inDrive và các cơ quan quản lý LTFRB/DOTr).
Agent tích hợp ma trận nguồn tin 4 tầng đa chiều:
1. **Nguồn chính thức đối thủ:** Grab Press Centre, trang tài xế Grab, inDrive Fair Services/Driver, JoyRide PH, Angkas, Move It.
2. **Cơ quan quản lý:** LTFRB và DOTr (Chính sách giá cước, phí đặt xe, quy chế thử nghiệm xe máy công nghệ, hạn mức tài xế).
3. **Báo chí uy tín Philippines:** BusinessWorld, Philstar, Rappler, GMA News, Philippine Daily Inquirer.
4. **Kho ứng dụng di động:** Google Play Store (Driver & Passenger apps) và Apple App Store (iOS Lookup API).

Hoạt động:
- **Chu kỳ 2 ngày/lần:** Quét toàn bộ 4 tầng nguồn tin, lọc trùng theo URL, chuẩn hóa bằng LLM, ghi vào tab `Raw_Data` trên Google Sheets và gửi tóm tắt qua Telegram (kể cả khi không có tin mới).
- **Chu kỳ 2 tuần/lần:** Lọc dữ liệu 14 ngày, đánh số tham chiếu `[1], [2]...`, tạo bảng sạch `Current_Report_Table`, lưu trữ lịch sử vào `BiWeekly_Overview_Archive`, xuất báo cáo Markdown/Doc và gửi qua Telegram + Email HTML (kể cả khi không có tin mới).

---

## 📁 Cấu trúc thư mục

```text
ph-competitor-agent/
├── .github/
│   └── workflows/
│       └── collect_2day.yml      # GitHub Actions quét tin 48h/lần
│       └── biweekly_overview.yml # GitHub Actions báo cáo tổng hợp 2 tuần/lần
├── .env.example                  # Mẫu cấu hình biến môi trường
├── .gitignore                    # Loại trừ file nhạy cảm và tạm thời
├── requirements.txt              # Danh sách thư viện Python cần thiết
├── notifier.py                   # Module thông báo Telegram và HTML Email
├── main.py                       # Luồng chính thu thập, lọc trùng và báo cáo
├── ARCHITECTURE.md               # Tài liệu kiến trúc Sheet, Form Doc & Sơ đồ DFD
└── README.md                     # Hướng dẫn cài đặt và vận hành
```

---

## 🚀 Hướng dẫn cài đặt & Chạy kiểm thử

### 1. Chuẩn bị môi trường & Cấu hình
1. Di chuyển vào thư mục dự án:
   ```bash
   cd ph-competitor-agent
   ```
2. Đặt file Google Service Account `service_account.json` (được cấp quyền chỉnh sửa Google Sheets) vào thư mục gốc của dự án `ph-competitor-agent/`.
3. Tạo file `.env` từ file mẫu `.env.example`:
   ```bash
   cp .env.example .env
   ```
4. Điền các thông tin trong file `.env`:
   - `GEMINI_API_KEY`: API Key Google Gemini (lấy miễn phí tại `https://aistudio.google.com/apikey`) để chạy `gemini-3.5-flash`.
   - `SHEET_NAME` & `SHEET_URL`: Tên và đường link Google Sheets.
   - `TELEGRAM_BOT_TOKEN` & `TELEGRAM_CHAT_ID`: Token bot Telegram và Chat ID nhận tin.
   - `SENDER_EMAIL`, `RECEIVER_EMAIL`, `EMAIL_APP_PASSWORD`: Tài khoản gửi/nhận email báo cáo.

### 2. Cài đặt thư viện
```bash
pip install -r requirements.txt
```

### 3. Lệnh chạy kiểm thử

- **Kiểm thử luồng thu thập dữ liệu (Chu kỳ 2 ngày):**
  ```bash
  python main.py collect
  ```
  *Luồng này sẽ quét RSS Google News PH + Play Store, phân tích trích xuất dữ liệu, lưu vào tab `Raw_Data` và gửi thông báo qua Telegram.*

- **Kiểm thử luồng xuất báo cáo Overview (Chu kỳ 2 tuần):**
  ```bash
  python main.py overview
  ```
  *Luồng này sẽ tổng hợp dữ liệu 14 ngày, xuất file `PH_Competitor_Report_YYYYMMDD.md`, cập nhật tab `Current_Report_Table` & `BiWeekly_Overview_Archive`, gửi file báo cáo qua Telegram và gửi Email HTML.*

---

## ⚙️ Tự động hóa với GitHub Actions
File workflow `.github/workflows/ph_agent.yml` đã được thiết lập sẵn 2 lịch trình tự động:
- `cron: '0 1 1-31/2 * *'`: Chạy quét tin mỗi 2 ngày lúc 08:00 sáng (giờ VN / UTC+7).
- `cron: '30 1 1,15 * *'`: Chạy tổng hợp báo cáo 2 tuần vào ngày 1 và 15 hàng tháng lúc 08:30 sáng (giờ VN / UTC+7).
- Hỗ trợ kích hoạt thủ công qua `workflow_dispatch` (chọn mode `collect` hoặc `overview`).

**Lưu ý khi đưa lên GitHub Repository:** Cấu hình các Repository Secrets tương ứng (`GEMINI_API_KEY`, `GCP_SA_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `SHEET_URL`, `SENDER_EMAIL`, `RECEIVER_EMAIL`, `EMAIL_APP_PASSWORD`).
