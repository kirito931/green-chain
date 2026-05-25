import os
import json
import shutil
import tempfile
from typing import List, Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.messages import HumanMessage, AIMessage

# Import pipeline LangGraph của bạn
from pipeline.graph import build_esg_pipeline

# Khởi tạo FastAPI App
app = FastAPI(title="GreenChain AI Core API", version="1.0")

# 1. CẤU HÌNH CORS (Bắt buộc để Next.js port 3000 gọi được sang FastAPI port 8000)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Trong thực tế production sẽ thay bằng domain của Vercel
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Khởi tạo Pipeline ngầm
esg_pipeline = build_esg_pipeline()

# ============================================================
# HÀM BỔ TRỢ (HELPERS)
# ============================================================
def serialize_messages(messages) -> list:
    """Chuyển object Message của LangChain thành JSON thuần cho Node.js hiểu"""
    result = []
    for msg in messages:
        # Bỏ qua các tin nhắn lệnh ngầm của hệ thống
        if "[Hệ thống:" in msg.content: 
            continue
        role = "user" if isinstance(msg, HumanMessage) else "assistant"
        result.append({"role": role, "content": msg.content})
    return result

# ============================================================
# API ENDPOINTS
# ============================================================
@app.get("/")
def health_check():
    return {"status": "ok", "message": "GreenChain AI Core is running!"}


@app.post("/api/chat")
async def chat_endpoint(
    thread_id: str = Form(...),
    message: str = Form(""),
    action: str = Form("chat"),           # "chat" hoặc "confirm"
    edited_data: str = Form("[]"),        # Dữ liệu JSON bảng đã sửa (nếu action="confirm")
    files: List[UploadFile] = File(default=[])
):
    """
    Endpoint chính xử lý mọi giao tiếp từ Frontend Node.js đẩy sang.
    """
    config = {"configurable": {"thread_id": thread_id}}
    pdf_paths = []
    tmp_dir = None

    try:
        # --- LUỒNG 1: TIẾP TỤC SAU KHI CON NGƯỜI KIỂM DUYỆT (RESUME HITL) ---
        if action == "confirm":
            try:
                extracted_data = json.loads(edited_data)
            except:
                raise HTTPException(status_code=400, detail="edited_data phải là JSON hợp lệ")
            
            # Cập nhật state với dữ liệu con người đã sửa
            esg_pipeline.update_state(config, {"extracted_data": extracted_data})
            
            # Gọi invoke với None để đồ thị chạy tiếp phần còn lại (Node calculate)
            output_state = esg_pipeline.invoke(None, config=config)

        # --- LUỒNG 2: CHAT HOẶC GỬI FILE MỚI ---
        else:
            # 1. Lưu file tạm thời để extractor.py đọc
            if files and files[0].filename != "":
                tmp_dir = tempfile.mkdtemp()
                for uf in files:
                    file_path = os.path.join(tmp_dir, uf.filename)
                    with open(file_path, "wb") as buffer:
                        shutil.copyfileobj(uf.file, buffer)
                    pdf_paths.append(file_path)

            # 2. Xây dựng tin nhắn đầu vào
            input_message = message
            if pdf_paths:
                input_message += f"\n\n*[Hệ thống: Kích hoạt phân tích {len(pdf_paths)} chứng từ đính kèm]*"
            
            # 3. Kích hoạt LangGraph
            output_state = esg_pipeline.invoke({
                "messages": [HumanMessage(content=input_message)] if input_message.strip() else [],
                "pdf_files": pdf_paths,
                # Nếu không gửi gì, mặc định là list rỗng thay vì ghi đè lên memory
            }, config=config)

        # --- XỬ LÝ KẾT QUẢ TRẢ VỀ CHO NODE.JS ---
        # Kiểm tra xem đồ thị có đang bị tạm dừng (Interrupt) chờ duyệt hóa đơn không?
        state_snapshot = esg_pipeline.get_state(config)
        needs_review = False
        if state_snapshot.next and "calculate" in state_snapshot.next:
            needs_review = True

        return {
            "status": "success",
            "thread_id": thread_id,
            "needs_review": needs_review,
            "messages": serialize_messages(output_state.get("messages", [])),
            "extracted_data": output_state.get("extracted_data", []),
            "emission_results": output_state.get("emission_results", []),
            "summary": output_state.get("summary", {}),
            "final_report": output_state.get("final_report"),
            "errors": output_state.get("errors", [])
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}

    finally:
        # Dọn dẹp rác: Xóa file tạm sau khi xử lý xong
        if tmp_dir and os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir)