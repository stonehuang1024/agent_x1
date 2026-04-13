#!/usr/bin/env python3
"""
Parse DCN V2 PDF to extract key information
"""
import fitz  # PyMuPDF
import os

def parse_pdf(pdf_path, output_md_path):
    """Extract text from PDF and save as markdown"""
    doc = fitz.open(pdf_path)
    
    text = []
    text.append("# DCN V2: Improved Deep & Cross Network and Practical Lessons for Web-scale Learning to Rank Systems\n")
    text.append("**Authors:** Ruoxi Wang, Rakesh Shivanna, Derek Z. Cheng, Sagar Jain, Dong Lin, Lichan Hong, Ed H. Chi\n")
    text.append("**arXiv:** 2008.13535\n")
    text.append("**Published:** WWW 2021\n\n")
    text.append("---\n\n")
    
    for page_num in range(len(doc)):
        page = doc[page_num]
        page_text = page.get_text()
        text.append(f"## Page {page_num + 1}\n\n")
        text.append(page_text)
        text.append("\n\n---\n\n")
    
    doc.close()
    
    with open(output_md_path, 'w', encoding='utf-8') as f:
        f.write(''.join(text))
    
    print(f"Parsed PDF saved to: {output_md_path}")
    return output_md_path

if __name__ == "__main__":
    pdf_path = "/Users/simonwang/agent_x1/results/session/session_153909_2026-03-15_15-39-09/research/recommendation_research/papers/dcn_v2.pdf"
    output_path = "/Users/simonwang/agent_x1/results/session/session_153909_2026-03-15_15-39-09/research/recommendation_research/papers/dcn_v2.md"
    parse_pdf(pdf_path, output_path)
