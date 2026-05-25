"use client";

import { useState, useRef, FormEvent } from "react";
import axios from "axios";
import ReactMarkdown from "react-markdown";
import { Paperclip, Send, Bot, User, FileText } from "lucide-react";

// Dùng crypto để tạo ID phiên chat duy nhất
const THREAD_ID = typeof window !== "undefined" ? crypto.randomUUID() : "default-thread";

export default function ChatInterface() {
  const [messages, setMessages] = useState([
    { role: "assistant", content: "🌿 **Chào bạn! Tôi là GreenChain — ESG Carbon Intelligence Agent.**\n\nHãy đính kèm hóa đơn hoặc đặt câu hỏi về tiêu chuẩn GHG Protocol, tôi sẽ hỗ trợ ngay." }
  ]);
  const [input, setInput] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Hàm xử lý gửi tin nhắn sang FastAPI
  const handleSendMessage = async (e: FormEvent) => {
    e.preventDefault();
    if (!input.trim() && files.length === 0) return;

    // 1. Cập nhật UI ngay lập tức với tin nhắn của user
    const newMessages = [...messages];
    if (input.trim()) {
      newMessages.push({ role: "user", content: input });
    }
    if (files.length > 0) {
      newMessages.push({ role: "user", content: `*[Đính kèm ${files.length} tệp: ${files.map(f => f.name).join(", ")}]*` });
    }
    setMessages(newMessages);
    setInput("");
    setIsLoading(true);

    // 2. Đóng gói dữ liệu dạng Form (để gửi kèm file sang Python)
    const formData = new FormData();
    formData.append("thread_id", THREAD_ID);
    formData.append("action", "chat");
    formData.append("message", input);
    files.forEach((file) => formData.append("files", file));

    // Clear file UI sau khi đã gói vào form
    setFiles([]); 

    try {
      // 3. Bắn sang cổng 8000 của FastAPI
      const response = await axios.post("http://localhost:8000/api/chat", formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });

      // 4. Lấy câu trả lời của AI và cập nhật lên màn hình
      const data = response.data;
      if (data.status === "success") {
        // Lấy tin nhắn cuối cùng (thường là của AI)
        const agentReplies = data.messages.filter((m: any) => m.role === "assistant");
        const lastReply = agentReplies[agentReplies.length - 1];
        
        if (lastReply) {
          setMessages((prev) => [...prev, { role: "assistant", content: lastReply.content }]);
        }
      } else {
        setMessages((prev) => [...prev, { role: "assistant", content: "❌ Lỗi hệ thống: " + data.message }]);
      }
    } catch (error) {
      setMessages((prev) => [...prev, { role: "assistant", content: "❌ Không thể kết nối đến Máy chủ AI." }]);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="flex h-screen bg-gray-50 text-gray-800 font-sans">
      
      {/* CỘT TRÁI - SIDEBAR (Có thể làm lịch sử chat sau) */}
      <div className="w-64 bg-white border-r border-gray-200 p-4 hidden md:flex flex-col">
        <h2 className="text-xl font-bold text-emerald-700 flex items-center gap-2 mb-6">
          <Bot size={24} /> GreenChain
        </h2>
        <div className="text-sm text-gray-500 mb-2 font-semibold">Cơ sở dữ liệu (Source of Truth)</div>
        <ul className="text-xs text-gray-400 space-y-2">
          <li>• Điện lưới VN: 0.4936 tCO2e/MWh</li>
          <li>• Dầu Diesel: 2.68 kgCO2e/lít</li>
          <li>• Rác chôn lấp: 580 kgCO2e/tấn</li>
        </ul>
      </div>

      {/* CỘT PHẢI - KHU VỰC CHAT */}
      <div className="flex-1 flex flex-col h-full relative">
        
        {/* Lịch sử tin nhắn */}
        <div className="flex-1 overflow-y-auto p-4 sm:p-6 pb-32">
          <div className="max-w-3xl mx-auto space-y-6">
            {messages.map((msg, idx) => (
              <div key={idx} className={`flex gap-4 ${msg.role === "user" ? "flex-row-reverse" : "flex-row"}`}>
                
                {/* Avatar */}
                <div className={`w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0 ${msg.role === "user" ? "bg-emerald-600 text-white" : "bg-gray-200 text-emerald-700"}`}>
                  {msg.role === "user" ? <User size={18} /> : <Bot size={18} />}
                </div>

                {/* Nội dung tin nhắn */}
                <div className={`max-w-[80%] rounded-2xl px-5 py-3 ${msg.role === "user" ? "bg-emerald-50 text-emerald-900" : "bg-white border border-gray-100 shadow-sm"}`}>
                  <div className="prose prose-sm prose-emerald max-w-none">
                    <ReactMarkdown>
                      {msg.content}
                    </ReactMarkdown>
                  </div>
                </div>

              </div>
            ))}
            
            {isLoading && (
              <div className="flex gap-4 flex-row animate-pulse">
                <div className="w-8 h-8 rounded-full bg-gray-200 flex items-center justify-center"><Bot size={18} className="text-gray-400" /></div>
                <div className="bg-white border border-gray-100 shadow-sm rounded-2xl px-5 py-3 text-gray-400 text-sm">Đang phân tích dữ liệu...</div>
              </div>
            )}
          </div>
        </div>

        {/* Thanh Input dính dưới đáy (Sticky Bottom) */}
        <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-gray-50 via-gray-50 to-transparent pt-10 pb-6 px-4">
          <div className="max-w-3xl mx-auto">
            
            {/* Hiển thị file nháp đang đính kèm */}
            {files.length > 0 && (
              <div className="flex gap-2 mb-2 flex-wrap">
                {files.map((f, i) => (
                  <div key={i} className="bg-white border border-gray-200 text-xs text-gray-600 px-3 py-1.5 rounded-full flex items-center gap-2 shadow-sm">
                    <FileText size={14} className="text-emerald-600" /> {f.name}
                  </div>
                ))}
              </div>
            )}

            {/* Khung Gõ Chat */}
            <form onSubmit={handleSendMessage} className="bg-white border border-gray-300 rounded-2xl shadow-sm flex items-end p-2 transition-all focus-within:ring-2 focus-within:ring-emerald-500/50 focus-within:border-emerald-500">
              
              {/* Nút Upload File ẩn */}
              <input 
                type="file" multiple className="hidden" ref={fileInputRef}
                onChange={(e) => setFiles(Array.from(e.target.files || []))}
                accept=".pdf,.png,.jpg,.jpeg"
              />
              
              {/* Nút kẹp ghim (Mở thư mục) */}
              <button type="button" onClick={() => fileInputRef.current?.click()} className="p-3 text-gray-400 hover:text-emerald-600 transition-colors rounded-xl">
                <Paperclip size={20} />
              </button>

              <textarea 
                rows={1}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); handleSendMessage(e); } }}
                placeholder="Nhập yêu cầu phân tích hóa đơn hoặc đặt câu hỏi..."
                className="flex-1 max-h-32 min-h-[44px] bg-transparent border-none focus:outline-none focus:ring-0 resize-none py-3 px-2 text-sm"
              />

              <button type="submit" disabled={isLoading || (!input.trim() && files.length === 0)} className="p-3 bg-emerald-600 text-white hover:bg-emerald-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors rounded-xl m-1">
                <Send size={18} />
              </button>
            </form>
            <div className="text-center mt-2 text-[11px] text-gray-400">GreenChain AI có thể mắc sai lầm. Hãy kiểm tra các thông số pháp lý quan trọng.</div>
          </div>
        </div>
      </div>
    </div>
  );
}