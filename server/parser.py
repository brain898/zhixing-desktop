import re
from pathlib import Path
from typing import List, Dict, Any, Optional

def parse_file_to_blocks(file_path: Path, file_type: str) -> List[Dict[str, Any]]:
    """
    结构化正文解析引擎。
    支持格式：PDF (文本类), DOCX, TXT, Markdown。
    返回结构块列表：
    [
        {
            "block_index": int,
            "block_type": "heading" | "paragraph" | "table" | "list_item",
            "heading_path": Optional[str],
            "page_number": Optional[int],
            "paragraph_anchor": str,
            "text_content": str
        },
        ...
    ]
    """
    ext = file_path.suffix.lower()
    if ext == ".pdf":
        return _parse_pdf(file_path)
    elif ext == ".docx":
        return _parse_docx(file_path)
    elif ext in (".txt", ".text"):
        return _parse_text(file_path, is_markdown=False)
    elif ext in (".md", ".markdown"):
        return _parse_text(file_path, is_markdown=True)
    else:
        raise ValueError(f"不支持的文件类型: {ext}，当前仅支持 .pdf, .docx, .txt, .md")

def _parse_pdf(file_path: Path) -> List[Dict[str, Any]]:
    import pypdf
    try:
        reader = pypdf.PdfReader(str(file_path))
    except Exception as e:
        raise ValueError(f"PDF 文件损坏或格式错误: {str(e)}")

    if reader.is_encrypted:
        raise ValueError("PDF 文件已加密，请解除密码保护后重新上传")

    blocks: List[Dict[str, Any]] = []
    block_idx = 0
    total_text = ""
    current_heading: Optional[str] = None

    for page_num, page in enumerate(reader.pages, start=1):
        try:
            page_text = page.extract_text() or ""
        except Exception as e:
            page_text = ""

        page_trimmed = page_text.strip()
        if not page_trimmed:
            continue
        total_text += page_trimmed

        # 尝试按段落拆分
        raw_paras = [p.strip() for p in page_text.split("\n\n") if p.strip()]
        if not raw_paras:
            raw_paras = [p.strip() for p in page_text.split("\n") if p.strip()]

        for p_idx, para in enumerate(raw_paras):
            # 识别可能的标题（长度短、无结尾标点或符合章节序号特征）
            is_heading = (
                len(para) <= 45
                and not para.endswith(("。", "；", ";", "...", "”", "’"))
                and (
                    bool(re.match(r"^(第[一二三四五六七八九十0-9]+[章节条部分]|[\d\.]+\s+|[一二三四五六七八九十]、)", para))
                    or len(para) <= 25
                )
            )

            if is_heading:
                current_heading = para
                block_type = "heading"
            else:
                block_type = "paragraph"

            blocks.append({
                "block_index": block_idx,
                "block_type": block_type,
                "heading_path": current_heading,
                "page_number": page_num,
                "paragraph_anchor": f"page_{page_num}_p_{p_idx + 1}",
                "text_content": para,
            })
            block_idx += 1

    if not total_text.strip() or not blocks:
        raise ValueError("PDF 为纯图片扫描件或无文字层，本阶段暂不支持 OCR 识别")

    return blocks

def _parse_docx(file_path: Path) -> List[Dict[str, Any]]:
    import docx
    try:
        doc = docx.Document(str(file_path))
    except Exception as e:
        raise ValueError(f"DOCX 文件损坏或格式错误: {str(e)}")

    blocks: List[Dict[str, Any]] = []
    block_idx = 0
    current_headings: Dict[int, str] = {}

    # 保留原文中段落和表格的视觉与上下文顺序
    for elem in doc.element.body:
        if elem.tag.endswith("p"):
            p = docx.text.paragraph.Paragraph(elem, doc)
            text = p.text.strip()
            if not text:
                continue

            style_name = p.style.name if p.style else ""
            is_heading = (
                style_name.startswith("Heading")
                or style_name.startswith("标题")
                or (
                    bool(re.match(r"^(第[一二三四五六七八九十0-9]+[章节部分]|[\d\.]+\s+|[一二三四五六七八九十]、)", text))
                    and len(text) <= 35
                    and not text.endswith(("。", "；", ";"))
                )
            )

            level = 1
            if "1" in style_name:
                level = 1
            elif "2" in style_name:
                level = 2
            elif "3" in style_name:
                level = 3
            elif "4" in style_name:
                level = 4

            if is_heading:
                current_headings = {k: v for k, v in current_headings.items() if k < level}
                current_headings[level] = text
                heading_path = " / ".join([current_headings[k] for k in sorted(current_headings.keys())])
                blocks.append({
                    "block_index": block_idx,
                    "block_type": "heading",
                    "heading_path": heading_path,
                    "page_number": None,
                    "paragraph_anchor": f"p_{block_idx + 1}",
                    "text_content": text,
                })
            else:
                heading_path = " / ".join([current_headings[k] for k in sorted(current_headings.keys())]) if current_headings else None
                # 判断列表项
                is_list = style_name.startswith("List") or text.startswith(("•", "-", "*", "·"))
                block_type = "list_item" if is_list else "paragraph"
                blocks.append({
                    "block_index": block_idx,
                    "block_type": block_type,
                    "heading_path": heading_path,
                    "page_number": None,
                    "paragraph_anchor": f"p_{block_idx + 1}",
                    "text_content": text,
                })
            block_idx += 1

        elif elem.tag.endswith("tbl"):
            t = docx.table.Table(elem, doc)
            rows_data: List[str] = []
            for row in t.rows:
                row_cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
                # 去重相邻相同单元格（合并单元格现象）
                cleaned_cells = []
                for c in row_cells:
                    if not cleaned_cells or c != cleaned_cells[-1]:
                        cleaned_cells.append(c)
                if any(cleaned_cells):
                    rows_data.append(" | ".join(cleaned_cells))

            if rows_data:
                table_text = "\n".join(rows_data)
                heading_path = " / ".join([current_headings[k] for k in sorted(current_headings.keys())]) if current_headings else None
                blocks.append({
                    "block_index": block_idx,
                    "block_type": "table",
                    "heading_path": heading_path,
                    "page_number": None,
                    "paragraph_anchor": f"tbl_{block_idx + 1}",
                    "text_content": table_text,
                })
                block_idx += 1

    if not blocks:
        raise ValueError("DOCX 文件无有效正文或表格内容")

    return blocks

def _parse_text(file_path: Path, is_markdown: bool = False) -> List[Dict[str, Any]]:
    # 尝试多种常见编码读取
    encodings = ["utf-8", "utf-8-sig", "gbk", "gb2312", "gb18030"]
    content: Optional[str] = None
    for enc in encodings:
        try:
            with open(file_path, "r", encoding=enc) as f:
                content = f.read()
            break
        except UnicodeDecodeError:
            continue

    if content is None:
        raise ValueError("文本文件字符编码不受支持（非 UTF-8 / GBK 编码）")

    lines = content.splitlines()
    blocks: List[Dict[str, Any]] = []
    block_idx = 0
    current_headings: Dict[int, str] = {}
    current_para_lines: List[str] = []
    para_start_line = 1

    def flush_para():
        nonlocal block_idx, current_para_lines, para_start_line
        if current_para_lines:
            text = "\n".join(current_para_lines).strip()
            if text:
                heading_path = " / ".join([current_headings[k] for k in sorted(current_headings.keys())]) if current_headings else None
                is_list = text.startswith(("•", "-", "*", "1.", "2.", "3.", "·"))
                block_type = "list_item" if is_list else "paragraph"
                blocks.append({
                    "block_index": block_idx,
                    "block_type": block_type,
                    "heading_path": heading_path,
                    "page_number": None,
                    "paragraph_anchor": f"line_{para_start_line}",
                    "text_content": text,
                })
                block_idx += 1
            current_para_lines = []

    for line_idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            flush_para()
            continue

        if is_markdown and stripped.startswith("#"):
            flush_para()
            level = 0
            while level < len(stripped) and stripped[level] == "#":
                level += 1
            heading_text = stripped[level:].strip()
            current_headings = {k: v for k, v in current_headings.items() if k < level}
            current_headings[level] = heading_text
            heading_path = " / ".join([current_headings[k] for k in sorted(current_headings.keys())])
            blocks.append({
                "block_index": block_idx,
                "block_type": "heading",
                "heading_path": heading_path,
                "page_number": None,
                "paragraph_anchor": f"line_{line_idx}",
                "text_content": heading_text,
            })
            block_idx += 1
        else:
            if not current_para_lines:
                para_start_line = line_idx
            current_para_lines.append(stripped)

    flush_para()

    if not blocks:
        raise ValueError("文件内容为空或无有效文本")

    return blocks
