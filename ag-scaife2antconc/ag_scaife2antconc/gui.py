"""The window: choose XML files, an output folder and what to export; Run.

Files can also be dropped on the desktop shortcut: they arrive as command-line arguments.
The work runs in a thread; messages come back through a queue polled by Tk.
"""
import json
import os
import queue
import sys
import threading
import traceback
import webbrowser
import xml.etree.ElementTree as ET
from collections import Counter
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from ag_diogenes_spacy_txt2xml import diogenes

from . import APP_NAME, __version__, accents
from .pipeline import DIOGENES_URL, STEP_NAMES, Options, SetupError, expected_results, old_results, plan, run, shown
from .reader import STEPS, read

SETTINGS = os.path.join(os.environ.get('APPDATA') or os.path.expanduser('~'), APP_NAME, 'settings.json')


# step -> (title, what was found, question, button: carry out, button: skip)
QUESTIONS = {
    'hyphenation': ('Λέξεις χωρισμένες με ενωτικό',
                    'Βρέθηκαν {n} λέξεις χωρισμένες σε δύο μέρη με ενωτικό, π.χ. στο τέλος ενός στίχου.',
                    'Να ενωθούν τα δύο μέρη σε μία λέξη;', 'Ένωση', 'Να μείνουν χωρισμένες'),
    'hypothesis': ('Υπόθεση',
                   'Βρέθηκε υπόθεση: περίληψη του έργου από μεταγενέστερο συγγραφέα ({w} λέξεις).',
                   'Να αφαιρεθεί;', 'Αφαίρεση', 'Διατήρηση'),
    'notes': ('Σημειώσεις και κριτικό υπόμνημα',
              'Βρέθηκαν {n} σημειώσεις του εκδότη, με {w} ελληνικές λέξεις (γραφές χειρογράφων, διορθώσεις).',
              'Να αφαιρεθούν; Αν τις κρατήσετε, μπαίνουν στο κείμενο εκεί όπου βρίσκονται.',
              'Αφαίρεση', 'Διατήρηση'),
    'speaker names': ('Ονόματα ομιλητών',
                      'Βρέθηκαν {n} ονόματα ομιλητών πριν από τις ρήσεις των προσώπων.',
                      'Να αφαιρεθούν; Αν τα κρατήσετε, το όνομα μπαίνει ως λέξη στην αρχή του πρώτου '
                      'στίχου κάθε ρήσης, και μετριέται στο AntConc.', 'Αφαίρεση', 'Διατήρηση'),
    'headings': ('Τίτλοι και επικεφαλίδες',
                 'Βρέθηκαν {n} τίτλοι ή επικεφαλίδες του εκδότη.',
                 'Να αφαιρεθούν;', 'Αφαίρεση', 'Διατήρηση'),
    'stage directions': ('Σκηνικές οδηγίες',
                         'Βρέθηκαν {n} σκηνικές οδηγίες.',
                         'Να αφαιρεθούν;', 'Αφαίρεση', 'Διατήρηση'),
    'deletions': ('Διαγραφές του εκδότη [ ]',
                  'Βρέθηκαν {n} σημεία σε αγκύλες [ ], που ο εκδότης θεωρεί ότι δεν ανήκουν στο κείμενο '
                  '({w} λέξεις).',
                  'Να αφαιρεθούν; Αν τα κρατήσετε, μένουν οι λέξεις χωρίς τις αγκύλες.', 'Αφαίρεση', 'Διατήρηση'),
    'rejected readings': ('Απορριφθείσες γραφές',
                          'Βρέθηκαν {n} γραφές που ο εκδότης απέρριψε, π.χ. λάθη του χειρογράφου δίπλα στη '
                          'διόρθωσή τους.',
                          'Να αφαιρεθούν; Αν τις κρατήσετε, γράφονται μετά τη γραφή του κειμένου.',
                          'Αφαίρεση', 'Διατήρηση'),
    'accents': ('Τονισμός',
                'Βρέθηκαν {n} λέξεις που το AntConc θα μετρούσε ως χωριστούς τύπους μόνο λόγω του τονισμού: '
                'με βαρεία (καὶ, ενώ αλλού καί) ή με δεύτερο τόνο από εγκλιτική λέξη (ὄρνιθός τινος, '
                'ενώ αλλού ὄρνιθος· {w} λέξεις).',
                'Να κανονικοποιηθεί ο τονισμός; Τότε το AntConc μετρά το καὶ μαζί με το καί και το ὄρνιθός '
                'μαζί με το ὄρνιθος, αλλά το κείμενο δεν είναι πια ακριβώς όπως στην έκδοση. Τα λήμματα '
                'δεν αλλάζουν· οι κράσεις (ὦγαθοί) μένουν ως έχουν.', 'Κανονικοποίηση', 'Όπως στην έκδοση'),
}
STEP_ORDER = STEPS + ['accents']
OPT_IN = {'accents'}      # steps not carried out unless the user says so: the other button is recommended


class StepDialog:
    """One cleansing question: what was found, examples, carry out / skip / cancel."""

    def __init__(self, parent, index, total, step, count, words, examples):
        title, found, question, yes, no = QUESTIONS[step]
        self.answer = None
        top = self.top = tk.Toplevel(parent)
        top.title(f'Καθαρισμός: βήμα {index} από {total}')
        top.transient(parent)
        top.resizable(False, False)
        frame = ttk.Frame(top, padding=16)
        frame.pack(fill='both', expand=True)
        ttk.Label(frame, text=title, font=('Segoe UI', 12, 'bold')).pack(anchor='w')
        ttk.Label(frame, text=found.format(n=f'{count:,}', w=f'{words:,}'), wraplength=520,
                  justify='left').pack(anchor='w', pady=(8, 4))
        if examples:
            box = tk.Frame(frame, background='#f3f3f3', padx=10, pady=6)
            box.pack(fill='x', pady=4)
            tk.Label(box, text='Παραδείγματα:', background='#f3f3f3', foreground='#555555').pack(anchor='w')
            for e in examples:
                tk.Label(box, text='• ' + e, background='#f3f3f3', wraplength=500, justify='left',
                         font=('Segoe UI', 10)).pack(anchor='w')
        ttk.Label(frame, text=question, wraplength=520, justify='left', font=('Segoe UI', 10, 'bold')).pack(
            anchor='w', pady=(10, 12))
        buttons = ttk.Frame(frame)
        buttons.pack(fill='x')
        ttk.Button(buttons, text='Ακύρωση', command=lambda: self.close(None)).pack(side='right')
        best = 'keep' if step in OPT_IN else 'clean'
        for answer, text in (('keep', no), ('clean', yes)):
            button = ttk.Button(buttons, text=text + (' (συνιστάται)' if answer == best else ''),
                                command=lambda a=answer: self.close(a))
            button.pack(side='right', padx=(0, 8))
            if answer == best:
                button.focus_set()
        top.bind('<Return>', lambda e: self.close(best))
        top.bind('<Escape>', lambda e: self.close(None))
        top.protocol('WM_DELETE_WINDOW', lambda: self.close(None))
        top.update_idletasks()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - top.winfo_width()) // 2)
        y = parent.winfo_rooty() + 80
        top.geometry(f'+{x}+{y}')
        top.grab_set()

    def close(self, answer):
        self.answer = answer
        self.top.grab_release()
        self.top.destroy()

    def ask(self):
        self.top.wait_window()
        return self.answer


def scan(paths):
    """What the full cleansing would do in these files: {step: (count, words, examples)}."""
    found, speakers, changed = {}, None, Counter()
    for path in paths:
        try:
            work = read(path)
        except (ET.ParseError, OSError):
            continue
        changed += accents.normalize_lines(work.lines)
        speakers = work.speakers if speakers is None else speakers + work.speakers
        for step in STEPS:
            count, words, examples = found.get(step, (0, 0, []))
            examples = examples + [e for e in work.examples[step] if e not in examples]
            found[step] = (count + work.elements[step], words + work.dropped[step], examples[:5])
    if speakers:
        count, words, _ = found['speaker names']
        found['speaker names'] = (count, words, [', '.join(f'{s} ({n})' for s, n in speakers.most_common(12))
                                                 + (' …' if len(speakers) > 12 else '')])
    grave, enclitic, examples = accents.summary(changed, limit=6)
    found['accents'] = (grave + enclitic, enclitic, examples)
    return {step: v for step, v in found.items() if v[0]}


def load_settings():
    try:
        with open(SETTINGS, encoding='utf-8') as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def save_settings(data):
    try:
        os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
        with open(SETTINGS, 'w', encoding='utf-8') as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
    except OSError:
        pass


def open_path(path):
    if hasattr(os, 'startfile'):
        os.startfile(path)
    else:
        webbrowser.open('file://' + os.path.abspath(path))


from .desktop import App


def desktop_main(argv=None):
    root = tk.Tk()
    App(root, sys.argv[1:] if argv is None else argv)
    root.mainloop()


def main(argv=None):
    try:
        from .web import main as browser_main
        return browser_main(argv)
    except (SetupError, OSError) as error:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(APP_NAME, str(error), parent=root)
        root.destroy()


if __name__ == '__main__':
    main()
