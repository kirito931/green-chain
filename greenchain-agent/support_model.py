import os
from dotenv import load_dotenv

# 1. Tải API key từ file .env
load_dotenv()

print("=" * 50)
print("KIỂM TRA DANH SÁCH MODEL ĐƯỢC HỖ TRỢ")
print("=" * 50)

# # ============================================================
# # PHÂN ĐOẠN 1: GOOGLE GEMINI
# # ============================================================
# print("\n[1] GOOGLE GEMINI MODELS")
# print("-" * 30)
# try:
#     from google import genai
#     gemini_key = os.getenv("GEMINI_API_KEY")
#     if gemini_key:
#         client_gemini = genai.Client(api_key=gemini_key)
#         for model_info in client_gemini.models.list():
#             print(f"- {model_info.name}")
#     else:
#         print("⚠ Không tìm thấy GEMINI_API_KEY trong file .env")
# except Exception as e:
#     print(f"⚠ Lỗi khi lấy model Gemini: {e}")


# # ============================================================
# # PHÂN ĐOẠN 2: GROQ (LLAMA, MIXTRAL...)
# # ============================================================
# print("\n[2] GROQ MODELS")
# print("-" * 30)
# try:
#     from groq import Groq
#     groq_key = os.getenv("GROQ_API_KEY")
#     if groq_key:
#         client_groq = Groq(api_key=groq_key)
#         # Lấy danh sách models từ Groq
#         groq_models = client_groq.models.list()
#         # Groq lưu danh sách bên trong thuộc tính 'data', mỗi model dùng thuộc tính 'id'
#         for m in groq_models.data:
#             print(f"- {m.id}")
#     else:
#         print("⚠ Không tìm thấy GROQ_API_KEY trong file .env")
# except ImportError:
#     print("⚠ Chưa cài đặt thư viện 'groq'. Hãy chạy: pip install groq")
# except Exception as e:
#     print(f"⚠ Lỗi khi lấy model Groq: {e}")


# # ============================================================
# # PHÂN ĐOẠN 3: COHERE (COMMAND-R...)
# # ============================================================
# print("\n[3] COHERE MODELS")
# print("-" * 30)
# try:
#     import cohere
#     cohere_key = os.getenv("COHERE_API_KEY")
#     if cohere_key:
#         # Khởi tạo client Cohere bằng V1 API (chuẩn nhất hiện tại)
#         client_cohere = cohere.Client(api_key=cohere_key)
#         cohere_models = client_cohere.models.list()
        
#         # Tùy phiên bản SDK, list có thể nằm trực tiếp hoặc trong thuộc tính 'models'
#         model_list = getattr(cohere_models, 'models', cohere_models)
#         for m in model_list:
#             print(f"- {getattr(m, 'name', str(m))}")
#     else:
#         print("⚠ Không tìm thấy COHERE_API_KEY trong file .env")
# except ImportError:
#     print("⚠ Chưa cài đặt thư viện 'cohere'. Hãy chạy: pip install cohere")
# except Exception as e:
#     print(f"⚠ Lỗi khi lấy model Cohere: {e}")

# print("\n" + "=" * 50)
# print("HOÀN TẤT KIỂM TRA!")

# # ============================================================
# # PHÂN ĐOẠN 4: OPENROUTER
# # ============================================================
# print("\n[4] OPENROUTER MODELS")
# print("-" * 30)
# try:
#     import requests
#     # OpenRouter cung cấp một endpoint public để lấy danh sách model
#     url = "https://openrouter.ai/api/v1/models"
#     response = requests.get(url)
    
#     if response.status_code == 200:
#         models_data = response.json().get("data", [])
        
#         # Chỉ in ra ID của các model để màn hình Terminal không bị trôi quá dài
#         for m in models_data:
#             model_id = m.get('id')
#             # Đánh dấu các model miễn phí cho dễ nhìn
#             pricing = m.get('pricing', {})
#             is_free = (pricing.get('prompt') == "0" and pricing.get('completion') == "0")
            
#             if is_free:
#                 print(f"- {model_id} [MIỄN PHÍ]")
#             else:
#                 print(f"- {model_id}")
                
#         print(f"\n=> Tổng cộng tìm thấy {len(models_data)} models trên OpenRouter!")
#     else:
#         print(f"⚠ Lỗi khi gọi API OpenRouter: Mã lỗi {response.status_code}")
# except ImportError:
#     print("⚠ Chưa cài đặt thư viện 'requests'. Hãy chạy: pip install requests")
# except Exception as e:
#     print(f"⚠ Lỗi khi lấy model OpenRouter: {e}")

# print("\n" + "=" * 50)
# print("HOÀN TẤT KIỂM TRA!")

# ============================================================
# PHÂN ĐOẠN 5: OPENAI
# ============================================================
print("\n[5] OPENAI MODELS")
print("-" * 30)
try:
    from openai import OpenAI
    openai_key = os.getenv("OPENAI_API_KEY")
    if openai_key:
        client_openai = OpenAI(api_key=openai_key)
        openai_models = client_openai.models.list()
        
        # Trích xuất ID model và sắp xếp theo bảng chữ cái cho dễ nhìn
        models = sorted([m.id for m in openai_models.data])
        for m_id in models:
            print(f"- {m_id}")
            
        print(f"\n=> Tổng cộng tìm thấy {len(models)} models trên OpenAI!")
    else:
        print("⚠ Không tìm thấy OPENAI_API_KEY trong file .env")
except ImportError:
    print("⚠ Chưa cài đặt thư viện 'openai'. Hãy chạy: pip install openai")
except Exception as e:
    print(f"⚠ Lỗi khi gọi API OpenAI: {e}")

print("\n" + "=" * 50)
print("HOÀN TẤT KIỂM TRA!")