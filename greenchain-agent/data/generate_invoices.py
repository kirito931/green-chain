"""
generate_invoices.py — GreenChain
Sinh hóa đơn VAT mẫu đa dạng loại phát thải, format giống hóa đơn điện tử thật.

Các loại hóa đơn được sinh:
  - Điện (Scope 2)
  - Diesel / Xăng (Scope 1)
  - LPG / Gas (Scope 1)
  - Khí công nghiệp nhiều dòng: Oxy, CO2, Argon (Scope 1 process)
  - Vận chuyển thuê ngoài (Scope 3 Cat 4)
  - Xử lý rác thải (Scope 3 Cat 5)
  - Công tác phí (Scope 3 Cat 6)

Chữ ký điện tử: xen kẽ có/không theo thứ tự luân phiên (không random).

Output:
  - data/invoices/*.pdf   ← PDF gốc (giữ nguyên như cũ)
  - data/images/*.png     ← Ảnh PNG chụp lại từng hóa đơn (MỚI)

Cách chạy:
  python generate_invoices.py           # sinh cả PDF + ảnh
  python generate_invoices.py --no-img  # chỉ sinh PDF (nhanh hơn)
"""

import os
import io
import uuid
import itertools
import argparse
import tempfile

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Table, TableStyle, Paragraph,
    Spacer, HRFlowable
)
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# ============================================================
# SETUP
# ============================================================
os.makedirs("data/invoices", exist_ok=True)
os.makedirs("data/images",   exist_ok=True)   # ← thư mục ảnh mới

# Font hỗ trợ tiếng Việt — dùng DejaVu có sẵn trong reportlab
try:
    pdfmetrics.registerFont(TTFont("DejaVu",      "DejaVuSans.ttf"))
    pdfmetrics.registerFont(TTFont("DejaVu-Bold", "DejaVuSans-Bold.ttf"))
    FONT_NORMAL = "DejaVu"
    FONT_BOLD   = "DejaVu-Bold"
except Exception:
    FONT_NORMAL = "Helvetica"
    FONT_BOLD   = "Helvetica-Bold"

W, H = A4   # 595 x 842 pt

# ============================================================
# STYLES
# ============================================================
def _style(name, font=None, size=9, bold=False, align=TA_LEFT,
           color=colors.black, leading=None):
    f = font or (FONT_BOLD if bold else FONT_NORMAL)
    return ParagraphStyle(
        name,
        fontName=f,
        fontSize=size,
        textColor=color,
        alignment=align,
        leading=leading or size * 1.35,
        wordWrap="CJK",
    )

S_TITLE    = _style("title",    size=14, bold=True,  align=TA_CENTER)
S_SUBTITLE = _style("subtitle", size=9,              align=TA_CENTER)
S_LABEL    = _style("label",    size=8,  bold=True)
S_VALUE    = _style("value",    size=8)
S_SMALL    = _style("small",    size=7,              align=TA_CENTER, color=colors.grey)
S_SIGN_RED = _style("sign_red", size=8,  bold=True,  align=TA_CENTER, color=colors.red)
S_SIGN_GRY = _style("sign_gry", size=8,              align=TA_CENTER, color=colors.grey)
S_TH       = _style("th",       size=8,  bold=True,  align=TA_CENTER)
S_TD_C     = _style("td_c",     size=8,              align=TA_CENTER)
S_TD_R     = _style("td_r",     size=8,              align=TA_RIGHT)
S_TD_L     = _style("td_l",     size=8)
S_TOTAL_L  = _style("tot_l",    size=8,  bold=True,  align=TA_RIGHT)
S_TOTAL_R  = _style("tot_r",    size=8,  bold=True,  align=TA_RIGHT)

# ============================================================
# NGƯỜI MUA CỐ ĐỊNH
# ============================================================
BUYER = {
    "name":     "CONG TY CO PHAN ALTERNO ENERGY",
    "tax_code": "0402271874",
    "address":  "Tang 3, Toa nha van phong Indochina Riverside, 74 Bach Dang, Hai Chau, Da Nang",
}

# ============================================================
# DANH MỤC HÓA ĐƠN
# ============================================================
def _make_catalog(month: str, year: int) -> list[dict]:
    """Trả về danh sách tất cả loại hóa đơn cho tháng/năm đã cho."""
    return [

        # ── SCOPE 2 ──────────────────────────────────────────
        {
            "prefix":      f"electricity_{month}_{year}",
            "inv_type":    "electricity",
            "seller_name": "TONG CONG TY DIEN LUC MIEN NAM - EVN SPC",
            "seller_tax":  "0300942001",
            "seller_addr": "180 Nguyen Thi Minh Khai, Q.3, TP.HCM",
            "serial_no":   f"1C{str(year)[-2:]}TEE",
            "items": [
                ("Dien nang tieu thu thang " + month, "kWh", 45_000, 1_978),
            ],
        },

        # ── SCOPE 1 — Diesel ─────────────────────────────────
        {
            "prefix":      f"diesel_{month}_{year}",
            "inv_type":    "diesel",
            "seller_name": "CONG TY XANG DAU PETROLIMEX - CN DA NANG",
            "seller_tax":  "0100107370",
            "seller_addr": "99 Dien Bien Phu, Thanh Khe, Da Nang",
            "serial_no":   f"1C{str(year)[-2:]}TDE",
            "items": [
                ("Nhien lieu Diesel B5", "LIT", 1_250, 21_500),
            ],
        },

        # ── SCOPE 1 — Xăng ───────────────────────────────────
        {
            "prefix":      f"petrol_{month}_{year}",
            "inv_type":    "petrol",
            "seller_name": "CONG TY XANG DAU PETROLIMEX - CN DA NANG",
            "seller_tax":  "0100107370",
            "seller_addr": "99 Dien Bien Phu, Thanh Khe, Da Nang",
            "serial_no":   f"1C{str(year)[-2:]}TXA",
            "items": [
                ("Xang Ron 95-III",  "LIT",  320, 24_320),
                ("Xang Ron 92-II",   "LIT",  180, 23_150),
            ],
        },

        # ── SCOPE 1 — LPG / Gas ──────────────────────────────
        {
            "prefix":      f"lpg_{month}_{year}",
            "inv_type":    "gas",
            "seller_name": "CONG TY CP KHI DAU KHI MIEN TRUNG - PETROVIETNAM GAS",
            "seller_tax":  "0312000123",
            "seller_addr": "Khu CN Lien Chieu, Da Nang",
            "serial_no":   f"1C{str(year)[-2:]}TGA",
            "items": [
                ("Khi LPG cong nghiep binh 45kg", "BINH", 20, 870_000),
                ("Khi LPG cong nghiep binh 12kg", "BINH", 15, 235_000),
            ],
        },

        # ── SCOPE 1 — Khí công nghiệp ────────────────────────
        {
            "prefix":      f"industrial_gas_{month}_{year}",
            "inv_type":    "industrial_gas",
            "seller_name": "CONG TY TNHH MTV TM DV PHUC HUNG VANG",
            "seller_tax":  "0312643824",
            "seller_addr": "61/21 Hoang Cam, Linh Xuan, Thu Duc, TP.HCM",
            "serial_no":   f"1C{str(year)[-2:]}TPH",
            "items": [
                ("Khi Oxy binh 40L",  "CHAI", 12,  200_000),
                ("Khi CO2 binh 40L",  "CHAI",  8,  361_111),
                ("Khi Argon 40L",     "CHAI", 20,  370_370),
            ],
        },

        # ── SCOPE 1 — Refrigerant / Gas lạnh ─────────────────
        {
            "prefix":      f"refrigerant_{month}_{year}",
            "inv_type":    "refrigerant",
            "seller_name": "CONG TY CP KY THUAT LANH VIET NAM - VIETTRANS COOL",
            "seller_tax":  "0310456789",
            "seller_addr": "45 Tran Phu, Hai Chau, Da Nang",
            "serial_no":   f"1C{str(year)[-2:]}TLC",
            "items": [
                ("Gas lanh R-410A binh 11.3kg (HFC - dap ung lo dieu hoa)", "BINH", 4, 1_250_000),
                ("Gas lanh R-32 binh 10kg (HFC - thay the R-22)",           "BINH", 2,   980_000),
            ],
        },

        # ── SCOPE 3 Cat 4 — Vận chuyển thuê ngoài ────────────
        {
            "prefix":      f"transport_{month}_{year}",
            "inv_type":    "transport",
            "seller_name": "CONG TY TNHH VAN TAI GREENLOG MIEN TRUNG",
            "seller_tax":  "0401234567",
            "seller_addr": "Khu CN Hoa Khanh, Lien Chieu, Da Nang",
            "serial_no":   f"1C{str(year)[-2:]}TVT",
            "items": [
                ("Dich vu van chuyen hang hoa - Xe tai lon (>16 tan) - 8 tan hang - tuyen Da Nang - Ha Noi (790km)",
                 "CHUYEN", 2, 4_800_000),
                ("Dich vu van chuyen hang hoa - Xe tai vua (5 tan) - 3 tan hang - tuyen Da Nang - Hue (100km)",
                 "CHUYEN", 3,   650_000),
            ],
        },

        # ── SCOPE 3 Cat 5 — Xử lý rác thải ──────────────────
        {
            "prefix":      f"waste_{month}_{year}",
            "inv_type":    "waste",
            "seller_name": "CONG TY TNHH MOI TRUONG XANH DA NANG - GREENVN",
            "seller_tax":  "0401987654",
            "seller_addr": "Bai xu ly chat thai Khanh Son, Lien Chieu, Da Nang",
            "serial_no":   f"1C{str(year)[-2:]}TMT",
            "items": [
                ("Thu gom xu ly rac thai cong nghiep thong thuong - chon lap hop ve sinh",
                 "TAN", 2.5, 850_000),
                ("Thu gom xu ly rac thai nguy hai (dau tham, giet) - tieu huy",
                 "KG",  120,  12_000),
            ],
        },

        # ── SCOPE 3 Cat 6 — Công tác phí ─────────────────────
        {
            "prefix":      f"travel_{month}_{year}",
            "inv_type":    "travel",
            "seller_name": "VIETNAM AIRLINES - CHI NHANH DA NANG",
            "seller_tax":  "0100107518",
            "seller_addr": "35 Nguyen Van Linh, Hai Chau, Da Nang",
            "serial_no":   f"1C{str(year)[-2:]}TVA",
            "items": [
                ("Ve may bay hang pho thong - tuyen Da Nang - Ha Noi (760km) - 2 hanh khach",
                 "VE", 2, 1_350_000),
                ("Ve may bay hang pho thong - tuyen Da Nang - Ho Chi Minh (960km) - 1 hanh khach",
                 "VE", 1, 1_150_000),
                ("Phi dich vu san bay noi dia",
                 "VE", 3,   100_000),
            ],
        },
    ]


# ============================================================
# HELPER
# ============================================================
def _fmt_vnd(n: float) -> str:
    return f"{int(n):,}".replace(",", ".")


# ============================================================
# HÀM SINH PDF (giữ nguyên 100% logic cũ)
# ============================================================
def create_invoice(invoice_def: dict, day: int, month: str, year: int,
                   is_signed: bool) -> str:
    """
    Tạo 1 file PDF hóa đơn VAT.
    Trả về đường dẫn file PDF đã tạo.
    """
    sig_label     = "signed" if is_signed else "unsigned"
    filename      = f"data/invoices/{invoice_def['prefix']}_{sig_label}.pdf"
    invoice_no    = f"{abs(hash(filename)) % 10_000_000:07d}"
    tax_auth_code = uuid.uuid4().hex.upper()
    lookup_code   = uuid.uuid4().hex[:11].upper()

    doc   = SimpleDocTemplate(
        filename, pagesize=A4,
        leftMargin=15*mm, rightMargin=15*mm,
        topMargin=12*mm,  bottomMargin=12*mm,
    )
    story = []

    # Header
    story.append(Paragraph("HOA DON GIA TRI GIA TANG", S_TITLE))
    story.append(Paragraph("(VAT INVOICE)", S_SUBTITLE))
    story.append(Paragraph(
        f"Ngay (day) {day:02d} thang (month) {month} nam (year) {year}",
        S_SUBTITLE
    ))
    story.append(Spacer(1, 2*mm))
    story.append(Paragraph(f"Ma cua Co quan thue: {tax_auth_code}", S_SUBTITLE))
    story.append(Spacer(1, 3*mm))

    # Serial + Invoice No
    meta_table = Table([[
        Paragraph("", S_VALUE),
        Paragraph(f"Mau so - Ky hieu (Serial No.): {invoice_def['serial_no']}", S_TD_R),
    ],[
        Paragraph("", S_VALUE),
        Paragraph(f"<b>So (Invoice No.): {invoice_no}</b>", S_TD_R),
    ]], colWidths=[100*mm, None])
    meta_table.setStyle(TableStyle([("VALIGN", (0,0), (-1,-1), "TOP")]))
    story.append(meta_table)
    story.append(Spacer(1, 2*mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.grey))
    story.append(Spacer(1, 2*mm))

    # Thông tin người bán
    story.append(Paragraph(f"<b>Don vi ban (Seller):</b> {invoice_def['seller_name']}", S_VALUE))
    story.append(Paragraph(f"<b>Ma so thue (Tax Code):</b> {invoice_def['seller_tax']}", S_VALUE))
    story.append(Paragraph(f"<b>Dia chi (Address):</b> {invoice_def['seller_addr']}", S_VALUE))
    story.append(Spacer(1, 3*mm))
    story.append(HRFlowable(width="100%", thickness=0.5, color=colors.lightgrey))
    story.append(Spacer(1, 2*mm))

    # Thông tin người mua
    story.append(Paragraph("<b>Nguoi mua (Buyer):</b>", S_VALUE))
    story.append(Paragraph(f"<b>Don vi (Company name):</b> {BUYER['name']}", S_VALUE))
    story.append(Paragraph(f"<b>Ma so thue (Tax Code):</b> {BUYER['tax_code']}", S_VALUE))
    story.append(Paragraph(f"<b>Dia chi (Address):</b> {BUYER['address']}", S_VALUE))
    story.append(Paragraph(
        "<b>Hinh thuc thanh toan (Payment method):</b> Tien mat / Chuyen khoan", S_VALUE
    ))
    story.append(Spacer(1, 4*mm))

    # Bảng hàng hóa
    headers = [
        Paragraph("STT\n(No.)",                    S_TH),
        Paragraph("Ten hang hoa, dich vu\n(Description)", S_TH),
        Paragraph("DVT\n(Unit)",                   S_TH),
        Paragraph("So luong\n(Qty)",               S_TH),
        Paragraph("Don gia\n(Unit Price)",          S_TH),
        Paragraph("Thanh tien\n(Amount)",           S_TH),
    ]
    col_widths = [10*mm, 82*mm, 14*mm, 16*mm, 22*mm, 22*mm]
    rows       = [headers]

    subtotal = 0.0
    for idx, (desc, unit, qty, price) in enumerate(invoice_def["items"], 1):
        amount    = qty * price
        subtotal += amount
        rows.append([
            Paragraph(str(idx),          S_TD_C),
            Paragraph(desc,              S_TD_L),
            Paragraph(unit,              S_TD_C),
            Paragraph(_fmt_vnd(qty),     S_TD_C),
            Paragraph(_fmt_vnd(price),   S_TD_R),
            Paragraph(_fmt_vnd(amount),  S_TD_R),
        ])

    for _ in range(max(0, 6 - len(invoice_def["items"]))):
        rows.append(["", "", "", "", "", ""])

    vat   = subtotal * 0.08
    total = subtotal + vat

    rows.append([
        Paragraph("", S_VALUE),
        Paragraph("<b>Cong tien hang (Sub total):</b>", S_TOTAL_L),
        "", "", "",
        Paragraph(_fmt_vnd(subtotal), S_TOTAL_R),
    ])
    rows.append([
        Paragraph("", S_VALUE),
        Paragraph("Thue suat GTGT (Tax rate): 8%", S_VALUE),
        "", "",
        Paragraph("Tien thue:", S_TD_R),
        Paragraph(_fmt_vnd(vat), S_TD_R),
    ])
    rows.append([
        Paragraph("", S_VALUE),
        Paragraph("<b>Tong cong tien thanh toan (Total payment):</b>", S_TOTAL_L),
        "", "", "",
        Paragraph(_fmt_vnd(total), S_TOTAL_R),
    ])

    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",     (0, 0),  (-1, 0),  colors.HexColor("#1565C0")),
        ("TEXTCOLOR",      (0, 0),  (-1, 0),  colors.white),
        ("GRID",           (0, 0),  (-1, -4), 0.4, colors.grey),
        ("VALIGN",         (0, 0),  (-1, -1), "MIDDLE"),
        ("ROWBACKGROUNDS", (0, 1),  (-1, -4), [colors.white, colors.HexColor("#F5F5F5")]),
        ("LINEABOVE",      (0, -3), (-1, -3), 0.8, colors.grey),
        ("SPAN",           (1, -3), (4, -3)),
        ("SPAN",           (1, -2), (3, -2)),
        ("SPAN",           (1, -1), (4, -1)),
        ("BACKGROUND",     (0, -1), (-1, -1), colors.HexColor("#E3F2FD")),
        ("LINEBELOW",      (0, -1), (-1, -1), 0.8, colors.grey),
        ("TOPPADDING",     (0, 0),  (-1, -1), 3),
        ("BOTTOMPADDING",  (0, 0),  (-1, -1), 3),
        ("LEFTPADDING",    (0, 0),  (-1, -1), 3),
        ("RIGHTPADDING",   (0, 0),  (-1, -1), 3),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 3*mm))

    # Số tiền bằng chữ
    story.append(Paragraph(
        f"So tien viet bang chu (Amount in words): [Tuong duong {_fmt_vnd(total)} VND]",
        _style("words", size=8, bold=True)
    ))
    story.append(Spacer(1, 5*mm))

    # Chữ ký
    if is_signed:
        sign_block = Table([[
            Paragraph("<b>Nguoi mua hang (Buyer)</b><br/>(Ky, ghi ro ho ten)", S_SIGN_GRY),
            Paragraph(
                f"<b>Nguoi ban hang (Seller)</b><br/>"
                f"<font color='red'>Da duoc ky dien tu boi<br/>"
                f"{invoice_def['seller_name']}<br/>"
                f"Ngay: {day:02d}/{month}/{year}</font>",
                S_SIGN_RED
            ),
        ]])
    else:
        sign_block = Table([[
            Paragraph("<b>Nguoi mua hang (Buyer)</b><br/>(Ky, ghi ro ho ten)", S_SIGN_GRY),
            Paragraph("<b>Nguoi ban hang (Seller)</b><br/>(Chua ky dien tu)", S_SIGN_GRY),
        ]])

    sign_block.setStyle(TableStyle([
        ("VALIGN",          (0,0), (-1,-1), "TOP"),
        ("ALIGN",           (0,0), (-1,-1), "CENTER"),
        ("TOPPADDING",      (0,0), (-1,-1), 4),
        ("BOTTOMPADDING",   (0,0), (-1,-1), 20),
    ]))
    story.append(sign_block)

    # Footer
    story.append(HRFlowable(width="100%", thickness=0.3, color=colors.lightgrey))
    story.append(Spacer(1, 1*mm))
    story.append(Paragraph("(Can kiem tra doi chieu khi lap, giao, nhan hoa don)", S_SMALL))
    story.append(Paragraph("Giai phap Hoa don Dien tu duoc cung cap boi GreenChain ESG System", S_SMALL))
    story.append(Paragraph(f"Ma tra cuu HDDT: {lookup_code}", S_SMALL))

    doc.build(story)
    return filename


# ============================================================
# HÀM SINH ẢNH PNG  ← MỚI
# ============================================================
def pdf_to_image(pdf_path: str, output_dir: str = "data/images",
                 dpi: int = 150) -> list[str]:
    """
    Chuyển 1 file PDF thành ảnh PNG, lưu vào output_dir.

    Params:
        pdf_path   : Đường dẫn file PDF nguồn.
        output_dir : Thư mục lưu ảnh (mặc định data/images/).
        dpi        : Độ phân giải render.
                     150 DPI → ảnh ~1240×1754px, dung lượng ~200–400KB — 
                     đủ rõ cho Vision LLM, không quá nặng.

    Returns:
        Danh sách đường dẫn các file PNG đã tạo
        (1 trang PDF → 1 file PNG; nhiều trang → nhiều file).
    """
    from pdf2image import convert_from_path

    os.makedirs(output_dir, exist_ok=True)

    # Tên cơ sở = tên file PDF bỏ đuôi .pdf
    base_name = os.path.splitext(os.path.basename(pdf_path))[0]

    pages = convert_from_path(pdf_path, dpi=dpi)

    saved_paths = []
    for page_idx, page_img in enumerate(pages):
        if len(pages) == 1:
            # Hóa đơn 1 trang (phổ biến) → không thêm số trang vào tên
            out_path = os.path.join(output_dir, f"{base_name}.png")
        else:
            # Hóa đơn nhiều trang → thêm _p01, _p02, ...
            out_path = os.path.join(output_dir, f"{base_name}_p{page_idx+1:02d}.png")

        page_img.save(out_path, "PNG", optimize=True)
        saved_paths.append(out_path)

    return saved_paths


def create_invoice_image(invoice_def: dict, day: int, month: str, year: int,
                         is_signed: bool, dpi: int = 150) -> tuple[str, list[str]]:
    """
    Tạo hóa đơn PDF rồi render sang ảnh PNG.

    Returns:
        (pdf_path, [png_path, ...])
    """
    pdf_path   = create_invoice(invoice_def, day, month, year, is_signed)
    png_paths  = pdf_to_image(pdf_path, dpi=dpi)
    return pdf_path, png_paths


# ============================================================
# CHẠY CHÍNH
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sinh dữ liệu hóa đơn mẫu cho GreenChain")
    parser.add_argument(
        "--no-img", action="store_true",
        help="Bỏ qua bước render ảnh PNG — chỉ sinh file PDF (nhanh hơn ~3×)"
    )
    parser.add_argument(
        "--dpi", type=int, default=150,
        help="Độ phân giải ảnh PNG (mặc định: 150 DPI). Tăng lên 200–300 nếu cần rõ hơn."
    )
    parser.add_argument(
        "--year", type=int, default=2026,
        help="Năm sinh hóa đơn (mặc định: 2026)"
    )
    args = parser.parse_args()

    YEAR       = args.year
    GEN_IMAGES = not args.no_img
    DPI        = args.dpi
    MONTH_LIST = [f"{m:02d}" for m in range(1, 13)]

    # Xen kẽ signed/unsigned
    sign_cycle = itertools.cycle([True, False])

    print(f"Đang tạo hóa đơn mẫu năm {YEAR}...")
    if GEN_IMAGES:
        print(f"  → Sẽ render cả ảnh PNG ({DPI} DPI) vào data/images/")
    else:
        print("  → Chỉ tạo PDF (bỏ qua ảnh)")
    print("=" * 60)

    all_pdf_files = []
    all_img_files = []

    for month in MONTH_LIST:
        day     = 10
        catalog = _make_catalog(month, YEAR)

        for inv_def in catalog:
            is_signed = next(sign_cycle)
            sig_str   = "✅ ký" if is_signed else "⬜ chưa ký"

            try:
                if GEN_IMAGES:
                    # Sinh PDF + ảnh cùng lúc
                    pdf_path, png_paths = create_invoice_image(
                        inv_def, day, month, YEAR, is_signed, dpi=DPI
                    )
                    img_info = f"→ {len(png_paths)} ảnh"
                    all_img_files.extend(png_paths)
                else:
                    # Chỉ sinh PDF
                    pdf_path = create_invoice(inv_def, day, month, YEAR, is_signed)
                    img_info = ""

                all_pdf_files.append(pdf_path)
                print(f"  {sig_str} | {os.path.basename(pdf_path)} {img_info}")

            except Exception as e:
                print(f"  ❌ Lỗi tạo {inv_def['prefix']}: {e}")

    # ── Tóm tắt ──────────────────────────────────────────────
    print()
    print("=" * 60)
    print(f"✅ PDF  : {len(all_pdf_files)} file trong data/invoices/")
    print(f"   Signed  : {sum(1 for f in all_pdf_files if 'signed' in f and 'unsigned' not in f)}")
    print(f"   Unsigned: {sum(1 for f in all_pdf_files if 'unsigned' in f)}")

    if GEN_IMAGES:
        print(f"✅ Ảnh  : {len(all_img_files)} file PNG trong data/images/")

    print()
    print("Loại hóa đơn được sinh:")
    for inv_type in ["electricity", "diesel", "petrol", "lpg", "industrial_gas",
                     "refrigerant", "transport", "waste", "travel"]:
        count = sum(1 for f in all_pdf_files if f"/{inv_type}_" in f)
        print(f"  {inv_type:20s}: {count:3d} PDF", end="")
        if GEN_IMAGES:
            img_count = sum(1 for f in all_img_files if f"/{inv_type}_" in f)
            print(f"  |  {img_count:3d} PNG", end="")
        print()

    print()
    print("Ghi chú pipeline:")
    print("  • Scope 1/2 (PDF)  : CHỈ nhận file _signed_")
    print("  • Scope 3  (PDF)   : Nhận cả signed và unsigned")
    print("  • Ảnh PNG          : Pipeline tự nhận dạng qua extract_invoice_data()")