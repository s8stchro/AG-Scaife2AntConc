"""Native Windows workspace: automatic export, preparation, progress and occurrence review."""
import os
from pathlib import Path
import queue
import re
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ag_diogenes_spacy_txt2xml import diogenes
from ag_diogenes_spacy_txt2xml.greta import default_path
from ag_diogenes_spacy_txt2xml.greberta import default_path as default_context_path
from . import APP_NAME, __version__, catalog
from .pipeline import (Options, SetupError, expected_results, old_results, plan, run, shown,
                       unsaved_corrections)
from .reader import STEPS, read
from .session import Saved
from .workspace import Workspace, POS_NAMES, identity

BG, PAPER, INK, MUTED, ACCENT, SOFT = '#f1f4f3', '#ffffff', '#23373d', '#586b72', '#17695e', '#e4f1ec'
POS_LABELS = {code: f'{name} ({code})' for code, name in POS_NAMES.items()}
POS_CODES = {label: code for code, label in POS_LABELS.items()}
VIEWS = [('files', 'Κείμενα'), ('prepare', 'Προετοιμασία'), ('progress', 'Ανάλυση'),
         ('review', 'Έλεγχος λημμάτων'), ('results', 'Αποτελέσματα')]


class Cancelled(Exception):
    pass


class ScrollPanel(ttk.Frame):
    def __init__(self, parent):
        super().__init__(parent, style='Paper.TFrame')
        self.canvas = tk.Canvas(self, bg=PAPER, highlightthickness=0, width=300)
        bar = ttk.Scrollbar(self, command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=bar.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        bar.pack(side='right', fill='y')
        self.content = ttk.Frame(self.canvas, padding=18, style='Paper.TFrame')
        window = self.canvas.create_window((0, 0), window=self.content, anchor='nw')
        self.content.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(window, width=e.width))
        self.winfo_toplevel().bind('<MouseWheel>', self._wheel, add='+')

    def _wheel(self, event):
        widget = self.winfo_containing(event.x_root, event.y_root)
        if isinstance(widget, (tk.Text, ttk.Treeview, ttk.Combobox)):
            return
        while widget:
            if widget is self:
                self.canvas.yview_scroll(-int(event.delta / 120), 'units')
                return 'break'
            widget = getattr(widget, 'master', None)


def readonly_text(parent, height=3, greek=False):
    box = tk.Text(parent, height=height, wrap='word', relief='flat', bg=PAPER, fg=INK,
                  font=('Cambria', 15) if greek else ('Segoe UI', 10), padx=10, pady=8,
                  highlightthickness=1, highlightbackground='#dbe3e0', state='disabled')
    return box


def put_text(box, text):
    box.configure(state='normal')
    box.delete('1.0', 'end')
    box.insert('1.0', text)
    box.configure(state='disabled')


class App:
    def __init__(self, root, files):
        from .gui import load_settings, save_settings, open_path, scan, QUESTIONS
        self.root, self.settings = root, load_settings()
        self.save_settings, self.open_path, self.scan, self.questions = save_settings, open_path, scan, QUESTIONS
        self.messages = queue.Queue()
        self.cancel_event, self.commit_event = threading.Event(), threading.Event()
        self.busy, self.closing, self.dirty = False, False, False
        self.workspace, self.saved, self.selected = None, None, None
        self.report, self.current_view, self.phase = None, 'files', ''
        self.started, self.phase_started = 0.0, 0.0
        self.file_paths, self.visible_records = [], {}
        self.mode = tk.StringVar(value=self.settings.get('workflow', 'quick'))
        if self.mode.get() not in ('quick', 'review'):
            self.mode.set('quick')
        self.outdir = tk.StringVar(value=self.settings.get('outdir', ''))
        self.corrections = tk.StringVar(value=self.settings.get('corrections', ''))
        self.dio_path = self.settings.get('diogenes') or None
        self.greta_path = self.settings.get('greta_model') or None
        self.context_path = self.settings.get('context_model') or None
        self.accents = tk.BooleanVar(value=False)
        self.xml = tk.BooleanVar(value=False)
        self.clean_vars = {step: tk.BooleanVar(value=True) for step in STEPS}
        self.filter = tk.StringVar(value='Αμφίβολες')
        self.query = tk.StringVar()
        self.scope = tk.StringVar(value='one')
        self.status = tk.StringVar(value='Προσθέστε XML ή ανοίξτε προηγούμενα αποτελέσματα.')
        self.clock = tk.StringVar(value='')
        self._style()
        root.title(f'{APP_NAME} {__version__}')
        width, height = min(1180, root.winfo_screenwidth() - 70), min(850, root.winfo_screenheight() - 90)
        root.geometry(f'{max(900, width)}x{max(650, height)}')
        root.minsize(880, 640)
        root.configure(bg=BG)
        self._shell()
        self._files_view()
        self._prepare_view()
        self._progress_view()
        self._review_view()
        self._results_view()
        for path in files:
            self.add_file(path)
        self._mode_changed()
        self.show('files')
        root.protocol('WM_DELETE_WINDOW', self.close)
        root.after(120, self.poll)

    def _style(self):
        style = ttk.Style(self.root)
        if 'clam' in style.theme_names():
            style.theme_use('clam')
        style.configure('.', font=('Segoe UI', 10), foreground=INK)
        style.configure('TFrame', background=BG)
        style.configure('TLabel', background=BG)
        style.configure('Paper.TFrame', background=PAPER)
        style.configure('Paper.TLabel', background=PAPER)
        style.configure('Hint.TLabel', foreground=MUTED)
        style.configure('PaperHint.TLabel', background=PAPER, foreground=MUTED)
        style.configure('Title.TLabel', font=('Segoe UI', 19), foreground=INK)
        style.configure('Subtitle.TLabel', font=('Segoe UI', 12))
        style.configure('Greek.TLabel', font=('Cambria', 15), background=PAPER)
        style.configure('TButton', padding=(12, 8), background=PAPER)
        style.configure('Primary.TButton', background=ACCENT, foreground=PAPER, padding=(16, 10))
        style.map('Primary.TButton', background=[('active', '#11584f'), ('disabled', '#9baaa5')],
                  foreground=[('disabled', '#f5f5f5')])
        style.configure('Nav.TButton', background=BG, anchor='w', padding=(12, 12), borderwidth=0, relief='flat')
        style.configure('Selected.Nav.TButton', background=SOFT, foreground=ACCENT, anchor='w', padding=(12, 12), borderwidth=0, relief='flat')
        style.configure('TRadiobutton', background=BG, padding=(3, 5))
        style.configure('TCheckbutton', background=PAPER, padding=(3, 5))
        style.configure('Treeview', rowheight=31, fieldbackground=PAPER, background=PAPER)
        style.map('Treeview', background=[('selected', ACCENT)], foreground=[('selected', PAPER)])
        style.configure('Words.Treeview', font=('Cambria', 12), rowheight=33)
        style.configure('TProgressbar', background=ACCENT, troughcolor='#dce5e1')

    def _shell(self):
        header = ttk.Frame(self.root, padding=(20, 13), style='Paper.TFrame')
        header.pack(fill='x')
        tk.Label(header, text='α', bg=ACCENT, fg=PAPER, font=('Cambria', 24), width=2).pack(side='left', padx=(0, 12))
        brand = ttk.Frame(header, style='Paper.TFrame')
        brand.pack(side='left')
        ttk.Label(brand, text='Scaife → AntConc', font=('Segoe UI', 16), style='Paper.TLabel').pack(anchor='w')
        ttk.Label(brand, text='Κείμενο · λήμματα · φιλολογικός έλεγχος', style='PaperHint.TLabel').pack(anchor='w')
        self.settings_button = ttk.Button(header, text='Ρυθμίσεις πόρων', command=self.settings_dialog)
        ttk.Button(header, text='Έξοδος', command=self.close).pack(side='right', padx=(8, 0))
        self.settings_button.pack(side='right')
        modes = ttk.Frame(self.root, padding=(20, 8))
        modes.pack(fill='x')
        self.mode_buttons = []
        for value, title in [('quick', 'Γρήγορη εξαγωγή'), ('review', 'Με φιλολογικό έλεγχο')]:
            b = ttk.Radiobutton(modes, text=title, variable=self.mode, value=value, command=self._mode_changed)
            b.pack(side='left', padx=(0, 24))
            self.mode_buttons.append(b)
        self.mode_hint = ttk.Label(modes, style='Hint.TLabel')
        self.mode_hint.pack(side='left')
        ttk.Separator(self.root).pack(fill='x')
        shell = ttk.Frame(self.root)
        shell.pack(fill='both', expand=True)
        rail = ttk.Frame(shell, padding=(10, 15))
        rail.pack(side='left', fill='y')
        self.nav = {}
        for key, text in VIEWS:
            b = ttk.Button(rail, text=text, style='Nav.TButton', width=20, command=lambda k=key: self.show(k))
            b.pack(fill='x', pady=2)
            self.nav[key] = b
        self.open_previous = ttk.Button(rail, text='Άνοιγμα αποτελεσμάτων…', command=self.choose_results, width=23)
        self.open_previous.pack(side='bottom', pady=8)
        self.body = ttk.Frame(shell, padding=(18, 16, 20, 12))
        self.body.pack(side='left', fill='both', expand=True)
        self.body.rowconfigure(0, weight=1)
        self.body.columnconfigure(0, weight=1)
        self.pages = {key: ttk.Frame(self.body) for key, _ in VIEWS}
        for page in self.pages.values():
            page.grid(row=0, column=0, sticky='nsew')
        footer = ttk.Frame(self.root, padding=(18, 9))
        footer.pack(fill='x')
        ttk.Label(footer, textvariable=self.status, style='Hint.TLabel', wraplength=850).pack(side='left')
        ttk.Label(footer, text=f'v{__version__}', style='Hint.TLabel').pack(side='right')

    def heading(self, parent, title, hint):
        ttk.Label(parent, text=title, style='Title.TLabel').pack(anchor='w')
        ttk.Label(parent, text=hint, style='Hint.TLabel', wraplength=850).pack(anchor='w', pady=(3, 15))

    def _files_view(self):
        page = self.pages['files']
        self.heading(page, 'Τα κείμενά σας', 'XML από το Scaife Viewer ή το Perseus. Ένα ή περισσότερα έργα σε ένα σώμα.')
        actions = ttk.Frame(page)
        actions.pack(fill='x', pady=(0, 10))
        self.file_buttons = []
        for title, command in [('Προσθήκη XML…', self.add_files), ('Αφαίρεση', self.remove_files)]:
            b = ttk.Button(actions, text=title, command=command)
            b.pack(side='left', padx=(0, 8))
            self.file_buttons.append(b)
        frame = ttk.Frame(page)
        frame.pack(fill='both', expand=True)
        self.files = ttk.Treeview(frame, columns=('title', 'lines', 'file'), show='headings', height=6)
        for key, title, width in [('title', 'Έργο', 340), ('lines', 'Ενότητες', 90), ('file', 'Αρχείο', 240)]:
            self.files.heading(key, text=title)
            self.files.column(key, width=width, minwidth=60, stretch=key != 'lines')
        self.files.pack(side='left', fill='both', expand=True)
        bar = ttk.Scrollbar(frame, command=self.files.yview)
        self.files.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        output = ttk.Frame(page, padding=(0, 15))
        output.pack(fill='x')
        ttk.Label(output, text='Φάκελος αποτελεσμάτων').pack(anchor='w')
        row = ttk.Frame(output)
        row.pack(fill='x', pady=5)
        self.output_entry = ttk.Entry(row, textvariable=self.outdir)
        self.output_entry.pack(side='left', fill='x', expand=True)
        self.output_button = ttk.Button(row, text='Επιλογή…', command=self.choose_outdir)
        self.output_button.pack(side='left', padx=(8, 0))
        self.quick_hint = ttk.Label(page, wraplength=850, style='Hint.TLabel')
        self.quick_hint.pack(anchor='w', pady=(3, 10))
        self.start_button = ttk.Button(page, text='Ανάλυση και εξαγωγή', style='Primary.TButton', command=self.start)
        self.start_button.pack(anchor='e')

    def _prepare_view(self):
        page = self.pages['prepare']
        self.heading(page, 'Προετοιμασία κειμένου', 'Οι επιλογές και τα παραδείγματα βρίσκονται μαζί, χωρίς διαδοχικά παράθυρα.')
        top = ttk.Frame(page)
        top.pack(fill='x', pady=(0, 8))
        self.scan_button = ttk.Button(top, text='Ανανέωση παραδειγμάτων', command=self.prepare_examples)
        self.scan_button.pack(side='left')
        self.prep_run = ttk.Button(top, text='Έναρξη ανάλυσης', style='Primary.TButton', command=self.start)
        self.prep_run.pack(side='right')
        scroll = ScrollPanel(page)
        scroll.pack(fill='both', expand=True)
        self.prep_labels = {}
        for step in STEPS:
            title = self.questions[step][0]
            label = 'Ένωση λέξεων με ενωτικό' if step == 'hyphenation' else 'Αφαίρεση: ' + title.lower()
            ttk.Checkbutton(scroll.content, text=label, variable=self.clean_vars[step]).pack(anchor='w')
            example = ttk.Label(scroll.content, text='Πατήστε «Ανανέωση παραδειγμάτων».', style='PaperHint.TLabel', wraplength=700)
            example.pack(anchor='w', padx=25, pady=(0, 10))
            self.prep_labels[step] = example
        ttk.Separator(scroll.content).pack(fill='x', pady=10)
        ttk.Checkbutton(scroll.content, text='Κανονικοποίηση τονισμού', variable=self.accents).pack(anchor='w')
        ttk.Label(scroll.content, text='Αρχικά διατηρείται ο τονισμός της έκδοσης.', style='PaperHint.TLabel').pack(anchor='w', padx=25)
        ttk.Checkbutton(scroll.content, text='Επιπλέον XML με μορφολογία και προέλευση', variable=self.xml).pack(anchor='w', pady=(12, 0))
        ttk.Label(scroll.content, text='Εισαγωγή προηγούμενων διορθώσεων (προαιρετική):', style='Paper.TLabel').pack(anchor='w', pady=(15, 3))
        row = ttk.Frame(scroll.content, style='Paper.TFrame')
        row.pack(fill='x')
        ttk.Entry(row, textvariable=self.corrections).pack(side='left', fill='x', expand=True)
        ttk.Button(row, text='Επιλογή…', command=self.choose_corrections).pack(side='left', padx=5)
        ttk.Button(row, text='Απαλοιφή', command=lambda: self.corrections.set('')).pack(side='left')

    def _progress_view(self):
        page = self.pages['progress']
        self.heading(page, 'Πρόοδος ανάλυσης', 'Η πρόοδος εμφανίζεται ανά στάδιο. Το GreTa παραμένει ενεργό και στις δύο λειτουργίες.')
        paper = ttk.Frame(page, padding=22, style='Paper.TFrame')
        paper.pack(fill='x')
        self.phase_label = ttk.Label(paper, text='Έτοιμο για εκτέλεση', style='Paper.TLabel', font=('Segoe UI', 14))
        self.phase_label.pack(anchor='w')
        self.progress = ttk.Progressbar(paper, mode='determinate', value=0)
        self.progress.pack(fill='x', pady=18)
        self.progress_count = ttk.Label(paper, text='', style='PaperHint.TLabel')
        self.progress_count.pack(anchor='w')
        ttk.Label(paper, textvariable=self.clock, style='PaperHint.TLabel').pack(anchor='w', pady=(4, 0))
        stages = ['Πόροι και προετοιμασία', 'Γραμματική συμφραζομένων', 'Προτάσεις GreTa',
                  'Βαθμολόγηση εναλλακτικών', 'Αποθήκευση αποτελεσμάτων']
        self.stage_labels = []
        for name in stages:
            label = ttk.Label(paper, text='○  ' + name, style='PaperHint.TLabel')
            label.pack(anchor='w', pady=(9, 0))
            self.stage_labels.append((name, label))
        self.cancel_button = ttk.Button(page, text='Ακύρωση στο επόμενο ασφαλές σημείο', command=self.cancel)
        self.cancel_button.pack(anchor='e', pady=12)
        self.details_button = ttk.Button(page, text='Εμφάνιση λεπτομερειών', command=self.toggle_log)
        self.details_button.pack(anchor='w')
        self.log_frame = ttk.Frame(page)
        self.log_text = readonly_text(self.log_frame, height=8)
        self.log_text.pack(side='left', fill='both', expand=True)
        logbar = ttk.Scrollbar(self.log_frame, command=self.log_text.yview)
        logbar.pack(side='right', fill='y')
        self.log_text.configure(yscrollcommand=logbar.set)

    def _review_view(self):
        page = self.pages['review']
        self.heading(page, 'Έλεγχος λημμάτων', '«Αμφίβολες»: ουσιαστικές αμφιβολίες λήμματος ή ανάπτυξης. Η γραμματική και οι λοιπές διαφωνίες έχουν χωριστή προβολή.')
        top = ttk.Frame(page)
        top.pack(fill='x', pady=(0, 10))
        ttk.Entry(top, textvariable=self.query, width=23).pack(side='left', fill='x', expand=True)
        ttk.Label(top, text='  Προβολή: ').pack(side='left')
        ttk.Combobox(top, textvariable=self.filter, values=['Αμφίβολες', 'Λοιπές επισημάνσεις', 'Όλες', 'Αποθηκευμένες'], state='readonly', width=21).pack(side='left')
        self.apply_button = ttk.Button(top, text='Ενημέρωση αρχείων', style='Primary.TButton', command=self.apply_choices)
        self.apply_button.pack(side='left', padx=(10, 0))
        self.review_summary = ttk.Label(page, style='Hint.TLabel')
        self.review_summary.pack(anchor='w', pady=(0, 7))
        pane = ttk.Panedwindow(page, orient='horizontal')
        pane.pack(fill='both', expand=True)
        left = ttk.Frame(pane)
        self.words = ttk.Treeview(left, columns=('form', 'place', 'state'), show='headings', style='Words.Treeview', selectmode='browse')
        for key, title, width in [('form', 'Μορφή', 160), ('place', 'Θέση', 75), ('state', 'Έλεγχος', 80)]:
            self.words.heading(key, text=title)
            self.words.column(key, width=width, minwidth=45, stretch=key == 'form')
        self.words.pack(side='left', fill='both', expand=True)
        bar = ttk.Scrollbar(left, command=self.words.yview)
        self.words.configure(yscrollcommand=bar.set)
        bar.pack(side='right', fill='y')
        pane.add(left, weight=1)
        detail_container = ttk.Frame(pane, style='Paper.TFrame')
        pane.add(detail_container, weight=2)
        detail_scroll = self.detail_scroll = ScrollPanel(detail_container)
        detail_scroll.pack(fill='both', expand=True)
        self.detail = detail_scroll.content
        self.record_title = ttk.Label(self.detail, text='Ανοίξτε αποτελέσματα ή επιλέξτε μια λέξη.', style='Greek.TLabel', wraplength=600)
        self.record_title.pack(anchor='w')
        self.context = readonly_text(self.detail, height=3, greek=True)
        self.context.pack(fill='x', pady=10)
        self.context.tag_configure('target', background=SOFT, foreground=ACCENT)
        self.reason = ttk.Label(self.detail, text='', style='PaperHint.TLabel', wraplength=600)
        self.reason.pack(anchor='w', pady=(0, 12))
        self.parts_frame = ttk.Frame(self.detail, style='Paper.TFrame')
        self.parts_frame.pack(fill='x')
        self.part_vars = []
        self.scope_frame = ttk.Frame(self.detail, style='Paper.TFrame')
        self.scope_frame.pack(fill='x', pady=12)
        ttk.Label(self.scope_frame, text='Εφαρμογή της επιλογής:', style='Paper.TLabel').pack(anchor='w')
        ttk.Radiobutton(self.scope_frame, text='Μόνο σε αυτή τη θέση', variable=self.scope, value='one').pack(anchor='w')
        ttk.Radiobutton(self.scope_frame, text='Σε όλες τις ίδιες μορφές με το ίδιο αρχικό λήμμα', variable=self.scope, value='all').pack(anchor='w')
        self.save_choice_button = ttk.Button(detail_container, text='Αποθήκευση επιλογής / επιβεβαίωσης', command=self.save_choice)
        self.save_choice_button.pack(side='bottom', fill='x', padx=12, pady=7)
        self.choice_message = ttk.Label(self.detail, style='PaperHint.TLabel', wraplength=600)
        self.choice_message.pack(anchor='w')
        ttk.Label(self.detail, text='Πηγές και εναλλακτικές', style='Paper.TLabel', font=('Segoe UI', 11)).pack(anchor='w', pady=(18, 5))
        self.evidence = readonly_text(self.detail, height=10)
        self.evidence.pack(fill='x')
        self.detail.bind('<Configure>', lambda e: [label.configure(wraplength=max(200, e.width - 38))
                                                 for label in (self.record_title, self.reason, self.choice_message)])
        self.words.bind('<<TreeviewSelect>>', self.select_word)
        self.query.trace_add('write', lambda *_: self.refresh_words())
        self.filter.trace_add('write', lambda *_: self.refresh_words())

    def _results_view(self):
        page = self.pages['results']
        self.heading(page, 'Αποτελέσματα για το AntConc', 'Μπορείτε να χρησιμοποιήσετε την αυτόματη εξαγωγή και να ελέγξετε τις λέξεις αργότερα.')
        box = ttk.Frame(page, padding=22, style='Paper.TFrame')
        box.pack(fill='both', expand=True)
        self.result_title = ttk.Label(box, text='Δεν έχει ολοκληρωθεί ανάλυση.', style='Paper.TLabel', font=('Segoe UI', 15))
        self.result_title.pack(anchor='w')
        self.result_status = ttk.Label(box, text='', style='PaperHint.TLabel', wraplength=760)
        self.result_status.pack(anchor='w', pady=(10, 18))
        for title, rel in [('Κείμενα για το AntConc', 'antconc-tagged'), ('Κείμενα λημμάτων για το AntConc', 'antconc-lemmata'),
                           ('Φύλλο διορθώσεων', 'lemma-review.tsv'),
                           ('Αναφορά εκτέλεσης', 'report.txt')]:
            row = ttk.Frame(box, padding=(0, 8), style='Paper.TFrame')
            row.pack(fill='x')
            ttk.Label(row, text=title, style='Paper.TLabel').pack(side='left')
            ttk.Button(row, text='Άνοιγμα', command=lambda r=rel: self.open_result(r)).pack(side='right')
        actions = ttk.Frame(box, style='Paper.TFrame')
        actions.pack(fill='x', pady=22)
        ttk.Button(actions, text='Έλεγχος λημμάτων τώρα', command=lambda: self.show('review')).pack(side='left')
        ttk.Button(actions, text='Άνοιγμα φακέλου', style='Primary.TButton', command=lambda: self.open_result('')).pack(side='right')
        help_button = ttk.Button(box, text='Πώς ανοίγουν στο AntConc;', command=self.antconc_help)
        help_button.pack(anchor='w')

    def _mode_changed(self):
        quick = self.mode.get() == 'quick'
        self.mode_hint.configure(text='Αυτόματα · έλεγχος αργότερα' if quick else 'Επιλογές και έλεγχος ανά εμφάνιση')
        self.quick_hint.configure(text=('Προτεινόμενος καθαρισμός, τονισμός όπως στην έκδοση. '
            'Η επεξεργασία με GreTa χρειάζεται χρόνο. Τα αποτελέσματα επισημαίνονται ως μη ελεγμένα.' if quick else
            'Προσαρμόστε τον καθαρισμό στην «Προετοιμασία». Μετά την ανάλυση ανοίγει ο έλεγχος λημμάτων.'))
        self.start_button.configure(text='Ανάλυση και εξαγωγή' if quick else 'Προετοιμασία κειμένου')
        self.nav['prepare'].state(['disabled'] if quick else ['!disabled'])
        if self.current_view == 'prepare' and quick:
            self.show('files')
        self.save()

    def show(self, view):
        if view == 'prepare' and self.mode.get() == 'quick':
            return
        if view != 'review' and self.dirty and not self.check_draft():
            return
        self.pages[view].tkraise()
        self.current_view = view
        for key, button in self.nav.items():
            button.configure(style='Selected.Nav.TButton' if key == view else 'Nav.TButton')

    def add_file(self, path):
        path = os.path.abspath(path)
        if os.path.normcase(path) in {os.path.normcase(p) for p in self.file_paths}:
            return
        try:
            work = read(path)
            entry = catalog.lookup(work.ident)
            title, lines = entry.display if entry else work.title or work.ident, len(work.lines)
        except Exception:
            title, lines = 'Μη αναγνωρισμένο XML', '—'
        self.file_paths.append(path)
        self.files.insert('', 'end', iid=str(len(self.file_paths) - 1), values=(title, lines, os.path.basename(path)))
        if not self.outdir.get():
            self.outdir.set(os.path.join(os.path.dirname(path), 'antconc-output'))

    def add_files(self):
        for path in filedialog.askopenfilenames(title='Αρχεία XML', filetypes=[('XML', '*.xml'), ('Όλα', '*.*')]):
            self.add_file(path)

    def remove_files(self):
        selected = set(self.files.selection())
        paths = [p for i, p in enumerate(self.file_paths) if str(i) not in selected]
        self.file_paths = []
        self.files.delete(*self.files.get_children())
        for path in paths:
            self.add_file(path)

    def choose_outdir(self):
        path = filedialog.askdirectory(title='Φάκελος αποτελεσμάτων', initialdir=self.outdir.get() or None)
        if path:
            self.outdir.set(path)

    def choose_corrections(self):
        path = filedialog.askopenfilename(title='Φύλλο διορθώσεων', filetypes=[('Φύλλο', '*.tsv *.csv *.txt')])
        if path:
            self.corrections.set(path)

    def options(self):
        quick = self.mode.get() == 'quick'
        return Options(session=True, diogenes=self.dio_path, greta_model=self.greta_path, context_model=self.context_path,
                       keep=frozenset() if quick else frozenset(k for k, v in self.clean_vars.items() if not v.get()),
                       accents=False if quick else self.accents.get(),
                       corrections=None if quick else self.corrections.get().strip() or None,
                       xml=False if quick else self.xml.get(), morphology=True, provenance=True)

    def prepare_examples(self):
        self.root.configure(cursor='watch')
        self.root.update_idletasks()
        try:
            found = self.scan(self.file_paths)
            for step, label in self.prep_labels.items():
                count, words, examples = found.get(step, (0, 0, []))
                text = f'{count:,} σημεία · {words:,} λέξεις' + ('\n' + '\n'.join(examples[:2]) if examples else '') if count else 'Δεν βρέθηκε στο επιλεγμένο κείμενο.'
                label.configure(text=text)
        finally:
            self.root.configure(cursor='')

    def start(self):
        if self.busy:
            return
        if self.mode.get() == 'review' and self.current_view == 'files':
            self.show('prepare')
            self.prepare_examples()
            return
        if not self.file_paths or not self.outdir.get().strip():
            messagebox.showinfo(APP_NAME, 'Προσθέστε XML και επιλέξτε φάκελο αποτελεσμάτων.')
            return
        opts = self.options()
        folder = self.outdir.get().strip()
        try:
            if not self.ask_replace(self.file_paths, opts):
                return
            pending = unsaved_corrections(folder, opts)
            if pending:
                if not messagebox.askyesno(APP_NAME, 'Υπάρχουν αποθηκευμένες διορθώσεις στον φάκελο. '
                                          'Να χρησιμοποιηθούν σε αυτή την ανάλυση;\n'
                                          'Αν επιλέξετε Όχι, διαλέξτε άλλον φάκελο.', default='no'):
                    return
                opts.corrections = pending
        except (OSError, ValueError, SetupError) as error:
            messagebox.showerror(APP_NAME, str(error))
            return
        self.begin(self.file_paths[:], folder, opts)

    def ask_replace(self, paths, opts):
        old = old_results(self.outdir.get().strip(), expected_results(plan(paths, opts.keep), opts))
        if old:
            if not messagebox.askyesno(APP_NAME, 'Τα παρακάτω παλιά αποτελέσματα δεν ανήκουν στη νέα εκτέλεση:\n'
                                      + shown(old, 8) + '\n\nΝα αφαιρεθούν κατά την αποθήκευση;', default='no'):
                return False
            opts.replace = True
        return True

    def begin(self, paths, folder, opts, replay=None):
        if self.busy or not self.check_draft():
            return
        self.save()
        self.busy, self.started, self.phase_started = True, time.monotonic(), time.monotonic()
        self.phase = ''
        self.cancel_event.clear()
        self.commit_event.clear()
        self.set_busy(True)
        put_text(self.log_text, '')
        self.show('progress')
        self.set_phase(0, 'Προετοιμασία αποθηκευμένων αναλύσεων…' if replay else 'Φόρτωση πόρων…')
        def worker():
            def log(message):
                if self.cancel_event.is_set() and not self.commit_event.is_set():
                    raise Cancelled()
                self.messages.put(('log', message))
            def commit():
                if self.cancel_event.is_set():
                    raise Cancelled()
                self.commit_event.set()
                self.messages.put(('commit', None))
            try:
                if replay:
                    replay.reset()
                report = run(paths, folder, opts, log=log, lemmatizer=replay, before_publish=commit)
                workspace = Workspace(folder)
                try:
                    saved = Saved.load(folder)
                except SetupError:
                    saved = None
                self.messages.put(('done', (report, workspace, saved, self.mode_value)))
            except Cancelled:
                self.messages.put(('cancelled', None))
            except Exception as error:
                self.messages.put(('error', (str(error), traceback.format_exc())))
        self.mode_value = self.mode.get()
        threading.Thread(target=worker, daemon=True).start()

    def set_busy(self, busy):
        controls = self.file_buttons + self.mode_buttons + [self.settings_button, self.output_entry, self.output_button,
                    self.start_button, self.prep_run, self.scan_button, self.open_previous,
                    self.apply_button, self.save_choice_button]
        for widget in controls:
            widget.state(['disabled'] if busy else ['!disabled'])
        self.nav['files'].state(['disabled'] if busy else ['!disabled'])
        self.nav['review'].state(['disabled'] if busy else ['!disabled'])
        self.nav['prepare'].state(['disabled'] if busy or self.mode.get() == 'quick' else ['!disabled'])
        self.cancel_button.state(['!disabled'] if busy else ['disabled'])

    def set_phase(self, stage, text):
        if text != self.phase:
            self.phase, self.phase_started = text, time.monotonic()
        self.phase_label.configure(text=text)
        for i, (name, label) in enumerate(self.stage_labels):
            label.configure(text=('✓  ' if i < stage else '●  ' if i == stage else '○  ') + name,
                            foreground=ACCENT if i == stage else MUTED)
        self.progress.stop()
        self.progress.configure(mode='indeterminate')
        self.progress.start(14)
        self.progress_count.configure(text='')

    def progress_message(self, text):
        match = re.search(r'GreTa (λήμματα|υποψήφιοι): ([\d,]+)/([\d,]+)', text)
        if match:
            phase = 2 if match[1] == 'λήμματα' else 3
            title = 'Προτάσεις λημμάτων GreTa' if phase == 2 else 'Βαθμολόγηση εναλλακτικών'
            if self.phase != title:
                self.set_phase(phase, title)
            count, total = int(match[2].replace(',', '')), int(match[3].replace(',', ''))
            self.progress.stop()
            self.progress.configure(mode='determinate', maximum=max(1, total), value=count)
            elapsed = time.monotonic() - self.phase_started
            remaining = f' · εκτίμηση υπολοίπου φάσης ~{int(elapsed / count * (total-count))} δ.' if count > 8 and elapsed > 2 and total > count else ''
            self.progress_count.configure(text=f'{count:,} / {total:,}' + remaining)
        elif 'GreBERTa joint:' in text or 'OdyCy: γραμματική' in text:
            self.set_phase(1, 'Γραμματική συμφραζομένων')
        elif 'GreTa: συγκριτικά' in text or 'GreTa: comparator' in text or 'GreTa: νευρωνικά' in text:
            self.set_phase(2, 'Προτάσεις λημμάτων GreTa')
        elif 'GreTa: βαθμολόγηση' in text:
            self.set_phase(3, 'Βαθμολόγηση εναλλακτικών')

    def poll(self):
        if self.busy:
            elapsed = int(time.monotonic() - self.started)
            self.clock.set(f'Χρόνος εκτέλεσης: {elapsed//60}:{elapsed%60:02d}')
        while True:
            try:
                kind, value = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == 'log':
                self.log_text.configure(state='normal')
                self.log_text.insert('end', value + '\n')
                self.log_text.see('end')
                self.log_text.configure(state='disabled')
                self.progress_message(value)
                continue
            if kind == 'commit':
                self.set_phase(4, 'Αποθήκευση αποτελεσμάτων')
                self.cancel_button.state(['disabled'])
                continue
            self.busy = False
            self.progress.stop()
            if self.closing:
                if kind == 'error':
                    self.root.deiconify()
                    messagebox.showerror(APP_NAME, value[0], parent=self.root)
                self.root.destroy()
                return
            self.set_busy(False)
            if kind == 'done':
                self.set_phase(5, 'Ολοκληρώθηκε')
                self.progress.stop()
                self.progress.configure(mode='determinate', maximum=1, value=1)
                self.report, self.workspace, self.saved, mode = value
                self.outdir.set(str(self.workspace.folder))
                self.refresh_words()
                self.refresh_results()
                self.status.set('Η εξαγωγή ολοκληρώθηκε. Ο έλεγχος παραμένει διαθέσιμος.')
                self.show('results' if mode == 'quick' else 'review')
            elif kind == 'cancelled':
                self.status.set('Η ανάλυση ακυρώθηκε. Τα προηγούμενα αποτελέσματα διατηρήθηκαν.')
                self.phase_label.configure(text='Ακυρώθηκε')
            else:
                message, trace = value
                self.status.set('Η εκτέλεση δεν ολοκληρώθηκε. Δείτε τις λεπτομέρειες.')
                put_text(self.log_text, self.log_text.get('1.0', 'end') + '\n' + trace)
                messagebox.showerror(APP_NAME, message)
        self.root.after(120, self.poll)

    def cancel(self):
        if self.busy and not self.commit_event.is_set():
            self.cancel_event.set()
            self.cancel_button.state(['disabled'])
            self.status.set('Ζητήθηκε ακύρωση· θα ολοκληρωθεί στο επόμενο ασφαλές σημείο.')

    def toggle_log(self):
        if self.log_frame.winfo_manager():
            self.log_frame.pack_forget()
            self.details_button.configure(text='Εμφάνιση λεπτομερειών')
        else:
            self.log_frame.pack(fill='both', expand=True, pady=8)
            self.details_button.configure(text='Απόκρυψη λεπτομερειών')

    def choose_results(self):
        folder = filedialog.askdirectory(title='Φάκελος προηγούμενων αποτελεσμάτων', initialdir=self.outdir.get() or None)
        if folder:
            self.load_output(folder)

    def load_output(self, folder):
        from .working_text import WorkingText
        if WorkingText.exists(folder):
            messagebox.showinfo(APP_NAME, 'Ανοίξτε αυτόν τον επεξεργάσιμο φάκελο στο Workbench για να συνεχίσετε από την τρέχουσα αναθεώρηση.')
            return
        if not self.check_draft():
            return
        try:
            workspace = Workspace(folder)
            try:
                saved = Saved.load(folder)
            except SetupError:
                saved = None
        except SetupError as error:
            messagebox.showerror(APP_NAME, str(error))
            return
        self.workspace, self.saved = workspace, saved
        self.outdir.set(str(workspace.folder))
        self.report = str(workspace.folder / 'report.txt')
        self.refresh_words()
        self.refresh_results()
        self.show('results')
        self.status.set('Άνοιξαν προηγούμενα αποτελέσματα.' + (' Οι αναλύσεις είναι διαθέσιμες για γρήγορη επανεξαγωγή.' if saved else ''))

    def refresh_words(self, select_first=True):
        if not self.workspace:
            return
        if self.dirty:
            return  # keep the draft visible until explicitly saved or discarded
        previous = identity(self.selected) if self.selected else None
        self.selected = None
        rows = self.workspace.visible(self.query.get(), {'Αμφίβολες': 'uncertain', 'Λοιπές επισημάνσεις': 'warnings', 'Όλες': 'all', 'Αποθηκευμένες': 'checked'}[self.filter.get()])
        self.words.delete(*self.words.get_children())
        self.visible_records = {}
        for i, record in enumerate(rows):
            key = str(i)
            self.visible_records[key] = record
            self.words.insert('', 'end', iid=key, values=(record['form'], record['citation'], '✓' if self.workspace.checked(record) else ''))
        checked = sum(self.workspace.checked(r) for r in self.workspace.records)
        self.review_summary.configure(text=f'{len(rows):,} εμφανίσεις στην προβολή · {checked:,} αποθηκευμένες επιλογές / {len(self.workspace.records):,} λέξεις')
        key = next((k for k, r in self.visible_records.items() if identity(r) == previous),
                   '0' if rows and select_first else None)
        if key is not None:
            self.words.selection_set(key)
            self.words.focus(key)
            self.words.see(key)
        else:
            self.selected = None
            self.record_title.configure(text=('Δεν υπάρχει επόμενη εμφάνιση. Επιλέξτε άλλη από τη λίστα.' if rows else
                                              'Δεν υπάρχουν εμφανίσεις σε αυτή την προβολή.'))
            put_text(self.context, '')
            put_text(self.evidence, '')
            self.reason.configure(text='')
            for widget in self.parts_frame.winfo_children():
                widget.destroy()
            self.part_vars = []

    def select_word(self, _event=None):
        selected = self.words.selection()
        if not selected:
            return
        record = self.visible_records.get(selected[0])
        if not record or record is self.selected:
            return
        if not self.check_draft():
            previous = next((k for k, r in self.visible_records.items() if r is self.selected), None)
            if previous is not None:
                self.words.selection_set(previous)
            return
        self.selected, self.dirty = record, False
        self.root.after_idle(lambda: self.detail_scroll.canvas.yview_moveto(0))
        self.scope.set('one')
        self.record_title.configure(text=f'{record["form"]}  ·  {record["work"]}  ·  {record["citation"]}')
        context = self.workspace.contexts[identity(record)]
        put_text(self.context, context)
        self.context.configure(state='normal')
        start = context.find('«')
        if start >= 0:
            self.context.tag_add('target', f'1.0+{start}c', f'1.0+{context.find("»", start)+1}c')
        self.context.configure(state='disabled')
        primary = self.workspace.priorities[identity(record)]
        reasons = list(dict.fromkeys(record['review_reasons']))
        secondary = [r for r in reasons if r not in primary]
        text = '\n'.join(reason_text(r) for r in primary)
        if secondary:
            text += ('\n\n' if text else '') + 'Λοιπές επισημάνσεις (δεν δηλώνουν από μόνες τους αμφίβολο λήμμα):\n' + '\n'.join(reason_text(r) for r in secondary)
        self.reason.configure(text=text or 'Δεν καταγράφηκε διαφωνία μεταξύ των διαθέσιμων στοιχείων.')
        for widget in self.parts_frame.winfo_children():
            widget.destroy()
        lemma, tags = self.workspace.values(record)
        lemmas, tags = lemma.split('+'), tags.split('+')
        self.part_vars = []
        for i, form in enumerate(self.workspace.expansion(record)):
            ttk.Label(self.parts_frame, text=form, style='Greek.TLabel').pack(anchor='w', pady=(8, 3))
            row = ttk.Frame(self.parts_frame, style='Paper.TFrame')
            row.pack(fill='x')
            row.columnconfigure(0, weight=1)
            row.columnconfigure(1, weight=1)
            lemma_var = tk.StringVar(value=lemmas[i] if i < len(lemmas) else '')
            pos_var = tk.StringVar(value=POS_LABELS.get(tags[i] if i < len(tags) else 'X', POS_LABELS['X']))
            ttk.Label(row, text='Λήμμα', style='PaperHint.TLabel').grid(row=0, column=0, sticky='w')
            ttk.Label(row, text='Μέρος του λόγου', style='PaperHint.TLabel').grid(row=0, column=1, sticky='w', padx=(8, 0))
            ttk.Entry(row, textvariable=lemma_var, font=('Cambria', 14)).grid(row=1, column=0, sticky='ew', pady=4)
            ttk.Combobox(row, textvariable=pos_var, values=list(POS_LABELS.values()), state='readonly', width=22).grid(row=1, column=1, sticky='ew', padx=(8, 0), pady=4)
            self.part_vars.append((lemma_var, pos_var))
            for var in (lemma_var, pos_var):
                var.trace_add('write', lambda *_: self.mark_dirty())
        self.choice_message.configure(text='Η αρχική γραφή του κειμένου διατηρείται.')
        put_text(self.evidence, evidence_text(record))

    def mark_dirty(self):
        self.dirty = True
        self.choice_message.configure(text='Μη αποθηκευμένη επιλογή.')

    def check_draft(self):
        if not self.dirty:
            return True
        answer = messagebox.askyesnocancel(APP_NAME, 'Υπάρχει μη αποθηκευμένη επιλογή. Να αποθηκευτεί;')
        if answer is None:
            return False
        if answer:
            return self.save_choice(advance=False)
        lemma, pos = self.workspace.values(self.selected)
        lemmas, tags = lemma.split('+'), pos.split('+')
        for i, (lemma_var, pos_var) in enumerate(self.part_vars):
            lemma_var.set(lemmas[i] if i < len(lemmas) else '')
            pos_var.set(POS_LABELS.get(tags[i] if i < len(tags) else 'X', POS_LABELS['X']))
        self.scope.set('one')
        self.choice_message.configure(text='Η μη αποθηκευμένη αλλαγή απορρίφθηκε.')
        self.dirty = False
        return True

    def save_choice(self, advance=True):
        if self.busy or not self.workspace or not self.selected:
            return False
        visible = [identity(r) for r in self.visible_records.values()]
        current = identity(self.selected)
        following = set(visible[visible.index(current) + 1:]) if current in visible else set()
        try:
            self.workspace.choose(self.selected, [v.get().strip() for v, _ in self.part_vars],
                                  [POS_CODES[p.get()] for _, p in self.part_vars], self.scope.get() == 'all',
                                  crasis=self.workspace.manual_crasis(self.selected))
        except (ValueError, OSError, KeyError) as error:
            messagebox.showerror(APP_NAME, f'Δεν αποθηκεύτηκε η επιλογή. Αν το φύλλο είναι ανοιχτό, κλείστε το και ξαναδοκιμάστε.\n{error}')
            return False
        self.dirty = False
        self.choice_message.configure(text='Αποθηκεύτηκε στο φύλλο διορθώσεων. Πατήστε «Ενημέρωση αρχείων» για την εξαγωγή.')
        self.status.set('Υπάρχουν αποθηκευμένες επιλογές προς ενσωμάτωση στα αποτελέσματα.')
        for key, record in self.visible_records.items():
            self.words.set(key, 'state', '✓' if self.workspace.checked(record) else '')
        checked = sum(self.workspace.checked(r) for r in self.workspace.records)
        self.review_summary.configure(text=f'{len(self.visible_records):,} εμφανίσεις στην προβολή · {checked:,} αποθηκευμένες επιλογές / {len(self.workspace.records):,} λέξεις')
        if advance:
            self.refresh_words(select_first=False)
            next_key = next((k for k, r in self.visible_records.items() if identity(r) in following), None)
            if next_key is not None:
                self.words.selection_set(next_key)
                self.words.focus(next_key)
                self.words.see(next_key)
                self.select_word()
            else:
                self.status.set('Η επιλογή αποθηκεύτηκε. Δεν υπάρχει επόμενη εμφάνιση στην τρέχουσα προβολή. '
                                'Πατήστε «Ενημέρωση αρχείων» για την εξαγωγή.')
        return True

    def apply_choices(self):
        if self.busy or not self.workspace or not self.check_draft():
            return
        if self.saved:
            self.begin(self.saved.paths, str(self.workspace.folder), self.saved.export_options(self.workspace.folder), self.saved)
        elif self.file_paths:
            opts = self.options()
            opts.corrections = str(self.workspace.sheet)
            self.begin(self.file_paths[:], str(self.workspace.folder), opts)
        else:
            messagebox.showinfo(APP_NAME, 'Οι επιλογές αποθηκεύτηκαν. Για αυτόν τον παλιό φάκελο δεν υπάρχει '
                                'αποθηκευμένη συνεδρία. Προσθέστε τα αρχικά XML για νέα εξαγωγή.')
            self.show('files')

    def refresh_results(self):
        if not self.workspace:
            return
        records = self.workspace.records
        total, expanded = len(records), sum(len(r['expansion']) for r in records)
        checked = sum(bool(r.get('reviewed')) for r in records)
        self.result_title.configure(text=f'{total:,} λέξεις · {expanded:,} λεκτικές μονάδες για το AntConc')
        status = f'Χειροκίνητος έλεγχος: {checked:,} / {total:,} εμφανίσεις.' if checked else 'Χωρίς χειροκίνητο έλεγχο ανά εμφάνιση.'
        self.result_status.configure(text=status + '\nΟι αυτόματες προτάσεις και οι διαφωνίες διατηρούνται για έλεγχο.\n' + str(self.workspace.folder))

    def open_result(self, relative):
        if self.workspace:
            path = self.workspace.folder / relative
            if path.exists():
                self.open_path(str(path))

    def antconc_help(self):
        messagebox.showinfo('Στο AntConc', '1. Corpus Manager → Raw Files: προσθέστε τα αρχεία του antconc-tagged.\n\n'
                            '2. Indexer Options: simple_word_pos_headword_indexer.\n\n'
                            '3. Create Database. Στο Concordance αναζητήστε π.χ. {καί}.')

    def settings_dialog(self):
        top = tk.Toplevel(self.root)
        top.title('Πόροι λημματοποίησης')
        top.transient(self.root)
        frame = ttk.Frame(top, padding=20)
        frame.pack(fill='both', expand=True)
        dio, greta = tk.StringVar(value=self.dio_path or ''), tk.StringVar(value=self.greta_path or default_path())
        context = tk.StringVar(value=self.context_path or default_context_path())
        for label, var in [('Φάκελος Diogenes', dio), ('Φάκελος GreBERTa joint', context), ('Φάκελος εκπαιδευμένου GreTa', greta)]:
            ttk.Label(frame, text=label).pack(anchor='w', pady=(8, 3))
            row = ttk.Frame(frame)
            row.pack(fill='x')
            ttk.Entry(row, textvariable=var, width=65).pack(side='left', fill='x', expand=True)
            def browse(value=var):
                directory = filedialog.askdirectory(parent=top)
                if directory:
                    value.set(directory)
            ttk.Button(row, text='Επιλογή…', command=browse).pack(side='left', padx=7)
        ttk.Label(frame, text='Το GreBERTa joint αναλύει τη λέξη στα συμφραζόμενά της. Το GreTa παρέχει συγκριτικά λήμματα.', wraplength=600, style='Hint.TLabel').pack(anchor='w', pady=15)
        def accept():
            self.dio_path, self.greta_path = dio.get().strip() or None, greta.get().strip() or None
            self.context_path = context.get().strip() or None
            self.save()
            top.destroy()
        ttk.Button(frame, text='Αποθήκευση', style='Primary.TButton', command=accept).pack(anchor='e')
        top.grab_set()

    def save(self):
        self.settings.update(outdir=self.outdir.get(), workflow=self.mode.get(), diogenes=self.dio_path,
                             greta_model=self.greta_path, context_model=self.context_path, corrections=self.corrections.get().strip())
        self.save_settings(self.settings)

    def close(self):
        if self.closing:
            return
        if self.busy:
            if not (self.commit_event.is_set() or self.cancel_event.is_set()):
                if not messagebox.askokcancel(APP_NAME, 'Η εργασία συνεχίζεται. Να κλείσει το πρόγραμμα μόλις ολοκληρωθεί;', parent=self.root):
                    return
            self.closing = True
            self.save()
            self.root.withdraw()
            return
        if self.check_draft():
            self.save()
            self.root.destroy()


def reason_text(reason):
    translations = {
        'GreTa and selected lexical analysis disagree': 'Το GreTa διαφωνεί με το επιλεγμένο λήμμα.',
        'lemma lacks a full local morphological analysis': 'Δεν βρέθηκε πλήρης τοπική μορφολογική ανάλυση.',
        'ambiguous grammatical/lexical analysis': 'Υπάρχουν κοντινές γραμματικές ή λεξικές αναλύσεις.',
        'crasis detected but no supported inflected expansion': 'Εντοπίστηκε πιθανή κράση χωρίς τεκμηριωμένη ανάπτυξη.',
        'unexpanded elision; restoration uncertain': 'Η έκθλιψη δεν αποκαταστάθηκε με ασφάλεια.',
        'unresolved lemma': 'Δεν βρέθηκε λήμμα.',
        'competing lemmas; ranking margin below 0.75 (not a probability)': 'Κοντινές εναλλακτικές με διαφορετικά λήμματα.',
        'competing lexical expansions; ranking margin below 0.75 (not a probability)': 'Κοντινές αναπτύξεις με διαφορετικά λήμματα ή διαφορετικό αριθμό λέξεων.',
        'interrogative grammar conflicts with indefinite lemma': 'Η ερωτηματική γραμματική διαφωνεί με το αόριστο λήμμα τις.',
        'GreTa length limit or incomplete prediction': 'Η πρόταση του GreTa είναι ελλιπής ή ξεπέρασε το όριο μήκους.',
        'expansion candidate limit reached (8)': 'Η αναζήτηση έφτασε στο όριο εναλλακτικών αναπτύξεων.',
        'internal coronis detected; no supported independent-word expansion': 'Εσωτερική κορωνίδα χωρίς τεκμηριωμένη ανάπτυξη σε ανεξάρτητες λέξεις.',
        'unresolved initial elision': 'Δεν αποκαταστάθηκε η αρχική έκθλιψη.',
    }
    if reason.startswith('lexical and contextual grammar disagree:'):
        return 'Διαφωνία γραμματικής: ' + reason.split(':', 1)[1].strip()
    if reason.startswith('ambiguous expansion'):
        return 'Αμφίσημη ανάπτυξη. Το περιθώριο κατάταξης δεν είναι πιθανότητα ορθότητας.'
    return translations.get(reason, reason)


def evidence_text(record):
    out = ['Αυτόματες αναλύσεις πριν από χειροκίνητες επιλογές. Οι βαθμοί δεν είναι πιθανότητες ορθότητας.']
    for reading in record['readings']:
        out.append('\nΑνάπτυξη: ' + ' + '.join(reading.get('expansion', ())))
        for component in reading.get('components', ()):
            out.append(f'  {component.get("form", "")} · GreTa: {component.get("greta") or "—"}')
            tagger = component.get('tagger', {})
            out.append(f'  Γραμματική OdyCy: {tagger.get("pos") or "—"} · {tagger.get("msd") or "—"}')
            for candidate in component.get('candidates', ())[:5]:
                out.append('    ' + ' · '.join(str(candidate.get(k) or '—') for k in ('lemma', 'pos', 'msd', 'source')))
                if candidate.get('raw'):
                    out.append('      ' + candidate['raw'])
    return '\n'.join(out)
