"""
Emission Calculator — GreenChain MVP
Tất cả hệ số phát thải có trích dẫn nguồn rõ ràng.
Đây là "source of truth" cho mọi con số CO2 trong hệ thống.

Changelog:
  - FIX: LPG từ 1.61 kgCO2e/lít → 2.98 kgCO2e/kg (đúng đơn vị theo tài liệu)
  - ADD: Xăng (petrol) 2.27 kgCO2e/lít — Scope 1 Mobile Combustion
  - ADD: Scope 3 — vận chuyển thuê ngoài (distance-based & spend-based)
  - ADD: Scope 3 — công tác phí (business travel)
  - ADD: Scope 3 — xử lý rác thải (waste)
"""

# ============================================================
# SCOPE 1 & 2 — HỆ SỐ PHÁT THẢI
# ============================================================
EMISSION_FACTORS = {

    # ----------------------------------------------------------
    # Scope 2 — Điện lưới Việt Nam
    # Nguồn: Bộ TN&MT, Quyết định 2626/QĐ-BTNMT năm 2023
    # ----------------------------------------------------------
    "electricity_vn_grid": {
        "value": 0.4936,        # tCO2e / MWh
        "unit_input": "kWh",
        "unit_output": "tCO2e",
        "scope": 2,
        "source": "MONRE Vietnam 2023 - QD 2626/QD-BTNMT",
        "convert": lambda kwh: kwh * 0.4936 / 1000
    },

    # ----------------------------------------------------------
    # Scope 1 — Diesel (đốt tĩnh và di động)
    # Nguồn: IPCC AR6 WG3 Annex II, Table A.III.2 (2022)
    # ----------------------------------------------------------
    "diesel_combustion": {
        "value": 2.68,          # kgCO2e / lít
        "unit_input": "lít",
        "unit_output": "tCO2e",
        "scope": 1,
        "source": "IPCC AR6 2022 - Table A.III.2",
        "convert": lambda liters: liters * 2.68 / 1000
    },

    # ----------------------------------------------------------
    # Scope 1 — Xăng mô tô / phương tiện (Mobile Combustion)
    # Nguồn: IPCC AR6 (2022)
    # ----------------------------------------------------------
    "petrol_combustion": {
        "value": 2.27,          # kgCO2e / lít
        "unit_input": "lít",
        "unit_output": "tCO2e",
        "scope": 1,
        "source": "IPCC AR6 2022",
        "convert": lambda liters: liters * 2.27 / 1000
    },

    # ----------------------------------------------------------
    # Scope 1 — LPG / Gas công nghiệp
    # FIX: đơn vị là kg (không phải lít), hệ số 2.98 kgCO2e/kg
    # Nguồn: IPCC AR6 WG3 (2022)
    # ----------------------------------------------------------
    "lpg_combustion": {
        "value": 2.98,          # kgCO2e / kg  ← đã sửa từ 1.61 kgCO2e/lít
        "unit_input": "kg",
        "unit_output": "tCO2e",
        "scope": 1,
        "source": "IPCC AR6 2022",
        "convert": lambda kg: kg * 2.98 / 1000
    },

    "co2_industrial": {
        "value": 1.0,           # 1 kg CO2 công nghiệp = 1 kg CO2e khi phát thải
        "unit_input": "kg",
        "unit_output": "tCO2e",
        "scope": 1,
        "source": "IPCC 2006 - Industrial Processes",
        "convert": lambda kg: kg * 1.0 / 1000
    },

    "refrigerant_hfc": {
        "value": 2088.0,        # GWP của R-410A theo IPCC AR6
        # R-410A = hỗn hợp R-32 (GWP 675) + R-125 (GWP 3500) → trung bình ~2088
        "unit_input": "kg",
        "unit_output": "tCO2e",
        "scope": 1,
        "source": "IPCC AR6 2022 - Annex II Table 7.SM.7 (GWP100)",
        "convert": lambda kg: kg * 2088.0 / 1000
    },
}

# ============================================================
# SCOPE 3 — HỆ SỐ PHÁT THẢI
# ============================================================

# Hệ số vận chuyển thuê ngoài theo loại phương tiện
# Nguồn: GHG Protocol Scope 3 Standard — distance-based method
# Đơn vị: kgCO2e / tấn.km (tấn hàng × km di chuyển)
TRANSPORT_FACTORS = {
    "truck_small":   0.150,   # Xe tải nhỏ < 3.5 tấn
    "truck_medium":  0.110,   # Xe tải trung 3.5–16 tấn
    "truck_large":   0.062,   # Xe tải lớn > 16 tấn
    "ship":          0.008,   # Tàu biển container
    "air":           0.500,   # Máy bay hàng hóa
    "default":       0.110,   # Mặc định nếu không rõ loại xe
}

# Hệ số công tác phí theo phương tiện di chuyển
# Nguồn: GHG Protocol Scope 3 — business travel
# Đơn vị: kgCO2e / km / người
TRAVEL_FACTORS = {
    "flight_domestic":       0.255,   # Bay nội địa
    "flight_international":  0.195,   # Bay quốc tế (hiệu quả hơn vì chặng dài)
    "train":                 0.041,   # Tàu hỏa
    "bus":                   0.089,   # Xe buýt / xe khách
    "taxi_car":              0.171,   # Taxi / xe con
    "default":               0.171,   # Mặc định
}

# Hệ số xử lý rác thải
# Nguồn: IPCC 2006 / GHG Protocol
# Đơn vị: kgCO2e / tấn rác
WASTE_FACTORS = {
    "landfill":    580.0,   # Chôn lấp
    "incineration": 21.0,   # Đốt
    "composting":   10.0,   # Ủ phân
    "recycling":     0.0,   # Tái chế (phát thải = 0 ở giai đoạn này)
    "default":     580.0,   # Mặc định nếu không rõ
}

# ============================================================
# MAP từ invoice_type → factor key (Scope 1 & 2)
# ============================================================
INVOICE_TYPE_MAP = {
    "electricity": "electricity_vn_grid",
    "diesel":      "diesel_combustion",
    "petrol":      "petrol_combustion",
    "gasoline":    "petrol_combustion",   # alias tiếng Anh
    "xăng":        "petrol_combustion",   # alias tiếng Việt
    "gas":         "lpg_combustion",
    "lpg":         "lpg_combustion",
    "industrial_gas": None,   # Xử lý riêng theo từng line_item
    "co2_gas":        "co2_industrial",
    "oxygen":         None,   # Không phát thải
    "argon":          None,   # Không phát thải
    "refrigerant":     None,        # Xử lý riêng theo line_item
    "refrigerant_hfc": "refrigerant_hfc",
}


# ============================================================
# CALCULATOR FUNCTIONS
# ============================================================

def calculate_emission(extracted_data: dict) -> dict:
    """
    Tính phát thải Scope 1 hoặc Scope 2 từ dữ liệu hóa đơn năng lượng.
    Nhận dict từ extractor, trả về dict bổ sung thông tin phát thải.
    Raise ValueError nếu thiếu dữ liệu đầu vào.
    """
    invoice_type = extracted_data.get("invoice_type")
    quantity = extracted_data.get("quantity")

    if not invoice_type or quantity is None:
        raise ValueError("Thiếu invoice_type hoặc quantity")

    factor_key = INVOICE_TYPE_MAP.get(invoice_type)
    if not factor_key:
        # Không phải loại năng lượng trực tiếp → trả về 0, không raise lỗi
        return {
            **extracted_data,
            "emission_tco2e":  0,
            "emission_scope":  None,
            "emission_source": "Loại hóa đơn không phát thải trực tiếp (Scope 1/2)",
        }

    factor = EMISSION_FACTORS[factor_key]
    emission_tco2e = factor["convert"](quantity)

    return {
        **extracted_data,
        "emission_tco2e":        round(emission_tco2e, 4),
        "emission_scope":        factor["scope"],
        "emission_factor_value": factor["value"],
        "emission_factor_unit":  f"kgCO2e/{factor['unit_input']}",
        "emission_source":       factor["source"],
        "calculation_formula": (
            f"{quantity:,.2f} {factor['unit_input']} × "
            f"{factor['value']} kgCO2e/{factor['unit_input']} "
            f"÷ 1000 = {emission_tco2e:.4f} tCO2e"
        ),
    }


def calculate_scope3_transport(
    cargo_tons: float,
    distance_km: float,
    vehicle_type: str = "default",
    direction: str = "upstream",   # "upstream" | "downstream"
) -> dict:
    """
    Tính phát thải Scope 3 từ vận chuyển thuê ngoài — distance-based method.
    Theo GHG Protocol Scope 3 Standard, Cat 4 (upstream) hoặc Cat 9 (downstream).

    Args:
        cargo_tons:   Tải trọng hàng hóa (tấn)
        distance_km:  Quãng đường (km)
        vehicle_type: Loại phương tiện — xem TRANSPORT_FACTORS
        direction:    "upstream" (NVL mua vào) hoặc "downstream" (sản phẩm bán ra)

    Returns:
        dict chứa emission_tco2e và metadata
    """
    ef = TRANSPORT_FACTORS.get(vehicle_type, TRANSPORT_FACTORS["default"])
    tkm = cargo_tons * distance_km               # tấn.km
    emission_tco2e = tkm * ef / 1000             # kgCO2e → tCO2e

    category = "Cat 4 - Upstream Transport" if direction == "upstream" \
               else "Cat 9 - Downstream Transport"

    return {
        "emission_tco2e":        round(emission_tco2e, 4),
        "emission_scope":        3,
        "scope3_category":       category,
        "vehicle_type":          vehicle_type,
        "cargo_tons":            cargo_tons,
        "distance_km":           distance_km,
        "ton_km":                round(tkm, 2),
        "emission_factor_value": ef,
        "emission_factor_unit":  "kgCO2e/tấn.km",
        "emission_source":       "GHG Protocol Scope 3 Standard - Distance-based",
        "calculation_formula": (
            f"{cargo_tons} tấn × {distance_km} km = {tkm:.1f} tấn.km × "
            f"{ef} kgCO2e/tấn.km ÷ 1000 = {emission_tco2e:.4f} tCO2e"
        ),
    }


def calculate_scope3_transport_spend(
    spend_vnd: float,
    eeio_factor: float = 0.00028,   # kgCO2e/VND — ước tính ngành vận tải VN
) -> dict:
    """
    Tính phát thải Scope 3 vận chuyển — spend-based method (fallback).
    Dùng khi không có dữ liệu quãng đường/tải trọng từ đối tác.
    Theo GHG Protocol: KNK = Chi tiêu (VND) × EEIO factor.

    Args:
        spend_vnd:    Tổng tiền trả cho dịch vụ vận chuyển (VND)
        eeio_factor:  Hệ số cường độ kinh tế (kgCO2e/VND)
    """
    emission_tco2e = spend_vnd * eeio_factor / 1000

    return {
        "emission_tco2e":        round(emission_tco2e, 4),
        "emission_scope":        3,
        "scope3_category":       "Cat 4/9 - Transport (Spend-based)",
        "spend_vnd":             spend_vnd,
        "eeio_factor":           eeio_factor,
        "emission_factor_unit":  "kgCO2e/VND",
        "emission_source":       "GHG Protocol Scope 3 - Spend-based method",
        "calculation_formula": (
            f"{spend_vnd:,.0f} VND × {eeio_factor} kgCO2e/VND "
            f"÷ 1000 = {emission_tco2e:.4f} tCO2e"
        ),
    }


def calculate_scope3_business_travel(
    distance_km: float,
    num_passengers: int = 1,
    transport_mode: str = "default",
) -> dict:
    """
    Tính phát thải Scope 3 từ công tác phí (Cat 6 - Business Travel).
    Theo GHG Protocol Scope 3 Standard.

    Args:
        distance_km:       Quãng đường (km)
        num_passengers:    Số người đi
        transport_mode:    Phương tiện — xem TRAVEL_FACTORS
    """
    ef = TRAVEL_FACTORS.get(transport_mode, TRAVEL_FACTORS["default"])
    emission_tco2e = distance_km * num_passengers * ef / 1000

    return {
        "emission_tco2e":        round(emission_tco2e, 4),
        "emission_scope":        3,
        "scope3_category":       "Cat 6 - Business Travel",
        "transport_mode":        transport_mode,
        "distance_km":           distance_km,
        "num_passengers":        num_passengers,
        "emission_factor_value": ef,
        "emission_factor_unit":  "kgCO2e/km/người",
        "emission_source":       "GHG Protocol Scope 3 - Business Travel",
        "calculation_formula": (
            f"{distance_km} km × {num_passengers} người × "
            f"{ef} kgCO2e/km ÷ 1000 = {emission_tco2e:.4f} tCO2e"
        ),
    }


def calculate_scope3_waste(
    waste_tons: float,
    treatment_method: str = "default",
) -> dict:
    """
    Tính phát thải Scope 3 từ xử lý rác thải (Cat 5 - Waste Generated).
    Theo IPCC 2006 / GHG Protocol.

    Args:
        waste_tons:        Khối lượng rác thải (tấn)
        treatment_method:  Phương pháp xử lý — xem WASTE_FACTORS
    """
    ef = WASTE_FACTORS.get(treatment_method, WASTE_FACTORS["default"])
    emission_tco2e = waste_tons * ef / 1000

    return {
        "emission_tco2e":        round(emission_tco2e, 4),
        "emission_scope":        3,
        "scope3_category":       "Cat 5 - Waste Generated in Operations",
        "treatment_method":      treatment_method,
        "waste_tons":            waste_tons,
        "emission_factor_value": ef,
        "emission_factor_unit":  "kgCO2e/tấn",
        "emission_source":       "IPCC 2006 / GHG Protocol Scope 3",
        "calculation_formula": (
            f"{waste_tons} tấn × {ef} kgCO2e/tấn "
            f"÷ 1000 = {emission_tco2e:.4f} tCO2e"
        ),
    }


# ============================================================
# QUICK TEST
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("SCOPE 1 & 2 — Hóa đơn năng lượng")
    print("=" * 60)

    tests_energy = [
        {"invoice_type": "electricity", "month": 1, "year": 2024, "quantity": 42000, "unit": "kWh"},
        {"invoice_type": "diesel",      "month": 1, "year": 2024, "quantity": 1200,  "unit": "lít"},
        {"invoice_type": "petrol",      "month": 1, "year": 2024, "quantity": 300,   "unit": "lít"},
        {"invoice_type": "gas",         "month": 1, "year": 2024, "quantity": 500,   "unit": "kg"},
    ]
    for t in tests_energy:
        r = calculate_emission(t)
        print(f"  [{r['invoice_type']:12s}] Scope {r['emission_scope']} | "
              f"{r['calculation_formula']}")

    print()
    print("=" * 60)
    print("SCOPE 3 — Vận chuyển thuê ngoài")
    print("=" * 60)

    r = calculate_scope3_transport(cargo_tons=10, distance_km=300, vehicle_type="truck_medium")
    print(f"  Distance-based : {r['calculation_formula']}")

    r = calculate_scope3_transport_spend(spend_vnd=5_000_000)
    print(f"  Spend-based    : {r['calculation_formula']}")

    print()
    print("=" * 60)
    print("SCOPE 3 — Công tác phí & Rác thải")
    print("=" * 60)

    r = calculate_scope3_business_travel(distance_km=700, num_passengers=2, transport_mode="flight_domestic")
    print(f"  Công tác phí   : {r['calculation_formula']}")

    r = calculate_scope3_waste(waste_tons=2.5, treatment_method="landfill")
    print(f"  Rác thải       : {r['calculation_formula']}")