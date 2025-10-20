#!/usr/bin/env python3
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
import traceback
import threading

from .er_soundbank_helper import transfer_wwise_main


class ToolTip:
    def __init__(self, widget, text: str):
        self.widget = widget
        self.text = text
        self.tooltip = None
        self.widget.bind("<Enter>", self.show)
        self.widget.bind("<Leave>", self.hide)

    def show(self, event=None) -> None:
        x, y, _, _ = self.widget.bbox("insert")
        x += self.widget.winfo_rootx() + 25
        y += self.widget.winfo_rooty() + 25

        self.tooltip = tk.Toplevel(self.widget)
        self.tooltip.wm_overrideredirect(True)
        self.tooltip.wm_geometry(f"+{x}+{y}")

        label = tk.Label(
            self.tooltip,
            text=self.text,
            background="#ffffe0",
            relief="solid",
            borderwidth=1,
            padx=5,
            pady=3,
            wraplength=300,
            justify="left",
        )
        label.pack()

    def hide(self, event=None) -> None:
        if self.tooltip:
            self.tooltip.destroy()
            self.tooltip = None


class LoadingDialog(tk.Toplevel):
    def __init__(self, parent, message: str = "Processing..."):
        super().__init__(parent)

        self.title("Please Wait")
        self.transient(parent)  # Set to be on top of parent
        self.grab_set()  # Block interaction with parent

        # Remove window decorations for a cleaner look
        self.overrideredirect(True)

        # Center the dialog
        self.geometry("300x100")

        # Create frame with border
        frame = tk.Frame(self, relief="solid", borderwidth=2, bg="white")
        frame.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)

        # Message
        tk.Label(frame, text=message, bg="white", font=("TkDefaultFont", 10)).pack(
            pady=20
        )

        # Progress bar (indeterminate mode)
        self.progress = ttk.Progressbar(frame, mode="indeterminate", length=250)
        self.progress.pack(pady=10)
        self.progress.start(10)  # Start animation

        # Center on parent
        self.update_idletasks()
        x = parent.winfo_x() + (parent.winfo_width() // 2) - (self.winfo_width() // 2)
        y = parent.winfo_y() + (parent.winfo_height() // 2) - (self.winfo_height() // 2)
        self.geometry(f"+{x}+{y}")

    def close(self):
        self.progress.stop()
        self.destroy()


class SoundbankHelperGui(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("ERSoundbankHelper")
        self.geometry("600x500")

        # File paths
        self.src_bank_path: str = ""
        self.dst_bank_path: str = ""

        # Create UI
        self._create_widgets()

    def _create_widgets(self) -> None:
        # File selection frame
        file_frame = ttk.Frame(self, padding="10")
        file_frame.pack(fill=tk.X)

        # File 1 with help icon in front
        help1 = ttk.Label(file_frame, text="❓", foreground="blue", cursor="hand2")
        help1.grid(row=0, column=0, sticky=tk.W, pady=5)
        ToolTip(help1, "Select the soundbank your sounds are coming from")

        ttk.Label(file_frame, text="Source Soundbank").grid(
            row=0, column=1, sticky=tk.W, padx=(5, 10), pady=5
        )
        self.src_bank_label = ttk.Label(
            file_frame, text="No file selected", foreground="gray"
        )
        self.src_bank_label.grid(row=0, column=2, sticky=tk.W, padx=10)
        ttk.Button(file_frame, text="Browse", command=self._browse_src_bank).grid(
            row=0, column=3
        )

        # File 2 with help icon in front
        help2 = ttk.Label(file_frame, text="❓", foreground="blue", cursor="hand2")
        help2.grid(row=1, column=0, sticky=tk.W, pady=5)
        ToolTip(help2, "Select the soundbank you want to copy the sounds to")

        ttk.Label(file_frame, text="Destination Soundbank:").grid(
            row=1, column=1, sticky=tk.W, padx=(5, 10), pady=5
        )
        self.dst_bank_label = ttk.Label(
            file_frame, text="No file selected", foreground="gray"
        )
        self.dst_bank_label.grid(row=1, column=2, sticky=tk.W, padx=10)
        ttk.Button(file_frame, text="Browse", command=self._browse_dst_bank).grid(
            row=1, column=3
        )

        # Text boxes frame
        text_frame = ttk.Frame(self, padding="10")
        text_frame.pack(fill=tk.BOTH, expand=True)

        # Container for both text boxes
        text_container = tk.Frame(text_frame)
        text_container.pack(fill=tk.BOTH, expand=True)

        # Left text box
        left_frame = tk.Frame(text_container)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))

        # Label with tooltip for left box
        left_label_row = tk.Frame(left_frame)
        left_label_row.pack(anchor=tk.W)

        help_left = ttk.Label(
            left_label_row, text="❓", foreground="blue", cursor="hand2"
        )
        help_left.pack(side=tk.LEFT)
        ToolTip(
            help_left,
            "The wwise IDs to copy from the source soundbank. Each line represents one entry and must follow the format 'xYYYY...', where x is a single character and Y 8-10 digits.",
        )

        ttk.Label(left_label_row, text="Source Wwise IDs").pack(
            side=tk.LEFT, padx=(5, 0)
        )

        # Left text box with scrollbar
        left_scroll = ttk.Scrollbar(left_frame)
        left_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.src_wwise_ids = tk.Text(
            left_frame, width=30, height=10, yscrollcommand=left_scroll.set
        )
        self.src_wwise_ids.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        left_scroll.config(command=self.src_wwise_ids.yview)

        # Right text box
        right_frame = tk.Frame(text_container)
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0))

        # Label with tooltip for right box
        right_label_row = tk.Frame(right_frame)
        right_label_row.pack(anchor=tk.W)

        help_right = ttk.Label(
            right_label_row, text="❓", foreground="blue", cursor="hand2"
        )
        help_right.pack(side=tk.LEFT)
        ToolTip(
            help_right,
            "How the copied sounds will be named in the destination soundbank. Each line represents one entry and must follow the format 'xYYYY...', where x is a single character and Y 8-10 digits.",
        )

        ttk.Label(right_label_row, text="Destination Wwise IDs").pack(
            side=tk.LEFT, padx=(5, 0)
        )

        # Right text box with scrollbar
        right_scroll = ttk.Scrollbar(right_frame)
        right_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.dst_wwise_ids = tk.Text(
            right_frame, width=30, height=10, yscrollcommand=right_scroll.set
        )
        self.dst_wwise_ids.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right_scroll.config(command=self.dst_wwise_ids.yview)

        # Checkboxes
        check_frame = ttk.Frame(self, padding="10")
        check_frame.pack(fill=tk.X)

        self.enable_write_var = tk.BooleanVar(value=True)
        self.no_questions_var = tk.BooleanVar(value=False)

        ttk.Checkbutton(
            check_frame, text="Write to destination", variable=self.enable_write_var
        ).pack(anchor=tk.W)
        ttk.Checkbutton(
            check_frame, text="No questions", variable=self.no_questions_var
        ).pack(anchor=tk.W)

        # Separator
        ttk.Separator(self, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=10, pady=10)

        # Explanation text
        info_frame = ttk.Frame(self, padding="10")
        info_frame.pack(fill=tk.X)

        info_text = tk.Text(info_frame, height=4, wrap=tk.WORD)
        info_text.insert(
            "1.0",
            'This tool allows you to transfer sounds from one soundbank to another. Streamed sounds are not handled (yet). If you see errors that some sounds could not be found, search for them in the game\'s "sd/wem" folder and copy them manually.',
        )
        info_text.config(state=tk.DISABLED)
        info_text.pack(fill=tk.X)

        # Start button
        ttk.Button(self, text="Transfer", command=self._exec_transfer).pack(pady=10)

    def _browse_src_bank(self) -> None:
        path = filedialog.askopenfilename(
            title="Select first soundbank.json",
            filetypes=[("JSON files", "soundbank.json"), ("All files", "*.*")],
        )
        if path:
            print(f"Selected source soundbank: {path}")
            self.src_bank_path = path
            self.src_bank_label.config(text=Path(path).parent.name, foreground="black")

    def _browse_dst_bank(self) -> None:
        path = filedialog.askopenfilename(
            title="Select second soundbank.json",
            filetypes=[("JSON files", "soundbank.json"), ("All files", "*.*")],
        )
        if path:
            print(f"Selected destination soundbank: {path}")
            self.dst_bank_path = path
            self.dst_bank_label.config(text=Path(path).parent.name, foreground="black")

    def _exec_transfer(self) -> None:
        try:
            # Source soundbank path
            if not self.src_bank_path:
                raise ValueError("Source soundbank not set")

            src_bank_dir = Path(self.src_bank_path).parent
            if not src_bank_dir.is_dir():
                raise ValueError("Source soundbank folder does not exist")

            # Destination soundbank path
            if not self.dst_bank_path:
                raise ValueError("Destination soundbank not set")

            dst_bank_dir = Path(self.dst_bank_path).parent
            if not dst_bank_dir.is_dir():
                raise ValueError("Destination soundbank folder does not exist")

            # Get text from both boxes and split by lines
            src_wwise_lines = self.src_wwise_ids.get("1.0", tk.END).strip().split("\n")
            dst_wwise_lines = self.dst_wwise_ids.get("1.0", tk.END).strip().split("\n")

            if not src_wwise_lines:
                raise ValueError("No lines were specified")

            if len(src_wwise_lines) != len(dst_wwise_lines):
                raise ValueError("Number of lines did not match")

            # Create rows from the lines
            wwise_map = {}
            max_len = max(len(src_wwise_lines), len(dst_wwise_lines))
            for i in range(max_len):
                left_val = (
                    src_wwise_lines[i].strip() if i < len(src_wwise_lines) else ""
                )
                right_val = (
                    dst_wwise_lines[i].strip() if i < len(dst_wwise_lines) else ""
                )
                wwise_map[left_val] = right_val

            enable_write = self.enable_write_var.get()
            no_questions = self.no_questions_var.get()

            # Show loading dialog
            loading = LoadingDialog(self, "Transferring sounds...")

            # Run it!
            def do_the_work():
                try:
                    transfer_wwise_main(
                        src_bank_dir,
                        dst_bank_dir,
                        wwise_map,
                        enable_write=enable_write,
                        no_questions=no_questions,
                    )
                except Exception as inner_exc:
                    traceback.print_exception(inner_exc)
                    self.after(
                        0, lambda e=inner_exc: messagebox.showerror("Error", str(e))
                    )
                else:
                    messagebox.showinfo("Transfer successful", "Yay!")
                finally:
                    self.after(0, loading.close)

            thread = threading.Thread(target=do_the_work, daemon=True)
            thread.start()
        except Exception as e:
            traceback.print_exception(e)
            messagebox.showerror("Transfer failed", str(e))


if __name__ == "__main__":
    app = SoundbankHelperGui()
    app.mainloop()
