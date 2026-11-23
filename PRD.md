# TÀI LIỆU MÔ TẢ SẢN PHẨM (PRD TÓM GỌN CHO PO)
**Tên sản phẩm:** PH Ride-Hailing Competitor Intelligence AI Agent (`ph-competitor-agent`)  
**Mục tiêu:** Tự động hóa 100% việc theo dõi tính năng mới, bản cập nhật ứng dụng (iOS/Android), chính sách giá/chiết khấu của đối thủ (**Grab PH, Move It, Angkas, JoyRide PH, inDrive PH**) và quy định vận hành từ **LTFRB / DOTr** tại Philippines.

---

## 1. TỔNG QUAN LUỒNG HOẠT ĐỘNG (2 CHU KỲ TỰ ĐỘNG)

| Chu kỳ | Thời gian chạy | Nguồn quét & Xử lý | Đầu ra (Outputs) |
| :--- | :--- | :--- | :--- |
| **Luồng 1: Quét dữ liệu (48h/lần)** | 08:00 sáng (2 ngày/lần) | Quét 6 nguồn (Báo chí PH, Từ khóa LTFRB, Website đối thủ, Grab Press, Google Play PH, App Store PH) -> Lọc bỏ tin rác bằng AI (`Gemini 3.5 Flash`) -> Gán nhãn phân hệ (`Driver`, `Passenger`, `Both`, `Regulatory`, `Marketplace`). | 1. Ghi vào **Tab 1 (`Raw_Data`)** trên Google Sheets.<br/>2. Gửi tin nhắn **Telegram Digest 48h**. |
| **Luồng 2: Báo cáo Tổng hợp (2 tuần/lần)** | 08:30 sáng (Ngày 01 & 15 hàng tháng) | Lấy dữ liệu 14 ngày gần nhất -> Lọc trùng lặp -> Dùng AI (`Gemini 3.5 Flash`) tổng hợp xu hướng, lập bảng tính năng kèm mã trích dẫn `[1], [2]...` và đề xuất hành động cho PO. | 1. Cập nhật **Tab 2** & lưu trữ vào **Tab 3** trên Google Sheets.<br/>2. Xuất **File Báo cáo chuẩn Doc/Markdown**.<br/>3. Gửi **Email HTML** & **Telegram (kèm file)** cho PO. |

> **Quy tắc khi không có tin mới (No-Update Policy):** Nếu trong 48h hoặc 14 ngày đối thủ không có cập nhật mới, hệ thống **vẫn gửi thông báo xác nhận thị trường ổn định** qua Telegram, Email và ghi log vào Sheet để PO nắm được trạng thái hệ thống.

---

## 2. ĐẶC TẢ CHI TIẾT CÁC FORM KẾT QUẢ ĐẦU RA (OUTPUT SPECIFICATIONS)

### Đầu ra 1: File Google Sheets (`PH_RideHailing_Competitor_Tracking`) — Gồm 3 Tab

#### A. Form Tab 1: `Raw_Data` (Kho dữ liệu gốc — Cập nhật 2 ngày/lần)
| Timestamp_ISO | Date | Who | What | Side | Source_Name | Source_Title | Source_URL |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `2026-11-14` | `14 Nov 2026` | `Grab PH` | `GrabCar Group Ride now splits the fare automatically` | `Passenger` | `GadgetMatch` | `GrabCar Group Ride now splits the fare automatically` | `https://...` |
| `2026-11-14` | `14 Nov 2026` | `Angkas` | `Reduction of commission from 20% to 18% or less via tiered incentive system` | `Driver` | `Philstar` | `Angkas CEO announces commission reduction...` | `https://...` |

#### B. Form Tab 2: `Current_Report_Table` (Bảng báo cáo sạch kỳ hiện tại — Cập nhật 2 tuần/lần)
| Date | Who | What (Feature / Launch) | Side | Ref | Full_Reference_Citation |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `14 Nov 2026` | `Grab PH` | `GrabCar Group Ride now splits the fare automatically` | `Passenger` | `[1]` | `[1] GadgetMatch — *GrabCar Group Ride...* (14 Nov 2026): https://...` |
| `14 Nov 2026` | `Move It` | `v5.436.0 — Real-time ride tracking & sharing; enhanced safety features & ride receipts` | `Marketplace` | `[2]` | `[2] Apple App Store PH — *Move It iOS App v5.436.0 Release Notes* (14 Nov 2026): https://...` |

#### C. Form Tab 3: `BiWeekly_Overview_Archive` (Lưu trữ lịch sử báo cáo — Cập nhật 2 tuần/lần)
| Report_Date | Report_Header | Bottom_Line | Feature_Launches_Count | What_Id_Like_Us_To_Act_On | References_Block |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `2026-11-19` | `Feature & Product Launch Update, 05 Nov to 19 Nov 2026 \| Grab PH, inDrive...` | `• GrabCar launched automated fare splitting... [1]` | `19` | `• Benchmark commission cuts...` | `* [1] GadgetMatch...` |

---

### Đầu ra 2: Form Báo cáo Tổng hợp 2 Tuần/Lần (Xuất ra File `.md`/Doc & Email HTML)
Báo cáo gửi cho PO và Stakeholders qua **Email HTML** và **File đính kèm** luôn tuân thủ cố định 4 phần:

```markdown
## Feature & Product Launch Update, [Ngày bắt đầu] to [Ngày kết thúc] | [Danh sách đối thủ]

**1. Bottom Line & Key Product Takeaways (Điểm cốt lõi)**
• Tóm tắt các xu hướng sản phẩm, tính năng mới, thay đổi giá/chiết khấu và quy định LTFRB nổi bật nhất trong 14 ngày qua, gắn mã trích dẫn trực tiếp ở cuối câu [1], [2].

**2. Feature and Product Launches (Bảng ma trận tính năng)**
| Date | Who | What (Feature / Launch) | Side | Ref |
| :--- | :--- | :--- | :--- | :--- |
| 14 Nov 2026 | Grab PH | GrabCar Group Ride now splits the fare automatically | Passenger | [1] |
| 14 Nov 2026 | Angkas | Commission reduced from 20% to 18% via tiered system | Driver | [2] |

**3. What I'd Like Us to Act On (Đề xuất hành động cho đội ngũ Product & Ops)**
• **Driver Retention & Take Rates**: Đề xuất đối sách về chiết khấu/thưởng tài xế...
• **Passenger UX & Group Rides**: Đề xuất benchmark tính năng cho ứng dụng khách hàng...
• **Regulatory Compliance**: Đề xuất rà soát tuân thủ quy định mới của LTFRB...

**4. References & Citations (Danh mục nguồn trích dẫn)**
* [1] GadgetMatch — *GrabCar Group Ride now splits the fare automatically* (14 Nov 2026): https://...
* [2] Philstar — *Angkas CEO announces reduction of commission...* (14 Nov 2026): https://...
```

---

### Đầu ra 3: Form Tin nhắn Thông báo trên Telegram

#### A. Form Telegram Digest (Gửi 2 ngày/lần)
```text
🇵🇭 PH COMPETITOR UPDATE (X new updates)

• [Grab PH - Passenger] GrabCar Group Ride now splits the fare automatically (Source)
• [Angkas - Driver] Commission reduced from 20% to 18% via tiered incentive system (Source)
• [Move It - Both] v5.436.0 — Real-time ride sharing, enhanced safety & detailed receipts (Source)

📊 Open Google Sheets for details
```

#### B. Form Telegram Bi-Weekly Report (Gửi 2 tuần/lần)
```text
📑 Feature & Product Launch Update, 05 Nov to 19 Nov 2026 | Grab PH, inDrive PH, LTFRB, Angkas, JoyRide PH, Move It

Bottom Line & Key Product Takeaways:
• [Các gạch đầu dòng tóm tắt điểm cốt lõi kèm mã trích dẫn [1], [2]...]

What I'd Like Us to Act On:
• [Các gạch đầu dòng đề xuất hành động cho đội ngũ Product & Ops...]

📎 Full detailed product report (including Feature Matrix & References) attached below.
+ [Đính kèm trực tiếp file PH_Competitor_Report_YYYYMMDD.md vào khung chat]
```
