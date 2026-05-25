# pipeline/graph.py
from typing import List, Optional
from typing_extensions import TypedDict
from langgraph.graph import StateGraph, END
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage
from langchain_core.prompts import ChatPromptTemplate   
from langgraph.checkpoint.memory import MemorySaver

from pipeline.extractor import extract_invoice_data
from pipeline.calculator import (
    calculate_emission,
    calculate_scope3_transport,
    calculate_scope3_transport_spend,
    calculate_scope3_waste,
    calculate_scope3_business_travel,
)
from pipeline.llm_manager import master_llm, master_report_llm

# ============================================================
# STATE HOÀN CHỈNH CHO CHAT-CENTRIC AGENT
# ============================================================
class ESGState(TypedDict):
    messages: List[BaseMessage]          # Lịch sử hội thoại đầy đủ (Trọng tâm mới)
    pdf_files: List[str]                 # Danh sách file truyền từ Sidebar
    extracted_data: List[dict]
    emission_results: List[dict]
    summary: dict                        # Kết quả tổng hợp hiển thị lên Dashboard
    errors: List[str]
    final_report: Optional[str]          # Nội dung báo cáo dạng Markdown
    next_node: Optional[str]             # Cờ điều hướng luồng ngầm cho Router


# ============================================================
# ROUTING LOGIC CHO SCOPE 3 (GIỮ NGUYÊN 100% LOGIC CŨ)
# ============================================================
SCOPE3_TRANSPORT_TYPES = {"transport", "logistics", "shipping", "van_chuyen"}
SCOPE3_WASTE_TYPES     = {"waste", "rac_thai"}
SCOPE3_TRAVEL_TYPES    = {"travel", "business_travel", "cong_tac"}

def _calculate_single(data: dict) -> dict:
    invoice_type = data.get("invoice_type", "").lower()

    if invoice_type in SCOPE3_TRANSPORT_TYPES:
        cargo_tons  = data.get("cargo_tons")
        distance_km = data.get("distance_km")
        spend_vnd   = data.get("total_amount_vnd")

        if cargo_tons and distance_km:
            result = calculate_scope3_transport(
                cargo_tons=float(cargo_tons),
                distance_km=float(distance_km),
                vehicle_type=data.get("vehicle_type", "default"),
                direction=data.get("direction", "upstream"),
            )
        elif spend_vnd:
            result = calculate_scope3_transport_spend(spend_vnd=float(spend_vnd))
        else:
            raise ValueError(
                f"Hóa đơn vận chuyển '{data.get('source_file')}' thiếu "
                f"cả (cargo_tons + distance_km) lẫn total_amount_vnd"
            )
        return {**data, **result}

    if invoice_type in SCOPE3_WASTE_TYPES:
        waste_tons = data.get("quantity") or data.get("waste_tons")
        if not waste_tons:
            raise ValueError("Thiếu khối lượng rác thải (quantity hoặc waste_tons)")
        return {**data, **calculate_scope3_waste(
            waste_tons=float(waste_tons),
            treatment_method=data.get("treatment_method", "default"),
        )}

    if invoice_type in SCOPE3_TRAVEL_TYPES:
        distance_km = data.get("distance_km") or data.get("quantity")
        if not distance_km:
            raise ValueError("Thiếu quãng đường (distance_km hoặc quantity)")
        return {**data, **calculate_scope3_business_travel(
            distance_km=float(distance_km),
            num_passengers=int(data.get("num_passengers", 1)),
            transport_mode=data.get("transport_mode", "default"),
        )}

    return calculate_emission(data)


# ============================================================
# BÓC TÁCH LINE ITEMS (GIỮ NGUYÊN BỘ TỪ KHÓA CỦA BẠN)
# ============================================================
LINE_ITEM_KEYWORD_MAP = {
    "co2":    "co2_industrial",
    "oxy":    None,
    "oxygen": None,
    "argon":  None,
    "n2":     None,
    "nitrogen": None,
    "heli":      None,
    "acetylene": "co2_industrial",
    "r-410a":    "refrigerant_hfc",
    "r-32":      "refrigerant_hfc",
    "r-22":      "refrigerant_hfc",
    "r-134a":    "refrigerant_hfc",
    "gas lanh":  "refrigerant_hfc",
}

def _classify_line_item(description: str) -> str | None:
    desc_lower = description.lower()
    for keyword, factor_key in LINE_ITEM_KEYWORD_MAP.items():
        if keyword in desc_lower:
            return factor_key
    return None

def _expand_line_items(data: dict) -> list[dict]:
    line_items = data.get("line_items")
    if not line_items:
        return [data]

    expanded = []
    for item in line_items:
        desc        = item.get("description", "")
        factor_key  = _classify_line_item(desc)

        if factor_key is None:
            print(f"    → Bỏ qua '{desc}' (không phát thải hoặc chưa có hệ số)")
            continue

        item_data = {
            **data,
            "invoice_type": factor_key,
            "quantity":     item.get("quantity", 0),
            "unit":         item.get("unit", ""),
            "source_file":  f"{data.get('source_file', '')} [{desc}]",
            "line_items":   None,
        }
        expanded.append(item_data)

    return expanded


# ============================================================
# RAG PROMPT TEMPLATE (BẢO LƯU 100% QUY TẮC CŨ)
# ============================================================
RAG_PROMPT_TEMPLATE = ChatPromptTemplate.from_messages([
("system", """Bạn là GreenChain, chuyên gia pháp lý và tư vấn ESG cấp cao.
Nhiệm vụ của bạn là hỗ trợ người dùng tra cứu quy định phát thải và tư vấn chuyên môn.

CÁC QUY TẮC BẮT BUỘC:
1. ƯU TIÊN 1 (RAG): Luôn kiểm tra [TÀI LIỆU HỆ THỐNG] trước. Nếu thông tin có trong đó, hãy trả lời và trích dẫn tên file nguồn ở cuối.
2. ƯU TIÊN 2 (LLM Fallback): Nếu tài liệu hệ thống KHÔNG CÓ thông tin, KHÔNG được nói "Tôi không biết". Hãy sử dụng kiến thức nền tảng chuyên sâu của bạn về ESG, GHG Protocol, ISO 14064, và GRI 305 để giải đáp cho người dùng.
3. KHI DÙNG ƯU TIÊN 2: Bắt buộc phải thêm một câu rào trước: "Thông tin này hiện chưa được cập nhật trong cơ sở dữ liệu nội bộ, tuy nhiên theo kiến thức chuyên môn về ESG..."
4. Luôn trả lời bằng tiếng Việt, trình bày rõ ràng, mạch lạc bằng markdown.

[TÀI LIỆU HỆ THỐNG]:
{context}"""),
    ("placeholder", "{chat_history}"),
    ("human", "{question}"),
])

_vector_store = None

def _get_vector_store():
    global _vector_store
    if _vector_store is None:
        from pipeline.rag_builder import load_index
        _vector_store = load_index()
    return _vector_store


# ============================================================
# NODES (CÁC ĐẦU VIỆC CỦA AGENT ĐÃ ĐƯỢC TÍCH HỢP)
# ============================================================

def classify_intent_node(state: ESGState) -> ESGState:
    """Đọc tin nhắn cuối cùng để nhận diện hành động của người dùng."""
    messages = state.get("messages", [])
    last_message = messages[-1].content if messages else ""
    
    # Nếu hệ thống truyền lệnh phân tích hoặc phát hiện có file mới đẩy lên sidebar
    if "/analyze" in last_message or (state.get("pdf_files") and not state.get("summary")):
        return {**state, "next_node": "extract"}
        
    intent_prompt = """Bạn là bộ brain phân loại ý định cho GreenChain ESG Agent.
Hãy đọc tin nhắn mới nhất của người dùng và phản hồi DUY NHẤT một trong các từ khóa nhãn sau:
- RETRIEVE_LAW: Khi người dùng hỏi đáp, tra cứu luật, tiêu chuẩn ESG, GHG Protocol, Nghị định 06, phân loại các Scope.
- GENERATE_REPORT: Khi người dùng yêu cầu lập báo cáo, xuất báo cáo kiểm kê, tạo file GRI 305.
- GENERAL: Lời chào, câu hỏi thông thường hoặc tương tác xã giao.

Tin nhắn người dùng: "{user_input}"
Nhãn ý định:"""
    
    try:
        response = master_llm.invoke([HumanMessage(content=intent_prompt.format(user_input=last_message))])
        intent = response.content.strip().upper()
    except Exception:
        intent = "GENERAL"
    
    if "RETRIEVE_LAW" in intent:
        return {**state, "next_node": "rag"}
    elif "GENERATE_REPORT" in intent:
        return {**state, "next_node": "report"}
    else:
        return {**state, "next_node": "general_chat"}


def extract_node(state: ESGState) -> ESGState:
    files = state.get("pdf_files", [])
    print(f"\n[Node: extract] Đang xử lý {len(files)} file...")
 
    extracted = []
    errors = list(state.get("errors", []))
 
    for file_path in files:
        try:
            data = extract_invoice_data(file_path)
            qty_display = (
                f"{data['quantity']} {data['unit']}"
                if data.get("quantity") and data.get("unit")
                else f"{len(data.get('line_items', []))} dòng hàng"
            )
            print(f"  ✓ {data['source_file']}: {qty_display} ({data['invoice_type']})")
            extracted.append(data)
        except Exception as e:
            error_msg = f"Lỗi đọc {file_path}: {e}"
            errors.append(error_msg)
            print(f"  ✗ {error_msg}")
 
    return {**state, "extracted_data": extracted, "errors": errors}


def calculate_node(state: ESGState) -> ESGState:
    print(f"\n[Node: calculate] Tính phát thải...")
    results = []
    errors  = list(state.get("errors", []))

    for data in state["extracted_data"]:
        sub_items = _expand_line_items(data)
        if not sub_items:
            print(f"  ℹ {data.get('source_file')}: Không có dòng nào phát thải, bỏ qua.")
            continue

        for item in sub_items:
            try:
                result = _calculate_single(item)
                results.append(result)
                scope_label = f"Scope {result['emission_scope']}" if result.get("emission_scope") else "N/A"
                print(f"  ✓ {result.get('source_file', '?')}: {result['emission_tco2e']} tCO2e ({scope_label})")
            except Exception as e:
                error_msg = f"Lỗi tính {item.get('source_file', '?')}: {e}"
                errors.append(error_msg)
                print(f"  ✗ {error_msg}")

    return {**state, "emission_results": results, "errors": errors}


def summarize_node(state: ESGState) -> ESGState:
    """Tính toán tích lũy số liệu và tự động phát hồi âm kết quả vào box chat."""
    print(f"\n[Node: summarize] Tổng hợp báo cáo...")
    results = state["emission_results"]

    scope1 = [r for r in results if r.get("emission_scope") == 1]
    scope2 = [r for r in results if r.get("emission_scope") == 2]
    scope3 = [r for r in results if r.get("emission_scope") == 3]

    monthly = {}
    for r in results:
        month = r.get("month")
        year  = r.get("year")
        key   = f"{year}-{month:02d}" if month and year else "other"
        if key not in monthly:
            monthly[key] = {"scope1": 0.0, "scope2": 0.0, "scope3": 0.0}
        scope = r.get("emission_scope")
        if scope == 1:   monthly[key]["scope1"] += r["emission_tco2e"]
        elif scope == 2: monthly[key]["scope2"] += r["emission_tco2e"]
        elif scope == 3: monthly[key]["scope3"] += r["emission_tco2e"]

    total_scope1 = round(sum(r["emission_tco2e"] for r in scope1), 4)
    total_scope2 = round(sum(r["emission_tco2e"] for r in scope2), 4)
    total_scope3 = round(sum(r["emission_tco2e"] for r in scope3), 4)

    years = [r.get("year") for r in results if isinstance(r.get("year"), int)]
    target_year = max(set(years), key=years.count) if years else "N/A"

    company_names = [r.get("company_name", "").strip() for r in results if r.get("company_name", "").strip()]
    company_name = max(set(company_names), key=company_names.count) if company_names else "Chưa xác định"

    fuel_types_scope1 = list({r.get("invoice_type", "").lower() for r in scope1 if r.get("invoice_type")})
    fuel_types_scope3_cats = list({r.get("scope3_category", "") for r in scope3 if r.get("scope3_category")})

    peak_analysis = {}
    for scope_key in ["scope1", "scope2", "scope3"]:
        vals = {k: v[scope_key] for k, v in monthly.items() if v[scope_key] > 0}
        if vals:
            peak_analysis[scope_key] = {
                "max_month": max(vals, key=vals.get),
                "max_val":   round(max(vals.values()), 4),
                "min_month": min(vals, key=vals.get),
                "min_val":   round(min(vals.values()), 4),
            }

    summary = {
        "target_year":            target_year,
        "company_name":           company_name,           
        "total_scope1_tco2e":     total_scope1,
        "total_scope2_tco2e":     total_scope2,
        "total_scope3_tco2e":     total_scope3,
        "total_emission_tco2e":   round(total_scope1 + total_scope2 + total_scope3, 4),
        "monthly_breakdown":      monthly,
        "invoice_count":          len(results),
        "fuel_types_scope1":      fuel_types_scope1,      
        "fuel_types_scope3_cats": fuel_types_scope3_cats, 
        "peak_analysis":          peak_analysis,          
        "errors":                 state.get("errors", []),
    }

    # Bổ sung một tin nhắn thông báo đẩy ngược lại giao diện Chat trực quan
    chat_notify = f"📊 **GreenChain Agent phân tích thành công {summary['invoice_count']} chứng từ!**\n\n" \
                  f"- **Tổng phát thải:** {summary['total_emission_tco2e']:,.2f} tCO2e\n" \
                  f"- **Scope 1 (Trực tiếp):** {summary['total_scope1_tco2e']:,.2f} tCO2e\n" \
                  f"- **Scope 2 (Gián tiếp):** {summary['total_scope2_tco2e']:,.2f} tCO2e\n" \
                  f"- **Scope 3 (Chuỗi giá trị):** {summary['total_scope3_tco2e']:,.2f} tCO2e\n\n" \
                  f"Biểu đồ cơ cấu và bảng dữ liệu chi tiết đã được cập nhật real-time tại bảng điều khiển bên phải."
    
    updated_messages = list(state["messages"]) + [AIMessage(content=chat_notify)]
    return {**state, "summary": summary, "messages": updated_messages}


def report_node(state: ESGState) -> ESGState:
    print(f"\n[Node: report] Sinh báo cáo kiểm kê phát thải GRI 305...")
    summary = state.get("summary", {})
    
    if not summary:
        msg = "⚠️ Hiện tại hệ thống chưa có dữ liệu tổng hợp từ hóa đơn. Vui lòng tải các tệp chứng từ năng lượng lên hệ thống và kích hoạt phân tích trước khi xuất báo cáo GRI 305."
        return {**state, "final_report": "Không có dữ liệu", "messages": list(state["messages"]) + [AIMessage(content=msg)]}

    try:
        template_path = "data/esg_docs/templates/gri_305.md"
        try:
            with open(template_path, "r", encoding="utf-8") as f:
                skeleton_text = f.read()
        except FileNotFoundError:
            skeleton_text = "## BÁO CÁO PHÁT THẢI GRI 305\n\n[Dự thảo hoàn thiện tự động bởi AI]"

        scope1 = summary.get('total_scope1_tco2e', 0)
        scope2 = summary.get('total_scope2_tco2e', 0)
        scope3 = summary.get('total_scope3_tco2e', 0)
        total  = summary.get('total_emission_tco2e', 0)
        invoice_count = summary.get('invoice_count', 0)
        intensity = round(total / invoice_count, 4) if invoice_count else 0
        target_year = summary.get('target_year', 'N/A')

        company_name  = summary.get("company_name", "Chưa xác định")
        fuel_scope1   = summary.get("fuel_types_scope1", [])
        scope3_cats   = summary.get("fuel_types_scope3_cats", [])
        peak_analysis = summary.get("peak_analysis", {})

        pct1 = round(scope1 / total * 100, 1) if total else 0
        pct2 = round(scope2 / total * 100, 1) if total else 0
        pct3 = round(100 - pct1 - pct2, 1)

        FUEL_LABEL = {
            "diesel":         "Dầu diesel — đốt di động/tĩnh",
            "petrol":         "Xăng — đốt di động",
            "gas":            "LPG — đốt tĩnh (lò hơi)",
            "lpg":            "LPG — đốt tĩnh (lò hơi)",
            "co2_industrial": "CO2 công nghiệp — phát thải quá trình (hàn, làm lạnh)",
        }
        fuel_desc_text = "\n  - ".join(FUEL_LABEL.get(f, f.capitalize()) for f in fuel_scope1) or "Không có nguồn Scope 1 trong kỳ"
        scope3_cat_text = "\n  - ".join(scope3_cats) if scope3_cats else "Chưa thu thập được dữ liệu Scope 3 trong kỳ"

        peak_text = "\n".join(
            f"  {lbl}: cao nhất {p['max_month']} ({p['max_val']} tCO2e), thấp nhất {p['min_month']} ({p['min_val']} tCO2e)"
            for sk, lbl in [("scope1","Scope 1"),("scope2","Scope 2"),("scope3","Scope 3")]
            if (p := peak_analysis.get(sk))
        ) or "  Chỉ có 1 tháng dữ liệu."

        dominant_scope = "Scope 1" if scope1 >= scope2 else "Scope 2"

        prompt = f"""
                Bạn là Kiểm toán viên ESG cấp cao. Hãy hoàn thiện Báo cáo GRI 305
                bằng cách điền số liệu và phân tích vào đúng Skeleton bên dưới.

                ═══ DỮ LIỆU THỰC TẾ (không được thay đổi số) ═══
                Tên công ty: {company_name}
                Năm báo cáo: {target_year}
                Tổng hóa đơn: {invoice_count}

                Scope 1: {scope1:.4f} tCO2e ({pct1}%)
                Scope 2: {scope2:.4f} tCO2e ({pct2}%)
                Scope 3: {scope3:.4f} tCO2e ({pct3}%)
                Tổng:    {total:.4f} tCO2e
                Cường độ (Scope1+2/hóa đơn): {intensity:.4f} tCO2e/hóa đơn

                Nguồn Scope 1 CÓ TRONG HÓA ĐƠN:
                - {fuel_desc_text}

                Danh mục Scope 3 đã tính:
                - {scope3_cat_text}

                Phân tích cao/thấp điểm:
                {peak_text}

                Scope chiếm tỷ trọng cao nhất: {dominant_scope}

                ═══ QUY TẮC BẮT BUỘC ═══
                1. Tên công ty: dùng đúng "{company_name}", KHÔNG dùng "Công ty ABC".
                2. Số liệu: chỉ dùng số ở trên, KHÔNG bịa thêm.
                3. Scope 1 (Mục 2.1): CHỈ liệt kê nguồn có trong danh sách thực tế trên. Xóa hoàn toàn nguồn khác nếu không xuất hiện.
                4. Scope 3 (Mục 2.3): Nếu tổng = 0 → ghi "Chưa thu thập được dữ liệu".
                5. Đề xuất (Mục 6): Tập trung vào {dominant_scope}. Nếu Scope 2 = 0 → KHÔNG đề xuất giảm Scope 2.
                6. Xu hướng (Mục 3 & 4): Dùng peak_analysis trên. Nếu chỉ 1 tháng → báo cáo đúng thực tế.
                7. Giữ nguyên 100% cấu trúc heading (##) của Skeleton.

                ═══ SKELETON ═══
                {skeleton_text}
                """

        response = master_report_llm.invoke([HumanMessage(content=prompt)])
        content = response.content.strip()
        if not content:
            raise ValueError("API trả về kết quả rỗng.")
            
        print("  ✓ Đã sinh báo cáo thành công!")
        
        chat_msg = "📑 **Báo cáo kiểm kê phát thải GRI 305 đã được thiết lập thành công!**\n\nBản dự thảo chi tiết đã được render toàn văn ngay phía dưới đây. Bạn cũng có thể nhấn nút **Tải xuống Báo cáo (.md)** ở bảng bên phải để lưu trữ file."
        updated_messages = list(state["messages"]) + [AIMessage(content=chat_msg), AIMessage(content=content)]
        return {**state, "final_report": content, "messages": updated_messages}

    except Exception as e:
        error_msg = f"Lỗi khi sinh báo cáo: {e}"
        print(f"  ✗ {error_msg}")
        errors = list(state.get("errors", []))
        errors.append(error_msg)
        return {**state, "final_report": "LỖI_HỆ_THỐNG", "errors": errors, "messages": list(state["messages"]) + [AIMessage(content=f"❌ Hệ thống không thể sinh báo cáo do lỗi: {e}")]}


def rag_node(state: ESGState) -> ESGState:
    """Tra cứu luật dựa trên câu hỏi cuối cùng nằm trong list tin nhắn."""
    query = state["messages"][-1].content
    print(f"\n[Node: RAG] Tra cứu: '{query}'")
    errors = list(state.get("errors", []))
    context_text = ""
    sources = []

    try:
        vs = _get_vector_store()
        retriever = vs.as_retriever(search_type="similarity_score_threshold", search_kwargs={"k": 6, "score_threshold": 0.5})
        retrieved_docs = retriever.invoke(query)
        if retrieved_docs:
            context_parts = []
            for doc in retrieved_docs:
                context_parts.append(doc.page_content)
                src = doc.metadata.get("source_file", "unknown")
                if src not in sources:
                    sources.append(src)
            context_text = "\n\n---\n\n".join(context_parts)
            print(f"  ✓ {len(retrieved_docs)} chunks từ: {', '.join(sources)}")
        else:
            context_text = "Tài liệu hệ thống không chứa thông tin về câu hỏi này."
            print("  ⚠ Không có chunk nào vượt qua ngưỡng threshold 0.5")

    except FileNotFoundError:
        warning = "FAISS index chưa được xây dựng. Đang sử dụng dữ liệu fallback tạm thời."
        errors.append(warning)
        context_text = "Theo tiêu chuẩn GRI 305, Scope 1 là phát thải trực tiếp, Scope 2 là phát thải gián tiếp từ điện năng tiêu thụ."
        sources = ["Hệ thống Fallback"]
    except Exception as e:
        error_msg = f"Lỗi truy vấn FAISS: {e}"
        errors.append(error_msg)
        return {**state, "messages": list(state["messages"]) + [AIMessage(content="Hệ thống tra cứu gặp lỗi kỹ thuật.")], "errors": errors}

    try:
        prompt_input = {
            "context": context_text,
            "question": query,
            "chat_history": [] 
        }
        messages = RAG_PROMPT_TEMPLATE.format_messages(**prompt_input)
        response = master_llm.invoke(messages)
        answer = response.content

        if sources and "Tôi không tìm thấy thông tin này" not in answer:
            answer += f"\n\n*Nguồn trích dẫn tài liệu: {', '.join(sources)}*"

        return {**state, "messages": list(state["messages"]) + [AIMessage(content=answer)]}
    except Exception as e:
        errors.append(f"Lỗi sinh câu trả lời RAG: {e}")
        return {**state, "errors": errors}


def general_chat_node(state: ESGState) -> ESGState:
    """Xử lý các câu hỏi chào hỏi hoặc hội thoại thông thường."""
    system_msg = SystemMessage(content="Bạn là GreenChain, Trợ lý ảo ESG thông minh. Bạn phản hồi một cách ngắn gọn, lịch sự, chuyên nghiệp và điều hướng khéo léo để người dùng sử dụng các tính năng phân tích hóa đơn hoặc tra cứu luật của hệ thống.")
    try:
        response = master_llm.invoke([system_msg] + state["messages"])
        ans_content = response.content
    except Exception as e:
        ans_content = f"Xin lỗi, tôi đang gặp một chút gián đoạn kết nối: {e}"
        
    return {**state, "messages": list(state["messages"]) + [AIMessage(content=ans_content)]}


# ============================================================
# ROUTER GRAPH TIẾN TRÌNH TUẦN TỰ
# ============================================================
_shared_memory = MemorySaver()

def should_calculate(state: ESGState) -> str:
    if state["extracted_data"]:
        return "calculate"
    return "end_with_error"

def route_by_intent(state: ESGState) -> str:
    return state.get("next_node", "general_chat")

def build_esg_pipeline():
    graph = StateGraph(ESGState)

    # Đăng ký các nút xử lý thực tế
    graph.add_node("classify_intent", classify_intent_node)
    graph.add_node("extract",         extract_node)
    graph.add_node("calculate",       calculate_node)
    graph.add_node("summarize",       summarize_node)
    graph.add_node("rag",             rag_node)
    graph.add_node("report",          report_node)
    graph.add_node("general_chat",    general_chat_node)

    # Điểm vào cố định xuất phát từ bộ phân loại ý định
    graph.set_entry_point("classify_intent")

    # Liên kết các cạnh điều hướng có điều kiện
    graph.add_conditional_edges(
        "classify_intent",
        route_by_intent,
        {
            "extract":      "extract",
            "rag":          "rag",
            "report":       "report",
            "general_chat": "general_chat"
        }
    )

    graph.add_conditional_edges(
        "extract",
        should_calculate,
        {"calculate": "calculate", "end_with_error": END}
    )

    # Luồng xử lý dữ liệu số liệu đi thẳng về END (Dashboard sẽ hiển thị)
    graph.add_edge("calculate", "summarize")
    graph.add_edge("summarize", END)
    
    # Các luồng độc lập khác kết thúc sau khi append tin nhắn vào box chat
    graph.add_edge("rag",        END)
    graph.add_edge("report",     END)
    graph.add_edge("general_chat", END)

    # Biên dịch đồ thị: Yêu cầu Graph phải DỪNG LẠI trước khi vào node "calculate"
    return graph.compile(
        checkpointer=_shared_memory,
        interrupt_before=["calculate"] 
    )