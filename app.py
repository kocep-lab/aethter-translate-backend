import os
import re
import sys
import requests
from flask import Flask, request, jsonify, render_template
from bs4 import BeautifulSoup, Comment, NavigableString
from urllib.parse import urljoin
from pypinyin import pinyin, Style
import jieba
from concurrent.futures import ThreadPoolExecutor
from chinese_english_lookup import Dictionary

# Initialize local offline dictionary for fast translations and bypassing rate-limits
print("Loading offline CC-CEDICT dictionary...", flush=True)
offline_dict = Dictionary()
print("Offline dictionary loaded successfully.", flush=True)

# Initialize Flask App
app = Flask(__name__, template_folder='templates', static_folder='static')

# Set standard headers for fetching websites
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

# Circuit Breakers to prevent hanging on rate-limited/down APIs
google_at_blocked = False
google_gtx_blocked = False
lingva_blocked = False
mymemory_blocked = False
LAST_RESET_TIME = 0

def check_reset_circuit_breakers():
    global google_at_blocked, google_gtx_blocked, lingva_blocked, mymemory_blocked, LAST_RESET_TIME
    import time
    now = time.time()
    if now - LAST_RESET_TIME > 120:  # Reset every 2 minutes
        google_at_blocked = False
        google_gtx_blocked = False
        lingva_blocked = False
        mymemory_blocked = False
        LAST_RESET_TIME = now

def translate_en_to_zh(text):
    """
    Translates English text to Simplified Chinese using the free Google Translate API.
    Supports client fallback (at -> gtx) and MyMemory fallback.
    """
    global google_at_blocked, google_gtx_blocked, lingva_blocked, mymemory_blocked
    check_reset_circuit_breakers()
    
    if not text.strip():
        return ""
    
    url = "https://translate.google.com/translate_a/single"
    params = {
        "client": "at",
        "sl": "en",
        "tl": "zh-CN",
        "dt": "t"
    }
    data = {
        "q": text
    }
    
    # 1. Try client=at
    if not google_at_blocked:
        try:
            response = requests.post(url, params=params, data=data, headers=HEADERS, timeout=3)
            if response.status_code == 200:
                res_data = response.json()
                return "".join([segment[0] for segment in res_data[0] if segment[0]])
            elif response.status_code == 429:
                google_at_blocked = True
        except Exception:
            pass
        
    # 2. Try client=gtx
    if not google_gtx_blocked:
        params["client"] = "gtx"
        try:
            response = requests.post(url, params=params, data=data, timeout=3)
            if response.status_code == 200:
                res_data = response.json()
                return "".join([segment[0] for segment in res_data[0] if segment[0]])
            elif response.status_code == 429:
                google_gtx_blocked = True
        except Exception:
            pass

    # 3. Try Lingva Translate (privacy proxy for Google Translate)
    if not lingva_blocked:
        try:
            from urllib.parse import quote
            lingva_url = f"https://lingva.ml/api/v1/en/zh/{quote(text)}"
            response = requests.get(lingva_url, timeout=3)
            if response.status_code == 200:
                val = response.json().get("translation", "")
                if val:
                    return val
            elif response.status_code == 429:
                lingva_blocked = True
        except Exception:
            pass
        
    # 4. Fallback to MyMemory
    if not mymemory_blocked:
        try:
            mm_url = "https://api.mymemory.translated.net/get"
            mm_params = {
                "q": text.strip().replace('\n', ' '),
                "langpair": "en|zh-CN",
                "de": "aethertranslate_test@example.com"
            }
            response = requests.get(mm_url, params=mm_params, timeout=2)
            if response.status_code == 429 or (response.status_code == 200 and "MYMEMORY WARNING" in response.text):
                mymemory_blocked = True
            elif response.status_code == 200:
                val = response.json()["responseData"]["translatedText"]
                if val and not val.startswith("MYMEMORY WARNING"):
                    return val
        except Exception:
            pass
        
    return text

def translate_zh_to_en_batch(words):
    """
    Translates a list of Chinese words/phrases to English.
    First tries looking up words in the local offline CC-CEDICT dictionary.
    For any words not found locally, falls back to batch translating them
    using Google Translate / Lingva / MyMemory.
    """
    global google_at_blocked, google_gtx_blocked, lingva_blocked, mymemory_blocked
    if not words:
        return []
        
    final_results = [""] * len(words)
    needs_external_translation = []
    needs_external_indices = []
    
    # 1. Lookup in offline dictionary
    for i, w in enumerate(words):
        w_clean = w.strip()
        if w_clean and re.search(r'[\u4e00-\u9fff]', w_clean):
            entry = offline_dict.lookup(w_clean)
            if entry:
                all_defs = []
                for def_entry in entry.definition_entries:
                    joined_def = "; ".join(def_entry.definitions)
                    all_defs.append(joined_def)
                if len(all_defs) > 1:
                    final_results[i] = "; ".join(f"({idx+1}) {d}" for idx, d in enumerate(all_defs))
                else:
                    final_results[i] = all_defs[0] if all_defs else ""
            else:
                needs_external_translation.append(w_clean)
                needs_external_indices.append(i)
        else:
            final_results[i] = ""
            
    # 2. Translate any misses externally in batch
    if needs_external_translation:
        print(f"Offline dict missed {len(needs_external_translation)} words. Translating externally...", file=sys.stderr)
        translated_external = translate_zh_to_en_batch_external(needs_external_translation)
        for idx, trans in zip(needs_external_indices, translated_external):
            final_results[idx] = trans
            
    return final_results

def translate_zh_to_en_batch_external(words):
    """
    Translates a list of Chinese words/phrases to English in chunks of 15
    in parallel using ThreadPoolExecutor.
    """
    global google_at_blocked, google_gtx_blocked, lingva_blocked, mymemory_blocked
    check_reset_circuit_breakers()

    if not words:
        return []
        
    clean_words = []
    word_indices = []
    for i, w in enumerate(words):
        if w.strip() and re.search(r'[\u4e00-\u9fff]', w):
            clean_words.append(w)
            word_indices.append(i)
            
    if not clean_words:
        return [""] * len(words)
        
    results = [None] * len(clean_words)
    batch_size = 15
    chunks = []
    chunk_starts = []
    
    for start_idx in range(0, len(clean_words), batch_size):
        chunks.append(clean_words[start_idx : start_idx + batch_size])
        chunk_starts.append(start_idx)
        
    def translate_chunk(args):
        start_idx, chunk = args
        prefixed_items = [f"[{i}] {w.strip()}" for i, w in enumerate(chunk)]
        payload = "\n".join(prefixed_items)
        
        translated_payload = ""
        # 1. Try Google client=at
        if not google_at_blocked:
            url = "https://translate.google.com/translate_a/single"
            params = {"client": "at", "sl": "zh-CN", "tl": "en", "dt": "t"}
            data = {"q": payload}
            try:
                response = requests.post(url, params=params, data=data, headers=HEADERS, timeout=3)
                if response.status_code == 200:
                    translated_payload = parse_batch_to_string(response.json())
                elif response.status_code == 429:
                    globals()['google_at_blocked'] = True
            except Exception:
                pass
                
        # 2. Try Google client=gtx
        if not translated_payload and not google_gtx_blocked:
            url = "https://translate.google.com/translate_a/single"
            params = {"client": "gtx", "sl": "zh-CN", "tl": "en", "dt": "t"}
            data = {"q": payload}
            try:
                response = requests.post(url, params=params, data=data, timeout=3)
                if response.status_code == 200:
                    translated_payload = parse_batch_to_string(response.json())
                elif response.status_code == 429:
                    globals()['google_gtx_blocked'] = True
            except Exception:
                pass

        # 3. Try Lingva
        if not translated_payload and not lingva_blocked:
            lingva_payload = " | ".join(prefixed_items)
            try:
                from urllib.parse import quote
                lingva_url = f"https://lingva.ml/api/v1/zh/en/{quote(lingva_payload)}"
                response = requests.get(lingva_url, timeout=5)
                if response.status_code == 200:
                    val = response.json().get("translation", "")
                    if val:
                        translated_payload = val.replace("|", "\n").replace("｜", "\n")
                elif response.status_code == 429:
                    globals()['lingva_blocked'] = True
            except Exception:
                pass

        # 4. Try MyMemory
        if not translated_payload and not mymemory_blocked:
            def translate_single_word(args_inner):
                idx_inner, w_inner = args_inner
                if globals()['mymemory_blocked']:
                    return idx_inner, w_inner
                try:
                    mm_url = "https://api.mymemory.translated.net/get"
                    mm_params = {
                        "q": w_inner,
                        "langpair": "zh-CN|en",
                        "de": "aethertranslate_test@example.com"
                    }
                    res = requests.get(mm_url, params=mm_params, timeout=1.5)
                    if res.status_code == 429 or (res.status_code == 200 and "MYMEMORY WARNING" in res.text):
                        globals()['mymemory_blocked'] = True
                        return idx_inner, w_inner
                    elif res.status_code == 200:
                        val = res.json()["responseData"]["translatedText"]
                        if val and not val.startswith("MYMEMORY WARNING"):
                            return idx_inner, val
                        else:
                            return idx_inner, w_inner
                except Exception:
                    pass
                return idx_inner, w_inner

            mm_results_indexed = [None] * len(chunk)
            with ThreadPoolExecutor(max_workers=8) as mm_executor:
                mm_completed = list(mm_executor.map(translate_single_word, enumerate(chunk)))
            
            for idx_inner, val_inner in mm_completed:
                mm_results_indexed[idx_inner] = f"[{idx_inner}] {val_inner}"
                
            translated_payload = "\n".join(mm_results_indexed)
            
        # Parse translated payload
        chunk_results = [None] * len(chunk)
        if translated_payload:
            lines = translated_payload.split("\n")
            for line in lines:
                line = line.strip()
                match = re.match(r'^\[(\d+)\]\s*(.*)$', line)
                if match:
                    idx = int(match.group(1))
                    val = match.group(2).strip()
                    if idx < len(chunk_results):
                        chunk_results[idx] = val
        return start_idx, chunk_results

    # Run chunk translation in parallel
    with ThreadPoolExecutor(max_workers=6) as executor:
        completed = list(executor.map(translate_chunk, zip(chunk_starts, chunks)))
        
    for start_idx, chunk_results in completed:
        for idx, val in enumerate(chunk_results):
            global_idx = start_idx + idx
            if global_idx < len(results):
                results[global_idx] = val

    # Fill default
    final_results = [""] * len(words)
    for clean_i, orig_i in enumerate(word_indices):
        val = results[clean_i]
        final_results[orig_i] = val if val is not None else ""
        
    return final_results

def get_character_pinyins(text):
    """
    Generates character-by-character pinyin corresponding to the input text.
    Maintains context-aware polyphone mapping.
    """
    pinyin_output = pinyin(text, style=Style.TONE)
    char_pinyins = []
    idx = 0
    n = len(text)
    
    for p_list in pinyin_output:
        if idx >= n:
            break
        p_str = p_list[0]
        char = text[idx]
        
        if re.match(r'^[\u4e00-\u9fff]$', char):
            char_pinyins.append(p_str.strip())
            idx += 1
        else:
            token_len = len(p_str)
            for _ in range(token_len):
                char_pinyins.append("")
            idx += token_len
            
    while len(char_pinyins) < n:
        char_pinyins.append("")
        
    return char_pinyins

def process_translation(chinese_text):
    """
    Takes Chinese text, segments it into words using jieba, translates the words back
    to English for tooltips, aligns character pinyins, and returns a structured list.
    """
    if not chinese_text:
        return []
        
    words = list(jieba.cut(chinese_text))
    translations = translate_zh_to_en_batch(words)
    char_pinyins = get_character_pinyins(chinese_text)
    
    structured_words = []
    char_idx = 0
    
    for w, trans in zip(words, translations):
        w_len = len(w)
        w_chars = chinese_text[char_idx : char_idx + w_len]
        w_pinyins = char_pinyins[char_idx : char_idx + w_len]
        
        chars_list = []
        for c, py in zip(w_chars, w_pinyins):
            chars_list.append({
                "char": c,
                "pinyin": py,
                "is_chinese": bool(re.match(r'^[\u4e00-\u9fff]$', c))
            })
            
        structured_words.append({
            "word": w,
            "translation": trans if trans else None,
            "characters": chars_list,
            "is_chinese_word": bool(re.search(r'[\u4e00-\u9fff]', w))
        })
        
        char_idx += w_len
        
    return structured_words

def process_translation_with_dict(chinese_text, translation_dict):
    """
    Takes Chinese text, segments it into words, looks up their translations
    in the pre-computed translation_dict, aligns character pinyins, and returns a structured list.
    """
    if not chinese_text:
        return []
        
    words = list(jieba.cut(chinese_text))
    char_pinyins = get_character_pinyins(chinese_text)
    
    structured_words = []
    char_idx = 0
    
    for w in words:
        trans = translation_dict.get(w, "")
        w_len = len(w)
        w_chars = chinese_text[char_idx : char_idx + w_len]
        w_pinyins = char_pinyins[char_idx : char_idx + w_len]
        
        chars_list = []
        for c, py in zip(w_chars, w_pinyins):
            chars_list.append({
                "char": c,
                "pinyin": py,
                "is_chinese": bool(re.match(r'^[\u4e00-\u9fff]$', c))
            })
            
        structured_words.append({
            "word": w,
            "translation": trans if trans else None,
            "characters": chars_list,
            "is_chinese_word": bool(re.search(r'[\u4e00-\u9fff]', w))
        })
        
        char_idx += w_len
        
    return structured_words

# --- Website Translation Proxy Helpers ---

def translate_list(texts):
    """
    Translates a list of texts in parallel in batches of 15 using client=at,
    falling back to client=gtx, Lingva Translate (pipe-batch), and MyMemory if needed.
    """
    global google_at_blocked, google_gtx_blocked, lingva_blocked, mymemory_blocked
    check_reset_circuit_breakers()
    
    results = [None] * len(texts)
    batch_size = 15
    
    chunks = []
    chunk_starts = []
    for start_idx in range(0, len(texts), batch_size):
        chunks.append(texts[start_idx : start_idx + batch_size])
        chunk_starts.append(start_idx)
        
    def translate_single_chunk(args):
        start_idx, chunk = args
        prefixed_items = [f"[{i}] {text.strip().replace('\n', ' ')}" for i, text in enumerate(chunk)]
        payload = "\n".join(prefixed_items)
        
        translated_payload = ""
        # 1. Try Google Translate client=at
        if not google_at_blocked:
            url = "https://translate.google.com/translate_a/single"
            params = {
                "client": "at",
                "sl": "en",
                "tl": "zh-CN",
                "dt": "t"
            }
            data = {
                "q": payload
            }
            try:
                response = requests.post(url, params=params, data=data, headers=HEADERS, timeout=3)
                if response.status_code == 200:
                    translated_payload = parse_batch_to_string(response.json())
                elif response.status_code == 429:
                    globals()['google_at_blocked'] = True
            except Exception:
                pass
            
        # 2. Try Google Translate client=gtx
        if not translated_payload and not google_gtx_blocked:
            url = "https://translate.google.com/translate_a/single"
            params = {
                "client": "gtx",
                "sl": "en",
                "tl": "zh-CN",
                "dt": "t"
            }
            data = {
                "q": payload
            }
            try:
                response = requests.post(url, params=params, data=data, timeout=3)
                if response.status_code == 200:
                    translated_payload = parse_batch_to_string(response.json())
                elif response.status_code == 429:
                    globals()['google_gtx_blocked'] = True
            except Exception:
                pass

        # 3. Try Lingva Translate (single request for the chunk using pipe separator)
        if not translated_payload and not lingva_blocked:
            lingva_payload = " | ".join(prefixed_items)
            try:
                from urllib.parse import quote
                lingva_url = f"https://lingva.ml/api/v1/en/zh/{quote(lingva_payload)}"
                response = requests.get(lingva_url, timeout=5)
                if response.status_code == 200:
                    val = response.json().get("translation", "")
                    if val:
                        # Replace both standard and Chinese full-width pipes with newlines for standard parsing
                        translated_payload = val.replace("|", "\n").replace("｜", "\n")
                elif response.status_code == 429:
                    globals()['lingva_blocked'] = True
            except Exception:
                pass
                
        # 4. Fallback to MyMemory in parallel
        if not translated_payload and not mymemory_blocked:
            translated_payload = translate_chunk_mymemory(chunk)
            
        # Parse output and map back to indices
        chunk_results = [None] * len(chunk)
        if translated_payload:
            lines = translated_payload.split("\n")
            for line in lines:
                line = line.strip()
                match = re.match(r'^\[(\d+)\]\s*(.*)$', line)
                if match:
                    idx = int(match.group(1))
                    val = match.group(2).strip()
                    if idx < len(chunk_results):
                        chunk_results[idx] = val
                        
        return start_idx, chunk_results

    # Run all chunks in parallel
    with ThreadPoolExecutor(max_workers=6) as executor:
        completed = list(executor.map(translate_single_chunk, zip(chunk_starts, chunks)))
        
    for start_idx, chunk_results in completed:
        for idx, val in enumerate(chunk_results):
            global_idx = start_idx + idx
            if global_idx < len(results):
                results[global_idx] = val
                
    # Fallback to original text for any failures
    for i in range(len(results)):
        if results[i] is None:
            results[i] = texts[i]
            
    return results

def parse_batch_to_string(res_data):
    return "".join([segment[0] for segment in res_data[0] if segment[0]])

def translate_chunk_mymemory(chunk):
    """
    Translates a list of texts using MyMemory in parallel.
    """
    global mymemory_blocked
    if mymemory_blocked:
        return "\n".join([f"[{i}] {text}" for i, text in enumerate(chunk)])
        
    def translate_single(args):
        global mymemory_blocked
        i, text = args
        if mymemory_blocked:
            return f"[{i}] {text}"
            
        try:
            url = "https://api.mymemory.translated.net/get"
            params = {
                "q": text.strip().replace('\n', ' '),
                "langpair": "en|zh-CN",
                "de": "aethertranslate_test@example.com"
            }
            res = requests.get(url, params=params, timeout=2)
            if res.status_code == 429 or (res.status_code == 200 and "MYMEMORY WARNING" in res.text):
                mymemory_blocked = True
                return f"[{i}] {text}"
                
            if res.status_code == 200:
                val = res.json()["responseData"]["translatedText"]
                if val and not val.startswith("MYMEMORY WARNING"):
                    return f"[{i}] {val}"
        except Exception:
            pass
        return f"[{i}] {text}"
            
    with ThreadPoolExecutor(max_workers=5) as executor:
        results = list(executor.map(translate_single, enumerate(chunk)))
        
    return "\n".join(results)

def make_ruby_html(text):
    """
    Converts Chinese text into HTML containing <ruby> elements for characters.
    """
    char_pinyins = get_character_pinyins(text)
    html_parts = []
    
    for char, py in zip(text, char_pinyins):
        if re.match(r'^[\u4e00-\u9fff]$', char):
            html_parts.append(f"<ruby>{char}<rt>{py}</rt></ruby>")
        else:
            html_parts.append(char)
            
    return "".join(html_parts)

# --- Flask Routes ---

@app.route('/')
def home():
    """Serves the index page template."""
    return render_template('index.html')

@app.route('/api/translate', methods=['POST', 'OPTIONS'])
def translate_api():
    """API endpoint for translating and annotating text."""
    if request.method == 'OPTIONS':
        return '', 200
        
    data = request.get_json() or {}
    text = data.get("text", "")
    
    if not text.strip():
        return jsonify({"error": "Empty input text"}), 400
        
    try:
        translated = translate_en_to_zh(text)
        structured = process_translation(translated)
        return jsonify({
            "original_text": text,
            "translated_text": translated,
            "structured_translation": structured
        })
    except Exception as e:
        return jsonify({"error": f"Translation failed: {str(e)}"}), 500

@app.route('/proxy')
def proxy():
    """
    Fetches external website, parses it, translates structural block elements in-place,
    resolves relative resources, and injects styling/script.
    """
    url = request.args.get('url', '')
    if not url:
        return "Missing URL parameter", 400
        
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "https://" + url
        
    try:
        response = requests.get(url, headers=HEADERS, timeout=12)
        response.raise_for_status()
        response.encoding = response.apparent_encoding
        html = response.text
    except Exception as e:
        return f"Failed to fetch website: {str(e)}", 500
        
    soup = BeautifulSoup(html, 'html.parser')
    
    # 1. Resolve relative resources to absolute paths
    for tag in soup.find_all('link', href=True):
        tag['href'] = urljoin(url, tag['href'])
    for tag in soup.find_all('script', src=True):
        tag['src'] = urljoin(url, tag['src'])
    for tag in soup.find_all('img', src=True):
        tag['src'] = urljoin(url, tag['src'])
        if tag.get('data-src'):
            tag['data-src'] = urljoin(url, tag['data-src'])
    for tag in soup.find_all('source', src=True):
        tag['src'] = urljoin(url, tag['src'])
        
    # Proxy all anchor links so users navigate through our translator proxy
    for tag in soup.find_all('a', href=True):
        href = tag['href']
        if href.startswith('#') or href.startswith('mailto:') or href.startswith('javascript:'):
            continue
        tag['href'] = f"/proxy?url={urljoin(url, href)}"
        
    # 2. Extract visible content structural block elements (no navigation lists/menus)
    elements = []
    target_tags = ['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'td', 'th', 'span']
    
    body = soup.body
    if body:
        # Decompose scripts, styles, layouts to avoid translating hidden items
        for tag in body(["script", "style", "noscript", "iframe", "svg"]):
            tag.decompose()
            
        for tag in body.find_all(target_tags):
            text = tag.text.strip()
            # Only translate blocks with words and >= 20 characters (targets readable content)
            if text and re.search(r'[a-zA-Z]', text) and len(text) >= 20:
                # Filter out nested elements to translate parent block in a single coherent context
                is_nested = False
                for parent in tag.parents:
                    if parent in [el for el, _ in elements]:
                        is_nested = True
                        break
                if not is_nested:
                    elements.append((tag, text))
                    
    # Cap maximum elements per page to 120 to guarantee low latency (under 3 seconds)
    elements = elements[:120]
    
    # 3. Translate sequentially in batches of 15
    if elements:
        texts_to_translate = [t for tag, t in elements]
        translated_texts = translate_list(texts_to_translate)
        
        for (tag, original_text), translated_text in zip(elements, translated_texts):
            if translated_text and translated_text != original_text:
                ruby_html = make_ruby_html(translated_text)
                frag = BeautifulSoup(ruby_html, 'html.parser')
                tag.clear()
                tag.append(frag)
                
    # 4. Inject custom styling to ensure ruby rendering is formatted properly on all pages
    custom_style = soup.new_tag('style')
    custom_style.string = """
    ruby {
        ruby-position: over !important;
        ruby-align: center !important;
        display: inline-flex !important;
        flex-direction: column-reverse !important;
        vertical-align: bottom !important;
        align-items: center !important;
        border-radius: 3px !important;
        padding: 0 1px !important;
        margin: 0 1px !important;
    }
    rt {
        font-family: 'Inter', system-ui, sans-serif !important;
        font-size: 0.65em !important;
        font-weight: 500 !important;
        line-height: 1 !important;
        color: #4f46e5 !important;
        margin-bottom: 0.1em !important;
        user-select: none !important;
        display: block !important;
        text-align: center !important;
    }
    @media (prefers-color-scheme: dark) {
        rt {
            color: #a5b4fc !important;
        }
    }
    """
    if soup.head:
        soup.head.append(custom_style)
    else:
        head = soup.new_tag('head')
        head.append(custom_style)
        soup.insert(0, head)
        
    return str(soup)

@app.route('/api/translate_batch', methods=['POST', 'OPTIONS'])
def translate_batch_api():
    """API endpoint for translating a batch of texts concurrently on the server."""
    if request.method == 'OPTIONS':
        return '', 200
        
    data = request.get_json() or {}
    texts = data.get("texts", [])
    
    if not texts:
        return jsonify({"error": "Empty texts list"}), 400
        
    try:
        translated_texts = translate_list(texts)
        
        # Aggregate unique Chinese words
        unique_words = set()
        for trans_text, orig_text in zip(translated_texts, texts):
            if trans_text.strip() and trans_text != orig_text:
                for w in jieba.cut(trans_text):
                    if w.strip() and re.search(r'[\u4e00-\u9fff]', w):
                        unique_words.add(w)
                        
        # Translate the global set of unique words in batch
        unique_words_list = list(unique_words)
        translated_words = translate_zh_to_en_batch(unique_words_list)
        translation_dict = dict(zip(unique_words_list, translated_words))
        
        def process_item(item):
            translated_text, original_text = item
            if not translated_text.strip() or translated_text == original_text:
                return []
            return process_translation_with_dict(translated_text, translation_dict)
            
        with ThreadPoolExecutor(max_workers=8) as executor:
            structured_results = list(executor.map(process_item, zip(translated_texts, texts)))
            
        return jsonify({
            "results": structured_results
        })
    except Exception as e:
        return jsonify({"error": f"Batch translation failed: {str(e)}"}), 500

# Enable CORS for Chrome Extension requests
@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Allow-Methods"] = "POST, GET, OPTIONS"
    return response

# Run server
if __name__ == '__main__':
    app.run(debug=True, port=5000)
