import os, json, re
import base64
from pathlib import Path
from dotenv import load_dotenv
import pypdf
from langchain_core.messages import HumanMessage, SystemMessage
from pipeline.llm_manager import master_llm, gemini_flash_native

load_dotenv()

llm = master_llm

# ============================================================
# ĐỊNH DẠNG FILE ĐƯỢC HỖ TRỢ
# ============================================================
PDF_EXTENSIONS   = {".pdf"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff", ".tif"}

MIME_TYPE_MAP = {
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".webp": "image/webp",
    ".gif":  "image/gif",
    ".bmp":  "image/bmp",
    ".tiff": "image/tiff",
    ".tif":  "image/tiff",
}

# ============================================================
# UNIT VALIDATION MAP
# Kiểm tra unit có hợp lệ cho từng loại hóa đơn không
# ============================================================
VALID_UNITS = {
    "electricity": ["kwh", "mwh"],
    "diesel":      ["lít", "lit", "l", "liter"],
    "petrol":      ["lít", "lit", "l", "liter"],
    "gasoline":    ["lít", "lit", "l", "liter"],
    "xăng":        ["lít", "lit", "l", "liter"],
    "gas":         ["kg", "kilogram"],
    "lpg":         ["kg", "kilogram"],
    "transport":   ["km", "tấn", "ton", "tấn.km"],
    "logistics":   ["km", "tấn", "ton", "tấn.km"],
    "shipping":    ["km", "tấn", "ton", "tấn.km"],
    "van_chuyen":  ["km", "tấn", "ton", "tấn.km"],
    "waste":       ["tấn", "ton", "kg"],
    "rac_thai":    ["tấn", "ton", "kg"],
    "travel":      ["km", "vé", "ve", "chuyến", "chuyen", "lượt", "luot"],
    "business_travel": ["km", "vé", "ve", "chuyến", "chuyen", "lượt", "luot"],
    "cong_tac":        ["km", "vé", "ve", "chuyến", "chuyen", "lượt", "luot"],
    "industrial_gas":  ["chai", "binh", "bình", "kg", "lit"], # Bổ sung thêm chữ có dấu
    "refrigerant":     ["binh", "bình", "kg"],
    # water, other: không tính phát thải, không cần validate
}

EXTRACTION_SYSTEM_PROMPT = """
Bạn là một Kiểm toán viên Dữ liệu ESG cấp cao.
Nhiệm vụ: Đọc nội dung hóa đơn, trích xuất dữ liệu và TỰ ĐÁNH GIÁ độ tin cậy của thông tin vừa đọc.

QUAN TRỌNG:
- Chỉ trả về JSON thuần túy, tuyệt đối không dùng markdown, không giải thích thêm.
- invoice_type phân loại thành: electricity, diesel, petrol, gas, water,
  transport (vận chuyển), waste (rác thải), travel (công tác phí), industrial_gas (khí công nghiệp), refrigerant (khí gas lạnh), other.
- is_signed: true nếu hóa đơn có chữ ký điện tử (thường ghi "Đã được ký điện tử bởi" hoặc
  có mã xác thực CQT). Hóa đơn vận chuyển/công tác/rác thải thường KHÔNG có chữ ký điện tử
  → đánh dấu is_signed: false, NHƯNG vẫn trích xuất đầy đủ dữ liệu.
- Với hóa đơn vận chuyển: cố tìm cargo_tons (tải trọng hàng tấn) và distance_km (km).
- Với hóa đơn công tác: cố tìm distance_km và num_passengers.
- unit phải đúng với loại hàng: điện→kWh, diesel/xăng→lít, gas→kg, rác thải→tấn, vận chuyển→km hoặc tấn.

BỘ QUY TẮC ĐÁNH GIÁ ĐỘ TIN CẬY (SELF-REFLECTION):
Bạn phải đánh giá chất lượng đầu vào để báo cáo cho hệ thống biết có cần con người can thiệp hay không.
1. "confidence_score" (0-100):
   - 95-100: Thông tin rõ nét, đầy đủ số lượng, đơn vị, không có dấu hiệu tẩy xóa.
   - 75-94: Đọc được nhưng ảnh hơi mờ, định dạng bảng biểu lộn xộn, hoặc thiếu một số thông tin phụ.
   - < 75: Ảnh nhòe, mất góc, bị che khuất phần số liệu quan trọng, hoặc có sự mâu thuẫn (VD: Đơn giá x Số lượng != Tổng tiền).
2. "needs_review" (boolean): BẮT BUỘC trả về true NẾU confidence_score < 80 HOẶC bạn phải "đoán" bất kỳ con số nào.
3. "flag_reason" (string|null): Nếu needs_review là true, giải thích cực kỳ ngắn gọn (VD: "Mờ phần số lượng tiêu thụ", "Thiếu đơn vị tính", "Tổng tiền không khớp"). Nếu false, để null

JSON schema bắt buộc:
{
  "invoice_type": string,        # Loại chính của hóa đơn
  "company_name": string,    # Tên công ty xuất hóa đơn (nếu có)
  "month": int, 
  "year": int,  
  "quantity": float|null,             # Dùng cho hóa đơn 1 dòng. Nếu nhiều dòng: để null
  "unit": string|null,                # Dùng cho hóa đơn 1 dòng. Nếu nhiều dòng: để null
  "total_amount_vnd": float,     # Thành tiền cuối cùng (đã bao gồm thuế nếu có). Nếu không tìm được, hãy ước lượng bằng cách nhân quantity x unit_price hoặc tổng các line_items.
  "is_signed": boolean, 
  "line_items": [                # QUAN TRỌNG: Liệt kê TẤT CẢ các dòng hàng hóa
    {
      "description": string,     # Tên hàng hóa (VD: "Oxy bình 40L", "CO2 bình 40L", "Argon bình 40L")
      "unit": string,            # Đơn vị tính  (VD: "chai", "binh", "kg", "lit")
      "quantity": float,         # Số lượng 
      "unit_price": float,       # Đơn giá  
      "amount_vnd": float        # Thành tiền   
    }
  ],
  "cargo_tons": float|null, 
  "distance_km": float|null,    
  "num_passengers": int|null,   
  "vehicle_type": string|null,  
  "transport_mode": string|null,    
  "treatment_method": string|null,  
  "notes": string|null, 
  "confidence_score": int,       # Đánh giá độ tự tin của bạn từ 0 đến 100 khi đọc hóa đơn này.
  "needs_review": boolean,       # Trả về true nếu ảnh mờ, thông tin bị che khuất hoặc confidence_score < 80.
  "flag_reason": string|null     # Nếu needs_review là true, giải thích ngắn gọn lý do (VD: "Mờ phần số lượng", "Thiếu đơn vị tính").
}

=== VÍ DỤ 1: Hóa đơn điện ===
Input: "...EVN SPC...tháng 05/2024...42,000 kWh...đã ký điện tử bởi EVNSPC..."
Output:
{
  "invoice_type": "electricity",
  "company_name": "EVN SPC",
  "month": 5, "year": 2024,
  "quantity": 42000, "unit": "kWh",
  "total_amount_vnd": 77700000,
  "is_signed": true,
  "cargo_tons": null, "distance_km": null, "num_passengers": null,
  "vehicle_type": null, "transport_mode": null, "treatment_method": null,
  "notes": null,
  "confidence_score": 98,
  "needs_review": false,
  "flag_reason": null
}

=== VÍ DỤ 2: Hóa đơn vận chuyển (không có chữ ký điện tử GTGT) ===
Input: "...Công ty Vận tải ABC...chở 8 tấn hàng...Hà Nội → HCM 1700km...xe tải lớn...5,200,000đ..."
Output:
{
  "invoice_type": "transport",
  "company_name": "Công ty Vận tải ABC",
  "month": 3, "year": 2024,
  "quantity": 1700, "unit": "km",
  "total_amount_vnd": 5200000,
  "is_signed": false,
  "cargo_tons": 8.0, "distance_km": 1700.0, "num_passengers": null,
  "vehicle_type": "truck_large", "transport_mode": null, "treatment_method": null,
  "notes": "Tuyến HN-HCM",
  "confidence_score": 85,
  "needs_review": false,
  "flag_reason": null
}

=== VÍ DỤ 3: Hóa đơn rác thải ===
Input: "...Công ty TNHH Môi trường Xanh...thu gom 2.5 tấn rác công nghiệp...chôn lấp..."
Output:
{
  "invoice_type": "waste",
  "company_name": "Công ty TNHH Môi trường Xanh",
  "month": 4, "year": 2024,
  "quantity": 2.5, "unit": "tấn",
  "total_amount_vnd": 1250000,
  "is_signed": false,
  "cargo_tons": null, "distance_km": null, "num_passengers": null,
  "vehicle_type": null, "transport_mode": null, "treatment_method": "landfill",
  "notes": null,
  "confidence_score": 90,
  "needs_review": false,
  "flag_reason": null
}

=== VÍ DỤ 4: Hóa đơn nhiều dòng hàng (khí công nghiệp) ===
Input: "...Oxy bình 40L...12 chai...CO2 bình 40L...8 chai...Argon 40L...20 chai..."
Output:
{
  "invoice_type": "industrial_gas",
  "company_name": "Công ty TNHH MTV TM DV Phục Hưng Vàng",
  "month": 2, "year": 2026,
  "quantity": null,
  "unit": null,
  "total_amount_vnd": 13712000,
  "is_signed": true,
  "line_items": [
    {"description": "Khí Oxy bình 40L", "unit": "chai", "quantity": 12, "unit_price": 200000, "amount_vnd": 2400000},
    {"description": "Khí CO2 bình 40L", "unit": "chai", "quantity": 8,  "unit_price": 361111, "amount_vnd": 2888888},
    {"description": "Khí Argon 40L",    "unit": "chai", "quantity": 20, "unit_price": 370370, "amount_vnd": 7407407}
  ],
  "cargo_tons": null, "distance_km": null, "num_passengers": null,
  "vehicle_type": null, "transport_mode": null, "treatment_method": null,
  "notes": "Hóa đơn khí công nghiệp nhiều dòng",
  "confidence_score": 92,
  "needs_review": false,
  "flag_reason": null
}

=== VÍ DỤ 5: HÓA ĐƠN VẬN CHUYỂN BỊ MỜ SỐ LƯỢNG ===
Input: [Ảnh chụp bằng điện thoại hơi rung] "...Vận tải ABC...chở [mờ] tấn hàng...Hà Nội → HCM 1700km...5,200,000đ..."
Output:
{
  "invoice_type": "transport",
  "company_name": "Công ty Vận tải ABC",
  "month": 3, "year": 2024,
  "quantity": 1700, "unit": "km",
  "total_amount_vnd": 5200000,
  "is_signed": false,
  "line_items": [],
  "cargo_tons": null,
  "distance_km": 1700.0, 
  "num_passengers": null,
  "vehicle_type": null, "transport_mode": null, "treatment_method": null,
  "notes": "Tuyến HN-HCM",
  "confidence_score": 65,
  "needs_review": true,
  "flag_reason": "Ảnh rung mờ, không thể đọc chính xác tải trọng hàng hóa (cargo_tons)."
}
"""


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def _clean_json_response(raw: str) -> str:
    raw = raw.strip()
    if "```" in raw:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.IGNORECASE)
        if match:
            raw = match.group(1).strip()
        else:
            raw = raw.replace("```json", "").replace("```", "").strip()
    start = raw.find("{")
    end   = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw = raw[start:end + 1]
    return raw.strip()


def _validate_unit(invoice_type: str, unit: str) -> tuple[bool, str]:
    """
    Kiểm tra unit có hợp lệ không.
    Trả về (is_valid, warning_message).
    """
    key = invoice_type.lower()
    if key not in VALID_UNITS:
        return True, ""  # Loại không validate (water, other...)

    unit_lower = unit.lower()
    allowed = VALID_UNITS[key]
    if not any(u in unit_lower for u in allowed):
        warning = (
            f"Unit '{unit}' có thể không hợp lệ cho invoice_type '{invoice_type}'. "
            f"Đơn vị hợp lệ: {allowed}"
        )
        return False, warning
    return True, ""


def _post_process(data: dict, source_file: str) -> dict:
    """
    Validate + chuẩn hóa dữ liệu sau khi LLM trích xuất.
    Tích hợp Phễu lọc 3 tầng: Chặn Cứng (Hard Block) và Cảnh Báo Mềm (Soft Warning).
    """
    # ---------------------------------------------------------
    # 1. THU THẬP CHỈ SỐ TỰ ĐÁNH GIÁ (SELF-REFLECTION) TỪ AI
    # ---------------------------------------------------------
    confidence = data.get("confidence_score", 100)
    
    # Ép kiểu an toàn đề phòng AI trả về string thay vì int
    if isinstance(confidence, str) and confidence.isdigit():
        confidence = int(confidence)
    elif not isinstance(confidence, (int, float)):
        confidence = 100 
        
    needs_review = data.get("needs_review", False)
    flag_reason = data.get("flag_reason") or ""

    # ---------------------------------------------------------
    # 2. LOGIC CHẶN CỨNG (HARD BLOCK) - Ngăn chặn "Garbage In"
    # ---------------------------------------------------------
    if confidence < 40:
        raise ValueError(
            f"❌ TỪ CHỐI TÀI LIỆU: Ảnh '{os.path.basename(source_file)}' "
            f"quá mờ hoặc không thể nhận diện (Độ tin cậy: {confidence}%). "
            f"AI báo cáo: {flag_reason}. Vui lòng tải lên bản rõ nét hơn."
        )

    # ---------------------------------------------------------
    # 3. LOGIC CẢNH BÁO MỀM (SOFT WARNING) - Đẩy lên Trạm kiểm duyệt
    # ---------------------------------------------------------
    if 40 <= confidence < 80:
        needs_review = True
        if not flag_reason:
            flag_reason = f"Độ tin cậy ở mức trung bình ({confidence}%). Cần người dùng rà soát."

    # ---------------------------------------------------------
    # 4. VALIDATE CẤU TRÚC VÀ LỖI NGỮ NGHĨA (SEMANTIC CHECKS)
    # ---------------------------------------------------------
    has_line_items = bool(data.get("line_items"))
    if has_line_items:
        required_fields = ["invoice_type", "month", "year", "is_signed"]
    else:
        required_fields = ["invoice_type", "month", "year", "quantity", "unit", "is_signed"]

    missing = [f for f in required_fields if data.get(f) is None]
    if missing:
        # Tư duy HITL: Thay vì sập luồng, ta cắm cờ để con người điền bù vào chỗ thiếu!
        needs_review = True
        flag_reason = f"{flag_reason} | Bị khuyết thông tin: {missing}".strip(" |")

    # Validate giá trị số lượng
    if not has_line_items and data.get("quantity") is not None:
        try:
            data["quantity"] = float(data["quantity"])
        except (TypeError, ValueError):
            needs_review = True
            flag_reason = f"{flag_reason} | Sai định dạng số lượng: {data.get('quantity')}".strip(" |")

    # Validate đơn vị tính (Unit)
    unit_warnings = []

    if has_line_items:
        # Hóa đơn NHIỀU DÒNG: Lặp qua từng mặt hàng để kiểm tra
        for idx, item in enumerate(data.get("line_items", [])):
            item_unit = item.get("unit", "")
            is_valid, warn_msg = _validate_unit(data.get("invoice_type", ""), item_unit)
            if not is_valid:
                # Ghi nhận lỗi cụ thể ở dòng nào để đẩy lên UI cho con người dễ sửa
                unit_warnings.append(f"Dòng {idx+1}: {warn_msg}")
    else:
        # Hóa đơn 1 DÒNG: Kiểm tra unit tổng
        global_unit = data.get("unit", "")
        if global_unit:
            is_valid, warn_msg = _validate_unit(data.get("invoice_type", ""), global_unit)
            if not is_valid:
                unit_warnings.append(warn_msg)

    # Nếu phát hiện bất kỳ lỗi đơn vị nào (ở tổng hoặc ở dòng) -> Cắm cờ Review
    if unit_warnings:
        data["unit_warning"] = True
        needs_review = True
        # Gộp tất cả các lỗi lại thành 1 chuỗi lý do
        flag_reason = f"{flag_reason} | Lỗi đơn vị tính: {'; '.join(unit_warnings)}".strip(" |")
    else:
        data["unit_warning"] = False

    # Logic chữ ký điện tử đặc thù của hóa đơn Scope 1 & 2
    scope3_types = {"transport", "logistics", "shipping", "van_chuyen",
                    "waste", "rac_thai", "travel", "business_travel", "cong_tac"}
    invoice_type_lower = data.get("invoice_type", "").lower()

    if data.get("is_signed") is False:
        if invoice_type_lower in scope3_types:
            existing_notes = data.get("notes") or ""
            data["notes"]  = f"[SCOPE3 - không có chữ ký GTGT] {existing_notes}".strip()
        else:
            raise ValueError(
                f"❌ VI PHẠM ESG: Hóa đơn điện/năng lượng '{os.path.basename(source_file)}' "
                "chưa được ký điện tử hợp lệ. Bị loại bỏ để đảm bảo tính minh bạch."
            )

    # ---------------------------------------------------------
    # 5. ĐÓNG GÓI TRẢ VỀ STATE
    # ---------------------------------------------------------
    data["source_file"] = os.path.basename(source_file)
    data["needs_review"] = needs_review
    data["flag_reason"] = flag_reason
    
    return data


def _call_llm_with_text(text: str) -> dict:
    """Gọi LLM với input là text thuần (dành cho PDF) qua FreeLLMAPI."""
    messages = [
        SystemMessage(content=EXTRACTION_SYSTEM_PROMPT),
        HumanMessage(content=f"Hóa đơn cần trích xuất:\n\n{text}")
    ]
    try:
        # Gọi qua proxy để tiết kiệm token
        response = master_llm.invoke(messages)
        raw_json = response.content.strip()
    except Exception as e:
        raise ValueError(f"Sập kết nối API Proxy. Chi tiết: {str(e)}")
        
    # Tái sử dụng hàm dọn dẹp JSON tối ưu cũ của ông
    cleaned = _clean_json_response(raw_json)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM trả về JSON không hợp lệ: {e}\nRaw: {raw_json[:300]}...")


def _call_llm_with_image(image_base64: str, mime_type: str) -> dict:
    """Hàm xử lý ẢNH CHỤP hóa đơn -> ÉP BUỘC đi trực tiếp bằng native model có thị giác (Vision)"""
    messages = [
        SystemMessage(content=EXTRACTION_SYSTEM_PROMPT),
        HumanMessage(
            content=[
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{image_base64}"
                    }
                },
                {
                    "type": "text",
                    "text": (
                        "Đây là ảnh chụp hóa đơn. "
                        "Hãy đọc toàn bộ nội dung trong ảnh và trích xuất dữ liệu theo schema JSON đã yêu cầu. "
                        "Chỉ trả về JSON thuần túy."
                    )
                }
            ]
        )
    ]
    
    # BẮT BUỘC: Dùng gemini_flash_native trực tiếp, không gọi qua proxy text-only
    response = gemini_flash_native.invoke(messages) 
    
    # ... đoạn xử lý chuỗi JSON phía dưới giữ nguyên ...
    cleaned = response.content.strip()
    if "```json" in cleaned:
        cleaned = re.search(r"```json\s*(.*?)\s*```", cleaned, re.DOTALL).group(1)
    return json.loads(cleaned)

# ============================================================
# PUBLIC API
# ============================================================

def extract_pdf_data(pdf_path: str) -> dict:
    """
    Đọc PDF và dùng LLM trích xuất dữ liệu.
    """
    try:
        reader   = pypdf.PdfReader(pdf_path)
        raw_text = ""
        for page in reader.pages:
            raw_text += page.extract_text() + "\n"
    except Exception as e:
        raise ValueError(f"Không đọc được PDF {pdf_path}: {e}")
 
    if not raw_text.strip():
        raise ValueError(f"PDF không có text layer: {pdf_path}")
 
    data = _call_llm_with_text(raw_text)
    return _post_process(data, pdf_path)


def extract_image_data(image_path: str) -> dict:
    """
    Đọc file ảnh (JPG, PNG, WEBP, GIF, BMP, TIFF) và dùng LLM Vision trích xuất dữ liệu.
    """
    ext = Path(image_path).suffix.lower()
    if ext not in IMAGE_EXTENSIONS:
        raise ValueError(
            f"Định dạng ảnh không được hỗ trợ: '{ext}'. "
            f"Các định dạng hỗ trợ: {sorted(IMAGE_EXTENSIONS)}"
        )
 
    mime_type = MIME_TYPE_MAP.get(ext, "image/jpeg")
 
    # Đọc file và encode base64
    try:
        with open(image_path, "rb") as f:
            image_bytes  = f.read()
        image_base64 = base64.b64encode(image_bytes).decode("utf-8")
    except Exception as e:
        raise ValueError(f"Không đọc được file ảnh {image_path}: {e}")
 
    # Kiểm tra kích thước ảnh (giới hạn ~20MB base64 ≈ 15MB file gốc)
    size_mb = len(image_bytes) / (1024 * 1024)
    if size_mb > 15:
        raise ValueError(
            f"File ảnh quá lớn ({size_mb:.1f}MB). "
            f"Giới hạn tối đa: 15MB. "
            f"Vui lòng nén ảnh trước khi upload."
        )
 
    data = _call_llm_with_image(image_base64, mime_type)
    return _post_process(data, image_path)


def extract_invoice_data(file_path: str) -> dict:
    """
    Hàm tổng hợp: tự nhận dạng định dạng file và gọi đúng extractor.
 
    Hỗ trợ:
        - PDF:  .pdf
        - Ảnh: .jpg .jpeg .png .webp .gif .bmp .tiff .tif
 
    Đây là hàm KHUYẾN NGHỊ dùng trong pipeline mới.
    """
    ext = Path(file_path).suffix.lower()
 
    if ext in PDF_EXTENSIONS:
        return extract_pdf_data(file_path)
 
    elif ext in IMAGE_EXTENSIONS:
        return extract_image_data(file_path)
 
    else:
        raise ValueError(
            f"Định dạng file không được hỗ trợ: '{ext}'.\n"
            f"  - PDF: {sorted(PDF_EXTENSIONS)}\n"
            f"  - Ảnh: {sorted(IMAGE_EXTENSIONS)}"
        )