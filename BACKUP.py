import logging
import threading
from pathlib import Path
import os
import subprocess
import humanize
import google.generativeai as genai
from dotenv import load_dotenv
import customtkinter as ctk
from tkinter import filedialog, messagebox, Menu
import tkinter as tk  # For Toplevel in tour popups
import heapq
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import time
import json
import requests  # For HTTP API calls
import re  # For regex matching of <think> tags

# Maximum results limit for unbounded mode
MAX_RESULTS_LIMIT = 100_000

# Known Windows system directories to skip (case-insensitive)
SYSTEM_DIRS = {
    "windows",
    "program files",
    "program files (x86)",
    "appdata",
    "system volume information",
    "$recycle.bin"
}

# Ultra-specific file type categories.
EXTENSION_CATEGORIES = {
    "Images": {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".svg"},
    "Videos": {".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".mpeg", ".mpg", ".3gp", ".webm"},
    "Audio": {".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a", ".wma"},
    "Documents": {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".odt", ".ods", ".odp"},
    "Text": {".txt", ".rtf", ".md", ".log", ".csv"},
    "Code": {".py", ".js", ".html", ".htm", ".css", ".java", ".c", ".cpp", ".cs", ".rb", ".php", ".sql", ".sh", ".go", ".rs", ".swift", ".kt", ".ts"},
    "Executables": {".exe", ".msi", ".bat", ".cmd"},
    "Libraries": {".dll", ".so", ".dylib"},
    "Archives": {".zip", ".rar", ".7z", ".tar", ".gz", ".iso", ".cab", ".arj", ".lzma", ".xz"},
    "Others": set()
}

# -------------------- TOOLTIP CLASS (for hover hints) --------------------
class ToolTip:
    """A polished tooltip for customtkinter/tkinter widgets."""
    def __init__(self, widget, text, app, delay=500):
        self.widget = widget
        self.text = text
        self.app = app  # Reference to the main DiskAnalyzerGUI instance.
        self.delay = delay
        self.tooltip_window = None
        self.id = None
        widget.bind("<Enter>", self.on_enter)
        widget.bind("<Leave>", self.on_leave)
        widget.bind("<ButtonPress>", self.on_leave)

    def on_enter(self, event=None):
        self.schedule()

    def schedule(self):
        self.unschedule()
        self.id = self.widget.after(self.delay, self.show_tooltip)

    def unschedule(self):
        if self.id:
            self.widget.after_cancel(self.id)
            self.id = None

    def show_tooltip(self, event=None):
        if self.tooltip_window or not self.text:
            return
        self.tooltip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_attributes("-topmost", True)
        label = tk.Label(tw, text=self.text, justify="left",
                         background="#333333", foreground="#ffffff",
                         relief="solid", borderwidth=1,
                         font=("Segoe UI", 10))
        label.pack(ipadx=5, ipady=3)
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 1
        tw.wm_geometry("+%d+%d" % (x, y))

    def on_leave(self, event=None):
        self.unschedule()
        self.hide_tooltip()

    def hide_tooltip(self):
        if self.tooltip_window:
            self.tooltip_window.destroy()
            self.tooltip_window = None

# -------------------- MAIN APPLICATION CLASS --------------------
class DiskAnalyzerGUI:
    def __init__(self):
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.window = ctk.CTk()
        self.window.title("Disk Space Analyzer")
        self.window.attributes("-fullscreen", True)
        # Hover tooltips remain enabled.
        self.tooltips_enabled = True
        # Scan configuration.
        self.two_pass_scan = True
        self.scanning = False
        self.skip_system_dirs = True
        self.min_file_size_mb = 10  # Default threshold in MB.
        self.top_k = 0  # 0 means store all (bounded).
        self.progress_update_interval = 50
        # Scanning statistics and data.
        self.total_size_scanned = 0
        self.items_scanned = 0
        self.total_items = 0
        self.current_progress = 0
        self.scan_start_time = None
        self.file_heap = []    # For top-K mode.
        self.file_list = []    # For full list.
        self.file_map = {}     # Mapping: path -> size.
        self.file_mtime = {}   # Mapping: path -> modification time.
        self.size_dict = {}    # For duplicate detection by size.
        self.category_map = {} # category -> [count, total_size].
        # Grouped files: category -> list of (size, path).
        self.grouped_files = {}
        # Variables for row selection.
        self.selected_row = None
        self.row_original_colors = {}
        # Analysis history.
        self.analysis_history = []
        # Chat history.
        self.chat_history = []
        # Layout preferences.
        self.layout_config_file = "layout_config.json"
        self.left_frame_visible = True
        self.right_frame_visible = True
        # Hold references to the Help and Tour windows.
        self.help_window = None
        self.tour_window = None
        # AI provider configuration.
        # Default provider is Gemini; user can switch to DeepSeekR1.
        self.default_ai_provider = "Gemini"
        self.model = None  # Will be initialized on demand for Gemini.
        self.ai_enabled = True
        # Define the Ollama server port.
        # For DeepSeekR1 7B (on a consumer GPU such as an RTX 3060Ti), use the default port 11434.
        self.ollama_port = 11434

        self.setup_logging()
        self.initialize_ai()  # Initializes Gemini if needed.
        self.setup_gui()
        self.load_layout_preferences()
        self.bind_shortcuts()

    # -------------------- HELPER: SAFE AFTER --------------------
    def safe_after(self, delay, callback, *args, **kwargs):
        try:
            if self.window.winfo_exists():
                return self.window.after(delay, callback, *args, **kwargs)
        except Exception as e:
            self.logger.error("safe_after error: " + str(e))
        return None

    # -------------------- LOGGING & AI SETUP --------------------
    def setup_logging(self):
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(asctime)s - %(levelname)s - %(message)s",
            handlers=[logging.FileHandler("disk_analyzer.log"), logging.StreamHandler()]
        )
        self.logger = logging.getLogger(__name__)

    def log_error(self, message: str):
        self.logger.error(message)
        if hasattr(self, "status_label") and self.window.winfo_exists():
            self.status_label.configure(text=f"Error: {message}")

    def initialize_ai(self):
        if self.default_ai_provider == "Gemini":
            try:
                load_dotenv()
                api_key = os.getenv("GEMINI_API_KEY")
                if not api_key:
                    raise ValueError("Gemini API key not found in environment")
                genai.configure(api_key=api_key)
                self.model = genai.GenerativeModel("gemini-1.5-pro")
                self.ai_enabled = True
                self.logger.info("Gemini AI initialization successful")
            except Exception as e:
                self.logger.error(f"Gemini AI initialization failed: {e}")
                self.ai_enabled = False
                self.model = None
        else:
            self.ai_enabled = True

    def setup_gui(self):
        # Increase columns to 14 to accommodate the new drop-down.
        self.window.rowconfigure(0, weight=0)
        self.window.rowconfigure(1, weight=1)
        self.window.columnconfigure(0, weight=1)

        self.top_frame = ctk.CTkFrame(self.window)
        self.top_frame.grid(row=0, column=0, sticky="ew", padx=20, pady=15)
        for col in range(14):
            self.top_frame.columnconfigure(col, weight=0)

        self.scan_btn = ctk.CTkButton(
            self.top_frame, text="Select Folder (Ctrl+O)", command=self.select_folder,
            width=140, fg_color="#1E90FF", hover_color="#1C86EE", text_color="#FFFFFF",
            font=("Segoe UI", 12)
        )
        self.scan_btn.grid(row=0, column=0, padx=(0, 10), pady=5, sticky="w")
        ToolTip(self.scan_btn, "Click to select a folder to scan. (Shortcut: Ctrl+O)", self)

        thresh_label = ctk.CTkLabel(
            self.top_frame, text="Min Size (MB):", font=("Segoe UI", 12),
            text_color="#FFFFFF"
        )
        thresh_label.grid(row=0, column=1, padx=(0, 5), pady=5, sticky="e")
        ToolTip(thresh_label, "Enter the minimum file size (in MB) to include in the scan.", self)

        self.min_size_var = ctk.StringVar(value=str(self.min_file_size_mb))
        self.min_size_entry = ctk.CTkEntry(
            self.top_frame, textvariable=self.min_size_var, width=70,
            font=("Segoe UI", 12)
        )
        self.min_size_entry.grid(row=0, column=2, padx=(0, 15), pady=5, sticky="w")
        ToolTip(self.min_size_entry, "Type the minimum file size (MB) for scanning.", self)

        self.skip_sys_var = ctk.BooleanVar(value=True)
        self.skip_sys_check = ctk.CTkCheckBox(
            self.top_frame, text="Skip System Dirs", variable=self.skip_sys_var,
            font=("Segoe UI", 12), text_color="#FFFFFF"
        )
        self.skip_sys_check.grid(row=0, column=3, padx=(0, 15), pady=5, sticky="w")
        ToolTip(self.skip_sys_check, "Toggle to skip Windows system directories.", self)

        self.status_label = ctk.CTkLabel(
            self.top_frame, text="Ready to scan", font=("Segoe UI", 12),
            text_color="#FFFFFF"
        )
        self.status_label.grid(row=0, column=4, padx=5, pady=5, sticky="w")

        self.progress_label = ctk.CTkLabel(
            self.top_frame, text="0%  (0/0)", font=("Segoe UI", 12), width=140,
            text_color="#FFFFFF"
        )
        self.progress_label.grid(row=0, column=5, padx=(10, 0), pady=5, sticky="e")

        self.progress_bar = ctk.CTkProgressBar(
            self.top_frame, mode="determinate", height=15, corner_radius=5, border_width=0, width=160
        )
        self.progress_bar.grid(row=0, column=6, padx=(10, 0), pady=5, sticky="e")
        self.progress_bar.set(0)

        self.export_btn = ctk.CTkButton(
            self.top_frame, text="Export Analysis", command=self.export_analysis, width=140,
            fg_color="#32CD32", hover_color="#2EB82E", text_color="#FFFFFF",
            font=("Segoe UI", 12)
        )
        self.export_btn.grid(row=0, column=7, padx=(10, 0), pady=5, sticky="e")
        ToolTip(self.export_btn, "Export the current analysis report to a file.", self)

        self.ai_provider = ctk.StringVar(value="Gemini")
        self.ai_provider_dropdown = ctk.CTkOptionMenu(
            self.top_frame, variable=self.ai_provider,
            values=["Gemini", "DeepSeekR1"],
            font=("Segoe UI", 12)
        )
        self.ai_provider_dropdown.grid(row=0, column=8, padx=(10, 0), pady=5, sticky="e")
        ToolTip(self.ai_provider_dropdown, "Select AI Provider (Gemini or DeepSeekR1)", self)

        self.chart_btn = ctk.CTkButton(
            self.top_frame, text="Show Chart", command=self.show_chart_window, width=140,
            fg_color="#6A5ACD", hover_color="#836FFF", text_color="#FFFFFF",
            font=("Segoe UI", 12)
        )
        self.chart_btn.grid(row=0, column=9, padx=(10, 0), pady=5, sticky="e")
        ToolTip(self.chart_btn, "Display visual charts summarizing disk usage.", self)

        self.collapse_left_btn = ctk.CTkButton(
            self.top_frame, text="Toggle File List", command=self.toggle_left_panel, width=140,
            fg_color="#FFA500", hover_color="#FF8C00", text_color="#FFFFFF",
            font=("Segoe UI", 12)
        )
        self.collapse_left_btn.grid(row=0, column=10, padx=(10, 0), pady=5, sticky="e")
        ToolTip(self.collapse_left_btn, "Show or hide the file list panel.", self)

        self.tour_btn = ctk.CTkButton(
            self.top_frame, text="Tour", command=self.start_guided_tour, width=80,
            fg_color="#20B2AA", hover_color="#1E8C90", text_color="#FFFFFF",
            font=("Segoe UI", 12)
        )
        self.tour_btn.grid(row=0, column=11, padx=(10, 0), pady=5, sticky="e")
        ToolTip(self.tour_btn, "Start a guided tour of the app.", self)

        self.help_btn = ctk.CTkButton(
            self.top_frame, text="Help", command=self.show_help, width=80,
            fg_color="#8A2BE2", hover_color="#7A1AB2", text_color="#FFFFFF",
            font=("Segoe UI", 12)
        )
        self.help_btn.grid(row=0, column=12, padx=(10, 0), pady=5, sticky="e")
        ToolTip(self.help_btn, "View detailed instructions on how to use the app.", self)

        self.exit_btn = ctk.CTkButton(
            self.top_frame, text="Exit (Ctrl+Q)", command=self.exit_app, width=100,
            fg_color="#FF4C4C", hover_color="#FF3B3B", text_color="#FFFFFF",
            font=("Segoe UI", 12)
        )
        self.exit_btn.grid(row=0, column=13, padx=(10, 0), pady=5, sticky="e")
        ToolTip(self.exit_btn, "Exit the application.", self)

        self.middle_frame = ctk.CTkFrame(self.window)
        self.middle_frame.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 20))
        self.middle_frame.rowconfigure(0, weight=1)
        self.middle_frame.columnconfigure(0, weight=1)
        self.middle_frame.columnconfigure(1, weight=1)

        self.left_frame = ctk.CTkFrame(self.middle_frame, fg_color="#2A2A2A")
        self.left_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self.left_frame.rowconfigure(1, weight=1)
        self.left_frame.columnconfigure(0, weight=1)

        controls_frame = ctk.CTkFrame(self.left_frame, fg_color="#2A2A2A")
        controls_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 5))
        controls_frame.columnconfigure(1, weight=1)

        filter_label = ctk.CTkLabel(controls_frame, text="Filter:", font=("Segoe UI", 12), text_color="#FFFFFF")
        filter_label.grid(row=0, column=0, padx=(0, 5))
        ToolTip(filter_label, "Enter a keyword to filter the file list.", self)
        self.filter_var = ctk.StringVar(value="")
        self.filter_entry = ctk.CTkEntry(controls_frame, textvariable=self.filter_var, width=150, font=("Segoe UI", 12))
        self.filter_entry.grid(row=0, column=1, padx=(0, 10))
        ToolTip(self.filter_entry, "Type text here to filter files.", self)
        self.filter_entry.bind("<KeyRelease>", lambda event: self.update_results())

        sort_label = ctk.CTkLabel(controls_frame, text="Sort by:", font=("Segoe UI", 12), text_color="#FFFFFF")
        sort_label.grid(row=0, column=2, padx=(10, 5))
        ToolTip(sort_label, "Select the criteria to sort the file list.", self)
        self.sort_options = ctk.StringVar(value="Size Desc")
        self.sort_menu = ctk.CTkOptionMenu(controls_frame, variable=self.sort_options,
                                           values=["Name Asc", "Name Desc", "Size Asc", "Size Desc"],
                                           font=("Segoe UI", 12))
        self.sort_menu.grid(row=0, column=3, padx=(0, 10))
        ToolTip(self.sort_menu, "Choose how to sort the files.", self)
        sort_btn = ctk.CTkButton(controls_frame, text="Apply", command=self.update_results, width=70,
                                 font=("Segoe UI", 12))
        sort_btn.grid(row=0, column=4)
        ToolTip(sort_btn, "Apply the filter and sort options.", self)

        self.file_scroll_frame = ctk.CTkScrollableFrame(self.left_frame, label_text="Files by Category", fg_color="#2A2A2A")
        self.file_scroll_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)

        self.right_frame = ctk.CTkFrame(self.middle_frame, fg_color="#2A2A2A", corner_radius=10)
        self.right_frame.grid(row=0, column=1, sticky="nsew")
        self.right_frame.rowconfigure(0, weight=1)
        self.right_frame.columnconfigure(0, weight=1)

        self.analysis_tabview = ctk.CTkTabview(self.right_frame, width=400)
        self.analysis_tabview.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)
        self.analysis_tabview.add("AI Analysis")
        self.analysis_tabview.add("History")
        self.analysis_tabview.add("Chatbot")  # New Chatbot tab.

        self.ai_scroll_frame = ctk.CTkScrollableFrame(self.analysis_tabview.tab("AI Analysis"), label_text="", fg_color="#2A2A2A")
        self.ai_scroll_frame.pack(fill="both", expand=True, padx=10, pady=10)

        self.history_textbox = ctk.CTkTextbox(self.analysis_tabview.tab("History"), wrap="word",
                                               font=("Segoe UI", 12), text_color="#FFFFFF", fg_color="#2A2A2A")
        self.history_textbox.pack(fill="both", expand=True, padx=10, pady=10)

        chat_tab = self.analysis_tabview.tab("Chatbot")
        self.chat_scroll_frame = ctk.CTkScrollableFrame(chat_tab, label_text="", fg_color="#2A2A2A")
        self.chat_scroll_frame.pack(fill="both", expand=True, padx=10, pady=(10, 5))

        chat_input_frame = ctk.CTkFrame(chat_tab, fg_color="#2A2A2A")
        chat_input_frame.pack(fill="x", padx=10, pady=(0, 10))
        self.chat_entry = ctk.CTkEntry(chat_input_frame, font=("Segoe UI", 12))
        self.chat_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
        ToolTip(self.chat_entry, "Enter your question here.", self)
        self.chat_entry.bind("<Return>", lambda event: self.send_chat_message())
        self.chat_send_btn = ctk.CTkButton(chat_input_frame, text="Send", command=self.send_chat_message,
                                           font=("Segoe UI", 12))
        self.chat_send_btn.pack(side="left")
        ToolTip(self.chat_send_btn, "Send your message to the disk management assistant.", self)

        self.analyze_btn = ctk.CTkButton(
            self.right_frame, text="Analyze with AI", command=self.trigger_ai_analysis, width=140,
            fg_color="#1E90FF", hover_color="#1C86EE", text_color="#FFFFFF",
            font=("Segoe UI", 12)
        )
        self.analyze_btn.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="ew")
        ToolTip(self.analyze_btn, "Run AI analysis on your scanned files to get insights and recommendations.", self)

    def bind_shortcuts(self):
        self.window.bind("<Control-o>", lambda event: self.select_folder())
        self.window.bind("<Control-O>", lambda event: self.select_folder())
        self.window.bind("<Control-q>", lambda event: self.exit_app())
        self.window.bind("<Control-Q>", lambda event: self.exit_app())

    def show_help(self):
        if self.help_window and self.help_window.winfo_exists():
            return
        self.help_window = ctk.CTkToplevel(self.window)
        self.help_window.title("Disk Space Analyzer - Help")
        self.help_window.geometry("600x400")
        self.help_window.grab_set()
        help_text = (
            "Disk Space Analyzer Help\n\n"
            "Select Folder (Ctrl+O):\n"
            "  - Click this button to choose a folder for scanning.\n"
            "  - The scan analyzes files larger than the specified minimum size.\n\n"
            "Min Size (MB):\n"
            "  - Enter the minimum file size (in MB) to include in the scan.\n"
            "  - Files smaller than this will be ignored.\n\n"
            "Skip System Dirs:\n"
            "  - Toggle to skip Windows system directories (helps avoid errors and speeds scanning).\n\n"
            "Export Analysis:\n"
            "  - Save the current disk analysis report to a text file.\n\n"
            "Show Chart:\n"
            "  - View charts that display file type breakdowns and disk usage.\n\n"
            "Toggle File List:\n"
            "  - Show or hide the list of files found during the scan.\n\n"
            "Tour:\n"
            "  - Launch a guided tour that explains the main controls of the app.\n\n"
            "AI Analysis & Chatbot:\n"
            "  - Use these tabs to get automated insights and ask questions about disk management.\n\n"
            "Exit (Ctrl+Q):\n"
            "  - Close the application."
        )
        textbox = ctk.CTkTextbox(self.help_window, wrap="word", font=("Segoe UI", 12),
                                 text_color="#FFFFFF", fg_color="#2A2A2A")
        textbox.insert("1.0", help_text)
        textbox.configure(state="disabled")
        textbox.pack(fill="both", expand=True, padx=10, pady=10)
        close_btn = ctk.CTkButton(self.help_window, text="Close", command=self.help_window.destroy,
                                  fg_color="#FF4C4C", hover_color="#FF3B3B", text_color="#FFFFFF",
                                  font=("Segoe UI", 12))
        close_btn.pack(pady=10)

    def get_chatbot_tab_button(self):
        try:
            segmented_btn = self.analysis_tabview._segmented_button
            for child in segmented_btn.winfo_children():
                if child.cget("text") == "Chatbot":
                    return child
        except Exception as e:
            self.logger.error(f"Could not retrieve Chatbot tab button: {e}")
        return None

    # -------------------- GUIDED TOUR --------------------
    def start_guided_tour(self):
        # Retrieve the Chatbot tab button so the tour can direct the user there.
        chatbot_btn = self.get_chatbot_tab_button()
        steps = [
            (self.scan_btn, "Click here to select a folder for scanning. (Shortcut: Ctrl+O)", "bottom"),
            (self.min_size_entry, "Enter the minimum file size (in MB). Files smaller than this are ignored.", "bottom"),
            (self.skip_sys_check, "Toggle to skip scanning system directories.", "bottom"),
            (self.export_btn, "Click to export the analysis report to a text file.", "bottom"),
            (self.chart_btn, "View charts that display file type breakdowns and disk usage.", "bottom"),
            (self.collapse_left_btn, "Toggle the file list panel visibility.", "bottom"),
            (self.analyze_btn, "Run AI analysis on your scanned files to get insights and recommendations.", "top"),
            (chatbot_btn, "Switch to the Chatbot tab to interact with the disk management assistant.", "bottom"),
            (self.help_btn, "Click here for detailed instructions on using the app.", "left"),
            (self.exit_btn, "Click here to exit the application.", "left")
        ]

        def calculate_popup_position(widget, popup_width, popup_height, preferred_pos):
            widget_x = widget.winfo_rootx()
            widget_y = widget.winfo_rooty()
            widget_width = widget.winfo_width()
            widget_height = widget.winfo_height()
            screen_width = widget.winfo_screenwidth()
            screen_height = widget.winfo_screenheight()

            arrow_text = "▼"
            arrow_x = "center"
            arrow_y = 0

            if preferred_pos == "bottom":
                x = widget_x + (widget_width - popup_width) // 2
                y = widget_y + widget_height + 10
                arrow_text = "▲"
                arrow_y = 0
            elif preferred_pos == "top":
                x = widget_x + (widget_width - popup_width) // 2
                y = widget_y - popup_height - 10
                arrow_text = "▼"
                arrow_y = popup_height - 5
            elif preferred_pos == "left":
                x = widget_x - popup_width - 10
                y = widget_y + (widget_height - popup_height) // 2
                arrow_text = "▶"
                arrow_x = "right"
            elif preferred_pos == "right":
                x = widget_x + widget_width + 10
                y = widget_y + (widget_height - popup_height) // 2
                arrow_text = "◀"
                arrow_x = "left"

            if x < 10:
                x = 10
            elif x + popup_width > screen_width - 10:
                x = screen_width - popup_width - 10

            if y < 10:
                y = 10
            elif y + popup_height > screen_height - 10:
                y = screen_height - popup_height - 10

            return x, y, arrow_text, arrow_x, arrow_y

        def highlight_widget(widget, active=True):
            if active:
                widget.configure(border_color="#FFFF00", border_width=3)
            else:
                widget.configure(
                    border_color=widget._orig_border_color if hasattr(widget, '_orig_border_color') else None,
                    border_width=widget._orig_border_width if hasattr(widget, '_orig_border_width') else 0
                )

        def show_step(index):
            if index >= len(steps):
                return
            widget, message, preferred_pos = steps[index]
            if not hasattr(widget, '_orig_border_color'):
                widget._orig_border_color = widget.cget("border_color")
            if not hasattr(widget, '_orig_border_width'):
                widget._orig_border_width = widget.cget("border_width")
            highlight_widget(widget, True)
            popup_width = 350
            popup_height = 180
            x, y, arrow_text, arrow_x, arrow_y = calculate_popup_position(widget, popup_width, popup_height, preferred_pos)
            tour_win = ctk.CTkToplevel(self.window)
            tour_win.title("")
            tour_win.geometry(f"{popup_width}x{popup_height}+{x}+{y}")
            tour_win.configure(fg_color="#333333")
            tour_win.attributes('-topmost', True)
            tour_win.overrideredirect(True)
            content_frame = ctk.CTkFrame(tour_win, fg_color="#444444", corner_radius=10)
            content_frame.pack(fill="both", expand=True, padx=10, pady=10)
            if arrow_x == "center":
                arrow_label = ctk.CTkLabel(content_frame, text=arrow_text, font=("Segoe UI", 24), text_color="#ffffff")
                arrow_label.pack(pady=(5 if arrow_y == 0 else 0, 0))
            elif arrow_x in ("left", "right"):
                arrow_frame = ctk.CTkFrame(content_frame, fg_color="#444444")
                arrow_frame.pack(side=arrow_x, fill="y")
                arrow_label = ctk.CTkLabel(arrow_frame, text=arrow_text, font=("Segoe UI", 24), text_color="#ffffff")
                arrow_label.pack(pady=5)
            msg_label = ctk.CTkLabel(content_frame, text=message, font=("Segoe UI", 12), text_color="#ffffff", wraplength=popup_width-40)
            msg_label.pack(padx=20, pady=20, fill="both", expand=True)
            btn_frame = ctk.CTkFrame(content_frame, fg_color="#444444")
            btn_frame.pack(pady=10)
            next_btn = ctk.CTkButton(btn_frame, text="Next",
                                     command=lambda: [highlight_widget(widget, False),
                                                      tour_win.destroy(),
                                                      show_step(index+1)],
                                     fg_color="#1E90FF", hover_color="#1C86EE", text_color="#FFFFFF", font=("Segoe UI", 12))
            next_btn.pack(side="left", padx=5)
            finish_btn = ctk.CTkButton(btn_frame, text="Finish Tour",
                                       command=lambda: [highlight_widget(widget, False),
                                                        tour_win.destroy()],
                                       fg_color="#FF4C4C", hover_color="#FF3B3B", text_color="#FFFFFF", font=("Segoe UI", 12))
            finish_btn.pack(side="left", padx=5)
        show_step(0)

    # -------------------- SAVE/LOAD LAYOUT PREFERENCES --------------------
    def save_layout_preferences(self):
        config = {
            "left_frame_visible": self.left_frame_visible,
            "right_frame_visible": self.right_frame_visible
        }
        try:
            with open(self.layout_config_file, "w") as f:
                json.dump(config, f)
        except Exception as e:
            self.logger.error(f"Failed to save layout preferences: {e}")

    def load_layout_preferences(self):
        try:
            if os.path.exists(self.layout_config_file):
                with open(self.layout_config_file, "r") as f:
                    config = json.load(f)
                self.left_frame_visible = config.get("left_frame_visible", True)
                self.right_frame_visible = config.get("right_frame_visible", True)
                if self.left_frame_visible:
                    self.left_frame.grid()
                else:
                    self.left_frame.grid_remove()
                if self.right_frame_visible:
                    self.right_frame.grid()
                else:
                    self.right_frame.grid_remove()
        except Exception as e:
            self.logger.error(f"Failed to load layout preferences: {e}")

    # -------------------- TOGGLE LEFT PANEL --------------------
    def toggle_left_panel(self):
        # Toggle the visibility of the left frame (file list)
        self.left_frame_visible = not self.left_frame_visible
        if self.left_frame_visible:
            self.left_frame.grid()
        else:
            self.left_frame.grid_remove()
        self.save_layout_preferences()

    # -------------------- SCAN LOGIC --------------------
    def select_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.start_scan(Path(folder))

    def start_scan(self, path: Path):
        try:
            threshold_mb = float(self.min_size_var.get())
            if threshold_mb < 0:
                threshold_mb = 0
            self.min_file_size_mb = threshold_mb
            self.min_file_size = int(threshold_mb * 1024 * 1024)
        except ValueError:
            self.log_error("Invalid threshold input; using default 10 MB.")
            self.min_file_size_mb = 10
            self.min_file_size = 10 * 1024 * 1024

        self.skip_system_dirs = bool(self.skip_sys_var.get())

        if self.scanning:
            if messagebox.askyesno("Confirm Cancel", "Cancel the current scan?"):
                self.stop_scan()
            return

        self.reset_scan_stats()
        self.scanning = True
        self.scan_start_time = time.time()
        self.scan_btn.configure(text="Stop Scan")
        self.status_label.configure(text="Scanning...")

        if self.two_pass_scan:
            threading.Thread(target=self.count_files_pass, args=(path,), daemon=True).start()
        else:
            threading.Thread(target=self.scan_directory, args=(path,), daemon=True).start()

    def stop_scan(self):
        self.scanning = False
        self.scan_btn.configure(text="Select Folder (Ctrl+O)")
        self.status_label.configure(text="Scan stopped")

    def reset_scan_stats(self):
        self.total_size_scanned = 0
        self.items_scanned = 0
        self.total_items = 0
        self.current_progress = 0
        self.file_heap.clear()
        self.file_list.clear()
        self.file_map.clear()
        self.file_mtime.clear()
        self.size_dict.clear()
        self.category_map = {}
        self.grouped_files = {}
        self.progress_bar.set(0)
        self.progress_label.configure(text="0%  (0/0)")
        self.selected_row = None
        self.row_original_colors = {}

    def count_files_pass(self, path: Path):
        try:
            if not path.exists():
                self.log_error(f"Path does not exist: {path}")
                return
            count = 0
            for root, dirs, files in os.walk(path):
                if not self.scanning:
                    break
                if self.skip_system_dirs and self.should_skip_dir(root):
                    dirs[:] = []
                    continue
                count += len(files)
                if count % 1000 == 0:
                    self.safe_after(0, lambda c=count: self.status_label.configure(text=f"Counting files: {c:,}..."))
            self.total_items = count
            self.safe_after(0, self.start_actual_scan, path)
        except Exception as e:
            self.logger.error(f"Counting files failed: {e}")
            self.scanning = False
            self.safe_after(0, self.scan_complete)

    def start_actual_scan(self, path: Path):
        if not self.scanning:
            return
        threading.Thread(target=self.scan_directory, args=(path,), daemon=True).start()

    def scan_directory(self, path: Path):
        try:
            if not path.exists():
                self.log_error(f"Path does not exist: {path}")
                return
            for root, dirs, files in os.walk(path):
                if not self.scanning:
                    break
                if self.skip_system_dirs and self.should_skip_dir(root):
                    dirs[:] = []
                    continue
                for filename in files:
                    if not self.scanning:
                        break
                    file_path = Path(root) / filename
                    try:
                        st = file_path.stat()
                        size = st.st_size
                        mtime = st.st_mtime
                    except (PermissionError, OSError):
                        continue
                    if size >= self.min_file_size:
                        self.total_size_scanned += size
                        self.items_scanned += 1
                        p_str = str(file_path)
                        self.file_map[p_str] = size
                        self.file_mtime[p_str] = mtime
                        ext = file_path.suffix.lower()
                        cat = self.detect_category(ext)
                        if cat not in self.category_map:
                            self.category_map[cat] = [0, 0]
                        self.category_map[cat][0] += 1
                        self.category_map[cat][1] += size
                        if cat not in self.grouped_files:
                            self.grouped_files[cat] = []
                        self.grouped_files[cat].append((size, p_str))
                        if self.top_k > 0:
                            if len(self.file_heap) < self.top_k:
                                heapq.heappush(self.file_heap, (size, p_str))
                            else:
                                smallest_size, _ = self.file_heap[0]
                                if size > smallest_size:
                                    heapq.heapreplace(self.file_heap, (size, p_str))
                        else:
                            if len(self.file_list) < MAX_RESULTS_LIMIT:
                                self.file_list.append((size, p_str))
                    self.current_progress += 1
                    if self.current_progress % self.progress_update_interval == 0:
                        self.safe_after(0, self.update_progress)
        except Exception as e:
            self.logger.error(f"Scan failed: {e}")
        finally:
            self.scanning = False
            self.safe_after(0, self.scan_complete)

    def should_skip_dir(self, root: str) -> bool:
        lower_path = root.lower()
        return any(sysdir in lower_path for sysdir in SYSTEM_DIRS)

    def detect_category(self, ext: str) -> str:
        for cat, exts in EXTENSION_CATEGORIES.items():
            if ext in exts:
                return cat
        return "Others"

    # -------------------- PROGRESS & COMPLETION --------------------
    def update_progress(self):
        elapsed = time.time() - self.scan_start_time if self.scan_start_time else 0.1
        speed = self.current_progress / elapsed if elapsed > 0 else 0
        remaining = (self.total_items - self.current_progress) / speed if speed > 0 else 0
        progress = min(self.current_progress / self.total_items, 1.0) if self.total_items > 0 else (0.0 if self.current_progress == 0 else 0.5)
        self.progress_bar.set(progress)
        pct = int(progress * 100)
        self.progress_label.configure(text=f"{pct}%  ({self.current_progress:,}/{self.total_items:,})\nSpeed: {speed:.2f} files/sec, ETA: {int(remaining)} sec")
        self.progress_bar.configure(progress_color="#1E90FF", border_color="#1E90FF")

    def scan_complete(self):
        def animate_completion(step=0):
            if not self.window.winfo_exists():
                return
            if step < 5:
                progress_color = "#00FF00" if step % 2 == 0 else "#1E90FF"
                self.progress_bar.configure(progress_color=progress_color, border_color=progress_color)
                self.safe_after(150, lambda: animate_completion(step + 1))
            else:
                if self.window.winfo_exists():
                    self.progress_bar.configure(progress_color="#1E90FF", border_color="#1E90FF")
                    self.progress_label.configure(
                        text=f"Scan Complete! ({self.items_scanned:,} files, {humanize.naturalsize(self.total_size_scanned)})"
                    )
                    self.scan_btn.configure(text="Select Folder (Ctrl+O)")
                    self.update_results()
        animate_completion()

    # -------------------- RESULTS DISPLAY --------------------
    def update_results(self):
        for widget in self.file_scroll_frame.winfo_children():
            widget.destroy()
        filter_text = self.filter_var.get().lower()
        for category, files in self.grouped_files.items():
            filtered_files = [(sz, path) for sz, path in files if filter_text in os.path.basename(path).lower()]
            if not filtered_files:
                continue
            sort_order = self.sort_options.get()
            if "Name" in sort_order:
                filtered_files.sort(key=lambda x: os.path.basename(x[1]).lower(), reverse=("Desc" in sort_order))
            else:
                filtered_files.sort(key=lambda x: x[0], reverse=("Desc" in sort_order))
            section_frame = ctk.CTkFrame(self.file_scroll_frame, fg_color="#333333", corner_radius=5)
            section_frame.pack(fill="x", padx=10, pady=5)
            header_frame = ctk.CTkFrame(section_frame, fg_color="#444444")
            header_frame.pack(fill="x")
            is_expanded = ctk.BooleanVar(value=True)
            def toggle_section(frame=section_frame, var=is_expanded):
                if var.get():
                    for child in frame.winfo_children()[1:]:
                        child.pack_forget()
                    var.set(False)
                else:
                    for child in frame.winfo_children()[1:]:
                        child.pack(fill="x", padx=5, pady=2)
                    var.set(True)
            toggle_btn = ctk.CTkButton(header_frame, text=f"{category} ({len(filtered_files)})", command=toggle_section,
                                       font=("Segoe UI", 12), fg_color="#444444", hover_color="#555555", text_color="#FFFFFF")
            toggle_btn.pack(side="left", padx=5, pady=5)
            ToolTip(toggle_btn, f"Expand or collapse the {category} section.", self)
            for (size, path_str) in filtered_files:
                row_color = self.pick_size_color(size)
                row_frame = ctk.CTkFrame(section_frame, fg_color=row_color, corner_radius=5)
                row_frame.pack(fill="x", padx=10, pady=(0, 4))
                self.row_original_colors[row_frame] = row_color
                row_frame.bind("<Button-1>", lambda event, frame=row_frame: self.select_row(frame))
                row_frame.bind("<Button-3>", lambda event, p=path_str: self.show_context_menu(event, p))
                file_name = os.path.basename(path_str)
                size_human = humanize.naturalsize(size)
                file_label = ctk.CTkLabel(row_frame, text=file_name, anchor="w", width=300,
                                           fg_color=row_color, font=("Segoe UI", 12), text_color="#FFFFFF")
                file_label.pack(side="left", padx=5, fill="x", expand=True)
                file_label.bind("<Button-1>", lambda event, frame=row_frame: self.select_row(frame))
                file_label.bind("<Button-3>", lambda event, p=path_str: self.show_context_menu(event, p))
                ToolTip(file_label, f"File: {file_name}", self)
                size_label = ctk.CTkLabel(row_frame, text=size_human, anchor="e", width=100,
                                           fg_color=row_color, font=("Segoe UI", 12), text_color="#FFFFFF")
                size_label.pack(side="left", padx=5)
                size_label.bind("<Button-1>", lambda event, frame=row_frame: self.select_row(frame))
                size_label.bind("<Button-3>", lambda event, p=path_str: self.show_context_menu(event, p))
                ToolTip(size_label, f"Size: {size_human}", self)
                actions_frame = ctk.CTkFrame(row_frame, fg_color=row_color, width=160)
                actions_frame.pack(side="left", padx=5)
                actions_frame.bind("<Button-3>", lambda event, p=path_str: self.show_context_menu(event, p))
                open_btn = ctk.CTkButton(
                    actions_frame, text="Open Folder", width=65, corner_radius=5,
                    fg_color="#1E90FF", hover_color="#1C86EE", text_color="#FFFFFF",
                    font=("Segoe UI", 12), command=lambda p=path_str: self.open_in_explorer(p)
                )
                open_btn.pack(side="left", padx=3)
                ToolTip(open_btn, "Open the folder containing this file.", self)
                delete_btn = ctk.CTkButton(
                    actions_frame, text="Delete", width=65, corner_radius=5,
                    fg_color="#FF4C4C", hover_color="#FF3B3B", text_color="#FFFFFF",
                    font=("Segoe UI", 12), command=lambda p=path_str: self.delete_file(p)
                )
                delete_btn.pack(side="left", padx=3)
                ToolTip(delete_btn, "Delete this file (after confirmation).", self)
        self.status_label.configure(text=f"Listed files above threshold.")

    def show_context_menu(self, event, file_path):
        menu = Menu(self.window, tearoff=0)
        menu.add_command(label="Open Folder", command=lambda: self.open_in_explorer(file_path))
        menu.add_command(label="Delete", command=lambda: self.delete_file(file_path))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def pick_size_color(self, size_bytes: int) -> str:
        gb_size = size_bytes / (1024 ** 3)
        if gb_size > 2:
            return "#3C3C3C"
        elif gb_size > 0.7:
            return "#333333"
        elif gb_size > 0.1:
            return "#2A2A2A"
        else:
            return "#242424"

    def select_row(self, selected_frame):
        if self.selected_row and self.selected_row != selected_frame:
            original_color = self.row_original_colors.get(self.selected_row, "#242424")
            self.selected_row.configure(fg_color=original_color)
        selected_frame.configure(fg_color="#1E90FF")
        self.selected_row = selected_frame

    # -------------------- FILE ACTIONS --------------------
    def open_in_explorer(self, file_path: str):
        try:
            if os.name == "nt":
                subprocess.run(["explorer", "/select,", file_path], check=False)
            else:
                os.startfile(file_path)
        except Exception as e:
            self.log_error(f"Error opening folder for file: {file_path}\n{e}")

    def delete_file(self, file_path: str):
        if messagebox.askyesno("Confirm Deletion", f"Delete file?\n{file_path}"):
            try:
                os.remove(file_path)
                self.status_label.configure(text=f"Deleted: {file_path}")
                if file_path in self.file_map:
                    del self.file_map[file_path]
                self.file_list = [(sz, p) for (sz, p) in self.file_list if p != file_path]
                for cat in self.grouped_files:
                    self.grouped_files[cat] = [(sz, p) for (sz, p) in self.grouped_files[cat] if p != file_path]
            except Exception as e:
                self.log_error(f"Error deleting file: {file_path}\n{e}")
            finally:
                self.update_results()

    # -------------------- ANALYSIS THINKING HELPERS --------------------
    def animate_thinking(self, label):
        texts = ["Thinking", "Thinking.", "Thinking..", "Thinking..."]
        def update(index=0):
            if label.winfo_exists():
                label.configure(text=texts[index % len(texts)])
                self.safe_after(500, update, index+1)
        update()

    def append_thinking_bubble_analysis(self):
        bubble_bg = "#444444"
        text_color = "#FFFFFF"
        bubble_frame = ctk.CTkFrame(self.ai_scroll_frame, fg_color=bubble_bg, corner_radius=10)
        thinking_label = ctk.CTkLabel(bubble_frame, text="Thinking", font=("Segoe UI", 18),
                                      text_color=text_color, wraplength=600, justify="left", padx=5, pady=5)
        thinking_label.pack(anchor="w", padx=5, pady=(0,5))
        bubble_frame.pack(fill="x", padx=10, pady=5, anchor="w")
        self.animate_thinking(thinking_label)
        self.ai_scroll_frame.update_idletasks()
        return bubble_frame

    def append_deepseek_response_analysis(self, message):
        pattern = re.compile(r"<\s*think\s*>(.*?)<\s*/\s*think\s*>", re.DOTALL | re.IGNORECASE)
        match = pattern.search(message)
        if match:
            think_content = match.group(1).strip()
            main_text = pattern.sub("", message).strip()
            bubble_bg = "#444444"
            text_color = "#FFFFFF"
            bubble_frame = ctk.CTkFrame(self.ai_scroll_frame, fg_color=bubble_bg, corner_radius=10)
            sender_label = ctk.CTkLabel(bubble_frame, text="Analysis", font=("Segoe UI", 16, "bold"),
                                        text_color=text_color, anchor="w", padx=5, pady=2)
            sender_label.pack(anchor="w", padx=5, pady=(5,0))
            main_label = ctk.CTkLabel(bubble_frame, text=main_text, font=("Segoe UI", 18),
                                      text_color=text_color, wraplength=600, justify="left", padx=5, pady=5)
            main_label.pack(anchor="w", padx=5, pady=(0,5))
            # Create a frame for toggling the hidden "think" details.
            toggle_frame = ctk.CTkFrame(bubble_frame, fg_color=bubble_bg)
            toggle_frame.pack(anchor="w", padx=5, pady=(0,5))
            details_label = ctk.CTkLabel(toggle_frame, text=think_content, font=("Segoe UI", 16),
                                          text_color=text_color, wraplength=600, justify="left", padx=5, pady=5)
            details_label.pack_forget()  # Hide details initially.
            def toggle_details():
                if details_label.winfo_ismapped():
                    details_label.pack_forget()
                    toggle_btn.configure(text="Show Thinking Process")
                else:
                    details_label.pack(anchor="w", padx=5, pady=(0,5))
                    toggle_btn.configure(text="Hide Thinking Process")
            toggle_btn = ctk.CTkButton(toggle_frame, text="Show Thinking Process", command=toggle_details,
                                         font=("Segoe UI", 12), fg_color="#1E90FF", hover_color="#1C86EE", text_color="#FFFFFF")
            toggle_btn.pack(anchor="w", padx=5, pady=(0,5))
            bubble_frame.pack(fill="x", padx=10, pady=5, anchor="w")
            self.ai_scroll_frame.update_idletasks()
        else:
            self.append_ai_message(message)

    def build_analysis_prompt(self) -> str:
        if not self.file_map:
            return "No files scanned yet."
        total_size = sum(self.file_map.values())
        file_count = len(self.file_map)
        sorted_files = sorted(self.file_map.items(), key=lambda x: x[1], reverse=True)
        top_files = sorted_files[:10]
        largest_file_section = [
            f"{i+1}. {os.path.basename(f)} - {humanize.naturalsize(s)} (Location: {f})"
            for i, (f, s) in enumerate(top_files)
        ]
        largest_files_str = "\n".join(largest_file_section)
        category_section = []
        for cat, (count, sz) in self.category_map.items():
            category_section.append(f"{cat}: {count} file(s), total {humanize.naturalsize(sz)}")
        category_summary = "\n".join(category_section) if category_section else "No category data available."
        duplicate_section = []
        for sz, paths in self.size_dict.items():
            if len(paths) > 1:
                duplicate_section.append(f"{humanize.naturalsize(sz)}: {len(paths)} files")
        duplicates_str = ("\nPotential duplicates by size:\n" + "\n".join(duplicate_section)
                          if duplicate_section else "No obvious duplicates by size.")
        prompt = f"""
You are an expert disk management AI.

- Total files: {file_count}
- Total size: {humanize.naturalsize(total_size)}

**Largest 10 files:**
{largest_files_str}

**Category Breakdown:**
{category_summary}

**Duplicate Check:**
{duplicates_str}

Provide a concise analysis with recommended cleanup, compression, or archiving steps,
including performance trade-offs and bullet-pointed advice.
"""
        return prompt

    # -------------------- ANALYSIS & AI --------------------
    def trigger_ai_analysis(self):
        if not self.ai_enabled:
            self.show_analysis_error("AI features are disabled")
            return
        if not self.file_map:
            self.show_analysis_error("No files scanned yet")
            return
        self.analyze_btn.configure(state="disabled")
        threading.Thread(target=self.run_analysis, daemon=True).start()

    def run_analysis(self):
        try:
            provider = self.ai_provider.get() if hasattr(self, "ai_provider") else "Gemini"
            if provider == "DeepSeekR1":
                prompt = self.build_analysis_prompt()
                thinking_bubble = self.append_thinking_bubble_analysis()
                results = self.generate_ai_content(prompt)
                self.safe_after(0, thinking_bubble.destroy)
                self.safe_after(0, self.append_deepseek_response_analysis, results)
            else:
                results = self.generate_ai_content(self.build_analysis_prompt())
                self.safe_after(0, self.update_analysis_results, results)
        except Exception as e:
            self.logger.error(f"Analysis failed: {e}")
            self.safe_after(0, self.show_analysis_error, str(e))
        finally:
            self.safe_after(0, self.reset_analysis_button)

    def generate_ai_content(self, prompt: str) -> str:
        provider = self.ai_provider.get() if hasattr(self, "ai_provider") else "Gemini"
        if provider == "Gemini":
            if self.model is None:
                try:
                    load_dotenv()
                    api_key = os.getenv("GEMINI_API_KEY")
                    if not api_key:
                        raise ValueError("Gemini API key not found in environment")
                    genai.configure(api_key=api_key)
                    self.model = genai.GenerativeModel("gemini-1.5-pro")
                    self.ai_enabled = True
                except Exception as e:
                    self.logger.error(f"Gemini AI initialization failed: {e}")
                    self.ai_enabled = False
                    return "Gemini AI is not available."
            response = self.model.generate_content(prompt)
            self.logger.debug(f"Gemini response: {response.text}")
            return response.text
        elif provider == "DeepSeekR1":
            try:
                url = f"http://localhost:{self.ollama_port}/api/generate"
                payload = {
                    "model": "deepseek-r1",  # Use the 7B model for RTX 3060Ti.
                    "stream": False,
                    "prompt": prompt
                }
                self.logger.debug(f"Sending request to DeepSeekR1: {payload}")
                r = requests.post(url, json=payload)
                self.logger.debug(f"Received HTTP {r.status_code} from DeepSeekR1")
                if r.status_code == 200:
                    try:
                        data = r.json()
                        self.logger.debug(f"DeepSeekR1 response data: {data}")
                        return data.get("response", "").strip()
                    except Exception as json_e:
                        self.logger.error(f"JSON decode error: {json_e}")
                        self.logger.debug(f"Response text: {r.text}")
                        return "DeepSeekR1 response parsing failed."
                else:
                    self.logger.error(f"DeepSeekR1 HTTP error: {r.status_code} - {r.text}")
                    return f"DeepSeekR1 is not available (HTTP error {r.status_code})."
            except Exception as e:
                self.logger.error(f"DeepSeekR1 HTTP request failed: {e}")
                return "DeepSeekR1 is not available."
        else:
            return "Unknown AI provider."

    def update_analysis_results(self, results: str):
        for widget in self.ai_scroll_frame.winfo_children():
            widget.destroy()
        total_size = sum(self.file_map.values())
        header = "=== Disk Space Analysis ===\n\n"
        summary = (f"Total Space Analyzed: {humanize.naturalsize(total_size)}\n"
                   f"Files Analyzed: {len(self.file_map)}\n\n")
        full_message = header + summary + "AI Recommendations:\n" + results
        self.append_ai_message(full_message)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        history_entry = f"--- {timestamp} ---\nTotal Space: {humanize.naturalsize(total_size)} | Files: {len(self.file_map)}\n{results}\n\n"
        self.analysis_history.append(history_entry)
        self.update_history_tab()

    def append_ai_message(self, message):
        bubble_bg = "#008080"  # Teal for analysis messages.
        text_color = "#FFFFFF"
        bubble_frame = ctk.CTkFrame(self.ai_scroll_frame, fg_color=bubble_bg, corner_radius=10)
        sender_label = ctk.CTkLabel(bubble_frame, text="Analysis", font=("Segoe UI", 16, "bold"),
                                    text_color=text_color, anchor="w", padx=5, pady=2)
        sender_label.pack(anchor="w", padx=5, pady=(5, 0))
        message_label = ctk.CTkLabel(bubble_frame, text=message, font=("Segoe UI", 18),
                                     text_color=text_color, wraplength=600, justify="left", padx=5, pady=5)
        message_label.pack(anchor="w", padx=5, pady=(0, 5))
        bubble_frame.pack(fill="x", padx=10, pady=5, anchor="w")
        self.ai_scroll_frame.update_idletasks()

    def update_history_tab(self):
        self.history_textbox.delete("1.0", "end")
        for entry in self.analysis_history[-10:]:
            self.history_textbox.insert("end", entry + "\n")

    def reset_analysis_button(self):
        self.analyze_btn.configure(state="normal")

    def show_analysis_error(self, error_msg: str):
        for widget in self.ai_scroll_frame.winfo_children():
            widget.destroy()
        self.append_ai_message(f"Error: {error_msg}")
        self.reset_analysis_button()

    def export_analysis(self):
        file_path = filedialog.asksaveasfilename(defaultextension=".txt", filetypes=[("Text files", "*.txt")])
        if file_path:
            try:
                with open(file_path, "w") as f:
                    f.write(self.history_textbox.get("1.0", "end"))
                messagebox.showinfo("Export Successful", f"Analysis exported to {file_path}")
            except Exception as e:
                self.log_error(f"Failed to export analysis: {e}")

    def show_chart_window(self):
        chart_window = ctk.CTkToplevel(self.window)
        chart_window.title("Comprehensive File Type Analysis")
        chart_window.geometry("1000x700")
        chart_window.grab_set()
        if not self.category_map:
            messagebox.showinfo("No Data", "No category data available to chart.")
            return
        categories = list(self.category_map.keys())
        counts = [self.category_map[cat][0] for cat in categories]
        sizes = [self.category_map[cat][1] for cat in categories]
        total_count = sum(counts)
        total_size = sum(sizes)
        avg_sizes = [s / c if c > 0 else 0 for c, s in zip(counts, sizes)]
        data = sorted(zip(categories, counts, sizes, avg_sizes), key=lambda x: x[1], reverse=True)
        if data:
            categories, counts, sizes, avg_sizes = zip(*data)
        else:
            categories, counts, sizes, avg_sizes = ([], [], [], [])
        fig, axs = plt.subplots(2, 2, figsize=(18, 12))
        fig.patch.set_facecolor('#2A2A2A')
        def autopct_counts(pct):
            absolute = int(pct / 100 * total_count)
            return f"{pct:.1f}%\n({absolute})"
        axs[0, 0].pie(counts, labels=categories, autopct=autopct_counts, textprops={'color': 'w'},
                      pctdistance=0.6, labeldistance=1.1)
        axs[0, 0].set_title("File Count by Type", color='w')
        axs[0, 0].set_facecolor('#2A2A2A')
        def autopct_sizes(pct):
            absolute = humanize.naturalsize(int(pct / 100 * total_size))
            return f"{pct:.1f}%\n({absolute})"
        axs[0, 1].pie(sizes, labels=categories, autopct=autopct_sizes, textprops={'color': 'w'},
                      pctdistance=0.6, labeldistance=1.1)
        axs[0, 1].set_title("Total Size by Type", color='w')
        axs[0, 1].set_facecolor('#2A2A2A')
        bars = axs[1, 0].bar(categories, avg_sizes, color="#1E90FF")
        axs[1, 0].set_title("Average File Size by Type", color='w')
        axs[1, 0].set_ylabel("Average Size (bytes)", color='w')
        axs[1, 0].set_facecolor('#2A2A2A')
        axs[1, 0].tick_params(axis='x', rotation=45, colors='w', labelsize=10)
        axs[1, 0].tick_params(axis='y', colors='w', labelsize=10)
        for bar in bars:
            height = bar.get_height()
            axs[1, 0].text(bar.get_x() + bar.get_width()/2, height,
                           humanize.naturalsize(height),
                           ha='center', va='bottom', color='w', fontsize=10)
        cell_text = []
        rows = []
        for cat, count, size, avg in zip(categories, counts, sizes, avg_sizes):
            percent_count = f"{(count/total_count*100):.1f}%" if total_count > 0 else "0%"
            percent_size = f"{(size/total_size*100):.1f}%" if total_size > 0 else "0%"
            rows.append(cat)
            cell_text.append([str(count),
                              humanize.naturalsize(size),
                              humanize.naturalsize(avg),
                              percent_count,
                              percent_size])
        col_labels = ["Count", "Total Size", "Avg Size", "Count %", "Size %"]
        axs[1, 1].axis('tight')
        axs[1, 1].axis('off')
        table = axs[1, 1].table(cellText=cell_text, rowLabels=rows, colLabels=col_labels,
                                 loc='center', cellLoc='center')
        table.auto_set_font_size(False)
        table.set_fontsize(10)
        table.scale(1, 2)
        axs[1, 1].set_title("Type Details", color='w')
        plt.tight_layout(pad=3.0)
        canvas = FigureCanvasTkAgg(fig, master=chart_window)
        canvas.draw()
        canvas.get_tk_widget().pack(fill="both", expand=True)
        close_btn = ctk.CTkButton(chart_window, text="Close", command=chart_window.destroy,
                                   fg_color="#FF4C4C", hover_color="#FF3B3B", text_color="#FFFFFF",
                                   font=("Segoe UI", 12))
        close_btn.pack(pady=10)

    # -------------------- CHATBOT THINKING HELPERS --------------------
    def animate_thinking_chat(self, label):
        texts = ["Thinking", "Thinking.", "Thinking..", "Thinking..."]
        def update(index=0):
            if label.winfo_exists():
                label.configure(text=texts[index % len(texts)])
                self.safe_after(500, update, index+1)
        update()

    def append_thinking_bubble(self):
        bubble_bg = "#444444"
        text_color = "#FFFFFF"
        bubble_frame = ctk.CTkFrame(self.chat_scroll_frame, fg_color=bubble_bg, corner_radius=10)
        thinking_label = ctk.CTkLabel(bubble_frame, text="Thinking", font=("Segoe UI", 18),
                                      text_color=text_color, wraplength=600, justify="left", padx=5, pady=5)
        thinking_label.pack(anchor="w", padx=5, pady=(0,5))
        bubble_frame.pack(fill="x", padx=10, pady=5, anchor="w")
        self.animate_thinking_chat(thinking_label)
        self.chat_scroll_frame.update_idletasks()
        return bubble_frame

    def append_deepseek_response(self, message):
        # For Chatbot responses – similar to analysis but using chat_scroll_frame.
        pattern = re.compile(r"<\s*think\s*>(.*?)<\s*/\s*think\s*>", re.DOTALL | re.IGNORECASE)
        match = pattern.search(message)
        if match:
            think_content = match.group(1).strip()
            main_text = pattern.sub("", message).strip()
            bubble_bg = "#444444"
            text_color = "#FFFFFF"
            bubble_frame = ctk.CTkFrame(self.chat_scroll_frame, fg_color=bubble_bg, corner_radius=10)
            sender_label = ctk.CTkLabel(bubble_frame, text="Analysis", font=("Segoe UI", 16, "bold"),
                                        text_color=text_color, anchor="w", padx=5, pady=2)
            sender_label.pack(anchor="w", padx=5, pady=(5,0))
            main_label = ctk.CTkLabel(bubble_frame, text=main_text, font=("Segoe UI", 18),
                                      text_color=text_color, wraplength=600, justify="left", padx=5, pady=5)
            main_label.pack(anchor="w", padx=5, pady=(0,5))
            toggle_frame = ctk.CTkFrame(bubble_frame, fg_color=bubble_bg)
            toggle_frame.pack(anchor="w", padx=5, pady=(0,5))
            details_label = ctk.CTkLabel(toggle_frame, text=think_content, font=("Segoe UI", 16),
                                          text_color=text_color, wraplength=600, justify="left", padx=5, pady=5)
            details_label.pack_forget()
            def toggle_details():
                if details_label.winfo_ismapped():
                    details_label.pack_forget()
                    toggle_btn.configure(text="Show Thinking Process")
                else:
                    details_label.pack(anchor="w", padx=5, pady=(0,5))
                    toggle_btn.configure(text="Hide Thinking Process")
            toggle_btn = ctk.CTkButton(toggle_frame, text="Show Thinking Process", command=toggle_details,
                                         font=("Segoe UI", 12), fg_color="#1E90FF", hover_color="#1C86EE", text_color="#FFFFFF")
            toggle_btn.pack(anchor="w", padx=5, pady=(0,5))
            bubble_frame.pack(fill="x", padx=10, pady=5, anchor="w")
            self.chat_scroll_frame.update_idletasks()
        else:
            self.append_chat_message("Analysis", message)

    # -------------------- ANALYSIS THINKING HELPERS (for AI Analysis tab) --------------------
    def append_thinking_bubble_analysis(self):
        bubble_bg = "#444444"
        text_color = "#FFFFFF"
        bubble_frame = ctk.CTkFrame(self.ai_scroll_frame, fg_color=bubble_bg, corner_radius=10)
        thinking_label = ctk.CTkLabel(bubble_frame, text="Thinking", font=("Segoe UI", 18),
                                      text_color=text_color, wraplength=600, justify="left", padx=5, pady=5)
        thinking_label.pack(anchor="w", padx=5, pady=(0,5))
        bubble_frame.pack(fill="x", padx=10, pady=5, anchor="w")
        self.animate_thinking(thinking_label)
        self.ai_scroll_frame.update_idletasks()
        return bubble_frame

    def append_deepseek_response_analysis(self, message):
        pattern = re.compile(r"<\s*think\s*>(.*?)<\s*/\s*think\s*>", re.DOTALL | re.IGNORECASE)
        match = pattern.search(message)
        if match:
            think_content = match.group(1).strip()
            main_text = pattern.sub("", message).strip()
            bubble_bg = "#444444"
            text_color = "#FFFFFF"
            bubble_frame = ctk.CTkFrame(self.ai_scroll_frame, fg_color=bubble_bg, corner_radius=10)
            sender_label = ctk.CTkLabel(bubble_frame, text="Analysis", font=("Segoe UI", 16, "bold"),
                                        text_color=text_color, anchor="w", padx=5, pady=2)
            sender_label.pack(anchor="w", padx=5, pady=(5,0))
            main_label = ctk.CTkLabel(bubble_frame, text=main_text, font=("Segoe UI", 18),
                                      text_color=text_color, wraplength=600, justify="left", padx=5, pady=5)
            main_label.pack(anchor="w", padx=5, pady=(0,5))
            toggle_frame = ctk.CTkFrame(bubble_frame, fg_color=bubble_bg)
            toggle_frame.pack(anchor="w", padx=5, pady=(0,5))
            details_label = ctk.CTkLabel(toggle_frame, text=think_content, font=("Segoe UI", 16),
                                          text_color=text_color, wraplength=600, justify="left", padx=5, pady=5)
            details_label.pack_forget()  # Hide details initially.
            def toggle_details():
                if details_label.winfo_ismapped():
                    details_label.pack_forget()
                    toggle_btn.configure(text="Show Thinking Process")
                else:
                    details_label.pack(anchor="w", padx=5, pady=(0,5))
                    toggle_btn.configure(text="Hide Thinking Process")
            toggle_btn = ctk.CTkButton(toggle_frame, text="Show Thinking Process", command=toggle_details,
                                         font=("Segoe UI", 12), fg_color="#1E90FF", hover_color="#1C86EE", text_color="#FFFFFF")
            toggle_btn.pack(anchor="w", padx=5, pady=(0,5))
            bubble_frame.pack(fill="x", padx=10, pady=5, anchor="w")
            self.ai_scroll_frame.update_idletasks()
        else:
            self.append_ai_message(message)

    # -------------------- CHATBOT FUNCTIONALITY --------------------
    def send_chat_message(self):
        message = self.chat_entry.get().strip()
        if not message:
            return
        self.append_chat_message("User", message)
        self.chat_entry.delete(0, "end")
        threading.Thread(target=self.run_chat_response, args=(message,), daemon=True).start()

    def run_chat_response(self, user_message):
        if not self.ai_enabled:
            self.safe_after(0, self.append_chat_message, "Assistant", "AI features are disabled.")
            return
        context = ""
        if self.file_map:
            total_size = sum(self.file_map.values())
            file_count = len(self.file_map)
            largest_file_path, largest_file_size = max(self.file_map.items(), key=lambda item: item[1])
            largest_file_info = f"{os.path.basename(largest_file_path)} - {humanize.naturalsize(largest_file_size)} (Location: {largest_file_path})"
            category_summary = "\n".join(
                f"{cat}: {data[0]} files, {humanize.naturalsize(data[1])}"
                for cat, data in self.category_map.items()
            )
            context = (
                f"Current scan results:\n"
                f"Total files: {file_count}\n"
                f"Total size: {humanize.naturalsize(total_size)}\n"
                f"Largest file: {largest_file_info}\n"
                f"Category breakdown:\n{category_summary}\n"
            )
        else:
            context = "No scan data available."
        prompt = (
            "You are a helpful disk management assistant. Use the following context from the current scan to answer the query:\n"
            f"{context}\nUser query: {user_message}"
        )
        provider = self.ai_provider.get() if hasattr(self, "ai_provider") else "Gemini"
        if provider == "DeepSeekR1":
            thinking_bubble = self.append_thinking_bubble()
            answer = self.generate_ai_content(prompt)
            self.safe_after(0, thinking_bubble.destroy)
            self.safe_after(0, self.append_deepseek_response, answer)
        else:
            answer = self.generate_ai_content(prompt)
            self.safe_after(0, self.append_chat_message, "Analysis", answer)

    def append_chat_message(self, sender, message):
        if sender == "User":
            bubble_bg = "#1E90FF"
            text_color = "#FFFFFF"
            anchor_side = "e"
        else:
            bubble_bg = "#444444"
            text_color = "#FFFFFF"
            anchor_side = "w"
        bubble_frame = ctk.CTkFrame(self.chat_scroll_frame, fg_color=bubble_bg, corner_radius=10)
        sender_label = ctk.CTkLabel(bubble_frame, text=sender, font=("Segoe UI", 16, "bold"),
                                    text_color=text_color, anchor="w", padx=5, pady=2)
        sender_label.pack(anchor="w", padx=5, pady=(5, 0))
        message_label = ctk.CTkLabel(bubble_frame, text=message, font=("Segoe UI", 18),
                                     text_color=text_color, wraplength=600, justify="left", padx=5, pady=5)
        message_label.pack(anchor="w", padx=5, pady=(0, 5))
        bubble_frame.pack(fill="x", padx=10, pady=5, anchor=anchor_side)
        self.chat_scroll_frame.update_idletasks()

    # -------------------- EXIT APPLICATION --------------------
    def exit_app(self):
        self.window.destroy()

    # -------------------- MAIN LOOP --------------------
    def run(self):
        self.window.mainloop()

    # -------------------- NEW: TOGGLE LEFT PANEL --------------------
    def toggle_left_panel(self):
        # Toggle the visibility of the left frame (file list)
        self.left_frame_visible = not self.left_frame_visible
        if self.left_frame_visible:
            self.left_frame.grid()
        else:
            self.left_frame.grid_remove()
        self.save_layout_preferences()

    def save_layout_preferences(self):
        config = {
            "left_frame_visible": self.left_frame_visible,
            "right_frame_visible": self.right_frame_visible
        }
        try:
            with open(self.layout_config_file, "w") as f:
                json.dump(config, f)
        except Exception as e:
            self.logger.error(f"Failed to save layout preferences: {e}")

    def load_layout_preferences(self):
        try:
            if os.path.exists(self.layout_config_file):
                with open(self.layout_config_file, "r") as f:
                    config = json.load(f)
                self.left_frame_visible = config.get("left_frame_visible", True)
                self.right_frame_visible = config.get("right_frame_visible", True)
                if self.left_frame_visible:
                    self.left_frame.grid()
                else:
                    self.left_frame.grid_remove()
                if self.right_frame_visible:
                    self.right_frame.grid()
                else:
                    self.right_frame.grid_remove()
        except Exception as e:
            self.logger.error(f"Failed to load layout preferences: {e}")

if __name__ == "__main__":
    try:
        app = DiskAnalyzerGUI()
        app.run()
    except Exception as e:
        print(f"Application failed to start: {e}")
