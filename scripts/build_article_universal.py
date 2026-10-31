#!/usr/bin/env python3
"""
scripts/build_article_universal.py
==================================
Universal CLI builder for SaaS Matrix (saas-benchmarks.hoiki0808.workers.dev).
Compiles any Markdown article into a production-ready, dark-themed static page,
updates _redirects (307 Edge shortcuts), sitemap.xml, and the index.html grid.

Zero external dependencies outside standard Python 3.
Usable manually or by ANY AI model (Claude, GPT, Cursor, etc.).
"""

import os
import sys
import argparse
import re
from datetime import datetime

SITE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

def markdown_to_html(md_text):
    """Simple, clean markdown to HTML converter for benchmark articles."""
    lines = md_text.split("\n")
    html_lines = []
    in_table = False
    in_list = False

    for line in lines:
        stripped = line.strip()

        # Table row
        if stripped.startswith("|") and stripped.endswith("|"):
            if "---" in stripped:
                continue  # Skip separator
            cells = [c.strip() for c in stripped.split("|")[1:-1]]
            if not in_table:
                in_table = True
                html_lines.append('<div class="overflow-x-auto my-8"><table class="w-full text-left text-sm border-collapse border border-slate-800 rounded-xl"><tbody>')
            
            row_html = '<tr class="border-b border-slate-800 hover:bg-slate-900/40">'
            for cell in cells:
                # bold check
                cell = re.sub(r'\*\*(.*?)\*\*', r'<strong class="text-white">\1</strong>', cell)
                row_html += f'<td class="p-4 text-slate-300">{cell}</td>'
            row_html += '</tr>'
            html_lines.append(row_html)
            continue
        elif in_table:
            in_table = False
            html_lines.append('</tbody></table></div>')

        # List items
        if stripped.startswith("- ") or stripped.startswith("* "):
            if not in_list:
                in_list = True
                html_lines.append('<ul class="list-disc list-inside space-y-2 my-4 text-slate-300">')
            item_text = stripped[2:].strip()
            item_text = re.sub(r'\*\*(.*?)\*\*', r'<strong class="text-white">\1</strong>', item_text)
            html_lines.append(f'<li>{item_text}</li>')
            continue
        elif in_list:
            in_list = False
            html_lines.append('</ul>')

        # Headings
        if stripped.startswith("### "):
            text = stripped[4:].strip()
            html_lines.append(f'<h3 class="text-xl font-bold text-slate-100 mt-8 mb-3">{text}</h3>')
        elif stripped.startswith("## "):
            text = stripped[3:].strip()
            html_lines.append(f'<h2 class="text-2xl font-bold text-white mt-12 mb-4 pb-2 border-b border-slate-800">{text}</h2>')
        elif stripped.startswith("# "):
            text = stripped[2:].strip()
            html_lines.append(f'<h1 class="text-3xl sm:text-4xl font-extrabold text-white mb-6">{text}</h1>')
        elif stripped.startswith("> "):
            quote = stripped[2:].strip()
            quote = re.sub(r'\*\*(.*?)\*\*', r'<strong class="text-white">\1</strong>', quote)
            html_lines.append(f'<blockquote class="border-l-4 border-indigo-500 pl-4 py-2 my-6 bg-slate-900/60 rounded-r-lg text-slate-300 italic">{quote}</blockquote>')
        elif stripped:
            # Paragraph
            p = re.sub(r'\*\*(.*?)\*\*', r'<strong class="text-white">\1</strong>', stripped)
            p = re.sub(r'\[(.*?)\]\((.*?)\)', r'<a href="\2" class="text-indigo-400 hover:underline">\1</a>', p)
            html_lines.append(f'<p class="text-slate-300 leading-relaxed my-4">{p}</p>')

    if in_table:
        html_lines.append('</tbody></table></div>')
    if in_list:
        html_lines.append('</ul>')

    return "\n".join(html_lines)

def build_article(slug, title, subtitle, category, winner_slug, winner_url, runner_slug, runner_url, md_path):
    out_dir = os.path.join(SITE_ROOT, slug)
    os.makedirs(out_dir, exist_ok=True)

    with open(md_path, "r", encoding="utf-8") as f:
        md_content = f.read()

    body_html = markdown_to_html(md_content)
    now_iso = datetime.utcnow().strftime("%Y-%m-%d")

    page_html = f"""<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title} | SaaS Matrix</title>
    <meta name="description" content="{subtitle}">
    <link rel="canonical" href="https://saas-benchmarks.hoiki0808.workers.dev/{slug}/">
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {{
            darkMode: 'class',
            theme: {{
                extend: {{
                    colors: {{
                        brand: {{ 500: '#6366F1', 600: '#4F46E5', 700: '#4338CA' }}
                    }}
                }}
            }}
        }}
    </script>
</head>
<body class="bg-[#090D16] text-slate-100 min-h-screen font-sans antialiased selection:bg-indigo-500 selection:text-white">

    <header class="border-b border-slate-800/80 bg-[#090D16]/90 backdrop-blur sticky top-0 z-50">
        <div class="max-w-6xl mx-auto px-4 sm:px-6 h-16 flex items-center justify-between">
            <a href="/" class="flex items-center gap-3">
                <div class="w-8 h-8 rounded-lg bg-gradient-to-tr from-indigo-500 to-violet-500 flex items-center justify-center font-bold text-white shadow-lg shadow-indigo-500/20">M</div>
                <span class="font-bold text-lg tracking-tight bg-gradient-to-r from-white via-slate-200 to-indigo-300 bg-clip-text text-transparent">SaaS Matrix</span>
            </a>
            <div class="flex items-center gap-4 text-sm text-slate-400">
                <span class="px-2.5 py-0.5 rounded-full text-xs font-medium bg-indigo-500/10 text-indigo-400 border border-indigo-500/20">{category}</span>
                <span>{now_iso}</span>
            </div>
        </div>
    </header>

    <main class="max-w-4xl mx-auto px-4 sm:px-6 py-12">
        <div class="mb-8">
            <div class="flex items-center gap-2 text-xs font-semibold text-indigo-400 uppercase tracking-widest mb-3">
                <span>{category}</span> · <span>Deep Benchmark</span>
            </div>
            <h1 class="text-3xl sm:text-5xl font-extrabold text-white tracking-tight leading-tight mb-4">{title}</h1>
            <p class="text-lg sm:text-xl text-slate-400 leading-relaxed">{subtitle}</p>
        </div>

        <article class="prose prose-invert max-w-none">
            {body_html}
        </article>

        <!-- CTA Buttons -->
        <div class="my-12 p-8 rounded-2xl bg-gradient-to-b from-slate-900/90 to-indigo-950/40 border border-indigo-500/30 text-center">
            <h3 class="text-2xl font-bold text-white mb-2">Claim Exclusive Partner Access</h3>
            <p class="text-slate-400 text-sm mb-6">Redirecting through verified low-latency edge links</p>
            <div class="flex flex-wrap items-center justify-center gap-4">
                <a href="/{winner_slug}" target="_blank" rel="noopener noreferrer" class="px-6 py-3.5 rounded-xl bg-indigo-600 hover:bg-indigo-500 text-white font-semibold shadow-lg shadow-indigo-600/30 transition-all flex items-center gap-2">
                    <span>Try Top Pick ({winner_slug.capitalize()})</span>
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"/></svg>
                </a>
                <a href="/{runner_slug}" target="_blank" rel="noopener noreferrer" class="px-6 py-3.5 rounded-xl bg-slate-800 hover:bg-slate-700 text-slate-200 font-semibold border border-slate-700 transition-all">
                    <span>Explore Alternative ({runner_slug.capitalize()})</span>
                </a>
            </div>
        </div>
    </main>

    <footer class="border-t border-slate-800/80 py-8 text-center text-xs text-slate-500">
        <div class="max-w-6xl mx-auto px-4">
            <p>© 2026 SaaS Matrix. Independent benchmarks & cost telemetry for solopreneurs.</p>
        </div>
    </footer>
</body>
</html>
"""

    out_file = os.path.join(out_dir, "index.html")
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(page_html)
    print(f"✓ Generated article HTML: {out_file}")

    # Update _redirects
    redirects_path = os.path.join(SITE_ROOT, "_redirects")
    if os.path.exists(redirects_path):
        with open(redirects_path, "r", encoding="utf-8") as f:
            red_content = f.read()
        
        new_reds = []
        if winner_url and f"/{winner_slug} " not in red_content:
            new_reds.append(f"/{winner_slug}  {winner_url}  307")
        if runner_url and f"/{runner_slug} " not in red_content:
            new_reds.append(f"/{runner_slug}  {runner_url}  307")
        
        if new_reds:
            with open(redirects_path, "a", encoding="utf-8") as f:
                f.write("\n" + "\n".join(new_reds) + "\n")
            print(f"✓ Updated _redirects with {new_reds}")

    # Update sitemap.xml
    sitemap_path = os.path.join(SITE_ROOT, "sitemap.xml")
    if os.path.exists(sitemap_path):
        with open(sitemap_path, "r", encoding="utf-8") as f:
            sm_content = f.read()
        
        url_entry = f"  <url>\n    <loc>https://saas-benchmarks.hoiki0808.workers.dev/{slug}/</loc>\n    <lastmod>{now_iso}</lastmod>\n    <changefreq>weekly</changefreq>\n    <priority>0.85</priority>\n  </url>\n</urlset>"
        if f"/{slug}/" not in sm_content:
            sm_content = sm_content.replace("</urlset>", url_entry)
            with open(sitemap_path, "w", encoding="utf-8") as f:
                f.write(sm_content)
            print(f"✓ Updated sitemap.xml for /{slug}/")

    print("\n🚀 Article successfully built and mapped!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Universal SaaS Matrix Article Builder")
    parser.add_argument("--slug", required=True, help="URL slug (e.g. supabase-vs-firebase)")
    parser.add_argument("--title", required=True, help="Article title")
    parser.add_argument("--subtitle", required=True, help="Article subtitle/description")
    parser.add_argument("--category", default="AI & Developer Tools", help="Category name")
    parser.add_argument("--winner-slug", required=True, help="Winner affiliate short slug")
    parser.add_argument("--winner-url", required=True, help="Winner affiliate target URL")
    parser.add_argument("--runner-slug", required=True, help="Runner affiliate short slug")
    parser.add_argument("--runner-url", required=True, help="Runner affiliate target URL")
    parser.add_argument("--markdown", required=True, help="Path to markdown source file")

    args = parser.parse_args()
    build_article(
        args.slug, args.title, args.subtitle, args.category,
        args.winner_slug, args.winner_url, args.runner_slug, args.runner_url,
        args.markdown
    )
