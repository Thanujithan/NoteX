import os
from datetime import datetime

import streamlit as st
from dotenv import load_dotenv
from google import genai
from pypdf import PdfReader
from pymongo import MongoClient, DESCENDING
from pymongo.server_api import ServerApi
from bson import ObjectId


# =========================================================
# PAGE CONFIG
# =========================================================

st.set_page_config(
    page_title="NoteX",
    page_icon="📝",
    layout="wide",
    initial_sidebar_state="collapsed",
)


# =========================================================
# CONFIG / SECRETS
# Works locally with .env and on Streamlit Cloud with Secrets
# =========================================================

load_dotenv()


def get_secret(name):
    """
    Priority:
    1. Streamlit Community Cloud Secrets
    2. Local environment / .env
    """
    value = None

    try:
        if name in st.secrets:
            value = st.secrets[name]
    except Exception:
        pass

    if not value:
        value = os.getenv(name)

    if isinstance(value, str):
        value = value.strip()

    return value


api_key = get_secret("GEMINI_API_KEY")
mongodb_uri = get_secret("MONGODB_URI")


# =========================================================
# GEMINI SETUP
# =========================================================

if not api_key:
    st.error(
        "Gemini API key is not configured. "
        "On Streamlit Cloud go to App settings → Secrets and add "
        'GEMINI_API_KEY = "your_key".'
    )
    st.stop()

try:
    client = genai.Client(api_key=api_key)
except Exception as error:
    st.error(f"Gemini client setup failed: {error}")
    st.stop()


# =========================================================
# MONGODB ATLAS SETUP
# =========================================================

if not mongodb_uri:
    st.error(
        "MongoDB URI is not configured. "
        "On Streamlit Cloud go to App settings → Secrets and add "
        'MONGODB_URI = "your_connection_string".'
    )
    st.stop()

try:
    mongo_client = MongoClient(
        mongodb_uri,
        server_api=ServerApi("1"),
        serverSelectionTimeoutMS=5000,
    )

    mongo_client.admin.command("ping")

    db = mongo_client["notex"]
    notes_collection = db["notes"]

    notes_collection.create_index(
        [("created_at", DESCENDING)]
    )

except Exception as error:
    st.error(
        "MongoDB connection failed. "
        "Check your Atlas connection string, database user, password, "
        f"and Network Access settings.\n\nDetails: {error}"
    )
    st.stop()


# =========================================================
# SESSION STATE
# =========================================================

if "history" not in st.session_state:
    st.session_state.history = []

if "input_text" not in st.session_state:
    st.session_state.input_text = ""

if "output_text" not in st.session_state:
    st.session_state.output_text = ""

if "mode" not in st.session_state:
    st.session_state.mode = "Smart Notes"

if "loaded_pdf_name" not in st.session_state:
    st.session_state.loaded_pdf_name = ""

if "current_note_id" not in st.session_state:
    st.session_state.current_note_id = None

if "view" not in st.session_state:
    st.session_state.view = "workspace"

if "output_length" not in st.session_state:
    st.session_state.output_length = "Medium"

if "mcq_count" not in st.session_state:
    st.session_state.mcq_count = 10

if "language" not in st.session_state:
    st.session_state.language = "English"

if "fast_mode" not in st.session_state:
    st.session_state.fast_mode = True

# =========================================================
# HELPERS
# =========================================================

def extract_pdf_text(uploaded_file):
    reader = PdfReader(uploaded_file)

    text_parts = []

    for page in reader.pages:
        page_text = page.extract_text()

        if page_text:
            text_parts.append(page_text)

    return "\n\n".join(text_parts), len(reader.pages)


def create_prompt(selected_mode, content):
    fast_mode = st.session_state.fast_mode
    length = st.session_state.output_length
    language = st.session_state.language
    mcq_count = st.session_state.mcq_count

    # Fast Mode reduces very large requests and asks Gemini to be concise.
    if fast_mode:
        content = content[:18000]
        length = "Short"
        if selected_mode == "MCQ":
            mcq_count = min(mcq_count, 5)

    length_instruction = {
        "Short": "Keep the response short and focused.",
        "Medium": "Give a balanced amount of detail.",
        "Detailed": "Give a detailed explanation with useful study detail.",
    }.get(length, "Give a balanced amount of detail.")

    language_instruction = (
        "Write the complete answer in Tamil."
        if language == "Tamil"
        else "Write the complete answer in English."
    )

    speed_instruction = (
        "Respond directly and concisely. Avoid unnecessary introduction or repetition."
        if fast_mode
        else "Prioritize completeness and study usefulness."
    )

    if selected_mode == "Smart Notes":
        return f"""
You are NoteX, an intelligent study assistant.

Convert the following study content into clear,
concise and examination-friendly notes.

Use this exact structure:

# Short Summary
Write a simple summary of the content.

# Important Points
Give the most important points using bullet points.

# Key Terms
List important terms with easy definitions.

# Quick Revision
Give a very short revision section for
last-minute exam preparation.

Additional instructions:
- {length_instruction}
- {language_instruction}
- {speed_instruction}
- Use simple student-friendly language.

Study Content:
{content}
"""

    if selected_mode == "Summary":
        return f"""
You are NoteX.

Summarize the following study material.

Requirements:
- Use simple language.
- Keep only important information.
- Avoid unnecessary details.
- Make it suitable for examination revision.
- {length_instruction}
- {language_instruction}
- {speed_instruction}

Study Content:
{content}
"""

    if selected_mode == "MCQ":
        return f"""
You are NoteX.

Create exactly {mcq_count} multiple-choice questions
using only the following study content.

For each question use this format:

### Question 1

Question text

A. Option
B. Option
C. Option
D. Option

**Correct Answer:** A

**Explanation:** Short explanation.

Requirements:
- Make the questions suitable for students.
- {length_instruction}
- {language_instruction}
- {speed_instruction}

Study Content:
{content}
"""

    return f"""
You are NoteX.

Create useful examination questions and answers
from the following study content.

Generate:

# Short Questions
Create 5 short-answer questions with clear answers.

# Descriptive Questions
Create 5 important descriptive questions
with simple but complete answers.

Requirements:
- {length_instruction}
- {language_instruction}
- {speed_instruction}

Study Content:
{content}
"""


# =========================================================
# MONGODB HELPERS
# =========================================================

def save_note_to_db(title, input_text, output_text, mode, source="text"):
    note = {
        "title": title,
        "input": input_text,
        "output": output_text,
        "mode": mode,
        "source": source,
        "saved": False,
        "created_at": datetime.utcnow(),
    }

    result = notes_collection.insert_one(note)
    return str(result.inserted_id)


def get_recent_notes(limit=5):
    return list(
        notes_collection.find()
        .sort("created_at", DESCENDING)
        .limit(limit)
    )


def mark_note_saved(note_id):
    if not note_id:
        return False

    result = notes_collection.update_one(
        {"_id": ObjectId(note_id)},
        {"$set": {"saved": True}}
    )

    return result.modified_count > 0 or result.matched_count > 0


def get_saved_notes(limit=20):
    return list(
        notes_collection.find({"saved": True})
        .sort("created_at", DESCENDING)
        .limit(limit)
    )


def unsave_note(note_id):
    result = notes_collection.update_one(
        {"_id": ObjectId(note_id)},
        {"$set": {"saved": False}}
    )
    return result.modified_count > 0 or result.matched_count > 0


def get_history_notes(search_text="", mode_filter="All"):
    query = {}

    if search_text.strip():
        query["$or"] = [
            {"title": {"$regex": search_text, "$options": "i"}},
            {"input": {"$regex": search_text, "$options": "i"}},
            {"output": {"$regex": search_text, "$options": "i"}},
        ]

    if mode_filter != "All":
        query["mode"] = mode_filter

    return list(
        notes_collection.find(query)
        .sort("created_at", DESCENDING)
    )


def delete_note(note_id):
    result = notes_collection.delete_one(
        {"_id": ObjectId(note_id)}
    )
    return result.deleted_count > 0


def go_to_workspace():
    st.session_state.view = "workspace"


def go_to_saved_notes():
    st.session_state.view = "saved"


def go_to_history():
    st.session_state.view = "history"


def go_to_settings():
    st.session_state.view = "settings"


# =========================================================
# CUSTOM CSS
# =========================================================

st.markdown(
    """
<style>

/* ===========================
   APP
=========================== */

html,
body,
[data-testid="stAppViewContainer"] {
    background:
        radial-gradient(circle at 85% 5%, rgba(55, 92, 255, 0.11), transparent 28%),
        radial-gradient(circle at 20% 14%, rgba(142, 76, 255, 0.11), transparent 30%),
        #07080d;
    color: #ffffff;
}

[data-testid="stMain"] {
    padding: 0 !important;
    min-height: 100vh !important;
}

.block-container {
    max-width: 1320px !important;
    margin-left: auto !important;
    margin-right: auto !important;
    padding-top: 1rem !important;
    padding-bottom: 1rem !important;
}

[data-testid="stVerticalBlock"] {
    gap: 0.55rem !important;
}


/* ===========================
   HEADER / SIDEBAR TOGGLE
=========================== */

[data-testid="stHeader"] {
    background: transparent;
}

[data-testid="stSidebarCollapsedControl"] {
    display: flex !important;
    visibility: visible !important;
}

[data-testid="stSidebarCollapsedControl"] button {
    background: rgba(16, 18, 27, 0.92);
    border: 1px solid #2a2d38;
    border-radius: 12px;
    color: white;
}

[data-testid="stSidebarCollapsedControl"] button:hover {
    border-color: #6e5cff;
}


/* ===========================
   SIDEBAR
=========================== */

[data-testid="stSidebar"] {
    background:
        linear-gradient(180deg, #0c0d13 0%, #090a10 100%);
    border-right: 1px solid #222530;
}

[data-testid="stSidebar"] > div:first-child {
    padding-top: 0.8rem;
}

.sidebar-center-brand {
    text-align: center;
    margin-bottom: 24px;
}

.sidebar-brand-title {
    font-size: 28px;
    font-weight: 850;
    letter-spacing: -1.2px;
    color: white;
}

.sidebar-brand-x {
    background: linear-gradient(90deg, #a855f7, #6b63ff, #1ea7ff);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
}

.sidebar-brand-tagline {
    margin-top: 4px;
    margin-bottom: 10px;
    color: #74798b;
    font-size: 11px;
}

.sidebar-divider {
    height: 1px;
    background: #232631;
    margin: 14px 0;
}

.sidebar-section-title {
    font-size: 13px;
    font-weight: 700;
    color: #eef0f6;
    margin-bottom: 7px;
}

.sidebar-empty {
    color: #6f7485;
    font-size: 12px;
    padding: 3px 2px 6px 2px;
}

.sidebar-status {
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 7px;
    color: #6ee7a0;
    font-size: 11px;
    margin-top: 4px;
}

.status-dot {
    width: 7px;
    height: 7px;
    border-radius: 50%;
    background: #45dc82;
    box-shadow: 0 0 9px rgba(69, 220, 130, 0.7);
}

.sidebar-version {
    text-align: center;
    margin-top: 4px;
    color: #565c6d;
    font-size: 10px;
}

[data-testid="stSidebar"] .stButton > button {
    min-height: 40px;
    border-radius: 12px;
    background: rgba(16, 18, 27, 0.92);
    border: 1px solid #292d39;
    color: #e8eaf1;
    font-size: 13px;
    transition: 0.2s ease;
}

[data-testid="stSidebar"] .stButton > button:hover {
    background: #151823;
    border-color: #6e59ff;
    transform: translateY(-1px);
}

[data-testid="stSidebar"] button[kind="primary"] {
    background: linear-gradient(90deg, #7750ff, #456eff) !important;
    border: none !important;
    color: white !important;
    font-weight: 700 !important;
    margin-top: 20px !important;
}


/* ===========================
   FILE UPLOADER
=========================== */

[data-testid="stFileUploader"] {
    background: rgba(16, 18, 27, 0.75);
    border: 1px solid #292d39;
    border-radius: 13px;
    padding: 10px;
}

[data-testid="stFileUploader"] section {
    background: transparent !important;
    border: 1px dashed #393e4d !important;
    border-radius: 11px !important;
}

[data-testid="stFileUploader"] button {
    border-radius: 9px !important;
}


/* ===========================
   MAIN HEADER
=========================== */

.workspace-title {
    font-size: 32px;
    font-weight: 800;
    letter-spacing: -1px;
    margin-top: 0 !important;
    margin-bottom: 2px !important;
}

.workspace-sub {
    font-size: 13px;
    color: #858a9c;
    margin-bottom: 8px !important;
}

.status-badge {
    display: inline-flex;
    align-items: center;
    padding: 6px 11px;
    color: #72e7a5;
    border: 1px solid rgba(93, 217, 146, 0.18);
    background: rgba(52, 190, 113, 0.07);
    border-radius: 30px;
    font-size: 11px;
}

.small-label {
    font-size: 10px;
    color: #6e7385;
    letter-spacing: 1.2px;
    font-weight: 650;
    margin-top: 0 !important;
    margin-bottom: 2px !important;
}

.card-title {
    font-size: 18px;
    font-weight: 750;
    margin-top: 0 !important;
    margin-bottom: 2px !important;
}

.card-subtitle {
    color: #7b8092;
    font-size: 12px;
    margin-bottom: 6px !important;
}


/* ===========================
   TEXTAREA
=========================== */

.stTextArea textarea {
    min-height: 180px !important;
    height: 180px !important;
    background: rgba(15, 17, 25, 0.95) !important;
    border: 1px solid #292c38 !important;
    border-radius: 16px !important;
    padding: 16px !important;
    color: white !important;
    font-size: 14px !important;
    line-height: 1.55 !important;
    transition: 0.25s ease;
}

.stTextArea textarea:focus {
    border: 1px solid #745cff !important;
    box-shadow: 0 0 0 3px rgba(116, 92, 255, 0.08) !important;
}

[data-testid="stCaptionContainer"] {
    margin-top: -4px !important;
    font-size: 11px !important;
}


/* ===========================
   BUTTONS
=========================== */

.stButton > button {
    border-radius: 11px;
    border: 1px solid #2b2e3a;
    background: #11131b;
    color: #e9eaf1;
    transition: 0.2s ease;
}

.stButton > button:hover {
    border-color: #7459ff;
    color: #ffffff;
    transform: translateY(-1px);
}

button[kind="primary"] {
    background: linear-gradient(90deg, #7a4fff, #5367ff, #2b8bff) !important;
    border: none !important;
    font-weight: 700 !important;
    min-height: 42px !important;
    box-shadow: 0 8px 25px rgba(91, 75, 255, 0.18);
}

button[kind="primary"]:hover {
    box-shadow: 0 10px 32px rgba(91, 75, 255, 0.30);
}


/* ===========================
   SEGMENTED CONTROL
=========================== */

[data-testid="stButtonGroup"] {
    background: rgba(14, 16, 23, 0.88);
    border: 1px solid #272a35;
    border-radius: 13px;
    padding: 3px;
    margin-bottom: 5px !important;
}


/* ===========================
   OUTPUT CARD
=========================== */

[data-testid="stVerticalBlockBorderWrapper"] {
    background: rgba(13, 15, 23, 0.82);
    border-color: #292c38 !important;
    border-radius: 18px !important;
}


/* ===========================
   DOWNLOAD BUTTON
=========================== */

[data-testid="stDownloadButton"] > button {
    min-height: 36px !important;
    padding: 0 13px !important;
    border-radius: 10px !important;
    border: 1px solid #313543 !important;
    background: linear-gradient(
        180deg,
        rgba(23, 26, 38, 0.96),
        rgba(15, 17, 25, 0.96)
    ) !important;
    color: #e9ebf5 !important;
    font-size: 12px !important;
    font-weight: 650 !important;
    box-shadow: none !important;
}

[data-testid="stDownloadButton"] > button:hover {
    border-color: #6d5aff !important;
    background: rgba(28, 30, 45, 0.98) !important;
    transform: translateY(-1px);
}


/* ===========================
   TOP LOGO
=========================== */

[data-testid="stImage"] img {
    max-height: 65px !important;
    object-fit: contain !important;
}


/* ===========================
   FOOTER
=========================== */

.notex-footer {
    margin-top: 28px;
    padding-bottom: 6px;
}

.footer-line {
    width: 100%;
    height: 1px;
    background: #20232e;
    margin-bottom: 10px;
}

.footer-text {
    text-align: center;
    color: #626778;
    font-size: 11px;
}


/* ===========================
   STREAMLIT DEFAULT UI
=========================== */

#MainMenu {
    visibility: hidden;
}

footer {
    visibility: hidden;
}


/* ===========================
   RESPONSIVE
=========================== */

@media (max-width: 900px) {

    .block-container {
        padding-left: 14px !important;
        padding-right: 14px !important;
        padding-top: 1rem !important;
    }

    .workspace-title {
        font-size: 27px;
    }

    .stTextArea textarea {
        min-height: 170px !important;
        height: 170px !important;
    }
}

</style>
""",
    unsafe_allow_html=True,
)


# =========================================================
# SIDEBAR
# =========================================================

with st.sidebar:

    if os.path.exists("logo.png"):
        logo_l, logo_c, logo_r = st.columns([1, 1.5, 1])

        with logo_c:
            st.image("logo.png", use_container_width=True)

    st.markdown(
        """
<div class="sidebar-center-brand">
    <div class="sidebar-brand-title">
        Note<span class="sidebar-brand-x">X</span>
    </div>
    <div class="sidebar-brand-tagline">
        Smart Notes. Better Learning.
    </div>
</div>
""",
        unsafe_allow_html=True,
    )

    if st.button(
        "＋  New Note",
        use_container_width=True,
        type="primary",
    ):
        st.session_state.input_text = ""
        st.session_state.output_text = ""
        st.session_state.mode = "Smart Notes"
        st.session_state.loaded_pdf_name = ""
        st.session_state.current_note_id = None
        st.session_state.view = "workspace"
        st.rerun()

    st.markdown(
        '<div class="sidebar-divider"></div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="sidebar-section-title">🕘 Recent</div>',
        unsafe_allow_html=True,
    )

    recent_items = get_recent_notes(limit=5)

    if not recent_items:
        st.markdown(
            '<div class="sidebar-empty">No recent notes yet.</div>',
            unsafe_allow_html=True,
        )
    else:
        for index, item in enumerate(recent_items):
            if st.button(
                f"📄  {item.get('title', 'Untitled note')}",
                key=f"recent_{item['_id']}",
                use_container_width=True,
            ):
                st.session_state.input_text = item.get("input", "")
                st.session_state.output_text = item.get("output", "")
                st.session_state.mode = item.get("mode", "Smart Notes")
                st.session_state.current_note_id = str(item["_id"])
                st.session_state.loaded_pdf_name = ""
                st.session_state.view = "workspace"
                st.rerun()

    st.markdown(
        '<div class="sidebar-divider"></div>',
        unsafe_allow_html=True,
    )

    st.markdown(
        '<div class="sidebar-section-title">Workspace</div>',
        unsafe_allow_html=True,
    )

    st.button(
        "⭐  Saved Notes",
        use_container_width=True,
        key="open_saved_notes",
        on_click=go_to_saved_notes,
    )

    uploaded_pdf = st.file_uploader(
        "📂 Upload PDF",
        type=["pdf"],
        help="Upload a text-based PDF and NoteX will extract the content.",
    )

    if uploaded_pdf is not None:
        if uploaded_pdf.name != st.session_state.loaded_pdf_name:
            try:
                pdf_text, page_count = extract_pdf_text(uploaded_pdf)

                if pdf_text.strip():
                    st.session_state.input_text = pdf_text
                    st.session_state.output_text = ""
                    st.session_state.loaded_pdf_name = uploaded_pdf.name
                    st.session_state.current_note_id = None

                    st.success(
                        f"PDF loaded • {page_count} page"
                        f"{'s' if page_count != 1 else ''}"
                    )

                    st.rerun()

                else:
                    st.warning(
                        "No readable text found in this PDF. "
                        "Scanned/image-only PDFs need OCR."
                    )

            except Exception as error:
                st.error(f"Could not read PDF: {error}")

    st.button(
        "📚  Full History",
        use_container_width=True,
        key="open_full_history",
        on_click=go_to_history,
    )

    st.button(
        "⚙️  Settings",
        use_container_width=True,
        key="open_settings",
        on_click=go_to_settings,
    )

    st.write("")

    st.markdown(
        """
<div class="sidebar-status">
    <span class="status-dot"></span>
    Gemini Connected
</div>

<div class="sidebar-version">
    NoteX v0.15
</div>
""",
        unsafe_allow_html=True,
    )


if st.session_state.view == "workspace":
    # =========================================================
    # TOP BRAND / HEADER
    # =========================================================

    brand_left, brand_center, brand_right = st.columns([5, 0.8, 5])

    with brand_center:
        if os.path.exists("logo.png"):
            st.image("logo.png", use_container_width=True)

    header_left, header_right = st.columns([5, 1])

    with header_left:
        st.markdown(
            """
    <div class="workspace-title">
        Study Workspace
    </div>

    <div class="workspace-sub">
        Turn long study materials into focused, clear and exam-ready learning content.
    </div>
    """,
            unsafe_allow_html=True,
        )

    with header_right:
        st.markdown(
            """
    <div style="text-align:right;margin-top:10px;">
        <span class="status-badge">
            ● AI Ready
        </span>
    </div>
    """,
            unsafe_allow_html=True,
        )


    # =========================================================
    # MODE SELECTOR
    # =========================================================

    st.markdown(
        '<div class="small-label">CHOOSE A MODE</div>',
        unsafe_allow_html=True,
    )

    st.segmented_control(
        "Mode",
        options=["Smart Notes", "Summary", "MCQ", "Q&A"],
        key="mode",
        label_visibility="collapsed",
    )

    st.write("")


    # =========================================================
    # CHATGPT-STYLE MAIN WORKSPACE
    # =========================================================

    space_left, main_area, space_right = st.columns([1.35, 7.3, 1.35])

    with main_area:

        st.markdown(
            """
    <div class="card-title">
        ✍️ What would you like to study?
    </div>

    <div class="card-subtitle">
        Paste your lecture notes, article or study material below.
    </div>
    """,
            unsafe_allow_html=True,
        )

        if st.session_state.loaded_pdf_name:
            st.caption(
                f"📄 Loaded PDF: {st.session_state.loaded_pdf_name}"
            )

        text = st.text_area(
            "Study Content",
            key="input_text",
            placeholder=(
                "Paste your study content here...\n\n"
                "Or upload a PDF from the sidebar."
            ),
            height=180,
            label_visibility="collapsed",
        )

        count_left, count_right = st.columns(2)

        with count_left:
            st.caption(f"📝 {len(text.split())} words")

        with count_right:
            st.caption(f"🔤 {len(text)} characters")

        generate = st.button(
            "✦ Generate with NoteX",
            use_container_width=True,
            type="primary",
        )


    # =========================================================
    # GENERATE CONTENT
    # =========================================================

    if generate:

        if not text.strip():
            st.toast(
                "Please enter some study content or upload a PDF.",
                icon="⚠️",
            )

        else:
            prompt = create_prompt(
                st.session_state.mode,
                text,
            )

            try:
                with st.spinner(
                    "NoteX is analysing your content..."
                ):
                    response = client.models.generate_content(
                        model="gemini-3.6-flash",
                        contents=prompt,
                    )

                result = response.text
                st.session_state.output_text = result

                words = text.split()
                title = " ".join(words[:5])

                if len(words) > 5:
                    title += "..."

                source_type = (
                    "pdf"
                    if st.session_state.loaded_pdf_name
                    else "text"
                )

                note_id = save_note_to_db(
                    title=title,
                    input_text=text,
                    output_text=result,
                    mode=st.session_state.mode,
                    source=source_type,
                )

                st.session_state.current_note_id = note_id

                st.toast(
                    "Generated and saved to history!",
                    icon="✨",
                )

            except Exception as error:
                st.error(
                    f"NoteX couldn't generate the result: {error}"
                )


    # =========================================================
    # OUTPUT BELOW INPUT
    # Nothing is shown before generation.
    # =========================================================

    if st.session_state.output_text:

        space_left, output_area, space_right = st.columns(
            [1.35, 7.3, 1.35]
        )

        with output_area:

            st.write("")
            st.write("")

            title_col, save_col, download_col = st.columns([4.2, 0.9, 1.2])

            with title_col:
                st.markdown(
                    f"""
    <div class="card-title">
        ✨ {st.session_state.mode}
    </div>

    <div class="card-subtitle">
        Generated by NoteX AI
    </div>
    """,
                    unsafe_allow_html=True,
                )

            with save_col:
                if st.button(
                    "⭐ Save",
                    use_container_width=True,
                    key="save_current_note",
                ):
                    if st.session_state.current_note_id:
                        try:
                            if mark_note_saved(st.session_state.current_note_id):
                                st.toast(
                                    "Saved to Saved Notes!",
                                    icon="⭐",
                                )
                            else:
                                st.warning("Could not save this note.")
                        except Exception as error:
                            st.error(f"Save failed: {error}")
                    else:
                        st.warning(
                            "Generate a new note first."
                        )

            with download_col:
                st.download_button(
                    "↓ Download",
                    data=st.session_state.output_text,
                    file_name=f"NoteX_{st.session_state.mode.replace('&', 'and')}.txt",
                    mime="text/plain",
                    use_container_width=True,
                )

            with st.container(border=True):
                st.markdown(st.session_state.output_text)



elif st.session_state.view == "saved":
    # =========================================================
    # SAVED NOTES PAGE
    # =========================================================

    saved_header_left, saved_header_right = st.columns([5, 1])

    with saved_header_left:
        st.markdown(
            """
<div class="workspace-title">
    ⭐ Saved Notes
</div>

<div class="workspace-sub">
    Your favourite NoteX study materials are saved here.
</div>
""",
            unsafe_allow_html=True,
        )

    with saved_header_right:
        st.button(
            "← Workspace",
            use_container_width=True,
            key="back_to_workspace",
            on_click=go_to_workspace,
        )

    saved_notes = get_saved_notes(limit=50)

    if not saved_notes:
        st.info(
            "No saved notes yet. Generate a note and click ⭐ Save."
        )

    else:
        st.caption(f"{len(saved_notes)} saved note(s)")

        for note in saved_notes:
            note_title = note.get("title", "Untitled note")
            note_mode = note.get("mode", "Smart Notes")
            note_source = note.get("source", "text")
            created_at = note.get("created_at")

            if created_at:
                created_text = created_at.strftime("%d %b %Y • %H:%M")
            else:
                created_text = ""

            with st.container(border=True):

                col_title, col_open, col_remove = st.columns(
                    [4.6, 1, 1]
                )

                with col_title:
                    st.markdown(f"### {note_title}")
                    st.caption(
                        f"{note_mode} • {note_source.upper()} • {created_text}"
                    )

                with col_open:
                    if st.button(
                        "Open",
                        key=f"open_saved_{note['_id']}",
                        use_container_width=True,
                    ):
                        st.session_state.input_text = note.get("input", "")
                        st.session_state.output_text = note.get("output", "")
                        st.session_state.mode = note.get("mode", "Smart Notes")
                        st.session_state.current_note_id = str(note["_id"])
                        st.session_state.loaded_pdf_name = ""
                        st.session_state.view = "workspace"
                        st.rerun()

                with col_remove:
                    if st.button(
                        "Unsave",
                        key=f"unsave_{note['_id']}",
                        use_container_width=True,
                    ):
                        try:
                            unsave_note(str(note["_id"]))
                            st.toast("Removed from Saved Notes.")
                            st.rerun()
                        except Exception as error:
                            st.error(f"Could not unsave note: {error}")

                preview = note.get("output", "")

                if preview:
                    short_preview = preview[:500]

                    if len(preview) > 500:
                        short_preview += "..."

                    st.markdown(short_preview)



elif st.session_state.view == "history":
    # =========================================================
    # FULL HISTORY PAGE
    # =========================================================

    history_header_left, history_header_right = st.columns([5, 1])

    with history_header_left:
        st.markdown(
            """
<div class="workspace-title">
    📚 Full History
</div>

<div class="workspace-sub">
    Search, reopen or delete your previous NoteX generations.
</div>
""",
            unsafe_allow_html=True,
        )

    with history_header_right:
        st.button(
            "← Workspace",
            use_container_width=True,
            key="history_back_to_workspace",
            on_click=go_to_workspace,
        )

    search_col, filter_col = st.columns([3.2, 1.2])

    with search_col:
        history_search = st.text_input(
            "Search history",
            placeholder="Search by title or content...",
            label_visibility="collapsed",
        )

    with filter_col:
        history_mode = st.selectbox(
            "Mode",
            ["All", "Smart Notes", "Summary", "MCQ", "Q&A"],
            label_visibility="collapsed",
        )

    history_notes = get_history_notes(
        search_text=history_search,
        mode_filter=history_mode,
    )

    if not history_notes:
        st.info("No history found for this search.")

    else:
        st.caption(f"{len(history_notes)} note(s) found")

        for note in history_notes:
            note_id = str(note["_id"])
            note_title = note.get("title", "Untitled note")
            note_mode = note.get("mode", "Smart Notes")
            note_source = note.get("source", "text")
            note_saved = note.get("saved", False)
            created_at = note.get("created_at")

            if created_at:
                created_text = created_at.strftime("%d %b %Y • %H:%M")
            else:
                created_text = ""

            with st.container(border=True):

                title_col, open_col, delete_col = st.columns(
                    [4.7, 0.9, 0.9]
                )

                with title_col:
                    saved_badge = " ⭐ Saved" if note_saved else ""
                    st.markdown(f"### {note_title}{saved_badge}")
                    st.caption(
                        f"{note_mode} • {note_source.upper()} • {created_text}"
                    )

                with open_col:
                    if st.button(
                        "Open",
                        key=f"history_open_{note_id}",
                        use_container_width=True,
                    ):
                        st.session_state.input_text = note.get("input", "")
                        st.session_state.output_text = note.get("output", "")
                        st.session_state.mode = note.get("mode", "Smart Notes")
                        st.session_state.current_note_id = note_id
                        st.session_state.loaded_pdf_name = ""
                        st.session_state.view = "workspace"
                        st.rerun()

                with delete_col:
                    if st.button(
                        "🗑 Delete",
                        key=f"history_delete_{note_id}",
                        use_container_width=True,
                    ):
                        try:
                            if delete_note(note_id):
                                if st.session_state.current_note_id == note_id:
                                    st.session_state.current_note_id = None
                                    st.session_state.output_text = ""
                                st.toast("History item deleted.")
                                st.rerun()
                            else:
                                st.warning("Could not delete this note.")
                        except Exception as error:
                            st.error(f"Delete failed: {error}")

                preview = note.get("output", "")

                if preview:
                    short_preview = preview[:420]

                    if len(preview) > 420:
                        short_preview += "..."

                    st.markdown(short_preview)


elif st.session_state.view == "settings":
    # =========================================================
    # SETTINGS PAGE
    # =========================================================

    settings_header_left, settings_header_right = st.columns([5, 1])

    with settings_header_left:
        st.markdown(
            """
<div class="workspace-title">
    ⚙️ Settings
</div>

<div class="workspace-sub">
    Customize how NoteX generates your study content.
</div>
""",
            unsafe_allow_html=True,
        )

    with settings_header_right:
        st.button(
            "← Workspace",
            use_container_width=True,
            key="settings_back_to_workspace",
            on_click=go_to_workspace,
        )

    st.write("")

    settings_left, settings_right = st.columns([1, 1], gap="large")

    with settings_left:
        st.markdown("### ✨ AI Output")

        st.toggle(
            "⚡ Fast Mode",
            key="fast_mode",
            help=(
                "Uses shorter responses and limits very large input "
                "to improve generation speed."
            ),
        )

        st.selectbox(
            "Output Length",
            ["Short", "Medium", "Detailed"],
            key="output_length",
            help="Controls how short or detailed NoteX responses should be.",
        )

        st.selectbox(
            "Output Language",
            ["English", "Tamil"],
            key="language",
            help="Choose the language NoteX should use for generated answers.",
        )

    with settings_right:
        st.markdown("### ❓ MCQ Settings")

        st.selectbox(
            "Number of MCQs",
            [5, 10, 15, 20],
            key="mcq_count",
            help="Used whenever MCQ mode is selected.",
        )

        speed_label = "⚡ Fast" if st.session_state.fast_mode else "📘 Normal"

        st.info(
            f"Current setup: {speed_label} • "
            f"{st.session_state.output_length} output • "
            f"{st.session_state.language} • "
            f"{st.session_state.mcq_count} MCQs"
        )

    st.write("")
    st.success(
        "Settings are active immediately. "
        "Go back to Workspace and generate new content to test them."
    )

# =========================================================
# FOOTER
# =========================================================

st.markdown(
    """
<div class="notex-footer">
    <div class="footer-line"></div>
    <div class="footer-text">
        NoteX • Smart Notes. Better Learning.
    </div>
</div>
""",
    unsafe_allow_html=True,
)