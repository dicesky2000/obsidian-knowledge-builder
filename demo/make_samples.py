# -*- coding: utf-8 -*-
"""生成演示用样例素材（放入 <库>/01未处理/），用于端到端验证。

用法：
  python demo/make_samples.py --vault D:\\test_vault

生成：
  - 电力机车牵引系统.pdf     （PDF 正文提取测试，中文用内置 CJK 字体写入）
  - 卡片盒笔记法实践心得.md  （Markdown 导入）
  - 豆包辅助知识管理笔记.txt （TXT 自动转 Markdown）
  - 轨道车辆标准体系与认证.md （Markdown 导入）
  - 灵感速写.png             （图片 → 06附件归档）
"""
import argparse
import os
import sys
import io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

PDF_TEXT = (
    "电力机车牵引系统概述\n"
    "牵引变流器是电力机车辅助供电与牵引系统的核心部件。\n"
    "IGBT 功率模块负责将直流电逆变为三相交流电驱动牵引电机。\n"
    "辅助供电系统为列车空调、照明与控制系统提供电源。\n"
    "设计需遵循 IEC 与国标要求，并通过型式试验认证。\n"
)

MD_CARD = """# 卡片盒笔记法实践心得

卢曼的卡片盒笔记法强调概念之间的连接而非存储。

- 每张卡片只记录一个概念
- 用 `[[双链]]` 建立网络
- 大脑只负责连接，不负责存储

这与 Obsidian 的双向链接机制天然契合。
"""

TXT_DOUBAN = """豆包与知识库自动化

用免费的 AI 工具（如豆包）配合知识库管理，可以实现零成本的内容提炼。

关键点：程序负责流程，AI 负责提炼，人负责创作。
"""

MD_STANDARD = """# 轨道车辆标准体系与认证

轨道交通装备设计需要遵循完整的标准体系：

- IEC 国际标准（IEC 60077 等）
- 中国国标 GB / TB
- 欧盟 EN 标准与互操作性技术规范

牵引与辅助供电系统的型式试验是认证的关键环节。
"""

PNG_TEXT = "灵感速写"  # 仅作文件名提示，图片内容不解析


def make_pdf(path: str) -> None:
    try:
        try:
            import pymupdf as fitz
        except Exception:
            import fitz
        doc = fitz.open()
        page = doc.new_page(width=595, height=842)  # A4
        rect = fitz.Rect(72, 72, 523, 400)
        page.insert_textbox(rect, PDF_TEXT, fontname="china-s", fontsize=14)
        doc.save(path)
        doc.close()
        print("[OK] 生成 PDF: %s" % path)
    except Exception as e:
        print("[FAIL] PDF 生成失败（不影响其他样例）: %s" % e)


def make_png(path: str) -> None:
    try:
        try:
            import pymupdf as fitz
        except Exception:
            import fitz
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 240, 120), False)
        pix.clear_with(0xE8E8F0)
        pix.save(path)
        print("[OK] 生成 PNG: %s" % path)
    except Exception as e:
        print("[FAIL] PNG 生成失败（不影响其他样例）: %s" % e)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--vault", required=True, help="知识库根目录")
    ap.add_argument("--inbox", default="01未处理", help="01未处理相对路径")
    args = ap.parse_args()

    inbox = os.path.join(args.vault, args.inbox)
    os.makedirs(inbox, exist_ok=True)
    print("样例目录: %s" % inbox)

    make_pdf(os.path.join(inbox, "电力机车牵引系统.pdf"))
    with open(os.path.join(inbox, "卡片盒笔记法实践心得.md"), "w", encoding="utf-8") as f:
        f.write(MD_CARD)
    print("[OK] 生成 MD: 卡片盒笔记法实践心得.md")
    with open(os.path.join(inbox, "豆包辅助知识管理笔记.txt"), "w", encoding="utf-8") as f:
        f.write(TXT_DOUBAN)
    print("[OK] 生成 TXT: 豆包辅助知识管理笔记.txt")
    with open(os.path.join(inbox, "轨道车辆标准体系与认证.md"), "w", encoding="utf-8") as f:
        f.write(MD_STANDARD)
    print("[OK] 生成 MD: 轨道车辆标准体系与认证.md")
    make_png(os.path.join(inbox, "灵感速写.png"))
    print("样例生成完成，共 5 个文件。")


if __name__ == "__main__":
    main()
