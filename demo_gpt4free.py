#!/usr/bin/env python3
"""
GPT4Free 演示脚本
演示如何免费使用大模型
"""

import sys
from pathlib import Path

# 添加 gpt4free 目录到路径
gpt4free_path = Path(__file__).parent / "gpt4free"
sys.path.insert(0, str(gpt4free_path))

try:
    from g4f.client import Client
    print("✅ 成功导入 g4f 库！")
except ImportError as e:
    print(f"❌ 导入失败: {e}")
    print("请先安装依赖：pip install -r /workspace/gpt4free/requirements.txt")
    sys.exit(1)


def demo_basic_chat():
    """基础对话演示"""
    print("\n" + "="*50)
    print("🎯 基础对话演示")
    print("="*50)
    
    client = Client()
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "你是一个乐于助人的助手，用中文回答。"},
                {"role": "user", "content": "你好，请介绍一下自己"}
            ],
        )
        
        print(f"\n🤖 AI 回复：")
        print(response.choices[0].message.content)
        return True
        
    except Exception as e:
        print(f"❌ 出错了: {e}")
        return False


def list_available_models():
    """列出可用模型"""
    print("\n" + "="*50)
    print("📋 可用模型示例")
    print("="*50)
    print("""
一些常用的模型：
- gpt-4o
- gpt-4
- claude-3-opus
- gemini-pro
- deepseek-chat
- qwen-turbo
- llama-3-70b
    """)


if __name__ == "__main__":
    print("🚀 GPT4Free 演示开始")
    print(f"📂 项目位置: {gpt4free_path}")
    
    # 列出可用模型
    list_available_models()
    
    # 进行基础对话演示
    success = demo_basic_chat()
    
    if success:
        print("\n" + "="*50)
        print("🎉 演示成功！你可以免费使用大模型了！")
        print("="*50)
        print("💡 更多使用方法请查看：")
        print("   - /workspace/如何免费使用大模型.md")
        print("   - /workspace/gpt4free/etc/examples/")
    else:
        print("\n" + "="*50)
        print("⚠️  演示遇到问题，请检查网络或查看项目文档")
        print("="*50)
