"""Optional local file chooser; a separate process keeps Tk out of the web server."""
import json
import sys


def main():
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes('-topmost', True)
    try:
        kind = sys.argv[1]
        if kind == 'files':
            result = list(filedialog.askopenfilenames(
                parent=root, title='Select XML / OGA (.conllu) files',
                filetypes=[('XML / OGA', '*.xml *.conllu'), ('XML', '*.xml'), ('OGA CoNLL-U', '*.conllu')]))
        elif kind == 'corrections':
            result = filedialog.askopenfilename(parent=root, title='Select review sheet', filetypes=[('Spreadsheets', '*.tsv *.csv *.txt')])
        else:
            result = filedialog.askdirectory(parent=root, title='Choose folder')
        print(json.dumps(result, ensure_ascii=False), flush=True)
    finally:
        root.destroy()


if __name__ == '__main__':
    main()
