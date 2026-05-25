"""
rag_builder.py — GreenChain
Chạy một lần để build FAISS index từ tài liệu ESG.

Cách dùng:
    python -m pipeline.rag_builder                          # index thư mục mặc định, dùng để cập nhật luôn
    python -m pipeline.rag_builder --docs data/esg_docs     # chỉ định thư mục khác
    python -m pipeline.rag_builder --reset                  # xoá index cũ và build lại

Kết quả lưu tại: data/faiss_index/  (2 file: index.faiss + index.pkl)
"""

import os
import argparse
from dotenv import load_dotenv

from langchain_community.vectorstores import FAISS
from langchain_community.vectorstores.utils import DistanceStrategy
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter, Language

load_dotenv()

# ============================================================
# CONFIG
# ============================================================
FAISS_INDEX_PATH = "data/faiss_index"
DEFAULT_DOCS_DIR = "data/esg_docs"

# Chunk size phù hợp cho văn bản pháp luật/tiêu chuẩn:
# - 1000 token đủ giữ nguyên 1 điều khoản
# - overlap 250 tránh mất ngữ cảnh ở ranh giới chunk
CHUNK_SIZE    = 1000
CHUNK_OVERLAP = 250

SUPPORTED_EXTENSIONS = [".pdf", ".txt", ".md"]


# ============================================================
# HELPERS
# ============================================================
def _get_embeddings():
    """Gọi trực tiếp Siêu Embedding (đã gom mọi API Key) từ llm_manager"""
    from pipeline.llm_manager import master_embeddings
    
    if master_embeddings is None:
        raise EnvironmentError("Không có model Embedding nào được cấu hình trong llm_manager.")
        
    return master_embeddings

def _get_indexed_files(vector_store: FAISS) -> set:
    """Quét ngầm bộ kho tài liệu của FAISS để trích xuất danh sách file đã tồn tại."""
    indexed_files = set()
    if vector_store and vector_store.docstore:
        # Duyệt qua toàn bộ các chunk đang lưu trong docstore của FAISS
        for doc_id, doc in vector_store.docstore._dict.items():
            src = doc.metadata.get("source_file")
            if src:
                indexed_files.add(src)
    return indexed_files

def _load_new_documents(docs_dir: str, indexed_files: set) -> list:
    """CHỈ đọc các file mới hoàn toàn, bỏ qua các file tên đã nằm trong indexed_files."""
    if not os.path.isdir(docs_dir):
        raise FileNotFoundError(f"Thư mục tài liệu không tồn tại: {docs_dir}")

    all_docs = []
    found_files = []

    import pathlib
    path = pathlib.Path(docs_dir)
    for ext in SUPPORTED_EXTENSIONS:
        found_files.extend([str(p) for p in path.rglob(f"*{ext}")])

    if not found_files:
        raise ValueError(f"Không tìm thấy file tài liệu nào trong: {docs_dir}")

    print(f"  🔍 Phát hiện tổng cộng {len(found_files)} file trong thư mục.")
    print(f"  🧠 Bộ nhớ FAISS hiện tại đã lưu trữ: {len(indexed_files)} file.")

    for file_path in sorted(found_files):
        filename = os.path.basename(file_path)
        
        # 🛡️ KIỂM TRA TRÙNG LẶP: Nếu file đã được tạo vector từ trước -> BỎ QUA NGAY
        if filename in indexed_files:
            print(f"  ⏭️  Bỏ qua (Đã được nhúng từ trước): {filename}")
            continue

        try:
            if file_path.endswith(".pdf"):
                loader = PyPDFLoader(file_path)
            else:
                loader = TextLoader(file_path, encoding="utf-8")

            docs = loader.load()
            for doc in docs:
                doc.metadata["source_file"] = filename

            all_docs.extend(docs)
            print(f"  🔥 Đọc file MỚI: {filename} ({len(docs)} trang/đoạn)")

        except Exception as e:
            print(f"  ✗ Lỗi đọc file {filename}: {e}")

    return all_docs


def _split_documents(docs: list) -> list:
    """Chia nhỏ document thành chunks."""
    md_separators = RecursiveCharacterTextSplitter.get_separators_for_language(Language.MARKDOWN)
    hybrid_separators = md_separators[:-2] + ["\. ", "。"] + md_separators[-2:]
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        add_start_index=True,       # Đánh dấu toạ độ (Hữu ích khi cần truy vết nguồn)
        strip_whitespace=True,      # Gọt sạch khoảng trắng thừa ở 2 đầu chunk
        is_separator_regex=True,
        # Bộ phân tách lai (Hybrid) hỗ trợ Markdown + TXT + PDF
        separators=hybrid_separators,
    )
    chunks = splitter.split_documents(docs)
    return chunks


# ============================================================
# PUBLIC API
# ============================================================
def build_index(docs_dir: str = DEFAULT_DOCS_DIR, reset: bool = False) -> FAISS:
    """
    Tự động cập nhật thêm file mới vào hệ thống FAISS hiện có mà không làm mất dữ liệu cũ.
    Dùng --reset nếu muốn xóa sạch làm lại từ đầu.
    """
    vector_store = None
    indexed_files = set()

    # Nếu không bấm reset và đã có db cũ trên đĩa cứng -> Tiến hành nạp lên để chuẩn bị nối đuôi
    if os.path.exists(FAISS_INDEX_PATH) and not reset:
        try:
            vector_store = load_index()
            indexed_files = _get_indexed_files(vector_store)
            print(f"[RAG] Đã nạp Database cũ. Đang kiểm tra cập nhật...")
        except Exception as e:
            print(f"⚠️ Không đọc được Database cũ ({e}). Sẽ tiến hành build mới hoàn toàn.")
            vector_store = None

    if reset:
        print("\n🧹 [RAG] Kích hoạt lệnh RESET: Xóa toàn bộ dữ liệu cũ để làm lại từ đầu...")
        indexed_files = set()
        vector_store = None

    # Bước 1: Chỉ lọc lấy tài liệu MỚI CHƯA CÓ TRONG DB
    docs = _load_new_documents(docs_dir, indexed_files)
    
    if not docs:
        print("\n✅ [Hoàn tất] Không phát hiện thêm file mới nào. Cơ sở dữ liệu đã đồng bộ 100%.")
        return vector_store

    # Bước 2: Chia nhỏ chunks cho tài liệu mới
    print("\n[Hệ thống] Chia nhỏ cấu trúc văn bản mới...")
    chunks = _split_documents(docs)
    print(f"  → Tạo thêm {len(chunks)} chunks mới.")

    # Bước 3: Tạo embedding và tích hợp lũy tiến
    embeddings = _get_embeddings()
    
    if vector_store is None:
        # Trường hợp DB trống hoặc bấm reset -> Khởi tạo khối lượng đầu tiên
        print("\n🎨 Khởi tạo bộ khung Database FAISS mới...")
        vector_store = FAISS.from_documents(chunks, embeddings, distance_strategy=DistanceStrategy.COSINE)
    else:
        # Trường hợp đã có sẵn DB -> CHỈ GỌI API ĐỂ EMBED FILE MỚI rồi nối đuôi vào sau
        print("\n🧬 Đang embed đoạn văn bản mới và nối đuôi vào hệ thống sẵn có...")
        vector_store.add_documents(chunks)

    # Lưu lại bản cập nhật xuống disk
    os.makedirs(FAISS_INDEX_PATH, exist_ok=True)
    vector_store.save_local(FAISS_INDEX_PATH)

    print(f"\n✅ Cập nhật lũy tiến thành công! Dữ liệu lưu tại: {FAISS_INDEX_PATH}/")
    print(f"   Tổng số lượng vector hiện tại trong DB: {vector_store.index.ntotal}")
    return vector_store


def load_index() -> FAISS:
    if not os.path.exists(FAISS_INDEX_PATH):
        raise FileNotFoundError(f"Chưa có FAISS index tại '{FAISS_INDEX_PATH}'. Chạy lệnh để tạo.")
    embeddings = _get_embeddings()
    return FAISS.load_local(
        FAISS_INDEX_PATH,
        embeddings,
        allow_dangerous_deserialization=True,
        distance_strategy=DistanceStrategy.COSINE,
    )


# ============================================================
# CLI
# ============================================================
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build/Update FAISS index cho GreenChain RAG")
    parser.add_argument("--docs",  default=DEFAULT_DOCS_DIR, help="Thư mục chứa tài liệu ESG")
    parser.add_argument("--reset", action="store_true",      help="Xoá sạch sành sanh và build lại")
    args = parser.parse_args()

    build_index(docs_dir=args.docs, reset=args.reset)