import os
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from langchain_cohere import ChatCohere
from langchain_google_genai import GoogleGenerativeAIEmbeddings
from langchain_openai import OpenAIEmbeddings
from langchain_cohere import CohereEmbeddings
from langchain_core.embeddings import Embeddings
import hashlib

load_dotenv()

# ============================================================
# CẤU HÌNH KẾT NỐI PROXY FREELLMAPI
# ============================================================
FREELLM_KEY = "Tung0987612"
FREELLM_BASE_URL = "https://greenchain-esg-agent.onrender.com/v1"

# 1. Thực thể LLM đi qua Proxy (Dùng cho Chat, RAG, Viết báo cáo)
# Đặt model="auto" để bộ Router của FreeLLMAPI tự điều phối key tối ưu nhất
free_llm_proxy = ChatOpenAI(
    model="", 
    temperature=0,
    api_key=FREELLM_KEY,
    base_url=FREELLM_BASE_URL
)

# 2. Khởi tạo một model Gemini Native trực tiếp (Không qua Proxy)
# Mục đích: Làm phao cứu sinh (Fallback) và dùng riêng cho tác vụ đọc ẢNH (Vision)
gemini_flash_native = ChatGoogleGenerativeAI(
    model="gemini-2.5-flash",
    temperature=0,
    google_api_key=os.getenv("GEMINI_API_KEY") # Lấy key gốc trong .env của ông
)

# --- KHỞI TẠO CÁC CỤM MODEL CỐ ĐỊNH ---

# Master LLM chính: Ưu tiên bào token qua Proxy trước. 
# Nếu lỡ ông tắt proxy hoặc lỗi kết nối, hệ thống tự động nhảy về Gemini Native, không lo đứng bánh!
master_llm = free_llm_proxy.with_fallbacks([gemini_flash_native])

# Riêng luồng viết báo cáo nặng đô GRI 305, ta ép chạy qua proxy để tiết kiệm tuyệt đối
master_report_llm = free_llm_proxy


# ============================================================
# QUẢN LÝ MODEL EMBEDDING (NHÚNG VECTOR CHO RAG)
# ============================================================
import time
import json
import os

class ResilientEmbeddings(Embeddings):
    def __init__(self, embedder_list: list, model_label: str = "",
                 batch_size: int = 20,           # Số chunks mỗi lần gửi
                 checkpoint_path: str = None):   # Đường dẫn file checkpoint
        self.embedders        = [e for e in embedder_list if e is not None]
        self.model_label      = model_label
        self.batch_size       = batch_size
        self.checkpoint_path  = checkpoint_path or "data/faiss_index/.embed_checkpoint.json"
        if not self.embedders:
            raise ValueError("Không có Embedding key nào hoạt động.")

    def _embed_single_batch_with_retry(self, texts: list) -> list:
        """
        Gửi 1 batch nhỏ, retry với backoff nếu gặp rate limit.
        Thử lần lượt từng key nếu key hiện tại hết monthly quota.
        """
        RATE_LIMIT_CODES = ["429", "quota", "rate", "exhausted", "resource_exhausted"]
        MAX_RETRIES_PER_KEY = 4   # Số lần retry trước khi chuyển key
        BASE_WAIT = 15            # Giây chờ ban đầu (15 → 30 → 60 → 120)

        for key_idx, emb in enumerate(self.embedders):
            wait_time = BASE_WAIT
            for attempt in range(MAX_RETRIES_PER_KEY):
                try:
                    return emb.embed_documents(texts)

                except Exception as e:
                    err_str = str(e).lower()
                    is_rate_limit = any(code in err_str for code in RATE_LIMIT_CODES)

                    if is_rate_limit and attempt < MAX_RETRIES_PER_KEY - 1:
                        # Rate limit tạm thời → chờ rồi retry CÙNG key
                        print(f"  ⏳ [{self.model_label}] Key {key_idx+1} rate limit. "
                              f"Chờ {wait_time}s rồi retry ({attempt+1}/{MAX_RETRIES_PER_KEY-1})...")
                        time.sleep(wait_time)
                        wait_time = min(wait_time * 2, 120)  # Tối đa 120s
                    else:
                        # Hết monthly quota hoặc lỗi khác → chuyển key tiếp
                        print(f"  ⚠ [{self.model_label}] Key {key_idx+1} lỗi vĩnh viễn "
                              f"({type(e).__name__}). Chuyển key tiếp...")
                        break  # Thoát vòng retry, sang key tiếp

        # Tất cả key đều fail
        raise RuntimeError(
            f"\n{'='*50}\n"
            f"❌ TẤT CẢ KEY [{self.model_label}] đều hết quota!\n"
            f"Hướng dẫn:\n"
            f"  1. Thêm key mới vào .env: {self.model_label}_API_KEY_2=...\n"
            f"  2. Hoặc đổi model → rebuild index:\n"
            f"     python -m pipeline.rag_builder --reset\n"
            f"  3. Checkpoint đã lưu tại: {self.checkpoint_path}\n"
            f"     Chạy lại lệnh trên để tiếp tục từ chỗ dở.\n"
            f"{'='*50}"
        )

    def _load_checkpoint(self) -> dict:
        """Load checkpoint nếu có, trả về dict {text_hash: vector}."""
        if os.path.exists(self.checkpoint_path):
            try:
                with open(self.checkpoint_path, "r") as f:
                    data = json.load(f)
                print(f"  📂 Tìm thấy checkpoint: {len(data)} vectors đã embed trước đó.")
                return data
            except Exception:
                pass
        return {}

    def _save_checkpoint(self, cache: dict):
        """Lưu checkpoint ra file."""
        os.makedirs(os.path.dirname(self.checkpoint_path), exist_ok=True)
        with open(self.checkpoint_path, "w") as f:
            json.dump(cache, f)

    def embed_documents(self, texts: list) -> list:
        """
        Embed toàn bộ danh sách texts theo batch nhỏ.
        Có checkpoint để resume nếu bị gián đoạn.
        """
        # Load checkpoint
        cache      = self._load_checkpoint()
        results    = [None] * len(texts)
        todo_idxs  = []   # Index của các text chưa embed

        # Kiểm tra xem text nào đã có trong checkpoint
        for i, text in enumerate(texts):
            key = hashlib.md5(text.encode('utf-8')).hexdigest()
            if key in cache:
                results[i] = cache[key]
            else:
                todo_idxs.append(i)

        if not todo_idxs:
            print(f"  ✅ Tất cả {len(texts)} vectors đã có trong checkpoint, bỏ qua API call.")
            return results

        cached_count = len(texts) - len(todo_idxs)
        if cached_count > 0:
            print(f"  📂 Dùng {cached_count} vectors từ checkpoint, "
                  f"cần embed thêm {len(todo_idxs)} vectors.")

        # Chia thành batch nhỏ
        total_batches = (len(todo_idxs) + self.batch_size - 1) // self.batch_size
        print(f"  🔄 Embedding {len(todo_idxs)} chunks "
              f"({total_batches} batches × {self.batch_size})...")

        for batch_num in range(total_batches):
            batch_idxs = todo_idxs[batch_num * self.batch_size:(batch_num + 1) * self.batch_size]
            batch_texts = [texts[i] for i in batch_idxs]

            # Gửi batch với retry + key rotation
            batch_vectors = self._embed_single_batch_with_retry(batch_texts)

            # Lưu kết quả vào results và cache
            for i, vec in zip(batch_idxs, batch_vectors):
                results[i]           = vec
                cache[str(hash(texts[i]))] = vec

            # Lưu checkpoint sau mỗi batch
            self._save_checkpoint(cache)

            # Log tiến độ
            done = (batch_num + 1) * self.batch_size
            pct  = min(100, int(done / len(todo_idxs) * 100))
            print(f"  ✓ Batch {batch_num+1}/{total_batches} xong ({pct}%)")

            # Delay nhỏ giữa các batch để tránh rate limit
            if batch_num < total_batches - 1:
                time.sleep(1)

        # Xóa checkpoint khi hoàn thành toàn bộ
        if os.path.exists(self.checkpoint_path):
            os.remove(self.checkpoint_path)
            print("  🗑 Đã xóa checkpoint (embed hoàn tất).")

        return results

    def embed_query(self, text: str) -> list:
        """Embed 1 query — dùng trực tiếp, không cần batch hay checkpoint."""
        return self._embed_single_batch_with_retry([text])[0]


def get_embedding_clones(provider, model_name):
    """Tạo danh sách các bản sao Embedding model (cùng model, nhiều key)."""
    config = {
        "gemini": {
            "prefix": "GEMINI_API_KEY",
            "class":  GoogleGenerativeAIEmbeddings,
            "param":  "google_api_key"
        },
        "openai": {
            "prefix": "OPENAI_API_KEY",
            "class":  OpenAIEmbeddings,
            "param":  "openai_api_key"
        },
        "cohere": {
            "prefix": "COHERE_API_KEY",
            "class":  CohereEmbeddings,
            "param":  "cohere_api_key"
        },
    }
    cfg = config.get(provider.lower())
    if not cfg:
        return []

    keys = []
    primary = os.getenv(cfg["prefix"])
    if primary:
        keys.append(primary)
    for i in range(2, 6):
        extra = os.getenv(f"{cfg['prefix']}_{i}")
        if extra:
            keys.append(extra)

    if not keys:
        print(f"⚠ Không tìm thấy key nào cho embedding provider: {provider}")
        return []

    clones = []
    for k in keys:
        params = {"model": model_name, cfg["param"]: k}
        try:
            clones.append(cfg["class"](**params))
        except Exception as e:
            print(f"⚠ Không khởi tạo được embedding {provider} key: {e}")

    return clones


# Khởi tạo — CHỈ dùng 1 model duy nhất, nhiều key
# Để đổi model: thay dòng này + rebuild index
# ƯU TIÊN 1: Khởi tạo Cohere Multilingual (Giải pháp cứu cánh khi cạn token Gemini/OpenAI)
_cohere_embedding_clones = get_embedding_clones("cohere", "embed-multilingual-v3.0")

if _cohere_embedding_clones:
    print("🚀 [Hệ thống] Khởi tạo thành công: COHERE MULTILINGUAL EMBEDDING")
    master_embeddings = ResilientEmbeddings(
        embedder_list=_cohere_embedding_clones,
        model_label="COHERE_EMBEDDING",
        batch_size=40,  # Cohere Trial Key hỗ trợ xử lý batch size lớn hơn
    )
else:
    # ƯU TIÊN 2: Sập về dùng Gemini cũ nếu không tìm thấy COHERE_API_KEY trong file .env
    print("⚠ [Hệ thống] Không tìm thấy Cohere key -> Chuyển sang khởi tạo GEMINI EMBEDDING")
    _gemini_embedding_clones = get_embedding_clones("gemini", "models/gemini-embedding-001")
    
    if _gemini_embedding_clones:
        master_embeddings = ResilientEmbeddings(
            embedder_list=_gemini_embedding_clones,
            model_label="GEMINI_EMBEDDING",
            batch_size=20,
        )
    else:
        # ƯU TIÊN 3: Fallback cuối cùng sang OpenAI
        print("⚠ [Hệ thống] Không có Gemini key -> Thử OpenAI làm phương án cuối")
        _openai_embedding_clones = get_embedding_clones("openai", "text-embedding-3-small")
        if _openai_embedding_clones:
            master_embeddings = ResilientEmbeddings(
                embedder_list=_openai_embedding_clones,
                model_label="OPENAI_EMBEDDING"
            )
        else:
            print("❌ [Hệ thống CRITICAL] Không tìm thấy bất kỳ API Key Embedding nào hoạt động!")
            master_embeddings = None