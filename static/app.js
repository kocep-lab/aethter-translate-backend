document.addEventListener("DOMContentLoaded", () => {
    
    // --- State Variables ---
    let activeTextLayout = "ruby"; // ruby, inline, hover

    // --- DOM Elements ---
    const htmlElement = document.documentElement;
    const themeToggle = document.getElementById("theme-toggle");
    
    // Tab switching
    const tabButtons = document.querySelectorAll(".tab-btn");
    const tabPanels = document.querySelectorAll(".tab-panel");
    
    // Text Translator DOM Elements
    const textInput = document.getElementById("text-input");
    const clearInputBtn = document.getElementById("clear-input-btn");
    const charCounter = document.getElementById("char-counter");
    const translateBtn = document.getElementById("translate-btn");
    
    const outputPlaceholder = document.getElementById("translation-output-placeholder");
    const outputText = document.getElementById("translation-output-text");
    const outputActions = document.getElementById("output-actions");
    const ttsBtn = document.getElementById("tts-btn");
    const copyBtn = document.getElementById("copy-btn");
    const layoutSelectorText = document.getElementById("layout-selector-text");
    
    // Website Translator DOM Elements
    const webTranslateForm = document.getElementById("web-translate-form");
    const webUrlInput = document.getElementById("web-url");
    const webSubmitBtn = document.getElementById("web-submit-btn");
    const webError = document.getElementById("web-error");
    
    // Website Viewer DOM Elements
    const dashboardView = document.getElementById("dashboard-view");
    const webViewer_view = document.getElementById("web-viewer-view");
    const closeWebViewerBtn = document.getElementById("close-web-viewer-btn");
    const webViewerUrl = document.getElementById("web-viewer-url");
    const iframeLoadingIndicator = document.getElementById("iframe-loading-indicator");
    const webIframe = document.getElementById("web-iframe");
    
    // Hover Tooltip DOM Element
    const hoverTooltip = document.getElementById("hover-tooltip");

    // --- 1. Theme Configuration ---
    const savedTheme = localStorage.getItem("theme") || "dark";
    htmlElement.setAttribute("data-theme", savedTheme);

    themeToggle.addEventListener("click", () => {
        const currentTheme = htmlElement.getAttribute("data-theme");
        const newTheme = currentTheme === "dark" ? "light" : "dark";
        htmlElement.setAttribute("data-theme", newTheme);
        localStorage.setItem("theme", newTheme);
    });

    // --- 2. Tab Navigation ---
    tabButtons.forEach(btn => {
        btn.addEventListener("click", () => {
            tabButtons.forEach(b => b.classList.remove("active"));
            tabPanels.forEach(p => p.classList.remove("active"));
            
            btn.classList.add("active");
            const targetTab = btn.getAttribute("data-tab");
            document.getElementById(targetTab).classList.add("active");
        });
    });

    // --- 3. Text Input Handlers ---
    textInput.addEventListener("input", () => {
        const len = textInput.value.length;
        charCounter.textContent = `${len} / 5000`;
        if (len > 5000) {
            charCounter.style.color = "var(--color-error)";
            translateBtn.disabled = true;
        } else {
            charCounter.style.color = "var(--text-muted)";
            translateBtn.disabled = false;
        }
    });

    clearInputBtn.addEventListener("click", () => {
        textInput.value = "";
        charCounter.textContent = "0 / 5000";
        charCounter.style.color = "var(--text-muted)";
        textInput.focus();
        
        // Hide output
        outputPlaceholder.classList.remove("hidden");
        outputText.classList.add("hidden");
        outputActions.classList.add("hidden");
        outputText.innerHTML = "";
    });

    // --- 4. Text Translation Logic ---
    translateBtn.addEventListener("click", async () => {
        const text = textInput.value.trim();
        if (!text) return;

        setLoading(translateBtn, true);
        
        try {
            const response = await fetch("/api/translate", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ text })
            });
            
            const data = await response.json();
            
            if (response.ok) {
                renderTranslationOutput(data.structured_translation);
                
                outputPlaceholder.classList.add("hidden");
                outputText.classList.remove("hidden");
                outputActions.classList.remove("hidden");
            } else {
                alert(data.error || "Translation failed.");
            }
        } catch (error) {
            console.error(error);
            alert("An error occurred during translation.");
        } finally {
            setLoading(translateBtn, false);
        }
    });

    // --- 5. Pinyin Layout Selection (Text Tab) ---
    layoutSelectorText.addEventListener("click", (e) => {
        const btn = e.target.closest("button");
        if (!btn) return;
        
        layoutSelectorText.querySelectorAll("button").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        
        activeTextLayout = btn.getAttribute("data-layout");
        
        outputText.className = "output-text";
        outputText.classList.add(`layout-${activeTextLayout}`);
    });

    // --- 6. Pronunciation (Text-to-Speech) ---
    ttsBtn.addEventListener("click", () => {
        const textToSpeak = getRawChineseText(outputText);
        if (!textToSpeak) return;
        
        if ("speechSynthesis" in window) {
            window.speechSynthesis.cancel();
            
            const utterance = new SpeechSynthesisUtterance(textToSpeak);
            utterance.lang = "zh-CN";
            utterance.rate = 0.85; 
            
            const voices = window.speechSynthesis.getVoices();
            const zhVoice = voices.find(voice => voice.lang.includes("ZH") || voice.lang.includes("zh"));
            if (zhVoice) utterance.voice = zhVoice;
            
            window.speechSynthesis.speak(utterance);
        } else {
            alert("Text-to-Speech is not supported in this browser.");
        }
    });

    // Speak Chinese when clicking words in hover mode
    document.addEventListener("click", (e) => {
        const wordSpan = e.target.closest(".zh-word");
        if (!wordSpan) return;
        
        if (wordSpan.closest(".layout-hover") || e.target.closest("ruby")) {
            const rawWord = Array.from(wordSpan.querySelectorAll("ruby"))
                .map(r => r.firstChild ? (r.firstChild.textContent || r.firstChild.nodeValue || "").trim() : "")
                .join("");
            
            if (rawWord && "speechSynthesis" in window) {
                window.speechSynthesis.cancel();
                const utterance = new SpeechSynthesisUtterance(rawWord);
                utterance.lang = "zh-CN";
                utterance.rate = 0.8;
                window.speechSynthesis.speak(utterance);
            }
        }
    });

    // --- 7. Copy Translation ---
    copyBtn.addEventListener("click", () => {
        let copyText = "";
        const words = outputText.querySelectorAll(".zh-word, .plain-text");
        words.forEach(el => {
            if (el.classList.contains("zh-word")) {
                const rubies = el.querySelectorAll("ruby");
                rubies.forEach(r => {
                    const char = r.firstChild ? (r.firstChild.nodeValue || r.firstChild.textContent || "").trim() : "";
                    const rt = r.querySelector("rt").textContent;
                    if (rt) {
                        copyText += `${char}(${rt}) `;
                    } else {
                        copyText += char;
                    }
                });
            } else {
                copyText += el.textContent;
            }
        });

        navigator.clipboard.writeText(copyText.trim()).then(() => {
            const btnText = copyBtn.querySelector(".action-btn-text");
            btnText.textContent = "Copied!";
            copyBtn.style.color = "var(--color-success)";
            
            setTimeout(() => {
                btnText.textContent = "Copy";
                copyBtn.style.color = "";
            }, 2000);
        }).catch(err => {
            console.error("Could not copy text: ", err);
        });
    });

    // --- 8. Website Iframe Translation Loader ---
    webTranslateForm.addEventListener("submit", (e) => {
        e.preventDefault();
        let url = webUrlInput.value.trim();
        if (!url) return;
        
        // Simple protocol addition
        if (!url.startsWith("http://") && !url.startsWith("https://")) {
            url = "https://" + url;
        }
        
        webError.classList.add("hidden");
        iframeLoadingIndicator.style.opacity = "1";
        
        // Show domain url tag
        webViewerUrl.textContent = url;
        
        // Set Iframe Source to Proxy
        webIframe.src = `/proxy?url=${encodeURIComponent(url)}`;
        
        // Toggle view panels
        dashboardView.classList.remove("active");
        webViewer_view.classList.add("active");
    });

    // Iframe Load Event to Hide Loading Spinner
    webIframe.addEventListener("load", () => {
        iframeLoadingIndicator.style.opacity = "0";
    });

    // Close Web Viewer Button
    closeWebViewerBtn.addEventListener("click", () => {
        webViewer_view.classList.remove("active");
        dashboardView.classList.add("active");
        webIframe.src = "about:blank"; // clear memory and stop requests
    });

    // --- 9. Interactive English Translation Tooltips (Text Translator only) ---
    document.addEventListener("mouseover", (e) => {
        const word = e.target.closest(".zh-word");
        if (!word) return;
        
        const translation = word.getAttribute("data-translation");
        if (!translation) return;
        
        hoverTooltip.textContent = translation;
        hoverTooltip.classList.remove("hidden");
        
        const rect = word.getBoundingClientRect();
        const scrollLeft = window.pageXOffset || document.documentElement.scrollLeft;
        const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
        
        hoverTooltip.style.left = `${rect.left + scrollLeft + rect.width / 2}px`;
        hoverTooltip.style.top = `${rect.top + scrollTop}px`;
    });

    document.addEventListener("mouseout", (e) => {
        const word = e.target.closest(".zh-word");
        if (word) {
            hoverTooltip.classList.add("hidden");
        }
    });

    // --- Helper Utility Functions ---

    function setLoading(btn, isLoading) {
        btn.disabled = isLoading;
        const spinner = btn.querySelector(".loading-spinner");
        if (isLoading) {
            spinner.classList.remove("hidden");
            btn.style.opacity = "0.8";
        } else {
            spinner.classList.add("hidden");
            btn.style.opacity = "";
        }
    }

    // Convert structured word lists to raw Hanzi string (for TTS)
    function getRawChineseText(container) {
        const rubies = container.querySelectorAll("ruby");
        if (rubies.length === 0) return container.textContent;
        
        let chineseStr = "";
        rubies.forEach(r => {
            chineseStr += r.firstChild ? (r.firstChild.nodeValue || r.firstChild.textContent || "").trim() : "";
        });
        return chineseStr;
    }

    // Build interactive annotated HTML for standard text translator
    function renderTranslationOutput(segments) {
        outputText.innerHTML = "";
        const fragment = document.createDocumentFragment();
        
        segments.forEach(seg => {
            if (seg.is_chinese_word) {
                const wordSpan = document.createElement("span");
                wordSpan.className = "zh-word";
                if (seg.translation) {
                    wordSpan.setAttribute("data-translation", seg.translation);
                }
                
                seg.characters.forEach(c => {
                    if (c.is_chinese) {
                        const ruby = document.createElement("ruby");
                        ruby.textContent = c.char;
                        
                        const rt = document.createElement("rt");
                        rt.textContent = c.pinyin;
                        
                        ruby.appendChild(rt);
                        wordSpan.appendChild(ruby);
                    } else {
                        const plain = document.createElement("span");
                        plain.className = "plain-text";
                        plain.textContent = c.char;
                        wordSpan.appendChild(plain);
                    }
                });
                
                fragment.appendChild(wordSpan);
            } else {
                const plainSpan = document.createElement("span");
                plainSpan.className = "plain-text";
                plainSpan.textContent = seg.word;
                fragment.appendChild(plainSpan);
            }
        });
        
        outputText.appendChild(fragment);
    }
});
